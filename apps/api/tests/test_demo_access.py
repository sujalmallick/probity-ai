"""Demo sign-in (H1): passwordless "sign in as anyone" never reaches other machines by default, a public demo
only exposes the demo workspace, and the demo write endpoints respect roles."""

import pytest
from conftest import login
from fastapi.testclient import TestClient
from sqlalchemy import select

from probity.config import Settings, get_settings
from probity.db.models import AuditLog, User
from probity.db.session import session_scope
from probity.demo import seed

API = "/api/v1"


@pytest.fixture()
def app(fresh_db):
    from probity.api.main import app

    return app


def _as(app, host: str) -> TestClient:
    return TestClient(app, client=(host, 50000))


def _uid(role: str, workspace_name: str = seed.DEMO_WORKSPACE_NAME) -> str:
    with session_scope() as s:
        from probity.db.models import Workspace

        ws = s.scalars(select(Workspace).where(Workspace.name == workspace_name)).first()
        return s.scalars(select(User).where(User.workspace_id == ws.id, User.role == role)).first().id


@pytest.mark.parametrize("host", ["10.0.0.7", "203.0.113.9", "testclient"])
def test_dev_demo_login_is_local_only(app, monkeypatch, host):
    monkeypatch.setattr(get_settings(), "env", "dev")
    c = _as(app, host)
    assert c.get(f"{API}/auth/config").json()["demo_login"] is False
    assert c.get(f"{API}/app/config").json()["auth"]["demo_login"] is False
    assert c.get(f"{API}/auth/demo-users").status_code == 404
    assert c.post(f"{API}/auth/demo-login", json={"user_id": _uid("owner")}).status_code == 404


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_dev_demo_login_works_on_this_machine(app, monkeypatch, host):
    monkeypatch.setattr(get_settings(), "env", "dev")
    c = _as(app, host)
    assert c.get(f"{API}/app/config").json()["auth"]["demo_login"] is True
    assert c.post(f"{API}/auth/demo-login", json={"user_id": _uid("owner")}).status_code == 200


def test_public_demo_exposes_only_the_demo_workspace(app, monkeypatch):
    with session_scope() as s:
        seed.seed_workspace(s, name="Real Customer Ltd")
    monkeypatch.setattr(get_settings(), "env", "demo")
    c = _as(app, "203.0.113.9")
    users = c.get(f"{API}/auth/demo-users").json()
    assert users and {u["workspace"]["name"] for u in users} == {seed.DEMO_WORKSPACE_NAME}
    assert c.post(f"{API}/auth/demo-login", json={"user_id": _uid("owner", "Real Customer Ltd")}).status_code == 404
    assert c.post(f"{API}/auth/demo-login", json={"user_id": _uid("owner")}).status_code == 200


def test_demo_login_refuses_deactivated_users(client):
    uid = _uid("viewer")
    with session_scope() as s:
        s.get(User, uid).active = False
    assert client.post(f"{API}/auth/demo-login", json={"user_id": uid}).status_code == 404


def test_prod_never_offers_demo_login(app, monkeypatch):
    monkeypatch.setattr(get_settings(), "env", "prod")
    c = _as(app, "127.0.0.1")
    assert c.get(f"{API}/auth/demo-users").status_code == 404
    assert c.post(f"{API}/auth/demo-login", json={"user_id": _uid("owner")}).status_code == 404


def test_public_demo_requires_real_jwt_secret(monkeypatch):
    monkeypatch.setenv("ENV", "demo")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(_env_file=None).validate_for_env()
    monkeypatch.setenv("JWT_SECRET", "short")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(_env_file=None).validate_for_env()
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    Settings(_env_file=None).validate_for_env()


def test_demo_speed_is_owner_only_and_audited(client):
    for role in ("viewer", "accountant", "approver"):
        assert client.put(f"{API}/demo/speed?delay_ms=500", headers=login(client, role)).status_code == 403
    assert client.put(f"{API}/demo/speed?delay_ms=500", headers=login(client, "owner")).status_code == 200
    with session_scope() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "policy.updated").order_by(AuditLog.id.desc())).first()
        assert row is not None and row.data["demo_agent_delay_ms"]["after"] == 500


def test_simulated_vendor_reply_needs_accountant(client):
    r = client.post(f"{API}/demo/vendor-reply/case_doesnotexist", headers=login(client, "viewer"))
    assert r.status_code == 403
