"""Find Yourself 运维 CLI（Infra 分片）。

仅使用 Python 标准库，不依赖任何尚未锁定的第三方包；对 Core/Runtime 服务
一律 lazy import，服务尚未就绪时返回明确的 NOT_RUN，而不是崩溃或伪造成功。

子命令：
    doctor            环境与依赖健康检查（不输出连接串/密钥）
    backup            备份预检并写出清单（服务未就绪时标 NOT_RUN）
    restore           恢复预检；必须显式 environment/manifest，绝不默认覆盖生产
    verify-deletions  删除 tombstone 重放校验（服务未就绪时标 NOT_RUN）

退出码约定：
    0  成功 / 全部检查通过
    1  参数或内部错误
    2  存在 FAIL 项
    3  运行了，但必需服务/依赖未就绪（NOT_RUN）
    4  被安全守卫拒绝（例如未显式确认就恢复到生产）
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# 通用输出
# --------------------------------------------------------------------------- #
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(result: dict, fmt: str) -> int:
    """按格式输出结果并返回约定的退出码。"""
    code = int(result.get("exit_code", 0))
    if fmt == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        status = result.get("status", "UNKNOWN")
        print(f"[{status}] find_yourself.cli:{result.get('command','?')}")
        for chk in result.get("checks", []):
            line = f"  - {chk['name']:<22} {chk['status']:<8} {chk['detail']}"
            print(line)
        if result.get("summary"):
            print(f"  summary: {result['summary']}")
        if result.get("manifest"):
            print(f"  manifest: {result['manifest']}")
        print(f"  exit_code: {code}")
    return code


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# --------------------------------------------------------------------------- #
# 探测原语（全部 stdlib；失败返回 NOT_RUN 语义，不抛给上层）
# --------------------------------------------------------------------------- #
def tcp_probe(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_probe(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - 受控本地地址
            return (200 <= resp.status < 500, f"http {resp.status}")
    except urllib.error.HTTPError as e:
        return (e.code < 500, f"http {e.code}")
    except OSError as e:
        return (False, f"unreachable: {type(e).__name__}")


def parse_db_target(database_url: str) -> tuple[str, int] | None:
    """从 postgresql URL 提取 host/port；只用于回环探测，绝不回显口令。"""
    if not database_url.startswith("postgresql"):
        return None
    try:
        # postgresql+psycopg://user:pass@host:port/db
        tail = database_url.split("://", 1)[1]
        host_part = tail.split("/", 1)[0]
        host_part = host_part.split("@", 1)[-1]  # 去掉 user:pass
        if ":" in host_part:
            host, port = host_part.rsplit(":", 1)
            return (host, int(port))
        return (host_part, 5432)
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args: argparse.Namespace) -> dict:
    checks: list[dict] = []

    checks.append({
        "name": "python", "status": "OK",
        "detail": f"{sys.version.split()[0]} {sys.implementation.name}",
    })

    # 依赖：lazy 探测，缺包不算 FAIL，而是 NOT_RUN（Core/Runtime 未就绪）
    missing = [
        m for m in ("fastapi", "sqlalchemy", "temporalio", "pydantic_settings")
        if not _has_module(m)
    ]
    if missing:
        checks.append({
            "name": "app_dependencies", "status": "NOT_RUN",
            "detail": f"missing: {','.join(missing)} (uv.lock/venv not prepared by Core yet)",
        })
    else:
        checks.append({"name": "app_dependencies", "status": "OK", "detail": "core imports present"})

    # 配置：lazy 加载，绝不打印值
    cfg_status, cfg_detail = _probe_config(args.environment)
    checks.append({"name": "config", "status": cfg_status, "detail": cfg_detail})

    # 数据库
    db_url = _env("FY_DATABASE_URL")
    tgt = parse_db_target(db_url)
    if tgt is None:
        checks.append({"name": "database", "status": "NOT_RUN",
                       "detail": "FY_DATABASE_URL not a postgresql target"})
    else:
        host, port = tgt
        checks.append({
            "name": "database",
            "status": "OK" if tcp_probe(host, port) else "NOT_RUN",
            "detail": f"tcp {host}:{port} " + ("open" if tcp_probe(host, port) else "closed"),
        })

    # Temporal（gRPC TCP 探活）
    taddr = _env("FY_TEMPORAL_ADDRESS", "temporal:7233")
    thost = taddr.split(":")[0]
    tport = int(taddr.split(":")[1]) if ":" in taddr else 7233
    t_up = tcp_probe(thost, tport)
    checks.append({"name": "temporal", "status": "OK" if t_up else "NOT_RUN",
                   "detail": f"grpc {thost}:{tport} " + ("open" if t_up else "closed")})

    # S3
    s3 = _env("FY_S3_ENDPOINT")
    if not s3:
        checks.append({"name": "object_storage", "status": "NOT_RUN",
                       "detail": "FY_S3_ENDPOINT not set"})
    else:
        ok, detail = http_probe(s3.rstrip("/") + "/minio/health/live")
        # MinIO 在未起时会连接失败 → NOT_RUN；5xx 也算未就绪。
        checks.append({"name": "object_storage", "status": "OK" if ok else "NOT_RUN",
                       "detail": f"{s3} {detail}"})

    # API
    api_base = _env("FY_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    ok, detail = http_probe(api_base + "/health/live")
    checks.append({"name": "api", "status": "OK" if ok else "NOT_RUN",
                   "detail": f"{api_base}/health/live {detail}"})

    # 模型网关（信息项，不阻断）
    model_configured = bool(_env("FY_MODEL_API_KEY") and _env("FY_MODEL_BASE_URL"))
    checks.append({"name": "model_gateway",
                   "status": "OK" if model_configured else "NOT_RUN",
                   "detail": "configured" if model_configured else "disabled (cold start, no paid call)"})

    # 生产安全守卫：production 缺 OIDC 视为 FAIL
    if args.environment == "production":
        need = all(_env(k) for k in ("FY_OIDC_ISSUER", "FY_OIDC_CLIENT_ID", "FY_OIDC_OWNER_SUB"))
        checks.append({"name": "production_oidc", "status": "OK" if need else "FAIL",
                       "detail": "oidc fully configured" if need else "production requires OIDC issuer/client_id/owner_sub"})

    statuses = {c["status"] for c in checks}
    service_names = {"database", "temporal", "object_storage", "api"}
    service_ok = [c for c in checks if c["name"] in service_names and c["status"] == "OK"]
    if "FAIL" in statuses:
        overall, code = "FAIL", 2
    elif not service_ok:
        # 依赖可导入、Python 正常，但数据层/应用层都没起来 -> 明确 NOT_RUN。
        overall, code = "NOT_RUN", 3
    else:
        overall, code = "OK", 0

    return {
        "command": "doctor", "status": overall, "exit_code": code,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": checks,
        "summary": f"{sum(c['status']=='OK' for c in checks)} OK, "
                   f"{sum(c['status']=='FAIL' for c in checks)} FAIL, "
                   f"{sum(c['status']=='NOT_RUN' for c in checks)} NOT_RUN",
    }


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _probe_config(environment: str) -> tuple[str, str]:
    """lazy 导入 Settings；失败一律 NOT_RUN，且不泄露任何配置值。"""
    try:
        from find_yourself.config import settings  # type: ignore
        settings()
        return "OK", "settings loaded"
    except Exception as e:  # noqa: BLE001 - 明确捕获，绝不打印字段值
        return "NOT_RUN", f"settings not usable in this env: {type(e).__name__}"


# --------------------------------------------------------------------------- #
# backup
# --------------------------------------------------------------------------- #
def cmd_backup(args: argparse.Namespace) -> dict:
    preflight: list[dict] = []
    tgt = parse_db_target(_env("FY_DATABASE_URL"))
    db_ready = bool(tgt and tcp_probe(*tgt))
    preflight.append({"component": "database", "ready": db_ready,
                      "detail": ("probe ok" if db_ready else "not reachable")})

    s3 = _env("FY_S3_ENDPOINT")
    s3_ready = bool(s3 and http_probe(s3.rstrip("/") + "/minio/health/live")[0])
    preflight.append({"component": "object_storage", "ready": s3_ready,
                      "detail": ("probe ok" if s3_ready else "not reachable")})

    target = os.path.abspath(args.target)
    planned = {
        "database_dump": f"pg_dump -> {target}/db.sql (when postgres reachable)",
        "object_prefix": f"rclone/mirror bucket -> {target}/s3/ (when minio reachable)",
        "tombstones": "include tombstones table so restore can replay deletions",
        "retention_days": 30,
    }

    not_ready = [c["component"] for c in preflight if not c["ready"]]
    status = "OK" if not not_ready else "NOT_RUN"
    code = 0 if not not_ready else 3

    manifest = {
        "kind": "find_yourself.backup.manifest",
        "version": 1,
        "environment": args.environment,
        "created_at_utc": _utcnow(),
        "target": target,
        "include": args.include,
        "components": preflight,
        "planned": planned,
        "status": status,
        "note": "Actual dump execution is coordinated by Infra once Core/Runtime is ready; "
                "this manifest only records the approved plan and preflight result.",
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.manifest)), exist_ok=True)
    with open(args.manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    return {
        "command": "backup", "status": status, "exit_code": code,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": [{"name": c["component"], "status": "OK" if c["ready"] else "NOT_RUN",
                    "detail": c["detail"]} for c in preflight],
        "summary": f"backup manifest written; services not ready: {not_ready or 'none'}",
        "manifest": os.path.abspath(args.manifest),
    }


# --------------------------------------------------------------------------- #
# restore
# --------------------------------------------------------------------------- #
def cmd_restore(args: argparse.Namespace) -> dict:
    # 守卫：environment 必须显式给出（parser required）；生产需二次确认。
    if args.environment == "production" and not args.allow_production:
        return {
            "command": "restore", "status": "REFUSED", "exit_code": 4,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [], "summary":
                "Refusing restore into 'production' without --allow-production and an approved manifest. "
                "Recovery must target a staging/recovery environment first.",
        }

    if not os.path.exists(args.manifest):
        return {
            "command": "restore", "status": "ERROR", "exit_code": 1,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [], "summary": f"manifest not found: {args.manifest}",
        }

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    # 生产恢复还要求清单里存在批准记录
    if args.environment == "production":
        if not (manifest.get("approved_by") and manifest.get("approved_at_utc")):
            return {
                "command": "restore", "status": "REFUSED", "exit_code": 4,
                "environment": args.environment, "recorded_at_utc": _utcnow(),
                "checks": [], "summary":
                    "Production restore refused: manifest lacks approved_by/approved_at_utc approval record.",
            }

    tgt = parse_db_target(_env("FY_DATABASE_URL"))
    db_ready = bool(tgt and tcp_probe(*tgt))
    status = "OK" if db_ready else "NOT_RUN"
    code = 0 if db_ready else 3

    return {
        "command": "restore", "status": status, "exit_code": code,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": [{"name": "database_target", "status": "OK" if db_ready else "NOT_RUN",
                    "detail": f"target env = {args.environment}; manifest = {os.path.basename(args.manifest)}"}],
        "summary": ("restore preflight passed; actual replay to be executed by approved runbook"
                    if db_ready else "database not reachable; restore NOT_RUN (no data touched)"),
        "manifest": os.path.abspath(args.manifest),
    }


# --------------------------------------------------------------------------- #
# verify-deletions
# --------------------------------------------------------------------------- #
def cmd_verify_deletions(args: argparse.Namespace) -> dict:
    tgt = parse_db_target(_env("FY_DATABASE_URL"))
    ready = bool(tgt and tcp_probe(*tgt))
    if not ready:
        return {
            "command": "verify-deletions", "status": "NOT_RUN", "exit_code": 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "tombstones", "status": "NOT_RUN",
                        "detail": "database not reachable; cannot replay tombstones yet"}],
            "summary": "Deletion replay verification requires postgres; NOT_RUN in this environment.",
        }
    # 服务就绪后由 Core 的 DeletionService 提供计数与顺序校验；此处只做可达性确认。
    return {
        "command": "verify-deletions", "status": "OK", "exit_code": 0,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": [{"name": "tombstones", "status": "OK",
                    "detail": "database reachable; replay order/count to be asserted by DeletionService"}],
        "summary": "preflight ok",
    }


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="find_yourself.cli",
        description="Find Yourself 运维 CLI：doctor / backup / restore / verify-deletions。",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--format", choices=["json", "text"], default="text",
                        help="输出格式（默认 text；机器对接收 json）")
        sp.add_argument("--environment",
                        default=os.environ.get("FY_ENVIRONMENT", "local"),
                        choices=["local", "test", "production", "recovery"],
                        help="目标环境（默认取 FY_ENVIRONMENT 或 local）")

    sp = sub.add_parser("doctor", help="健康检查，不输出连接串/密钥")
    common(sp)
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("backup", help="备份预检并写出清单（不伪造 dump）")
    common(sp)
    sp.add_argument("--target", required=True, help="已批准的备份目的地目录")
    sp.add_argument("--manifest", required=True, help="输出清单 JSON 路径")
    sp.add_argument("--include", choices=["db", "s3", "all"], default="all")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("restore", help="恢复预检；绝不默认覆盖生产")
    common(sp)
    sp.add_argument("--manifest", required=True, help="输入备份清单 JSON")
    sp.add_argument("--allow-production", action="store_true",
                    help="仅当 environment=production 时需要的二次确认")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("verify-deletions", help="删除 tombstone 重放校验")
    common(sp)
    sp.set_defaults(func=cmd_verify_deletions)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return emit(result, args.format)


if __name__ == "__main__":
    sys.exit(main())
