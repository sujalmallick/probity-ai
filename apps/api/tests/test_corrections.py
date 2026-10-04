"""Pre-investigation corrections (C2): a person may fix parser misreads of identity/reference fields, never payment
routing or money; every correction keeps the document's original value, is audited, and blocks auto-clear."""

import pytest
from conftest import login

from probity.demo.seed import DEMO_DIR

API = "/api/v1"


def _upload(client, h, name):
    r = client.post(f"{API}/documents", headers=h, files={"file": (name, (DEMO_DIR / name).read_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["document_id"]


def _create(client, h, doc_id, corrections):
    return client.post(f"{API}/cases", headers=h, json={"document_id": doc_id, "corrections": corrections})


@pytest.mark.parametrize("corrections", [
    {"bank_account": "50200012341234"},
    {"sender_domain": "abcsupplies.in"},
    {"total": 11800000},
    {"line_items": [{"description": "Industrial Components", "qty": 500, "unit_price_minor": 59000}]},
    {"vendor_email": 42},
    {"vendor_address": "x" * 301},
    {"po_number": "PO-7710\r\nX-Injected: 1"},
])
def test_disallowed_corrections_rejected(client, corrections):
    acc = login(client, "accountant")
    r = _create(client, acc, _upload(client, acc, "invoice_4821.pdf"), corrections)
    assert r.status_code == 400, r.text


def test_bec_invoice_cannot_be_laundered_by_corrections(client):
    """The audit's attack: correcting the routing fields of the BEC invoice used to auto-clear it at score 0."""
    acc = login(client, "accountant")
    doc = _upload(client, acc, "invoice_4821.pdf")
    assert _create(client, acc, doc, {"bank_account": "50200012341234", "ifsc": "HDFC0001234"}).status_code == 400
    r = _create(client, acc, doc, {"ifsc": "HDFC0001234", "vendor_email": "accounts@abcsupplies.in"})
    assert r.status_code == 201, r.text
    case = client.get(f"{API}/cases/{r.json()['case_id']}", headers=acc).json()
    assert case["status"] == "AWAITING_HUMAN"
    assert "bank_account_changed" in {c["signal"] for c in case["risk"]["contributions"] if c["points"]}


def test_correction_keeps_original_is_audited_and_blocks_auto_clear(client):
    acc = login(client, "accountant")
    r = _create(client, acc, _upload(client, acc, "invoice_kaveri_clean.pdf"), {"vendor_address": "Unit 9, Peenya, Bengaluru"})
    assert r.status_code == 201, r.text
    case_id = r.json()["case_id"]
    case = client.get(f"{API}/cases/{case_id}", headers=acc).json()
    assert case["status"] == "AWAITING_HUMAN"
    assert any(x.startswith("extracted fields corrected by a person") and "vendor_address" in x for x in case["recommendation"]["gate"]["reasons"])
    field = case["invoice"]["vendor_address"]
    assert field["via"] == "human_correction" and field["value"] == "Unit 9, Peenya, Bengaluru"
    assert "Peenya Industrial Area" in field["original"]["raw"]
    actions = {i["action"]: i["data"] for i in client.get(f"{API}/cases/{case_id}/audit", headers=acc).json()["items"]}
    assert actions["case.created"]["corrections"] == {"vendor_address": "Unit 9, Peenya, Bengaluru"}
    assert actions["case.corrections_applied"]["vendor_address"]["after"] == "Unit 9, Peenya, Bengaluru"
    assert "Peenya Industrial Area" in actions["case.corrections_applied"]["vendor_address"]["before"]


def test_unchanged_value_is_not_a_correction(client):
    """The UI pencil pre-fills the extracted value; submitting it unchanged must not hold a clean invoice."""
    acc = login(client, "accountant")
    r = _create(client, acc, _upload(client, acc, "invoice_kaveri_clean.pdf"), {"po_number": "PO-7711"})
    assert r.status_code == 201, r.text
    case = client.get(f"{API}/cases/{r.json()['case_id']}", headers=acc).json()
    assert case["status"] == "AUTO_CLEARED", case["recommendation"].get("gate")
