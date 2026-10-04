"""API routes for 18 工程代码工作台与主协调 Agent.

Covers workspace registration/authorization, file list/read/write with conflict,
change subscription, terminal create/connect/resize/stop, Git status/diff/
selected commit, preview start/stop, verification & acceptance, and orchestrator
lease takeover.

Every mutating route is CSRF + same-origin protected, and identity is resolved
server-side — no route accepts a client-supplied owner/domain.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field

from ..deps import csrf_protected, get_actor, get_services, Services
from ...services.actor import Actor

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


class RegisterWorkspaceRequest(BaseModel):
    project_name: str = Field(min_length=1, max_length=120)
    authorized_root: str = Field(min_length=1, max_length=1000)
    mode: str = Field(default="local", pattern="^(local|cloud)$")
    data_domain: str = Field(default="work", pattern="^(personal|work|shared)$")
    branch: str = Field(default="", max_length=200)
    task_refs: list[str] = Field(default_factory=list)


class WriteFileRequest(BaseModel):
    content: str
    expected_revision: int | None = None
    source_task_id: str | None = None
    encoding: str = Field(default="utf-8", max_length=32)


class RenameFileRequest(BaseModel):
    new_path: str = Field(min_length=1, max_length=1000)
    expected_revision: int | None = None


class DeleteFileRequest(BaseModel):
    expected_revision: int | None = None


class CreateTerminalRequest(BaseModel):
    shell: str | None = None
    cols: int = Field(default=120, ge=20, le=400)
    rows: int = Field(default=30, ge=5, le=200)
    timeout_seconds: float = Field(default=300.0, gt=0, le=7200)
    rel_cwd: str = Field(default="", max_length=1000)


class TerminalWriteRequest(BaseModel):
    data: str = Field(min_length=1, max_length=8000)


class ResizeRequest(BaseModel):
    cols: int = Field(ge=20, le=400)
    rows: int = Field(ge=5, le=200)


class StopRequest(BaseModel):
    reason: str = Field(default="user_stop", max_length=200)


class GitPathsRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=200)


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    paths: list[str] = Field(min_length=1, max_length=200)


class PreviewStartRequest(BaseModel):
    command: list[str] = Field(min_length=1, max_length=20)
    target_port: int | None = Field(default=None, ge=1, le=65535)
    kind: str = Field(default="http", pattern="^(http|api)$")
    entry_path: str = Field(default="/", max_length=500)
    rel_cwd: str = Field(default="", max_length=1000)
    lease_seconds: float = Field(default=900.0, gt=0, le=86400)


class StartPreviewResponse(BaseModel):
    ok: bool = True


class LeaseAcquireRequest(BaseModel):
    root_task_id: str = Field(min_length=1, max_length=64)
    orchestrator_id: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(default_factory=list)
    connector_stage: str | None = None


class TakeoverRequest(BaseModel):
    root_task_id: str = Field(min_length=1, max_length=64)
    new_orchestrator_id: str = Field(min_length=1, max_length=64)
    new_capabilities: list[str] = Field(default_factory=list)
    new_connector_stage: str | None = None


class DispatchCheckRequest(BaseModel):
    root_task_id: str = Field(min_length=1, max_length=64)
    orchestrator_id: str = Field(min_length=1, max_length=64)
    fencing_token: int | None = None


# ----------------------------------------------------------------------
# Workspaces
# ----------------------------------------------------------------------
@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def register_workspace(
    body: RegisterWorkspaceRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    ws = svc.workspaces.register_workspace(
        actor,
        project_name=body.project_name,
        authorized_root=body.authorized_root,
        mode=body.mode,
        data_domain=body.data_domain,
        branch=body.branch,
        task_refs=body.task_refs,
    )
    svc.session.commit()
    return {
        "id": ws.id, "project_name": ws.project_name, "mode": ws.mode,
        "data_domain": ws.data_domain, "authorized_root": ws.authorized_root,
        "branch": ws.branch, "task_refs": ws.task_refs, "state": ws.state,
        "resource_limits": ws.resource_limits, "created_at": ws.created_at.isoformat(),
    }


@router.get("/workspaces")
async def list_workspaces(
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.workspaces.list_workspaces(actor)
    return {
        "items": [
            {"id": w.id, "project_name": w.project_name, "mode": w.mode,
             "authorized_root": w.authorized_root, "branch": w.branch,
             "data_domain": w.data_domain, "state": w.state}
            for w in items
        ],
        "count": len(items),
    }


@router.get("/workspaces/{workspace_id}/tree")
async def get_tree(
    workspace_id: str,
    path: str = Query(default=""),
    depth: int = Query(default=1, ge=1, le=5),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.list_tree(
        actor, workspace_id, path, depth=depth, offset=offset, limit=limit
    )
    # Commit: list_tree reconciles on-disk changes into file_revisions/events.
    # Without an explicit commit Session.close() rolls those rows back, so every
    # read re-detected the same file and re-issued the INSERT (write storm).
    svc.session.commit()
    return res


@router.get("/workspaces/{workspace_id}/snapshot")
async def get_snapshot(
    workspace_id: str,
    path: str = Query(default=""),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.get_snapshot(actor, workspace_id, path)
    svc.session.commit()
    return res


@router.get("/workspaces/{workspace_id}/events")
async def get_events(
    workspace_id: str,
    cursor: int = Query(default=0, ge=0),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.workspaces.get_events(actor, workspace_id, cursor)
    return {"items": items, "count": len(items)}


@router.get("/workspaces/{workspace_id}/file")
async def read_file(
    workspace_id: str,
    path: str = Query(min_length=1),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.read_file(actor, workspace_id, path)
    svc.session.commit()
    return res


@router.post("/workspaces/{workspace_id}/file")
async def write_file(
    workspace_id: str,
    body: WriteFileRequest,
    path: str = Query(min_length=1),
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.write_file(
        actor, workspace_id, path, body.content,
        expected_revision=body.expected_revision,
        source_task_id=body.source_task_id,
        encoding=body.encoding,
    )
    svc.session.commit()
    return res


@router.post("/workspaces/{workspace_id}/rename")
async def rename_file(
    workspace_id: str,
    body: RenameFileRequest,
    path: str = Query(min_length=1),
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.rename_file(
        actor, workspace_id, path, body.new_path, expected_revision=body.expected_revision
    )
    svc.session.commit()
    return res


@router.post("/workspaces/{workspace_id}/delete")
async def delete_file(
    workspace_id: str,
    body: DeleteFileRequest,
    path: str = Query(min_length=1),
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.delete_file(
        actor, workspace_id, path, expected_revision=body.expected_revision
    )
    svc.session.commit()
    return res


@router.get("/workspaces/{workspace_id}/search")
async def search(
    workspace_id: str,
    q: str = Query(min_length=1),
    mode: str = Query(default="name", pattern="^(name|content)$"),
    limit: int = Query(default=50, ge=1, le=200),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.workspaces.search(actor, workspace_id, q, mode=mode, limit=limit)
    svc.session.commit()
    return res


# ----------------------------------------------------------------------
# Terminal
# ----------------------------------------------------------------------
@router.post("/workspaces/{workspace_id}/terminals", status_code=status.HTTP_201_CREATED)
async def create_terminal(
    workspace_id: str,
    body: CreateTerminalRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    sess = svc.terminals.create_session(
        actor, workspace_id, shell=body.shell, cols=body.cols, rows=body.rows,
        timeout_seconds=body.timeout_seconds, rel_cwd=body.rel_cwd,
    )
    svc.session.commit()
    return sess


@router.get("/workspaces/{workspace_id}/terminals")
async def list_terminals(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.terminals.list_sessions(actor, workspace_id)
    return {"items": items, "count": len(items)}


@router.get("/terminals/{session_id}")
async def get_terminal(
    session_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.terminals.describe(actor, session_id)


@router.post("/terminals/{session_id}/write")
async def terminal_write(
    session_id: str,
    body: TerminalWriteRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.terminals.write(actor, session_id, body.data)


@router.get("/terminals/{session_id}/read")
async def terminal_read(
    session_id: str,
    wait_seconds: float = Query(default=0.5, ge=0.0, le=5.0),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.terminals.read(actor, session_id, wait_seconds=wait_seconds)
    svc.session.commit()
    return res


@router.post("/terminals/{session_id}/resize")
async def terminal_resize(
    session_id: str,
    body: ResizeRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.terminals.resize(actor, session_id, body.cols, body.rows)
    svc.session.commit()
    return res


@router.post("/terminals/{session_id}/stop")
async def terminal_stop(
    session_id: str,
    body: StopRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.terminals.stop(actor, session_id, reason=body.reason)
    svc.session.commit()
    return res


@router.post("/terminals/{session_id}/enforce-timeout")
async def terminal_enforce_timeout(
    session_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.terminals.enforce_timeout(actor, session_id)
    svc.session.commit()
    return res


@router.post("/workspaces/{workspace_id}/terminals/reap")
async def reap_terminals(
    workspace_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.terminals.reap_orphans(actor, workspace_id)
    svc.session.commit()
    return res


# ----------------------------------------------------------------------
# Git
# ----------------------------------------------------------------------
@router.get("/workspaces/{workspace_id}/git/status")
async def git_status(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.git.status(actor, workspace_id)
    # Commit: status() refreshes workspace_manifests.branch when it changed.
    # Rolled-back branch updates were re-attempted (and contended) on every call.
    svc.session.commit()
    return res


@router.get("/workspaces/{workspace_id}/git/branches")
async def git_branches(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.git.branches(actor, workspace_id)


@router.post("/workspaces/{workspace_id}/git/diff")
async def git_diff(
    workspace_id: str,
    body: GitPathsRequest,
    staged: bool = Query(default=False),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.git.diff(actor, workspace_id, body.paths, staged=staged)


@router.post("/workspaces/{workspace_id}/git/stage")
async def git_stage(
    workspace_id: str,
    body: GitPathsRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.git.stage(actor, workspace_id, body.paths)
    svc.session.commit()
    return res


@router.post("/workspaces/{workspace_id}/git/commit")
async def git_commit(
    workspace_id: str,
    body: GitCommitRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.git.commit(actor, workspace_id, body.message, body.paths)
    svc.session.commit()
    return res


@router.post("/workspaces/{workspace_id}/git/revert-preview")
async def git_revert_preview(
    workspace_id: str,
    body: GitPathsRequest,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.git.revert_preview(actor, workspace_id, body.paths)


@router.post("/workspaces/{workspace_id}/git/revert")
async def git_revert(
    workspace_id: str,
    body: GitPathsRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.git.revert_apply(actor, workspace_id, body.paths)
    svc.session.commit()
    return res


# ----------------------------------------------------------------------
# Preview
# ----------------------------------------------------------------------
@router.post("/workspaces/{workspace_id}/preview", status_code=status.HTTP_201_CREATED)
async def start_preview(
    workspace_id: str,
    body: PreviewStartRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.previews.start(
        actor, workspace_id, command=body.command, target_port=body.target_port,
        kind=body.kind, entry_path=body.entry_path, rel_cwd=body.rel_cwd,
        lease_seconds=body.lease_seconds,
    )
    svc.session.commit()
    return res


@router.get("/workspaces/{workspace_id}/preview")
async def list_previews(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.previews.list_sessions(actor, workspace_id)
    return {"items": items, "count": len(items)}


@router.get("/preview/{session_id}")
async def preview_status(
    session_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.previews.status(actor, session_id)
    svc.session.commit()
    return res


@router.post("/preview/{session_id}/stop")
async def stop_preview(
    session_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.previews.stop(actor, session_id)
    svc.session.commit()
    return res


# ----------------------------------------------------------------------
# Preview sources (P1-A 统一预览源注册协议)
# ----------------------------------------------------------------------
class StaticPreviewSourceRequest(BaseModel):
    kind: str = Field(pattern="^static$")
    path: str = Field(min_length=1, max_length=1000)


class ProcessPreviewSourceRequest(BaseModel):
    kind: str = Field(pattern="^process$")
    command: list[str] = Field(min_length=1, max_length=20)
    target_port: int | None = Field(default=None, ge=1, le=65535)
    process_kind: str = Field(default="http", pattern="^(http|api)$")
    entry_path: str = Field(default="/", max_length=500)
    rel_cwd: str = Field(default="", max_length=1000)
    lease_seconds: float = Field(default=900.0, gt=0, le=86400)


@router.post(
    "/workspaces/{workspace_id}/preview-sources",
    status_code=status.HTTP_201_CREATED,
)
async def register_preview_source(
    workspace_id: str,
    body: StaticPreviewSourceRequest | ProcessPreviewSourceRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    if isinstance(body, StaticPreviewSourceRequest):
        res = svc.preview_sources.register_static(actor, workspace_id, rel_path=body.path)
    else:
        res = svc.preview_sources.register_process(
            actor,
            workspace_id,
            command=body.command,
            target_port=body.target_port,
            process_kind=body.process_kind,
            entry_path=body.entry_path,
            rel_cwd=body.rel_cwd,
            lease_seconds=body.lease_seconds,
        )
    svc.session.commit()
    return res


@router.get("/workspaces/{workspace_id}/preview-sources")
async def list_preview_sources(
    workspace_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    items = svc.preview_sources.list_sources(actor, workspace_id)
    return {"items": items, "count": len(items)}


@router.get("/preview-sources/{source_id}/version")
async def preview_source_version(
    source_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.preview_sources.version(actor, source_id)
    svc.session.commit()
    return res


@router.get("/preview-sources/{source_id}/content")
async def preview_source_content(
    source_id: str,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> Response:
    """Read-only static preview content.

    Untrusted workspace HTML is served inside a CSP sandbox (scripts allowed,
    unique opaque origin) with MIME sniffing disabled. The response never
    contains the absolute filesystem path.
    """
    res = svc.preview_sources.read_content(actor, source_id)
    svc.session.commit()
    return Response(
        content=res["data"],
        media_type=res["media_type"],
        headers=res["headers"],
    )


@router.delete("/preview-sources/{source_id}", status_code=status.HTTP_200_OK)
async def unregister_preview_source(
    source_id: str,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.preview_sources.unregister(actor, source_id)
    svc.session.commit()
    return res


# ----------------------------------------------------------------------
# Orchestrator lease
# ----------------------------------------------------------------------
@router.post("/orchestrator/lease", status_code=status.HTTP_201_CREATED)
async def acquire_lease(
    body: LeaseAcquireRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.orchestrator_leases.acquire(
        actor, body.root_task_id, body.orchestrator_id,
        capabilities=body.capabilities, connector_stage=body.connector_stage,
    )
    svc.session.commit()
    return res


@router.get("/orchestrator/lease")
async def current_lease(
    root_task_id: str = Query(min_length=1),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.orchestrator_leases.current(actor, root_task_id)


@router.post("/orchestrator/dispatch-check")
async def dispatch_check(
    body: DispatchCheckRequest,
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.orchestrator_leases.validate_dispatch(
        body.root_task_id, body.orchestrator_id, body.fencing_token
    )


@router.post("/orchestrator/takeover")
async def begin_takeover(
    body: TakeoverRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.orchestrator_leases.begin_takeover(
        actor, body.root_task_id, body.new_orchestrator_id,
        new_capabilities=body.new_capabilities, new_connector_stage=body.new_connector_stage,
    )
    svc.session.commit()
    return res


@router.post("/orchestrator/takeover/resume")
async def resume_takeover(
    body: LeaseAcquireRequest,
    actor: Actor = Depends(csrf_protected),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    res = svc.orchestrator_leases.resume_takeover(actor, body.root_task_id)
    svc.session.commit()
    return res


@router.get("/orchestrator/takeover/summary")
async def takeover_summary(
    root_task_id: str = Query(min_length=1),
    actor: Actor = Depends(get_actor),
    svc: Services = Depends(get_services),
) -> dict[str, Any]:
    return svc.orchestrator_leases.takeover_summary(actor, root_task_id)
