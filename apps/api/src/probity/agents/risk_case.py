"""Risk Engine node (pure code) → Case Analyst (explains, cannot change the score) → Policy gate."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field
from sqlalchemy import select

from probity.agents.common import load_case
from probity.db.models import ClaimRow, RiskScoreRow, Workspace
from probity.db.session import session_scope
from probity.events import CaseCtx, rule_based_fallback
from probity.tools.base import BudgetExceeded
from probity.guardrails.text import neutralize_language, wrap_untrusted
from probity.llm import client as llm
from probity.policy import get_policy
from probity.risk import engine

ORDER = {"verified": 0, "unverified": 1, "refuted": 2}


def compute(s, workspace_id: str, case_id: str, weights_version: str, overrides: dict) -> tuple[engine.RiskResult, list[engine.ScoredSignal]]:  # type: ignore[no-untyped-def]
    claims = list(s.scalars(select(ClaimRow).where(ClaimRow.workspace_id == workspace_id, ClaimRow.case_id == case_id, ClaimRow.active.is_(True), ClaimRow.signal.is_not(None))))
    best: dict[str, ClaimRow] = {}
    for c in claims:
        if c.signal not in best or ORDER.get(c.status, 3) < ORDER.get(best[c.signal].status, 3):
            best[c.signal] = c
    signals = [engine.ScoredSignal(sig, True, c.id, c.status, c.severity) for sig, c in best.items()]
    return engine.score(signals, weights_version, overrides), signals


def score_case(ctx: CaseCtx, reason: str) -> dict:
    ctx.emit("agent.started", agent="risk_engine", status="running", message="Scoring (deterministic, versioned weights)")
    with session_scope() as s:
        case = load_case(s, ctx)
        policy = get_policy(s.get(Workspace, ctx.workspace_id))
        result, signals = compute(s, ctx.workspace_id, ctx.case_id, policy["weights_version"], policy["weight_overrides"])
        prev = dict(case.risk or {})
        contribs = result.explain()
        claims = {c.id: c for c in s.scalars(select(ClaimRow).where(ClaimRow.case_id == ctx.case_id))}
        for c in contribs:
            cl = claims.get(c["claim_id"]) if c["claim_id"] else None
            c["statement"] = cl.statement if cl else None
            c["observed"] = (cl.data or {}).get("observed") if cl else None
            c["baseline"] = (cl.data or {}).get("baseline") if cl else None
        risk = {
            "score": result.score,
            "tier": result.tier,
            "provisional": False,
            "weights_version": result.weights_version,
            "contributions": contribs,
            "partial": case.partial,
        }
        if prev.get("score") is not None and reason != "initial":
            before = {c["signal"]: c["points"] for c in prev.get("contributions", [])}
            after = {c["signal"]: c["points"] for c in contribs}
            risk["previous"] = {"score": prev["score"], "tier": prev["tier"]}
            risk["diff"] = [
                {"signal": sg, "label": engine.LABELS.get(sg, sg), "before": before.get(sg, 0), "after": after.get(sg, 0)}
                for sg in sorted(set(before) | set(after)) if before.get(sg, 0) != after.get(sg, 0)
            ]
        case.risk = risk
        s.add(RiskScoreRow(workspace_id=ctx.workspace_id, case_id=ctx.case_id, score=result.score, tier=result.tier, weights_version=result.weights_version,
                           signals=[sig.__dict__ for sig in signals], contributions=contribs, reason=reason))
    ctx.emit("risk.updated", agent="risk_engine", status="done", message=f"{result.score}/100 · {result.tier}", data=risk)
    ctx.emit("agent.completed", agent="risk_engine", status="done", message=f"Score {result.score} ({result.tier})")
    return risk


class CaseSummary(BaseModel):
    summary: str = Field(max_length=600)
    top_findings: list[str] = Field(default_factory=list)
    recommendation: str
    suggested_next_checks: list[str] = Field(default_factory=list)
    unconfirmed: list[str] = Field(default_factory=list)


KEEP_ACROSS_RUNS = ("retry_of", "retried_as")


def analyst(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent="case_analyst", status="running", message="Writing case summary")
    with session_scope() as s:
        case = load_case(s, ctx)
        risk = case.risk
        counted = [c for c in risk["contributions"] if c["status"] == "counted" and c["points"] > 0]
        unconf = [c for c in risk["contributions"] if c["status"] == "unconfirmed"]
        rec = engine.recommendation_for(risk["tier"])
        if rec == "HOLD_PAYMENT" and any(c["signal"] in ("bank_account_changed", "identity_mismatch", "new_domain") for c in counted):
            rec_next = ["Request vendor verification via the verified master contact", "Confirm bank details out-of-band (call known contact)"]
        else:
            rec_next = ["Review findings"] if counted else []

        def engine_summary() -> CaseSummary:
            n = len(counted)
            if n:
                lst = "; ".join(f"{c['label'].lower()} ({c['observed']} vs {c['baseline']})" if c.get("observed") else c["label"].lower() for c in counted)
                text = f"{n} verified anomal{'y was' if n == 1 else 'ies were'} identified: {lst}. Score {risk['score']}/100 ({risk['tier']}) is the sum of these verified signals."
            else:
                text = f"No verified risk indicators were identified across the checks that ran. Score {risk['score']}/100 ({risk['tier']})."
            if risk.get("partial"):
                text += " Some checks did not complete; confidence is reduced."
            return CaseSummary(summary=text[:600], top_findings=[c["statement"] or c["label"] for c in counted], recommendation=rec,
                               suggested_next_checks=rec_next, unconfirmed=[c["statement"] or c["label"] for c in unconf])

        pv, system = llm.load_prompt("case_analyst", "summary")
        summary_source = "ai"
        summary_fallback = None
        try:
            out = llm.generate(schema=CaseSummary, system=system, user=analyst_input(risk, case.checks), tier="reasoning",
                               tags={**ctx.tags, "agent": "case_analyst", "prompt_version": pv}, budget=ctx.budget)
        except llm.LLMFailed as e:
            out = engine_summary()
            summary_source = "engine"
            summary_fallback = rule_based_fallback(f"summary written by the risk engine: {e.reason}")
            ctx.progress("case_analyst", f"AI summary unavailable ({e.reason}); showing the risk engine's own summary", fallback="engine_summary")
        out.recommendation = rec  # consistency with tier is enforced in code
        case.summary = neutralize_language(out.summary)
        keep = {k: v for k, v in (case.recommendation or {}).items() if k in KEEP_ACROSS_RUNS}  # links survive a re-summary
        case.recommendation = {
            **keep,
            "action": rec,
            "summary": case.summary,
            "top_findings": [neutralize_language(f) for f in out.top_findings],
            "unconfirmed": [neutralize_language(u) for u in out.unconfirmed],
            "suggested_next_checks": [neutralize_language(n) for n in out.suggested_next_checks],
            "summary_source": summary_source,
            "summary_fallback": summary_fallback,
        }
    ctx.emit("agent.completed", agent="case_analyst", status="done", message=rec.replace("_", " ").title())
    return {"recommendation": rec}


TOTAL_MIN_CONFIDENCE = 0.8


def analyst_input(risk: dict, checks: dict | None) -> str:
    """What the case analyst sees: structured engine facts only. Claim statements and evidence snippets quote the
    invoice and vendor replies (an injected instruction can sit right there), so they are left out, and observed /
    baseline values, which can also come from the document, are passed inside an untrusted-data wrapper."""
    facts = {
        "score": risk.get("score"), "tier": risk.get("tier"), "partial": bool(risk.get("partial")),
        "contributions": [{k: c.get(k) for k in ("signal", "label", "points", "status", "observed", "baseline")} for c in risk.get("contributions", [])],
    }
    check_facts = {k: {"status": v.get("status"), "reason": v.get("reason")} for k, v in (checks or {}).items() if isinstance(v, dict)}
    return (wrap_untrusted("risk_engine_output", json.dumps(facts, default=str, ensure_ascii=False)) + "\n"
            + wrap_untrusted("check_results", json.dumps(check_facts, default=str, ensure_ascii=False)))


def total_problem(case) -> str | None:  # type: ignore[no-untyped-def]
    """Why the invoice total can't be trusted, or None. Amount-based limits (auto-clear cap, dual approval) are
    meaningless without a reliable total, so an unreliable one is treated as above every limit."""
    f = (case.extraction or {}).get("total") or {}
    value = f.get("value")
    if not isinstance(value, int):
        return "invoice total could not be read"
    if value <= 0:
        return "invoice total is zero or negative"
    if f.get("via") != "human_correction" and float(f.get("confidence") or 0) < TOTAL_MIN_CONFIDENCE:
        return f"invoice total was read with low confidence ({float(f.get('confidence') or 0):.2f})"
    return None


def requires_dual_approval(case, policy: dict, tier: str | None = None) -> bool:  # type: ignore[no-untyped-def]
    """tier: the case's peak tier when deciding (services.peak_tier); defaults to the current tier."""
    tier = tier or (case.risk or {}).get("tier")
    return (tier == "CRITICAL" or (case.amount_minor or 0) >= policy["dual_approval_amount_minor"]
            or total_problem(case) is not None)


# Checks that must each produce a real answer before an invoice can auto-clear (user decision, Phase 1 audit).
# "document read" is invoice_validation; quantity_po_match applies only when the invoice quotes a PO (an invoice
# without a PO already raises the missing_po indicator, which holds it).
REQUIRED_CHECKS: tuple[str, ...] = (
    "invoice_validation", "vendor_identity", "bank_account_verification", "price_anomaly", "quantity_po_match", "duplicate_detection",
)
# Enrichment: when these could not be verified the case shows an "incomplete" note, but auto-clear is not blocked.
OPTIONAL_CHECKS: tuple[str, ...] = ("domain_verification", "external_reputation")
NOT_ANSWERED = ("skipped", "could_not_verify", "failed")


def _cap(budget, code: str) -> int | None:  # type: ignore[no-untyped-def]
    from probity.config import get_settings

    return {"tokens": budget.max_tokens, "searches": budget.max_searches, "web_calls": budget.max_web_calls,
            "llm_calls": budget.max_llm_calls, "wall_clock": budget.max_seconds,
            "workspace_daily_tokens": get_settings().workspace_daily_token_limit}.get(code)


def required_checks_for(case) -> list[str]:  # type: ignore[no-untyped-def]
    from probity.agents.common import fv

    return [k for k in REQUIRED_CHECKS if k != "quantity_po_match" or fv(case.extraction or {}, "po_number")]


def unverified_required(case) -> list[dict]:  # type: ignore[no-untyped-def]
    """Required checks without a real answer: a tool failed, data was missing, the check could not run, or it never
    recorded a result at all. "No evidence of a problem" is not evidence of no problem, so each of these holds."""
    checks = case.checks or {}
    missing_info = {m.split(":", 1)[0]: m.split(":", 1)[1].strip() for m in (case.plan or {}).get("missing_info", []) if ":" in m}
    out = []
    for k in required_checks_for(case):
        if k == "invoice_validation":
            # Document checks live in case.validation; a failed one is handled by the gate's validation rule.
            if not case.validation:
                out.append({"check": k, "status": "missing", "reason": "the document was not read"})
            continue
        v = checks.get(k)
        if v is None:
            out.append({"check": k, "status": "missing", "reason": missing_info.get(k) or "the check did not run"})
        elif v.get("status") in NOT_ANSWERED:
            out.append({"check": k, "status": v.get("status"), "reason": v.get("reason") or v.get("status")})
    return out


def unverified_optional(case) -> list[dict]:  # type: ignore[no-untyped-def]
    return [{"check": k, "status": v.get("status"), "reason": v.get("reason") or v.get("status")}
            for k, v in (case.checks or {}).items() if k in OPTIONAL_CHECKS and v.get("status") in ("could_not_verify", "failed")]


def gate(ctx: CaseCtx) -> dict:
    """Policy gate (Guardrails G5). Auto-clear only when every condition holds; otherwise a human decides."""
    with session_scope() as s:
        case = load_case(s, ctx)
        if case.status == "FAILED":
            # Ended by the watchdog or restart recovery while this run was still going: never revive it.
            from probity.services import CaseStopped

            raise CaseStopped(ctx.case_id)
        policy = get_policy(s.get(Workspace, ctx.workspace_id))
        risk = case.risk
        claims = list(s.scalars(select(ClaimRow).where(ClaimRow.case_id == ctx.case_id, ClaimRow.active.is_(True))))
        required = set(required_checks_for(case))
        failed_checks = [k for k, v in (case.checks or {}).items() if v.get("status") == "failed"]
        # A failed optional enrichment step (web research, domain age) leaves only an "incomplete" note; any other
        # failure, or a failed step that recorded no check at all, still holds the invoice.
        incomplete = [k for k in failed_checks if k not in OPTIONAL_CHECKS]
        partial_blocks = bool(case.partial) and not (failed_checks and set(failed_checks) <= set(OPTIONAL_CHECKS))
        not_verified = unverified_required(case)
        optional_gaps = unverified_optional(case)
        total_issue = total_problem(case)
        gst_manual_flag = ((case.checks or {}).get("gst_manual") or {}).get("status") == "fired"
        no_history = (case.checks or {}).get("history", {}).get("status") == "fired"
        # Someone changed what the document says before it was checked; an approver must see both versions.
        corrected = sorted(k for k, f in (case.extraction or {}).items() if isinstance(f, dict) and f.get("via") == "human_correction")
        fired_signals = [c for c in claims if c.signal and c.signal not in ("no_history", "round_sum")]
        unverified_high = [c for c in claims if c.status == "unverified" and c.severity == "high"]
        flagged_before = any(h.get("peak_tier") in ("HIGH", "CRITICAL") or h.get("outcome") == "CONFIRMED_ISSUE" for h in case.memory_hits or [])
        reasons = []
        if not policy["auto_clear_enabled"]:
            reasons.append("auto-clear disabled by policy")
        if risk["tier"] != "LOW":
            reasons.append(f"tier {risk['tier']}")
        if case.vendor_id is None:
            reasons.append("vendor not matched to the vendor master")
        if partial_blocks or incomplete:
            reasons.append("investigation incomplete (FAILED_PARTIAL)")
        for u in not_verified:
            reasons.append(f"Could not verify: {u['check'].replace('_', ' ')} — {u['reason']}")
        if total_issue:
            reasons.append(total_issue + " (dual approval required)")
        if gst_manual_flag:
            reasons.append("GST status entered manually as " + str((case.checks or {}).get("gst_manual", {}).get("reason", "not Active")))
        if no_history:
            reasons.append("no transaction history with this vendor")
        if corrected:
            reasons.append("extracted fields corrected by a person before investigation: " + ", ".join(corrected))
        if fired_signals:
            reasons.append(f"{len(fired_signals)} risk indicator(s) present")
        if unverified_high:
            reasons.append("unverified high-severity claims")
        if (case.amount_minor or 0) > policy["auto_clear_max_amount_minor"]:
            reasons.append("amount above auto-clear limit")
        if flagged_before and policy["previously_flagged_blocks_auto_clear"]:
            reasons.append("vendor previously flagged (case memory)")
        if "invoice_validation" in required:
            for k, v in (case.validation or {}).items():
                if k == "injection" or v.get("ok") is not False:
                    continue
                if k == "currency":
                    reasons.append(f"Currency: {v.get('reason') or 'not INR'} (held; amounts are never converted)")
                else:
                    reasons.append(f"document validation failed: {k.replace('_', ' ')}")
        limits_hit = [str(BudgetExceeded(code, _cap(ctx.budget, code))) for code in (ctx.budget.exhausted if ctx.budget else [])]
        for msg in limits_hit:
            reasons.append(f"Stopped early — {msg}")
        from probity import sanity

        for p in sanity.check_case(s, case, final=False):  # a result that doesn't add up is never auto-cleared
            reasons.append(f"Sanity check failed: {p['message']}")
        auto = not reasons
        case.status = "AUTO_CLEARED" if auto else "AWAITING_HUMAN"
        notes = [f"Incomplete: {g['check'].replace('_', ' ')} — {g['reason']}" for g in optional_gaps]
        case.recommendation = {**case.recommendation, "gate": {"auto_cleared": auto, "reasons": reasons, "dual_approval": requires_dual_approval(case, policy),
                                                               "requires_role": "approver", "could_not_verify": not_verified,
                                                               "incomplete_optional": optional_gaps, "notes": notes, "limits_reached": limits_hit}}
        final_problems = sanity.check_case(s, case, final=True)
        if auto and final_problems:  # defence in depth: the decision itself must pass the invariants
            auto = False
            case.status = "AWAITING_HUMAN"
            reasons.extend(f"Sanity check failed: {p['message']}" for p in final_problems)
            case.recommendation = {**case.recommendation, "gate": {**case.recommendation["gate"], "auto_cleared": False, "reasons": reasons}}
        sanity.stamp(case, final_problems)
        if not auto:
            from probity.agents.common import fv
            from probity.ingestion.validators import format_inr
            from probity.notify import notify

            inv = fv(case.extraction, "invoice_number") or f"case #{case.number}"
            vendor = fv(case.extraction, "vendor_name") or "unknown vendor"
            notify(s, ctx.workspace_id, "approver", "case_held",
                   f"{inv} from {vendor} held — {risk['score']}/100 {risk['tier']}",
                   f"{format_inr(case.amount_minor)} · {case.recommendation.get('action', '').replace('_', ' ').lower()}. {case.summary or ''}", case.id)
    if auto:
        ctx.emit("gate.waiting", agent="policy_gate", status="done",
                 message="Auto-cleared by policy (LOW, every required check answered)" + (f"; {'; '.join(notes)}" if notes else ""),
                 data={"auto_cleared": True, "notes": notes})
    else:
        ctx.emit("gate.waiting", agent="policy_gate", status="waiting", message="Held for human decision: " + "; ".join(reasons), data={"auto_cleared": False, "reasons": reasons})
    return {"auto_cleared": auto}
