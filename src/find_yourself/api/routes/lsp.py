"""A-代码智能-01 · LSP 代码智能 HTTP 面。

* ``GET  /api/lsp/servers``            —— 各语言服务端状态（installed/running 如实报）
* ``POST /api/lsp/definition``         —— 跳转定义
* ``POST /api/lsp/references``         —— 查找引用
* ``GET  /api/lsp/symbols``            —— 文档符号大纲
* ``GET  /api/lsp/diagnostics``        —— 诊断（拉取 publishDiagnostics）

* **位置契约**：``line``/``character`` 均 **0-based**（LSP 原生语义），前端接线时自行换算；
* **诚实降级**：语言服务端未安装 → 503 ``lsp_server_not_installed``；
* **路径安全**：仅允许已登记根目录内的文件（越界 → 403 ``path_outside_allowed_roots``）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from ...services.lsp import (
    LSPServerManager,
    LSPService,
    PathOutsideAllowedRoots,
)
from ..deps import Services, get_actor, get_services
from ...services.actor import Actor
from ...services.errors import DomainError

router = APIRouter(prefix="/api/lsp", tags=["code-intelligence"])

#: 允许 LSP 触达的根目录——仓库根（worktree 内即本仓），与工作台授权根对齐
_manager = LSPServerManager()
_allowed_roots = [str(__import__("pathlib").Path.cwd())]
_service = LSPService(_manager, allowed_roots=_allowed_roots)


class LSPPositionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    line: int = Field(ge=0)
    character: int = Field(ge=0)
    include_declaration: bool = False


class LSPPathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


def _translate(exc: Exception) -> DomainError:
    if isinstance(exc, PathOutsideAllowedRoots):
        return DomainError("path_outside_allowed_roots", str(exc), 403)
    if isinstance(exc, LookupError):
        return DomainError("lsp_server_not_installed", str(exc), 503)
    if isinstance(exc, TimeoutError):
        return DomainError("lsp_timeout", str(exc), 504)
    return DomainError("lsp_error", str(exc)[:300], 500)


@router.get("/servers")
def list_servers(actor: Actor = Depends(get_actor)) -> dict[str, Any]:
    """各语言服务端安装/运行状态（诚实探测，不假设）。"""
    return {"servers": _manager.status()}


@router.post("/definition")
def goto_definition(
    body: LSPPositionBody,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    try:
        return _service.definition(path=body.path, line=body.line,
                                   character=body.character)
    except Exception as exc:  # noqa: BLE001 —— 统一翻译为错误信封
        raise _translate(exc) from exc


@router.post("/references")
def find_references(
    body: LSPPositionBody,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    try:
        return _service.references(path=body.path, line=body.line,
                                   character=body.character,
                                   include_declaration=body.include_declaration)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/symbols")
def document_symbols(
    path: str = Query(min_length=1),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    try:
        return _service.symbols(path=path)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/diagnostics")
def diagnostics(
    path: str = Query(min_length=1),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    try:
        return _service.diagnostics(path=path)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
