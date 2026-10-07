"""LSP 语言服务器子进程生命周期管理（A-代码智能-01 自研主体 2/2）。

* 按语言定义候选服务端命令（argv）；``shutil.which`` 逐个探测——**没装就
  如实报未安装**，绝不假装启动成功；
* 每语言进程级单例：首次使用拉起，进程内复用；``stop_all`` 统一回收；
* 传输接 :class:`~find_yourself.services.lsp.bridge.LSPConnection`（测试可
  注入 fake，跳过真实子进程）。
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from .bridge import LSPConnection


@dataclass(frozen=True)
class ServerSpec:
    language_id: str
    argv: tuple[str, ...]          # 候选命令（按序探测，取 which 命中者）
    extensions: tuple[str, ...]    # 该语言负责的文件扩展名


SERVER_SPECS: dict[str, ServerSpec] = {
    "python": ServerSpec("python", ("pylsp",), (".py",)),
    "typescript": ServerSpec("typescript", ("typescript-language-server", "--stdio"),
                             (".ts", ".tsx")),
    "javascript": ServerSpec("javascript", ("typescript-language-server", "--stdio"),
                             (".js", ".jsx", ".mjs")),
}


def language_for_path(path: str) -> str | None:
    low = path.lower()
    for spec in SERVER_SPECS.values():
        if any(low.endswith(ext) for ext in spec.extensions):
            return spec.language_id
    return None


def to_file_uri(path: str) -> str:
    """本地路径 → file:// URI（Windows 盘符做正确转义）。"""
    normalized = path.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return "file://" + quote(normalized, safe="/:._-")


class LSPServerManager:
    """语言服务端探测 / 拉起 / 复用 / 回收。"""

    def __init__(self, *, specs: dict[str, ServerSpec] | None = None):
        self._specs = specs or SERVER_SPECS
        self._connections: dict[str, LSPConnection] = {}
        self._procs: dict[str, subprocess.Popen] = {}
        self._initialized: set[str] = set()
        self._lock = threading.Lock()

    # -- 探测与状态 ----------------------------------------------------------

    def status(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for lang, spec in self._specs.items():
            command = next((argv[0] for argv in (spec.argv,) if shutil.which(argv[0])), None)
            out[lang] = {
                "installed": command is not None,
                "command": command,
                "running": lang in self._connections,
                "extensions": list(spec.extensions),
            }
        return out

    def is_installed(self, language: str) -> bool:
        spec = self._specs.get(language)
        return bool(spec and shutil.which(spec.argv[0]))

    # -- 生命周期 ------------------------------------------------------------

    def get_connection(
        self, language: str, *, root_path: str,
        connection_factory: Callable[[list[str]], LSPConnection] | None = None,
    ) -> LSPConnection:
        """取（或拉起）某语言的服务端连接；未安装 → LookupError（调用方转 503）。

        ``connection_factory`` 供测试注入 fake（绕过真实子进程）。
        """
        spec = self._specs.get(language)
        if spec is None:
            raise LookupError(f"unsupported language: {language!r}")
        with self._lock:
            conn = self._connections.get(language)
            if conn is not None:
                return conn
            if connection_factory is None:
                if not shutil.which(spec.argv[0]):
                    raise LookupError(f"LSP server for {language!r} not installed "
                                      f"(looked for {spec.argv[0]!r})")
                proc = subprocess.Popen(
                    list(spec.argv),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )

                def _read(n: int = 65536, _proc: subprocess.Popen = proc) -> bytes:
                    stdout = _proc.stdout
                    if hasattr(stdout, "read1"):
                        return stdout.read1(n)     # Windows 管道非阻塞友好
                    return stdout.read(n)

                conn = LSPConnection(
                    write_fn=lambda data, _proc: (
                        _proc.stdin.write(data), _proc.stdin.flush()),
                    read_fn=_read,
                )
                self._procs[language] = proc
            else:
                conn = connection_factory(list(spec.argv))
            result = conn.initialize(root_uri=to_file_uri(root_path))
            if not result:
                # initialize 必须返回 capabilities——空结果视作握手失败
                self._discard(language)
                raise LookupError(f"LSP server for {language!r} failed handshake")
            self._connections[language] = conn
            self._initialized.add(language)
            return conn

    def _discard(self, language: str) -> None:
        self._connections.pop(language, None)
        proc = self._procs.pop(language, None)
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass

    def stop_all(self) -> None:
        with self._lock:
            for lang in list(self._connections):
                self._discard(lang)
