"""Audit chain, RBAC matrix, status machine, policy gate, budgets."""

import pytest
from conftest import login, owner_session
from factories import NEW_ACCOUNT, NEW_DOMAIN, bank_change_spec, clean_spec
from helpers import run_case
from sqlalchemy import select, update

from probity import services as svc
from probity.db.audit import audit, verify_chain
from probity.db.models import AuditLog, Case, Workspace
from probity.db.session import session_scope
from probity.tools.base import Budget, BudgetExceeded


def test_audit_chain_detects_tampering(world):
    ws = world.workspace_id
    with session_scope(ws) as s:
        for i in range(3):
            audit(s, ws, "u", "test", f"e{i}", {"i": i})
    with session_scope(ws) as s:
        assert verify_chain(s, ws) == (True, None)
        row = s.scalars(select(AuditLog).where(AuditLog.workspace_id == ws).order_by(AuditLog.id.desc())).first()
        with pytest.raises(PermissionError):  # ORM refuses UPDATE
            row.action = "tampered"
            s.flush()
        s.rollback()
    # The database itself refuses UPDATE on audit_log (trigger + revoked grant).
    with pytest.raises(Exception, match="(?i)immutable|permission denied"):
        with session_scope(ws) as s:
            s.execute(update(AuditLog).where(AuditLog.entity == "e1").values(data={"i": 99}))


def test_rls_blocks_cross_tenant_reads(world):
    """With no tenant (or another tenant) bound, tenant tables return nothing, and writes into another tenant fail."""
    from probity.db.models import Vendor

    with session_scope(world.workspace_id) as s:
        assert len(list(s.scalars(select(Vendor)))) == 2
    with session_scope("ws_someone_else") as s:
        assert list(s.scalars(select(Vendor))) == []
    with session_scope() as s:
        assert list(s.scalars(select(Vendor))) == []
    with pytest.raises(Exception, match="(?i)row-level security"):
        with session_scope("ws_someone_else") as s:
            s.add(Vendor(workspace_id=world.workspace_id, name="smuggled"))
            s.flush()


def test_status_machine():
    c = Case(status="AWAITING_HUMAN")
    svc.transition(c, "APPROVED")
    with pytest.raises(svc.Conflict):
        svc.transition(c, "AWAITING_VENDOR")
    with pytest.raises(svc.Conflict):
        svc.transition(Case(status="AUTO_CLEARED"), "APPROVED")


@pytest.mark.parametrize("role,code", [("viewer", 403), ("accountant", 403), ("approver", 200), ("owner", 200)])
def test_rbac_policy_update(client, world, role, code):
    h = login(client, role)
    r = client.put("/api/v1/workspace/policy", headers=h, json={"auto_clear_enabled": True})
    assert r.status_code == (code if role == "owner" else 403)


def test_viewer_cannot_upload(client, world):
    r = client.post("/api/v1/documents", headers=login(client, "viewer"), files={"file": ("a.txt", b"Invoice No: 1", "text/plain")})
    assert r.status_code == 403


def test_viewer_cannot_reveal_account(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert client.get(f"/api/v1/cases/{case['id']}/reveal-account", headers=login(client, "viewer")).status_code == 403
    r = client.get(f"/api/v1/cases/{case['id']}/reveal-account", headers=login(client, "approver"))
    assert r.json()["bank_account"] == NEW_ACCOUNT
    assert NEW_ACCOUNT not in str(client.get(f"/api/v1/cases/{case['id']}", headers=login(client, "viewer")).json())


def test_illegal_decision_409(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=login(client, "approver"), json={"decision": "APPROVE", "reason": "ok"})
    assert r.status_code == 409


def test_approve_high_requires_reason(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=login(client, "approver"), json={"decision": "APPROVE", "reason": ""})
    assert r.status_code == 400


def test_critical_never_auto_clears_and_needs_two_approvers(client, world, fake_lookups):
    """Auto-clear abuse: even with auto-clear on and a huge auto-clear limit, CRITICAL routes to humans."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    with owner_session() as s:
        ws = s.get(Workspace, world.workspace_id)
        ws.policy = {"auto_clear_enabled": True, "auto_clear_max_amount_minor": 10**12, "weight_overrides": {"bank_account_changed": 50}}
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["risk"]["tier"] == "CRITICAL" and case["status"] == "AWAITING_HUMAN"
    a1, a2 = login(client, "approver", 0), login(client, "approver", 1)
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=a1, json={"decision": "APPROVE", "reason": "checked by phone"})
    assert r.json()["status"] == "AWAITING_HUMAN"  # 1 of 2
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=a2, json={"decision": "APPROVE", "reason": "second approval"})
    assert r.json()["status"] == "APPROVED"


def test_budget_caps():
    b = Budget(max_llm_calls=2, max_tokens=100, max_web_calls=1, max_seconds=60)
    b.charge_web()
    with pytest.raises(BudgetExceeded):
        b.charge_web()
    b.charge_llm(10)
    b.charge_llm(10)
    with pytest.raises(BudgetExceeded):
        b.charge_llm(10)
    assert set(b.exhausted) == {"web_calls", "llm_calls"}


def test_investigate_further_depth_limit(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    for depth in (1, 2):
        r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=appr, json={"decision": "INVESTIGATE_FURTHER", "reason": "dig deeper"})
        assert r.status_code == 200, r.text
        c = client.get(f"/api/v1/cases/{case['id']}", headers=acc).json()
        assert c["depth"] == depth and c["status"] == "AWAITING_HUMAN" and c["risk"]["score"] == 70
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=appr, json={"decision": "INVESTIGATE_FURTHER", "reason": "again"})
    assert r.status_code == 409  # deterministic termination: depth ≤ 2
