"""Agent event bus. Events are persisted (so SSE can resume with Last-Event-ID) and streamed to the UI."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from probity.db.models import AgentEvent
from probity.db.session import telemetry_scope
from probity.tools.base import Budget

EVENT_TYPES = {
    "case.created", "plan.created", "agent.started", "agent.progress", "agent.completed", "agent.failed",
    "agent.skipped", "evidence.added", "claim.verified", "claim.refuted", "risk.updated", "gate.waiting",
    "decision.recorded", "action.sent", "vendor.reply_received", "verification.confirmed_out_of_band", "case.closed",
}


def emit(workspace_id: str, case_id: str, type_: str, *, agent: str | None = None, status: str | None = None, message: str = "", data: dict[str, Any] | None = None) -> None:
    assert type_ in EVENT_TYPES, type_
    with telemetry_scope() as s:
        s.add(AgentEvent(workspace_id=workspace_id, case_id=case_id, type=type_, agent=agent, status=status, message=message, data=data or {}))


@dataclass
class CaseCtx:
    workspace_id: str
    case_id: str
    budget: Budget
    delay_ms: int = 0
    trace: list[str] = field(default_factory=list)

    def emit(self, type_: str, **kw: Any) -> None:
        emit(self.workspace_id, self.case_id, type_, **kw)

    def progress(self, agent: str, message: str, **data: Any) -> None:
        self.emit("agent.progress", agent=agent, status="running", message=message, data=data)
        self.pause()

    def pause(self, factor: float = 1.0) -> None:
        if self.delay_ms:
            time.sleep(self.delay_ms * factor / 1000)

    @property
    def tags(self) -> dict[str, str]:
        return {"workspace_id": self.workspace_id, "case_id": self.case_id}
