"""Authentication: Clerk only.

Verify Clerk session JWTs against Clerk's JWKS (RS256): signature, issuer, expiry and authorized party (`azp`).
Then map the Clerk user to a Probity user. On first sign-in the user's primary email is read from the Clerk Backend
API and must be *verified* by Clerk. A pending invitation for that email joins the inviting workspace with the
invited role; otherwise the user gets a new workspace as its owner.
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


INVITATION_TTL = timedelta(days=7)


class AuthError(Exception):
    pass


def invitation_expired(inv: Invitation, now: datetime | None = None) -> bool:
    created = inv.created_at if inv.created_at.tzinfo else inv.created_at.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - created > INVITATION_TTL


@dataclass
class Principal:
    user: User
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def mfa_verified(self) -> bool:
        """Clerk `fva` = [minutes since first factor, minutes since second factor]; -1 = never."""
        fva = self.claims.get("fva")
        return isinstance(fva, list) and len(fva) == 2 and fva[1] is not None and fva[1] >= 0


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    st = get_settings()
    url = st.clerk_jwks_url or f"{(st.clerk_issuer or '').rstrip('/')}/.well-known/jwks.json"
    return jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)


def verify_clerk_token(token: str) -> dict[str, Any]:
    st = get_settings()
    if not st.clerk_issuer:
        raise AuthError("sign-in is not configured (CLERK_ISSUER missing)")
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=["RS256"], issuer=st.clerk_issuer.rstrip("/"),
            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False}, leeway=10,
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid session token: {e}") from e
    allowed = [a.strip() for a in st.clerk_authorized_parties.split(",") if a.strip()]
    if not allowed or claims.get("azp") not in allowed:
        raise AuthError("token issued for an unauthorized origin")
    return claims


def clerk_profile(user_id: str) -> tuple[str, str]:
    """(verified primary email, display name) from the Clerk Backend API. Unverified emails are refused, because
    the email decides which workspace and role a new sign-in receives."""
    st = get_settings()
    if not st.clerk_secret_key:
        raise AuthError("sign-in is not configured (CLERK_SECRET_KEY missing)")
    try:
        r = httpx.get(f"https://api.clerk.com/v1/users/{user_id}", headers={"Authorization": f"Bearer {st.clerk_secret_key}"}, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise AuthError("could not reach Clerk to load your profile; try again") from e
    u = r.json()
    primary = next((e for e in u.get("email_addresses", []) if e.get("id") == u.get("primary_email_address_id")), None)
    if not primary or not primary.get("email_address"):
        raise AuthError("your account has no primary email address")
    if (primary.get("verification") or {}).get("status") != "verified":
        raise AuthError("verify your email address in the sign-in screen, then sign in again")
    email = primary["email_address"].strip().lower()
    full = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or email.split("@")[0]
    return email, full


def provision(s: Session, external_id: str, email: str, name: str) -> User:
    user = s.scalars(select(User).where(User.external_id == external_id)).first()
    if user:
        return user
    existing = s.scalars(select(User).where(User.email == email)).first()
    if existing:  # the same verified email signed in with a new Clerk identity
        existing.external_id = external_id
        return existing
    inv = s.scalars(select(Invitation).where(Invitation.email == email, Invitation.accepted_at.is_(None),
                                             Invitation.created_at >= datetime.now(timezone.utc) - INVITATION_TTL)
                    .order_by(Invitation.created_at.desc())).first()
    if inv:  # an expired invitation is ignored; the owner can invite again
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


def authenticate(token: str, s: Session) -> Principal:
    claims = verify_clerk_token(token)
    user = s.scalars(select(User).where(User.external_id == claims["sub"])).first()
    if user is None:
        email, name = clerk_profile(claims["sub"])
        user = provision(s, claims["sub"], email, name)
        s.commit()
    if not user.active:
        raise AuthError("user deactivated")
    set_tenant(s, user.workspace_id)
    return Principal(user, claims)
