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
import urllib.error
import urllib.request
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


def _target_db() -> str:
    """P13-a · 备份/对账的目标库名，**唯一真源** = ``FY_POSTGRES_DB``（默认 findyourself）。

    修复前的 bug：``pg_dump -d findyourself`` 硬编码库名——设了 ``FY_POSTGRES_DB``
    指向别的库时，备份会**静默备错库**且不报错（sha256 校验照常通过），灾备
    场景下要到恢复那一刻才发现。现在 dump 与对账查询统一用本函数。
    """
    return _env("FY_POSTGRES_DB", "findyourself")


def _build_artifact_store():
    """P13-b · 构造对象存储适配器；Settings 校验失败给**明确诊断**而非裸 pydantic 堆栈。

    陷阱（P11 演练实测）：shell 里 export ``FY_ENVIRONMENT=recovery`` 会让
    ``config.settings()`` 的校验器（只认 local/production/test）在 CLI 深处
    炸出难懂的 ValueError。这里转成带修复指引的人话报错。
    """
    try:
        from find_yourself.adapters.artifacts import build_artifact_store
        from find_yourself.config import settings
        return build_artifact_store(settings())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"对象存储适配器构造失败：{exc}。"
            "提示：shell 导出的 FY_ENVIRONMENT 会被 Settings 校验（只接受 local/production/test）；"
            "restore/backup 的 --environment 请走命令行参数，不要 export 进环境变量。"
        ) from exc


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


def _s3_ready(endpoint: str = "") -> tuple[bool, str]:
    ep = endpoint or os.environ.get("FY_S3_ENDPOINT", "")
    if ep:
        try:
            import urllib.request
            req = urllib.request.Request(f"{ep.rstrip('/')}/health/live", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return True, f"S3 endpoint {ep} healthy"
        except Exception:
            pass
    if _container_running("fy-minio"):
        return True, "container fy-minio running"
    return False, f"S3 unavailable (checked endpoint '{ep}' and fy-minio container)"


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
    s3_ready, s3_detail = _s3_ready()
    preflight.append({"component": "object_storage", "ready": s3_ready,
                      "detail": s3_detail})

    # ---- 计划模式（默认）：不执行 dump，只写计划 ----
    if not args.execute:
        planned = {
            "database_dump": f"pg_dump -> {target}/db-<utc>.sql (with --execute)",
            "object_prefix": "s3/mirror bucket (when s3 running)",
            "tombstones": "include tombstones table so restore can replay deletions",
            "retention_days": 30,
        }
        manifest = {
            "kind": "find_yourself.backup.manifest", "version": 1,
            "mode": "plan", "environment": args.environment,
            "created_at_utc": _utcnow(), "target": target, "include": args.include,
            "components": preflight, "planned": planned, "status": "PLANNED",
            "note": "Run with --execute to actually run pg_dump and snapshot object storage. This writes no data.",
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

    # ---- --execute：真实 pg_dump + 对象镜像 ----
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
        # P13-a：库名取 FY_POSTGRES_DB（修硬编码——设了别的库名必须真的备那个库）。
        r = subprocess.run(["docker", "exec", args.container, "pg_dump", "-U", "fy",
                            "-d", _target_db()],
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
    target_db = _target_db()
    _, ver = _pg(args.container, "show server_version;", db=target_db)
    _, pgdver = _run(["docker", "exec", args.container, "pg_dump", "--version"])
    _, tc = _pg(args.container,
                "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE';",
                db=target_db)
    try:
        table_count = int((tc or "0").strip() or 0)
    except ValueError:
        table_count = -1

    obj_info = {"status": "SKIPPED", "detail": s3_detail, "count": 0, "objects": []}
    covers_obj = False
    if s3_ready or os.path.exists(".runtime/artifacts"):
        try:
            store = _build_artifact_store()
            snap = store.backup_snapshot(target)
            obj_info = {
                "status": "OK",
                "detail": f"captured {snap.get('count', 0)} objects from {type(store).__name__}",
                "count": snap.get("count", 0),
                "objects": snap.get("objects", []),
            }
            covers_obj = True
        except Exception as e:
            obj_info = {"status": "FAIL", "detail": f"snapshot failed: {e}", "count": 0, "objects": []}
    else:
        obj_info = {"status": "SKIPPED", "detail": s3_detail, "count": 0, "objects": []}

    manifest = {
        "kind": "find_yourself.backup.manifest", "version": 1,
        "mode": "execute", "environment": args.environment,
        "created_at_utc": _utcnow(), "target": target,
        "db_dump": {
            "dir": target, "file": dump_name, "sha256": sha, "size_bytes": size,
            "table_count": table_count, "server_version": ver.strip(),
            "pg_dump_version": pgdver.strip(), "exit_code": dump_rc,
        },
        "object_storage": obj_info,
        "covers_object_storage": covers_obj,
        "note": "Full backup containing database dump and object storage snapshots. No credentials in this manifest."
        if covers_obj else "DB-only backup; object storage explicitly NOT included. No credentials in this manifest.",
    }
    os.makedirs(os.path.dirname(manifest_path) or ".", exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)

    overall = "OK" if covers_obj else "OK_DB_ONLY"
    return {
        "command": "backup", "status": overall, "exit_code": 0,
        "environment": args.environment, "recorded_at_utc": _utcnow(),
        "checks": [
            {"name": "database_dump", "status": "OK",
             "detail": f"{dump_name} {size}B tables={table_count} sha256={sha[:12]}..."},
            {"name": "object_storage", "status": obj_info["status"], "detail": obj_info["detail"]},
        ],
        "summary": "Full backup (database and object storage) written successfully."
        if covers_obj else f"DB dump written; object storage NOT covered ({obj_info['status']}). Restore must also mirror objects for a full recovery.",
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

    # 3b) 对象存储恢复与删除 Tombstone 重放
    tombstones_set = set()
    _, tb_out = _pg(args.container, "select target_id from tombstones;", db=rec_db)
    _, del_art_out = _pg(args.container, "select id from artifacts where deleted_at is not null;", db=rec_db)
    for line in (tb_out or "").splitlines():
        t = line.strip()
        if t and not t.startswith("target_id") and not t.startswith("-") and not t.startswith("(") and len(t) > 3:
            tombstones_set.add(t)
    for line in (del_art_out or "").splitlines():
        t = line.strip()
        if t and not t.startswith("id") and not t.startswith("-") and not t.startswith("(") and len(t) > 3:
            tombstones_set.add(t)

    obj_restore_result = {"status": "SKIPPED", "detail": "not in manifest"}
    if manifest.get("covers_object_storage"):
        try:
            store = _build_artifact_store()
            source_dir = db_info.get("dir") or os.path.dirname(os.path.abspath(args.manifest))
            res = store.restore_snapshot(source_dir, tombstones=tombstones_set)
            obj_restore_result = {
                "status": "OK",
                "detail": f"restored {res['restored']} objects, suppressed {res['skipped_tombstones']} tombstoned artifacts",
                "restored": res["restored"],
                "skipped_tombstones": res["skipped_tombstones"],
            }
        except Exception as e:
            obj_restore_result = {"status": "FAIL", "detail": f"object restore failed: {e}"}

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

    status = "OK" if (tables_match and obj_restore_result["status"] in {"OK", "SKIPPED"}) else "FAIL"
    code = 0 if (tables_match and obj_restore_result["status"] in {"OK", "SKIPPED"}) else 2
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
            {"name": "object_storage_restore", "status": obj_restore_result["status"],
             "detail": obj_restore_result["detail"]},
        ],
        "summary": "Recovered into throwaway recovery DB and restored object storage with tombstone replay."
        if manifest.get("covers_object_storage") else f"recovered into throwaway db; tables match={tables_match}. Object storage replay still required for full recovery.",
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
def cmd_reap_queued(args: argparse.Namespace) -> dict:
    """Reclaim queued tasks that will never run (C0 / R-13).

    Intentionally does not consult Temporal: the only scenario this command
    exists for is Temporal being unavailable, so a Temporal-dependent reaper
    would be dead code exactly when it is needed.
    """
    from .db.session import engine_from_url, session_factory
    from .services.task_reaper import (
        find_stuck_queued_tasks,
        reap_stuck_queued_tasks,
    )

    stuck_minutes = max(1, int(getattr(args, "stuck_minutes", 5)))
    dry_run = bool(getattr(args, "dry_run", False))

    engine = engine_from_url(_host_db_url())
    Session = session_factory(engine)
    session = Session()
    try:
        if dry_run:
            stuck = find_stuck_queued_tasks(session, stuck_minutes=stuck_minutes)
            return {
                "command": "reap-queued",
                "dry_run": True,
                "stuck_minutes": stuck_minutes,
                "would_reap_count": len(stuck),
                "would_reap_ids": [t.id for t in stuck][:50],
            }
        report = reap_stuck_queued_tasks(session, stuck_minutes=stuck_minutes)
        return {
            "command": "reap-queued",
            "dry_run": False,
            "stuck_minutes": stuck_minutes,
            **report.as_dict(),
        }
    finally:
        session.close()
        engine.dispose()


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
                        default="local",
                        choices=["local", "test", "production", "recovery"],
                        help="目标环境（默认 local；**不走** FY_ENVIRONMENT 环境变量——"
                             "该变量由 Settings 严格校验，export 非法值会破坏 Settings 消费方）")
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

    sp = sub.add_parser("reap-queued", help="回收卡死的 queued 任务（R-13，独立于 Temporal）")
    common(sp)
    sp.add_argument("--stuck-minutes", type=int, default=5,
                    help="queued 超过多少分钟视为卡死（默认 5）")
    sp.add_argument("--dry-run", action="store_true",
                    help="只报告不修改")
    sp.set_defaults(func=cmd_reap_queued)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    return emit(result, args.format)


if __name__ == "__main__":
    sys.exit(main())
