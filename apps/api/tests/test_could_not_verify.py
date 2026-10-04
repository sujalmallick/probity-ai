"""Never fabricate: when a tool or the AI fails, the check says "could not verify" (in the result and the timeline) and
adds no points. "No adverse public findings" only appears when a search actually ran."""

import pathlib
import re

from conftest import login, owner_session
from factories import NEW_DOMAIN, bank_change_spec
from helpers import run_case
from sqlalchemy import select

from probity.db.models import AgentEvent
from probity.tools import lookups


def _events(case_id: str, type_: str) -> list[AgentEvent]:
    with owner_session() as s:
        return list(s.scalars(select(AgentEvent).where(AgentEvent.case_id == case_id, AgentEvent.type == type_)))


def test_search_not_configured_is_could_not_verify(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    chk = case["checks"]["external_reputation"]
    assert chk["status"] == "could_not_verify" and "TAVILY_API_KEY" in chk["reason"]
    assert not any("No adverse" in c["statement"] for c in case["claims"])
    assert any(e.data.get("check") == "external_reputation" for e in _events(case["id"], "check.could_not_verify"))


def test_search_failing_is_could_not_verify(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "error"
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["checks"]["external_reputation"]["status"] == "could_not_verify"
    assert not any("No adverse" in c["statement"] for c in case["claims"])
    assert len(_events(case["id"], "check.could_not_verify")) >= 2  # each failed search is visible


def test_no_adverse_findings_only_after_a_real_search(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    fake_lookups.search["complaints"] = [lookups.SearchHit("https://directory.example/alpha", "Alpha listing", "Alpha Components listing", 3)]
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["checks"]["external_reputation"]["status"] == "passed"
    claim = next(c for c in case["claims"] if c["statement"].startswith("No adverse public findings"))
    assert "searches completed" in claim["statement"]
    # the page itself could not be fetched: said in the timeline, the snippet was used instead
    assert any("Could not read https://directory.example/alpha" in e.message for e in _events(case["id"], "agent.progress"))


def test_rdap_not_found_is_could_not_verify_and_adds_no_points(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = "not_found"
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["checks"]["domain_verification"]["status"] == "could_not_verify"
    assert "new_domain" not in {c["signal"] for c in case["risk"]["contributions"] if c["points"]}


def test_ai_answers_are_used_when_available(client, world, fake_lookups, llm):
    fake_lookups.domains[NEW_DOMAIN] = 21
    llm.answer("CaseSummary", {"summary": "Three verified anomalies need a person to look at them.", "top_findings": ["bank account differs"],
                               "recommendation": "APPROVE", "suggested_next_checks": [], "unconfirmed": []})
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["recommendation"]["summary_source"] == "ai"
    assert case["summary"].startswith("Three verified anomalies")
    assert case["recommendation"]["action"] == "HOLD_PAYMENT"  # the AI cannot change what the engine recommends
    assert "CaseSummary" in llm.calls


def test_ai_failure_is_shown_in_the_timeline(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    msgs = [e.message for e in _events(case["id"], "agent.progress")]
    assert any("AI summary unavailable" in m for m in msgs)
    assert any("AI could not write search queries" in m for m in msgs)


def test_app_code_never_imports_tests():
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "probity"
    pattern = re.compile(r"^\s*(from|import)\s+(tests|factories|conftest|helpers)\b", re.M)
    offenders = [str(p) for p in src.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_no_mode_switches_in_app_code():
    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "probity"
    banned = re.compile(r"\b(LLM_MODE|TOOLS_MODE|AUTH_MODE|EMAIL_BACKEND|DEMO_FEATURES|AGENT_DELAY_MS|llm_mode|tools_mode|auth_mode)\b|\bmock=")
    offenders = [str(p) for p in src.rglob("*.py") if banned.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_rule_based_fallbacks_are_labelled_next_to_their_results(client, world, fake_lookups):
    """Every result produced by rules because the AI failed carries the same visible label in the case JSON."""
    from factories import VENDOR_A
    from helpers import legit_reply_body, record_reply, send_verification

    from probity.events import FALLBACK_LABEL

    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    fb = case["checks"]["external_reputation"]["fallback"]  # search queries were written by rules
    assert fb["kind"] == "rule_based" and fb["label"] == FALLBACK_LABEL == "rule-based fallback, AI unavailable" and fb["reason"]
    assert case["recommendation"]["summary_fallback"]["label"] == FALLBACK_LABEL  # engine summary, AI unavailable
    send_verification(client, case, appr)
    reply = record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case))
    case = client.get(f"/api/v1/cases/{case['id']}", headers=acc).json()
    assert case["drafts"][0]["fallback"]["label"] == FALLBACK_LABEL  # standard template, AI unavailable
    stmts = [c for c in case["claims"] if c["id"] in reply["claim_ids"]]
    assert stmts and all(c["data"]["fallback"]["label"] == FALLBACK_LABEL for c in stmts)


def test_ai_results_carry_no_fallback_label(client, world, fake_lookups, llm):
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    llm.answer("Queries", {"queries": ['"Alpha Components" complaints']})
    llm.answer("CaseSummary", {"summary": "Three verified anomalies.", "recommendation": "HOLD_PAYMENT"})
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert "fallback" not in case["checks"]["external_reputation"]
    assert case["recommendation"]["summary_fallback"] is None


def test_narrative_fallback_is_labelled():
    from probity.risk import narrate

    out = narrate.narrate({"explanation": {"text": "Score 0.2 (LOW).", "contributions": [], "top_drivers": [], "unknown": []}, "tier": "LOW",
                           "score": 0.2, "signals": []})
    assert out["source"] == "template" and out["fallback"]["label"] == "rule-based fallback, AI unavailable"
