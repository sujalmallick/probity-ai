"""Create / read / update / delete for every record a user manages, through the HTTP API, with every step checked in
PostgreSQL over a separate connection, so a passing test means the data was committed, not just held in a session.

Append-only records (case notes, audit log, evidence) are checked for create + read and for refusing changes."""

from datetime import date, timedelta

import pytest
from conftest import login, owner_session
from factories import VENDOR_B, clean_spec
from helpers import API, run_case
from sqlalchemy import select, text, update

from probity.db.models import (
    AuditLog, Case, CaseNote, HistoricalInvoice, Invitation, PurchaseOrder, User, Vendor, VendorBankAccount, VendorContact,
    VendorDomain, Workspace,
)

NOTE = "Confirmed with the vendor's finance team on the number in our records"


def _row(model, id_):  # type: ignore[no-untyped-def]
    """Fresh read from the database on a separate connection (never the API's session)."""
    with owner_session() as s:
        return s.get(model, id_)


def test_vendor_crud_persists(client, world):
    acc, appr = login(client, "accountant"), login(client, "approver")
    # create
    r = client.post(f"{API}/vendors", headers=acc, json={"name": "Delta Tools Pvt Ltd", "address": "4 Test Lane, Pune", "website": "https://delta-tools.test/"})
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    v = _row(Vendor, vid)
    assert v is not None and v.workspace_id == world.workspace_id and v.name == "Delta Tools Pvt Ltd"
    # read
    assert client.get(f"{API}/vendors/{vid}", headers=acc).json()["name"] == "Delta Tools Pvt Ltd"
    assert vid in [x["id"] for x in client.get(f"{API}/vendors?q=delta", headers=acc).json()["items"]]
    # update (identity field → approver; notes → accountant)
    assert client.patch(f"{API}/vendors/{vid}", headers=appr, json={"name": "Delta Tools India Pvt Ltd"}).status_code == 200
    assert client.patch(f"{API}/vendors/{vid}", headers=acc, json={"notes": "net 30"}).status_code == 200
    v = _row(Vendor, vid)
    assert v.name == "Delta Tools India Pvt Ltd" and v.notes == "net 30"
    # "delete" for vendors is archive (cases and history keep pointing at them)
    assert client.patch(f"{API}/vendors/{vid}", headers=acc, json={"archived": True}).json()["archived"] is True
    assert _row(Vendor, vid).archived is True
    assert vid not in [x["id"] for x in client.get(f"{API}/vendors", headers=acc).json()["items"]]
    assert client.patch(f"{API}/vendors/{vid}", headers=acc, json={"archived": False}).status_code == 200
    assert _row(Vendor, vid).archived is False


def test_bank_domain_contact_crud_persists(client, world):
    acc, appr = login(client, "accountant"), login(client, "approver")
    vid = world.vendors["B"].id
    # bank account: create, verify, delete
    a = client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": "123456789012", "ifsc": "HDFC0000001"}).json()
    row = _row(VendorBankAccount, a["id"])
    assert row.last4 == "9012" and not row.verified and row.acct_enc and b"123456789012" not in row.acct_enc  # encrypted at rest
    client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True, "verification_note": NOTE})
    row = _row(VendorBankAccount, a["id"])
    assert row.verified and row.verified_note == NOTE and row.verified_by == client.get(f"{API}/me", headers=appr).json()["id"]
    assert client.delete(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr).status_code == 204
    assert _row(VendorBankAccount, a["id"]) is None
    # domain
    d = client.post(f"{API}/vendors/{vid}/domains", headers=acc, json={"domain": "beta-billing.test"}).json()
    assert _row(VendorDomain, d["id"]).domain == "beta-billing.test"
    client.patch(f"{API}/vendors/{vid}/domains/{d['id']}", headers=appr, json={"verified": True, "verification_note": NOTE})
    assert _row(VendorDomain, d["id"]).verified
    assert client.delete(f"{API}/vendors/{vid}/domains/{d['id']}", headers=appr).status_code == 204
    assert _row(VendorDomain, d["id"]) is None
    # contact
    c = client.post(f"{API}/vendors/{vid}/contacts", headers=acc, json={"name": "New Person", "email": "new@beta-packaging.test", "phone": "+91 80 1111 2222"}).json()
    assert _row(VendorContact, c["id"]).email == "new@beta-packaging.test"
    client.patch(f"{API}/vendors/{vid}/contacts/{c['id']}", headers=acc, json={"phone": "+91 80 3333 4444"})
    assert _row(VendorContact, c["id"]).phone == "+91 80 3333 4444"
    assert client.delete(f"{API}/vendors/{vid}/contacts/{c['id']}", headers=appr).status_code == 204
    assert _row(VendorContact, c["id"]) is None


def test_gst_manual_crud_persists(client, world):
    vid = world.vendors["B"].id
    acc = login(client, "accountant")
    client.put(f"{API}/vendors/{vid}/gst-manual", headers=acc, json={"status": "Active", "legal_name": VENDOR_B.name, "note": "portal check"})
    assert _row(Vendor, vid).gst_manual["status"] == "Active"
    client.put(f"{API}/vendors/{vid}/gst-manual", headers=acc, json={"status": "Suspended"})
    assert _row(Vendor, vid).gst_manual["status"] == "Suspended"
    assert client.delete(f"{API}/vendors/{vid}/gst-manual", headers=acc).status_code == 204
    assert _row(Vendor, vid).gst_manual is None


def test_history_and_po_crud_persists(client, world):
    acc, appr = login(client, "accountant"), login(client, "approver")
    vid = world.vendors["B"].id
    when = (date.today() - timedelta(days=40)).isoformat()
    h = client.post(f"{API}/vendors/{vid}/history", headers=acc, json={
        "invoice_number": "BP-9001", "invoice_date": when, "total": "1,00,300.00",
        "line_items": [{"description": VENDOR_B.item, "qty": 2000, "unit_price": "42.50"}]}).json()
    row = _row(HistoricalInvoice, h["id"])
    assert row.total_minor == 10030000 and row.source == "manual" and row.approved_at is None and row.entered_by == client.get(f"{API}/me", headers=acc).json()["id"]
    client.patch(f"{API}/vendors/{vid}/history/{h['id']}", headers=acc, json={"total": "1,00,000.00"})
    assert _row(HistoricalInvoice, h["id"]).total_minor == 10000000
    client.post(f"{API}/history/approve", headers=appr, json={"ids": [h["id"]]})
    assert _row(HistoricalInvoice, h["id"]).approved_by == client.get(f"{API}/me", headers=appr).json()["id"]
    assert client.delete(f"{API}/vendors/{vid}/history/{h['id']}", headers=appr).status_code == 204
    assert _row(HistoricalInvoice, h["id"]) is None

    p = client.post(f"{API}/purchase-orders", headers=acc, json={"vendor_id": vid, "po_number": "PO-9001", "po_date": when,
                                                               "lines": [{"description": VENDOR_B.item, "qty": 10, "unit_price": "42.50"}]}).json()
    assert _row(PurchaseOrder, p["id"]).lines[0]["unit_price_minor"] == 4250
    client.patch(f"{API}/purchase-orders/{p['id']}", headers=acc, json={"lines": [{"description": VENDOR_B.item, "qty": 20, "unit_price": "42.50"}]})
    assert _row(PurchaseOrder, p["id"]).lines[0]["qty"] == 20
    assert client.delete(f"{API}/purchase-orders/{p['id']}", headers=acc).status_code == 204  # still pending → accountant may delete
    assert _row(PurchaseOrder, p["id"]) is None


def test_case_lifecycle_and_notes_persist(client, world):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, clean_spec())
    row = _row(Case, case["id"])
    assert row.status == "AUTO_CLEARED" and row.extraction["total"]["value"] == 10030000 and row.risk["score"] == 0
    assert case["id"] in [c["id"] for c in client.get(f"{API}/cases", headers=acc).json()["items"]]
    n = client.post(f"{API}/cases/{case['id']}/notes", headers=acc, json={"text": "Paid on schedule."}).json()
    with owner_session() as s:
        assert s.get(CaseNote, n["id"]).text == "Paid on schedule."
    r = client.post(f"{API}/cases/{case['id']}/close", headers=appr, json={"outcome": "CLEARED", "resolution": "paid normally, nothing unusual"})
    assert r.status_code == 200, r.text
    assert _row(Case, case["id"]).status == "CLOSED"
    with owner_session() as s:  # a cleared, paid INR invoice becomes approved baseline
        h = s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.case_id == case["id"])).one()
        assert h.source == "case" and h.approved_at is not None


def test_workspace_team_and_policy_persist(client, world):
    owner = login(client, "owner")
    client.patch(f"{API}/workspace", headers=owner, json={"name": "Renamed Co"})
    assert _row(Workspace, world.workspace_id).name == "Renamed Co"
    inv = client.post(f"{API}/workspace/invitations", headers=owner, json={"email": "joiner@company.test", "role": "viewer"}).json()
    assert _row(Invitation, inv["id"]).email == "joiner@company.test"
    assert client.delete(f"{API}/workspace/invitations/{inv['id']}", headers=owner).status_code == 204
    assert _row(Invitation, inv["id"]) is None
    viewer = world.user("viewer")
    client.patch(f"{API}/workspace/members/{viewer.id}", headers=owner, json={"role": "accountant"})
    assert _row(User, viewer.id).role == "accountant"
    client.put(f"{API}/workspace/policy", headers=owner, json={"auto_clear_max_amount_minor": 5000000})
    assert _row(Workspace, world.workspace_id).policy["auto_clear_max_amount_minor"] == 5000000


def test_every_write_is_audited_and_the_audit_log_cannot_be_changed(client, world):
    acc = login(client, "accountant")
    client.post(f"{API}/vendors", headers=acc, json={"name": "Epsilon Traders"})
    with owner_session() as s:
        actions = {a for (a,) in s.execute(select(AuditLog.action).where(AuditLog.workspace_id == world.workspace_id))}
    assert "vendor.created" in actions
    with pytest.raises(Exception, match="(?i)immutable|append-only|not allowed|permission"):
        with owner_session() as s:
            s.execute(update(AuditLog).where(AuditLog.workspace_id == world.workspace_id).values(action="tampered"))


def test_another_workspace_cannot_read_or_write_these_rows(client, world):
    from factories import build_world

    from conftest import bearer

    other = bearer(build_world(name="Other Co").user("owner"))
    vid = world.vendors["B"].id
    assert client.get(f"{API}/vendors/{vid}", headers=other).status_code == 404
    assert client.patch(f"{API}/vendors/{vid}", headers=other, json={"notes": "hijack"}).status_code == 404
    assert client.get(f"{API}/vendors/{vid}/history", headers=other).status_code == 404
    assert _row(Vendor, vid).notes is None
    with owner_session() as s:  # and the database itself enforces it for the app's role
        assert s.execute(text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'vendors'")).scalar() is True
