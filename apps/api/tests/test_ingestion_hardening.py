"""Ingestion hardening (H7 upload DoS, H8 amount parsing, H6 account masking, M12/M13). Pure unit tests: no DB."""

import time
import zlib

import pytest

from probity.guardrails.text import redact
from probity.ingestion import isolate
from probity.ingestion.parse import SCAN_MESSAGE, UnsupportedDocument, _money_from_label_value, email_meta, extract, parse_fields
from probity.ingestion.scan import reject_active_content
from probity.ingestion.validators import EMAIL_RE, parse_money_minor


def _pdf(content: bytes, *, compress: bool = True) -> bytes:
    """Minimal one-page PDF with the given content stream."""
    body = zlib.compress(content) if compress else content
    filt = b" /Filter /FlateDecode" if compress else b""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length %d%s >>\nstream\n" % (len(body), filt) + body + b"\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


# ---------------------------------------------------------------- H7: denial of service

def test_active_content_scan_is_linear():
    t0 = time.perf_counter()
    reject_active_content(b"%PDF-1.4\n" + b"/OpenAction<<" * 50_000, "application/pdf")
    assert time.perf_counter() - t0 < 1.0


@pytest.mark.parametrize("name", [b"/JavaScript", b"/J#61vaScript", b"/L#61unch", b"/OpenAction << /S /JavaScript"])
def test_active_content_rejected_including_hex_escaped_names(name):
    with pytest.raises(UnsupportedDocument):
        reject_active_content(b"%PDF-1.4\n1 0 obj << " + name + b" >> endobj", "application/pdf")


def test_active_content_rejected_inside_email_attachment():
    import base64

    pdf = base64.b64encode(b"%PDF-1.4\n1 0 obj << /JavaScript (app.alert(1)) >> endobj").decode()
    eml = ("From: a@vendor.example\r\nTo: ap@buyer.example\r\nSubject: invoice\r\nMIME-Version: 1.0\r\n"
           "Content-Type: multipart/mixed; boundary=b\r\n\r\n--b\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
           "--b\r\nContent-Type: application/pdf\r\nContent-Disposition: attachment; filename=i.pdf\r\n"
           f"Content-Transfer-Encoding: base64\r\n\r\n{pdf}\r\n--b--\r\n").encode()
    with pytest.raises(UnsupportedDocument):
        reject_active_content(eml, "message/rfc822")


def test_email_regexes_are_linear():
    evil = "a." * 50_000 + "@"
    t0 = time.perf_counter()
    redact(evil)
    EMAIL_RE.search(evil)
    parse_fields(["E-mail: " + "a." * 20_000 + "@x"])
    assert time.perf_counter() - t0 < 2.0


def test_slow_pdf_is_killed_at_the_time_limit():
    """~1 MB of path operators in a 2 KB file used to pin a CPU for tens of seconds inside pdfminer."""
    slow = _pdf(b"0 0 m " * 200_000)
    t0 = time.perf_counter()
    with pytest.raises(UnsupportedDocument, match="too long"):
        isolate.run("probity.ingestion.parse._extract_text_local", slow, "application/pdf", timeout=2)
    assert time.perf_counter() - t0 < 15


def test_page_bomb_rejected_by_cheap_page_count():
    kids = b" ".join(b"3 0 R" for _ in range(60))
    pdf = _pdf(b"BT ET", compress=False).replace(b"/Kids [3 0 R] /Count 1", b"/Kids [" + kids + b"] /Count 60")
    with pytest.raises(UnsupportedDocument, match="pages"):
        extract(pdf, "application/pdf")


def test_scans_are_refused_with_a_clear_message():
    with pytest.raises(UnsupportedDocument, match="scanned"):
        extract(b"\x89PNG\r\n\x1a\n", "image/png")
    with pytest.raises(UnsupportedDocument) as e:
        extract(_pdf(b"0 0 m 10 10 l S"), "application/pdf")
    assert str(e.value) == SCAN_MESSAGE


# ---------------------------------------------------------------- H8: amounts

@pytest.mark.parametrize("raw,expected", [
    ("₹4,85,000.00", 48_500_000),
    ("Rs.590", 59_000),
    ("INR 4 85 000.00", 48_500_000),
    ("Rs. 5.90.000", 59_000_000),
    ("4,85,000.00 Round Off: 0.40", 48_500_000),
    ("4.85 Lakh", 48_500_000),
    ("1.2 crore", 1_200_000_000),
    ("500 Nos 5,66,400.00", 56_640_000),
    ("18% 85,320.00", 8_532_000),
    ("1,23,45,678.00", 1_234_567_800),
    ("1e9", None),
    ("-4,85,000.00", None),
    ("5,90,000.000", None),
    ("NaN", None),
    ("see attached", None),
])
def test_money_parsing_never_reads_low(raw, expected):
    assert _money_from_label_value(raw) == expected


@pytest.mark.parametrize("raw", ["1e9", "NaN", "inf", "Infinity", "-inf", "1.2.3"])
def test_parse_money_minor_rejects_non_plain_numbers(raw):
    assert parse_money_minor(raw) is None


def test_total_label_reads_the_total_not_the_round_off():
    f = parse_fields(["ABC Supplies Pvt Ltd\nTotal Amount Payable: 4,85,000.00 Round Off: 0.40"])
    assert f["total"]["value"] == 48_500_000


# ---------------------------------------------------------------- H6: account number only in the bank_account field

def test_account_number_masked_in_other_fields():
    f = parse_fields(["ABC Supplies Pvt Ltd\nBank: HDFC Bank A/c No: 5010 0098 1298 12 IFSC: HDFC0009812"])
    assert f["bank_account"]["value"] == "50100098129812"
    for key, field in f.items():
        if key == "bank_account":
            continue
        text = f"{field.get('value')} {field.get('raw')} {field.get('evidence_snippet')}".replace(" ", "")
        assert "50100098129812" not in text, key
    assert "XXXX9812" in f["ifsc"]["evidence_snippet"]


# ---------------------------------------------------------------- M13: sender is the real From address

def test_display_name_cannot_spoof_the_sender_domain():
    eml = b'From: "billing@abcsupplies.in" <attacker@evil.example>\r\nTo: ap@buyer.example\r\nSubject: Invoice\r\n\r\nhi\r\n'
    assert email_meta(eml)["from"] == "attacker@evil.example"
