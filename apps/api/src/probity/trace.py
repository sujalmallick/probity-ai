"""Per-agent trace of one case: for each agent, when it ran, how it ended, what it checked, what it could not verify,
which results came from a rule-based fallback, its AI calls (model, tokens, latency, failures) and the claims it made.

Everything is read back from what the run stored (timeline events, AI call log, checks, claims); nothing is inferred.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import AgentEvent, Case, ClaimRow, Draft, LLMCall, iso

AGENTS = ["document", "orchestrator", "vendor_investigator", "transaction_analyst", "web_research", "verification",
          "risk_engine", "case_analyst", "policy_gate", "action"]
# Which agent records which check.
CHECK_OWNER = {
    "vendor_identity": "vendor_investigator", "address_match": "vendor_investigator", "relationship_check": "vendor_investigator",
    "gst_format": "vendor_investigator", "gst_registry": "vendor_investigator", "gst_manual": "vendor_investigator",
    "domain_verification": "vendor_investigator",
    "bank_account_verification": "transaction_analyst", "duplicate_detection": "transaction_analyst", "price_anomaly": "transaction_analyst",
    "po_present": "transaction_analyst", "quantity_po_match": "transaction_analyst", "dates": "transaction_analyst",
    "history": "transaction_analyst", "statistical": "transaction_analyst", "round_sum": "transaction_analyst",
    "external_reputation": "web_research",
}
# Events that record a step of the case's life rather than an agent run: a person's decision or confirmation, the
# verification email, the vendor's reply, case creation and closing. Each one shows its agent ran; the event's own
# status says how it ended ("waiting" = a decision is recorded but another approval is still needed).
LIFECYCLE_EVENTS = {"case.created", "decision.recorded", "action.sent", "vendor.reply_received",
                    "verification.confirmed_out_of_band", "case.closed"}
_LIFECYCLE_STATUS = {"failed": "failed", "waiting": "waiting"}


def _blank(agent: str) -> dict[str, Any]:
    return {"agent": agent, "status": "not_run", "started_at": None, "finished_at": None, "seconds": None, "steps": [],
            "checks": {}, "could_not_verify": [], "fallbacks": [], "errors": [],
            "ai": {"calls": 0, "failed": 0, "tokens_in": 0, "tokens_out": 0, "latency_ms": 0, "models": []},
            "claims": {"total": 0, "verified": 0, "unverified": 0, "refuted": 0, "dropped": 0}}


def build(s: Session, case: Case) -> dict[str, Any]:
    from probity.guardrails.text import mask_account_numbers

    agents: dict[str, dict[str, Any]] = {a: _blank(a) for a in AGENTS}

    def get(name: str | None) -> dict[str, Any]:
        name = name or "pipeline"
        if name not in agents:
            agents[name] = _blank(name)
        return agents[name]

    events = list(s.scalars(select(AgentEvent).where(AgentEvent.case_id == case.id).order_by(AgentEvent.id)))
    for e in events:
        a = get(e.agent)
        if e.type == "agent.started":
            a["started_at"], a["status"] = a["started_at"] or e.ts, "running"
        elif e.type == "agent.completed":
            a["finished_at"], a["status"] = e.ts, "done"
        elif e.type == "agent.failed":
            a["finished_at"], a["status"] = e.ts, "failed"
            a["errors"].append(mask_account_numbers(e.message))
        elif e.type == "agent.skipped":
            a["status"] = "skipped"
            a["steps"].append(mask_account_numbers(e.message))
        elif e.type == "check.could_not_verify":
            a["could_not_verify"].append({"check": (e.data or {}).get("check"), "reason": (e.data or {}).get("reason") or e.message})
        elif e.type == "agent.progress":
            a["steps"].append(mask_account_numbers(e.message))
            if (e.data or {}).get("fallback"):
                a["fallbacks"].append({"what": (e.data or {}).get("fallback"), "message": mask_account_numbers(e.message)})
        elif e.type in ("gate.waiting", "risk.updated") and a["status"] == "not_run":
            a["status"], a["finished_at"] = "done", e.ts
            a["steps"].append(mask_account_numbers(e.message))
        elif e.type in LIFECYCLE_EVENTS:  # no agent.started here, so no duration: a person's think-time isn't run time
            a["status"], a["finished_at"] = _LIFECYCLE_STATUS.get(e.status or "", "done"), e.ts
            a["steps"].append(mask_account_numbers(e.message))
    for a in agents.values():
        if a["started_at"] and a["finished_at"]:
            a["seconds"] = round((a["finished_at"] - a["started_at"]).total_seconds(), 2)
        a["started_at"], a["finished_at"] = iso(a["started_at"]), iso(a["finished_at"])

    for name, chk in (case.checks or {}).items():
        a = get(CHECK_OWNER.get(name, "transaction_analyst"))
        a["checks"][name] = {"status": chk.get("status"), "reason": chk.get("reason")}
        if chk.get("fallback"):
            a["fallbacks"].append({"what": name, "label": chk["fallback"].get("label"), "message": chk["fallback"].get("reason")})
    rec = case.recommendation or {}
    if rec.get("summary_fallback"):
        get("case_analyst")["fallbacks"].append({"what": "summary", "label": rec["summary_fallback"].get("label"), "message": rec["summary_fallback"].get("reason")})
    for d in s.scalars(select(Draft).where(Draft.case_id == case.id)):
        if d.fallback:
            get("action")["fallbacks"].append({"what": "draft", "label": d.fallback.get("label"), "message": d.fallback.get("reason")})
    for f, v in (case.extraction or {}).items():
        if isinstance(v, dict) and v.get("fallback"):
            get("document")["fallbacks"].append({"what": f"field {f}", "label": v["fallback"].get("label"), "message": v["fallback"].get("reason")})

    for c in s.scalars(select(LLMCall).where(LLMCall.case_id == case.id).order_by(LLMCall.id)):
        ai = get(c.agent)["ai"]
        ai["calls"] += 1
        ai["failed"] += 0 if c.ok else 1
        ai["tokens_in"] += c.tokens_in
        ai["tokens_out"] += c.tokens_out
        ai["latency_ms"] += c.latency_ms
        if c.model not in ai["models"]:
            ai["models"].append(c.model)

    for c in s.scalars(select(ClaimRow).where(ClaimRow.case_id == case.id)):
        cl = get(c.agent)["claims"]
        cl["total"] += 1
        if c.status in cl:
            cl[c.status] += 1

    ordered = [agents[a] for a in AGENTS] + [v for k, v in agents.items() if k not in AGENTS]
    for a in ordered:
        a["steps"] = a["steps"][-20:]
        # Some work leaves no timeline events, only AI calls or claims (the on-demand risk explanation, approver
        # statements): those records show it ran.
        if a["status"] == "not_run" and (a["ai"]["calls"] or a["claims"]["total"]):
            a["status"] = "failed" if a["ai"]["calls"] and a["ai"]["failed"] == a["ai"]["calls"] and not a["claims"]["total"] else "done"
    totals = {
        "ai_calls": sum(a["ai"]["calls"] for a in ordered), "ai_failed": sum(a["ai"]["failed"] for a in ordered),
        "tokens": sum(a["ai"]["tokens_in"] + a["ai"]["tokens_out"] for a in ordered),
        "could_not_verify": sum(len(a["could_not_verify"]) for a in ordered),
        "fallbacks": sum(len(a["fallbacks"]) for a in ordered),
        "budget": case.budget or {},
    }
    return {"case_id": case.id, "status": case.status, "agents": ordered, "totals": totals,
            "sanity": rec.get("sanity") or {"ok": None, "problems": [], "checked_at": None}}
