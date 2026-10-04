"""Phase 6: per-agent trace and sanity checks."""

from conftest import bearer, login, owner_session
from factories import NEW_ACCOUNT, NEW_DOMAIN, bank_change_spec, build_world, clean_spec
from helpers import API, run_case
from sqlalchemy import select

from probity import sanity
from probity.db.models import Case, ClaimRow, EvidenceRow


def _agents(trace):  # type: ignore[no-untyped-def]
    return {a["agent"]: a for a in trace["agents"]}


def test_trace_shows_each_agent_and_its_gaps(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc = login(client, "accountant")
    case = run_case(client, acc, bank_change_spec())
    t = client.get(f"{API}/cases/{case['id']}/trace", headers=login(client, "viewer")).json()
    a = _agents(t)
    for name in ("document", "orchestrator", "vendor_investigator", "transaction_analyst", "web_research", "verification",
                 "risk_engine", "case_analyst"):
        assert a[name]["status"] == "done", (name, a[name]["status"])
    assert a["vendor_investigator"]["checks"]["domain_verification"]["status"] == "fired"
    assert a["transaction_analyst"]["checks"]["bank_account_verification"]["status"] == "fired"
    assert a["transaction_analyst"]["claims"]["verified"] >= 2
    web = a["web_research"]
    assert any(c["check"] == "external_reputation" for c in web["could_not_verify"])
    assert any(f.get("label") == "rule-based fallback, AI unavailable" for f in web["fallbacks"])  # queries written by rules
    assert any(f["what"] == "summary" for f in a["case_analyst"]["fallbacks"])
    assert a["case_analyst"]["ai"]["calls"] >= 1 and a["case_analyst"]["ai"]["failed"] == a["case_analyst"]["ai"]["calls"]
    assert t["totals"]["could_not_verify"] >= 2 and t["totals"]["fallbacks"] >= 2
    assert t["sanity"]["ok"] is True and t["sanity"]["problems"] == []
    assert NEW_ACCOUNT not in str(t)  # account numbers stay masked in the trace


def test_trace_is_workspace_scoped(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    other = bearer(build_world(name="Other Co").user("owner"))
    assert client.get(f"{API}/cases/{case['id']}/trace", headers=other).status_code == 404


def test_clean_case_passes_sanity_and_auto_clears(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AUTO_CLEARED" and case["recommendation"]["sanity"]["ok"] is True


def test_sanity_rules_catch_inconsistent_results(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    with owner_session() as s:
        c = s.get(Case, case["id"])
        assert sanity.check_case(s, c) == []
        # a point whose claim is no longer verified
        claim = s.scalars(select(ClaimRow).where(ClaimRow.case_id == c.id, ClaimRow.signal == "price_anomaly")).one()
        claim.status = "unverified"
        s.flush()
        codes = {p["code"] for p in sanity.check_case(s, c)}
        assert "unverified_points" in codes
        claim.status = "verified"
        # a score that is not the sum of its parts, and a tier that doesn't match
        c.risk = {**c.risk, "score": 99}
        codes = {p["code"] for p in sanity.check_case(s, c)}
        assert {"score_mismatch", "tier_mismatch"} <= codes
        c.risk = {**c.risk, "score": case["risk"]["score"]}
        # a citation of evidence from another case
        other = run_case(client, login(client, "accountant"), clean_spec())
        foreign = EvidenceRow(workspace_id=c.workspace_id, case_id=other["id"], source="invoice", source_ref="invoice", tier=1,
                              content_hash="0" * 64, agent="test")
        s.add(foreign)
        s.flush()
        claim.evidence_ids = [*claim.evidence_ids, foreign.id]
        s.flush()
        assert "foreign_evidence" in {p["code"] for p in sanity.check_case(s, c)}
        # an engine summary without its fallback label
        c.recommendation = {**c.recommendation, "summary_fallback": None}
        assert "unlabelled_fallback" in {p["code"] for p in sanity.check_case(s, c)}
        s.rollback()


def test_unsupported_all_clear_is_caught(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())  # web research could not verify here
    with owner_session() as s:
        c = s.get(Case, case["id"])
        ev = s.scalars(select(EvidenceRow).where(EvidenceRow.case_id == c.id)).first()
        s.add(ClaimRow(workspace_id=c.workspace_id, case_id=c.id, agent="web_research", statement="No adverse public findings located in 0 sources.",
                       evidence_ids=[ev.id], status="unverified", data={}))
        s.flush()
        assert "unsupported_all_clear" in {p["code"] for p in sanity.check_case(s, c)}
        s.rollback()


def test_gate_holds_when_a_sanity_check_fails(client, world, monkeypatch):
    real = sanity.check_case
    monkeypatch.setattr(sanity, "check_case", lambda s, case, final=True: [sanity.problem("test_rule", "planted inconsistency")] if not final else real(s, case, final=True))
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AWAITING_HUMAN"
    assert "Sanity check failed: planted inconsistency" in case["recommendation"]["gate"]["reasons"]


def test_sanity_cli(client, world, capsys):
    run_case(client, login(client, "accountant"), clean_spec())
    assert sanity.main(["--workspace", world.workspace_id]) == 0
    assert "1 case(s) checked, 0 with problems" in capsys.readouterr().out
    with owner_session() as s:
        c = s.scalars(select(Case)).one()
        c.risk = {**c.risk, "score": 42}
    assert sanity.main([]) == 1
    out = capsys.readouterr().out
    assert "[score_mismatch]" in out and "1 with problems" in out
