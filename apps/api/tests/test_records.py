"""Phase 3: real data entry. Past invoices and POs entered by hand or imported, approved by an approver before they
count as a baseline (M9); vendor identity edits need an approver; the out-of-band note is the approver's own words with
a channel already on file (M8); sending to an unverified recipient needs a reason (M10)."""

import csv
import io
from datetime import date, timedelta

import pytest
from conftest import login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, VendorSpec, bank_change_spec, spec_for
from helpers import API, legit_reply_body, record_reply, run_case, send_verification
from sqlalchemy import select

from probity.db.models import AuditLog, HistoricalInvoice

VENDOR_C = VendorSpec(
    name="Gamma Fasteners Pvt Ltd", pan="AADCG4410R", state="24", address="Shop 3, Test Market, Ahmedabad, Gujarat 380001",
    domain="gamma-fasteners.test", contact_email="accounts@gamma-fasteners.test", contact_name="G. Contact",
    account="44100155044101", ifsc="BARB0RELIEF", item="Hex Bolts M12", unit_price=1200, qty=1000,
)


@pytest.fixture()
def gamma(client, world):
    """Vendor C set up through the API like a real user: an approver verifies the bank account, domain and contact."""
    appr = login(client, "approver")
    v = client.post(f"{API}/vendors", headers=appr, json={"name": VENDOR_C.name, "gstin": VENDOR_C.gstin, "address": VENDOR_C.address,
                                                         "website": VENDOR_C.domain}).json()
    note = "Confirmed with the vendor's finance team on the number in our records"
    assert client.post(f"{API}/vendors/{v['id']}/bank-accounts", headers=appr,
                       json={"account_number": VENDOR_C.account, "ifsc": VENDOR_C.ifsc, "verified": True, "verification_note": note}).status_code == 201
    dom = client.get(f"{API}/vendors/{v['id']}", headers=appr).json()["domains"][0]
    client.patch(f"{API}/vendors/{v['id']}/domains/{dom['id']}", headers=appr, json={"verified": True, "verification_note": note})
    client.post(f"{API}/vendors/{v['id']}/contacts", headers=appr,
                json={"name": VENDOR_C.contact_name, "email": VENDOR_C.contact_email, "verified": True, "verification_note": note})
    return v["id"]


def _history(client, h, vid, n=0, price="12.00", qty=1000, **extra):  # type: ignore[no-untyped-def]
    when = (date.today() - timedelta(days=30 * (n + 1))).isoformat()
    body = {"invoice_number": f"GF-{100 + n}", "invoice_date": when, "total": f"{qty * float(price) * 1.18:.2f}",
            "bank_account_number": VENDOR_C.account, "line_items": [{"description": VENDOR_C.item, "qty": qty, "unit_price": price}], **extra}
    return client.post(f"{API}/vendors/{vid}/history", headers=h, json=body)


def _gamma_invoice(price=1250, po=None):  # type: ignore[no-untyped-def]
    t = date.today()
    return spec_for(VENDOR_C, t, number="GF-900", qty=1000, price=price, po=po, invoice_date=t - timedelta(days=1))


# ---------------------------------------------------------------- past invoices


def test_accountant_history_waits_for_approval_and_is_not_a_baseline(client, gamma):
    acc = login(client, "accountant")
    for n in range(5):
        r = _history(client, acc, gamma, n)
        assert r.status_code == 201, r.text
        assert r.json()["status"] == "pending" and r.json()["source"] == "manual" and r.json()["account"] == "XXXX4101"
    case = run_case(client, acc, _gamma_invoice())
    chk = case["checks"]["price_anomaly"]
    assert chk["status"] == "could_not_verify" and "5 past invoice(s) are waiting for approver approval" in chk["reason"]
    assert case["status"] == "AWAITING_HUMAN"
    assert any("No approved transaction history" in c["statement"] for c in case["claims"])

    pending = client.get(f"{API}/baseline/pending", headers=acc).json()
    assert pending["invoices"] == 5
    ids = [i["id"] for i in pending["invoice_items"]]
    assert client.post(f"{API}/history/approve", headers=acc, json={"ids": ids}).status_code == 403
    assert client.post(f"{API}/history/approve", headers=login(client, "approver"), json={"ids": ids}).json() == {"approved": 5}
    listed = client.get(f"{API}/vendors/{gamma}/history", headers=acc).json()
    assert listed["approved"] == 5 and listed["pending"] == 0 and listed["items"][0]["approved_by"]["name"]

    case = run_case(client, acc, replace_number(_gamma_invoice(price=2000), "GF-901"))  # +66% vs ₹12.00 average
    assert case["checks"]["price_anomaly"]["status"] == "fired"


def replace_number(spec, number):  # type: ignore[no-untyped-def]
    from dataclasses import replace

    return replace(spec, invoice_number=number)


def test_approver_history_counts_immediately(client, gamma):
    r = _history(client, login(client, "approver"), gamma)
    assert r.status_code == 201 and r.json()["status"] == "approved"


def test_history_rules(client, gamma):
    acc, appr = login(client, "accountant"), login(client, "approver")
    assert _history(client, acc, gamma, currency="USD").status_code == 422  # only INR, never converted
    assert _history(client, acc, gamma, total="-5").status_code == 400
    assert _history(client, acc, gamma, invoice_date=(date.today() + timedelta(days=3)).isoformat()).status_code == 400
    h = _history(client, appr, gamma).json()
    assert _history(client, acc, gamma).status_code == 409  # same invoice number twice
    # an accountant's edit sends an approved row back to pending
    r = client.patch(f"{API}/vendors/{gamma}/history/{h['id']}", headers=acc, json={"total": "15,000.00"})
    assert r.json()["status"] == "pending" and r.json()["total_minor"] == 1500000
    with owner_session() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "history.edited")).first()
        assert row.data["approval_reset"] is True  # the approver's row went back to pending
    client.post(f"{API}/history/approve", headers=appr, json={"ids": [h["id"]]})
    assert client.delete(f"{API}/vendors/{gamma}/history/{h['id']}", headers=acc).status_code == 403  # approved: approver only
    assert client.delete(f"{API}/vendors/{gamma}/history/{h['id']}", headers=appr).status_code == 204
    assert client.get(f"{API}/vendors/{gamma}/history", headers=acc).json()["items"] == []
    assert _history(client, login(client, "viewer"), gamma).status_code == 403


def test_history_from_a_decided_case_cannot_be_edited(client, world):
    with owner_session() as s:
        h = s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.vendor_id == world.vendors["A"].id)).first()
        h.source, h.case_id = "case", "case_x"
        hid = h.id
    r = client.patch(f"{API}/vendors/{world.vendors['A'].id}/history/{hid}", headers=login(client, "approver"), json={"total": "1"})
    assert r.status_code == 409


# ---------------------------------------------------------------- purchase orders


def test_pending_po_is_not_evidence_until_approved(client, gamma):
    acc, appr = login(client, "accountant"), login(client, "approver")
    for n in range(5):
        _history(client, appr, gamma, n)
    po = client.post(f"{API}/purchase-orders", headers=acc, json={"vendor_id": gamma, "po_number": "PO-G1", "po_date": date.today().isoformat(),
                                                                "lines": [{"description": VENDOR_C.item, "qty": 1000, "unit_price": "12.50"}]})
    assert po.status_code == 201 and po.json()["status"] == "pending"
    case = run_case(client, acc, _gamma_invoice(po="PO-G1"))
    assert case["checks"]["po_present"]["status"] == "could_not_verify" and "waiting for approver approval" in case["checks"]["po_present"]["reason"]
    assert not any(c["signal"] == "missing_po" for c in case["claims"])  # pending is not "not found"
    assert client.post(f"{API}/purchase-orders/approve", headers=appr, json={"ids": [po.json()["id"]]}).json() == {"approved": 1}
    case = run_case(client, acc, replace_number(_gamma_invoice(po="PO-G1"), "GF-902"))
    assert case["checks"]["po_present"]["status"] == "passed" and case["checks"]["quantity_po_match"]["status"] == "passed"
    assert client.post(f"{API}/purchase-orders", headers=acc, json={"vendor_id": gamma, "po_number": "PO-G1", "po_date": date.today().isoformat(),
                                                                  "lines": [{"description": "x", "qty": 1, "unit_price": "1"}]}).status_code == 409
    assert [p["po_number"] for p in client.get(f"{API}/purchase-orders?vendor_id={gamma}&status=approved", headers=acc).json()["items"]] == ["PO-G1"]


# ---------------------------------------------------------------- CSV import


def _csv(header, rows):  # type: ignore[no-untyped-def]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def test_csv_import_status_follows_role_and_vendor_match_is_exact(client, gamma):
    ih = ["vendor_gstin", "vendor_name", "invoice_number", "invoice_date", "total", "bank_account_number", "po_number", "item_description", "qty", "unit_price"]
    rows = [["", VENDOR_C.name.upper(), "GF-500", "01/01/2026", "14,160.00", VENDOR_C.account, "", VENDOR_C.item, "1000", "12.00"],
            ["", "%", "GF-501", "01/01/2026", "14,160.00", "", "", VENDOR_C.item, "1000", "12.00"]]  # '%' must not match any vendor
    up = lambda h: client.post(f"{API}/imports/invoices?dry_run=false&skip_invalid=true", headers=h,  # noqa: E731
                               files={"file": ("i.csv", _csv(ih, rows), "text/csv")})
    rep = up(login(client, "accountant")).json()
    assert rep["created"] == 1 and any(e["field"] in ("vendor_gstin", "vendor_name") for e in rep["errors"])
    items = client.get(f"{API}/vendors/{gamma}/history", headers=login(client, "accountant")).json()["items"]
    assert [(i["invoice_number"], i["status"], i["source"]) for i in items] == [("GF-500", "pending", "import")]
    up(login(client, "approver"))  # re-import by an approver approves it
    assert client.get(f"{API}/vendors/{gamma}/history", headers=login(client, "accountant")).json()["items"][0]["status"] == "approved"


def test_onboarding_shows_waiting_records(client, gamma):
    _history(client, login(client, "accountant"), gamma)
    steps = {st["key"]: st for st in client.get(f"{API}/workspace/onboarding", headers=login(client, "owner")).json()["steps"]}
    assert steps["approve_records"]["done"] is False and "1 record(s) waiting" in steps["approve_records"]["detail"]


# ---------------------------------------------------------------- vendor identity edits


def test_vendor_identity_edits_need_an_approver(client, world):
    vid = world.vendors["B"].id
    acc = login(client, "accountant")
    for change in ({"name": "Someone Else Pvt Ltd"}, {"address": "1 Different Road"}):
        assert client.patch(f"{API}/vendors/{vid}", headers=acc, json=change).status_code == 403
    assert client.patch(f"{API}/vendors/{vid}", headers=acc, json={"notes": "pays on the 5th"}).status_code == 200
    assert client.patch(f"{API}/vendors/{vid}", headers=login(client, "approver"), json={"address": "1 Different Road"}).status_code == 200


# ---------------------------------------------------------------- out-of-band note (M8) and override reason (M10)


@pytest.fixture()
def awaiting_reply(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    body = legit_reply_body(case)
    claims = record_reply(client, case, acc, VENDOR_A.contact_email, body)["claim_ids"]
    return case, claims, body, appr


def _oob(client, case, appr, claims, **kw):  # type: ignore[no-untyped-def]
    body = {"claim_ids": claims, "method": "phone_known_contact", "confirmed_account_last4": "9812",
            "note": "Called the accounts contact on the number in our vendor master; confirmed the new account.", "known_channel": True, **kw}
    return client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr, json=body)


def test_oob_requires_known_channel_attestation(client, awaiting_reply):
    case, claims, _, appr = awaiting_reply
    for kc in (None, False):
        r = _oob(client, case, appr, claims, known_channel=kc)
        assert r.status_code == 400 and "already on file" in r.json()["error"]["message"]
    assert _oob(client, case, appr, claims).status_code == 200


def test_oob_note_copied_from_the_reply_is_refused(client, awaiting_reply):
    case, claims, body, appr = awaiting_reply
    copied = body.split("\n")[3]  # a sentence lifted from the vendor's reply
    r = _oob(client, case, appr, claims, note=copied)
    assert r.status_code == 400 and "repeats the vendor's reply" in r.json()["error"]["message"]


def test_override_to_unverified_recipient_needs_a_reason(client, world, fake_lookups, settings):
    from probity.db.models import VendorContact

    fake_lookups.domains[NEW_DOMAIN] = 21
    with owner_session() as s:
        for c in s.scalars(select(VendorContact).where(VendorContact.vendor_id == world.vendors["A"].id)):
            c.verified = False  # no verified contact: the draft goes to the address on the invoice
    settings(EMAIL_ALLOWLIST=f"billing@{NEW_DOMAIN}")
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify bank change"})
    d = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    assert not d["recipient_verified"]
    send = lambda **b: client.post(f"{API}/cases/{case['id']}/drafts/{d['id']}/send", headers=appr, json=b)  # noqa: E731
    assert send().status_code == 400
    assert send(override_unverified_recipient=True).status_code == 400
    assert send(override_unverified_recipient=True, override_reason="ok").status_code == 400
    r = send(override_unverified_recipient=True, override_reason="Vendor's MD confirmed this billing address in our onboarding call")
    assert r.status_code == 200, r.text
    with owner_session() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "draft.sent")).one()
        assert row.data["override"] is True and "onboarding call" in row.data["override_reason"]
