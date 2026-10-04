"""Helpers shared by agents: loading the case, vendor data, and turning signals into evidence-backed claims."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import Case, ClaimRow, Vendor, VendorBankAccount, VendorContact, VendorDomain
from probity.evidence.models import AgentClaim
from probity.evidence.store import add_claim
from probity.events import CaseCtx


def fv(extraction: dict, key: str) -> Any:
    return (extraction.get(key) or {}).get("value")


def fsnip(extraction: dict, key: str) -> str | None:
    return (extraction.get(key) or {}).get("evidence_snippet")


def load_case(s: Session, ctx: CaseCtx) -> Case:
    c = s.get(Case, ctx.case_id)
    if c is None or c.workspace_id != ctx.workspace_id:
        raise LookupError("case not found in workspace")
    return c


def vendor_bundle(s: Session, workspace_id: str, vendor_id: str | None) -> dict[str, Any]:
    if not vendor_id:
        return {"vendor": None, "accounts": [], "domains": [], "contacts": []}
    v = s.get(Vendor, vendor_id)
    if v is None or v.workspace_id != workspace_id:
        return {"vendor": None, "accounts": [], "domains": [], "contacts": []}
    q = lambda M: list(s.scalars(select(M).where(M.workspace_id == workspace_id, M.vendor_id == vendor_id)))  # noqa: E731
    return {"vendor": v, "accounts": q(VendorBankAccount), "domains": q(VendorDomain), "contacts": q(VendorContact)}


def record_claim(s: Session, ctx: CaseCtx, agent: str, claim: AgentClaim) -> ClaimRow:
    row = add_claim(s, ctx.workspace_id, ctx.case_id, agent, claim)
    ctx.emit(
        "evidence.added",
        agent=agent,
        status="running",
        message=claim.claim,
        data={"claim_id": row.id, "signal": claim.signal, "evidence_ids": row.evidence_ids},
    )
    return row


def deactivate_agent_claims(s: Session, ctx: CaseCtx, agents: list[str], reason: str) -> None:
    """Re-runs replace an agent's previous claims (kept for audit, excluded from scoring)."""
    for c in s.scalars(
        select(ClaimRow).where(ClaimRow.workspace_id == ctx.workspace_id, ClaimRow.case_id == ctx.case_id, ClaimRow.agent.in_(agents), ClaimRow.active.is_(True))
    ):
        c.active = False
        c.data = {**(c.data or {}), "superseded": reason}


def today() -> date:
    return date.today()
