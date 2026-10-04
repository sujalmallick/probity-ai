"""LangGraph investigation workflow (Architecture.md §4).

START → document → orchestrator ─┬─ vendor_investigator ─┐
                                 └─ transaction_analyst ─┴→ join ─(plan.external_research?)→ web_research ─┐
                                                                  └──────────────────────────────────────┴→ verification
      → risk_engine → case_analyst → policy_gate → END

Routing and termination are deterministic code. Each node is wrapped with bounded retries; a failed
node degrades the case to FAILED_PARTIAL (reduced confidence, auto-clear disabled) — never silent success.
The human gate is a durable status (AWAITING_HUMAN) persisted in Postgres/SQLite; decisions resume the
case through the services layer.
"""

from __future__ import annotations

import operator
import time
from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from probity.agents import document, orchestrator, risk_case, transaction, vendor, verification, web
from probity.config import get_settings
from probity.db.models import Case
from probity.db.session import session_scope, tenant
from probity.events import CaseCtx
from probity.ingestion.parse import UnsupportedDocument
from probity.tools.base import BudgetExceeded


def _merge(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    depth: int
    external: bool
    checks: Annotated[dict, _merge]
    sources: Annotated[int, operator.add]
    failed: Annotated[list, operator.add]


def _set_status(ctx: CaseCtx, status: str) -> None:
    with session_scope(ctx.workspace_id) as s:
        c = s.get(Case, ctx.case_id)
        assert c is not None
        c.status = status


def _guard(name: str, fn: Callable[[CaseCtx, State], dict], checks_on_fail: list[str], fatal: bool = False):  # type: ignore[no-untyped-def]
    def node(state: State, config: RunnableConfig) -> dict:
        ctx: CaseCtx = config["configurable"]["ctx"]
        retries = get_settings().max_retries
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with tenant(ctx.workspace_id):
                    return fn(ctx, state) or {}
            except (BudgetExceeded, UnsupportedDocument) as e:
                last = e
                break
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < retries:
                    time.sleep(0.2 * (2**attempt))
        ctx.emit("agent.failed", agent=name, status="failed", message=f"{name} failed: {last}" + ("" if fatal else " — continuing with reduced confidence"))
        if fatal:
            raise RuntimeError(f"{name}: {last}") from last
        with session_scope(ctx.workspace_id) as s:
            c = s.get(Case, ctx.case_id)
            assert c is not None
            c.partial = True
        return {"failed": [name], "checks": {k: {"status": "failed", "reason": str(last)[:200]} for k in checks_on_fail}}

    return node


def n_document(ctx: CaseCtx, st: State) -> dict:
    _set_status(ctx, "EXTRACTING")
    document.run(ctx)
    return {}


def n_orchestrator(ctx: CaseCtx, st: State) -> dict:
    _set_status(ctx, "INVESTIGATING")
    out = orchestrator.run(ctx, st.get("depth", 0))
    return {"external": out["plan"]["external_research"]}


def n_vendor(ctx: CaseCtx, st: State) -> dict:
    out = vendor.run(ctx)
    return {"checks": out["checks"], "sources": out["sources"]}


def n_transaction(ctx: CaseCtx, st: State) -> dict:
    out = transaction.run(ctx)
    return {"checks": out["checks"], "sources": 2}  # vendor history + PO records


def n_join(ctx: CaseCtx, st: State) -> dict:
    if not st.get("external"):
        ctx.emit("agent.skipped", agent="web_research", status="skipped", message="Skipped by policy: known vendor, verified bank, amount below research threshold")
    return {}


def n_web(ctx: CaseCtx, st: State) -> dict:
    out = web.run(ctx, st.get("depth", 0))
    return {"sources": out["sources"], "checks": {"external_reputation": {"status": "passed", "reason": f"{out['sources']} sources"}}}


def n_verification(ctx: CaseCtx, st: State) -> dict:
    _set_status(ctx, "VERIFYING")
    with session_scope() as s:
        c = s.get(Case, ctx.case_id)
        assert c is not None
        c.checks = {**(c.checks or {}), **st.get("checks", {})}
        c.sources_checked = (c.sources_checked or 0) + st.get("sources", 0)
    verification.run(ctx)
    return {}


def n_risk(ctx: CaseCtx, st: State) -> dict:
    _set_status(ctx, "SCORING")
    risk_case.score_case(ctx, "initial" if st.get("depth", 0) == 0 else "investigate_further")
    return {}


def n_analyst(ctx: CaseCtx, st: State) -> dict:
    risk_case.analyst(ctx)
    return {}


def n_gate(ctx: CaseCtx, st: State) -> dict:
    with session_scope() as s:
        c = s.get(Case, ctx.case_id)
        assert c is not None
        c.budget = ctx.budget.snapshot()
        if ctx.budget.exhausted:
            c.partial = True
    risk_case.gate(ctx)
    return {}


def route_web(st: State) -> str:
    return "web_research" if st.get("external") else "verification"


def build_graph():  # type: ignore[no-untyped-def]
    g = StateGraph(State)
    g.add_node("document", _guard("document", n_document, ["invoice_validation"], fatal=True))
    g.add_node("orchestrator", _guard("orchestrator", n_orchestrator, [], fatal=True))
    g.add_node("vendor_investigator", _guard("vendor_investigator", n_vendor, ["vendor_identity", "domain_verification"]))
    g.add_node("transaction_analyst", _guard("transaction_analyst", n_transaction, ["bank_account_verification", "duplicate_detection", "price_anomaly"]))
    g.add_node("join", _guard("join", n_join, []))
    g.add_node("web_research", _guard("web_research", n_web, ["external_reputation"]))
    g.add_node("verification", _guard("verification", n_verification, [], fatal=True))
    g.add_node("risk_engine", _guard("risk_engine", n_risk, [], fatal=True))
    g.add_node("case_analyst", _guard("case_analyst", n_analyst, []))
    g.add_node("policy_gate", _guard("policy_gate", n_gate, [], fatal=True))

    g.add_edge(START, "document")
    g.add_edge("document", "orchestrator")
    g.add_edge("orchestrator", "vendor_investigator")
    g.add_edge("orchestrator", "transaction_analyst")
    g.add_edge(["vendor_investigator", "transaction_analyst"], "join")
    g.add_conditional_edges("join", route_web, {"web_research": "web_research", "verification": "verification"})
    g.add_edge("web_research", "verification")
    g.add_edge("verification", "risk_engine")
    g.add_edge("risk_engine", "case_analyst")
    g.add_edge("case_analyst", "policy_gate")
    g.add_edge("policy_gate", END)
    return g.compile()


_GRAPH = None


def investigate(ctx: CaseCtx, depth: int = 0) -> None:
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    _GRAPH.invoke({"depth": depth, "checks": {}, "sources": 0, "failed": []}, config={"configurable": {"ctx": ctx}})
