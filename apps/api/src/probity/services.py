"""Case lifecycle: status machine, decisions, actions, re-scoring, memory (API.md §3, §7; Guardrails G5, G11)."""

from __future__ import annotations

import base64
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from probity.agents import action, risk_case, transaction, vendor, verification
from probity.agents.common import deactivate_agent_claims, fv, record_claim, vendor_bundle
from probity.config import get_settings
from probity.db.audit import audit
from probity.db.models import (
    iso,
    AgentEvent, Case, CaseMemory, ClaimRow, Decision, Document, Draft, EvidenceRow, HistoricalInvoice, Message,
    RiskScoreRow, User, VendorBankAccount, VendorDomain, Workspace,
)
from probity.db.session import session_scope, tenant
from probity.events import CaseCtx, emit
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.evidence.store import evidence_public
from probity.graph.build import investigate
from probity.guardrails.crypto import decrypt, mask
from probity.guardrails.text import mask_account_numbers
from probity.ingestion.parse import CORRECTABLE_FIELDS, MAX_CORRECTION_LENGTH, sha256, sniff_mime
from probity.ingestion.validators import normalize_invoice_number, parse_date
from probity.policy import get_policy
from probity.tools.base import Budget

ROLES = ["viewer", "accountant", "approver", "owner"]


class Conflict(Exception):
    """Illegal state transition → 409."""


class Forbidden(Exception):
    """Role not permitted → 403."""


class BadRequest(Exception):
    pass


TRANSITIONS: dict[str, set[str]] = {
    "QUEUED": {"EXTRACTING", "FAILED"},
    "EXTRACTING": {"INVESTIGATING", "FAILED"},
    "INVESTIGATING": {"VERIFYING", "FAILED", "EXTRACTING"},
    "VERIFYING": {"SCORING", "FAILED"},
    "SCORING": {"AUTO_CLEARED", "AWAITING_HUMAN", "FAILED"},
    "AWAITING_HUMAN": {"APPROVED", "REJECTED", "AWAITING_VENDOR", "INVESTIGATING", "AWAITING_HUMAN"},
    "AWAITING_VENDOR": {"AWAITING_HUMAN"},
    "AUTO_CLEARED": {"CLOSED"},
    "APPROVED": {"CLOSED"},
    "REJECTED": {"CLOSED"},
    "FAILED": set(),
    "CLOSED": set(),
}


def require_role(user: User, minimum: str) -> None:
    if ROLES.index(user.role) < ROLES.index(minimum):
        raise Forbidden(f"requires role {minimum}")


def transition(case: Case, to: str) -> None:
    if to not in TRANSITIONS.get(case.status, set()):
        raise Conflict(f"illegal transition {case.status} → {to}")
    case.status = to


def get_case(s: Session, workspace_id: str, case_id: str, *, for_update: bool = False) -> Case:
    """for_update: lock the row until commit (Postgres SELECT ... FOR UPDATE) so concurrent state changes on one case
    (approve vs reject, two "investigate further" clicks) run one after the other and the loser sees the new status."""
    if for_update:
        c = s.execute(select(Case).where(Case.id == case_id).with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    else:
        c = s.get(Case, case_id)
    if c is None or c.workspace_id != workspace_id:  # tenant isolation: never leak existence across workspaces
        raise LookupError("case not found")
    return c


class CaseStopped(Exception):
    """The case was ended (watchdog or restart recovery) while this run was still going; the run stops quietly."""


def get_logger_lazy():  # type: ignore[no-untyped-def]
    from probity.logging import get_logger

    return get_logger("pipeline")


def _ctx(workspace_id: str, case_id: str) -> CaseCtx:
    return CaseCtx(workspace_id, case_id, Budget.from_settings())


# ---------------------------------------------------------------- execution

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="case")
_SYNC = {"on": False}


def run_sync(flag: bool = True) -> None:
    """Tests run cases inline instead of on the worker pool."""
    _SYNC["on"] = flag


def _submit(fn, *args) -> None:  # type: ignore[no-untyped-def]
    """Run an investigation: inline (tests), on a Celery worker (production), or on the local thread pool."""
    if _SYNC["on"]:
        fn(*args)
    elif get_settings().task_backend == "celery":
        from probity.worker import run_case

        workspace_id, case_id, depth = args
        run_case.apply_async(args=[workspace_id, case_id, depth], queue="probity")
    else:
        _pool.submit(fn, *args)


def check_followups() -> int:
    """Flag verification emails whose follow-up date passed without a vendor reply (never auto-sends)."""
    from probity.logging import get_logger
    from probity.notify import notify

    now = datetime.now(timezone.utc)
    flagged = 0
    with session_scope() as s:
        workspaces = [w.id for w in s.scalars(select(Workspace))]
    for ws in workspaces:
        with session_scope(ws) as s:
            for d in s.scalars(select(Draft).where(Draft.workspace_id == ws, Draft.status == "sent", Draft.followup_at <= now)):
                case = s.get(Case, d.case_id)
                if case is None or case.status != "AWAITING_VENDOR":
                    continue
                d.status = "followup_due"
                notify(s, ws, "approver", "followup_due", f"No vendor reply on case #{case.number}",
                       f"Verification email to {d.to_email} sent {d.sent_at:%Y-%m-%d} has no reply. Call the known contact or send a reminder.", d.case_id)
                flagged += 1
                emit(ws, d.case_id, "action.sent", agent="action", status="waiting",
                     message=f"No reply from {d.to_email} since {d.sent_at:%Y-%m-%d}; follow-up due — draft a reminder or call the known contact")
    get_logger("followups").info("followups.checked", flagged=flagged)
    return flagged


# ---------------------------------------------------------------- stuck cases

# Statuses a case passes through while the pipeline runs. A case may not sit in one of these forever.
RUNNING_STATUSES = ("QUEUED", "EXTRACTING", "INVESTIGATING", "VERIFYING", "SCORING")
# No progress (case update or timeline event) for this long means the run is gone: a dead worker, a hung call,
# a killed task. Longer than the per-case time budget plus the slowest single AI call.
WATCHDOG_INTERVAL_SECONDS = 60
STOPPED_ON_RESTART = ("The server restarted while this invoice was being investigated, so the investigation stopped. "
                      "Upload the invoice again to start a new investigation.")
STOPPED_STALLED = ("The investigation stopped making progress for 15 minutes and was ended. "
                   "Upload the invoice again to start a new investigation.")


def _last_progress(workspace_id: str, case: Case) -> datetime:
    from probity.db.session import telemetry_scope

    last = case.updated_at or case.created_at
    with telemetry_scope(workspace_id) as t:
        ev = t.scalar(select(func.max(AgentEvent.ts)).where(AgentEvent.case_id == case.id))
    if ev is not None and ev.tzinfo is None:
        ev = ev.replace(tzinfo=timezone.utc)
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return max(t for t in (last, ev) if t is not None)


def recover_stuck_cases(stale_after_seconds: float | None, message: str, code: str) -> int:
    """End cases that are still in a running status but no longer have a run behind them, so none stays
    "investigating" forever. stale_after_seconds=None ends every running case (used at start-up in inline mode,
    where the in-memory work queue died with the old process). Each one is marked FAILED with a plain reason,
    audited, announced in its timeline and notified to the accountants."""
    from probity.logging import get_logger
    from probity.notify import notify

    now = datetime.now(timezone.utc)
    with session_scope() as s:
        workspaces = [w.id for w in s.scalars(select(Workspace))]
    ended: list[tuple[str, str]] = []
    for ws in workspaces:
        with session_scope(ws) as s:
            running = list(s.scalars(select(Case).where(Case.workspace_id == ws, Case.status.in_(RUNNING_STATUSES)).with_for_update(skip_locked=True)))
            for c in running:
                if stale_after_seconds is not None and (now - _last_progress(ws, c)).total_seconds() < stale_after_seconds:
                    continue
                previous = c.status
                c.status = "FAILED"
                c.recommendation = {**(c.recommendation or {}), "failure": {"code": code, "message": message, "previous_status": previous, "at": now.isoformat()}}
                audit(s, ws, "system", "case.stopped", c.id, {"code": code, "previous_status": previous})
                notify(s, ws, "accountant", "case_failed", f"Investigation of case #{c.number} stopped", message, c.id)
                ended.append((ws, c.id))
    for ws, case_id in ended:  # after commit, so the timeline never shows a stop that was rolled back
        emit(ws, case_id, "agent.failed", agent="pipeline", status="failed", message=message, data={"code": code})
    if ended:
        get_logger("watchdog").warning("cases.stopped", code=code, count=len(ended), case_ids=[c for _, c in ended])
    return len(ended)


def watchdog() -> int:
    return recover_stuck_cases(get_settings().case_stall_seconds, STOPPED_STALLED, "stalled")


def recover_after_restart() -> int:
    """Start-up: with the inline backend every running case lost its run when the old process died. With Celery a
    worker may still be on it, so only stalled cases are ended (the watchdog covers the rest)."""
    if get_settings().task_backend == "inline":
        return recover_stuck_cases(None, STOPPED_ON_RESTART, "interrupted")
    return watchdog()


_SCHEDULER = {"started": False}


def start_inline_scheduler(interval_seconds: int = 15 * 60) -> None:
    """TASK_BACKEND=inline: run periodic jobs on a daemon thread inside the API process (Celery beat does this
    when a worker is used): the case watchdog every minute and the follow-up sweep every `interval_seconds`."""
    import threading
    import time as _t

    from probity.logging import get_logger

    if _SCHEDULER["started"]:
        return
    _SCHEDULER["started"] = True
    stop = threading.Event()

    def loop() -> None:
        next_followups = _t.monotonic() + interval_seconds
        while not stop.wait(WATCHDOG_INTERVAL_SECONDS):
            try:
                watchdog()
            except Exception:  # noqa: BLE001 - a failed sweep is retried next interval
                get_logger("watchdog").exception("watchdog.failed")
            if _t.monotonic() >= next_followups:
                next_followups = _t.monotonic() + interval_seconds
                try:
                    check_followups()
                except Exception:  # noqa: BLE001 - a failed sweep is retried next interval
                    get_logger("followups").exception("followups.failed")

    threading.Thread(target=loop, name="probity-scheduler", daemon=True).start()


def _run(workspace_id: str, case_id: str, depth: int) -> None:
    with tenant(workspace_id):
        _run_inner(workspace_id, case_id, depth)


def _run_inner(workspace_id: str, case_id: str, depth: int) -> None:
    ctx = _ctx(workspace_id, case_id)
    started = time.monotonic()
    try:
        investigate(ctx, depth)
    except CaseStopped:
        get_logger_lazy().warning("case.run_abandoned", case_id=case_id, workspace_id=workspace_id)
        return
    except Exception as e:  # noqa: BLE001
        with session_scope() as s:
            c = s.get(Case, case_id)
            if c and c.status == "FAILED":
                return  # already ended (watchdog or restart recovery); don't report it twice
            if c:
                c.status = "FAILED"
                audit(s, workspace_id, "system", "case.failed", case_id, {"error": str(e)[:300]})
                from probity.notify import notify

                notify(s, workspace_id, "accountant", "case_failed", f"Investigation of case #{c.number} failed",
                       f"{str(e)[:300]}. The document may be unreadable — try re-uploading or entering fields manually.", case_id)
        emit(workspace_id, case_id, "agent.failed", agent="pipeline", status="failed", message=f"Investigation failed: {e}")
        return
    with session_scope() as s:
        c = s.get(Case, case_id)
        if c:
            c.duration_ms = (c.duration_ms or 0) + int((time.monotonic() - started) * 1000)
            audit(s, workspace_id, "system", "case.investigated", case_id, {"score": c.risk.get("score"), "tier": c.risk.get("tier"), "status": c.status, "depth": depth})


# ---------------------------------------------------------------- documents & cases

def upload_document(s: Session, user: User, filename: str, data: bytes) -> tuple[Document, str | None]:
    require_role(user, "accountant")
    mime = sniff_mime(data, filename)
    digest = sha256(data)
    existing = s.scalars(select(Document).where(Document.workspace_id == user.workspace_id, Document.sha256 == digest)).first()
    if existing:
        prior = s.scalars(select(Case).where(Case.document_id == existing.id).order_by(Case.created_at.desc())).first()
        return existing, prior.id if prior else existing.id
    from probity import storage
    from probity.ingestion.scan import check_upload, clamav_scan

    # PDFs are rewritten without active content and only the clean copy is stored and served; the hash stays the
    # upload's own so a re-upload of the same file is still recognised. A virus scanner is used only if configured.
    stored, removed = check_upload(data, mime)
    av = clamav_scan(stored)
    ref = storage.put(user.workspace_id, digest, stored, mime)
    doc = Document(workspace_id=user.workspace_id, sha256=digest, filename=filename[:300], mime=mime, size=len(stored), storage_path=ref, uploaded_by=user.id)
    s.add(doc)
    s.flush()
    audit(s, user.workspace_id, user.id, "document.uploaded", doc.id, {"sha256": digest, "filename": filename, "mime": mime, "antivirus": av,
                                                                        "cleaned": removed})
    return doc, None


def clean_corrections(corrections: dict | None) -> dict[str, str]:
    """Only parser misreads of identity/reference fields may be corrected, as short plain strings. Payment routing
    and money (bank account, sender domain, amounts, line items) always come from the document itself."""
    out: dict[str, str] = {}
    for k, v in (corrections or {}).items():
        if k not in CORRECTABLE_FIELDS:
            raise BadRequest(f"'{k}' cannot be corrected; correctable fields: {', '.join(sorted(CORRECTABLE_FIELDS))}")
        if not isinstance(v, str):
            raise BadRequest(f"correction for '{k}' must be text")
        v = v.strip()
        if len(v) > MAX_CORRECTION_LENGTH or any(ord(ch) < 32 for ch in v):
            raise BadRequest(f"correction for '{k}' must be a single line of at most {MAX_CORRECTION_LENGTH} characters")
        if v:
            out[k] = v
    return out


def create_case(s: Session, user: User, document_id: str, corrections: dict | None = None, retry_of: str | None = None) -> Case:
    require_role(user, "accountant")
    doc = s.get(Document, document_id)
    if doc is None or doc.workspace_id != user.workspace_id:
        raise LookupError("document not found")
    corrections = clean_corrections(corrections)
    existing = s.scalars(select(Case).where(Case.workspace_id == user.workspace_id, Case.document_id == doc.id,
                                            Case.status.notin_(("REJECTED", "FAILED")))).first()
    if existing is not None:
        raise Conflict(f"This document already has case #{existing.number}. Open that case instead of starting a new one.")
    n = (s.scalar(select(func.max(Case.number)).where(Case.workspace_id == user.workspace_id)) or 1840) + 1
    case = Case(workspace_id=user.workspace_id, document_id=doc.id, number=n, corrections=corrections,
                recommendation={"retry_of": retry_of} if retry_of else {})
    s.add(case)
    s.flush()
    audit(s, user.workspace_id, user.id, "case.created", case.id, {"document_id": doc.id, "corrections": corrections,
                                                                    **({"retry_of": retry_of} if retry_of else {})})
    s.commit()
    emit(user.workspace_id, case.id, "case.created", status="queued", message=f"Case #{n} created from {doc.filename}")
    _submit(_run, user.workspace_id, case.id, 0)
    return case


def retry_case(s: Session, user: User, case_id: str) -> Case:
    """Start a fresh investigation of a FAILED case's document. The failed case stays FAILED (its timeline and audit
    trail are evidence of what happened) and records which case replaced it."""
    require_role(user, "accountant")
    old = get_case(s, user.workspace_id, case_id, for_update=True)
    if old.status != "FAILED":
        raise Conflict(f"case is {old.status}; only a FAILED case can be retried")
    if (old.recommendation or {}).get("retried_as"):
        raise Conflict(f"already retried as case {old.recommendation['retried_as']}")
    old_id = old.id
    old.recommendation = {**(old.recommendation or {}), "retried_as": "pending"}  # claimed under the row lock: no double retry
    new = create_case(s, user, old.document_id, old.corrections, retry_of=old_id)  # commits, then starts the run
    with session_scope(user.workspace_id) as s2:
        o = s2.get(Case, old_id)
        o.recommendation = {**(o.recommendation or {}), "retried_as": new.id}
        audit(s2, user.workspace_id, user.id, "case.retried", old_id, {"new_case_id": new.id})
    return new


# ---------------------------------------------------------------- decisions

TIER_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
REASON_MIN_ALNUM = 10


def peak_tier(s: Session, case: Case) -> str:
    """The highest tier this case ever reached. Approval rules use it, so lowering the score (an out-of-band
    confirmation) cannot also lower the bar for approving: a once-CRITICAL case still needs two approvers."""
    tiers = [(case.risk or {}).get("tier"), *s.scalars(select(RiskScoreRow.tier).where(RiskScoreRow.case_id == case.id))]
    return max((t for t in tiers if t in TIER_ORDER), key=TIER_ORDER.index, default="LOW")


def meaningful(reason: str | None) -> bool:
    return len(re.findall(r"[^\W_]", reason or "")) >= REASON_MIN_ALNUM


def _oob_confirmers(s: Session, case: Case) -> set[str]:
    from probity.db.models import AuditLog

    return set(s.scalars(select(AuditLog.actor).where(AuditLog.workspace_id == case.workspace_id, AuditLog.entity == case.id,
                                                      AuditLog.action == "verification.out_of_band")))


def decide(s: Session, user: User, case_id: str, decision: str, reason: str) -> Case:
    case = get_case(s, user.workspace_id, case_id, for_update=True)
    if case.status != "AWAITING_HUMAN":
        raise Conflict(f"case is {case.status}, not AWAITING_HUMAN")
    require_role(user, "approver")
    policy = get_policy(s.get(Workspace, user.workspace_id))
    tier = peak_tier(s, case)
    if decision in ("APPROVE", "REJECT") and tier in policy["require_reason_for_approve_tiers"] and not meaningful(reason):
        raise BadRequest(f"{'approving' if decision == 'APPROVE' else 'rejecting'} a case that reached {tier} requires a written reason "
                         f"(at least {REASON_MIN_ALNUM} letters or digits)")
    s.add(Decision(workspace_id=user.workspace_id, case_id=case.id, decision=decision, reason=reason or "", actor_id=user.id))
    s.flush()
    audit(s, user.workspace_id, user.id, f"decision.{decision.lower()}", case.id, {"reason": reason, "score": case.risk.get("score"), "tier": tier})
    msg = f"{decision.replace('_', ' ').title()} by {user.name}"
    if decision == "APPROVE":
        # Approvals count only if given after the latest score: an approval of an earlier version of the case
        # (before a re-run or an out-of-band rescore) is not an approval of what the case says now.
        scored_at = s.scalar(select(func.max(RiskScoreRow.created_at)).where(RiskScoreRow.case_id == case.id))
        q = select(Decision.actor_id).where(Decision.case_id == case.id, Decision.decision == "APPROVE")
        approvers = set(s.scalars(q.where(Decision.created_at >= scored_at) if scored_at else q))
        needed = 2 if risk_case.requires_dual_approval(case, policy, tier=tier) else 1
        # Separation of duties: whoever confirmed the vendor's statements out-of-band (which lowered the score)
        # cannot be the only approver of the payment.
        confirmers = _oob_confirmers(s, case)
        independent = approvers - confirmers
        if len(approvers) < needed or (confirmers and not independent):
            case.recommendation = {**case.recommendation, "approvals": sorted(approvers)}
            s.flush()
            from probity.notify import notify

            why = (f"{len(approvers)} of {needed} approvals (dual approval required)" if len(approvers) < needed
                   else "needs an approver other than the person who confirmed out-of-band")
            notify(s, user.workspace_id, "approver", "approval_needed", f"Another approval needed on case #{case.number}",
                   f"{user.name} approved. This case {why}.", case.id, exclude_user=user.id)
            emit(user.workspace_id, case.id, "decision.recorded", agent="human_gate", status="waiting", message=f"{msg} — {why}")
            return case
        transition(case, "APPROVED")
    elif decision == "REJECT":
        transition(case, "REJECTED")
    elif decision == "REQUEST_VERIFICATION":
        ctx = _ctx(user.workspace_id, case.id)
        d = action.draft_email(s, ctx, case)
        s.add(Draft(workspace_id=user.workspace_id, case_id=case.id, **d))
        msg += " — draft email prepared for approval"
    elif decision == "INVESTIGATE_FURTHER":
        if case.depth >= get_settings().max_depth:
            raise Conflict(f"investigate-further depth limit ({get_settings().max_depth}) reached")
        transition(case, "INVESTIGATING")
        s.commit()
        emit(user.workspace_id, case.id, "decision.recorded", agent="human_gate", status="done", message=msg + f" (depth {case.depth + 1})")
        _submit(_rerun_deeper, user.workspace_id, case.id, case.depth + 1)
        return case
    else:
        raise BadRequest("unknown decision")
    s.flush()
    emit(user.workspace_id, case.id, "decision.recorded", agent="human_gate", status="done", message=msg)
    return case


def _rerun_deeper(workspace_id: str, case_id: str, depth: int) -> None:
    with session_scope(workspace_id) as s:
        ctx = CaseCtx(workspace_id, case_id, Budget.from_settings())
        deactivate_agent_claims(s, ctx, ["orchestrator", "vendor_investigator", "transaction_analyst", "web_research"], f"investigate_further depth {depth}")
    _run(workspace_id, case_id, depth)


# ---------------------------------------------------------------- drafts / email

def update_draft(s: Session, user: User, case_id: str, draft_id: str, subject: str | None, body: str | None) -> Draft:
    require_role(user, "accountant")
    d = s.get(Draft, draft_id)
    if d is None or d.case_id != case_id or d.workspace_id != user.workspace_id:
        raise LookupError("draft not found")
    if d.status != "draft":
        raise Conflict("draft already sent")
    from probity.guardrails.text import language_violations

    if language_violations((subject or "") + " " + (body or "")):
        raise BadRequest("draft contains accusatory language; use neutral wording (anomaly / verification)")
    if subject is not None:
        d.subject = subject
    if body is not None:
        d.body = body
    audit(s, user.workspace_id, user.id, "draft.edited", d.id, {"case_id": case_id})
    return d


def send_draft(s: Session, user: User, case_id: str, draft_id: str, override_unverified_recipient: bool = False,
               override_reason: str | None = None) -> Draft:
    require_role(user, "approver")
    case = get_case(s, user.workspace_id, case_id, for_update=True)
    d = s.get(Draft, draft_id)
    if d is None or d.case_id != case.id:
        raise LookupError("draft not found")
    if d.status != "draft":
        raise Conflict("draft already sent")
    if not d.recipient_verified and not override_unverified_recipient:
        raise BadRequest("recipient comes from the invoice, not the verified vendor master — explicit approver override required")
    if not d.recipient_verified and not meaningful(override_reason):
        raise BadRequest(f"Sending to an address that isn't a verified contact needs a reason (at least {REASON_MIN_ALNUM} letters or digits): "
                         "why this address, and how you know it belongs to the vendor.")
    if case.status != "AWAITING_HUMAN":
        raise Conflict(f"case is {case.status}")
    from probity import mailer

    try:
        provider_id = mailer.send_case_email(case.id, d.to_email, d.subject, d.body)
    except mailer.MailBlocked as e:
        audit(s, user.workspace_id, user.id, "email.blocked", d.id, {"to": mailer.masked(d.to_email), "case_id": case.id, "reason": "not on EMAIL_ALLOWLIST"})
        s.commit()
        raise BadRequest(f"email not sent: {e}") from e
    except mailer.MailError as e:
        raise BadRequest(f"email not sent: {e}") from e
    d.status, d.approved_by, d.sent_at = "sent", user.id, datetime.now(timezone.utc)
    d.followup_at = d.sent_at + timedelta(days=2)
    s.add(Message(workspace_id=user.workspace_id, case_id=case.id, direction="out", from_email=get_settings().email_from, to_email=d.to_email, subject=d.subject, body=d.body))
    transition(case, "AWAITING_VENDOR")
    audit(s, user.workspace_id, user.id, "draft.sent", d.id, {"to": d.to_email, "case_id": case.id, "override": override_unverified_recipient,
                                                                 "override_reason": (override_reason or "").strip()[:500] if not d.recipient_verified else None,
                                                                 "provider_id": provider_id})
    s.flush()
    emit(user.workspace_id, case.id, "action.sent", agent="action", status="done", message=f"Verification email sent to {d.to_email}; follow-up {d.followup_at.date()}")
    return d


def vendor_reply(s: Session, workspace_id: str, actor: str, case_id: str, from_email: str, subject: str, body: str,
                 extra_indicators: list[str] | None = None) -> dict:
    case = get_case(s, workspace_id, case_id, for_update=True)
    if case.status != "AWAITING_VENDOR":
        raise Conflict(f"case is {case.status}, not AWAITING_VENDOR")
    ctx = _ctx(workspace_id, case.id)
    bundle = vendor_bundle(s, workspace_id, case.vendor_id)
    claims, indicators = action.analyze_reply(ctx, case, bundle, from_email, body)
    indicators = [*(extra_indicators or []), *indicators]  # transport facts (DKIM) are stored with the message
    s.add(Message(workspace_id=workspace_id, case_id=case.id, direction="in", from_email=from_email, to_email=get_settings().email_from or "", subject=subject, body=body, indicators=indicators))
    rows = [record_claim(s, ctx, "action", c) for c in claims]
    from probity.evidence.verifier import verify_claims

    verify_claims(s, workspace_id, case.id, rows)  # → all `unverified` (requires_out_of_band)
    transition(case, "AWAITING_HUMAN")
    from probity.notify import notify

    notify(s, workspace_id, "approver", "vendor_replied", f"Vendor replied on case #{case.number}",
           f"From {from_email}. {len(rows)} statement(s) need out-of-band confirmation before the score can change."
           + (f" Warning: {'; '.join(indicators)}." if indicators else ""), case.id)
    audit(s, workspace_id, actor, "vendor.reply_received", case.id, {"from": from_email, "claims": [r.id for r in rows], "indicators": indicators})
    s.flush()
    emit(workspace_id, case.id, "vendor.reply_received", agent="action", status="done",
         message=f"Reply from {from_email}: {len(rows)} statement(s) recorded as unverified — awaiting approver" + (f"; {len(indicators)} indicator(s)" if indicators else ""),
         data={"claim_ids": [r.id for r in rows], "indicators": indicators})
    return {"claim_ids": [r.id for r in rows], "indicators": indicators, "score_changed": False}


OOB_NOTE_MIN_CHARS = 20


def _copies_vendor_reply(s: Session, case: Case, note: str) -> bool:
    """A note pasted from the vendor's own reply is not an out-of-band confirmation (M8)."""
    from rapidfuzz import fuzz

    norm = lambda t: re.sub(r"\s+", " ", (t or "").lower()).strip()  # noqa: E731
    n = norm(note)
    for body in s.scalars(select(Message.body).where(Message.case_id == case.id, Message.direction == "in")):
        b = norm(body)
        if not b:
            continue
        if n in b or b in n or fuzz.ratio(n, b) >= 85 or (len(n) >= 40 and fuzz.partial_ratio(n, b) >= 92):
            return True
    return False


def confirm_out_of_band(s: Session, user: User, case_id: str, claim_ids: list[str], method: str, note: str,
                        known_channel: bool | None = None, confirmed_account_last4: str | None = None) -> dict:
    """Approver-only. The ONLY path by which a vendor's statements can lower the score (G11).

    What gets verified is bound to the invoice, never to the reply: a bank confirmation verifies only the account the
    invoice pays (and the approver re-types its last 4 digits), a domain confirmation only the domain the invoice
    came from. A reply that names a different account or domain cannot be confirmed into the vendor master."""
    require_role(user, "approver")
    case = get_case(s, user.workspace_id, case_id, for_update=True)
    if case.status != "AWAITING_HUMAN":
        raise Conflict(f"case is {case.status}")
    if known_channel is not True:
        raise BadRequest("Confirm that you used a phone number or channel already on file before this invoice "
                         "(not one from the invoice or the vendor's reply).")
    if not note or len(note.strip()) < OOB_NOTE_MIN_CHARS:
        raise BadRequest(f"Describe the out-of-band confirmation in at least {OOB_NOTE_MIN_CHARS} characters: who you contacted, through which channel on file, and what they confirmed.")
    if _copies_vendor_reply(s, case, note):
        raise BadRequest("The note repeats the vendor's reply. Describe the confirmation you made yourself: who you contacted, "
                         "on which number or channel already on file, and what they confirmed.")
    claims = [c for c in s.scalars(select(ClaimRow).where(ClaimRow.id.in_(claim_ids), ClaimRow.case_id == case.id, ClaimRow.agent == "action",
                                                          ClaimRow.active.is_(True)))]
    if not claims:
        raise BadRequest("no vendor-reply claims selected")
    if any(c.status != "unverified" for c in claims):
        raise Conflict("one or more selected statements were already confirmed")
    ex = case.extraction
    bank = ex.get("bank_account") or {}
    inv_dom = (fv(ex, "sender_domain") or "").lower() or None
    for c in claims:  # validate everything before writing anything
        kind, named = (c.data or {}).get("kind"), ((c.data or {}).get("value") or "").lower() or None
        if kind == "bank":
            if not bank.get("hmac"):
                raise BadRequest("this invoice carries no bank account to confirm")
            if named and named[-4:] != bank.get("last4"):
                raise BadRequest(f"The reply names account {mask(named[-4:])}, but this invoice pays {mask(bank.get('last4'))}. "
                                 "Out-of-band confirmation can only verify the account the invoice pays.")
            if (confirmed_account_last4 or "").strip() != bank.get("last4"):
                raise BadRequest(f"Enter the last 4 digits of the account the vendor confirmed; it must be the invoice's account {mask(bank.get('last4'))}.")
        elif kind == "domain":
            if not inv_dom:
                raise BadRequest("this invoice has no sender domain to confirm")
            if named and named != inv_dom:
                raise BadRequest(f"The reply names {named}, but this invoice came from {inv_dom}. "
                                 "Out-of-band confirmation can only verify the domain the invoice came from.")
        else:
            raise BadRequest("statement cannot be confirmed out-of-band")
    today = datetime.now(timezone.utc).date()
    ctx = _ctx(user.workspace_id, case.id)
    for c in claims:
        kind = (c.data or {}).get("kind")
        if kind == "bank" and bank.get("hmac") and case.vendor_id:
            existing = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == case.vendor_id, VendorBankAccount.acct_hmac == bank["hmac"])).first()
            if existing:
                existing.verified, existing.verified_method, existing.verified_by = True, method, user.id
            else:
                s.add(VendorBankAccount(workspace_id=user.workspace_id, vendor_id=case.vendor_id, last4=bank["last4"], acct_hmac=bank["hmac"],
                                        acct_enc=base64.b64decode(bank["enc"]) if bank.get("enc") else None, ifsc=fv(ex, "ifsc"),
                                        verified=True, verified_method=method, verified_by=user.id, first_seen=today, last_seen=today))
        elif kind == "domain" and case.vendor_id:
            dom = inv_dom
            if dom and not s.scalars(select(VendorDomain).where(VendorDomain.vendor_id == case.vendor_id, VendorDomain.domain == dom)).first():
                s.add(VendorDomain(workspace_id=user.workspace_id, vendor_id=case.vendor_id, domain=dom, verified=True, verified_method=method))
        ev = record_claim(s, ctx, "approver", AgentClaim(
            claim=f"Approver {user.name} confirmed out-of-band ({method.replace('_', ' ')}): {c.statement.split(' (unverified')[0]}",
            evidence=[EvidenceIn(source="approver", field=f"{kind}_confirmation", value=method, source_ref=f"user:{user.id}", excerpt=note[:1000], tier=1)],
            evidence_ids=list(c.evidence_ids), confidence=0.97, severity="info", assertion={"op": "info"}, data={"confirms": c.id},
        ))
        ev.status = "verified"
        c.status, c.verifier_notes = "verified", f"confirmed out-of-band by {user.name} via {method}"
        c.confidence = 0.95
    audit(s, user.workspace_id, user.id, "verification.out_of_band", case.id, {"claims": claim_ids, "method": method, "note": note, "known_channel": known_channel,
                                                                               "account": mask(bank.get("last4")) if any((c.data or {}).get("kind") == "bank" for c in claims) else None,
                                                                               "domain": inv_dom if any((c.data or {}).get("kind") == "domain" for c in claims) else None})
    s.commit()
    emit(user.workspace_id, case.id, "verification.confirmed_out_of_band", agent="human_gate", status="done", message=f"{user.name} confirmed {len(claims)} statement(s) via {method.replace('_', ' ')}")
    risk = rescore(user.workspace_id, case.id, reason="out_of_band_confirmation", rerun_checks=True)
    return {"risk": risk}


def rescore(workspace_id: str, case_id: str, reason: str = "manual_rescore", rerun_checks: bool = False) -> dict:
    """Recompute the score. With rerun_checks the deterministic agents re-evaluate signals against current
    master data (e.g. newly verified bank account); without it, the score is recomputed from stored claims."""
    with tenant(workspace_id):
        return _rescore(workspace_id, case_id, reason, rerun_checks)


def _rescore(workspace_id: str, case_id: str, reason: str, rerun_checks: bool) -> dict:
    ctx = _ctx(workspace_id, case_id)
    if rerun_checks:
        with session_scope() as s:
            deactivate_agent_claims(s, ctx, ["vendor_investigator", "transaction_analyst"], reason)
        checks = {**vendor.run(ctx)["checks"], **transaction.run(ctx)["checks"]}
        with session_scope() as s:
            c = get_case(s, workspace_id, case_id)
            c.checks = {**(c.checks or {}), **checks}
        verification.run(ctx)
    risk = risk_case.score_case(ctx, reason)
    risk_case.analyst(ctx)
    with session_scope() as s:
        c = get_case(s, workspace_id, case_id)
        c.recommendation = {**c.recommendation, "gate": {**(c.recommendation.get("gate") or {}), "auto_cleared": False, "reasons": ["human review in progress"]}}
        audit(s, workspace_id, "system", "risk.rescored", case_id, {"reason": reason, "score": risk["score"], "previous": (risk.get("previous") or {}).get("score")})
        from probity import sanity

        sanity.stamp(c, sanity.check_case(s, c))
    return risk


# ---------------------------------------------------------------- close & memory

def close_case(s: Session, user: User, case_id: str, outcome: str, resolution: str) -> Case:
    case = get_case(s, user.workspace_id, case_id, for_update=True)
    if case.status == "AUTO_CLEARED":
        require_role(user, "accountant")
    else:
        require_role(user, "approver")
    if outcome not in ("CONFIRMED_ISSUE", "CLEARED", "INCONCLUSIVE"):
        raise BadRequest("invalid outcome")
    if not meaningful(resolution):
        raise BadRequest(f"describe the resolution (at least {REASON_MIN_ALNUM} letters or digits)")
    if case.status == "REJECTED" and outcome == "CLEARED":
        raise BadRequest("a rejected case cannot be closed as CLEARED")
    paid = case.status in ("APPROVED", "AUTO_CLEARED")  # only paid invoices become part of the vendor's history
    transition(case, "CLOSED")
    case.outcome, case.resolution = outcome, resolution
    peak = s.scalars(select(RiskScoreRow).where(RiskScoreRow.case_id == case.id).order_by(RiskScoreRow.score.desc())).first()
    peak_score = peak.score if peak else case.risk.get("score", 0)
    peak_contrib = peak.contributions if peak else case.risk.get("contributions", [])
    issues = sorted({c["signal"] for c in peak_contrib if c.get("points", 0) > 0})
    bank = (case.extraction.get("bank_account") or {}).get("hmac")
    dom = fv(case.extraction, "sender_domain")
    summary = f"Case #{case.number} ({fv(case.extraction, 'invoice_number')}): peak {peak_score}/100. " + (case.summary or "")
    # F11: only human-confirmed outcomes become memory (auto-cleared cases closed by a human count as CLEARED).
    s.add(CaseMemory(workspace_id=user.workspace_id, vendor_id=case.vendor_id, case_id=case.id, issues=issues, outcome=outcome, resolution=resolution,
                     summary=summary[:2000], peak_score=peak_score, peak_tier=peak.tier if peak else case.risk.get("tier", "LOW"),
                     evidence_ids=[e.id for e in s.scalars(select(EvidenceRow).where(EvidenceRow.case_id == case.id))],
                     bank_hmacs=[bank] if bank else [], domains=[dom] if dom else []))
    # Only INR invoices become baseline: history is compared in INR and Probity never converts.
    if paid and case.vendor_id and outcome == "CLEARED" and fv(case.extraction, "invoice_number") and fv(case.extraction, "currency") == "INR":
        ex = case.extraction
        s.add(HistoricalInvoice(workspace_id=user.workspace_id, vendor_id=case.vendor_id, invoice_number=fv(ex, "invoice_number"),
                                invoice_number_norm=normalize_invoice_number(fv(ex, "invoice_number")), invoice_date=parse_date(fv(ex, "invoice_date")) or datetime.now().date(),
                                total_minor=case.amount_minor or 0, bank_last4=(ex.get("bank_account") or {}).get("last4"), bank_hmac=bank,
                                po_number=fv(ex, "po_number"), line_items=fv(ex, "line_items") or [], case_id=case.id,
                                source="case", entered_by=user.id, approved_by=user.id, approved_at=datetime.now(timezone.utc)))
    audit(s, user.workspace_id, user.id, "case.closed", case.id, {"outcome": outcome, "resolution": resolution})
    s.flush()
    emit(user.workspace_id, case.id, "case.closed", agent="memory", status="done", message=f"Closed as {outcome}; saved to case memory")
    return case


# ---------------------------------------------------------------- serialization

def serialize_case(s: Session, case: Case, user: User, full: bool = True) -> dict[str, Any]:
    doc = s.get(Document, case.document_id)
    vendor_name = None
    if case.vendor_id:
        from probity.db.models import Vendor

        v = s.get(Vendor, case.vendor_id)
        vendor_name = v.name if v else None
    ex = {k: {kk: vv for kk, vv in v.items() if kk not in ("hmac", "enc")} for k, v in (case.extraction or {}).items()}
    out: dict[str, Any] = {
        "id": case.id,
        "number": case.number,
        "status": case.status,
        "vendor_id": case.vendor_id,
        "vendor_name": vendor_name or fv(case.extraction, "vendor_name"),
        "invoice_number": fv(case.extraction, "invoice_number"),
        "amount": {"amount_minor": case.amount_minor, "currency": fv(case.extraction, "currency") or "INR"},
        "risk": case.risk,
        "recommendation": case.recommendation,
        "partial": case.partial,
        "depth": case.depth,
        "outcome": case.outcome,
        "document": {"id": doc.id, "filename": doc.filename, "sha256": doc.sha256} if doc else None,
        "created_at": iso(case.created_at),
        "updated_at": iso(case.updated_at),
        "duration_ms": case.duration_ms,
        "sources_checked": case.sources_checked,
    }
    if not full:
        return out
    claims = list(s.scalars(select(ClaimRow).where(ClaimRow.case_id == case.id).order_by(ClaimRow.created_at)))
    out.update({
        "invoice": ex,
        "validation": case.validation,
        "plan": case.plan,
        "checks": case.checks,
        "summary": case.summary,
        "memory_hits": case.memory_hits,
        "budget": case.budget,
        "claims": [
            {"id": c.id, "agent": c.agent, "statement": mask_account_numbers(c.statement), "signal": c.signal, "status": c.status, "confidence": c.confidence,
             "severity": c.severity, "evidence_ids": c.evidence_ids, "verifier_notes": c.verifier_notes,
             "supporting_quote": mask_account_numbers(c.supporting_quote), "data": c.data, "active": c.active}
            for c in claims
        ],
        "drafts": [
            {"id": d.id, "to_email": d.to_email, "recipient_verified": d.recipient_verified, "subject": d.subject, "body": d.body, "requested_items": d.requested_items,
             "fallback": d.fallback, "status": d.status, "sent_at": iso(d.sent_at), "followup_at": iso(d.followup_at)}
            for d in s.scalars(select(Draft).where(Draft.case_id == case.id))
        ],
        "messages": [
            {"id": m.id, "direction": m.direction, "from": m.from_email, "to": m.to_email, "subject": mask_account_numbers(m.subject),
             "body": mask_account_numbers(m.body), "indicators": m.indicators, "at": iso(m.created_at)}
            for m in s.scalars(select(Message).where(Message.case_id == case.id).order_by(Message.created_at))
        ],
        "decisions": [
            {"decision": d.decision, "reason": d.reason, "actor_id": d.actor_id, "at": iso(d.created_at)}
            for d in s.scalars(select(Decision).where(Decision.case_id == case.id).order_by(Decision.created_at))
        ],
        "score_history": [
            {"score": r.score, "tier": r.tier, "reason": r.reason, "at": iso(r.created_at)}
            for r in s.scalars(select(RiskScoreRow).where(RiskScoreRow.case_id == case.id).order_by(RiskScoreRow.created_at))
        ],
    })
    return out


def case_evidence(s: Session, case: Case) -> list[dict]:
    out = [evidence_public(e) for e in s.scalars(select(EvidenceRow).where(EvidenceRow.case_id == case.id).order_by(EvidenceRow.retrieved_at))]
    for e in out:  # excerpts quote documents and replies verbatim (G8)
        e["excerpt"] = mask_account_numbers(e.get("excerpt"))
    return out


def reveal_account(s: Session, user: User, case: Case) -> str | None:
    """Full account number only for approver+ (G8); everyone else sees last 4. Audited."""
    require_role(user, "approver")
    enc = (case.extraction.get("bank_account") or {}).get("enc")
    audit(s, user.workspace_id, user.id, "sensitive.viewed", case.id, {"field": "bank_account"})
    return decrypt(base64.b64decode(enc)) if enc else None


def events_after(workspace_id: str, case_id: str, after: int) -> list[AgentEvent]:
    from probity.db.session import telemetry_scope

    with telemetry_scope(workspace_id) as s:
        return list(s.scalars(select(AgentEvent).where(AgentEvent.workspace_id == workspace_id, AgentEvent.case_id == case_id, AgentEvent.id > after).order_by(AgentEvent.id)))
