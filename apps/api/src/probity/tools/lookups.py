"""External lookups. Each returns an explicit outcome, so "nothing found" and "could not check" are never confused:

    status = "ok"              the source answered (possibly with no results)
           | "not_found"       the source answered that the thing does not exist
           | "not_configured"  the integration has no key; nothing was asked
           | "error"           the source could not be reached or answered badly (reason says why)

Every successful result becomes Evidence; nothing is invented when a source fails.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import httpx

from probity.config import get_settings
from probity.tools.base import Budget
from probity.tools.fetch import FetchBlocked, FetchUnreachable, check_url, safe_get

Status = Literal["ok", "not_found", "not_configured", "error"]

T1_SUFFIXES = (".gov.in", ".nic.in", "mca.gov.in", "gst.gov.in")
T2_DOMAINS = ("indiamart.com", "justdial.com", "zaubacorp.com", "tofler.in", "economictimes.indiatimes.com", "thehindubusinessline.com")

GST_REGISTRY_REASON = "No GST registry provider is configured (there is no free official GSTIN lookup API)"


def source_tier(url: str) -> int:
    host = re.sub(r"^https?://", "", url or "").split("/", 1)[0].lower()
    if host.endswith(T1_SUFFIXES):
        return 1
    if any(host == d or host.endswith("." + d) for d in T2_DOMAINS):
        return 2
    return 3


# ---------------------------------------------------------------- domain registration (RDAP)

@dataclass
class RdapOutcome:
    status: Status
    domain: str
    created: date | None = None
    registrar: str | None = None
    excerpt: str = ""
    source_ref: str = ""
    reason: str = ""


def rdap_lookup(domain: str, budget: Budget) -> RdapOutcome:
    """Registration date from RDAP (rdap.org bootstraps to the registry's own RDAP server). Public, no key."""
    budget.charge_web()
    url = f"https://rdap.org/domain/{domain}"
    try:
        final_url, text = safe_get(url)
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404:
            return RdapOutcome("not_found", domain, source_ref=url, reason="the registry has no record of this domain")
        return RdapOutcome("error", domain, source_ref=url, reason=f"RDAP returned HTTP {e.response.status_code if e.response is not None else '?'}")
    except FetchUnreachable as e:
        return RdapOutcome("error", domain, source_ref=url, reason=f"RDAP unreachable: {e}")
    except FetchBlocked as e:
        return RdapOutcome("error", domain, source_ref=url, reason=f"RDAP request blocked: {e}")
    except Exception as e:  # noqa: BLE001 - network failures become "could not verify", never a guess
        return RdapOutcome("error", domain, source_ref=url, reason=f"RDAP unreachable ({type(e).__name__})")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return RdapOutcome("error", domain, source_ref=final_url, reason="RDAP answered with something that isn't JSON")
    reg = next((ev.get("eventDate") for ev in data.get("events", []) if ev.get("eventAction") == "registration"), None)
    registrar = next((ent.get("vcardArray", [None, []])[1] for ent in data.get("entities", []) if "registrar" in (ent.get("roles") or [])), None)
    registrar_name = None
    if isinstance(registrar, list):
        registrar_name = next((v[3] for v in registrar if isinstance(v, list) and len(v) > 3 and v[0] == "fn"), None)
    if not reg or not re.match(r"\d{4}-\d{2}-\d{2}", reg):
        return RdapOutcome("error", domain, registrar=registrar_name, source_ref=final_url, reason="RDAP record has no registration date")
    created = date.fromisoformat(reg[:10])
    excerpt = f"Domain Name: {domain.upper()}\nRegistrar: {registrar_name or 'unknown'}\nCreation Date: {created.isoformat()}"
    return RdapOutcome("ok", domain, created, registrar_name, excerpt, final_url)


# ---------------------------------------------------------------- web search (Tavily)

@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    tier: int


@dataclass
class SearchOutcome:
    status: Status
    query: str
    hits: list[SearchHit] = field(default_factory=list)
    reason: str = ""


def web_search(query: str, budget: Budget) -> SearchOutcome:
    key = get_settings().tavily_api_key
    if not key:
        return SearchOutcome("not_configured", query, reason="web search is not configured (TAVILY_API_KEY missing)")
    budget.charge_web()
    try:
        r = httpx.post("https://api.tavily.com/search", json={"query": query, "max_results": 5, "search_depth": "basic", "include_answer": False},
                       headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError as e:
        return SearchOutcome("error", query, reason=f"search service unreachable ({type(e).__name__})")
    if r.status_code in (401, 403):
        return SearchOutcome("error", query, reason="search key was rejected (check TAVILY_API_KEY)")
    if r.status_code == 429:
        return SearchOutcome("error", query, reason="search rate limit or credits exhausted")
    if r.status_code >= 400:
        return SearchOutcome("error", query, reason=f"search service returned HTTP {r.status_code}")
    try:
        results = r.json().get("results", [])
    except ValueError:
        return SearchOutcome("error", query, reason="search service returned invalid JSON")
    hits = []
    for h in results:
        try:
            check_url(h["url"])  # never surface links to private/internal hosts
        except (FetchBlocked, KeyError):
            continue
        hits.append(SearchHit(h["url"], h.get("title", ""), (h.get("content") or "")[:600], source_tier(h["url"])))
    return SearchOutcome("ok", query, hits)


# ---------------------------------------------------------------- page fetch

@dataclass
class FetchOutcome:
    status: Status
    url: str
    text: str = ""
    reason: str = ""


def fetch_page(url: str, budget: Budget) -> FetchOutcome:
    budget.charge_web()
    try:
        _, text = safe_get(url)
    except FetchUnreachable as e:
        return FetchOutcome("error", url, reason=f"page unreachable: {e}")
    except FetchBlocked as e:
        return FetchOutcome("error", url, reason=f"blocked by the SSRF guard: {e}")
    except httpx.HTTPStatusError as e:
        return FetchOutcome("error", url, reason=f"page returned HTTP {e.response.status_code if e.response is not None else '?'}")
    except Exception as e:  # noqa: BLE001
        return FetchOutcome("error", url, reason=f"page unreachable ({type(e).__name__})")
    return FetchOutcome("ok", url, re.sub(r"<[^>]+>", " ", text))
