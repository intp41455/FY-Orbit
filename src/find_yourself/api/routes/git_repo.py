"""P1-03 git 工作区路由：init / stage / commit / remote / push / 提交历史.

路径前缀 ``/api/git-repo``；全部操作仅作用于产品工作区根目录
（``FY_WORKSPACE_ROOT``，默认 ``.runtime/workspaces``）之内。
"""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from ...services.actor import Actor
from ...services.git_repo_service import GitRepoService
from ..deps import csrf_protected, get_actor

router = APIRouter(prefix="/api/git-repo", tags=["git-repo"])


@lru_cache(maxsize=1)
def _service_for_root(root: str) -> GitRepoService:
    return GitRepoService(root)


def get_git_repo_service() -> GitRepoService:
    """按 FY_WORKSPACE_ROOT 构建服务实例（同一进程内复用）."""
    root = os.environ.get("FY_WORKSPACE_ROOT", ".runtime/workspaces")
    return _service_for_root(root)


class WorkspaceInitRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="工作区名称（安全字符白名单）")
    default_branch: str = Field(default="main", max_length=128)


class StageRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, description="显式文件清单（禁止 git add -A / .）")


class CommitRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    paths: list[str] = Field(..., min_length=1, description="显式文件清单（禁止 git add -A / .）")


class RemoteBindRequest(BaseModel):
    remote: str = Field(..., min_length=1, max_length=64, description="remote 名称，如 origin")
    url: str = Field(..., min_length=1, max_length=2000, description="远端地址（file/https/http/ssh/git）")


class PushRequest(BaseModel):
    remote: str = Field(default="origin", max_length=64)
    branch: str = Field(..., max_length=128)
    set_upstream: bool = Field(default=False)


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceInitRequest,
    actor: Actor = Depends(csrf_protected),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.init_workspace(body.name, body.default_branch)


@router.post("/workspaces/{workspace}/stage")
async def stage_files(
    workspace: str,
    body: StageRequest,
    actor: Actor = Depends(csrf_protected),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.stage(workspace, body.paths)


@router.post("/workspaces/{workspace}/commit")
async def commit_files(
    workspace: str,
    body: CommitRequest,
    actor: Actor = Depends(csrf_protected),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.commit(workspace, body.message, body.paths)


@router.post("/workspaces/{workspace}/remote")
async def bind_remote(
    workspace: str,
    body: RemoteBindRequest,
    actor: Actor = Depends(csrf_protected),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.add_remote(workspace, body.remote, body.url)


@router.post("/workspaces/{workspace}/push")
async def push_branch(
    workspace: str,
    body: PushRequest,
    actor: Actor = Depends(csrf_protected),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.push(workspace, body.remote, body.branch, body.set_upstream)


@router.get("/workspaces/{workspace}/status")
async def workspace_status(
    workspace: str,
    actor: Actor = Depends(get_actor),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.status(workspace)


@router.get("/workspaces/{workspace}/commits")
async def commit_history(
    workspace: str,
    limit: int = Query(default=50, ge=1, le=500),
    branch: str | None = Query(default=None, max_length=128),
    actor: Actor = Depends(get_actor),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    """提交历史（sha/parents/author/date/message），供 P1-12 提交树图消费."""
    return svc.commit_history(workspace, limit=limit, branch=branch)


@router.get("/workspaces/{workspace}/remotes")
async def list_remotes(
    workspace: str,
    actor: Actor = Depends(get_actor),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    return svc.list_remotes(workspace)


@router.get("/workspaces/{workspace}/diff")
async def workspace_diff(
    workspace: str,
    from_ref: str = Query(alias="from", max_length=128),
    to_ref: str | None = Query(default=None, alias="to", max_length=128),
    context: int = Query(default=3, ge=0, le=50),
    actor: Actor = Depends(get_actor),
    svc: GitRepoService = Depends(get_git_repo_service),
) -> dict:
    """两个 commit（或 commit vs 工作区，``to=worktree``）的 unified diff 原文."""
    return svc.diff(workspace, from_ref, to_ref, context)
