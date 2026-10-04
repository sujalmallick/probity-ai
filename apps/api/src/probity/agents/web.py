"""Agent 4 — Web Research: "What external evidence exists?"

Structured findings only (URL + verbatim excerpt + tier). Third-party allegations are reported as
"a third-party source reports…", never as fact. Absence of results is "no adverse public findings
located in N sources", never "clean".
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from probity.agents.common import fv, load_case, record_claim, vendor_bundle
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.llm import client as llm
from probity.tools import lookups
from probity.tools.base import BudgetExceeded
from probity.tools.fetch import FetchBlocked

AGENT = "web_research"
ADVERSE = re.compile(r"(?i)\b(complain\w*|cheat\w*|unpaid|non-?delivery|consumer court|police|blacklist\w*|warning)\b")


class Queries(BaseModel):
    queries: list[str] = Field(max_length=6)


def _excerpt(text: str, needle: str, width: int = 300) -> str:
    i = text.lower().find(needle.lower())
    if i < 0:
        return text[:width]
    start = max(0, i - width // 3)
    return text[start : start + width]


def run(ctx: CaseCtx, depth: int) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Searching external sources")
    with session_scope() as s:
        case = load_case(s, ctx)
        ex = case.extraction
        b = vendor_bundle(s, ctx.workspace_id, case.vendor_id)
        name = (b["vendor"].name if b["vendor"] else None) or fv(ex, "vendor_name") or ""
        sender = fv(ex, "sender_domain")
        official = [x.domain for x in b["domains"] if x.verified]

    def mock_queries() -> Queries:
        q = [f'"{name}" complaints', f'"{name}" address']
        if sender:
            q.append(f'"{sender}"')
        if depth > 0:
            q.append(f'"{name}" directors')
            q += [f'"{dmn}"' for dmn in official[:1]]
        return Queries(queries=q[:6])

    pv, system = llm.load_prompt("web_research", "queries")
    queries = llm.generate(
        schema=Queries, system=system, user=f"Vendor name: {name}\nSender domain: {sender}\nKnown domains: {official}\nDepth: {depth}",
        tier="fast", tags={**ctx.tags, "agent": AGENT, "prompt_version": pv}, mock=mock_queries, budget=ctx.budget,
    ).queries[:6]

    sources = 0
    findings = 0
    searched: list[str] = []
    domain_mentions: dict[str, tuple[str, str, int]] = {}
    with session_scope() as s:
        for q in queries:
            ctx.progress(AGENT, f"Searching {q}")
            try:
                hits = lookups.web_search(q, ctx.budget)
            except BudgetExceeded:
                ctx.emit("agent.progress", agent=AGENT, status="running", message="Web budget exhausted — stopping research")
                break
            sources += 1
            searched.append(q)
            for h in hits[:2]:
                try:
                    page = lookups.fetch_page(h.url, ctx.budget) or h.snippet
                except FetchBlocked as e:
                    ctx.emit("agent.progress", agent=AGENT, status="running", message=f"Fetch blocked by SSRF guard: {e}")
                    continue
                except BudgetExceeded:
                    break
                sources += 1
                if name and name.split()[0].lower() not in page.lower():
                    continue  # page doesn't mention the entity → not reported
                if "complaints" in q and ADVERSE.search(page):
                    m = ADVERSE.search(page)
                    quote = _excerpt(page, m.group(0), 160)  # type: ignore[union-attr]
                    record_claim(s, ctx, AGENT, AgentClaim(
                        claim=f"A third-party source reports complaints mentioning {name} ({h.url}).",
                        evidence=[EvidenceIn(source="web", field="complaint", value=h.title, source_ref=h.url, excerpt=_excerpt(page, m.group(0)), tier=h.tier)],  # type: ignore[union-attr]
                        confidence=0.6, severity="warn", assertion={"op": "quote", "evidence": "$0", "quote": quote},
                    ))
                    findings += 1
                for dom in re.findall(r"(?i)website:\s*([a-z0-9.-]+\.[a-z]{2,})", page):
                    domain_mentions.setdefault(dom.lower(), (h.url, _excerpt(page, dom), h.tier))

        for dom, (url, excerpt, tier) in domain_mentions.items():
            quote = f"Website: {dom}"
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"Public business directory lists {name}'s website as {dom}" + (f" (invoice was sent from {sender})." if sender and sender != dom else "."),
                evidence=[EvidenceIn(source="web", field="listed_website", value=dom, source_ref=url, excerpt=excerpt, tier=tier)],
                confidence=0.8, severity="info", assertion={"op": "quote", "evidence": "$0", "quote": quote},
            ))
        if findings == 0:
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"No adverse public findings located in {sources} sources.",
                evidence=[EvidenceIn(source="web", field="search_log", value=sources, source_ref="search:" + " | ".join(searched), excerpt="Queries: " + "; ".join(searched), tier=2)],
                confidence=0.7, severity="info", assertion={"op": "info"},
            ))

    ctx.emit("agent.completed", agent=AGENT, status="done", message=f"{sources} external sources checked, {findings} adverse finding(s)")
    return {"sources": sources}
