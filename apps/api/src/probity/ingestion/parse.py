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
        body = msg.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body else ""
        hdr = f"From: {msg['from']}\nSubject: {msg['subject']}\n"
        return [hdr + text]
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

_LABELS: dict[str, list[str]] = {
    "invoice_number": [r"invoice\s*(?:no\.?|number|#)"],
    "invoice_date": [r"invoice\s*date", r"date\s*of\s*issue"],
    "due_date": [r"due\s*date"],
    "po_number": [r"p\.?o\.?\s*(?:no\.?|number|#)", r"purchase\s*order"],
    "gstin": [r"gstin"],
    "pan": [r"\bpan\b"],
    "vendor_email": [r"e-?mail"],
    "vendor_phone": [r"phone|tel\.?"],
    "vendor_address": [r"address"],
    "bank_account": [r"(?:account|a/c)\s*(?:no\.?|number)"],
    "ifsc": [r"ifsc(?:\s*code)?"],
    "bank_name": [r"bank\s*name|\bbank\b(?!\s*details)"],
    "subtotal": [r"sub\s*-?\s*total"],
    "tax": [r"(?:gst|igst|tax)(?:\s*@\s*[\d.]+\s*%)?"],
    "total": [r"(?:grand\s*)?total(?:\s*amount)?(?:\s*due)?"],
    "currency": [r"currency"],
}
_MONEY_FIELDS = {"subtotal", "tax", "total"}
_LINE_RE = re.compile(r"^(?P<desc>[A-Za-z][\w &/().,'-]*?)\s+(?P<qty>\d[\d,]*)\s+(?P<price>[\d,]+\.\d{2})\s+(?P<amt>[\d,]+\.\d{2})$")


OCR_CONFIDENCE_PENALTY = 0.15  # OCR'd values are less certain than a text layer


def _field(value: Any, raw: str, snippet: str, page: int, confidence: float) -> dict:
    return {"value": value, "raw": raw, "confidence": confidence, "evidence_snippet": snippet.strip()[:300], "page": page}


def parse_fields(pages: list[str]) -> dict[str, dict]:
    fields: dict[str, dict] = {}
    line_items: list[dict] = []
    li_snips: list[str] = []
    for pno, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if pno == 1 and lines and "vendor_name" not in fields:
            # Convention: the issuing company name is the first non-title line of the header.
            for ln in lines[:4]:
                if not re.search(r"(?i)tax invoice|^invoice$", ln):
                    fields["vendor_name"] = _field(ln, ln, ln, pno, 0.9)
                    break
        for ln in lines:
            m = _LINE_RE.match(ln)
            if m and not re.match(r"(?i)(sub\s*total|total|gst|tax)", m.group("desc")):
                line_items.append(
                    {
                        "description": m.group("desc").strip(),
                        "qty": int(m.group("qty").replace(",", "")),
                        "unit_price_minor": parse_money_minor(m.group("price")),
                        "amount_minor": parse_money_minor(m.group("amt")),
                    }
                )
                li_snips.append(ln)
                continue
            for key, pats in _LABELS.items():
                if key in fields:
                    continue
                for pat in pats:
                    mm = re.match(rf"(?i)^\s*(?:{pat})\s*[:\-]\s*(?P<v>.+)$", ln)
                    if not mm:
                        continue
                    raw = mm.group("v").strip()
                    if key in _MONEY_FIELDS:
                        val: Any = parse_money_minor(raw)
                        if val is None:
                            continue
                    elif key == "bank_account":
                        val = re.sub(r"\s", "", raw)
                    elif key in ("gstin", "pan", "ifsc"):
                        val = raw.split()[0].upper()
                    elif key == "vendor_email":
                        val = raw.split()[0].lower()
                    else:
                        val = raw
                    fields[key] = _field(val, raw, ln, pno, 0.99)
                    break
    if line_items:
        fields["line_items"] = _field(line_items, "; ".join(li_snips), " | ".join(li_snips), 1, 0.97)
    if "currency" not in fields:
        joined = " ".join(pages)
        cur = "INR" if re.search(r"(?i)\bINR\b|₹|\bRs\.?", joined) else None
        if cur:
            fields["currency"] = _field(cur, cur, cur, 1, 0.9)
    if "vendor_email" in fields:
        dom = normalize_domain(fields["vendor_email"]["value"])
        fields["sender_domain"] = _field(dom, fields["vendor_email"]["raw"], fields["vendor_email"]["evidence_snippet"], fields["vendor_email"]["page"], 0.99)
    return fields


REQUIRED_FIELDS = ["vendor_name", "invoice_number", "invoice_date", "total", "line_items"]


def low_confidence(fields: dict[str, dict], threshold: float = 0.8) -> list[str]:
    missing = [k for k in REQUIRED_FIELDS if k not in fields]
    weak = [k for k, f in fields.items() if f.get("confidence", 0) < threshold]
    return missing + weak
