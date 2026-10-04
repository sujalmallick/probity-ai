"""Policy-driven invoice risk on real cases, through the HTTP API (risk/case_scoring.py, api/risk.py)."""

import copy

import pytest
from conftest import bearer, login
from factories import NEW_DOMAIN, bank_change_spec, build_world, clean_spec
from helpers import API, run_case

from probity.risk.case_scoring import default_policy

SPECS = {"bank_change": bank_change_spec, "clean": clean_spec}


@pytest.fixture(autouse=True)
def _world(world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21


def upload_and_run(client, h, name):
    return run_case(client, h, SPECS[name]())


def sig(out, name):
    return next(s for s in out["signals"] if s["signal"] == name)


def test_case_scored_against_workspace_history(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "bank_change")
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
    case = upload_and_run(client, acc, "clean")
    out = client.get(f"{API}/cases/{case['id']}/invoice-risk", headers=acc).json()
    assert out["tier"] == "LOW", out["explanation"]["text"]
    assert out["recommended_action"] == "approve"


def test_narrative_is_optional_and_never_fails(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "bank_change")
    out = client.get(f"{API}/cases/{case['id']}/invoice-risk?narrate=true", headers=acc).json()
    n = out["narrative"]
    assert n["source"] in ("llm", "template") and "HIGH" in n["summary"]


def test_narrative_is_written_once_per_score(client, monkeypatch):
    """Opening the Invoice risk tab again reuses the saved AI explanation; only a changed score asks the AI again,
    and a rule-based fallback is never kept."""
    from probity.risk import case_scoring, narrate

    calls = []

    def fake(result, **kw):  # type: ignore[no-untyped-def]
        calls.append(result["final_score"])
        return {"summary": f"Scored {result['tier']}", "key_points": [], "source": "llm" if len(calls) != 2 else "template", "note": None}

    monkeypatch.setattr(narrate, "narrate", fake)
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "bank_change")
    url = f"{API}/cases/{case['id']}/invoice-risk?narrate=true"
    first = client.get(url, headers=acc).json()["narrative"]
    again = client.get(url, headers=acc).json()["narrative"]
    assert len(calls) == 1 and again["cached"] is True and again["summary"] == first["summary"]
    assert "cached" not in client.get(f"{API}/cases/{case['id']}/invoice-risk", headers=acc).json()  # no narrate: no AI at all
    assert len(calls) == 1

    monkeypatch.setattr(case_scoring, "narrative_fingerprint", lambda result: "score changed")
    client.get(url, headers=acc)  # new facts: asked again; this time the AI fails (template) and nothing is kept
    client.get(url, headers=acc)  # so the next view tries the AI again
    assert len(calls) == 3


def test_other_workspace_cannot_score_case(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "bank_change")
    other = bearer(build_world(name="Other Co").user("owner"))
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

    case = upload_and_run(client, acc, "bank_change")
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
