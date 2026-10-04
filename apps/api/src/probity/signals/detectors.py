"""Deterministic signal detectors (Feature F5). Pure functions: no I/O, no LLM, no randomness.

Each detector returns a SignalResult carrying the *structured facts* it compared (observed vs baseline),
which the Transaction Analyst turns into Evidence + Claims.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from rapidfuzz import fuzz


@dataclass(frozen=True)
class SignalResult:
    signal: str
    fired: bool
    severity: str  # info | warn | high
    value: Any = None
    baseline: Any = None
    detail: dict[str, Any] = field(default_factory=dict)
    skipped: str | None = None  # reason when the check could not run


@dataclass(frozen=True)
class KnownAccount:
    last4: str
    hmac: str
    verified: bool


@dataclass(frozen=True)
class PastInvoice:
    invoice_number_norm: str
    invoice_date: date
    total_minor: int
    bank_hmac: str | None
    line_items: tuple[tuple[str, int, int], ...]  # (description, qty, unit_price_minor)


def bank_account_changed(invoice_hmac: str | None, invoice_last4: str | None, known: list[KnownAccount]) -> SignalResult:
    if not invoice_hmac:
        return SignalResult("bank_account_changed", False, "info", skipped="no bank account on invoice")
    verified = [a for a in known if a.verified]
    if not known:
        return SignalResult("bank_account_changed", False, "info", skipped="no bank history for vendor")
    if any(a.hmac == invoice_hmac for a in verified):
        return SignalResult("bank_account_changed", False, "info", value=invoice_last4, baseline=[a.last4 for a in verified])
    return SignalResult(
        "bank_account_changed",
        True,
        "high",
        value=invoice_last4,
        baseline=[a.last4 for a in verified] or [a.last4 for a in known],
        detail={"seen_unverified": any(a.hmac == invoice_hmac for a in known)},
    )


def duplicate_invoice(
    invoice_number_norm: str, total_minor: int | None, invoice_date: date | None, history: list[PastInvoice], window_days: int = 10
) -> SignalResult:
    for h in history:
        if invoice_number_norm and h.invoice_number_norm == invoice_number_norm:
            return SignalResult("duplicate_invoice", True, "high", value=invoice_number_norm, baseline=h.invoice_number_norm, detail={"match": "invoice_number", "date": str(h.invoice_date)})
    if total_minor is not None and invoice_date is not None:
        for h in history:
            if h.total_minor == total_minor and abs((h.invoice_date - invoice_date).days) <= window_days:
                return SignalResult("duplicate_invoice", True, "high", value=total_minor, baseline=h.total_minor, detail={"match": "amount_and_date", "date": str(h.invoice_date)})
    return SignalResult("duplicate_invoice", False, "info")


def price_anomaly(
    line_items: list[tuple[str, int, int]], history: list[PastInvoice], pct_threshold: float = 25.0, z_threshold: float = 2.5, min_points: int = 3
) -> SignalResult:
    """Unit price vs the vendor's historical average for the same item (fuzzy description match)."""
    worst: SignalResult | None = None
    compared = 0
    for desc, _qty, price in line_items:
        past = [p for h in history for (d, _q, p) in h.line_items if fuzz.token_set_ratio(d.lower(), desc.lower()) >= 85]
        if len(past) < min_points:
            continue
        compared += 1
        avg = statistics.fmean(past)
        sd = statistics.pstdev(past)
        pct = (price - avg) / avg * 100 if avg else 0.0
        z = (price - avg) / sd if sd else (float("inf") if price != avg else 0.0)
        fired = pct >= pct_threshold and z >= z_threshold
        res = SignalResult(
            "price_anomaly",
            fired,
            "high" if fired else "info",
            value=price,
            baseline=round(avg),
            detail={"item": desc, "pct_change": round(pct, 1), "z_score": None if z == float("inf") else round(z, 2), "history_points": len(past)},
        )
        if worst is None or (res.fired and not worst.fired) or res.detail["pct_change"] > worst.detail["pct_change"]:
            worst = res
    if worst is None:
        return SignalResult("price_anomaly", False, "info", skipped="insufficient price history" if not compared else None)
    return worst


def new_domain(domain: str | None, age_days: int | None, verified_domains: list[str], max_age_days: int = 90) -> SignalResult:
    if not domain:
        return SignalResult("new_domain", False, "info", skipped="no sender domain on invoice")
    if domain in verified_domains:
        return SignalResult("new_domain", False, "info", value=domain, baseline=verified_domains, detail={"verified": True})
    if age_days is None:
        return SignalResult("new_domain", False, "info", value=domain, skipped="domain registration date unavailable")
    fired = age_days < max_age_days
    return SignalResult("new_domain", fired, "warn" if fired else "info", value=age_days, baseline=max_age_days, detail={"domain": domain, "verified_domains": verified_domains})


def identity_mismatch(invoice_gstin: str | None, master_gstin: str | None, invoice_name: str | None, master_name: str | None) -> SignalResult:
    if invoice_gstin and master_gstin and invoice_gstin.upper() != master_gstin.upper():
        return SignalResult("identity_mismatch", True, "high", value=invoice_gstin, baseline=master_gstin, detail={"field": "gstin"})
    if invoice_name and master_name:
        score = fuzz.token_set_ratio(invoice_name.lower(), master_name.lower())
        if score < 80:
            return SignalResult("identity_mismatch", True, "warn", value=invoice_name, baseline=master_name, detail={"field": "vendor_name", "similarity": score})
    if not master_gstin and not master_name:
        return SignalResult("identity_mismatch", False, "info", skipped="vendor not in master data")
    return SignalResult("identity_mismatch", False, "info", value=invoice_gstin, baseline=master_gstin)


def address_mismatch(invoice_addr: str | None, master_addr: str | None, threshold: int = 70) -> SignalResult:
    if not invoice_addr or not master_addr:
        return SignalResult("address_mismatch", False, "info", skipped="address unavailable")
    score = fuzz.token_set_ratio(invoice_addr.lower(), master_addr.lower())
    return SignalResult("address_mismatch", score < threshold, "warn" if score < threshold else "info", value=invoice_addr, baseline=master_addr, detail={"similarity": score})


def missing_po(po_number: str | None, po_found: bool) -> SignalResult:
    if not po_number:
        return SignalResult("missing_po", True, "warn", value=None, baseline="PO required", detail={"reason": "no PO number on invoice"})
    if not po_found:
        return SignalResult("missing_po", True, "warn", value=po_number, baseline=None, detail={"reason": "PO number not found in records"})
    return SignalResult("missing_po", False, "info", value=po_number)


def quantity_po_mismatch(line_items: list[tuple[str, int, int]], po_lines: list[tuple[str, int, int]] | None) -> SignalResult:
    if not po_lines:
        return SignalResult("quantity_po_mismatch", False, "info", skipped="no PO lines")
    for desc, qty, _ in line_items:
        match = max(po_lines, key=lambda pl: fuzz.token_set_ratio(pl[0].lower(), desc.lower()))
        if fuzz.token_set_ratio(match[0].lower(), desc.lower()) >= 85 and qty > match[1]:
            return SignalResult("quantity_po_mismatch", True, "warn", value=qty, baseline=match[1], detail={"item": desc})
    return SignalResult("quantity_po_mismatch", False, "info")


def temporal_anomaly(invoice_date: date | None, po_date: date | None, today: date) -> SignalResult:
    if invoice_date and invoice_date > today:
        return SignalResult("temporal_anomaly", True, "warn", value=str(invoice_date), baseline=str(today), detail={"reason": "future-dated invoice"})
    if invoice_date and po_date and invoice_date < po_date:
        return SignalResult("temporal_anomaly", True, "warn", value=str(invoice_date), baseline=str(po_date), detail={"reason": "invoice dated before PO"})
    return SignalResult("temporal_anomaly", False, "info")


def round_sum(total_minor: int | None) -> SignalResult:
    """Informational only (0 points): exact round lakhs are weakly associated with fabricated invoices."""
    if total_minor is None:
        return SignalResult("round_sum", False, "info")
    fired = total_minor >= 100_000_00 and total_minor % 100_000_00 == 0
    return SignalResult("round_sum", fired, "info", value=total_minor)


def no_history(history_count: int) -> SignalResult:
    return SignalResult("no_history", history_count == 0, "info", value=history_count, baseline=1)
