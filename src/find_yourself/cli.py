"""Find Yourself 运维 CLI（Infra 分片）。

仅使用 Python 标准库，不依赖任何尚未锁定的第三方包；对 Core/Runtime 服务
一律 lazy import，服务尚未就绪时返回明确的 NOT_RUN，而不是崩溃或伪造成功。

子命令：
    doctor            环境与依赖健康检查（不输出连接串/密钥）
    backup            默认计划模式；--execute 才用 pg_dump 真实落盘（S3 未起则 SKIPPED）
    restore           默认预检；--execute 仅在 recovery/local/test 用临时库真实恢复并对账
    verify-deletions  默认可达性预检；--execute 才 lazy 调 Core DeletionService 重放校验

退出码约定：
    0  成功 / 全部检查通过（DB-only 备份会显式标注对象存储未覆盖）
    1  参数或内部错误
    2  存在 FAIL 项
    3  运行了，但必需服务/依赖未就绪（NOT_RUN）
    4  被安全守卫拒绝（例如未显式确认就恢复到生产）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# 通用输出
# --------------------------------------------------------------------------- #
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
# 探测 / docker 原语（全部 stdlib；失败返回 NOT_RUN 语义，不抛给上层）
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
        tail = database_url.split("://", 1)[1]
        host_part = tail.split("/", 1)[0]
        host_part = host_part.split("@", 1)[-1]  # 去掉 user:pass
        if ":" in host_part:
            host, port = host_part.rsplit(":", 1)
            return (host, int(port))
        return (host_part, 5432)
    except (ValueError, IndexError):
        return None


def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    """运行外部命令，返回 (rc, 合并输出)。不打印任何环境变量值。"""
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip()
    except FileNotFoundError:
        return 127, "command not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def _container_running(name: str) -> bool:
    rc, out = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=15)
    return rc == 0 and out.strip() == "true"


def _pg(container: str, sql: str, db: str = "findyourself") -> tuple[int, str]:
    """容器内 psql 查询；本地 socket trust，命令行不含口令。"""
    return _run(["docker", "exec", container, "psql", "-U", "fy", "-d", db, "-tAc", sql])


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _host_db_url() -> str:
    """主机侧连接串；优先 FY_DATABASE_URL，否则按本地回环映射构造。口令只用于连接，绝不回显。"""
    url = _env("FY_DATABASE_URL")
    if url:
        return url
    user = _env("FY_POSTGRES_USER", "fy")
    pwd = _env("FY_POSTGRES_PASSWORD", "fy_dev_change_me_not_for_prod")
    db = _env("FY_POSTGRES_DB", "findyourself")
    return f"postgresql+psycopg://{user}:{pwd}@127.0.0.1:5432/{db}"


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #
def cmd_doctor(args: argparse.Namespace) -> dict:
    checks: list[dict] = []

    checks.append({
        "name": "python", "status": "OK",
        "detail": f"{sys.version.split()[0]} {sys.implementation.name}",
    })

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

    cfg_status, cfg_detail = _probe_config(args.environment)
    checks.append({"name": "config", "status": cfg_status, "detail": cfg_detail})

    db_url = _env("FY_DATABASE_URL")
    tgt = parse_db_target(db_url)
    if tgt is None:
        checks.append({"name": "database", "status": "NOT_RUN",
                       "detail": "FY_DATABASE_URL not a postgresql target"})
    else:
        host, port = tgt
        up = tcp_probe(host, port)
        checks.append({"name": "database", "status": "OK" if up else "NOT_RUN",
                       "detail": f"tcp {host}:{port} " + ("open" if up else "closed")})

    taddr = _env("FY_TEMPORAL_ADDRESS", "temporal:7233")
    thost = taddr.split(":")[0]
    tport = int(taddr.split(":")[1]) if ":" in taddr else 7233
    t_up = tcp_probe(thost, tport)
    checks.append({"name": "temporal", "status": "OK" if t_up else "NOT_RUN",
                   "detail": f"grpc {thost}:{tport} " + ("open" if t_up else "closed")})

    s3 = _env("FY_S3_ENDPOINT")
    if not s3:
        checks.append({"name": "object_storage", "status": "NOT_RUN",
                       "detail": "FY_S3_ENDPOINT not set"})
    else:
        ok, detail = http_probe(s3.rstrip("/") + "/minio/health/live")
        checks.append({"name": "object_storage", "status": "OK" if ok else "NOT_RUN",
                       "detail": f"{s3} {detail}"})

    api_base = _env("FY_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/")
    ok, detail = http_probe(api_base + "/health/live")
    checks.append({"name": "api", "status": "OK" if ok else "NOT_RUN",
                   "detail": f"{api_base}/health/live {detail}"})

    model_configured = bool(_env("FY_MODEL_API_KEY") and _env("FY_MODEL_BASE_URL"))
    checks.append({"name": "model_gateway",
                   "status": "OK" if model_configured else "NOT_RUN",
                   "detail": "configured" if model_configured else "disabled (cold start, no paid call)"})

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
    try:
        from find_yourself.config import settings  # type: ignore
        settings()
        return "OK", "settings loaded"
    except Exception as e:  # noqa: BLE001
        return "NOT_RUN", f"settings not usable in this env: {type(e).__name__}"


# --------------------------------------------------------------------------- #
# backup
# --------------------------------------------------------------------------- #
def cmd_backup(args: argparse.Namespace) -> dict:
    target = os.path.abspath(args.target)
    manifest_path = os.path.abspath(args.manifest)

    preflight: list[dict] = []
    db_ready = _container_running(args.container)
    preflight.append({"component": "database", "ready": db_ready,
                      "detail": f"container {args.container} " + ("running" if db_ready else "not running")})
    s3_ready = _container_running("fy-minio")
    preflight.append({"component": "object_storage", "ready": s3_ready,
                      "detail": "container fy-minio running" if s3_ready else "container fy-minio not running"})

    # ---- 计划模式（默认）：不执行 dump，只写计划 ----
    if not args.execute:
        planned = {
            "database_dump": f"pg_dump -> {target}/db-<utc>.sql (with --execute)",
            "object_prefix": "minio/mirror bucket (when minio running)",
            "tombstones": "include tombstones table so restore can replay deletions",
            "retention_days": 30,
        }
        manifest = {
            "kind": "find_yourself.backup.manifest", "version": 1,
            "mode": "plan", "environment": args.environment,
            "created_at_utc": _utcnow(), "target": target, "include": args.include,
            "components": preflight, "planned": planned, "status": "PLANNED",
            "note": "Run with --execute to actually run pg_dump. This writes no data.",
        }
        os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        return {
            "command": "backup", "status": "PLANNED", "exit_code": 0,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": c["component"], "status": "NOT_RUN", "detail": c["detail"]}
                       for c in preflight],
            "summary": "plan mode; pass --execute to dump",
            "manifest": manifest_path,
        }

    # ---- --execute：真实 pg_dump ----
    if not db_ready:
        return {
            "command": "backup", "status": "NOT_RUN", "exit_code": 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "database", "status": "NOT_RUN",
                        "detail": f"container {args.container} not running; no dump written"}],
            "summary": "database container not running; aborting execute",
        }

    os.makedirs(target, exist_ok=True)
    dump_name = f"db-{_compact()}.sql"
    dump_path = os.path.join(target, dump_name)

    with open(dump_path, "wb") as f:
        r = subprocess.run(["docker", "exec", args.container, "pg_dump", "-U", "fy", "-d", "findyourself"],
                           stdout=f, stderr=subprocess.PIPE)
    dump_rc = r.returncode

    if dump_rc != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-400:]
        return {
            "command": "backup", "status": "FAIL", "exit_code": 2,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "database_dump", "status": "FAIL", "detail": f"pg_dump rc={dump_rc}: {err}"}],
            "summary": "pg_dump failed; dump may be partial",
        }

    size = os.path.getsize(dump_path)
    sha = _sha256_file(dump_path)
    _, ver = _pg(args.container, "show server_version;")
    _, pgdver = _run(["docker", "exec", args.container, "pg_dump", "--version"])
    _, tc = _pg(args.container,
                "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE';")
    try:
        table_count = int((tc or "0").strip() or 0)
    except ValueError:
        table_count = -1

    obj_status = "SKIPPED" if not s3_ready else "NOT_IMPLEMENTED"
    obj_detail = ("minio container not running; object storage NOT captured"
                  if not s3_ready else
                  "minio running; object mirror not implemented in this revision")

    manifest = {
        "kind": "find_yourself.backup.manifest", "version": 1,
        "mode": "execute", "environment": args.environment,
        "created_at_utc": _utcnow(), "target": target,
        "db_dump": {
            "dir": target, "file": dump_name, "sha256": sha, "size_bytes": size,
            "table_count": table_count, "server_version": ver.strip(),
            "pg_dump_version": pgdver.strip(), "exit_code": dump_rc,
        },
        "object_storage": {"status": obj_status, "detail": obj_detail},
        "covers_object_storage": False,
        "note": "DB-only backup; object storage explicitly NOT included. No credentials in this manifest.",
    }
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    overall = "OK_DB_ONLY"
    return {
        "command": "backup", "status": overall, "exit_code": 0,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": [
            {"name": "database_dump", "status": "OK",
             "detail": f"{dump_name} {size}B tables={table_count} sha256={sha[:12]}..."},
            {"name": "object_storage", "status": obj_status, "detail": obj_detail},
        ],
        "summary": f"DB dump written; object storage NOT covered ({obj_status}). "
                   f"Restore must also mirror objects for a full recovery.",
        "manifest": manifest_path,
    }


# --------------------------------------------------------------------------- #
# restore
# --------------------------------------------------------------------------- #
def cmd_restore(args: argparse.Namespace) -> dict:
    # 守卫：environment 必须显式给出；生产一律拒绝（--execute 也不例外）。
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

    if args.environment == "production":
        if not (manifest.get("approved_by") and manifest.get("approved_at_utc")):
            return {
                "command": "restore", "status": "REFUSED", "exit_code": 4,
                "environment": args.environment, "recorded_at_utc": _utcnow(),
                "checks": [], "summary":
                    "Production restore refused: manifest lacks approved_by/approved_at_utc approval record.",
            }

    # ---- 默认预检 ----
    if not args.execute:
        db_ready = _container_running(args.container)
        return {
            "command": "restore", "status": "OK" if db_ready else "NOT_RUN",
            "exit_code": 0 if db_ready else 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "database_target", "status": "OK" if db_ready else "NOT_RUN",
                        "detail": f"target env = {args.environment}; container running={db_ready}; "
                                  f"pass --execute to actually restore"}],
            "summary": "preflight only; pass --execute to restore into a throwaway recovery DB",
            "manifest": os.path.abspath(args.manifest),
        }

    # ---- --execute：仅允许非生产环境 ----
    if args.environment == "production":
        return {
            "command": "restore", "status": "REFUSED", "exit_code": 4,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [], "summary":
                "--execute into production is refused; run recovery against local/test/recovery first.",
        }

    if not _container_running(args.container):
        return {
            "command": "restore", "status": "NOT_RUN", "exit_code": 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "database_target", "status": "NOT_RUN",
                        "detail": f"container {args.container} not running"}],
            "summary": "database container not running",
        }

    db_info = manifest.get("db_dump") or {}
    dump_name = db_info.get("file")
    if not dump_name:
        return {
            "command": "restore", "status": "ERROR", "exit_code": 1,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [], "summary": "manifest has no db_dump.file; nothing to restore",
        }
    dump_path = os.path.join(db_info.get("dir") or os.path.dirname(os.path.abspath(args.manifest)),
                            dump_name)
    if not os.path.exists(dump_path):
        return {
            "command": "restore", "status": "FAIL", "exit_code": 2,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [], "summary": f"dump file missing next to manifest: {dump_path}",
        }

    # 1) 校验 dump sha256 与清单一致
    actual_sha = _sha256_file(dump_path)
    if db_info.get("sha256") and actual_sha != db_info["sha256"]:
        return {
            "command": "restore", "status": "FAIL", "exit_code": 2,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "dump_integrity", "status": "FAIL",
                        "detail": f"sha256 mismatch: manifest={db_info['sha256'][:12]}... actual={actual_sha[:12]}..."}],
            "summary": "aborting restore; dump does not match manifest",
        }

    # 2) 创建唯一临时 recovery DB 并管道恢复
    rec_db = f"fy_recovery_{_compact()}"
    rc, out = _run(["docker", "exec", args.container, "createdb", "-U", "fy", rec_db])
    if rc != 0:
        return {
            "command": "restore", "status": "FAIL", "exit_code": 2,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "createdb", "status": "FAIL", "detail": out[-300:]}],
            "summary": f"cannot create recovery db {rec_db}",
        }

    with open(dump_path, "rb") as f:
        r = subprocess.run(["docker", "exec", "-i", args.container, "psql", "-U", "fy",
                           "-d", rec_db, "-v", "ON_ERROR_STOP=1", "-q"],
                          stdin=f, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    restore_rc = r.returncode

    # 3) 对账：临时库表计数 vs 清单
    _, tc_out = _pg(args.container,
                    "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE';",
                    db=rec_db)
    try:
        rec_tables = int((tc_out or "0").strip() or 0)
    except ValueError:
        rec_tables = -1
    manifest_tables = int(db_info.get("table_count", -1))
    tables_match = (manifest_tables == rec_tables)

    # 4) 清理临时库（--keep-db 时保留，便于在恢复库上跑后续校验）
    dropped = True
    if getattr(args, "keep_db", False):
        dropped = False
    else:
        _run(["docker", "exec", args.container, "dropdb", "-U", "fy", "--if-exists", rec_db])

    if restore_rc != 0:
        err = (r.stderr or b"").decode("utf-8", "replace")[-400:]
        # 失败总是清理
        if not dropped:
            _run(["docker", "exec", args.container, "dropdb", "-U", "fy", "--if-exists", rec_db])
        return {
            "command": "restore", "status": "FAIL", "exit_code": 2,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "psql_restore", "status": "FAIL", "detail": f"rc={restore_rc}: {err}"}],
            "summary": f"restore into {rec_db} failed; temp db dropped",
        }

    status = "OK" if tables_match else "FAIL"
    code = 0 if tables_match else 2
    return {
        "command": "restore", "status": status, "exit_code": code,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "recovery_db": rec_db, "recovery_db_kept": (not dropped),
        "checks": [
            {"name": "dump_integrity", "status": "OK", "detail": f"sha256 match ({actual_sha[:12]}...)"},
            {"name": "temporary_db", "status": "OK",
             "detail": f"created+restored {rec_db}" + ("; kept (--keep-db)" if not dropped else "; then dropped")},
            {"name": "table_count", "status": "OK" if tables_match else "FAIL",
             "detail": f"manifest={manifest_tables} recovered={rec_tables}"},
        ],
        "summary": f"recovered into throwaway db; tables match={tables_match}. "
                   f"Object storage replay still required for full recovery.",
        "manifest": os.path.abspath(args.manifest),
    }


# --------------------------------------------------------------------------- #
# verify-deletions
# --------------------------------------------------------------------------- #
def cmd_verify_deletions(args: argparse.Namespace) -> dict:
    # 可达性预检：仅可达不算 PASS。
    if not args.execute:
        reachable = _container_running(args.container)
        return {
            "command": "verify-deletions", "status": "NOT_RUN", "exit_code": 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "tombstones", "status": "NOT_RUN",
                        "detail": f"container running={reachable}; reachability alone is NOT a PASS. "
                                  f"pass --execute to run Core replay verification."}],
            "summary": "preflight only; no replay executed",
        }

    # --execute：lazy 装配 Core 的 DeletionService.verify_replay。
    try:
        from find_yourself.db.session import engine_from_url, session_factory  # type: ignore
        from find_yourself.services.audit import AuditService  # type: ignore
        from find_yourself.services.deletion import DeletionService  # type: ignore
    except Exception as e:  # noqa: BLE001
        return {
            "command": "verify-deletions", "status": "NOT_RUN", "exit_code": 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "core_service", "status": "NOT_RUN",
                        "detail": f"cannot import Core deletion/audit/session: {type(e).__name__}"}],
            "summary": "waiting for Core services to be importable",
        }

    url = _host_db_url()
    if not url:
        return {
            "command": "verify-deletions", "status": "NOT_RUN", "exit_code": 3,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "database_url", "status": "NOT_RUN",
                        "detail": "no FY_DATABASE_URL / postgres credentials available"}],
            "summary": "cannot connect without a DB url",
        }

    engine = session = None
    try:
        engine = engine_from_url(url)
        session = session_factory(engine)()
        audit = AuditService(session)
        svc = DeletionService(session, audit)
        data = svc.verify_replay()
    except Exception as e:  # noqa: BLE001
        return {
            "command": "verify-deletions", "status": "FAIL", "exit_code": 2,
            "environment": args.environment, "recorded_at_utc": _utcnow(),
            "checks": [{"name": "replay", "status": "FAIL",
                        "detail": f"{type(e).__name__}: {e}"}],
            "summary": "Core verify_replay raised",
        }
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # noqa: BLE001
                pass
        if engine is not None:
            try:
                engine.dispose()
            except Exception:  # noqa: BLE001
                pass

    tombstones = data.get("tombstones", []) if isinstance(data, dict) else []
    violations: list[str] = []
    for t in tombstones:
        violations.extend(t.get("violations", []))
    ok = bool(data.get("ok")) if isinstance(data, dict) else False
    ok = ok and not violations
    status = "OK" if ok else "FAIL"
    code = 0 if ok else 2
    return {
        "command": "verify-deletions", "status": status, "exit_code": code,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": [{"name": "tombstones", "status": "OK" if ok else "FAIL",
                    "detail": f"checked={data.get('checked','?') if isinstance(data,dict) else '?'} "
                              f"violations={len(violations)}"}],
        "details": data,
        "summary": f"replay verified; tombstones={len(tombstones)} violations={len(violations)}",
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
        sp.add_argument("--container", default="fy-postgres",
                        help="Postgres 容器名（默认 fy-postgres）")

    sp = sub.add_parser("doctor", help="健康检查，不输出连接串/密钥")
    common(sp)
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("backup", help="备份；默认计划模式，--execute 才真实 pg_dump")
    common(sp)
    sp.add_argument("--target", required=True, help="已批准的备份目的地目录")
    sp.add_argument("--manifest", required=True, help="输出清单 JSON 路径")
    sp.add_argument("--include", choices=["db", "s3", "all"], default="all")
    sp.add_argument("--execute", action="store_true",
                    help="真实执行 pg_dump；不带则只写计划")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("restore", help="恢复；默认预检，--execute 仅在非生产临时库恢复")
    common(sp)
    sp.add_argument("--manifest", required=True, help="输入备份清单 JSON")
    sp.add_argument("--allow-production", action="store_true",
                    help="仅当 environment=production 时需要的二次确认")
    sp.add_argument("--execute", action="store_true",
                    help="在唯一临时 recovery DB 中真实恢复并对账")
    sp.add_argument("--keep-db", action="store_true",
                    help="恢复后保留临时 recovery DB（用于后续在恢复库上校验）")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("verify-deletions", help="删除 tombstone 重放校验")
    common(sp)
    sp.add_argument("--execute", action="store_true",
                    help="lazy 调 Core DeletionService.verify_replay")
    sp.set_defaults(func=cmd_verify_deletions)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return emit(result, args.format)


if __name__ == "__main__":
    sys.exit(main())
