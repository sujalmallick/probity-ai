"""Deterministic validators and canonicalizers (Feature F1, Guardrails G10). No LLM involvement."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
# Bounded and anchored on the left so scanning untrusted text is linear: the unbounded form
# [\w.+-]+@[\w-]+(?:\.[\w-]+)+ takes seconds to minutes on inputs like "a." * 20000 + "@".
EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,10}")


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        val = GSTIN_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += val // 36 + val % 36
    return GSTIN_CHARS[(36 - total % 36) % 36]


def valid_gstin(gstin: str | None) -> bool:
    if not gstin:
        return False
    g = gstin.strip().upper()
    return bool(GSTIN_RE.match(g)) and gstin_check_char(g[:14]) == g[14]


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    first14 = f"{state}{pan}{entity}Z"
    return first14 + gstin_check_char(first14)


def valid_ifsc(ifsc: str | None) -> bool:
    return bool(ifsc and IFSC_RE.match(ifsc.strip().upper()))


def parse_money_minor(raw: str | int | float | None) -> int | None:
    """Parse '₹4,85,000.00', 'INR 4,85,000', 'Rs. 590' → paise. Indian or western grouping."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw * 100
    s = re.sub(r"(?i)(inr|rs\.?|₹|\s)", "", str(raw))
    s = s.replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", s):  # plain decimal only: no exponent ("1e9"), NaN or Infinity
        return None
    try:
        return int((Decimal(s) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return None


def format_inr(minor: int | None) -> str:
    """Indian grouping: 56640000 → '₹5,66,400'."""
    if minor is None:
        return "—"
    rupees, paise = divmod(abs(int(minor)), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    sign = "-" if minor < 0 else ""
    return f"{sign}₹{s}" + (f".{paise:02d}" if paise else "")


def parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", raw).replace(",", " ").strip()  # 3rd Oct 2026 -> 3 Oct 2026
    raw = re.sub(r"\s+", " ", raw)
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d-%B-%Y",
                "%d/%b/%Y", "%Y/%m/%d", "%d-%b-%y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def normalize_invoice_number(raw: str | None) -> str:
    return re.sub(r"[^0-9A-Z]", "", (raw or "").upper()).lstrip("0")


def normalize_domain(email_or_url: str | None) -> str | None:
    if not email_or_url:
        return None
    v = email_or_url.strip().lower()
    if "@" in v:
        v = v.split("@", 1)[1]
    v = re.sub(r"^https?://", "", v).split("/", 1)[0]
    return v.removeprefix("www.") or None


def check_arithmetic(line_items: list[dict], subtotal: int | None, tax: int | None, total: int | None) -> list[dict]:
    """Return a list of arithmetic findings; empty means consistent."""
    issues = []
    tolerance = 100  # ₹1: invoices round totals ("Round Off") and per-line amounts
    if line_items and subtotal is not None:
        computed = round(sum(float(li["qty"]) * int(li["unit_price_minor"]) for li in line_items))
        if abs(computed - subtotal) > max(tolerance, len(line_items) * 100):
            issues.append({"check": "line_items_sum", "expected": computed, "printed": subtotal})
    if subtotal is not None and tax is not None and total is not None and abs(subtotal + tax - total) > tolerance:
        issues.append({"check": "subtotal_plus_tax", "expected": subtotal + tax, "printed": total})
    return issues


def validate_extraction(fields: dict, today: date) -> dict:
    """Run all deterministic checks over extracted fields. Returns {check: {ok, detail}}."""
    v = lambda k: (fields.get(k) or {}).get("value")  # noqa: E731
    results: dict[str, dict] = {}
    gst = v("gstin")
    results["gstin_checksum"] = {"ok": valid_gstin(gst) if gst else None, "detail": gst}
    ifsc = v("ifsc")
    results["ifsc_format"] = {"ok": valid_ifsc(ifsc) if ifsc else None, "detail": ifsc}
    arith = check_arithmetic(v("line_items") or [], v("subtotal"), v("tax"), v("total"))
    results["arithmetic"] = {"ok": not arith, "detail": arith}
    inv_date = parse_date(v("invoice_date"))
    due = parse_date(v("due_date"))
    date_ok = inv_date is not None and inv_date <= today and (due is None or due >= inv_date)
    results["dates"] = {"ok": date_ok, "detail": {"invoice_date": str(inv_date), "due_date": str(due)}}
    return results
