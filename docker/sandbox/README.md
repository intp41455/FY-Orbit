# P9 · 沙箱镜像与网络

* `IMAGE` — runner 实际使用的**按 digest 固定**的镜像引用（供应链锚点，禁用
  可变 tag 运行）。
* `Dockerfile` — 可选的自建沙箱镜像（FROM 同一 digest；非 root；无任何凭据）。
* 隔离网络 `fy-sandbox-net` 由 `runtime/sandbox_container.py::ensure_network`
  幂等创建：**internal=true**（无外网路由），与 app 网络/共享开发容器
  （fy-postgres 等 bridge 网络）经 docker 的 DOCKER-ISOLATION 规则天然不互通。
* 资源配额由 runner 的 docker run 参数强制：`--memory` / `--cpus` /
  `--pids-limit` / `--read-only` / `--cap-drop ALL` / `--security-opt
  no-new-privileges` / `--init`（tini 转发信号并收割僵尸，S-1/S-2 的容器态对应）。
