"""Deterministic extraction on real-world layouts (no LLM): label synonyms, buyer blocks, drawn tables,
split CGST/SGST, round-off, ordinal dates, and invoices emailed as PDF attachments."""

import io
from email.message import EmailMessage

from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table
from reportlab.lib.styles import getSampleStyleSheet

from probity.ingestion.parse import email_attachment_pdf, email_meta, extract_tables, extract_text, parse_fields
from probity.ingestion.validators import check_arithmetic, validate_extraction

SUPPLIER_GSTIN = "29AACCK5521M1Z9"
BUYER_GSTIN = "27AABCA1234F1Z9"


def layout_a() -> bytes:
    """A typical Tally-style GST invoice."""
    buf = io.BytesIO()
    ss = getSampleStyleSheet()
    p = lambda t: Paragraph(t, ss["BodyText"])  # noqa: E731
    story = [
        p("TAX INVOICE"), p("ORIGINAL FOR RECIPIENT"),
        p("Kaveri Packaging Private Limited"),
        p("Regd. Office: No. 42, Peenya Industrial Area, Bengaluru 560058"),
        p(f"GSTIN/UIN: {SUPPLIER_GSTIN}"),
        p("Email ID: billing@kaveripack.in"),
        Spacer(1, 6),
        p("Bill No. KP/24-25/0311"), p("Dated 3rd Oct 2026"), p("Buyer's Order No. PO-7711"),
        Spacer(1, 6),
        p("Bill To: Test Buyer Pvt Ltd"),
        p(f"GSTIN: {BUYER_GSTIN}"),
        p("Address: 5th Floor, Baner Road, Pune 411045"),
        Spacer(1, 8),
        Table([
            ["Sl", "Description of Goods", "HSN", "Quantity", "Rate", "Amount"],
            ["1", "Corrugated Boxes 5-ply", "4819", "2,000 Nos", "42.50", "85,000.00"],
            ["2", "Packing Tape 48mm", "3919", "120 Nos", "35.00", "4,200.00"],
        ]),
        Spacer(1, 8),
        p("Taxable Value: 89,200.00"),
        p("CGST @ 9%: 8,028.00"),
        p("SGST @ 9%: 8,028.00"),
        p("Round Off: (-) 0.00"),
        p("Grand Total: ₹ 1,05,256.00"),
        p("Bank A/c No.: 9180 2004 4556 67"),
        p("IFS Code: ICIC0000412"),
    ]
    SimpleDocTemplate(buf, pagesize=A4).build(story)
    return buf.getvalue()


def test_layout_a_tally_style():
    data = layout_a()
    f = parse_fields(extract_text(data, "application/pdf"), extract_tables(data, "application/pdf"))
    v = lambda k: f[k]["value"]  # noqa: E731
    assert v("vendor_name") == "Kaveri Packaging Private Limited"
    assert v("gstin") == SUPPLIER_GSTIN  # buyer GSTIN in the Bill To block is ignored
    assert v("invoice_number") == "KP/24-25/0311"
    assert v("invoice_date") == "2026-10-03"
    assert v("po_number") == "PO-7711"
    assert v("subtotal") == 8920000 and v("tax") == 1605600 and v("total") == 10525600
    assert v("bank_account") == "91802004455667" and v("ifsc") == "ICIC0000412"
    assert v("sender_domain") == "kaveripack.in"
    items = v("line_items")
    assert [(i["description"], i["qty"], i["unit_price_minor"]) for i in items] == [("Corrugated Boxes 5-ply", 2000, 4250), ("Packing Tape 48mm", 120, 3500)]
    assert check_arithmetic(items, v("subtotal"), v("tax"), v("total")) == []
    from datetime import date

    checks = validate_extraction(f, date(2026, 10, 4))
    assert checks["gstin_checksum"]["ok"] and checks["ifsc_format"]["ok"] and checks["arithmetic"]["ok"] and checks["dates"]["ok"]


def test_emailed_invoice_uses_attachment_and_envelope_sender():
    msg = EmailMessage()
    msg["From"] = "Kaveri Billing <billing@kaveri-pack.co>"  # lookalike domain; the PDF says kaveripack.in
    msg["To"] = "ap@buyer.test"
    msg["Subject"] = "Invoice KP/24-25/0311"
    msg["Authentication-Results"] = "mx.probity.test; dkim=fail header.d=kaveri-pack.co"
    msg.set_content("Please find the invoice attached.")
    msg.add_attachment(layout_a(), maintype="application", subtype="pdf", filename="invoice.pdf")
    raw = msg.as_bytes()
    pdf = email_attachment_pdf(raw)
    assert pdf and pdf.startswith(b"%PDF")
    pages = extract_text(raw, "message/rfc822")
    assert "Kaveri Packaging" in pages[0]
    meta = email_meta(raw)
    assert meta["from"] == "billing@kaveri-pack.co" and meta["dkim"] == "fail"


def test_emailed_invoice_end_to_end_flags_spoofed_sender(client, world, fake_lookups):
    """Through the full pipeline: the envelope sender (9-day-old lookalike domain) drives the domain signal."""
    from conftest import login

    fake_lookups.domains["kaveri-pack.co"] = 9

    msg = EmailMessage()
    msg["From"] = "billing@kaveri-pack.co"
    msg["To"] = "ap@buyer.test"
    msg["Subject"] = "Invoice"
    msg["Authentication-Results"] = "mx; dkim=fail"
    msg.set_content("Invoice attached.")
    msg.add_attachment(layout_a(), maintype="application", subtype="pdf", filename="invoice.pdf")
    acc = login(client, "accountant")
    doc = client.post("/api/v1/documents", headers=acc, files={"file": ("invoice.eml", msg.as_bytes(), "message/rfc822")}).json()
    cid = client.post("/api/v1/cases", headers=acc, json={"document_id": doc["document_id"]}).json()["case_id"]
    case = client.get(f"/api/v1/cases/{cid}", headers=acc).json()
    assert case["invoice"]["sender_domain"]["value"] == "kaveri-pack.co" and case["invoice"]["sender_domain"]["via"] == "email_header"
    assert any(c["signal"] == "new_domain" and c["status"] == "verified" for c in case["claims"])
    assert case["status"] == "AWAITING_HUMAN"
