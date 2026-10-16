# Docker 部署指南（单机形态）

> 目标：**一条命令跑起来，零手工配置**。数据落在 Docker 卷里，升级镜像不丢数据。

## 快速开始（三步）

```bash
git clone https://github.com/intp41455/FY-Orbit.git
cd FY-Orbit
docker compose up -d --build
```

然后浏览器打开 <http://localhost:8000> ——完成。无需注册、无需联网、无需手写任何配置文件。

## 首次启动发生了什么

入口脚本（`docker-entrypoint.sh`）按顺序做三件事：

1. **自动生成密钥**：`FY_SESSION_SECRET` 与 `FY_LOCAL_TOKEN` 首启自动生成，写入数据卷内的 `secrets.env`（权限 600）；重启沿用，不会变。若你在 environment 里显式提供了值，脚本尊重你的值、绝不覆盖。
2. **数据库迁移**：`alembic upgrade head`（幂等，已迁移则空跑）。
3. **启动服务**：uvicorn 监听 8000，健康检查 `/health/live`。

## 数据在哪

| 内容 | 位置 |
|---|---|
| SQLite 数据库、密钥、上传资产 | Docker 卷 `fy_orbit_data`（容器删了也在） |
| 备份 | `docker run --rm -v fy_orbit_data:/data -v "$PWD":/backup alpine tar czf /backup/fy-backup.tgz /data` |

## 接本地模型（Ollama，可选）

宿主机上装有 Ollama 的话，容器内通过 `host.docker.internal` 访问它：

```yaml
# docker-compose.yml 的 environment 追加：
- FY_MODEL_PROVIDER=ollama
- FY_MODEL_BASE_URL=http://host.docker.internal:11434
- FY_MODEL_NAME=qwen2.5:7b-instruct
```

本地模型属于"本机推理"，**不受离线门影响**（无需 `FY_OFFLINE_MODE=0`）——推理流量到的是宿主机，不是外网。

## 改端口

```bash
# 把 compose 里的 "8000:8000" 改成 "9000:8000" 即可（宿主端口:容器端口）
```

## 常见问题

| 症状 | 处置 |
|---|---|
| `port is already allocated` | 8000 被占用：改映射端口，或 `docker compose down` 后重试 |
| 忘了密钥想重新生成 | 删除卷内 secrets.env：`docker run --rm -v fy_orbit_data:/d alpine rm /d/secrets.env` 后重启（**注意：会使既有会话全部失效**，数据不受影响） |
| 构建很慢 | 确认 `.dockerignore` 生效（首次构建需拉基础镜像与依赖，之后有缓存） |
| 想升级版本 | `git pull && docker compose up -d --build`（数据在卷里，不丢） |

## 生产形态

单机 Compose 形态是**本地/试用定位**（SQLite + HTTP + 本地令牌）。生产部署需要：HTTPS 反向代理、PostgreSQL、Temporal、对象存储、OIDC——并在环境里设 `FY_ENVIRONMENT=production`，服务端会逐项强制校验（缺一项 fail-fast，不静默降级）。详见 `src/find_yourself/config.py::validate_security`。
