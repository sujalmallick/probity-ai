"""Authentication.

AUTH_MODE=clerk (production): verify Clerk session JWTs against Clerk's JWKS (RS256), check issuer,
expiry and authorized party, then map the Clerk user to a Probity user. First sign-in provisions:
a pending invitation for the email joins that workspace with the invited role; otherwise the user gets a
new workspace as owner.

AUTH_MODE=local (dev/demo only, refused in prod): HS256 tokens for seeded demo users.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.config import get_settings
from probity.db.audit import audit
from probity.db.models import Invitation, User, Workspace
from probity.db.session import set_tenant


class AuthError(Exception):
    pass


@dataclass
class Principal:
    user: User
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def mfa_verified(self) -> bool:
        """Clerk `fva` = [minutes since first factor, minutes since second factor]; -1 = never."""
        if get_settings().auth_mode != "clerk":
            return True
        fva = self.claims.get("fva")
        return isinstance(fva, list) and len(fva) == 2 and fva[1] is not None and fva[1] >= 0


# ---------------------------------------------------------------- local (dev/demo)

def issue_local_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user.id, "ws": user.workspace_id, "role": user.role, "iat": now, "exp": now + timedelta(minutes=s.jwt_ttl_minutes), "iss": "probity-local"},
        s.jwt_secret,
        algorithm="HS256",
    )


def _verify_local(token: str, s: Session) -> Principal:
    try:
        claims = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], issuer="probity-local")
    except jwt.PyJWTError as e:
        raise AuthError("invalid or expired token") from e
    user = s.get(User, claims["sub"])
    if user is None or user.workspace_id != claims.get("ws") or not user.active:
        raise AuthError("unknown user")
    return Principal(user, claims)


# ---------------------------------------------------------------- clerk

@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    st = get_settings()
    url = st.clerk_jwks_url or f"{(st.clerk_issuer or '').rstrip('/')}/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_clerk_token(token: str) -> dict[str, Any]:
    st = get_settings()
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], issuer=st.clerk_issuer.rstrip("/") if st.clerk_issuer else None,
            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False}, leeway=10,
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid session token: {e}") from e
    allowed = [a.strip() for a in st.clerk_authorized_parties.split(",") if a.strip()]
    if allowed and claims.get("azp") not in allowed:
        raise AuthError("token issued for an unauthorized origin")
    return claims


def _clerk_profile(user_id: str, claims: dict[str, Any]) -> tuple[str, str]:
    """Email + display name. Prefer claims (configure a session-token template with `email`/`name`);
    fall back to the Clerk Backend API."""
    email, name = claims.get("email"), claims.get("name")
    if email:
        return email.lower(), name or email.split("@")[0]
    st = get_settings()
    if not st.clerk_secret_key:
        raise AuthError("cannot resolve user email: add `email` to the Clerk session token or set CLERK_SECRET_KEY")
    r = httpx.get(f"https://api.clerk.com/v1/users/{user_id}", headers={"Authorization": f"Bearer {st.clerk_secret_key}"}, timeout=10)
    r.raise_for_status()
    u = r.json()
    primary = next((e["email_address"] for e in u.get("email_addresses", []) if e["id"] == u.get("primary_email_address_id")), None)
    if not primary:
        raise AuthError("Clerk user has no primary email")
    full = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or primary.split("@")[0]
    return primary.lower(), full


def provision(s: Session, external_id: str, email: str, name: str) -> User:
    user = s.scalars(select(User).where(User.external_id == external_id)).first()
    if user:
        return user
    existing = s.scalars(select(User).where(User.email == email)).first()
    if existing:  # pre-created user (e.g. owner added by email) — link the Clerk identity
        existing.external_id = external_id
        return existing
    inv = s.scalars(select(Invitation).where(Invitation.email == email, Invitation.accepted_at.is_(None)).order_by(Invitation.created_at.desc())).first()
    if inv:
        user = User(workspace_id=inv.workspace_id, email=email, name=name, role=inv.role, external_id=external_id)
        inv.accepted_at = datetime.now(timezone.utc)
        s.add(user)
        s.flush()
        set_tenant(s, user.workspace_id)
        audit(s, user.workspace_id, user.id, "user.joined", user.id, {"via": "invitation", "role": inv.role})
        return user
    ws = Workspace(name=f"{name}'s workspace", policy={})
    s.add(ws)
    s.flush()
    user = User(workspace_id=ws.id, email=email, name=name, role="owner", external_id=external_id)
    s.add(user)
    s.flush()
    set_tenant(s, ws.id)
    audit(s, ws.id, user.id, "workspace.created", ws.id, {"owner": user.id})
    return user


def _verify_clerk(token: str, s: Session) -> Principal:
    claims = verify_clerk_token(token)
    user = s.scalars(select(User).where(User.external_id == claims["sub"])).first()
    if user is None:
        email, name = _clerk_profile(claims["sub"], claims)
        user = provision(s, claims["sub"], email, name)
        s.commit()
    if not user.active:
        raise AuthError("user deactivated")
    return Principal(user, claims)


def authenticate(token: str, s: Session) -> Principal:
    mode = get_settings().auth_mode
    p = _verify_clerk(token, s) if mode == "clerk" else _verify_local(token, s)
    set_tenant(s, p.user.workspace_id)
    return p
