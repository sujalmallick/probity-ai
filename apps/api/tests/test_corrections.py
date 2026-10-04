"""Pre-investigation corrections (C2): a person may fix parser misreads of identity/reference fields, never payment
routing or money; every correction keeps the document's original value, is audited, and blocks auto-clear."""

import pytest
from conftest import login
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec, clean_spec
from helpers import API, upload

SPECS = {"bank_change": bank_change_spec, "clean": clean_spec}


def _upload(client, h, name):
    return upload(client, h, SPECS[name]())


def _create(client, h, doc_id, corrections):
    return client.post(f"{API}/cases", headers=h, json={"document_id": doc_id, "corrections": corrections})


@pytest.mark.parametrize("corrections", [
    {"bank_account": VENDOR_A.account},
    {"sender_domain": VENDOR_A.domain},
    {"total": 11800000},
    {"line_items": [{"description": VENDOR_A.item, "qty": 500, "unit_price_minor": 59000}]},
    {"vendor_email": 42},
    {"vendor_address": "x" * 301},
    {"po_number": "PO-7710\r\nX-Injected: 1"},
])
def test_disallowed_corrections_rejected(client, world, corrections):
    acc = login(client, "accountant")
    r = _create(client, acc, _upload(client, acc, "bank_change"), corrections)
    assert r.status_code == 400, r.text


def test_bec_invoice_cannot_be_laundered_by_corrections(client, world, fake_lookups):
    """The audit's attack: correcting the routing fields of the BEC invoice used to auto-clear it at score 0."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc = login(client, "accountant")
    doc = _upload(client, acc, "bank_change")
    assert _create(client, acc, doc, {"bank_account": VENDOR_A.account, "ifsc": VENDOR_A.ifsc}).status_code == 400
    r = _create(client, acc, doc, {"ifsc": VENDOR_A.ifsc, "vendor_email": VENDOR_A.contact_email})
    assert r.status_code == 201, r.text
    case = client.get(f"{API}/cases/{r.json()['case_id']}", headers=acc).json()
    assert case["status"] == "AWAITING_HUMAN"
    assert "bank_account_changed" in {c["signal"] for c in case["risk"]["contributions"] if c["points"]}


def test_correction_keeps_original_is_audited_and_blocks_auto_clear(client, world):
    acc = login(client, "accountant")
    r = _create(client, acc, _upload(client, acc, "clean"), {"vendor_address": "Unit 9, Other Road, Bengaluru"})
    assert r.status_code == 201, r.text
    case_id = r.json()["case_id"]
    case = client.get(f"{API}/cases/{case_id}", headers=acc).json()
    assert case["status"] == "AWAITING_HUMAN"
    assert any(x.startswith("extracted fields corrected by a person") and "vendor_address" in x for x in case["recommendation"]["gate"]["reasons"])
    field = case["invoice"]["vendor_address"]
    assert field["via"] == "human_correction" and field["value"] == "Unit 9, Other Road, Bengaluru"
    assert "Test Industrial Area" in field["original"]["raw"]
    actions = {i["action"]: i["data"] for i in client.get(f"{API}/cases/{case_id}/audit", headers=acc).json()["items"]}
    assert actions["case.created"]["corrections"] == {"vendor_address": "Unit 9, Other Road, Bengaluru"}
    assert actions["case.corrections_applied"]["vendor_address"]["after"] == "Unit 9, Other Road, Bengaluru"
    assert "Test Industrial Area" in actions["case.corrections_applied"]["vendor_address"]["before"]


def test_unchanged_value_is_not_a_correction(client, world):
    """The UI pencil pre-fills the extracted value; submitting it unchanged must not hold a clean invoice."""
    acc = login(client, "accountant")
    r = _create(client, acc, _upload(client, acc, "clean"), {"po_number": "PO-7711"})
    assert r.status_code == 201, r.text
    case = client.get(f"{API}/cases/{r.json()['case_id']}", headers=acc).json()
    assert case["status"] == "AUTO_CLEARED", case["recommendation"].get("gate")
