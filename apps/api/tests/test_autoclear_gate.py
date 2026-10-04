"""Auto-clear gate (Guardrails G5/G6): auto-clear needs positive evidence that the invoice matches a known vendor's
normal pattern. Missing evidence (unknown vendor, a required check that could not be verified, another vendor's PO, an
unreliable total) holds the case, and the gate says which check and why."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from conftest import login, owner_session
from factories import VENDOR_B, clean_spec
from helpers import contributions, gate, run_case
from sqlalchemy import delete, select

from probity.agents.risk_case import requires_dual_approval, total_problem
from probity.db.models import AgentEvent, HistoricalInvoice, VendorBankAccount, VendorDomain


def test_unknown_vendor_never_auto_clears(client, world):
    """An outsider's invoice quoting a real PO number and paying a never-seen account must reach a human."""
    spec = replace(clean_spec(), vendor_name="Totally Unknown Traders", gstin="", email="billing@unknown-traders.test",
                   account_number="99887766554433")
    case = run_case(client, login(client, "accountant"), spec)
    assert case["status"] == "AWAITING_HUMAN", gate(case)
    assert "vendor not matched to the vendor master" in gate(case)["reasons"]
    assert case["checks"]["vendor_identity"]["status"] == "could_not_verify"


def test_po_from_another_vendor_is_flagged(client, world):
    """Vendor B's otherwise clean invoice quoting vendor A's PO is not a PO match."""
    case = run_case(client, login(client, "accountant"), replace(clean_spec(), po_number="PO-7710"))
    assert case["status"] == "AWAITING_HUMAN", gate(case)
    assert any("raised for a different vendor" in c["statement"] for c in case["claims"]), [c["statement"] for c in case["claims"]]


def test_vendor_without_bank_history_never_auto_clears(client, world):
    """With nothing to compare the invoice's bank account against, the bank check could not be verified, so it cannot clear."""
    with owner_session() as s:
        s.execute(delete(VendorBankAccount).where(VendorBankAccount.vendor_id == world.vendors["B"].id))
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AWAITING_HUMAN", gate(case)
    assert case["checks"]["bank_account_verification"]["status"] == "could_not_verify"
    assert "Could not verify: bank account verification — no bank history for vendor" in gate(case)["reasons"], gate(case)
    assert {"check": "bank_account_verification", "status": "could_not_verify", "reason": "no bank history for vendor"} in gate(case)["could_not_verify"]


def test_unverifiable_domain_holds_and_is_shown_in_timeline(client, world, fake_lookups):
    """The vendor's domain is no longer verified and RDAP is unreachable: hold, with the reason in the timeline."""
    with owner_session() as s:
        for d in s.scalars(select(VendorDomain).where(VendorDomain.vendor_id == world.vendors["B"].id)):
            d.verified = False
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AWAITING_HUMAN"
    assert case["checks"]["domain_verification"]["status"] == "could_not_verify"
    assert any(r.startswith("Could not verify: domain verification — RDAP unreachable") for r in gate(case)["reasons"]), gate(case)
    assert fake_lookups.rdap_calls == [VENDOR_B.domain]
    with owner_session() as s:
        events = list(s.scalars(select(AgentEvent).where(AgentEvent.case_id == case["id"], AgentEvent.type == "check.could_not_verify")))
    assert any(e.data["check"] == "domain_verification" and "RDAP unreachable" in e.message for e in events)


def test_insufficient_price_history_holds_without_adding_points(client, world):
    with owner_session() as s:
        s.execute(delete(HistoricalInvoice).where(HistoricalInvoice.vendor_id == world.vendors["B"].id))
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AWAITING_HUMAN"
    assert case["checks"]["price_anomaly"]["status"] == "could_not_verify"
    # The price check adds nothing; the only points are the engine's own "no prior history" signal (a fact from our records).
    assert set(contributions(case)) <= {"no_history"}


def test_clean_known_vendor_still_auto_clears(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AUTO_CLEARED", gate(case)


@pytest.mark.parametrize("field,expect", [
    (None, "could not be read"),
    ({"value": 0, "confidence": 0.99}, "zero or negative"),
    ({"value": 100, "confidence": 0.4}, "low confidence"),
    ({"value": 100, "confidence": 0.4, "via": "human_correction"}, None),
    ({"value": 100, "confidence": 0.95}, None),
])
def test_total_problems_block_auto_clear_and_need_dual_approval(field, expect):
    case = SimpleNamespace(extraction={"total": field} if field else {}, risk={"tier": "LOW"}, amount_minor=100)
    problem = total_problem(case)
    assert (problem is None) if expect is None else (expect in problem)
    assert requires_dual_approval(case, {"dual_approval_amount_minor": 10**12}) is (expect is not None)
