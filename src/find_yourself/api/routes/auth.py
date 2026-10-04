"""Authentication routes (FROZEN_CONTRACT §5.1).

* ``GET /auth/login`` — starts the OIDC Authorization Code flow with PKCE; a
  short-lived signed cookie carries state/nonce/code_verifier.
* ``GET /auth/callback`` — exact callback; verifies state, exchanges the code
  with PKCE, verifies the ID token (iss/aud/sub/nonce/signature), then issues
  an HttpOnly owner session cookie.
* ``POST /auth/logout`` — revokes the server-side session and clears cookies.
* ``GET /auth/me`` — returns the current server-resolved identity.
* ``POST /auth/local/dev-token`` — local/test loopback-only owner bootstrap;
  refused in production. Never used over the public internet.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadData, URLSafeTimedSerializer

from ..deps import (
    SESSION_COOKIE, csrf_protected, get_actor, get_services, get_settings, Services,
)
from ..schemas import AuthMe, LocalDevTokenRequest
from ...services.actor import Actor
from ...services.errors import Unauthenticated, PermissionDenied
from ...config import Settings
from ..oidc import OIDCClient, OIDCSession

router = APIRouter(tags=["auth"])

OAUTH_COOKIE = "fy_oauth"
STATE_MAX_AGE = 600  # seconds


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="fy-oidc-state")


def _cookie_secure(settings: Settings, request: Request) -> bool:
    # P2-18: secure on HTTPS — production deployments always, plus any request
    # that actually arrived over https. Loopback http in local/test must work.
    return settings.environment == "production" or request.url.scheme == "https"


@router.get("/auth/login")
async def login(request: Request, settings: Settings = Depends(get_settings)) -> RedirectResponse:
    oidc: OIDCClient | None = request.app.state.oidc
    if oidc is None:
        return JSONResponse(
            {"error": {"code": "oidc_not_configured",
                       "message": "OIDC is not configured; use local dev token on loopback",
                       "details": {}}},
            status_code=503,
        )
    session = OIDCSession()
    import secrets as _s
    session.code_verifier = _s.token_urlsafe(48)
    redirect_uri = f"{settings.public_url.rstrip('/')}/auth/callback"
    session.redirect_uri = redirect_uri
    url = oidc.build_auth_url(redirect_uri=redirect_uri, session=session)

    token = _serializer(settings).dumps({
        "state": session.state, "nonce": session.nonce,
        "code_verifier": session.code_verifier, "redirect_uri": redirect_uri,
    })
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie(OAUTH_COOKIE, token, max_age=STATE_MAX_AGE, httponly=True,
                    secure=_cookie_secure(settings, request), samesite="lax", path="/")
    return resp


@router.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = "",
                   settings: Settings = Depends(get_settings),
                   svc: Services = Depends(get_services)) -> Response:
    oidc: OIDCClient | None = request.app.state.oidc
    if oidc is None:
        return JSONResponse({"error": {"code": "oidc_not_configured",
                                       "message": "OIDC is not configured", "details": {}}},
                            status_code=503)
    raw = request.cookies.get(OAUTH_COOKIE)
    if not raw:
        raise Unauthenticated("oauth_state", "Missing OAuth state cookie")
    try:
        data = _serializer(settings).loads(raw, max_age=STATE_MAX_AGE)
    except BadData as exc:
        raise Unauthenticated("oauth_state", "Invalid or expired OAuth state") from exc

    if not state or not secrets_compare(state, data["state"]):
        raise PermissionDenied("oauth_state", "State mismatch", 400)

    token = oidc.exchange_code(code=code, code_verifier=data["code_verifier"],
                               redirect_uri=data["redirect_uri"])
    oidc.verify_id_token(token.get("id_token", ""), expected_nonce=data["nonce"])
    # Only now do we trust the fixed owner sub; the session is bound server-side.
    row = svc.auth.create_owner_session(oidc.owner_sub)
    svc.session.commit()
    resp = RedirectResponse(settings.public_url.rstrip("/") + "/", status_code=302)
    resp.set_cookie(SESSION_COOKIE, getattr(row, "plaintext_token", row.id), max_age=8 * 3600, httponly=True,
                    secure=_cookie_secure(settings, request), samesite="lax", path="/")
    resp.delete_cookie(OAUTH_COOKIE, path="/")
    return resp


@router.post("/auth/logout")
async def logout(request: Request, actor: Actor = Depends(csrf_protected),
                 svc: Services = Depends(get_services),
                 settings: Settings = Depends(get_settings)) -> dict:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        svc.auth.revoke_session(actor, session_id)
    svc.session.commit()
    resp = JSONResponse({"status": "logged_out"})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/auth/me")
async def me(actor: Actor = Depends(get_actor), svc: Services = Depends(get_services)) -> AuthMe:
    # W8: the account tier is resolved server-side from the users row — never
    # from a query parameter or request body. Service identities and the legacy
    # bootstrap owner get is_guest=False / plan="unknown" (see describe_account).
    info = svc.auth.describe_account(actor.owner_id)
    return AuthMe(
        subject_type=actor.subject_type, owner_id=actor.owner_id,
        service_id=actor.service_id, service_kind=actor.service_kind,
        csrf_token=actor.csrf_token,
        is_guest=bool(info.get("is_guest", False)),
        plan=str(info.get("plan", "unknown")),
    )


@router.post("/auth/local/dev-token")
async def local_dev_token(body: LocalDevTokenRequest, request: Request,
                          svc: Services = Depends(get_services),
                          settings: Settings = Depends(get_settings)) -> Response:
    remote = request.client.host if request.client else ""
    # Raises PermissionDenied in production / non-loopback; Unauthenticated on bad token.
    actor = svc.auth.local_dev_actor(body.token, remote)
    row = svc.auth.create_owner_session(actor.owner_id)
    svc.session.commit()
    resp = JSONResponse({"status": "ok", "owner_id": actor.owner_id,
                         "csrf_token": row.csrf_secret})
    resp.set_cookie(SESSION_COOKIE, getattr(row, "plaintext_token", row.id), max_age=8 * 3600, httponly=True,
                    secure=_cookie_secure(settings, request), samesite="lax", path="/")
    return resp


# -- local user system (Route B multi-tenant) -------------------------------

def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


@router.post("/auth/register")
async def register(body: dict, request: Request,
                   svc: Services = Depends(get_services),
                   settings: Settings = Depends(get_settings)) -> Response:
    """Create a user account: email + password + explicit privacy consent."""
    user, row = svc.auth.register_user(
        email=str(body.get("email") or ""),
        password=str(body.get("password") or ""),
        consent_accepted=bool(body.get("consent_accepted")),
        display_name=str(body.get("display_name") or ""),
        ip=_client_ip(request),
    )
    svc.session.commit()
    resp = JSONResponse({
        "status": "registered", "owner_id": user.id, "email": user.email,
        "display_name": user.display_name, "csrf_token": row.csrf_secret,
    })
    resp.set_cookie(SESSION_COOKIE, getattr(row, "plaintext_token", row.id), max_age=8 * 3600,
                    httponly=True, secure=_cookie_secure(settings, request), samesite="lax", path="/")
    return resp


@router.post("/auth/login")
async def login_local(body: dict, request: Request,
                      svc: Services = Depends(get_services),
                      settings: Settings = Depends(get_settings)) -> Response:
    """Email+password login with per-(email, ip) failure throttling."""
    user, row = svc.auth.login_user(
        email=str(body.get("email") or ""),
        password=str(body.get("password") or ""),
        ip=_client_ip(request),
    )
    svc.session.commit()
    resp = JSONResponse({
        "status": "ok", "owner_id": user.id, "email": user.email,
        "display_name": user.display_name, "csrf_token": row.csrf_secret,
    })
    resp.set_cookie(SESSION_COOKIE, getattr(row, "plaintext_token", row.id), max_age=8 * 3600,
                    httponly=True, secure=_cookie_secure(settings, request), samesite="lax", path="/")
    return resp


@router.get("/api/account/consents")
async def list_consents(actor: Actor = Depends(get_actor),
                        svc: Services = Depends(get_services)) -> dict:
    actor.require_owner()
    rows = svc.auth.list_consents(actor.owner_id)
    return {"consents": [
        {"doc_id": c.doc_id, "doc_version": c.doc_version,
         "agreed_at": c.agreed_at.isoformat() if c.agreed_at else None}
        for c in rows
    ], "count": len(rows)}


@router.delete("/api/account")
async def delete_account(request: Request,
                         actor: Actor = Depends(csrf_protected),
                         svc: Services = Depends(get_services)) -> dict:
    """Self-service account deletion (GDPR erasure): cascades to the user's
    memories, revokes sessions, anonymizes the profile and keeps consent
    records for compliance proof."""
    result = svc.deletion.delete_account(actor, ip=_client_ip(request))
    svc.session.commit()
    resp = JSONResponse({"status": "deleted", **result})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def secrets_compare(a: str, b: str) -> bool:
    import secrets
    return secrets.compare_digest(a, b)
