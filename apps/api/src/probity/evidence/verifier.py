"""Evidence Verification (Feature F6, Architecture §6, Guardrails G1).

Order per claim:
  1. cite-or-drop: no evidence, or evidence ids not in the store → dropped.
  2. Deterministic check of the claim's `assertion` against evidence *values* (never the agent's numbers).
  3. Quote check: any quoted span must exist verbatim in the evidence excerpt.
  4. LLM entailment (live mode only, excerpt-only, returned quote re-checked by string match).
  5. Source-tier rule: T3-only text evidence cannot verify on its own (needs corroboration).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import ClaimRow, EvidenceRow
from probity.evidence.store import evidence_key, evidence_value
from probity.ingestion.validators import parse_date


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def quote_in(quote: str | None, excerpt: str | None) -> bool:
    return bool(quote) and _norm(quote) in _norm(excerpt or "")


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict) and "amount_minor" in v:
        return float(v["amount_minor"])
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def check_assertion(a: dict[str, Any], ev: dict[str, EvidenceRow]) -> tuple[str, str, str | None]:
    """Return (status, note, supporting_quote)."""
    op = a.get("op")
    get = lambda k: ev.get(a.get(k, ""))  # noqa: E731

    if op == "not_in":
        subj = get("subject")
        others = [ev.get(i) for i in a.get("set", [])]
        if subj is None or any(o is None for o in others) or not others:
            return "unverified", "referenced evidence missing", None
        keys = {evidence_key(o) for o in others}
        if evidence_key(subj) in keys:
            return "refuted", f"{evidence_value(subj)} is present in the comparison set", None
        return "verified", f"{evidence_value(subj)} not among {[evidence_value(o) for o in others]}", None

    if op == "equals":
        x, y = get("a"), get("b")
        if x is None or y is None:
            return "unverified", "referenced evidence missing", None
        same = evidence_key(x) == evidence_key(y)
        return ("verified" if same else "refuted"), f"{evidence_value(x)} vs {evidence_value(y)}", None

    if op == "pct_above":
        obs, base = get("observed"), get("baseline")
        o, b = (_num(evidence_value(obs)) if obs else None), (_num(evidence_value(base)) if base else None)
        if o is None or not b:
            return "unverified", "values not numeric", None
        pct = (o - b) / b * 100
        if pct < float(a.get("min_pct", 0)):
            return "refuted", f"recomputed change {pct:.1f}% below threshold", None
        claimed = a.get("claimed_pct")
        if claimed is not None and abs(pct - float(claimed)) > 0.5:
            return "refuted", f"agent stated {claimed}% but evidence gives {pct:.1f}%", None
        return "verified", f"recomputed {b:.0f} → {o:.0f} = +{pct:.1f}%", None

    if op == "gt":
        obs, base = get("observed"), get("baseline")
        o, b = (_num(evidence_value(obs)) if obs else None), (_num(evidence_value(base)) if base else None)
        if o is None or b is None:
            return "unverified", "values not numeric", None
        return ("verified" if o > b else "refuted"), f"{o:g} vs {b:g}", None

    if op == "similarity_below":
        x, y = get("a"), get("b")
        if x is None or y is None:
            return "unverified", "referenced evidence missing", None
        from rapidfuzz import fuzz

        sim = fuzz.token_set_ratio(str(evidence_value(x)).lower(), str(evidence_value(y)).lower())
        thr = int(a.get("threshold", 80))
        return ("verified" if sim < thr else "refuted"), f"similarity {sim} (threshold {thr})", None

    if op == "age_below":
        e = get("evidence")
        quote = a.get("quote")
        if e is None or not quote_in(quote, e.excerpt):
            return "unverified", "registration quote not found verbatim in evidence excerpt", None
        m = re.search(r"(\d{4}-\d{2}-\d{2})", quote or "")
        created = parse_date(m.group(1)) if m else None
        if created is None:
            return "unverified", "no parseable date in quote", None
        ref = e.retrieved_at.date() if isinstance(e.retrieved_at, datetime) else date.today()
        age = (ref - created).days
        if age >= int(a.get("max_days", 90)):
            return "refuted", f"domain age {age} days", quote
        claimed = a.get("claimed_days")
        if claimed is not None and abs(age - int(claimed)) > 1:
            return "refuted", f"agent stated {claimed} days but evidence gives {age}", quote
        return "verified", f"registered {created}, {age} days before retrieval", quote

    if op == "quote":
        e = get("evidence")
        quote = a.get("quote")
        if e is None:
            return "unverified", "referenced evidence missing", None
        if not quote_in(quote, e.excerpt):
            return "unverified", "quote not found verbatim in excerpt", None
        return "verified", "quote found verbatim in evidence", quote

    if op == "requires_out_of_band":
        return "unverified", "assertion from untrusted channel — requires approver out-of-band confirmation", None

    if op == "info":
        return "verified", "informational finding; evidence attached", None

    return "unverified", f"no deterministic check for op={op!r}", None


def verify_claims(s: Session, workspace_id: str, case_id: str, claims: list[ClaimRow], entail: Any = None) -> dict[str, int]:
    """Verify claims in place. `entail` is an optional callable(claim, excerpts) -> (status, quote[, note]); a note means it could not check."""
    stats = {"verified": 0, "refuted": 0, "unverified": 0, "dropped": 0}
    all_ev = {
        e.id: e
        for e in s.scalars(select(EvidenceRow).where(EvidenceRow.workspace_id == workspace_id, EvidenceRow.case_id == case_id))
    }
    for c in claims:
        if not c.active:
            continue
        ev = {i: all_ev[i] for i in c.evidence_ids if i in all_ev}
        # 1. cite-or-drop
        if not c.evidence_ids or len(ev) != len(c.evidence_ids):
            c.status, c.active = "dropped", False
            c.verifier_notes = "cite-or-drop: claim has no evidence or references evidence that does not exist"
            stats["dropped"] += 1
            continue
        status, note, quote = check_assertion(c.assertion or {}, ev)
        # 4. optional LLM entailment for claims with no deterministic check
        if status == "unverified" and entail is not None and (c.assertion or {}).get("op") in (None, "entail"):
            res = entail(c.statement, [e.excerpt or "" for e in ev.values()])
            status, quote = res[0], res[1]
            if len(res) > 2 and res[2]:  # the checker could not run (e.g. AI unavailable): say so, never imply a check
                note = res[2]
            elif status == "verified" and not any(quote_in(quote, e.excerpt) for e in ev.values()):
                status, note = "unverified", "LLM quote not found verbatim in evidence"
            else:
                note = f"LLM entailment: {status}"
        # 5. T3-only text evidence needs corroboration
        tiers = {e.tier for e in ev.values() if e.source in ("web",)}
        if status == "verified" and tiers == {3} and len(ev) < 2:
            status, note = "unverified", "only a single T3 source — needs corroboration"
            if c.severity == "high":
                c.severity = "warn"
        c.status = status
        c.verifier_notes = note
        c.supporting_quote = quote
        c.confidence = _confidence(status, ev, c.confidence)
        stats[status] += 1
    s.flush()
    return stats


def _confidence(status: str, ev: dict[str, EvidenceRow], prior: float) -> float:
    if status != "verified":
        return round(min(prior, 0.5), 2)
    best_tier = min((e.tier for e in ev.values()), default=3)
    base = {1: 0.97, 2: 0.85, 3: 0.6}[best_tier]
    return round(min(0.99, base + 0.02 * (len(ev) - 1)), 2)
