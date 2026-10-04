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

from probity.ingestion.validators import EMAIL_RE, normalize_domain, parse_money_minor

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
    dkim = re.search(r"dkim=(\w+)", str(msg.get("authentication-results") or "")[:4000])
    return {"from": _sender_address(msg), "subject": str(msg.get("subject") or ""), "dkim": dkim.group(1).lower() if dkim else None}


def _sender_address(msg: email.message.Message) -> str | None:
    """The actual From address (addr-spec), never an address-looking display name:
    '"billing@vendor.in" <attacker@evil.example>' is from attacker@evil.example."""
    try:
        addrs = msg["from"].addresses if msg["from"] is not None else ()
    except (AttributeError, IndexError, ValueError):
        addrs = ()
    spec = addrs[0].addr_spec if addrs else ""
    m = EMAIL_RE.fullmatch(spec or "")
    return m.group(0).lower() if m else None


def address_from_header(value: str) -> str | None:
    """addr-spec of a From header value given as text (inbound-email webhooks), same rules as email_meta."""
    msg = email.message.EmailMessage(policy=policy.default)
    try:
        msg["from"] = (value or "")[:998]
    except (ValueError, IndexError):
        return None
    return _sender_address(msg)


def email_attachment_pdf(data: bytes) -> bytes | None:
    msg = email.message_from_bytes(data, policy=policy.default)
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        if part.get_content_type() == "application/pdf" or payload.startswith(b"%PDF-"):
            return payload
    return None


def extract(data: bytes, mime: str) -> tuple[list[str], bool]:
    """Return (text per page, used_ocr). Scans are not read (no OCR), so used_ocr is always False."""
    return extract_text(data, mime), False


SCAN_MESSAGE = "This looks like a scanned or image-only invoice. Probity can't read scans yet, so upload a PDF with selectable text."


def extract_text(data: bytes, mime: str) -> list[str]:
    """Text per page. Untrusted documents are parsed in an isolated process with a hard time limit (isolate.py)."""
    if len(data) > MAX_BYTES:
        raise UnsupportedDocument("File exceeds 15 MB")
    if mime.startswith("image/"):
        raise UnsupportedDocument(SCAN_MESSAGE)
    from probity.ingestion import isolate

    return isolate.run("probity.ingestion.parse._extract_text_local", data, mime)


def _pdf_page_count(data: bytes) -> int:
    """Page count without building every page object (pdfplumber's len(pdf.pages) does, which a 100k-page PDF abuses)."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise UnsupportedDocument("the PDF could not be opened") from e
    try:
        return len(doc)
    finally:
        doc.close()


def _extract_text_local(data: bytes, mime: str) -> list[str]:
    if mime == "application/pdf":
        if _pdf_page_count(data) > MAX_PAGES:
            raise UnsupportedDocument(f"PDF exceeds {MAX_PAGES} pages")
        import pdfplumber

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [p.extract_text() or "" for p in pdf.pages]
        if not any(t.strip() for t in pages):
            raise UnsupportedDocument(SCAN_MESSAGE)
        return pages
    if mime == "message/rfc822":
        msg = email.message_from_bytes(data, policy=policy.default)
        for part in msg.iter_attachments():  # vendors usually email the invoice as an attachment
            payload = part.get_payload(decode=True) or b""
            if part.get_content_type() == "application/pdf" or payload.startswith(b"%PDF-"):
                return _extract_text_local(payload, "application/pdf")
            if part.get_content_type() in ("image/png", "image/jpeg"):
                raise UnsupportedDocument(SCAN_MESSAGE)
        body = msg.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body else ""
        if body is not None and body.get_content_type() == "text/html":
            text = re.sub(r"<[^>]+>", " ", text)
        return [text]
    if mime == "text/plain":
        return [data.decode("utf-8", errors="replace")]
    if mime.startswith("image/"):
        raise UnsupportedDocument(SCAN_MESSAGE)
    raise UnsupportedDocument(mime)


# ---------------------------------------------------------------- labelled-field parser
#
# Deterministic extraction that copes with common Indian GST invoice layouts. Every candidate value is
# type-checked (GSTIN checksum, IFSC pattern, parseable date/amount) before it is accepted, buyer-side
# details ("Bill To", "Consignee", "Buyer") are ignored for supplier fields, and the line-item table is
# read from PDF tables when present. Claude then fills whatever is still missing, and its
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



def _field(value: Any, raw: str, snippet: str, page: int, confidence: float) -> dict:
    return {"value": value, "raw": raw, "confidence": confidence, "evidence_snippet": snippet.strip()[:300], "page": page}


def _coerce(key: str, raw: str) -> Any | None:
    """Type-check a candidate; None means 'not a valid value for this field'."""
    from probity.ingestion.validators import parse_date, valid_gstin

    raw = raw.strip().strip(",;|")
    if not raw:
        return None
    if key in _MONEY_FIELDS:
        return _money_from_label_value(raw)
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
        m = EMAIL_RE.search(raw)
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


_CUR = r"(?:₹|\brs\.?|\binr)"
_SPACE_GROUPED = re.compile(rf"(?i)({_CUR}\s*)(\d{{1,3}}(?: \d{{2,3}})+(?:\.\d{{1,2}})?)(?![\d.,])")  # "INR 4 85 000.00"
_MONEY_TOKEN = re.compile(r"(?i)(?<![\w.,])(\d[\d,.]*\d|\d)(?:\s*(lakhs?|lacs?|crores?|cr)\b)?(?!\s*%)(?![\d,.]*\w)")
_SCALE = {"lakh": 100_000, "lakhs": 100_000, "lac": 100_000, "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000, "cr": 10_000_000}


def _money_token_minor(num: str, unit: str | None) -> int | None:
    """One amount → paise, or None when its format is ambiguous. Accepts Indian/western comma grouping, dot
    grouping ("5.90.000"), at most two decimals, and lakh/crore words."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{2,3}){2,}", num) and len(num.rsplit(".", 1)[1]) == 3:
        num = num.replace(".", "")  # dot used as a thousands/lakh separator
    if not re.fullmatch(r"\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?", num):
        return None
    minor = parse_money_minor(num)
    if minor is None:
        return None
    if unit:
        minor *= _SCALE[unit.lower()]
    return minor


def _money_from_label_value(raw: str) -> int | None:
    """The amount for a money label. Text after the label can hold several numbers ("4,85,000.00 Round Off: 0.40",
    "500 Nos 5,66,400.00"); the largest well-formed one is the amount, so a misread errs high (more scrutiny),
    never low. Negative or ambiguous amounts are rejected (None = amount unknown, held for a human)."""
    raw = _SPACE_GROUPED.sub(lambda m: m.group(1) + m.group(2).replace(" ", ""), raw[:300])
    raw = re.sub(rf"(?i){_CUR}", " ", raw)  # "Rs.590" → " 590"
    if re.search(r"(?<![\w/])-\s*\d", raw):
        return None
    vals = [v for m in _MONEY_TOKEN.finditer(raw) if (v := _money_token_minor(m.group(1), m.group(2))) is not None]
    return max(vals) if vals else None


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
    _mask_account_in_other_fields(fields)
    return fields


def _mask_account_in_other_fields(fields: dict[str, dict]) -> None:
    """The account number often shares a line with IFSC / bank name, whose snippets would otherwise show it in full to
    every role. Only the bank_account field keeps it (the document agent then stores last4 + HMAC + ciphertext)."""
    acct = str((fields.get("bank_account") or {}).get("value") or "")
    if not acct.isdigit():
        return
    pat = re.compile(r"(?<!\d)" + r"[\s-]?".join(acct) + r"(?!\d)")
    masked = "XXXX" + acct[-4:]
    for key, f in fields.items():
        if key == "bank_account":
            continue
        for k in ("value", "raw", "evidence_snippet"):
            if isinstance(f.get(k), str):
                f[k] = pat.sub(masked, f[k])


def extract_tables(data: bytes, mime: str) -> list:
    """Best-effort table rows from a PDF (isolated and time-limited like extract_text); [] when unavailable."""
    if mime != "application/pdf" or len(data) > MAX_BYTES:
        return []
    from probity.ingestion import isolate

    try:
        return isolate.run("probity.ingestion.parse._extract_tables_local", data, mime)
    except UnsupportedDocument:
        return []


def _extract_tables_local(data: bytes, mime: str) -> list:
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

# Fields a person may correct before an investigation (fixing what the parser misread). Payment routing and money
# — bank_account, sender_domain, amounts, line items — are never correctable: they are read from the document only.
CORRECTABLE_FIELDS = frozenset({"vendor_name", "gstin", "invoice_number", "invoice_date", "due_date", "po_number", "ifsc", "vendor_email", "vendor_address"})
MAX_CORRECTION_LENGTH = 300


def low_confidence(fields: dict[str, dict], threshold: float = 0.8) -> list[str]:
    missing = [k for k in REQUIRED_FIELDS if k not in fields]
    weak = [k for k, f in fields.items() if f.get("confidence", 0) < threshold]
    return missing + weak
