"""Production OIDC integration (FROZEN_CONTRACT §5.1, §7.2).

Implements the Authorization Code flow with PKCE:

* fixed issuer, audience (client id) and the single owner ``sub``;
* signature verified against the issuer JWKS;
* ``state`` / ``nonce`` / PKCE ``code_challenge`` / ``code_verifier``;
* exact callback URL binding at the token exchange.

The HTTP transport is injectable so tests can point the client at a local,
in-process test issuer (RSA-signed, real discovery + JWKS) without opening a
real socket. No production value ever lives in source control.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError

from ..services.errors import Unauthenticated

WELL_KNOWN = "/.well-known/openid-configuration"


def new_state() -> str:
    return secrets.token_urlsafe(24)


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = secrets.token_urlsafe(48)
    import hashlib
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = __import__("base64").urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@dataclass
class OIDCSession:
    """Transient OAuth request state, held in a signed HttpOnly cookie."""

    state: str = field(default_factory=new_state)
    nonce: str = field(default_factory=new_nonce)
    code_verifier: str = ""
    redirect_uri: str = ""


class OIDCClient:
    def __init__(self, *, issuer: str, client_id: str, owner_sub: str,
                 client_secret: str = "", http: httpx.Client | None = None):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.owner_sub = owner_sub
        # trust_env=False: loopback callback must not route through a corporate
        # proxy (see AGENTS.md NO_PROXY note); in-process tests use ASGI transport.
        self.http = http or httpx.Client(trust_env=False, timeout=10.0)
        self._discovery: dict | None = None
        self._jwks: dict | None = None

    # -- discovery --------------------------------------------------------
    def discover(self) -> dict:
        if self._discovery is None:
            r = self.http.get(self.issuer + WELL_KNOWN)
            if r.status_code != 200:
                raise Unauthenticated("oidc_discovery", "Identity provider discovery failed")
            self._discovery = r.json()
        return self._discovery

    def _fetch_jwks(self) -> dict:
        if self._jwks is None:
            disc = self.discover()
            jwks_uri = disc.get("jwks_uri")
            if not jwks_uri:
                raise Unauthenticated("oidc_jwks", "Identity provider did not publish JWKS")
            r = self.http.get(jwks_uri)
            if r.status_code != 200:
                raise Unauthenticated("oidc_jwks", "Failed to fetch identity provider keys")
            self._jwks = r.json()
        return self._jwks

    # -- auth URL ---------------------------------------------------------
    def build_auth_url(self, *, redirect_uri: str, session: OIDCSession) -> str:
        disc = self.discover()
        authz = disc["authorization_endpoint"]
        from urllib.parse import urlencode
        q = urlencode({
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": session.state,
            "nonce": session.nonce,
            "code_challenge": pkce_challenge_from_verifier(session.code_verifier),
            "code_challenge_method": "S256",
            "scope": "openid",
        })
        return f"{authz}?{q}"

    # -- token exchange --------------------------------------------------
    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> dict:
        disc = self.discover()
        token_endpoint = disc["token_endpoint"]
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": self.client_id,
        }
        if self.client_secret:
            data["client_secret"] = self.client_secret
        r = self.http.post(token_endpoint, data=data)
        if r.status_code != 200:
            raise Unauthenticated("oidc_token", "Token exchange rejected by identity provider")
        return r.json()

    # -- id token verification -------------------------------------------
    def verify_id_token(self, id_token: str, *, expected_nonce: str) -> dict:
        """Verify signature, issuer, audience, expiry, nonce and owner subject.

        Any mismatch raises Unauthenticated; never returns claims for a token
        that was not issued to this single-owner deployment.
        """
        jwks = self._fetch_jwks()
        try:
            # authlib resolves the signing key from the JWKS by kid.
            claims = jwt.decode(id_token, jwks, claims_options={
                "iss": {"essential": True, "value": self.issuer},
                "aud": {"essential": True, "value": self.client_id},
            })
            claims.validate()
        except JoseError as exc:  # signature / iss / aud / exp / nbf failures
            raise Unauthenticated("oidc_token_invalid", "Identity token rejected") from exc

        if claims.get("nonce") != expected_nonce:
            raise Unauthenticated("oidc_nonce", "Identity token nonce mismatch")
        if claims.get("sub") != self.owner_sub:
            # Not the fixed owner: refuse to create a session (S02).
            raise Unauthenticated("oidc_subject", "Identity token subject is not the owner")
        return dict(claims)


def pkce_challenge_from_verifier(verifier: str) -> str:
    import base64
    import hashlib
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def public_jwks_from_private_pem(private_pem: bytes) -> dict:
    """Build a public JWKS from an RSA private key PEM (used by the test issuer)."""
    key = JsonWebKey.import_key(private_pem)
    return {"keys": [key.as_dict(is_private=False, alg="RS256", use="sig")]}
