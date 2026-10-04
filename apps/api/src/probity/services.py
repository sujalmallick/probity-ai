"""Case lifecycle: status machine, decisions, actions, re-scoring, memory (API.md §3, §7; Guardrails G5, G11)."""

from __future__ import annotations

import base64
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
from probity.guardrails.crypto import decrypt
from probity.ingestion.parse import sha256, sniff_mime
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


def get_case(s: Session, workspace_id: str, case_id: str) -> Case:
    c = s.get(Case, case_id)
    if c is None or c.workspace_id != workspace_id:  # tenant isolation: never leak existence across workspaces
        raise LookupError("case not found")
    return c


def _ctx(workspace_id: str, case_id: str) -> CaseCtx:
    with session_scope() as s:
        ws = s.get(Workspace, workspace_id)
        delay = int((ws.policy or {}).get("demo_agent_delay_ms", get_settings().agent_delay_ms)) if ws else 0
    return CaseCtx(workspace_id, case_id, Budget.from_settings(), delay_ms=delay)


# ---------------------------------------------------------------- execution

_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="case")
_SYNC = {"on": False}


def run_sync(flag: bool = True) -> None:
    """Tests/benchmark run cases inline instead of on the worker pool."""
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


def _run(workspace_id: str, case_id: str, depth: int) -> None:
    with tenant(workspace_id):
        _run_inner(workspace_id, case_id, depth)


def _run_inner(workspace_id: str, case_id: str, depth: int) -> None:
    ctx = _ctx(workspace_id, case_id)
    started = time.monotonic()
    try:
        investigate(ctx, depth)
    except Exception as e:  # noqa: BLE001
        with session_scope() as s:
            c = s.get(Case, case_id)
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
    from probity.ingestion.scan import clamav_scan, reject_active_content

    reject_active_content(data, mime)
    av = clamav_scan(data)
    ref = storage.put(user.workspace_id, digest, data, mime)
    doc = Document(workspace_id=user.workspace_id, sha256=digest, filename=filename[:300], mime=mime, size=len(data), storage_path=ref, uploaded_by=user.id)
    s.add(doc)
    s.flush()
    audit(s, user.workspace_id, user.id, "document.uploaded", doc.id, {"sha256": digest, "filename": filename, "mime": mime, "antivirus": av})
    return doc, None


def create_case(s: Session, user: User, document_id: str, corrections: dict | None = None) -> Case:
    require_role(user, "accountant")
    doc = s.get(Document, document_id)
    if doc is None or doc.workspace_id != user.workspace_id:
        raise LookupError("document not found")
    n = (s.scalar(select(func.max(Case.number)).where(Case.workspace_id == user.workspace_id)) or 1840) + 1
    case = Case(workspace_id=user.workspace_id, document_id=doc.id, number=n, corrections=corrections or {})
    s.add(case)
    s.flush()
    audit(s, user.workspace_id, user.id, "case.created", case.id, {"document_id": doc.id})
    s.commit()
    emit(user.workspace_id, case.id, "case.created", status="queued", message=f"Case #{n} created from {doc.filename}")
    _submit(_run, user.workspace_id, case.id, 0)
    return case


# ---------------------------------------------------------------- decisions

def decide(s: Session, user: User, case_id: str, decision: str, reason: str) -> Case:
    case = get_case(s, user.workspace_id, case_id)
    if case.status != "AWAITING_HUMAN":
        raise Conflict(f"case is {case.status}, not AWAITING_HUMAN")
    require_role(user, "approver")
    policy = get_policy(s.get(Workspace, user.workspace_id))
    tier = (case.risk or {}).get("tier")
    if decision == "APPROVE" and tier in policy["require_reason_for_approve_tiers"] and len((reason or "").strip()) < 5:
        raise BadRequest(f"approving a {tier} case requires a written reason")
    s.add(Decision(workspace_id=user.workspace_id, case_id=case.id, decision=decision, reason=reason or "", actor_id=user.id))
    s.flush()
    audit(s, user.workspace_id, user.id, f"decision.{decision.lower()}", case.id, {"reason": reason, "score": case.risk.get("score"), "tier": tier})
    msg = f"{decision.replace('_', ' ').title()} by {user.name}"
    if decision == "APPROVE":
        if risk_case.requires_dual_approval(case, policy):
            approvers = set(s.scalars(select(Decision.actor_id).where(Decision.case_id == case.id, Decision.decision == "APPROVE")))
            if len(approvers) < 2:
                case.recommendation = {**case.recommendation, "approvals": sorted(approvers)}
                s.flush()
                from probity.notify import notify

                notify(s, user.workspace_id, "approver", "approval_needed", f"Second approval needed on case #{case.number}",
                       f"{user.name} approved. This case needs a second approver.", case.id, exclude_user=user.id)
                emit(user.workspace_id, case.id, "decision.recorded", agent="human_gate", status="waiting", message=f"{msg} — 1 of 2 approvals (dual approval required)")
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


def send_draft(s: Session, user: User, case_id: str, draft_id: str, override_unverified_recipient: bool = False) -> Draft:
    require_role(user, "approver")
    case = get_case(s, user.workspace_id, case_id)
    d = s.get(Draft, draft_id)
    if d is None or d.case_id != case.id:
        raise LookupError("draft not found")
    if d.status != "draft":
        raise Conflict("draft already sent")
    if not d.recipient_verified and not override_unverified_recipient:
        raise BadRequest("recipient comes from the invoice, not the verified vendor master — explicit approver override required")
    if case.status != "AWAITING_HUMAN":
        raise Conflict(f"case is {case.status}")
    from probity import mailer

    try:
        provider_id = mailer.send_case_email(case.id, d.to_email, d.subject, d.body)
    except mailer.MailError as e:
        raise BadRequest(f"email not sent: {e}") from e
    d.status, d.approved_by, d.sent_at = "sent", user.id, datetime.now(timezone.utc)
    d.followup_at = d.sent_at + timedelta(days=2)
    s.add(Message(workspace_id=user.workspace_id, case_id=case.id, direction="out", from_email=get_settings().email_from, to_email=d.to_email, subject=d.subject, body=d.body))
    transition(case, "AWAITING_VENDOR")
    audit(s, user.workspace_id, user.id, "draft.sent", d.id, {"to": d.to_email, "case_id": case.id, "override": override_unverified_recipient, "provider_id": provider_id})
    s.flush()
    emit(user.workspace_id, case.id, "action.sent", agent="action", status="done", message=f"Verification email sent to {d.to_email}; follow-up {d.followup_at.date()}")
    return d


def vendor_reply(s: Session, workspace_id: str, actor: str, case_id: str, from_email: str, subject: str, body: str) -> dict:
    case = get_case(s, workspace_id, case_id)
    if case.status != "AWAITING_VENDOR":
        raise Conflict(f"case is {case.status}, not AWAITING_VENDOR")
    ctx = _ctx(workspace_id, case.id)
    bundle = vendor_bundle(s, workspace_id, case.vendor_id)
    claims, indicators = action.analyze_reply(ctx, case, bundle, from_email, body)
    s.add(Message(workspace_id=workspace_id, case_id=case.id, direction="in", from_email=from_email, to_email="ap@probity-demo.in", subject=subject, body=body, indicators=indicators))
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


def confirm_out_of_band(s: Session, user: User, case_id: str, claim_ids: list[str], method: str, note: str) -> dict:
    """Approver-only. The ONLY path by which a vendor's statements can lower the score (G11)."""
    require_role(user, "approver")
    case = get_case(s, user.workspace_id, case_id)
    if case.status != "AWAITING_HUMAN":
        raise Conflict(f"case is {case.status}")
    if not note or len(note.strip()) < 5:
        raise BadRequest("describe the out-of-band confirmation (who was called, on which known number)")
    claims = [c for c in s.scalars(select(ClaimRow).where(ClaimRow.id.in_(claim_ids), ClaimRow.case_id == case.id, ClaimRow.agent == "action"))]
    if not claims:
        raise BadRequest("no vendor-reply claims selected")
    ex = case.extraction
    bank = ex.get("bank_account") or {}
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
            dom = (c.data or {}).get("value") or fv(ex, "sender_domain")
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
    audit(s, user.workspace_id, user.id, "verification.out_of_band", case.id, {"claims": claim_ids, "method": method, "note": note})
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
    ctx.delay_ms = min(ctx.delay_ms, 300)
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
    return risk


# ---------------------------------------------------------------- close & memory

def close_case(s: Session, user: User, case_id: str, outcome: str, resolution: str) -> Case:
    case = get_case(s, user.workspace_id, case_id)
    if case.status == "AUTO_CLEARED":
        require_role(user, "accountant")
    else:
        require_role(user, "approver")
    if outcome not in ("CONFIRMED_ISSUE", "CLEARED", "INCONCLUSIVE"):
        raise BadRequest("invalid outcome")
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
    if case.vendor_id and outcome in ("CLEARED",) and fv(case.extraction, "invoice_number"):
        ex = case.extraction
        s.add(HistoricalInvoice(workspace_id=user.workspace_id, vendor_id=case.vendor_id, invoice_number=fv(ex, "invoice_number"),
                                invoice_number_norm=normalize_invoice_number(fv(ex, "invoice_number")), invoice_date=parse_date(fv(ex, "invoice_date")) or datetime.now().date(),
                                total_minor=case.amount_minor or 0, bank_last4=(ex.get("bank_account") or {}).get("last4"), bank_hmac=bank,
                                po_number=fv(ex, "po_number"), line_items=fv(ex, "line_items") or [], case_id=case.id))
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
            {"id": c.id, "agent": c.agent, "statement": c.statement, "signal": c.signal, "status": c.status, "confidence": c.confidence, "severity": c.severity,
             "evidence_ids": c.evidence_ids, "verifier_notes": c.verifier_notes, "supporting_quote": c.supporting_quote, "data": c.data, "active": c.active}
            for c in claims
        ],
        "drafts": [
            {"id": d.id, "to_email": d.to_email, "recipient_verified": d.recipient_verified, "subject": d.subject, "body": d.body, "requested_items": d.requested_items,
             "status": d.status, "sent_at": iso(d.sent_at), "followup_at": iso(d.followup_at)}
            for d in s.scalars(select(Draft).where(Draft.case_id == case.id))
        ],
        "messages": [
            {"id": m.id, "direction": m.direction, "from": m.from_email, "to": m.to_email, "subject": m.subject, "body": m.body, "indicators": m.indicators, "at": iso(m.created_at)}
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
    return [evidence_public(e) for e in s.scalars(select(EvidenceRow).where(EvidenceRow.case_id == case.id).order_by(EvidenceRow.retrieved_at))]


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
