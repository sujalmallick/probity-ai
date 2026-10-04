"""Agent 1 — Orchestrator/Planner: "What is this and what must be checked?"

The plan is policy-driven code (routing is never decided by an LLM). It resolves the vendor against the
master, pulls case memory, and selects checks from the allowed catalog.
"""

from __future__ import annotations

from rapidfuzz import fuzz
from sqlalchemy import or_, select

from probity.agents.common import fv, load_case, record_claim, vendor_bundle
from probity.db.models import CaseMemory, Vendor, Workspace
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.ingestion.validators import format_inr
from probity.policy import get_policy

AGENT = "orchestrator"
CATALOG = [
    "invoice_validation", "vendor_identity", "duplicate_detection", "price_anomaly", "quantity_po_match",
    "bank_account_verification", "domain_verification", "external_reputation", "relationship_check",
]


_PHRASES = {
    "bank_account_changed": "a bank-account change",
    "price_anomaly": "a price anomaly",
    "new_domain": "a newly registered sender domain",
    "identity_mismatch": "a vendor identity mismatch",
    "duplicate_invoice": "a duplicate invoice",
    "address_mismatch": "an address mismatch",
    "missing_po": "a missing PO",
    "suspicious_instruction_in_document": "instruction-like text in a document",
}


def _issue_phrase(issues: list[str]) -> str:
    parts = [_PHRASES.get(i, i.replace("_", " ")) for i in issues]
    if not parts:
        return "no anomalies"
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def match_vendor(s, workspace_id: str, gstin: str | None, name: str | None) -> tuple[Vendor | None, str]:  # type: ignore[no-untyped-def]
    vendors = list(s.scalars(select(Vendor).where(Vendor.workspace_id == workspace_id)))
    if gstin:
        for v in vendors:
            if v.gstin and v.gstin.upper() == gstin.upper():
                return v, "gstin_exact"
    if name:
        best = max(vendors, key=lambda v: fuzz.token_set_ratio(v.name.lower(), name.lower()), default=None)
        if best and fuzz.token_set_ratio(best.name.lower(), name.lower()) >= 90:
            return best, "name_fuzzy"
    return None, "none"


def run(ctx: CaseCtx, depth: int) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Planning investigation")
    with session_scope() as s:
        case = load_case(s, ctx)
        ex = case.extraction
        policy = get_policy(s.get(Workspace, ctx.workspace_id))
        ctx.progress(AGENT, "Matching vendor against vendor master")
        vendor, how = match_vendor(s, ctx.workspace_id, fv(ex, "gstin"), fv(ex, "vendor_name"))
        case.vendor_id = vendor.id if vendor else None
        bundle = vendor_bundle(s, ctx.workspace_id, case.vendor_id)

        bank_hmac = (ex.get("bank_account") or {}).get("hmac")
        domain = fv(ex, "sender_domain")
        bank_known = any(a.acct_hmac == bank_hmac and a.verified for a in bundle["accounts"]) if bank_hmac else True

        # Memory lookup (same vendor, bank, or domain).
        ctx.progress(AGENT, "Checking case memory")
        conds = []
        if case.vendor_id:
            conds.append(CaseMemory.vendor_id == case.vendor_id)
        mems = list(s.scalars(select(CaseMemory).where(CaseMemory.workspace_id == ctx.workspace_id, or_(*conds)).order_by(CaseMemory.created_at.desc()))) if conds else []
        mems += [
            m for m in s.scalars(select(CaseMemory).where(CaseMemory.workspace_id == ctx.workspace_id))
            if m not in mems and ((bank_hmac and bank_hmac in (m.bank_hmacs or [])) or (domain and domain in (m.domains or [])))
        ]
        hits = [
            {"case_id": m.case_id, "outcome": m.outcome, "summary": m.summary, "issues": m.issues, "resolution": m.resolution, "peak_score": m.peak_score, "peak_tier": m.peak_tier, "date": m.created_at.date().isoformat()}
            for m in mems
        ]
        case.memory_hits = hits

        amount = case.amount_minor or 0
        # The gate holds on every required check without a real answer (risk_case.REQUIRED_CHECKS), including ones
        # whose input is missing here; missing_info records why, so the reason reaches the reviewer.
        required = ["invoice_validation", "duplicate_detection"]
        optional: list[str] = []
        missing: list[str] = []
        rationale: list[str] = []
        required.append("vendor_identity")
        if not (fv(ex, "gstin") or fv(ex, "vendor_name")):
            missing.append("vendor_identity: no GSTIN or vendor name on invoice")
        required.append("price_anomaly")
        if not fv(ex, "line_items"):
            missing.append("price_anomaly: no line items on invoice")
        if fv(ex, "po_number"):
            required.append("quantity_po_match")
        else:
            missing.append("quantity_po_match: no PO number")
        required.append("bank_account_verification")
        if bank_hmac:
            if not bank_known:
                rationale.append("bank account on invoice is not a verified account for this vendor → deeper bank check")
        else:
            missing.append("bank_account_verification: no bank details on invoice")
        if domain:
            optional.append("domain_verification")
        external = vendor is None or amount >= policy["external_research_amount_minor"] or not bank_known or depth > 0
        if external:
            optional.append("external_reputation")
            rationale.append("vendor unknown" if vendor is None else ("amount ≥ " + format_inr(policy["external_research_amount_minor"]) if amount >= policy["external_research_amount_minor"] else "bank change / investigate further"))
        else:
            optional.append("external_reputation")
            rationale.append("known vendor, verified bank, amount under research threshold → web research skipped by policy")
        optional.append("relationship_check")
        priority = "high" if (not bank_known or amount >= policy["dual_approval_amount_minor"]) else ("medium" if amount >= policy["external_research_amount_minor"] else "normal")
        plan = {
            "case_type": "invoice",
            "priority": priority,
            "required_checks": required,
            "optional_checks": optional,
            "missing_info": missing,
            "parallel_groups": [["vendor_identity", "domain_verification"], ["duplicate_detection", "price_anomaly", "quantity_po_match", "bank_account_verification"]],
            "external_research": external,
            "vendor_match": how,
            "depth": depth,
            "rationale": "; ".join(rationale),
        }
        case.plan = plan
        case.depth = depth

        # Memory → claims. Only human-confirmed outcomes are memory; CONFIRMED_ISSUE scores, CLEARED is context.
        for h in hits:
            confirmed = h["outcome"] == "CONFIRMED_ISSUE"
            issues = _issue_phrase(h["issues"])
            stmt = (
                f"Previous investigation ({h['date']}) confirmed an issue for this vendor: {issues}."
                if confirmed
                else f"Previous investigation ({h['date']}) found {issues} for this vendor; closed as {h['outcome']}" + (f" — {h['resolution']}" if h.get("resolution") else "") + "."
            )
            record_claim(
                s, ctx, AGENT,
                AgentClaim(
                    claim=stmt,
                    signal="prior_confirmed_issue" if confirmed else None,
                    evidence=[EvidenceIn(source="vendor_history", field="prior_case_outcome", value=h["outcome"], source_ref=f"case_memory:{h['case_id']}", excerpt=h["summary"][:1000], tier=1)],
                    confidence=0.95,
                    severity="high" if confirmed else "info",
                    assertion={"op": "info"},
                    data={"memory": h},
                ),
            )

    ctx.emit("plan.created", agent=AGENT, status="done", message=f"{len(required)} required checks", data=plan)
    ctx.emit("agent.completed", agent=AGENT, status="done", message=f"Vendor: {vendor.name if vendor else 'not in vendor master'} · {len(hits)} memory hit(s)")
    return {"plan": plan, "vendor_id": case.vendor_id}
