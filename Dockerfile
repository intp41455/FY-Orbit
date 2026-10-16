# syntax=docker/dockerfile:1
# =====================================================================
# FY Orbit · 星轨 — 单镜像构建（前端 + 后端一体）
#
# 三个阶段：
#   webbuilder : Node 22 构建前端（干净检出没有 web/dist，必须在此构建）
#   builder    : uv 安装 Python 依赖
#   runtime    : 非 root 运行时（入口脚本自动生成会话密钥 + 自动迁移 → 零手工配置）
#
# 构建：docker build -t find-yourself-orbit:latest .
# 运行：docker run -d -p 8000:8000 -v fy_data:/app/.runtime find-yourself-orbit:latest
# 详见 docs/docker.md
# =====================================================================

# ---- Stage 0 · 前端构建 ---------------------------------------------
FROM node:22-slim AS webbuilder
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# npm run build = tsc --noEmit && vite build（类型门禁 + 产出 /web/dist）
RUN npm run build

# ---- Stage 1 · Python 依赖 ------------------------------------------
FROM python:3.13-slim AS builder
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
COPY pyproject.toml ./
RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN uv pip install --no-cache -r pyproject.toml
COPY src/ ./src/
RUN uv pip install --no-cache --no-deps -e .

# ---- Stage 2 · 运行时 -----------------------------------------------
FROM python:3.13-slim AS runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/bash -m appuser

# 应用文件
COPY --chown=appuser:appgroup alembic.ini ./
COPY --chown=appuser:appgroup migrations/ ./migrations/
COPY --chown=appuser:appgroup src/ ./src/
COPY --chown=appuser:appgroup pyproject.toml ./
# 前端产物来自 webbuilder 阶段（不再依赖检出树里恰好有 web/dist）
COPY --from=webbuilder --chown=appuser:appgroup /web/dist/ /app/web/dist/
ENV FY_STATIC_DIR=/app/web/dist

# 入口脚本：首启自动生成会话密钥 + 自动迁移（零手工配置）
COPY --chown=appuser:appgroup docker-entrypoint.sh /app/docker-entrypoint.sh
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod 755 /app/docker-entrypoint.sh

RUN mkdir -p /app/.runtime/artifacts /app/.runtime/assets && \
    chown -R appuser:appgroup /app/.runtime

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/health/live || exit 1

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
