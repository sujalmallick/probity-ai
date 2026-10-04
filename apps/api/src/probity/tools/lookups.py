"""External lookups. Every result becomes Evidence; anything that cannot be evidenced is discarded.

In `cached` mode results replay from fixtures (deterministic offline demo). In `mock` mode unknown keys
return empty results. `live` uses real providers where implemented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from probity.tools.base import Budget, fixture, mode
import httpx

from probity.config import get_settings
from probity.tools.fetch import FetchBlocked, check_url, safe_get

T1_SUFFIXES = (".gov.in", ".nic.in", "mca.gov.in", "gst.gov.in")
T2_DOMAINS = ("indiamart.com", "justdial.com", "zaubacorp.com", "tofler.in", "economictimes.indiatimes.com", "thehindubusinessline.com")


def source_tier(url: str) -> int:
    host = re.sub(r"^https?://", "", url or "").split("/", 1)[0].lower()
    if host.endswith(T1_SUFFIXES):
        return 1
    if any(host == d or host.endswith("." + d) for d in T2_DOMAINS):
        return 2
    return 3


@dataclass
class WhoisResult:
    domain: str
    created: date | None
    registrar: str | None
    excerpt: str
    source_ref: str


def whois(domain: str, budget: Budget) -> WhoisResult | None:
    budget.charge_web()
    if mode() in ("cached", "mock"):
        rec = fixture("whois").get(domain)
        if not rec:
            return None
        created = date.fromisoformat(rec["creation_date"])
        excerpt = f"Domain Name: {domain.upper()}\nRegistrar: {rec.get('registrar')}\nCreation Date: {rec['creation_date']}"
        return WhoisResult(domain, created, rec.get("registrar"), excerpt, f"rdap://{domain}")
    try:  # live: RDAP over the SSRF-safe client
        _, text = safe_get(f"https://rdap.org/domain/{domain}")
    except (FetchBlocked, Exception):  # noqa: BLE001
        return None
    m = re.search(r'"eventAction"\s*:\s*"registration"\s*,\s*"eventDate"\s*:\s*"(\d{4}-\d{2}-\d{2})', text)
    if not m:
        return None
    created = date.fromisoformat(m.group(1))
    return WhoisResult(domain, created, None, f"Domain Name: {domain.upper()}\nCreation Date: {m.group(1)}", f"https://rdap.org/domain/{domain}")


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    tier: int


def web_search(query: str, budget: Budget) -> list[SearchHit]:
    budget.charge_web()
    if mode() in ("cached", "mock"):
        hits = fixture("web_search").get(query.lower().strip(), [])
        return [SearchHit(h["url"], h["title"], h["snippet"], source_tier(h["url"])) for h in hits]
    key = get_settings().tavily_api_key
    if not key:
        return []
    try:
        r = httpx.post("https://api.tavily.com/search", json={"query": query, "max_results": 5, "search_depth": "basic", "include_answer": False},
                       headers={"Authorization": f"Bearer {key}"}, timeout=15)
        r.raise_for_status()
    except httpx.HTTPError:
        return []
    out = []
    for h in r.json().get("results", []):
        try:
            check_url(h["url"])  # never surface links to private/internal hosts
        except FetchBlocked:
            continue
        out.append(SearchHit(h["url"], h.get("title", ""), (h.get("content") or "")[:600], source_tier(h["url"])))
    return out


def fetch_page(url: str, budget: Budget) -> str | None:
    budget.charge_web()
    if mode() in ("cached", "mock"):
        return fixture("pages").get(url)
    try:
        _, text = safe_get(url)
        return re.sub(r"<[^>]+>", " ", text)
    except FetchBlocked:
        raise
    except Exception:  # noqa: BLE001
        return None


def gst_lookup(gstin: str, budget: Budget) -> dict | None:
    """GSTN registry (mock provider; a licensed GSP API plugs in for live)."""
    budget.charge_web()
    rec = fixture("gst_registry").get((gstin or "").upper())
    if rec:
        rec = {**rec, "source_ref": f"gst://{gstin.upper()}", "retrieved_at": datetime.now(timezone.utc).isoformat()}
    return rec
