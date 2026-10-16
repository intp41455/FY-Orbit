#!/bin/sh
# =====================================================================
# FY Orbit · 容器入口
# 职责（零手工配置）：
#   1. 首启自动生成 FY_SESSION_SECRET / FY_LOCAL_TOKEN，持久化到数据卷
#   2. 幂等执行数据库迁移（alembic upgrade head）
#   3. exec 启动 uvicorn
# 已显式提供的变量一律尊重，绝不覆盖。
# =====================================================================
set -e

RUNTIME_DIR=/app/.runtime
SECRETS_FILE="$RUNTIME_DIR/secrets.env"

mkdir -p "$RUNTIME_DIR"
touch "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE" 2>/dev/null || true

# -- 载入上次生成的密钥（若存在） -------------------------------------
# 必须 export：`.` 只定义 shell 变量，不传给 exec 出去的 uvicorn 子进程；
# 少了 export 就是"首次启动正常、重启必崩"（Settings 拿到空密钥）。
# shellcheck disable=SC1090
if [ -f "$SECRETS_FILE" ]; then
    set -a
    . "$SECRETS_FILE"
    set +a
fi

gen() { python -c "import secrets; print(secrets.token_urlsafe(48))"; }

# -- FY_SESSION_SECRET：>=32 字符，缺失则生成并持久化 -----------------
if [ -z "${FY_SESSION_SECRET:-}" ] || [ "${#FY_SESSION_SECRET}" -lt 32 ]; then
    FY_SESSION_SECRET="$(gen)"
    export FY_SESSION_SECRET
    # 只追加缺失项（幂等：重启不会重复追加）
    grep -q '^FY_SESSION_SECRET=' "$SECRETS_FILE" 2>/dev/null || \
        printf 'FY_SESSION_SECRET=%s\n' "$FY_SESSION_SECRET" >> "$SECRETS_FILE"
    echo "[entrypoint] 已自动生成 FY_SESSION_SECRET（持久化于 $SECRETS_FILE）"
fi

# -- FY_LOCAL_TOKEN：本地访问令牌，缺失则生成 -------------------------
if [ -z "${FY_LOCAL_TOKEN:-}" ]; then
    FY_LOCAL_TOKEN="$(gen)"
    export FY_LOCAL_TOKEN
    grep -q '^FY_LOCAL_TOKEN=' "$SECRETS_FILE" 2>/dev/null || \
        printf 'FY_LOCAL_TOKEN=%s\n' "$FY_LOCAL_TOKEN" >> "$SECRETS_FILE"
    echo "[entrypoint] 已自动生成 FY_LOCAL_TOKEN"
fi

# -- 数据库迁移（幂等；已迁移则空跑） ---------------------------------
echo "[entrypoint] 执行 alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] 启动 uvicorn (0.0.0.0:8000)"
exec uvicorn find_yourself.api.main:app --host 0.0.0.0 --port 8000
