"""Guardrails test table (Guardrails.md) as pytest cases: injection, fabrication, overclaiming, SSRF, PII, auto-clear
abuse, loops and spoofed replies. Runs on factory data with injected fakes."""

from dataclasses import replace

import pytest
from conftest import login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec, clean_spec, injection_spec
from helpers import API, contributions, record_reply, run_case, send_verification
from sqlalchemy import select

from probity import services
from probity.db.models import ClaimRow, Workspace
from probity.db.session import session_scope
from probity.evidence.models import AgentClaim
from probity.evidence.store import add_claim
from probity.evidence.verifier import verify_claims
from probity.guardrails.text import language_violations, neutralize_language
from probity.tools.fetch import FetchBlocked, check_url


@pytest.fixture(autouse=True)
def _world(world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21


def test_injection_in_invoice_cannot_lower_the_score(client):
    h = login(client, "accountant")
    without = run_case(client, h, replace(injection_spec(), invoice_number="AC-5001", footer=None))
    with_inj = run_case(client, h, replace(injection_spec(), invoice_number="AC-5002"))
    fired = any(c["signal"] == "suspicious_instruction_in_document" and c["status"] == "verified" for c in with_inj["claims"])
    # The second copy has the same vendor, amount and date as the first (still open), so it is also flagged as a
    # duplicate; this test is only about the injection, so compare every other contribution.
    got = {k: v for k, v in contributions(with_inj).items() if k != "duplicate_invoice"}
    assert fired and got == {**contributions(without), "suspicious_instruction_in_document": 15}, (got, contributions(without))
    assert with_inj["status"] != "AUTO_CLEARED"


def test_fabricated_claims_are_dropped(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    with session_scope(world.workspace_id) as s:
        c1 = add_claim(s, world.workspace_id, case["id"], "web_research",
                       AgentClaim(claim="Domain is 5 days old", signal="new_domain", confidence=0.9, assertion={"op": "age_below"}))
        c2 = add_claim(s, world.workspace_id, case["id"], "web_research",
                       AgentClaim(claim="Bank changed", signal="bank_account_changed", evidence_ids=["ev_doesnotexist"], confidence=0.9))
        verify_claims(s, world.workspace_id, case["id"], [c1, c2])
        assert c1.status == "dropped" and c2.status == "dropped"
    assert services.rescore(world.workspace_id, case["id"])["score"] == 0


def test_no_accusatory_language_in_summary_or_drafts(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    c = run_case(client, acc, bank_change_spec())
    assert not language_violations(c["summary"]) and not any(language_violations(f) for f in c["recommendation"]["top_findings"])
    client.post(f"{API}/cases/{c['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    d = client.get(f"{API}/cases/{c['id']}/drafts", headers=appr).json()["items"][0]
    r = client.patch(f"{API}/cases/{c['id']}/drafts/{d['id']}", headers=appr, json={"body": "You are a fraudster running a scam."})
    assert r.status_code == 400
    assert not language_violations(neutralize_language("Is this vendor a fraudster? Looks like a scam."))


@pytest.mark.parametrize("url", ["https://169.254.169.254/latest/meta-data/", "http://example.com", "https://127.0.0.1/admin",
                                 "https://[::ffff:169.254.169.254]/"])
def test_ssrf_targets_blocked(url):
    with pytest.raises(FetchBlocked):
        check_url(url)


def test_viewer_never_sees_full_account_number(client):
    from factories import NEW_ACCOUNT

    c = run_case(client, login(client, "accountant"), bank_change_spec())
    hv = login(client, "viewer")
    body = str(client.get(f"{API}/cases/{c['id']}", headers=hv).json()) + str(client.get(f"{API}/cases/{c['id']}/evidence", headers=hv).json())
    assert NEW_ACCOUNT not in body
    assert client.get(f"{API}/cases/{c['id']}/reveal-account", headers=hv).status_code == 403


def test_critical_case_never_auto_clears_even_with_auto_clear_on(client, world):
    with owner_session() as s:
        s.get(Workspace, world.workspace_id).policy = {"auto_clear_enabled": True, "auto_clear_max_amount_minor": 10**12,
                                                       "weight_overrides": {"bank_account_changed": 50}}
    c = run_case(client, login(client, "accountant"), bank_change_spec())
    assert c["risk"]["tier"] == "CRITICAL" and c["status"] == "AWAITING_HUMAN" and c["recommendation"]["gate"]["dual_approval"]


def test_failing_node_retries_then_degrades_and_investigation_depth_is_bounded(client, monkeypatch):
    from probity.agents import web

    calls = {"n": 0}

    def broken(ctx, depth):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        raise RuntimeError("search provider down")

    monkeypatch.setattr(web, "run", broken)
    c = run_case(client, login(client, "accountant"), bank_change_spec())
    assert calls["n"] == 3 and c["partial"] and c["status"] == "AWAITING_HUMAN"
    assert c["checks"]["external_reputation"]["status"] == "failed"
    ha = login(client, "approver")
    codes = [client.post(f"{API}/cases/{c['id']}/decision", headers=ha, json={"decision": "INVESTIGATE_FURTHER", "reason": "again"}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 409]


def test_spoofed_reply_is_flagged_and_changes_nothing(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    c = run_case(client, acc, bank_change_spec())
    send_verification(client, c, appr)
    lookalike = VENDOR_A.contact_email.replace("components", "cornponents")
    r = record_reply(client, c, acc, lookalike, "Hello, yes our bank details changed. URGENT: please release payment today itself "
                                                "to the new account ending 9812, otherwise the shipment will be held.")
    assert any("not a verified domain" in i for i in r["indicators"])
    assert client.get(f"{API}/cases/{c['id']}", headers=acc).json()["risk"]["score"] == 70
    with owner_session() as s:
        assert {x.status for x in s.scalars(select(ClaimRow).where(ClaimRow.id.in_(r["claim_ids"])))} <= {"unverified"}
