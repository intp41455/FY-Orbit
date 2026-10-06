"""A-代码智能-01 · LSP 代码智能接入（语义级导航 / 引用 / 符号 / 诊断）。

公开面：
* :class:`LSPService` —— 路径安全 + didOpen 同步 + definition/references/
  symbols/diagnostics 语义查询；
* :class:`LSPServerManager` —— 语言服务端探测（pylsp / typescript-language-
  server，``shutil.which`` 如实探测）与子进程生命周期；
* :mod:`bridge` —— LSP JSON-RPC 协议桥（Content-Length 分帧 + 锁步请求）。

诚实边界：服务端未安装时全部端点显式 503 ``lsp_server_not_installed``——
不装假装语义智能可用。
"""

from .bridge import LSPConnection, LSPError, decode_messages, encode_message  # noqa: F401
from .manager import SERVER_SPECS, LSPServerManager, language_for_path, to_file_uri  # noqa: F401
from .service import LSPService, PathOutsideAllowedRoots  # noqa: F401

__all__ = [
    "LSPConnection", "LSPError", "encode_message", "decode_messages",
    "LSPServerManager", "SERVER_SPECS", "language_for_path", "to_file_uri",
    "LSPService", "PathOutsideAllowedRoots",
]
