"""Sign-in is Clerk only (H1): no demo users, no passwordless or HS256 path, no env switch that turns on a mock or
offline mode. New users get their own workspace as owner; invited users join with the invited role."""

import jwt
import pytest
from conftest import ISSUER, bearer, clerk_token, login, owner_session
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from probity.config import ConfigError, Settings
from probity.db.models import AuditLog, User, Workspace
from helpers import API

REMOVED = [("get", "/auth/config"), ("get", "/auth/demo-users"), ("post", "/auth/demo-login"), ("post", "/demo/seed"), ("get", "/demo/files/x.pdf"),
           ("post", "/demo/vendor-reply/case_x"), ("put", "/demo/speed"), ("get", "/benchmark/summary")]


@pytest.mark.parametrize("method,path", REMOVED)
def test_demo_and_benchmark_endpoints_do_not_exist(client, world, method, path):
    r = getattr(client, method)(f"{API}{path}", headers=login(client, "owner"))
    assert r.status_code in (404, 405)


def test_public_config_has_no_demo_or_mock_modes(client):
    cfg = client.get(f"{API}/app/config").json()
    assert cfg["auth"] == {"mode": "clerk", "sign_up": True}
    assert set(cfg["features"]) == {"landing_page"}
    assert cfg["integrations"]["gst_registry"] == "unavailable"


def test_settings_have_no_mode_switches():
    fields = set(Settings.model_fields)
    for banned in ("llm_mode", "tools_mode", "auth_mode", "email_backend", "demo_features", "agent_delay_ms", "jwt_secret", "ocr_enabled"):
        assert banned not in fields


def test_startup_refuses_without_required_settings(settings):
    from probity.api import main

    settings(ANTHROPIC_API_KEY="", CLERK_SECRET_KEY="")
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY") as e:
        main._startup()
    assert "CLERK_SECRET_KEY" in str(e.value)


@pytest.mark.parametrize("var,value,needle", [
    ("DATABASE_URL", "", "DATABASE_URL"),
    ("DATABASE_URL", "sqlite:///x.db", "PostgreSQL"),
    ("CLERK_ISSUER", "", "CLERK_ISSUER"),
    ("CLERK_AUTHORIZED_PARTIES", "", "CLERK_AUTHORIZED_PARTIES"),
    ("FIELD_KEY_B64", "", "FIELD_KEY_B64"),
    ("FIELD_KEY_B64", "c2hvcnQ=", "32 bytes"),
    ("HMAC_KEY", "short", "HMAC_KEY"),
])
def test_each_required_setting_is_checked(settings, var, value, needle):
    st = settings(**{var: value})
    with pytest.raises(ConfigError, match=needle):
        st.validate_required()


def test_checklist_never_prints_secret_values(settings):
    st = settings()
    text = st.checklist_text() + repr(st)
    for secret in (st.field_key_b64, st.hmac_key, st.anthropic_api_key, st.clerk_secret_key):
        assert secret and secret not in text


def test_valid_clerk_token_signs_in(client, world):
    r = client.get(f"{API}/me", headers=login(client, "approver"))
    assert r.status_code == 200 and r.json()["role"] == "approver"


@pytest.mark.parametrize("make", [
    lambda sub: jwt.encode({"sub": sub, "iss": ISSUER, "azp": "http://testserver", "exp": 9999999999, "iat": 1}, "secret", algorithm="HS256"),
    lambda sub: jwt.encode({"sub": sub, "iss": ISSUER, "azp": "http://testserver", "exp": 9999999999, "iat": 1}, None, algorithm="none"),
    lambda sub: clerk_token(sub, issuer="https://evil.example"),
    lambda sub: clerk_token(sub, azp="https://evil.example"),
    lambda sub: clerk_token(sub, exp_in=-120),
    lambda sub: clerk_token(sub, key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),
    lambda sub: "not-a-token",
])
def test_forged_or_wrong_tokens_are_rejected(client, world, make):
    sub = world.user("owner").external_id
    r = client.get(f"{API}/me", headers={"Authorization": f"Bearer {make(sub)}"})
    assert r.status_code == 401


def test_no_token_is_rejected(client, world):
    assert client.get(f"{API}/me").status_code == 401


def test_deactivated_user_is_rejected(client, world):
    with owner_session() as s:
        s.get(User, world.user("viewer").id).active = False
    assert client.get(f"{API}/me", headers=bearer(world.user("viewer"))).status_code in (401, 403)


def test_first_sign_in_creates_own_workspace_as_owner(client, db, clerk):
    clerk["user_new_1"] = ("new.person@company.test", "New Person")
    r = client.get(f"{API}/me", headers=bearer("user_new_1"))
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["role"] == "owner" and me["email"] == "new.person@company.test" and me["workspace"]["name"] == "New Person's workspace"
    with owner_session() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action == "workspace.created")).first() is not None
        assert s.scalars(select(Workspace)).all().__len__() == 1


def test_invited_user_accepts_and_joins_with_invited_role(client, world, clerk):
    owner = login(client, "owner")
    r = client.post(f"{API}/workspace/invitations", headers=owner, json={"email": "invitee@company.test", "role": "approver"})
    assert r.status_code == 201, r.text
    assert r.json()["email_sent"] is False  # not on the email allowlist → blocked, invitation still stored
    clerk["user_invitee"] = ("invitee@company.test", "Invited Person")
    pending = client.get(f"{API}/me", headers=bearer("user_invitee"))
    assert pending.status_code == 409 and pending.json()["error"]["code"] == "invitation_pending"
    [inv] = pending.json()["error"]["invitations"]
    assert inv["role"] == "approver" and inv["workspace"] and inv["invited_by"]["email"] == world.user("owner").email
    joined = client.post(f"{API}/me/join", headers=bearer("user_invitee"), json={"invitation_id": inv["id"]})
    assert joined.status_code == 200, joined.text
    me = client.get(f"{API}/me", headers=bearer("user_invitee")).json()
    assert me["role"] == "approver" and me["workspace"]["id"] == world.workspace_id
    assert client.post(f"{API}/me/join", headers=bearer("user_invitee"), json={"own_workspace": True}).status_code == 409


def test_a_strangers_invitation_is_never_joined_automatically(client, world, clerk):
    """Anyone can sign up and invite any email: the invitee sees who invited them and chooses (no auto-join)."""
    clerk["user_attacker"] = ("attacker@evil.test", "Acme Finance")
    attacker = bearer("user_attacker")
    assert client.get(f"{API}/me", headers=attacker).status_code == 200
    assert client.post(f"{API}/workspace/invitations", headers=attacker, json={"email": "ap@company.test", "role": "owner"}).status_code == 201
    assert client.post(f"{API}/workspace/invitations", headers=login(client, "owner"), json={"email": "ap@company.test", "role": "accountant"}).status_code == 201
    clerk["user_ap"] = ("ap@company.test", "AP Clerk")
    r = client.get(f"{API}/me", headers=bearer("user_ap"))
    assert r.status_code == 409
    inviters = {i["invited_by"]["email"] for i in r.json()["error"]["invitations"]}
    assert inviters == {"attacker@evil.test", world.user("owner").email}
    with owner_session() as s:
        assert s.scalars(select(User).where(User.email == "ap@company.test")).first() is None  # nothing created yet
    me = client.post(f"{API}/me/join", headers=bearer("user_ap"), json={"own_workspace": True}).json()
    assert me["role"] == "owner" and me["workspace"]["id"] != world.workspace_id


def test_join_only_accepts_your_own_invitations(client, world, clerk):
    owner = login(client, "owner")
    inv = client.post(f"{API}/workspace/invitations", headers=owner, json={"email": "someone@company.test", "role": "owner"}).json()
    clerk["user_other"] = ("other@company.test", "Other Person")
    r = client.post(f"{API}/me/join", headers=bearer("user_other"), json={"invitation_id": inv["id"]})
    assert r.status_code == 404
    assert client.post(f"{API}/me/join", headers=bearer("user_other"), json={}).status_code == 400
    assert client.post(f"{API}/me/join", json={"own_workspace": True}).status_code == 401


def test_invite_does_not_reveal_who_uses_probity(client, world, clerk):
    clerk["user_elsewhere"] = ("elsewhere@company.test", "Elsewhere")
    assert client.get(f"{API}/me", headers=bearer("user_elsewhere")).status_code == 200  # owns another workspace
    owner = login(client, "owner")
    assert client.post(f"{API}/workspace/invitations", headers=owner, json={"email": "elsewhere@company.test", "role": "viewer"}).status_code == 201
    member = world.user("viewer").email
    assert client.post(f"{API}/workspace/invitations", headers=owner, json={"email": member, "role": "viewer"}).status_code == 409


def test_new_identity_cannot_take_over_an_existing_account(client, world, clerk):
    victim = world.user("approver")
    clerk[victim.external_id] = (victim.email, victim.name)  # the original Clerk identity still exists
    clerk["user_new_identity"] = (victim.email, "Someone Else")
    assert client.get(f"{API}/me", headers=bearer("user_new_identity")).status_code == 401
    with owner_session() as s:
        assert s.get(User, victim.id).external_id == victim.external_id
        assert s.scalars(select(AuditLog).where(AuditLog.action == "auth.relink_refused")).first() is not None


def test_deleted_and_recreated_clerk_account_is_relinked(client, world, clerk):
    person = world.user("approver")  # their old Clerk identity is gone (not in the fake Clerk directory)
    clerk["user_recreated"] = (person.email, person.name)
    me = client.get(f"{API}/me", headers=bearer("user_recreated")).json()
    assert me["id"] == person.id and me["role"] == "approver"
    with owner_session() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.action == "user.relinked")).first() is not None


def test_rejected_token_does_not_echo_library_details(client, world):
    r = client.get(f"{API}/me", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401 and r.json()["error"]["message"] == "invalid session token"


def test_unverified_email_cannot_sign_in(client, db, clerk, monkeypatch):
    from probity import auth

    def unverified(user_id):  # type: ignore[no-untyped-def]
        raise auth.AuthError("verify your email address in the sign-in screen, then sign in again")

    monkeypatch.setattr(auth, "clerk_profile", unverified)
    assert client.get(f"{API}/me", headers=bearer("user_unverified")).status_code == 401


@pytest.mark.parametrize("role,code", [("viewer", 403), ("accountant", 403), ("approver", 403), ("owner", 201)])
def test_only_owner_invites(client, world, role, code):
    r = client.post(f"{API}/workspace/invitations", headers=login(client, role), json={"email": f"x-{role}@company.test", "role": "viewer"})
    assert r.status_code == code


def test_unknown_api_path_is_404_not_the_web_app(client):
    r = client.get(f"{API}/no-such-endpoint")
    assert r.status_code == 404 and "json" in r.headers["content-type"]


def test_expired_invitation_is_ignored(client, world, clerk):
    from datetime import datetime, timedelta, timezone

    from probity.db.models import Invitation

    owner = login(client, "owner")
    assert client.post(f"{API}/workspace/invitations", headers=owner, json={"email": "late@company.test", "role": "approver"}).status_code == 201
    with owner_session() as s:
        inv = s.scalars(select(Invitation).where(Invitation.email == "late@company.test")).one()
        inv.created_at = datetime.now(timezone.utc) - timedelta(days=8)
    listed = client.get(f"{API}/workspace/invitations", headers=owner).json()["items"]
    assert listed[0]["expired"] is True and listed[0]["expires_at"]
    clerk["user_late"] = ("late@company.test", "Late Person")
    me = client.get(f"{API}/me", headers=bearer("user_late")).json()
    assert me["workspace"]["id"] != world.workspace_id and me["role"] == "owner"  # did not join with the stale invite
