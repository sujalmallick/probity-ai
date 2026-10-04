"""Auto-clear gate (Guardrails G5/G6): auto-clear needs positive evidence that the invoice matches a known vendor's
normal pattern. Missing evidence (unknown vendor, a required check that could not run, another vendor's PO) holds the case."""

from dataclasses import replace
from datetime import date, timedelta

from conftest import login
from sqlalchemy import delete, select

from probity.db.models import Vendor, VendorBankAccount
from probity.db.session import session_scope
from probity.demo.invoice_pdf import InvoiceSpec, render
from probity.demo.seed import VENDORS

API = "/api/v1"


def _kaveri_clean(today: date) -> InvoiceSpec:
    v = VENDORS[1]
    return InvoiceSpec(vendor_name=v[0], vendor_address=v[2], gstin=v[1], email=v[4], phone="+91 80 4000 2000", invoice_number="KP-2026-311",
                       invoice_date=(today - timedelta(days=2)).isoformat(), due_date=(today + timedelta(days=28)).isoformat(), po_number="PO-7711",
                       items=[(v[8], 2000, 4250)], account_number=v[6], ifsc=v[7], bank_name="ICICI Bank")


def _run(client, h, spec: InvoiceSpec, tmp_path) -> dict:
    path = tmp_path / f"{spec.invoice_number}.pdf"
    render(spec, path)
    r = client.post(f"{API}/documents", headers=h, files={"file": (path.name, path.read_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    r = client.post(f"{API}/cases", headers=h, json={"document_id": r.json()["document_id"]})
    assert r.status_code == 201, r.text
    return client.get(f"{API}/cases/{r.json()['case_id']}", headers=h).json()


def _gate(case: dict) -> dict:
    return case["recommendation"].get("gate") or {}


def test_unknown_vendor_never_auto_clears(client, tmp_path):
    """An outsider's invoice quoting a real PO number and paying a never-seen account must reach a human."""
    t = date.today()
    spec = InvoiceSpec(vendor_name="Totally Unknown Traders", vendor_address="1 Some Road", gstin="", email="billing@unknown-traders.example",
                       phone="1", invoice_number="UK-1", invoice_date=t.isoformat(), due_date=t.isoformat(), po_number="PO-7711",
                       items=[("Corrugated Boxes", 100, 4200)], account_number="99887766554433", ifsc="HDFC0000001", bank_name="HDFC")
    case = _run(client, login(client, "accountant"), spec, tmp_path)
    assert case["status"] == "AWAITING_HUMAN", _gate(case)
    assert "vendor not matched to the vendor master" in _gate(case)["reasons"]


def test_po_from_another_vendor_is_flagged(client, tmp_path):
    """Kaveri's otherwise clean invoice quoting ABC Supplies' PO-7710 is not a PO match."""
    case = _run(client, login(client, "accountant"), replace(_kaveri_clean(date.today()), po_number="PO-7710"), tmp_path)
    assert case["status"] == "AWAITING_HUMAN", _gate(case)
    assert any("raised for a different vendor" in c["statement"] for c in case["claims"]), [c["statement"] for c in case["claims"]]


def test_vendor_without_bank_history_never_auto_clears(client, fresh_db, tmp_path):
    """With nothing to compare the invoice's bank account against, the bank check is skipped, so it cannot clear."""
    with session_scope(fresh_db) as s:
        kaveri = s.scalars(select(Vendor).where(Vendor.workspace_id == fresh_db, Vendor.name == VENDORS[1][0])).one()
        s.execute(delete(VendorBankAccount).where(VendorBankAccount.vendor_id == kaveri.id))
    case = _run(client, login(client, "accountant"), _kaveri_clean(date.today()), tmp_path)
    assert case["status"] == "AWAITING_HUMAN", _gate(case)
    assert any(r.startswith("required check could not run: bank account verification") for r in _gate(case)["reasons"]), _gate(case)


def test_clean_known_vendor_still_auto_clears(client, tmp_path):
    case = _run(client, login(client, "accountant"), _kaveri_clean(date.today()), tmp_path)
    assert case["status"] == "AUTO_CLEARED", _gate(case)
