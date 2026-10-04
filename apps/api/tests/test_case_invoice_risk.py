"""Policy-driven invoice risk on real cases, through the HTTP API (risk/case_scoring.py, api/risk.py)."""

import copy

from conftest import login
from sqlalchemy import select
from test_demo_flow import API, upload_and_run

from probity.db.models import User
from probity.db.session import session_scope
from probity.demo import seed
from probity.risk.case_scoring import default_policy


def sig(out, name):
    return next(s for s in out["signals"] if s["signal"] == name)


def test_case_scored_against_workspace_history(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    r = client.get(f"{API}/cases/{case['id']}/invoice-risk", headers=acc)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["meta"]["vendor_matched"] and out["meta"]["policy_source"] == "default" and out["meta"]["history_size"] > 0
    # bank account compared by fingerprint: the raw number is never in case data or the response
    assert sig(out, "bank_change")["score"] == 1.0
    assert "9812" in sig(out, "bank_change")["evidence"] and "1234" in sig(out, "bank_change")["evidence"]
    assert sig(out, "amount_anomaly")["score"] > 0.9 and "n=" in sig(out, "amount_anomaly")["evidence"]
    assert sig(out, "new_vendor")["score"] == 0.0 and sig(out, "math_mismatch")["score"] == 0.0
    assert out["coverage"] == 1.0 and out["tier"] == "HIGH" and out["escalated_by"] == ["bank_change"]
    assert out["explanation"]["top_drivers"][:2] == ["bank_change", "amount_anomaly"]
    assert "narrative" not in out


def test_clean_case_is_low(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "invoice_kaveri_clean.pdf")
    out = client.get(f"{API}/cases/{case['id']}/invoice-risk", headers=acc).json()
    assert out["tier"] == "LOW", out["explanation"]["text"]
    assert out["recommended_action"] == "approve"


def test_narrative_is_optional_and_never_fails(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    out = client.get(f"{API}/cases/{case['id']}/invoice-risk?narrate=true", headers=acc).json()
    n = out["narrative"]
    assert n["source"] in ("llm", "template") and "HIGH" in n["summary"]


def test_other_workspace_cannot_score_case(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    with session_scope() as s:
        ws = seed.seed_workspace(s, name="Other Co")
        uid = s.scalars(select(User).where(User.workspace_id == ws.id, User.role == "owner")).first().id
    other = {"Authorization": "Bearer " + client.post(f"{API}/auth/demo-login", json={"user_id": uid}).json()["token"]}
    assert client.get(f"{API}/cases/{case['id']}/invoice-risk", headers=other).status_code == 404


def test_workspace_policy_roundtrip(client):
    owner, acc = login(client, "owner"), login(client, "accountant")
    assert client.get(f"{API}/workspace/invoice-risk-policy", headers=acc).json()["source"] == "default"

    pol = copy.deepcopy(default_policy())
    pol["tiers"][0]["max_score"] = 0.1
    assert client.put(f"{API}/workspace/invoice-risk-policy", headers=acc, json=pol).status_code == 403
    r = client.put(f"{API}/workspace/invoice-risk-policy", headers=owner, json=pol)
    assert r.status_code == 200, r.text
    got = client.get(f"{API}/workspace/invoice-risk-policy", headers=acc).json()
    assert got["source"] == "workspace" and got["policy"]["tiers"][0]["max_score"] == 0.1

    case = upload_and_run(client, acc, "invoice_4821.pdf")
    assert client.get(f"{API}/cases/{case['id']}/invoice-risk", headers=acc).json()["meta"]["policy_source"] == "workspace"

    assert client.delete(f"{API}/workspace/invoice-risk-policy", headers=owner).json()["source"] == "default"
    assert client.get(f"{API}/workspace/invoice-risk-policy", headers=acc).json()["source"] == "default"


def test_invalid_policy_is_rejected(client):
    owner = login(client, "owner")
    bad = copy.deepcopy(default_policy())
    bad["weights"]["bank_change"] = -1
    bad["escalation_rules"].append({"signal": "bank_change", "at_least": 1, "min_tier_index": 9})
    r = client.put(f"{API}/workspace/invoice-risk-policy", headers=owner, json=bad)
    assert r.status_code == 400
    assert "weights.bank_change" in r.text and "escalation_rules" in r.text
