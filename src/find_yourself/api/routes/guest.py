"""W8 · 游客会话与账号分层路由（游客 / 注册 / 会员位）。

产品规则（任务书 §2.2）：**工作台免登录全功能可用**，个人空间里的
「云同步 / 社区 / 导出分享」才需要账号。所以这里的三个端点是分层的支点：

* ``POST /auth/guest`` —— 无会话时开一个本地游客账号（无感，不弹窗）。
  游客是**真实的 users 行**，因此和任何租户一样拥有数据；这也是
  「游客玩出数据 → 升级 → 数据还在」能靠同一个 user id 就地完成的原因。
* ``POST /auth/guest/upgrade`` —— 游客会话就地升级为正式账号（同 id UPDATE）。
* ``GET  /api/auth/account`` —— 设置页账号卡数据（``is_guest`` / ``plan``）。

安全边界（诚实原则）：游客登录**只在 local/test 环境开放**。
``POST /auth/guest`` 会为**每一个调用方**发一个新账号，如果允许远程调用，
就是一个无成本的无限账号铸造机。所以这里显式拒绝非 loopback 与 production，
失败时走 DomainError envelope（总纲第 3 条第 3 项），不静默降级。

会员位诚实标注：v1 **不接支付**。``plan`` 只由服务端数据决定，本模块
不提供任何把它改成 ``pro`` 的端点，``/api/auth/account`` 也只如实回报当前值。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ...config import Settings
from ...services.actor import Actor
from ...services.errors import PermissionDenied
from ..deps import SESSION_COOKIE, Services, csrf_protected, get_actor, get_services, get_settings

router = APIRouter(tags=["auth"])

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
SESSION_MAX_AGE = 8 * 3600


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _cookie_secure(settings: Settings, request: Request) -> bool:
    return settings.environment == "production" or request.url.scheme == "https"


def _require_local_guest(request: Request, settings: Settings) -> None:
    """Refuse guest minting outside local/test loopback.

    Two independent guards, both deliberate: ``environment`` because a shared or
    production deployment must never hand out accounts anonymously, and the
    loopback check because a browser page on another host must not be able to
    drive this endpoint through the user's own machine.
    """
    if settings.environment not in ("local", "test"):
        raise PermissionDenied(
            "guest_disabled",
            "Guest sessions are only available in the local desktop app",
            403,
        )
    remote = _client_ip(request)
    if remote not in LOOPBACK:
        raise PermissionDenied(
            "guest_loopback_only",
            "Guest sessions may only be created from 127.0.0.1",
            403,
        )


def _issue_session_cookie(
    resp: Response, row, *, request: Request, settings: Settings
) -> None:
    resp.set_cookie(
        SESSION_COOKIE,
        getattr(row, "plaintext_token", row.id),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=_cookie_secure(settings, request),
        samesite="lax",
        path="/",
    )


@router.post("/auth/guest")
async def create_guest(request: Request,
                       svc: Services = Depends(get_services),
                       settings: Settings = Depends(get_settings)) -> Response:
    """Create a local guest account and return a session (no password needed)."""
    _require_local_guest(request, settings)
    user, row = svc.auth.create_guest_user(ip=_client_ip(request))
    svc.session.commit()
    resp = JSONResponse({
        # `subject_type` mirrors GET /auth/me so the frontend has ONE rule for
        # "is this an owner session?" (it dispatches on this field, not on which
        # endpoint answered). Omitting it made the guest bootstrap a silent
        # no-op in the browser — see the W8 report for the caught defect.
        "subject_type": "owner",
        "status": "guest",
        "owner_id": user.id,
        "is_guest": True,
        "plan": user.plan,
        "email": user.email,
        "display_name": user.display_name,
        "csrf_token": row.csrf_secret,
    })
    _issue_session_cookie(resp, row, request=request, settings=settings)
    return resp


@router.post("/auth/guest/upgrade")
async def upgrade_guest(body: dict, request: Request,
                        actor: Actor = Depends(csrf_protected),
                        svc: Services = Depends(get_services),
                        settings: Settings = Depends(get_settings)) -> Response:
    """Upgrade the current guest session to a registered account, keeping all data.

    CSRF-protected: this writes credentials, so a cross-site POST must not be
    able to bind somebody's guest data to an attacker-controlled mailbox.
    """
    user, row = svc.auth.upgrade_guest(
        actor,
        email=str(body.get("email") or ""),
        password=str(body.get("password") or ""),
        consent_accepted=bool(body.get("consent_accepted")),
        display_name=str(body.get("display_name") or ""),
        ip=_client_ip(request),
    )
    svc.session.commit()
    resp = JSONResponse({
        "subject_type": "owner",
        "status": "upgraded",
        "owner_id": user.id,
        "is_guest": False,
        "plan": user.plan,
        "email": user.email,
        "display_name": user.display_name,
        "csrf_token": row.csrf_secret,
    })
    _issue_session_cookie(resp, row, request=request, settings=settings)
    return resp


@router.get("/api/auth/account")
async def account(actor: Actor = Depends(get_actor),
                  svc: Services = Depends(get_services)) -> dict:
    """Account tier summary for the settings account card.

    ``GET /auth/me`` answers *who is calling* (frozen contract §5.1) and is left
    untouched; this is the separate read model the settings page needs.
    """
    actor.require_owner()
    info = svc.auth.describe_account(actor.owner_id)
    # Honesty flag, not a mock: v1 ships no payment channel at all.
    return {**info, "payment_enabled": False}
