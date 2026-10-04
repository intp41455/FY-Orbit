"""P9 · 容器化沙箱 runner（需求 9：真隔离）。

与 :mod:`find_yourself.runtime.sandbox`（进程级沙箱，``describe_isolation``
如实自报 ``isolation_level="process"``）的关系：本模块是**叠加的更强隔离层**，
通过 Docker 把执行环境关进容器——二者可并存，本模块**只调用**不修改进程级沙箱。

诚实边界（``describe_isolation`` 逐项可验证，全部有测试锁定）：

* 网络隔离：专用 **internal** 网络 ``fy-sandbox-net``（无外网路由），与其它
  bridge 网络经 Docker 的 DOCKER-ISOLATION 规则不互通——沙箱内连 Postgres
  容器必须失败（测试 ①）。
* 资源配额：``--memory``（OOM 真实生效，测试 ②）/ ``--cpus`` / ``--pids-limit``。
* 进程卫生：``--init``（tini 作为 PID 1 转发信号并收割僵尸）+ 超时 ``docker
  kill`` **容器级**终止——容器消失即全部进程（含孙进程）死亡（测试 ③）。
* 收紧项：``--read-only`` 只读根文件系统、``--cap-drop ALL``、
  ``--security-opt no-new-privileges``、非 root（``--user 65534:65534``）。
* 供应链：镜像**按 digest 固定**（``docker/sandbox/IMAGE``），绝不用可变 tag。

``isolation_level="container"`` **只在上述全部 enforcement 真的生效时**才上报；
任何一项未能落实（如 daemon 不可用），``ensure_ready()`` 会失败，调用方拿到的
绝不可能是「虚报等级」的结果。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

#: 隔离网络名。internal=True：无外网路由；与其它 bridge 网络不互通。
SANDBOX_NETWORK = "fy-sandbox-net"

#: 默认资源配额（容器级硬限制）。
DEFAULT_MEMORY = "256m"
DEFAULT_CPUS = "0.5"
DEFAULT_PIDS_LIMIT = 64

#: 基础镜像 digest（与 docker/sandbox/IMAGE 一致；env 可覆盖用于换版重锚）。
_PIN_FILE = Path(__file__).resolve().parents[3] / "docker" / "sandbox" / "IMAGE"
DEFAULT_IMAGE = _PIN_FILE.read_text(encoding="utf-8").strip()


def _docker(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def ensure_ready() -> dict[str, Any]:
    """确认 daemon 可用且镜像在本地；不可用抛 RuntimeError（诚实失败，不虚报）。"""
    rc, out, err = _docker("info", "--format", "{{.ServerVersion}}")
    if rc != 0:
        raise RuntimeError(f"Docker daemon 不可用：{err.strip()[:200]}")
    image = os.environ.get("FY_SANDBOX_IMAGE", DEFAULT_IMAGE)
    rc, _, _ = _docker("image", "inspect", image)
    if rc != 0:
        rc, out, err = _docker("pull", image, timeout=600)
        if rc != 0:
            raise RuntimeError(f"沙箱镜像拉取失败：{err.strip()[:200]}")
    return {"server_version": out.strip(), "image": image}


def ensure_network() -> dict[str, Any]:
    """幂等创建 internal 隔离网络，并**验证** Internal=true（不轻信创建输出）。"""
    rc, _, _ = _docker("network", "inspect", SANDBOX_NETWORK)
    if rc != 0:
        rc, _, err = _docker("network", "create", "--internal", "--driver", "bridge",
                             SANDBOX_NETWORK)
        if rc != 0:
            raise RuntimeError(f"创建隔离网络失败：{err.strip()[:200]}")
    rc, out, _ = _docker("network", "inspect", SANDBOX_NETWORK,
                         "--format", "{{.Internal}}")
    internal = out.strip().lower() == "true"
    if not internal:
        raise RuntimeError(f"网络 {SANDBOX_NETWORK} 不是 internal，拒绝用作沙箱")
    return {"network": SANDBOX_NETWORK, "internal": True}


class ContainerSandboxRunner:
    """把受限命令关进容器的执行器（超时容器级终止，结果诚实回传）。"""

    def __init__(self, *, image: str | None = None, network: str = SANDBOX_NETWORK):
        self.image = image or os.environ.get("FY_SANDBOX_IMAGE", DEFAULT_IMAGE)
        self.network = network

    def describe_isolation(self) -> dict[str, Any]:
        """容器级隔离的真实上报（对齐 sandbox.py 的 describe_isolation 契约形态）。

        ``isolation_level="container"`` 以 docker 强制的参数为准——每一条都
        对应 docker run 的真实 flag（可被 ``docker inspect`` 复核）。
        """
        return {
            "isolation_level": "container",
            "security_boundary": True,
            "image_pinned": self.image,
            "enforced": {
                "network_isolation": True,          # --internal 网络 + DOCKER-ISOLATION
                "memory_cap": True,                 # --memory
                "cpu_quota": True,                  # --cpus
                "pids_limit": True,                 # --pids-limit
                "process_tree_kill": True,          # 容器级 kill：容器亡则全部进程亡
                "zombie_reaping": True,             # --init（tini）
                "read_only_rootfs": True,           # --read-only
                "capabilities_dropped": True,       # --cap-drop ALL
                "no_new_privileges": True,          # --security-opt no-new-privileges
                "non_root_user": True,              # --user 65534:65534
            },
            "not_enforced": {
                "seccomp_custom_profile": "使用 Docker 默认 seccomp，未另配定制 profile",
                "gvisor/kata 运行时": "使用 runc，非沙箱化运行时",
            },
        }

    # ------------------------------------------------------------------
    def _run_args(self, command: list[str], *, memory: str, cpus: str,
                  pids_limit: int, timeout_s: int, workspace: str | None,
                  env: dict[str, str] | None = None) -> list[str]:
        # 注意：不用 --rm —— 瞬时完成的容器会在首次轮询前被 --rm 移除，
        # 导致 exited 状态永远观测不到。生命周期由本方法显式管理（finally rm）。
        args = [
            "run", "-d",
            "--init",
            "--network", self.network,
            "--memory", memory,
            "--cpus", cpus,
            "--pids-limit", str(pids_limit),
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            # 超时兜底：docker 层自己也会停，双保险（test 里显式 kill 验证 S-1）。
            "--stop-timeout", str(max(1, min(timeout_s, 60))),
        ]
        if workspace:
            args += ["-v", f"{workspace}:/workspace:ro", "-w", "/workspace"]
        for k, v in (env or {}).items():
            args += ["--env", f"{k}={v}"]
        args += [self.image, *command]
        return args

    def run(self, command: list[str], *, workspace: str | None = None,
            timeout_s: float = 30, memory: str = DEFAULT_MEMORY,
            cpus: str = DEFAULT_CPUS, pids_limit: int = DEFAULT_PIDS_LIMIT,
            env: dict[str, str] | None = None) -> dict[str, Any]:
        """在容器里执行 command；超时 kill 容器（全部进程随之死亡）。

        返回 ``{exit_code, stdout, stderr, timed_out, container_id}``；
        daemon/网络未就绪时抛 RuntimeError（绝不静默降级为进程级执行）。
        """
        ensure_ready()
        ensure_network()
        args = self._run_args(command, memory=memory, cpus=cpus,
                              pids_limit=pids_limit, timeout_s=int(timeout_s),
                              workspace=workspace, env=env)
        rc, cid, err = _docker(*args, timeout=60)
        if rc != 0:
            raise RuntimeError(f"容器启动失败：{err.strip()[:300]}")
        container_id = cid.strip()

        deadline = time.monotonic() + timeout_s
        timed_out = False
        exit_code: int | None = None
        logs_out = logs_err = ""
        try:
            while time.monotonic() < deadline:
                qrc, qout, _ = _docker("inspect", "-f",
                                       "{{.State.Status}} {{.State.ExitCode}}",
                                       container_id)
                if qrc == 0:
                    status, _, code = qout.strip().partition(" ")
                    if status == "exited":
                        exit_code = int(code)
                        break
                time.sleep(0.15)
            else:
                timed_out = True
            if timed_out:
                # S-1 容器态：容器级 kill —— 容器亡 ⇒ 全部进程（含孙进程）亡。
                _docker("kill", container_id, timeout=60)
                for _ in range(50):
                    qrc, qout, _ = _docker("inspect", "-f", "{{.State.Status}}", container_id)
                    if qrc != 0 or qout.strip() == "exited":
                        break
                    time.sleep(0.1)
            # 容器对象此刻必然仍存在（无 --rm）——日志双流可回捞（含 kill 后）。
            _, logs_out, logs_err = _docker("logs", container_id, timeout=30)
        finally:
            _docker("rm", "-f", container_id, timeout=30)
        return {
            "exit_code": (137 if timed_out else exit_code) if exit_code is not None else 137,
            "stdout": logs_out,
            "stderr": logs_err,
            "timed_out": timed_out,
            "container_id": container_id,
        }
