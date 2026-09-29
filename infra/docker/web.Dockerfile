# Find Yourself Web (React + TS + Vite) 开发镜像
# 由 Web 分片维护 web/ 源码与 package.json；本文件只提供构建/运行骨架。
# 开发态运行 vite dev server；生产应改为多阶段构建静态产物并用只读 nginx 托管。

FROM node:22-alpine AS runtime

# 非 root 用户（alpine 中 node 镜像已有 node 用户 uid 1000，直接复用）。
WORKDIR /app

ENV CI=1

# 仅先拷清单以利用缓存；真正源码由 Web 分片在 web/ 下提供。
COPY web/package.json web/package-lock.json* ./

RUN if [ -f package-lock.json ]; then npm ci; else echo "[warn] web/package-lock.json 尚未生成；由 Web 分片提供"; fi

COPY web/ ./

# 切回非 root。
USER node

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
