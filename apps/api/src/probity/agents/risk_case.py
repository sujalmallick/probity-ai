"""Risk Engine node (pure code) → Case Analyst (explains, cannot change the score) → Policy gate."""

from __future__ import annotations

from pydantic import BaseModel, Field
from sqlalchemy import select

from probity.agents.common import load_case
from probity.db.models import ClaimRow, RiskScoreRow, Workspace
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.guardrails.text import neutralize_language
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

        def mock() -> CaseSummary:
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
        out = llm.generate(schema=CaseSummary, system=system, user=f"Engine output: {risk}", tier="reasoning",
                           tags={**ctx.tags, "agent": "case_analyst", "prompt_version": pv}, mock=mock, budget=ctx.budget)
        out.recommendation = rec  # consistency with tier is enforced in code
        case.summary = neutralize_language(out.summary)
        case.recommendation = {
            "action": rec,
            "summary": case.summary,
            "top_findings": [neutralize_language(f) for f in out.top_findings],
            "unconfirmed": out.unconfirmed,
            "suggested_next_checks": out.suggested_next_checks,
        }
    ctx.emit("agent.completed", agent="case_analyst", status="done", message=rec.replace("_", " ").title())
    return {"recommendation": rec}


def requires_dual_approval(case, policy: dict) -> bool:  # type: ignore[no-untyped-def]
    return (case.risk or {}).get("tier") == "CRITICAL" or (case.amount_minor or 0) >= policy["dual_approval_amount_minor"]


def gate(ctx: CaseCtx) -> dict:
    """Policy gate (Guardrails G5). Auto-clear only when every condition holds; otherwise a human decides."""
    with session_scope() as s:
        case = load_case(s, ctx)
        policy = get_policy(s.get(Workspace, ctx.workspace_id))
        risk = case.risk
        claims = list(s.scalars(select(ClaimRow).where(ClaimRow.case_id == ctx.case_id, ClaimRow.active.is_(True))))
        required = set(case.plan.get("required_checks", []))
        incomplete = [k for k, v in (case.checks or {}).items() if v.get("status") == "failed"]
        fired_signals = [c for c in claims if c.signal and c.signal not in ("no_history", "round_sum")]
        unverified_high = [c for c in claims if c.status == "unverified" and c.severity == "high"]
        flagged_before = any(h.get("peak_tier") in ("HIGH", "CRITICAL") or h.get("outcome") == "CONFIRMED_ISSUE" for h in case.memory_hits or [])
        reasons = []
        if not policy["auto_clear_enabled"]:
            reasons.append("auto-clear disabled by policy")
        if risk["tier"] != "LOW":
            reasons.append(f"tier {risk['tier']}")
        if case.partial or incomplete:
            reasons.append("investigation incomplete (FAILED_PARTIAL)")
        if fired_signals:
            reasons.append(f"{len(fired_signals)} risk indicator(s) present")
        if unverified_high:
            reasons.append("unverified high-severity claims")
        if (case.amount_minor or 0) > policy["auto_clear_max_amount_minor"]:
            reasons.append("amount above auto-clear limit")
        if flagged_before and policy["previously_flagged_blocks_auto_clear"]:
            reasons.append("vendor previously flagged (case memory)")
        if "invoice_validation" in required and not all(v.get("ok") is not False for k, v in (case.validation or {}).items() if k != "injection"):
            reasons.append("document validation failed")
        auto = not reasons
        case.status = "AUTO_CLEARED" if auto else "AWAITING_HUMAN"
        case.recommendation = {**case.recommendation, "gate": {"auto_cleared": auto, "reasons": reasons, "dual_approval": requires_dual_approval(case, policy),
                                                               "requires_role": "approver"}}
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
        ctx.emit("gate.waiting", agent="policy_gate", status="done", message="Auto-cleared by policy (LOW, all checks complete)", data={"auto_cleared": True})
    else:
        ctx.emit("gate.waiting", agent="policy_gate", status="waiting", message="Held for human decision: " + "; ".join(reasons), data={"auto_cleared": False, "reasons": reasons})
    return {"auto_cleared": auto}
