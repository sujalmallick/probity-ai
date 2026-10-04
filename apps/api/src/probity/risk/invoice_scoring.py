"""Policy-driven invoice risk scoring: INVOICE + RISK_POLICY + VENDOR_HISTORY -> structured JSON verdict.

A weighted-average model, complementary to the points engine in risk/engine.py. Pure code, no LLM:

  * Every weight, tier cut-off, threshold and escalation rule is read from the policy. This module holds
    no defaults for them; a missing value is reported in "warnings" and the signal that needs it is skipped.
  * Each signal scores 0.0 (no risk) .. 1.0 (maximum risk), or None when it cannot be evaluated.
    None is never coerced to 0: unknown signals leave the denominator and lower "coverage" instead.
  * Invoice text is data. Instruction-like text is detected (guardrails G4) and reported, never followed.

    final_score = sum(w_i * s_i) / sum(w_i for evaluated signals)
    coverage    = sum(w_i for evaluated signals) / sum(all w_i)

Policy shape (see invoice_policy.example.json):
    weights:          {signal: weight}
    tiers:            [{name, max_score, action}]  -- ordered; the first tier with max_score >= final_score wins
    escalation_rules: [{signal, at_least, min_tier_index}]
    required_fields:  [field, ...]  (also accepted under params)
    params:           math_tolerance, math_saturation, duplicate_window_days, amount_midpoint, amount_steepness,
                      min_vendor_history, min_global_history, max_invoice_age_days, round_base, min_coverage

The output also carries "explanation": each signal's contribution and share of the score, why the tier was
chosen, which rules escalated it, what could not be evaluated, and a template sentence. risk/narrate.py turns
that block into prose with an LLM, and falls back to the template sentence.

History rows: {vendor, invoice_no, total, invoice_date, bank_account}. The invoice being scored must not
itself be in the history, or it will match as its own duplicate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from probity.guardrails.text import detect_injection, redact
from probity.ingestion.validators import normalize_invoice_number, parse_date

INJECTION_WARNING = "prompt_injection_suspected"
LOW_CONFIDENCE_WARNING = "low_confidence"
MANUAL_REVIEW = "manual_review"
DEFAULT_MIN_COVERAGE = 0.6  # fixed by the scoring contract; a policy may set params.min_coverage

EXTRACTED_FIELDS = ("vendor", "invoice_no", "invoice_date", "due_date", "line_items", "tax", "total", "bank_account")
_LEGAL_SUFFIX = re.compile(r"\b(private|pvt|limited|ltd|llp|llc|inc|incorporated|corp|corporation|co|company|gmbh|plc)\b")


# ---------------------------------------------------------------- extraction


def _dec(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(re.sub(r"[,\s₹$€£]", "", v) if isinstance(v, str) else str(v))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _out_num(d: Decimal | None) -> int | float | None:
    if d is None:
        return None
    return int(d) if d == d.to_integral_value() else float(d)


def _iso(v: Any) -> str | None:
    if isinstance(v, date):
        return v.isoformat()
    d = parse_date(str(v)) if v not in (None, "") else None
    return d.isoformat() if d else None


def _from_fields(fields: dict[str, dict]) -> dict:
    """Map the labelled-field parser's output (money in minor units) to the extraction contract."""
    v = lambda k: (fields.get(k) or {}).get("value")  # noqa: E731
    minor = lambda x: None if x is None else Decimal(int(x)) / 100  # noqa: E731
    items = [
        {"desc": li.get("description"), "qty": _out_num(_dec(li.get("qty"))), "unit": _out_num(minor(li.get("unit_price_minor"))),
         "amount": _out_num(minor(li.get("amount_minor")))}
        for li in (v("line_items") or [])
    ]
    return {
        "vendor": v("vendor_name"), "invoice_no": v("invoice_number"), "invoice_date": v("invoice_date"), "due_date": v("due_date"),
        "line_items": items or None, "tax": _out_num(minor(v("tax"))), "total": _out_num(minor(v("total"))), "bank_account": v("bank_account"),
    }


def _from_dict(inv: dict, warnings: list[str]) -> dict:
    """Accept an already-extracted invoice (e.g. from an upstream extractor); copy, don't infer."""
    out: dict[str, Any] = {k: None for k in EXTRACTED_FIELDS}
    for k in ("vendor", "invoice_no", "bank_account"):
        out[k] = str(inv[k]).strip() or None if inv.get(k) not in (None, "") else None
    for k in ("invoice_date", "due_date"):
        out[k] = _iso(inv.get(k))
        if inv.get(k) not in (None, "") and out[k] is None:
            warnings.append(f"unparseable_{k}: {inv.get(k)!r} treated as null")
    for k in ("tax", "total"):
        out[k] = _out_num(_dec(inv.get(k)))
    items = []
    for li in inv.get("line_items") or []:
        if isinstance(li, dict):
            items.append({"desc": li.get("desc"), "qty": _out_num(_dec(li.get("qty"))), "unit": _out_num(_dec(li.get("unit"))),
                          "amount": _out_num(_dec(li.get("amount")))})
    out["line_items"] = items or None
    return out


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list | tuple):
        return [s for v in obj for s in _strings(v)]
    return []


def extract(invoice: str | bytes | dict, warnings: list[str]) -> tuple[dict, str]:
    """Return (extracted fields, the text that was read). Fields are copied as printed; absent -> None."""
    from probity.ingestion import parse

    if isinstance(invoice, dict):
        return _from_dict(invoice, warnings), "\n".join(_strings(invoice))
    if isinstance(invoice, bytes):
        mime = parse.sniff_mime(invoice, "")
        pages, _ocr = parse.extract(invoice, mime)
        pages = [p for p in pages if p != parse._OCR_MARK]
        tables = parse.extract_tables(invoice, mime)
    else:
        pages, tables = [invoice], []
    return _from_fields(parse.parse_fields(pages, tables)), "\n".join(pages)


# ---------------------------------------------------------------- normalisation


def _vendor_key(name: Any) -> str | None:
    if not name or not str(name).strip():
        return None
    raw = " ".join(re.sub(r"[\W_]+", " ", str(name).casefold()).split())
    stripped = " ".join(_LEGAL_SUFFIX.sub(" ", raw).split())
    return stripped or raw


def _account_key(acct: Any) -> str | None:
    k = re.sub(r"[^0-9A-Za-z]", "", str(acct or "")).upper()
    return k or None


def _money(d: Decimal | None) -> str:
    return "null" if d is None else f"{d:,.2f}"


@dataclass(frozen=True)
class HistoryRow:
    vendor: str | None
    invoice_no: str | None
    total: Decimal | None
    invoice_date: date | None
    bank: str | None  # normalised printed account
    bank_hmac: str | None = None  # opaque fingerprint, for callers that never hold the raw number
    bank_last4: str | None = None
    vendor_id: str | None = None

    @property
    def bank_label(self) -> str | None:
        last4 = self.bank_last4 or (self.bank[-4:] if self.bank else None)
        return f"account ending {last4}" if last4 else None


def _history(rows: Any, warnings: list[str]) -> list[HistoryRow]:
    if rows in (None, ""):
        return []
    if not isinstance(rows, list):
        warnings.append("invalid_history: VENDOR_HISTORY is not a list; treated as empty")
        return []
    out, bad = [], 0
    for r in rows:
        if not isinstance(r, dict):
            bad += 1
            continue
        out.append(HistoryRow(_vendor_key(r.get("vendor")), normalize_invoice_number(r.get("invoice_no")) or None, _dec(r.get("total")),
                              parse_date(_iso(r.get("invoice_date"))), _account_key(r.get("bank_account")),
                              r.get("bank_account_hmac") or None, r.get("bank_last4") or None, r.get("vendor_id") or None))
    if bad:
        warnings.append(f"invalid_history_rows: {bad} non-object row(s) ignored")
    return out


# ---------------------------------------------------------------- scoring context


@dataclass
class ScoringContext:
    """Everything a signal scorer may look at. Scorers return (score | None, evidence)."""

    signal: str
    extracted: dict
    policy: dict
    params: dict
    history: list[HistoryRow]
    vendor_history: list[HistoryRow]
    today: date
    injection_hits: list[str]
    warnings: list[str] = field(default_factory=list)
    vendor_id: str | None = None  # vendor matched by the caller (e.g. master data); overrides name matching
    invoice_bank_hmac: str | None = None
    invoice_bank_last4: str | None = None

    @property
    def vendor_known(self) -> bool:
        return bool(self.vendor_id or self.extracted.get("vendor"))

    @property
    def vendor_name(self) -> str:
        return self.extracted.get("vendor") or "this vendor"

    def num(self, name: str) -> float | None:
        """A numeric policy parameter; when missing or invalid, warn and return None (caller skips)."""
        v = self.params.get(name)
        if isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v):
            return float(v)
        self.warnings.append(f"missing_policy_value: params.{name} ({self.signal} needs it)" if v is None
                             else f"invalid_policy_value: params.{name}={v!r} ({self.signal} needs a number)")
        return None

    @property
    def total(self) -> Decimal | None:
        return _dec(self.extracted.get("total"))

    @property
    def invoice_date(self) -> date | None:
        return parse_date(self.extracted.get("invoice_date"))


Scorer = Callable[[ScoringContext], tuple[float | None, str]]


def _sentence(parts: list[str]) -> str:
    s = "; ".join(parts)
    return s[:1].upper() + s[1:] + "."


def _ramp(x: float, lo: float, hi: float) -> float:
    return 0.0 if x <= lo else 1.0 if x >= hi else (x - lo) / (hi - lo)


def _logistic(x: float, midpoint: float, steepness: float) -> float:
    t = -steepness * (x - midpoint)
    return 0.0 if t > 700 else 1.0 / (1.0 + math.exp(t))


# ---------------------------------------------------------------- signals


def math_mismatch(c: ScoringContext) -> tuple[float | None, str]:
    total, items = c.total, c.extracted.get("line_items") or []
    if total is None:
        return None, "No total on the invoice to reconcile against."
    if not items:
        return None, "No line items extracted, so the total cannot be reconciled."
    tol, sat = c.num("math_tolerance"), c.num("math_saturation")
    if tol is None or sat is None:
        return None, "Skipped: math tolerance/saturation missing from policy."
    if sat <= tol:
        c.warnings.append(f"invalid_policy_value: params.math_saturation ({sat}) must exceed params.math_tolerance ({tol})")
        return None, "Skipped: invalid math tolerance/saturation in policy."

    worst, worst_note = 0.0, ""
    amounts: list[Decimal] = []
    for i, li in enumerate(items, start=1):
        q, u, a = _dec(li.get("qty")), _dec(li.get("unit")), _dec(li.get("amount"))
        if a is not None:
            amounts.append(a)
        if q is None or u is None or a is None:
            continue
        expected = q * u
        rel = float(abs(expected - a) / max(abs(a), abs(expected), Decimal("0.01")))
        if rel > worst:
            worst, worst_note = rel, f"line {i} '{li.get('desc')}': {q} x {_money(u)} = {_money(expected)} but printed {_money(a)}"

    tax = _dec(c.extracted.get("tax"))
    if len(amounts) == len(items):
        lines = sum(amounts, Decimal(0))
        expected_total = lines + (tax or 0)
        rel = float(abs(expected_total - total) / max(abs(total), Decimal("0.01")))
        tax_txt = f"tax {_money(tax)}" if tax is not None else "no tax printed"
        if rel > worst or not worst_note:
            worst_note = (f"lines {_money(lines)} + {tax_txt} = {_money(expected_total)} vs printed total {_money(total)}")
            worst = max(worst, rel)
    elif not worst_note:
        return None, "Some line amounts are missing, so lines cannot be summed to the total."

    s = _ramp(worst, tol, sat)
    if worst <= tol:
        return s, f"Reconciles within tolerance: {worst_note} (relative error {worst:.4%})."
    return s, f"Mismatch: {worst_note} (relative error {worst:.4%})."


def duplicate_invoice(c: ScoringContext) -> tuple[float | None, str]:
    vendor, inv_no = c.vendor_name, normalize_invoice_number(c.extracted.get("invoice_no")) or None
    if not c.vendor_known:
        return None, "Vendor not extracted, so history cannot be searched for duplicates."
    if inv_no:
        for h in c.vendor_history:
            if h.invoice_no == inv_no:
                return 1.0, f"Invoice no {c.extracted['invoice_no']} already in history for {vendor} (dated {h.invoice_date}, total {_money(h.total)})."
    total, inv_date = c.total, c.invoice_date
    if total is None or inv_date is None:
        missing = "invoice no, " if not inv_no else ""
        return None, f"Cannot rule out a duplicate: {missing}{'total' if total is None else 'invoice date'} missing for the amount-and-date check."
    window = c.num("duplicate_window_days")
    if window is None:
        return None, "Invoice number not seen before; same-amount check skipped (window missing from policy)."
    for h in c.vendor_history:
        if h.total == total and h.invoice_date is not None and abs((h.invoice_date - inv_date).days) <= window:
            return 0.7, (f"Same total {_money(total)} as {vendor} invoice {h.invoice_no or '(no number)'} dated {h.invoice_date}, "
                         f"{abs((h.invoice_date - inv_date).days)} day(s) from {inv_date} (window {window:g} days).")
    return 0.0, f"No prior {vendor} invoice numbered {c.extracted.get('invoice_no')} or totalling {_money(total)} within {window:g} days ({len(c.vendor_history)} on file)."


def amount_anomaly(c: ScoringContext) -> tuple[float | None, str]:
    total = c.total
    if total is None:
        return None, "No total on the invoice."
    mid, steep, min_v = c.num("amount_midpoint"), c.num("amount_steepness"), c.num("min_vendor_history")
    if mid is None or steep is None or min_v is None:
        return None, "Skipped: amount anomaly parameters missing from policy."
    vendor_totals = [h.total for h in c.vendor_history if h.total is not None]
    if len(vendor_totals) >= min_v:
        base, label = vendor_totals, "vendor"
    else:
        min_g = c.num("min_global_history")
        global_totals = [h.total for h in c.history if h.total is not None]
        if min_g is None:
            return None, f"Vendor history too short (n={len(vendor_totals)}) and global fallback threshold missing from policy."
        if len(global_totals) < min_g:
            return None, (f"Insufficient history: vendor n={len(vendor_totals)} (need {min_v:g}), "
                          f"global n={len(global_totals)} (need {min_g:g}).")
        base, label = global_totals, "global (vendor history too short)"

    x = float(total)
    vals = [float(v) for v in base]
    med = statistics.median(vals)
    dev = [abs(v - med) for v in vals]
    mad = statistics.median(dev)
    if mad > 0:
        z = 0.6745 * (x - med) / mad
        spread = f"MAD {mad:,.2f}"
    else:  # over half the history is identical: fall back to mean absolute deviation
        mean_ad = statistics.fmean(dev)
        z = (x - med) / (1.2533 * mean_ad) if mean_ad > 0 else (0.0 if x == med else math.copysign(math.inf, x - med))
        spread = f"MAD 0, mean abs dev {mean_ad:,.2f}"
    s = 1.0 if math.isinf(z) else _logistic(abs(z), mid, steep)
    z_txt = "inf" if math.isinf(z) else f"{z:+.2f}"
    return s, f"Total {_money(total)} vs {label} median {med:,.2f} ({spread}, n={len(vals)}): robust z = {z_txt}."


def bank_change(c: ScoringContext) -> tuple[float | None, str]:
    fp = c.invoice_bank_hmac
    acct = None if fp else _account_key(c.extracted.get("bank_account"))  # a fingerprint is authoritative
    if acct is not None and not any(ch.isdigit() for ch in acct):
        acct = None  # a fully masked value ("XXXX") is not an account number
    if acct is None and fp is None:
        return None, "No bank account printed on the invoice."
    if not c.vendor_known:
        return None, "Vendor not extracted, so known accounts cannot be looked up."
    rows = [h for h in c.vendor_history if (fp and h.bank_hmac) or (acct and h.bank)]
    if not rows:
        return None, f"No comparable bank account on file for {c.vendor_name}."
    label = f"account ending {c.invoice_bank_last4 or (acct or '')[-4:]}"
    known_txt = ", ".join(sorted({h.bank_label for h in rows if h.bank_label})) or "fingerprinted account(s)"
    if any((fp and h.bank_hmac == fp) or (acct and h.bank == acct) for h in rows):
        return 0.0, f"Invoice {label} matches a known account ({known_txt})."
    return 1.0, f"Invoice {label} differs from known account(s): {known_txt}."


def missing_fields(c: ScoringContext) -> tuple[float | None, str]:
    req = c.policy.get("required_fields", c.params.get("required_fields"))
    if not isinstance(req, list) or not req:
        c.warnings.append("missing_policy_value: required_fields (missing_fields needs it)")
        return None, "Skipped: required_fields missing from policy."
    unknown = [f for f in req if f not in EXTRACTED_FIELDS]
    if unknown:
        c.warnings.append(f"unknown_required_fields: {unknown} are not extracted fields and were ignored")
    req = [f for f in req if f in EXTRACTED_FIELDS]
    if not req:
        return None, "Skipped: none of the policy's required_fields are extractable."
    absent = [f for f in req if c.extracted.get(f) in (None, [], "")]
    s = len(absent) / len(req)
    return s, f"{len(absent)} of {len(req)} required fields null" + (f": {', '.join(absent)}." if absent else ".")


def date_anomaly(c: ScoringContext) -> tuple[float | None, str]:
    inv_date = c.invoice_date
    if inv_date is None:
        return None, "Invoice date not extracted."
    reasons = []
    if inv_date > c.today:
        reasons.append(f"invoice date {inv_date} is after today {c.today}")
    due = parse_date(c.extracted.get("due_date"))
    if due is not None and due < inv_date:
        reasons.append(f"due date {due} is before invoice date {inv_date}")
    max_age = c.num("max_invoice_age_days")
    if max_age is None:
        # a hard anomaly already found is conclusive; otherwise the age check is unresolved
        return (1.0, _sentence(reasons)) if reasons else (None, "Age check skipped: max_invoice_age_days missing from policy.")
    age = (c.today - inv_date).days
    if age > max_age:
        reasons.append(f"invoice is {age} days old (limit {max_age:g})")
    if reasons:
        return 1.0, _sentence(reasons)
    return 0.0, f"Invoice date {inv_date} is {age} day(s) old" + (f", due {due}." if due else ", no due date.")


def new_vendor(c: ScoringContext) -> tuple[float | None, str]:
    vendor = c.vendor_name
    if not c.vendor_known:
        return None, "Vendor not extracted."
    if c.vendor_history:
        return 0.0, f"{vendor} has {len(c.vendor_history)} prior invoice(s) on file."
    return 1.0, f"{vendor} does not appear in history ({len(c.history)} invoice(s) from other vendors)."


def round_amount(c: ScoringContext) -> tuple[float | None, str]:
    total = c.total
    if total is None:
        return None, "No total on the invoice."
    base = c.num("round_base")
    if base is None:
        return None, "Skipped: round_base missing from policy."
    if base <= 0:
        c.warnings.append(f"invalid_policy_value: params.round_base={base:g} must be positive")
        return None, "Skipped: invalid round_base in policy."
    b = _dec(base)
    if total != 0 and total % b == 0:
        return 1.0, f"Total {_money(total)} is an exact multiple of {base:g}."
    return 0.0, f"Total {_money(total)} is not a multiple of {base:g}."


def prompt_injection(c: ScoringContext) -> tuple[float | None, str]:
    if c.injection_hits:
        # the quoted context can include (fragments of) account numbers
        quote = re.sub(r"\d{5,}", "<digits>", " ".join(redact(c.injection_hits[0]).text.split()))[:160]
        return 1.0, f"Instruction-like text in invoice: \"{quote}\"."
    return 0.0, "No instruction-like text found in the invoice."


SCORERS: dict[str, Scorer] = {
    "math_mismatch": math_mismatch,
    "duplicate_invoice": duplicate_invoice,
    "amount_anomaly": amount_anomaly,
    "bank_change": bank_change,
    "missing_fields": missing_fields,
    "date_anomaly": date_anomaly,
    "new_vendor": new_vendor,
    "round_amount": round_amount,
    "prompt_injection": prompt_injection,
}


# ---------------------------------------------------------------- aggregation


def _tiers(policy: dict, warnings: list[str]) -> list[dict] | None:
    tiers = policy.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        warnings.append("missing_policy_value: tiers")
        return None
    for i, t in enumerate(tiers):
        mx = t.get("max_score") if isinstance(t, dict) else None
        if not isinstance(t, dict) or not t.get("name") or not isinstance(mx, int | float) or isinstance(mx, bool):
            warnings.append(f"invalid_policy_value: tiers[{i}] needs a name and a numeric max_score; tiering skipped")
            return None
    return tiers


def _tier_index(tiers: list[dict], final: float, warnings: list[str]) -> int:
    for i, t in enumerate(tiers):
        if t["max_score"] >= final:
            return i
    warnings.append(f"final_score {final} exceeds every tier max_score; using the last tier")
    return len(tiers) - 1


def _escalate(
    policy: dict, tiers: list[dict], idx: int, scores: dict[str, float | None], warnings: list[str]
) -> tuple[int, list[str], list[dict]]:
    """Apply escalation rules. Returns (tier index, signals that raised it, the rules that fired)."""
    rules = policy.get("escalation_rules") or []
    if not isinstance(rules, list):
        warnings.append("invalid_policy_value: escalation_rules is not a list")
        return idx, [], []
    base, by, fired = idx, [], []
    for i, r in enumerate(rules):
        sig = r.get("signal") if isinstance(r, dict) else None
        at_least = r.get("at_least") if isinstance(r, dict) else None
        min_idx = r.get("min_tier_index") if isinstance(r, dict) else None
        if not sig or not isinstance(at_least, int | float) or not isinstance(min_idx, int) or isinstance(min_idx, bool):
            warnings.append(f"invalid_policy_value: escalation_rules[{i}] needs signal, at_least and integer min_tier_index")
            continue
        if not 0 <= min_idx < len(tiers):
            warnings.append(f"invalid_policy_value: escalation_rules[{i}].min_tier_index {min_idx} is outside the {len(tiers)} tiers")
            continue
        if sig not in scores:
            warnings.append(f"escalation_rules[{i}] references signal '{sig}' that has no weight in the policy")
            continue
        s = scores[sig]
        if s is not None and s >= at_least and min_idx > base:
            idx = max(idx, min_idx)
            if sig not in by:
                by.append(sig)
            fired.append({"signal": sig, "score": s, "at_least": at_least, "min_tier": tiers[min_idx]["name"]})
    return idx, by, fired


def validate_policy(policy: Any) -> list[str]:
    """Structural problems that would make a policy unusable; empty means it can be stored.

    Scoring itself tolerates a partial policy (it warns and skips), so this only guards what an owner saves.
    """
    if not isinstance(policy, dict):
        return ["policy must be a JSON object"]
    problems = []
    if len(json.dumps(policy)) > 20_000:
        problems.append("policy is larger than 20 KB")
    weights = policy.get("weights")
    if not isinstance(weights, dict) or not weights:
        problems.append("weights must be a non-empty object of signal -> weight")
        weights = {}
    for k, w in weights.items():
        if not isinstance(w, int | float) or isinstance(w, bool) or not math.isfinite(w) or w < 0:
            problems.append(f"weights.{k} must be a non-negative number")
    if weights and not any(isinstance(w, int | float) and not isinstance(w, bool) and w > 0 for w in weights.values()):
        problems.append("at least one weight must be positive")
    tiers = policy.get("tiers")
    if _tiers(policy, []) is None:
        problems.append("tiers must be a non-empty list of {name, max_score, action}")
        tiers = []
    names = [t["name"] for t in tiers]
    if len(set(names)) != len(names):
        problems.append("tier names must be unique")
    for i, t in enumerate(tiers):
        if not t.get("action"):
            problems.append(f"tiers[{i}].action is missing")
    rules = policy.get("escalation_rules", [])
    if not isinstance(rules, list):
        problems.append("escalation_rules must be a list")
        rules = []
    for i, r in enumerate(rules):
        ok = isinstance(r, dict) and r.get("signal") in weights and isinstance(r.get("at_least"), int | float)             and isinstance(r.get("min_tier_index"), int) and not isinstance(r.get("min_tier_index"), bool) and 0 <= r["min_tier_index"] < len(tiers)
        if not ok:
            problems.append(f"escalation_rules[{i}] needs a weighted signal, numeric at_least and a min_tier_index within the tiers")
    if "params" in policy and not isinstance(policy["params"], dict):
        problems.append("params must be an object")
    req = policy.get("required_fields", (policy.get("params") or {}).get("required_fields") if isinstance(policy.get("params"), dict) else None)
    if req is not None and (not isinstance(req, list) or any(f not in EXTRACTED_FIELDS for f in req)):
        problems.append(f"required_fields must be a list drawn from {list(EXTRACTED_FIELDS)}")
    return problems


# ---------------------------------------------------------------- explanation
#
# The "why" behind a score, derived only from the numbers above. Contributions are normalised by the evaluated
# weight, so they sum to final_score exactly as the aggregation formula does. An LLM may rephrase this block
# (risk/narrate.py) but is never its source.

SIGNAL_LABELS = {
    "math_mismatch": "arithmetic mismatch",
    "duplicate_invoice": "possible duplicate",
    "amount_anomaly": "unusual amount",
    "bank_change": "bank account change",
    "missing_fields": "missing fields",
    "date_anomaly": "date anomaly",
    "new_vendor": "new vendor",
    "round_amount": "round amount",
    "prompt_injection": "instruction-like text in invoice",
}


def _label(signal: str) -> str:
    return SIGNAL_LABELS.get(signal, signal.replace("_", " "))


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def _explain(signals: list[dict], final: float | None, coverage: float, tiers: list[dict] | None, base_idx: int | None,
             idx: int | None, fired: list[dict], min_cov: float, action: str) -> dict:
    eval_w = sum(s["weight"] for s in signals if s["score"] is not None)
    contributions = []
    for s in signals:
        if s["score"] is None:
            continue
        c = s["weight"] * s["score"] / eval_w if eval_w else 0.0
        contributions.append({"signal": s["signal"], "label": _label(s["signal"]), "weight": s["weight"], "score": s["score"],
                              "contribution": round(c, 4), "share": round(c / final, 4) if final else 0.0, "evidence": s["evidence"]})
    contributions.sort(key=lambda c: (-c["contribution"], c["signal"]))
    drivers = [c for c in contributions if c["contribution"] > 0][:3]
    unknown = [{"signal": s["signal"], "label": _label(s["signal"]), "weight": s["weight"], "reason": s["evidence"]}
               for s in signals if s["score"] is None]

    tier_reason = None
    if final is not None and tiers and base_idx is not None:
        t = tiers[base_idx]
        below = f"above {tiers[base_idx - 1]['name']} (max {tiers[base_idx - 1]['max_score']:g}) and " if base_idx > 0 else ""
        tier_reason = f"final_score {final:g} is {below}within {t['name']} (max {t['max_score']:g})"
    escalations = [f"{_label(f['signal'])} scored {f['score']:g} (rule: at least {f['at_least']:g}), so the tier is at least {f['min_tier']}"
                   for f in fired]

    parts = []
    if final is None:
        parts.append("No signal could be evaluated, so there is no score")
    else:
        head = f"Score {final:g}" + (f" ({tiers[idx]['name']})" if tiers and idx is not None else "")
        if drivers:
            head += ": driven by " + ", ".join(f"{d['label']} ({_pct(d['share'])})" for d in drivers)
        else:
            head += ": no risk indicator scored above zero"
        parts.append(head)
    parts.extend(e[:1].upper() + e[1:] for e in escalations)
    if unknown:
        parts.append(f"{len(unknown)} signal(s) could not be evaluated ({', '.join(u['label'] for u in unknown)}); coverage {coverage:g}")
    if coverage < min_cov:
        parts.append(f"Coverage is below {min_cov:g}, so manual review is required")
    return {
        "contributions": contributions,
        "top_drivers": [d["signal"] for d in drivers],
        "tier_reason": tier_reason,
        "escalations": escalations,
        "unknown": unknown,
        "recommended_action": action,
        "text": ". ".join(parts) + ".",
    }


def score_invoice(
    invoice: str | bytes | dict,
    policy: dict,
    history: list[dict] | None,
    *,
    today: date | None = None,
    extra_scorers: dict[str, Scorer] | None = None,
    vendor_id: str | None = None,
    scan_text: str | None = None,
) -> dict:
    """Score one invoice. `invoice` is raw text, document bytes (PDF/image/EML), or an extracted-field dict.

    Callers holding master data may pass `vendor_id` (history rows then match on their `vendor_id`, not the
    printed name), fingerprinted accounts (`bank_account_hmac` + `bank_last4` on the invoice dict and history
    rows), and `scan_text` (further document text to check for instruction-like content).
    """
    today = today or date.today()
    warnings: list[str] = []
    policy = policy if isinstance(policy, dict) else {}
    params = policy.get("params") if isinstance(policy.get("params"), dict) else {}

    extracted, text = extract(invoice, warnings)
    hits = detect_injection("\n".join([text, scan_text or ""]))
    if hits:
        warnings.append(INJECTION_WARNING)
    rows = _history(history, warnings)
    vkey = _vendor_key(extracted.get("vendor"))
    vendor_rows = [h for h in rows if h.vendor_id == vendor_id] if vendor_id else [h for h in rows if vkey and h.vendor == vkey]
    bank_fp = invoice.get("bank_account_hmac") if isinstance(invoice, dict) else None
    bank_l4 = invoice.get("bank_last4") if isinstance(invoice, dict) else None

    weights = policy.get("weights")
    if not isinstance(weights, dict) or not weights:
        warnings.append("missing_policy_value: weights")
        weights = {}
    scorers = {**SCORERS, **(extra_scorers or {})}

    signals: list[dict] = []
    for name, w in weights.items():
        if not isinstance(w, int | float) or isinstance(w, bool) or not math.isfinite(w) or w < 0:
            warnings.append(f"invalid_policy_value: weights.{name}={w!r}; signal skipped")
            continue
        fn = scorers.get(name)
        if fn is None:
            score, evidence = None, "No deterministic scorer is registered for this signal."
            warnings.append(f"unscored_signal: '{name}' has no scorer; skipped")
        else:
            ctx = ScoringContext(name, extracted, policy, params, rows, vendor_rows, today, hits,
                                 vendor_id=vendor_id, invoice_bank_hmac=bank_fp, invoice_bank_last4=bank_l4)
            score, evidence = fn(ctx)
            warnings.extend(ctx.warnings)
            if score is not None:
                score = round(min(1.0, max(0.0, float(score))), 4)
        signals.append({"signal": name, "weight": w, "score": score, "evidence": evidence})

    total_w = sum(s["weight"] for s in signals)
    eval_w = sum(s["weight"] for s in signals if s["score"] is not None)
    final = round(sum(s["weight"] * s["score"] for s in signals if s["score"] is not None) / eval_w, 4) if eval_w > 0 else None
    coverage = round(eval_w / total_w, 4) if total_w > 0 else 0.0
    if signals and total_w == 0:
        warnings.append("invalid_policy_value: all weights are zero")

    tier, escalated_by, action, base_idx, idx, fired = None, [], None, None, None, []
    tiers = _tiers(policy, warnings)
    if final is None:
        warnings.append("no_signals_evaluated: final_score is null")
    elif tiers:
        base_idx = _tier_index(tiers, final, warnings)
        idx, escalated_by, fired = _escalate(policy, tiers, base_idx, {s["signal"]: s["score"] for s in signals}, warnings)
        tier = tiers[idx]["name"]
        action = tiers[idx].get("action")
        if not action:
            warnings.append(f"missing_policy_value: tiers[{idx}].action")

    min_cov = params.get("min_coverage", DEFAULT_MIN_COVERAGE)
    if not isinstance(min_cov, int | float) or isinstance(min_cov, bool):
        warnings.append(f"invalid_policy_value: params.min_coverage={min_cov!r}; using {DEFAULT_MIN_COVERAGE}")
        min_cov = DEFAULT_MIN_COVERAGE
    if coverage < min_cov:
        warnings.append(LOW_CONFIDENCE_WARNING)
    if coverage < min_cov or not action:
        action = MANUAL_REVIEW

    return {
        "extracted": extracted,
        "signals": signals,
        "final_score": final,
        "coverage": coverage,
        "tier": tier,
        "escalated_by": escalated_by,
        "recommended_action": action,
        "warnings": list(dict.fromkeys(warnings)),
        "explanation": _explain(signals, final, coverage, tiers, base_idx, idx, fired, min_cov, action),
    }


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m probity.risk.invoice_scoring", description="Score one invoice against a risk policy.")
    ap.add_argument("--invoice", required=True, help="invoice file: PDF, image, EML, .txt, or .json of extracted fields")
    ap.add_argument("--policy", required=True, help="RISK_POLICY json file")
    ap.add_argument("--history", help="VENDOR_HISTORY json file (list of past invoices)")
    ap.add_argument("--today", help="reference date YYYY-MM-DD (default: system date)")
    ap.add_argument("--narrate", action="store_true", help="add a plain-language 'narrative' written by the LLM (template fallback)")
    a = ap.parse_args(argv)

    path = Path(a.invoice)
    invoice: str | bytes | dict = json.loads(path.read_text("utf-8")) if path.suffix.lower() == ".json" else path.read_bytes()
    policy = json.loads(Path(a.policy).read_text("utf-8"))
    history = json.loads(Path(a.history).read_text("utf-8")) if a.history else []
    today = date.fromisoformat(a.today) if a.today else None
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    out = score_invoice(invoice, policy, history, today=today)
    if a.narrate:
        from probity.risk.narrate import narrate

        out["narrative"] = narrate(out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
