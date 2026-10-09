"""LSP 高级服务层（A-代码智能-01 自研主体 3/3）：路径安全 + 文本同步 + 语义查询。

* **路径安全**（Claw安全-01 同款纪律）：打开/查询的文件 resolve 后必须落在
  允许根目录集合内——拒绝 ``../`` 与任意绝对路径越界；
* **文本同步**：每次请求前重读文件发 didOpen（无变更状态机的轻量方案）——
  磁盘即真相，避免编辑器状态与服务端漂移；
* 语义查询：definition / references / documentSymbol / publishDiagnostics。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .bridge import LSPConnection
from .manager import LSPServerManager, language_for_path, to_file_uri


class PathOutsideAllowedRoots(Exception):
    """请求路径越出允许根目录（安全拒绝）。"""


class LSPService:
    def __init__(
        self,
        manager: LSPServerManager,
        *,
        allowed_roots: list[str],
        read_file: Callable[[str], str] | None = None,
    ):
        self.manager = manager
        self.allowed_roots = [str(Path(r).resolve()) for r in allowed_roots]
        self._read_file = read_file or (lambda p: Path(p).read_text(encoding="utf-8"))

    # -- 安全 ---------------------------------------------------------------

    def _resolve_checked(self, path: str) -> Path:
        resolved = Path(path).resolve()
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return resolved
            except ValueError:
                continue
        raise PathOutsideAllowedRoots(
            f"path {path!r} escapes all allowed roots {self.allowed_roots}")

    # -- 内部 ---------------------------------------------------------------

    def _conn_for(self, path: Path, *, root_path: str | None = None) -> tuple[LSPConnection, str]:
        language = language_for_path(str(path))
        if language is None:
            raise LookupError(f"unsupported file type: {path.suffix!r}")
        root = root_path or self.allowed_roots[0]
        conn = self.manager.get_connection(language, root_path=root)
        text = self._read_file(str(path))
        conn.did_open(path=to_file_uri(str(path)), language_id=language, text=text)
        return conn, language

    @staticmethod
    def _position(line: int, character: int) -> dict[str, int]:
        # LSP 位置是 0-based（line 与 character 都是）——调用方契约如实标注
        return {"line": int(line), "character": int(character)}

    @staticmethod
    def _loc(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        uri = (raw.get("uri") or "")[len("file://"):]
        start = raw.get("range", {}).get("start", {})
        return {"path": uri, "line": start.get("line"), "character": start.get("character")}

    # -- 语义查询 API --------------------------------------------------------

    def definition(self, *, path: str, line: int, character: int,
                   root_path: str | None = None) -> dict[str, Any]:
        p = self._resolve_checked(path)
        conn, _ = self._conn_for(p, root_path=root_path)
        raw = conn.request("textDocument/definition", {
            "textDocument": {"uri": to_file_uri(str(p))},
            "position": self._position(line, character),
        })
        if isinstance(raw, list):
            locations = [self._loc(x) for x in raw]
            return {"locations": [l for l in locations if l], "count": len(raw)}
        single = self._loc(raw)
        return {"locations": [single] if single else [], "count": 1 if single else 0}

    def references(self, *, path: str, line: int, character: int,
                   include_declaration: bool = False,
                   root_path: str | None = None) -> dict[str, Any]:
        p = self._resolve_checked(path)
        conn, _ = self._conn_for(p, root_path=root_path)
        raw = conn.request("textDocument/references", {
            "textDocument": {"uri": to_file_uri(str(p))},
            "position": self._position(line, character),
            "context": {"includeDeclaration": include_declaration},
        })
        locations = [self._loc(x) for x in (raw or []) if self._loc(x)]
        return {"locations": locations, "count": len(locations)}

    def symbols(self, *, path: str, root_path: str | None = None) -> dict[str, Any]:
        p = self._resolve_checked(path)
        conn, _ = self._conn_for(p, root_path=root_path)
        raw = conn.request("textDocument/documentSymbol", {
            "textDocument": {"uri": to_file_uri(str(p))},
        })
        items = raw if isinstance(raw, list) else []
        return {"symbols": [
            {"name": s.get("name"), "kind": s.get("kind"),
             "line": (s.get("range", {}).get("start", {}) or {}).get("line")}
            for s in items if isinstance(s, dict)
        ], "count": len(items)}

    def diagnostics(self, *, path: str, root_path: str | None = None) -> dict[str, Any]:
        p = self._resolve_checked(path)
        conn, _ = self._conn_for(p, root_path=root_path)
        conn.request("textDocument/diagnostic", {"textDocument": {"uri": to_file_uri(str(p))}})
        reports = conn.drain_diagnostics()
        items: list[dict[str, Any]] = []
        for report in reports:
            for d in report.get("params", {}).get("diagnostics", []):
                items.append({
                    "severity": d.get("severity"),
                    "message": d.get("message"),
                    "line": (d.get("range", {}).get("start", {}) or {}).get("line"),
                    "source": d.get("source"),
                })
        return {"diagnostics": items, "count": len(items)}
