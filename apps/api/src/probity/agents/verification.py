"""Agent 6 — Evidence Verification: "Are the claims supported?"  (+ the cite-or-drop validator)."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select

from probity.db.models import ClaimRow
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.verifier import verify_claims
from probity.llm import client as llm

AGENT = "verification"


class Entailment(BaseModel):
    status: str
    supporting_quote: str | None = None


def _entail_fn(ctx: CaseCtx):  # type: ignore[no-untyped-def]
    pv, system = llm.load_prompt("verification", "entail")

    def entail(claim: str, excerpts: list[str]) -> tuple[str, str | None]:
        out = llm.generate(
            schema=Entailment, system=system,
            user=f"CLAIM: {claim}\n\nEVIDENCE EXCERPTS:\n" + "\n---\n".join(excerpts),
            tier="reasoning", tags={**ctx.tags, "agent": AGENT, "prompt_version": pv},
            mock=lambda: Entailment(status="unverified"), budget=ctx.budget, redact_input=False,
        )
        st = out.status if out.status in ("verified", "refuted", "unverified") else "unverified"
        return st, out.supporting_quote

    return entail


def run(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Verifying claims against evidence")
    with session_scope() as s:
        claims = list(s.scalars(
            select(ClaimRow).where(ClaimRow.workspace_id == ctx.workspace_id, ClaimRow.case_id == ctx.case_id, ClaimRow.active.is_(True), ClaimRow.status == "unverified")
        ))
        ctx.progress(AGENT, f"Checking {len(claims)} claims (cite-or-drop, deterministic match, quote check)")
        stats = verify_claims(s, ctx.workspace_id, ctx.case_id, claims, entail=_entail_fn(ctx))
        for c in claims:
            if c.status == "verified" and c.signal:
                ctx.emit("claim.verified", agent=AGENT, status="running", message=c.statement, data={"claim_id": c.id, "signal": c.signal, "notes": c.verifier_notes})
            elif c.status in ("refuted", "dropped"):
                ctx.emit("claim.refuted", agent=AGENT, status="running", message=c.statement, data={"claim_id": c.id, "status": c.status, "notes": c.verifier_notes})
    ctx.emit("agent.completed", agent=AGENT, status="done", message=f"{stats['verified']} verified · {stats['unverified']} unconfirmed · {stats['refuted']} refuted · {stats['dropped']} dropped")
    return {"verification": stats}
