"""Regression tests for the five Critical findings in docs/FAILURE_AUDIT.md (Phase 1 failure audit)."""

from dataclasses import replace

from datetime import datetime, timedelta, timezone

from conftest import login, owner_session
from factories import NEW_DOMAIN, bank_change_spec, clean_spec
from helpers import API, contributions, gate, run_case, upload

from sqlalchemy import select, update

from probity import services
from probity.config import get_settings
from probity.db.models import AgentEvent, AuditLog, Case, Notification
from probity.tools import lookups

# ---------------------------------------------------------------- C1 duplicate detection sees in-flight cases


def test_resubmitted_invoice_after_auto_clear_is_a_duplicate(client, world):
    """The first copy auto-cleared but was not closed, so it never reached paid history. A second copy (different
    file, same invoice) must still be caught instead of auto-clearing a second time."""
    acc = login(client, "accountant")
    first = run_case(client, acc, clean_spec())
    assert first["status"] == "AUTO_CLEARED", gate(first)
    second = run_case(client, acc, replace(clean_spec(), footer="Resent copy of the same invoice"))
    assert second["status"] == "AWAITING_HUMAN", gate(second)
    assert second["checks"]["duplicate_detection"]["status"] == "fired"
    assert "duplicate_invoice" in contributions(second)
    claim = next(c for c in second["claims"] if c["signal"] == "duplicate_invoice")
    assert f"case #{first['number']}" in claim["statement"], claim["statement"]


def test_duplicate_of_an_open_case_is_caught(client, world, fake_lookups):
    """An invoice still waiting for a decision counts too."""
    acc = login(client, "accountant")
    first = run_case(client, acc, bank_change_spec())
    assert first["status"] == "AWAITING_HUMAN"
    second = run_case(client, acc, replace(bank_change_spec(), footer="Second copy"))
    assert second["checks"]["duplicate_detection"]["status"] == "fired"
    assert "duplicate_invoice" in contributions(second)


def test_same_document_cannot_start_a_second_case(client, world):
    acc = login(client, "accountant")
    doc = upload(client, acc, clean_spec())
    assert client.post(f"{API}/cases", headers=acc, json={"document_id": doc}).status_code == 201
    r = client.post(f"{API}/cases", headers=acc, json={"document_id": doc})
    assert r.status_code == 409
    assert "already has case" in r.json()["error"]["message"]


# ---------------------------------------------------------------- C4 the gate walks the required list


def test_required_check_with_no_recorded_result_holds(client, world, monkeypatch):
    """A step that silently skips a required check (here: the PO comparison never writes a result) must not let the
    invoice auto-clear. Before the fix the gate only looked at checks that were recorded."""
    from probity.agents import transaction

    real_run = transaction.run

    def run_without_po_check(ctx):  # type: ignore[no-untyped-def]
        out = real_run(ctx)
        out["checks"].pop("quantity_po_match", None)
        return out

    monkeypatch.setattr(transaction, "run", run_without_po_check)
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert "quantity_po_match" not in case["checks"]
    assert case["status"] == "AWAITING_HUMAN", gate(case)
    assert "Could not verify: quantity po match — the check did not run" in gate(case)["reasons"], gate(case)


def test_missing_bank_details_hold_the_invoice(client, world):
    """User decision: an invoice without bank details is held, because the bank comparison has nothing to compare."""
    case = run_case(client, login(client, "accountant"), replace(clean_spec(), account_number=""))
    assert case["status"] == "AWAITING_HUMAN", gate(case)
    assert any(r.startswith("Could not verify: bank account verification") for r in gate(case)["reasons"]), gate(case)


def test_required_and_optional_lists_follow_the_decision():
    from probity.agents.risk_case import OPTIONAL_CHECKS, REQUIRED_CHECKS

    assert set(REQUIRED_CHECKS) == {"invoice_validation", "vendor_identity", "bank_account_verification", "price_anomaly",
                                    "quantity_po_match", "duplicate_detection"}
    assert set(OPTIONAL_CHECKS) == {"domain_verification", "external_reputation"}


# ---------------------------------------------------------------- C2 web research reads before it says "clean"


def test_adverse_page_found_by_any_query_is_reported(client, world, fake_lookups, llm):
    """AI-written queries rarely contain the word "complaints". The page must be scanned anyway; before the fix it was
    never read and the check reported "No adverse public findings" and passed."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    llm.answer("Queries", {"queries": ['"Alpha Components" reviews']})
    url = "https://forum.example/alpha-components"
    fake_lookups.search["Alpha"] = [lookups.SearchHit(url, "Alpha Components reviews", "Alpha Components reviews", 3)]
    fake_lookups.pages[url] = "Alpha Components Pvt Ltd: several buyers report non-delivery after paying in advance."
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["checks"]["external_reputation"]["status"] == "fired", case["checks"]["external_reputation"]
    assert any(c["statement"].startswith("A third-party source reports complaints") for c in case["claims"])
    assert not any(c["statement"].startswith("No adverse public findings") for c in case["claims"])


def test_searches_with_nothing_readable_are_not_called_clean(client, world, fake_lookups, llm):
    """Searches that ran but returned nothing about the vendor are "could not verify", never "no adverse findings"."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    llm.answer("Queries", {"queries": ['"Alpha Components" reviews']})
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    chk = case["checks"]["external_reputation"]
    assert chk["status"] == "could_not_verify", chk
    assert "no public pages about the vendor could be read" in chk["reason"]
    assert not any(c["statement"].startswith("No adverse public findings") for c in case["claims"])


def test_partial_search_failure_is_incomplete_not_passed(client, world, fake_lookups, monkeypatch):
    """One search worked and read a clean page, another failed: the check says how much ran instead of "passed"."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    fake_lookups.search["complaints"] = [lookups.SearchHit("https://directory.example/alpha", "Alpha listing", "Alpha Components listing", 3)]
    real = fake_lookups.web_search

    def flaky(query, budget):  # type: ignore[no-untyped-def]
        if "address" in query:
            return lookups.SearchOutcome("error", query, reason="search service unreachable (test)")
        return real(query, budget)

    monkeypatch.setattr(lookups, "web_search", flaky)
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    chk = case["checks"]["external_reputation"]
    assert chk["status"] == "could_not_verify", chk
    assert chk["reason"].startswith("only ") and "no adverse findings in what was read" in chk["reason"]


# ---------------------------------------------------------------- C3 no case stays "investigating" forever


def _running_case(client, world, status: str = "INVESTIGATING", idle_minutes: int = 0) -> str:
    """A case left in a running status with no run behind it (as after a crash), optionally idle for a while."""
    doc = upload(client, login(client, "accountant"), clean_spec())
    with owner_session() as s:
        n = (s.scalar(select(Case.number).order_by(Case.number.desc())) or 1840) + 1
        c = Case(workspace_id=world.workspace_id, document_id=doc, number=n, status=status)
        s.add(c)
        s.commit()
        if idle_minutes:
            s.execute(update(Case).where(Case.id == c.id).values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)))
            s.commit()
        return c.id


def _case(case_id: str) -> Case:
    with owner_session() as s:
        return s.get(Case, case_id)


def test_restart_recovery_ends_cases_left_running(client, world):
    """Inline mode keeps its work queue in memory, so after a restart every running case has no run behind it."""
    stuck = [_running_case(client, world, st) for st in ("QUEUED", "EXTRACTING", "INVESTIGATING", "SCORING")]
    assert services.recover_after_restart() == 4
    for case_id in stuck:
        c = _case(case_id)
        assert c.status == "FAILED"
        assert c.recommendation["failure"]["code"] == "interrupted"
        assert "server restarted" in c.recommendation["failure"]["message"]
    with owner_session() as s:
        assert s.scalars(select(AuditLog).where(AuditLog.entity == stuck[0], AuditLog.action == "case.stopped")).first()
        assert s.scalars(select(AgentEvent).where(AgentEvent.case_id == stuck[0], AgentEvent.type == "agent.failed")).first()
        assert s.scalars(select(Notification).where(Notification.case_id == stuck[0], Notification.kind == "case_failed")).first()


def test_watchdog_ends_only_stalled_cases(client, world):
    active = _running_case(client, world, idle_minutes=2)
    stalled = _running_case(client, world, idle_minutes=get_settings().case_stall_seconds // 60 + 5)
    assert services.watchdog() == 1
    assert _case(active).status == "INVESTIGATING"
    c = _case(stalled)
    assert c.status == "FAILED" and c.recommendation["failure"]["code"] == "stalled"


def test_recent_timeline_activity_counts_as_progress(client, world):
    """A case whose row hasn't changed but whose agents are still emitting events is alive."""
    case_id = _running_case(client, world, idle_minutes=get_settings().case_stall_seconds // 60 + 5)
    services.emit(world.workspace_id, case_id, "agent.progress", agent="web_research", status="running", message="Searching")
    assert services.watchdog() == 0
    assert _case(case_id).status == "INVESTIGATING"


def test_ended_case_is_not_revived_by_a_late_run(client, world):
    """If the run that was declared dead wakes up, it stops quietly instead of auto-clearing the case."""
    case_id = _running_case(client, world, "QUEUED")
    services.recover_after_restart()
    services._run(world.workspace_id, case_id, 0)
    c = _case(case_id)
    assert c.status == "FAILED" and c.recommendation["failure"]["code"] == "interrupted"
    with owner_session() as s:
        assert not s.scalars(select(AuditLog).where(AuditLog.entity == case_id, AuditLog.action == "case.failed")).first()


def test_a_failed_case_can_be_started_again_from_the_same_document(client, world):
    """The way out of a stopped case today: upload the invoice again (same file) and start a new case."""
    case_id = _running_case(client, world)
    services.recover_after_restart()
    acc = login(client, "accountant")
    doc = _case(case_id).document_id  # re-uploading the same file returns this document
    assert client.post(f"{API}/cases", headers=acc, json={"document_id": doc}).status_code == 201


# ---------------------------------------------------------------- C5 audit log: no process-wide deadlock


def test_two_writers_in_one_workspace_do_not_deadlock(world):
    """Request A writes an audit entry (holding the workspace's advisory lock until commit), request B starts writing
    and waits, then A writes a second entry. Before the fix B held the process lock while waiting, A waited for that
    process lock, and both hung forever (Postgres can't see a wait inside Python)."""
    import threading
    import time

    from probity.db.audit import audit
    from probity.db.session import session_scope

    ws = world.workspace_id
    a_holds_lock, b_waiting = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def request_a() -> None:
        try:
            with session_scope(ws) as s:
                audit(s, ws, "system", "test.a1", "deadlock-test")
                a_holds_lock.set()
                b_waiting.wait(5)
                time.sleep(0.5)  # let B block on the advisory lock first
                audit(s, ws, "system", "test.a2", "deadlock-test")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def request_b() -> None:
        try:
            a_holds_lock.wait(5)
            b_waiting.set()
            with session_scope(ws) as s:
                audit(s, ws, "system", "test.b", "deadlock-test")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=request_a, daemon=True), threading.Thread(target=request_b, daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "audit writers deadlocked"
    assert not errors, errors
    with owner_session() as s:
        actions = [r.action for r in s.scalars(select(AuditLog).where(AuditLog.entity == "deadlock-test").order_by(AuditLog.id))]
    assert actions == ["test.a1", "test.a2", "test.b"]


def test_audit_write_blocked_by_a_stuck_transaction_fails_instead_of_hanging(world, monkeypatch):
    import pytest
    from sqlalchemy import text

    from probity.db import audit as audit_mod
    from probity.db.session import session_scope

    monkeypatch.setattr(audit_mod, "AUDIT_LOCK_TIMEOUT", "500ms")
    ws = world.workspace_id
    with owner_session() as holder:
        holder.execute(text("SELECT pg_advisory_xact_lock(hashtext(:ws))"), {"ws": ws})  # a transaction that never finishes
        with pytest.raises(Exception, match="lock timeout"):
            with session_scope(ws) as s:
                audit_mod.audit(s, ws, "system", "test.blocked", "deadlock-test")
        holder.rollback()
