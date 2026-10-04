"""Agent event bus. Events are persisted (so SSE can resume with Last-Event-ID) and streamed to the UI."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from probity.db.models import AgentEvent, iso
from probity.db.session import telemetry_scope
from probity.redis_client import channel, sync_redis
from probity.tools.base import Budget

EVENT_TYPES = {
    "case.created", "plan.created", "agent.started", "agent.progress", "agent.completed", "agent.failed",
    "agent.skipped", "evidence.added", "claim.verified", "claim.refuted", "risk.updated", "gate.waiting",
    "decision.recorded", "action.sent", "vendor.reply_received", "verification.confirmed_out_of_band", "case.closed",
    "check.could_not_verify",
}


def emit(workspace_id: str, case_id: str, type_: str, *, agent: str | None = None, status: str | None = None, message: str = "", data: dict[str, Any] | None = None) -> None:
    """Persist the event (source of truth, supports Last-Event-ID replay), then publish it for live fan-out."""
    assert type_ in EVENT_TYPES, type_
    with telemetry_scope(workspace_id) as s:
        ev = AgentEvent(workspace_id=workspace_id, case_id=case_id, type=type_, agent=agent, status=status, message=message, data=data or {})
        s.add(ev)
        s.flush()
        payload = event_payload(ev)
    r = sync_redis()
    if r is not None:
        try:
            r.publish(channel(workspace_id, case_id), json.dumps(payload, default=str))
        except Exception:  # noqa: BLE001 - live fan-out is best effort; clients backfill from the DB
            pass


def event_payload(r: AgentEvent) -> dict[str, Any]:
    return {"seq": r.id, "ts": iso(r.ts), "case_id": r.case_id, "type": r.type, "agent": r.agent, "status": r.status, "message": r.message, "data": r.data}


FALLBACK_LABEL = "rule-based fallback, AI unavailable"


def rule_based_fallback(reason: str) -> dict[str, str]:
    """Attached to any result produced by rules because the AI failed; the UI shows `label` next to that result."""
    return {"kind": "rule_based", "label": FALLBACK_LABEL, "reason": reason}


def could_not_verify(reason: str, **extra: Any) -> dict[str, Any]:
    """A check result meaning "a tool or data source failed, so this was not checked" — never a pass."""
    return {"status": "could_not_verify", "reason": reason, **extra}


@dataclass
class CaseCtx:
    workspace_id: str
    case_id: str
    budget: Budget
    trace: list[str] = field(default_factory=list)

    def emit(self, type_: str, **kw: Any) -> None:
        emit(self.workspace_id, self.case_id, type_, **kw)

    def progress(self, agent: str, message: str, **data: Any) -> None:
        self.emit("agent.progress", agent=agent, status="running", message=message, data=data)

    def unverifiable(self, agent: str, check: str, reason: str) -> dict[str, Any]:
        """Report in the timeline that `check` could not be verified, and return the check result to store."""
        self.emit("check.could_not_verify", agent=agent, status="warning", message=f"Could not verify {check.replace('_', ' ')}: {reason}",
                  data={"check": check, "reason": reason})
        return could_not_verify(reason)

    @property
    def tags(self) -> dict[str, str]:
        return {"workspace_id": self.workspace_id, "case_id": self.case_id}
