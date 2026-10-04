"""Agent 4 — Web Research: "What external evidence exists?"

Structured findings only (URL + verbatim excerpt + tier). Third-party allegations are reported as
"a third-party source reports…", never as fact. "No adverse public findings" is only said when at least one
search actually ran; if search is not configured or every search failed, the check is "could not verify".
Search, page-fetch and AI failures are reported in the timeline.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from probity.agents.common import fv, load_case, record_claim, vendor_bundle
from probity.db.session import session_scope
from probity.events import CaseCtx, rule_based_fallback
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.guardrails.text import wrap_untrusted
from probity.llm import client as llm
from probity.tools import lookups
from probity.tools.base import BudgetExceeded

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


def standard_queries(name: str, sender: str | None, official: list[str], depth: int) -> list[str]:
    """Rule-based queries, used when the AI can't write them."""
    q = [f'"{name}" complaints', f'"{name}" address']
    if sender:
        q.append(f'"{sender}"')
    if depth > 0:
        q.append(f'"{name}" directors')
        q += [f'"{dmn}"' for dmn in official[:1]]
    return q[:6]


def run(ctx: CaseCtx, depth: int) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Searching external sources")
    with session_scope() as s:
        case = load_case(s, ctx)
        ex = case.extraction
        b = vendor_bundle(s, ctx.workspace_id, case.vendor_id)
        name = (b["vendor"].name if b["vendor"] else None) or fv(ex, "vendor_name") or ""
        sender = fv(ex, "sender_domain")
        official = [x.domain for x in b["domains"] if x.verified]

    if not name:
        check = ctx.unverifiable(AGENT, "external_reputation", "no vendor name to search for")
        ctx.emit("agent.completed", agent=AGENT, status="done", message="Could not verify external reputation")
        return {"sources": 0, "check": check}

    fallback = None
    try:
        pv, system = llm.load_prompt("web_research", "queries")
        queries = llm.generate(
            schema=Queries, system=system,
            user=wrap_untrusted("invoice_fields", f"Vendor name: {name}\nSender domain: {sender}") + f"\nKnown domains: {official}\nDepth: {depth}",
            tier="fast", tags={**ctx.tags, "agent": AGENT, "prompt_version": pv}, budget=ctx.budget,
        ).queries[:6]
    except llm.LLMFailed as e:
        queries = standard_queries(name, sender, official, depth)
        fallback = rule_based_fallback(f"search queries written by rules: {e.reason}")
        ctx.progress(AGENT, f"AI could not write search queries ({e.reason}); using standard rule-based queries", fallback="rule_based_queries")

    sources = 0
    scanned = 0  # pages (or snippets) about this vendor that were actually read and scanned for adverse findings
    findings = 0
    searched: list[str] = []
    failures: list[str] = []
    domain_mentions: dict[str, tuple[str, str, int]] = {}
    with session_scope() as s:
        for q in queries:
            ctx.progress(AGENT, f"Searching {q}")
            try:
                res = lookups.web_search(q, ctx.budget)
            except BudgetExceeded:
                ctx.progress(AGENT, "Web budget exhausted — stopping research")
                failures.append("web budget exhausted")
                break
            if res.status == "not_configured":
                failures.append(res.reason)
                break  # every query would get the same answer
            if res.status != "ok":
                failures.append(res.reason)
                ctx.emit("check.could_not_verify", agent=AGENT, status="warning", message=f"Search failed for {q}: {res.reason}",
                         data={"check": "external_reputation", "query": q, "reason": res.reason})
                continue
            sources += 1
            searched.append(q)
            for h in res.hits[:2]:
                try:
                    page = lookups.fetch_page(h.url, ctx.budget)
                except BudgetExceeded:
                    break
                if page.status == "ok":
                    text = page.text
                    sources += 1
                else:
                    ctx.progress(AGENT, f"Could not read {h.url}: {page.reason}; using the search snippet only", url=h.url, reason=page.reason)
                    text = h.snippet
                if name.split()[0].lower() not in text.lower():
                    continue  # page doesn't mention the entity → not reported
                scanned += 1
                # Every page about the vendor is scanned, whatever query found it (AI-written queries rarely contain
                # the word "complaints"; scanning only those let adverse pages through unread).
                m = ADVERSE.search(text)
                if m:
                    quote = _excerpt(text, m.group(0), 160)  # type: ignore[union-attr]
                    record_claim(s, ctx, AGENT, AgentClaim(
                        claim=f"A third-party source reports complaints mentioning {name} ({h.url}).",
                        evidence=[EvidenceIn(source="web", field="complaint", value=h.title, source_ref=h.url, excerpt=_excerpt(text, m.group(0)), tier=h.tier)],  # type: ignore[union-attr]
                        confidence=0.6, severity="warn", assertion={"op": "quote", "evidence": "$0", "quote": quote},
                    ))
                    findings += 1
                for dom in re.findall(r"(?i)website:\s*([a-z0-9.-]+\.[a-z]{2,})", text):
                    domain_mentions.setdefault(dom.lower(), (h.url, _excerpt(text, dom), h.tier))

        for dom, (url, excerpt, tier) in domain_mentions.items():
            quote = f"Website: {dom}"
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"Public business directory lists {name}'s website as {dom}" + (f" (invoice was sent from {sender})." if sender and sender != dom else "."),
                evidence=[EvidenceIn(source="web", field="listed_website", value=dom, source_ref=url, excerpt=excerpt, tier=tier)],
                confidence=0.8, severity="info", assertion={"op": "quote", "evidence": "$0", "quote": quote},
            ))
        coverage = f"{len(searched)} of {len(queries)} searches completed, {scanned} pages about the vendor read"
        if not searched:
            check = ctx.unverifiable(AGENT, "external_reputation", failures[0] if failures else "no search could be run")
        elif findings:
            check = {"status": "fired", "reason": coverage}
        elif scanned == 0:
            # Searches ran but nothing about the vendor could be read: that is not evidence of a clean record.
            check = ctx.unverifiable(AGENT, "external_reputation", f"no public pages about the vendor could be read ({coverage})")
        else:
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"No adverse public findings in {scanned} pages about the vendor ({len(searched)} of {len(queries)} searches completed).",
                evidence=[EvidenceIn(source="web", field="search_log", value=scanned, source_ref="search:" + " | ".join(searched), excerpt="Queries: " + "; ".join(searched), tier=2)],
                confidence=0.7, severity="info", assertion={"op": "info"},
            ))
            if failures:
                # Some searches failed: what was read is clean, but the check is incomplete and says so.
                check = ctx.unverifiable(AGENT, "external_reputation", f"only {coverage}; no adverse findings in what was read")
            else:
                check = {"status": "passed", "reason": coverage}
        if failures:
            check["warnings"] = failures[:5]
        if fallback:
            check["fallback"] = fallback

    ctx.emit("agent.completed", agent=AGENT, status="done",
             message=f"{sources} external sources checked, {findings} adverse finding(s)" if searched else "Could not verify external reputation")
    return {"sources": sources, "check": check}
