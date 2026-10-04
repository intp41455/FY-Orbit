# P9 · 沙箱执行镜像（按需自建；默认 runner 直接使用 IMAGE 文件中按 digest 固定的
# python:3.12-alpine 上游镜像）。自建镜像必须以同一 digest 为 FROM 锚点，
# 且禁止在镜像内放置任何凭据/网络工具包以外的能力增强。
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

# 只读根文件系统 + 非 root 运行由 runner 的 docker run 参数强制（--read-only
# --user 65534:65534），镜像内不做任何特权配置。
RUN mkdir -p /workspace && chown 65534:65534 /workspace
USER 65534:65534
WORKDIR /workspace
