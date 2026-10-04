"""Audit chain, RBAC matrix, status machine, policy gate, budgets."""

import pytest
from conftest import login
from sqlalchemy import select, update

from probity import services as svc
from probity.db.audit import audit, verify_chain
from probity.db.models import AuditLog, Case, Workspace
from probity.db.session import session_scope
from probity.tools.base import Budget, BudgetExceeded


def test_audit_chain_detects_tampering(fresh_db):
    with session_scope() as s:
        for i in range(3):
            audit(s, fresh_db, "u", "test", f"e{i}", {"i": i})
    with session_scope() as s:
        assert verify_chain(s, fresh_db) == (True, None)
        row = s.scalars(select(AuditLog).where(AuditLog.workspace_id == fresh_db).order_by(AuditLog.id.desc())).first()
        with pytest.raises(PermissionError):  # ORM refuses UPDATE
            row.action = "tampered"
            s.flush()
        s.rollback()
    with session_scope() as s:  # raw SQL bypasses the ORM guard — the hash chain still catches it
        s.execute(update(AuditLog).where(AuditLog.entity == "e1").values(data={"i": 99}))
    with session_scope() as s:
        ok, bad = verify_chain(s, fresh_db)
        assert not ok and bad is not None


def test_status_machine():
    c = Case(status="AWAITING_HUMAN")
    svc.transition(c, "APPROVED")
    with pytest.raises(svc.Conflict):
        svc.transition(c, "AWAITING_VENDOR")
    with pytest.raises(svc.Conflict):
        svc.transition(Case(status="AUTO_CLEARED"), "APPROVED")


@pytest.mark.parametrize("role,code", [("viewer", 403), ("accountant", 403), ("approver", 200), ("owner", 200)])
def test_rbac_policy_update(client, role, code):
    h = login(client, role)
    r = client.put("/api/v1/workspace/policy", headers=h, json={"auto_clear_enabled": True})
    assert r.status_code == (code if role == "owner" else 403)


def test_viewer_cannot_upload(client):
    r = client.post("/api/v1/documents", headers=login(client, "viewer"), files={"file": ("a.txt", b"Invoice No: 1", "text/plain")})
    assert r.status_code == 403


def test_viewer_cannot_reveal_account(client):
    from test_demo_flow import upload_and_run

    case = upload_and_run(client, login(client, "accountant"), "invoice_4821.pdf")
    assert client.get(f"/api/v1/cases/{case['id']}/reveal-account", headers=login(client, "viewer")).status_code == 403
    r = client.get(f"/api/v1/cases/{case['id']}/reveal-account", headers=login(client, "approver"))
    assert r.json()["bank_account"] == "50100098129812"
    assert "50100098129812" not in str(client.get(f"/api/v1/cases/{case['id']}", headers=login(client, "viewer")).json())


def test_illegal_decision_409(client):
    from test_demo_flow import upload_and_run

    case = upload_and_run(client, login(client, "accountant"), "invoice_kaveri_clean.pdf")
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=login(client, "approver"), json={"decision": "APPROVE", "reason": "ok"})
    assert r.status_code == 409


def test_approve_high_requires_reason(client):
    from test_demo_flow import upload_and_run

    case = upload_and_run(client, login(client, "accountant"), "invoice_4821.pdf")
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=login(client, "approver"), json={"decision": "APPROVE", "reason": ""})
    assert r.status_code == 400


def test_critical_never_auto_clears_and_needs_two_approvers(client, fresh_db):
    """Auto-clear abuse: even with auto-clear on and a huge auto-clear limit, CRITICAL routes to humans."""
    from test_demo_flow import upload_and_run

    with session_scope() as s:
        ws = s.get(Workspace, fresh_db)
        ws.policy = {"auto_clear_enabled": True, "auto_clear_max_amount_minor": 10**12, "weight_overrides": {"bank_account_changed": 50}}
    case = upload_and_run(client, login(client, "accountant"), "invoice_4821.pdf")
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


def test_investigate_further_depth_limit(client):
    from test_demo_flow import upload_and_run

    acc, appr = login(client, "accountant"), login(client, "approver")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    for depth in (1, 2):
        r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=appr, json={"decision": "INVESTIGATE_FURTHER", "reason": "dig deeper"})
        assert r.status_code == 200, r.text
        c = client.get(f"/api/v1/cases/{case['id']}", headers=acc).json()
        assert c["depth"] == depth and c["status"] == "AWAITING_HUMAN" and c["risk"]["score"] == 70
    r = client.post(f"/api/v1/cases/{case['id']}/decision", headers=appr, json={"decision": "INVESTIGATE_FURTHER", "reason": "again"})
    assert r.status_code == 409  # deterministic termination: depth ≤ 2
