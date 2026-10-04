"""Document text extraction + deterministic labelled-field parsing.

Pipeline (Feature F1): classify by magic bytes → text (pdfplumber / EML / text) → OCR fallback → fields.
The parser copies values exactly as printed and records the snippet each value came from; the LLM is
never the source of truth for numbers.
"""

from __future__ import annotations

import email
import hashlib
import io
import re
from email import policy
from typing import Any

from probity.ingestion.validators import normalize_domain, parse_money_minor

MAX_BYTES = 15 * 1024 * 1024
MAX_PAGES = 50


class UnsupportedDocument(ValueError):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_mime(data: bytes, filename: str) -> str:
    """Magic bytes, not extension (Security.md §6)."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    head = data[:2048].decode("utf-8", errors="ignore").lower()
    if filename.lower().endswith(".eml") or ("from:" in head and "subject:" in head):
        return "message/rfc822"
    if all(32 <= b < 127 or b in (9, 10, 13) for b in data[:2048]) or _is_utf8(data[:2048]):
        return "text/plain"
    raise UnsupportedDocument("Unsupported file type")


def _is_utf8(b: bytes) -> bool:
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def email_meta(data: bytes) -> dict:
    """Envelope facts that matter for risk: who actually sent the email (not what the invoice claims)."""
    msg = email.message_from_bytes(data, policy=policy.default)
    m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", str(msg.get("from") or ""))
    dkim = re.search(r"dkim=(\w+)", str(msg.get("authentication-results") or ""))
    return {"from": m.group(0).lower() if m else None, "subject": str(msg.get("subject") or ""), "dkim": dkim.group(1).lower() if dkim else None}


def email_attachment_pdf(data: bytes) -> bytes | None:
    msg = email.message_from_bytes(data, policy=policy.default)
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        if part.get_content_type() == "application/pdf" or payload.startswith(b"%PDF-"):
            return payload
    return None


def extract(data: bytes, mime: str) -> tuple[list[str], bool]:
    """Return (text per page, used_ocr)."""
    if mime.startswith("image/"):
        return _ocr_image(data), True
    pages = extract_text(data, mime)
    return pages, mime == "application/pdf" and _OCR_MARK in pages[:1]


_OCR_MARK = "<<ocr>>"  # sentinel prepended to pages that came from OCR


def extract_text(data: bytes, mime: str) -> list[str]:
    """Return text per page."""
    if len(data) > MAX_BYTES:
        raise UnsupportedDocument("File exceeds 15 MB")
    if mime == "application/pdf":
        import pdfplumber

        pages = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise UnsupportedDocument(f"PDF exceeds {MAX_PAGES} pages")
            for p in pdf.pages:
                pages.append(p.extract_text() or "")
        if not any(t.strip() for t in pages):
            return [_OCR_MARK] + _ocr_pdf(data)
        return pages
    if mime == "message/rfc822":
        msg = email.message_from_bytes(data, policy=policy.default)
        for part in msg.iter_attachments():  # vendors usually email the invoice as an attachment
            payload = part.get_payload(decode=True) or b""
            if part.get_content_type() == "application/pdf" or payload.startswith(b"%PDF-"):
                return extract_text(payload, "application/pdf")
            if part.get_content_type() in ("image/png", "image/jpeg"):
                return _ocr_image(payload)
        body = msg.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body else ""
        if body is not None and body.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        return [text]
    if mime == "text/plain":
        return [data.decode("utf-8", errors="replace")]
    if mime.startswith("image/"):
        return _ocr_image(data)
    raise UnsupportedDocument(mime)


def _tesseract():  # type: ignore[no-untyped-def]
    from probity.config import get_settings

    if not get_settings().ocr_enabled:
        raise UnsupportedDocument("OCR is disabled (OCR_ENABLED=false)")
    try:
        import pytesseract  # type: ignore[import-not-found]

        pytesseract.get_tesseract_version()
    except Exception as e:  # noqa: BLE001
        raise UnsupportedDocument("OCR unavailable: the tesseract binary is not installed on this host") from e
    return pytesseract


def _ocr_image(data: bytes) -> list[str]:
    from PIL import Image

    tess = _tesseract()
    img = Image.open(io.BytesIO(data))
    if img.width * img.height > 40_000_000:
        raise UnsupportedDocument("image too large")
    return [tess.image_to_string(img.convert("L"), config="--psm 6")]


def _ocr_pdf(data: bytes) -> list[str]:
    import pdfplumber

    tess = _tesseract()
    out = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_PAGES]:
            img = page.to_image(resolution=250).original.convert("L")
            out.append(tess.image_to_string(img, config="--psm 6"))
    return out


# ---------------------------------------------------------------- labelled-field parser
#
# Deterministic extraction that copes with common Indian GST invoice layouts. Every candidate value is
# type-checked (GSTIN checksum, IFSC pattern, parseable date/amount) before it is accepted, buyer-side
# details ("Bill To", "Consignee", "Buyer") are ignored for supplier fields, and the line-item table is
# read from PDF tables when present. In LLM_MODE=live, Claude fills whatever is still missing, and its
# values are accepted only if they appear verbatim in the document.

_LABELS: dict[str, list[str]] = {
    "invoice_number": [r"(?:tax\s+)?invoice\s*(?:no\.?|number|num\.?|#)", r"inv\.?\s*(?:no\.?|#|number)", r"bill\s*(?:no\.?|number|#)", r"document\s*no\.?", r"invoice\s*id"],
    "invoice_date": [r"invoice\s*date", r"inv\.?\s*date", r"bill\s*date", r"date\s*of\s*(?:issue|invoice)", r"dated", r"date"],
    "due_date": [r"due\s*date", r"payment\s*due(?:\s*date)?", r"due\s*on", r"pay\s*by"],
    "po_number": [r"p\.?\s*o\.?\s*(?:no\.?|number|#|ref\.?)", r"purchase\s*order(?:\s*(?:no\.?|number|#))?", r"buyer'?s?\s*order\s*(?:no\.?|#)?", r"order\s*ref(?:erence)?\.?"],
    "gstin": [r"gstin(?:\s*/\s*uin)?(?:\s*no\.?)?", r"gst\s*(?:no\.?|number|reg(?:istration)?\.?\s*no\.?|in)"],
    "pan": [r"pan(?:\s*no\.?)?"],
    "vendor_email": [r"e-?mail(?:\s*id)?"],
    "vendor_phone": [r"phone|tel\.?|mobile|mob\.?|contact\s*no\.?"],
    "vendor_address": [r"(?:registered\s*)?address|regd\.?\s*office"],
    "bank_account": [r"(?:bank\s*)?(?:a/?c|account)\s*(?:no\.?|number|#)", r"account\s*no"],
    "ifsc": [r"ifs?c(?:\s*code)?", r"ifs\s*code"],
    "bank_name": [r"bank\s*name", r"bank(?!\s*(?:details|a/?c|account))"],
    "subtotal": [r"sub\s*-?\s*total", r"taxable\s*(?:value|amount)", r"(?:total\s*)?amount\s*before\s*tax", r"total\s*before\s*tax"],
    "tax": [r"total\s*(?:gst|tax)(?:\s*amount)?", r"tax\s*amount", r"(?:i|u)?gst(?:\s*@\s*[\d.]+\s*%)?", r"tax(?:\s*@\s*[\d.]+\s*%)?"],
    "_cgst": [r"cgst(?:\s*@\s*[\d.]+\s*%)?"],
    "_sgst": [r"(?:s|ut)gst(?:\s*@\s*[\d.]+\s*%)?"],
    "total": [r"grand\s*total", r"(?:invoice\s*)?total\s*(?:amount\s*)?(?:payable|due)", r"amount\s*payable", r"net\s*(?:amount\s*)?payable", r"balance\s*due",
              r"invoice\s*(?:total|value|amount)", r"total(?:\s*amount)?"],
    "currency": [r"currency"],
}
_MONEY_FIELDS = {"subtotal", "tax", "total", "_cgst", "_sgst"}
_PARTY_FIELDS = {"gstin", "pan", "vendor_email", "vendor_phone", "vendor_address"}
_BUYER = re.compile(r"(?i)\b(bill(?:ed)?\s*to|buyer(?!'?s?\s*order)|ship(?:ped)?\s*to|consignee|customer|recipient|sold\s*to|deliver(?:y)?\s*to|place\s*of\s*supply)\b")
_TITLE = re.compile(r"(?i)^(tax\s*invoice|invoice|bill\s*of\s*supply|proforma\s*invoice|original(\s*for\s*recipient)?|duplicate(\s*for\s*transporter)?|triplicate|retail\s*invoice|credit\s*note|debit\s*note)\b")
_COMPANY = re.compile(r"(?i)\b(pvt\.?\s*ltd\.?|private\s+limited|limited|ltd\.?|llp|enterprises?|traders?|industries|& co\.?|corporation|solutions|services|agencies|exports?|imports?)\b")
_ANY_LABEL = re.compile("(?i)(" + "|".join(f"(?:{p})" for pats in _LABELS.values() for p in pats) + r")\s*[:\-]")
_UNITS = r"(?:nos?\.?|pcs?\.?|units?|kgs?|ltrs?|mtrs?|m|box(?:es)?|sets?|ea|qty|bags?|rolls?|pairs?)"
_LINE_RE = re.compile(
    rf"^(?:\d{{1,3}}[.)]?\s+)?(?P<desc>[A-Za-z][\w &/().,'+%-]*?)\s+(?:(?P<hsn>\d{{4,8}})\s+)?(?P<qty>\d[\d,]*(?:\.\d+)?)\s*{_UNITS}?\.?\s+"
    r"(?:₹|rs\.?|inr)?\s*(?P<price>\d[\d,]*(?:\.\d{1,2})?)\s+(?:₹|rs\.?|inr)?\s*(?P<amt>\d[\d,]*(?:\.\d{1,2})?)$",
    re.I,
)

OCR_CONFIDENCE_PENALTY = 0.15  # OCR'd values are less certain than a text layer


def _field(value: Any, raw: str, snippet: str, page: int, confidence: float) -> dict:
    return {"value": value, "raw": raw, "confidence": confidence, "evidence_snippet": snippet.strip()[:300], "page": page}


def _coerce(key: str, raw: str) -> Any | None:
    """Type-check a candidate; None means 'not a valid value for this field'."""
    from probity.ingestion.validators import parse_date, valid_gstin

    raw = raw.strip().strip(",;|")
    if not raw:
        return None
    if key in _MONEY_FIELDS:
        m = re.search(r"(?:₹|rs\.?|inr)?\s*(-?\d[\d,]*(?:\.\d{1,2})?)\s*(?:/-)?$", raw, re.I) or re.search(r"(\d[\d,]*(?:\.\d{1,2})?)", raw)
        return parse_money_minor(m.group(1)) if m else None
    if key == "gstin":
        m = re.search(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b", raw.upper())
        return m.group(1) if m and valid_gstin(m.group(1)) else None
    if key == "pan":
        m = re.search(r"\b([A-Z]{5}\d{4}[A-Z])\b", raw.upper())
        return m.group(1) if m else None
    if key == "ifsc":
        m = re.search(r"\b([A-Z]{4}0[A-Z0-9]{6})\b", raw.upper())
        return m.group(1) if m else None
    if key == "bank_account":
        m = re.search(r"\b(\d[\d\s-]{6,24}\d)\b", raw)
        digits = re.sub(r"[\s-]", "", m.group(1)) if m else ""
        return digits if 9 <= len(digits) <= 18 else None
    if key == "vendor_email":
        m = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", raw)
        return m.group(0).lower() if m else None
    if key in ("invoice_date", "due_date"):
        token = re.match(r"(\d{1,4}(?:st|nd|rd|th)?[-/.\s][A-Za-z0-9]{1,9}[-/.\s,]*\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4})", raw, re.I)
        d = parse_date(token.group(1)) if token else None
        return d.isoformat() if d else None
    if key in ("invoice_number", "po_number"):
        tok = raw.split()[0].rstrip(".,;")
        return tok if re.search(r"\d", tok) and len(tok) <= 40 else None
    if key == "vendor_phone":
        return raw if len(re.sub(r"\D", "", raw)) >= 8 else None
    return raw


def _segments(line: str) -> list[tuple[str, str]]:
    """Split a line holding several 'Label: value' pairs into (label, value) segments."""
    hits = list(_ANY_LABEL.finditer(line))
    out = []
    for i, m in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(line)
        out.append((m.group(1), line[m.end():end]))
    return out


def _match_label(label: str) -> list[str]:
    return [k for k, pats in _LABELS.items() if any(re.fullmatch(rf"(?i){p}", label.strip()) for p in pats)]


def parse_table_items(tables: list) -> list[dict]:
    """Line items from PDF tables: find a header with description / qty / rate (/ amount) columns."""
    items: list[dict] = []
    for t in tables or []:
        if not t or len(t) < 2:
            continue
        header_idx = None
        cols: dict[str, int] = {}
        for ri, row in enumerate(t[:4]):
            cells = [(c or "").strip().lower() for c in row]
            idx: dict[str, int] = {}
            for ci, c in enumerate(cells):
                if re.search(r"description|particulars|item|goods|service", c) and "desc" not in idx:
                    idx["desc"] = ci
                elif re.search(r"\bqty\b|quantity", c) and "qty" not in idx:
                    idx["qty"] = ci
                elif re.search(r"rate|unit\s*price|price", c) and "price" not in idx:
                    idx["price"] = ci
                elif re.search(r"amount|total|value", c) and "amt" not in idx:
                    idx["amt"] = ci
            if {"desc", "qty", "price"} <= set(idx):
                header_idx, cols = ri, idx
                break
        if header_idx is None:
            continue
        for row in t[header_idx + 1:]:
            def cell(k: str, row=row) -> str:  # type: ignore[no-untyped-def]
                return (row[cols[k]] or "").strip() if k in cols and cols[k] < len(row) else ""

            desc = re.sub(r"\s+", " ", cell("desc"))
            if not desc or re.match(r"(?i)(sub\s*-?\s*total|total|grand|cgst|sgst|igst|tax|round)", desc):
                continue
            qm = re.search(r"\d[\d,]*(?:\.\d+)?", cell("qty"))
            price = parse_money_minor(re.sub(r"[^\d.,]", "", cell("price")) or None)
            if not qm or price is None:
                continue
            qty_f = float(qm.group(0).replace(",", ""))
            amt = parse_money_minor(re.sub(r"[^\d.,]", "", cell("amt")) or None) if "amt" in cols else None
            items.append({"description": desc, "qty": int(qty_f) if qty_f.is_integer() else qty_f, "unit_price_minor": price,
                          "amount_minor": amt if amt is not None else round(qty_f * price)})
    return items


def parse_fields(pages: list[str], tables: list | None = None) -> dict[str, dict]:
    fields: dict[str, dict] = {}
    line_items: list[dict] = []
    li_snips: list[str] = []
    for pno, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if pno == 1 and "vendor_name" not in fields:
            head = [ln for ln in lines[:10] if not _TITLE.match(ln) and not _ANY_LABEL.search(ln) and not _BUYER.search(ln)]
            name = next((ln for ln in head if _COMPANY.search(ln) and len(ln) <= 120), head[0] if head else None)
            if name:
                fields["vendor_name"] = _field(name, name, name, pno, 0.9 if _COMPANY.search(name) else 0.75)
        buyer_zone = 0
        for ln in lines:
            if _BUYER.search(ln) and not _TITLE.match(ln):
                buyer_zone = 6  # the next few lines describe the buyer, not the supplier
            m = _LINE_RE.match(ln)
            if m and not re.match(r"(?i)(sub\s*total|total|grand|gst|cgst|sgst|igst|tax|round)", m.group("desc")):
                qty_f = float(m.group("qty").replace(",", ""))
                price, amt = parse_money_minor(m.group("price")), parse_money_minor(m.group("amt"))
                if price and amt is not None and abs(qty_f * price - amt) <= max(100, amt * 0.01):  # qty x rate must equal amount
                    line_items.append({"description": m.group("desc").strip(), "qty": int(qty_f) if qty_f.is_integer() else qty_f,
                                       "unit_price_minor": price, "amount_minor": amt})
                    li_snips.append(ln)
                    buyer_zone = max(0, buyer_zone - 1)
                    continue
            segs = _segments(ln)
            if not segs:  # "Label value" without a colon, at line start
                for pats in _LABELS.values():
                    for pat in pats:
                        mm = re.match(rf"(?i)^\s*({pat})\s+(?P<v>\S.*)$", ln)
                        if mm:
                            segs = [(mm.group(1), mm.group("v"))]
                            break
                    if segs:
                        break
            for label, raw in segs:
                for key in _match_label(label):
                    if key in fields or (key in _PARTY_FIELDS and buyer_zone):
                        continue
                    val = _coerce(key, raw)
                    if val is None:
                        continue
                    fields[key] = _field(val, raw.strip(), ln, pno, 0.99 if (":" in ln or " - " in ln) else 0.9)
                    break
            buyer_zone = max(0, buyer_zone - 1)
    if not line_items and tables:
        line_items = parse_table_items(tables)
        li_snips = [f"{li['description']} | {li['qty']} | {li['unit_price_minor'] / 100:.2f}" for li in line_items]
    if line_items:
        fields["line_items"] = _field(line_items, "; ".join(li_snips), " | ".join(li_snips), 1, 0.97)
    # CGST + SGST shown separately -> tax is their sum
    if "tax" not in fields and "_cgst" in fields and "_sgst" in fields:
        c, sg = fields["_cgst"], fields["_sgst"]
        fields["tax"] = _field(c["value"] + sg["value"], f"{c['raw']} + {sg['raw']}", f"{c['evidence_snippet']} | {sg['evidence_snippet']}", c["page"], 0.95)
    for k in ("_cgst", "_sgst"):
        fields.pop(k, None)
    if "subtotal" not in fields and line_items:
        fields["subtotal"] = _field(sum(li["amount_minor"] for li in line_items), "sum of line items", " | ".join(li_snips)[:300], 1, 0.85)
    if "currency" not in fields:
        joined = " ".join(pages)
        cur = "INR" if re.search(r"(?i)\bINR\b|₹|\bRs\.?", joined) else None
        if cur:
            fields["currency"] = _field(cur, cur, cur, 1, 0.9)
    if "vendor_email" in fields:
        dom = normalize_domain(fields["vendor_email"]["value"])
        fields["sender_domain"] = _field(dom, fields["vendor_email"]["raw"], fields["vendor_email"]["evidence_snippet"], fields["vendor_email"]["page"], 0.99)
    return fields


def extract_tables(data: bytes, mime: str) -> list:
    if mime != "application/pdf":
        return []
    import pdfplumber

    out: list = []
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:MAX_PAGES]:
                out.extend(page.extract_tables() or [])
    except Exception:  # noqa: BLE001 - tables are a best-effort enhancement
        return []
    return out


REQUIRED_FIELDS = ["vendor_name", "invoice_number", "invoice_date", "total", "line_items"]


def low_confidence(fields: dict[str, dict], threshold: float = 0.8) -> list[str]:
    missing = [k for k in REQUIRED_FIELDS if k not in fields]
    weak = [k for k, f in fields.items() if f.get("confidence", 0) < threshold]
    return missing + weak
