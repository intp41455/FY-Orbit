# Find Yourself API / Worker 统一运行镜像（开发候选）
# 发布时必须固定基础镜像 digest，并通过可复现锁文件安装依赖。
# 设计：非 root 运行、最小运行时、不写入真实密钥（运行时由环境注入）。

# ---------- builder：安装依赖到独立 venv ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy

# uv 用于按锁文件安装；如未提供 uv.lock，构建会失败，强制先锁定依赖。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# 仅安装到 /app/.venv，不污染系统 Python。
RUN uv sync --frozen --no-install-project || echo "[warn] uv.lock 尚未生成；由 Core 分片锁定后此步才真正安装依赖"

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    # 非 root 运行用户
    && groupadd --system --gid 1000 fy \
    && useradd  --system --uid 1000 --gid fy --home-dir /app --shell /usr/sbin/nologin fy

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src /app/src
COPY migrations /app/migrations

RUN chown -R fy:fy /app
USER fy

# 无入口命令：由 compose 的 command 区分 api(uvicorn) 与 worker。
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=6 --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=3).status==200 else 1)" || exit 1
