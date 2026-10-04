"""CSV import of the internal baseline: vendor master, invoice history, purchase orders.

Two-step by design: a dry run validates every row and returns a report; commit applies only when the
file is clean (or with skip_invalid=True, only the valid rows). Upserts are idempotent: vendors match on
GSTIN (else exact name), invoices on (vendor, normalized invoice number), POs on PO number.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity import graph_rel
from probity.db.models import HistoricalInvoice, PurchaseOrder, User, Vendor, VendorBankAccount, VendorContact, VendorDomain
from probity.guardrails import crypto
from probity.ingestion.validators import normalize_domain, normalize_invoice_number, parse_date, parse_money_minor, valid_gstin, valid_ifsc

MAX_ROWS = 20_000
MAX_BYTES = 5 * 1024 * 1024

TEMPLATES: dict[str, dict[str, Any]] = {
    "vendors": {
        "columns": ["name", "gstin", "pan", "address", "website", "bank_account_number", "ifsc", "bank_verified", "contact_name", "contact_email", "contact_phone", "contact_verified"],
        "required": ["name"],
        "example": ["ABC Supplies Pvt Ltd", "27AABCA1234F1Z9", "AABCA1234F", "Plot 14, MIDC Bhosari, Pune 411026", "abcsupplies.in", "50200012341234", "HDFC0001234", "yes", "R. Kulkarni", "accounts@abcsupplies.in", "+91 20 4000 1000", "yes"],
        "help": "One row per vendor (repeat the vendor with another bank account/contact on extra rows). bank_verified / contact_verified = yes only if confirmed out-of-band; requires approver role.",
    },
    "invoices": {
        "columns": ["vendor_gstin", "vendor_name", "invoice_number", "invoice_date", "total", "currency", "bank_account_number", "po_number", "item_description", "qty", "unit_price"],
        "required": ["invoice_number", "invoice_date", "total"],
        "example": ["27AABCA1234F1Z9", "ABC Supplies Pvt Ltd", "INV-4700", "2025-10-01", "2,08,860.00", "INR", "50200012341234", "PO-7000", "Industrial Components", "300", "590.00"],
        "help": "Past, already-paid invoices (the baseline for price, bank and duplicate checks). One row per line item; repeat invoice_number for multi-line invoices. Identify the vendor by vendor_gstin (preferred) or vendor_name.",
    },
    "purchase_orders": {
        "columns": ["po_number", "vendor_gstin", "vendor_name", "po_date", "item_description", "qty", "unit_price"],
        "required": ["po_number", "po_date", "item_description", "qty"],
        "example": ["PO-7710", "27AABCA1234F1Z9", "ABC Supplies Pvt Ltd", "2026-09-24", "Industrial Components", "500", "590.00"],
        "help": "One row per PO line; repeat po_number for multi-line POs.",
    },
}


def template_csv(kind: str) -> str:
    t = TEMPLATES[kind]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(t["columns"])
    w.writerow(t["example"])
    return buf.getvalue()


@dataclass
class Report:
    kind: str
    rows_total: int = 0
    rows_ok: int = 0
    errors: list[dict] = field(default_factory=list)
    created: int = 0
    updated: int = 0
    preview: list[dict] = field(default_factory=list)

    def err(self, row: int, fieldname: str, msg: str) -> None:
        if len(self.errors) < 500:
            self.errors.append({"row": row, "field": fieldname, "message": msg})

    def as_dict(self) -> dict:
        return {"kind": self.kind, "rows_total": self.rows_total, "rows_ok": self.rows_ok, "error_count": len(self.errors), "errors": self.errors,
                "created": self.created, "updated": self.updated, "preview": self.preview[:10]}


class ImportRejected(ValueError):
    pass


_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _clean(v: str | None) -> str:
    v = (v or "").strip()
    if _FORMULA.match(v) and not re.fullmatch(r"[+-]?[\d,]+(\.\d+)?", v):
        raise ValueError("cell starts with a formula character")  # CSV/spreadsheet formula injection
    return v


def _yes(v: str) -> bool:
    return v.strip().lower() in ("yes", "y", "true", "1", "verified")


def read_rows(data: bytes, kind: str) -> list[dict[str, str]]:
    if len(data) > MAX_BYTES:
        raise ImportRejected("file exceeds 5 MB")
    text = data.decode("utf-8-sig", errors="strict") if data else ""
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportRejected("empty file or missing header row")
    headers = [h.strip().lower() for h in reader.fieldnames]
    expected = TEMPLATES[kind]["columns"]
    unknown = [h for h in headers if h not in expected]
    missing = [c for c in TEMPLATES[kind]["required"] if c not in headers]
    if missing:
        raise ImportRejected(f"missing required column(s): {', '.join(missing)}. Download the template for the expected header.")
    if unknown:
        raise ImportRejected(f"unknown column(s): {', '.join(unknown)}")
    rows = []
    for raw in reader:
        rows.append({(k or "").strip().lower(): (v if isinstance(v, str) else "") for k, v in raw.items()})
        if len(rows) > MAX_ROWS:
            raise ImportRejected(f"more than {MAX_ROWS} rows; split the file")
    return rows


def _find_vendor(s: Session, ws: str, gstin: str, name: str, cache: dict) -> Vendor | None:
    key = (gstin.upper(), name.lower())
    if key in cache:
        return cache[key]
    v = None
    if gstin:
        v = s.scalars(select(Vendor).where(Vendor.workspace_id == ws, Vendor.gstin == gstin.upper())).first()
    if v is None and name:
        v = s.scalars(select(Vendor).where(Vendor.workspace_id == ws, Vendor.name.ilike(name))).first()
    cache[key] = v
    return v


# ---------------------------------------------------------------- vendors

def import_vendors(s: Session, user: User, rows: list[dict], commit: bool, can_verify: bool) -> Report:
    rep = Report("vendors", rows_total=len(rows))
    ws = user.workspace_id
    seen: dict[str, Vendor] = {}
    for i, r in enumerate(rows, start=2):  # row 1 is the header
        try:
            c = {k: _clean(r.get(k)) for k in TEMPLATES["vendors"]["columns"]}
        except ValueError as e:
            rep.err(i, "*", str(e))
            continue
        ok = True
        if not c["name"]:
            rep.err(i, "name", "required")
            ok = False
        gst = c["gstin"].upper()
        if gst and not valid_gstin(gst):
            rep.err(i, "gstin", f"{gst} fails the GSTIN checksum")
            ok = False
        if c["ifsc"] and not valid_ifsc(c["ifsc"]):
            rep.err(i, "ifsc", f"{c['ifsc']} is not a valid IFSC")
            ok = False
        acct = re.sub(r"[\s-]", "", c["bank_account_number"])
        if acct and not re.fullmatch(r"[0-9A-Za-z]{6,34}", acct):
            rep.err(i, "bank_account_number", "must be 6–34 letters/digits")
            ok = False
        if c["contact_email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c["contact_email"]):
            rep.err(i, "contact_email", "not an email address")
            ok = False
        wants_verify = (acct and _yes(c["bank_verified"])) or (c["contact_email"] and _yes(c["contact_verified"]))
        if wants_verify and not can_verify:
            rep.err(i, "bank_verified", "only approvers can import verified bank accounts/contacts")
            ok = False
        if not ok:
            continue
        rep.rows_ok += 1
        rep.preview.append({"row": i, "name": c["name"], "gstin": gst or None, "bank": crypto.mask(crypto.last4(acct)) if acct else None,
                            "bank_verified": bool(acct and _yes(c["bank_verified"])), "contact": c["contact_email"] or None})
        if not commit:
            continue
        key = gst or c["name"].lower()
        v = seen.get(key) or _find_vendor(s, ws, gst, c["name"], {})
        if v is None:
            v = Vendor(workspace_id=ws, name=c["name"], gstin=gst or None, pan=c["pan"].upper() or (gst[2:12] if gst else None),
                       address=c["address"] or None, website=normalize_domain(c["website"]) if c["website"] else None)
            s.add(v)
            s.flush()
            rep.created += 1
        else:
            for attr, val in (("address", c["address"]), ("pan", c["pan"].upper())):
                if val and not getattr(v, attr):
                    setattr(v, attr, val)
            if key not in seen:
                rep.updated += 1
        seen[key] = v
        if c["website"]:
            dom = normalize_domain(c["website"])
            if dom and not s.scalars(select(VendorDomain).where(VendorDomain.vendor_id == v.id, VendorDomain.domain == dom)).first():
                s.add(VendorDomain(workspace_id=ws, vendor_id=v.id, domain=dom, verified=False))
        if acct:
            h = crypto.account_hmac(acct)
            existing = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == v.id, VendorBankAccount.acct_hmac == h)).first()
            verified = _yes(c["bank_verified"])
            if existing is None:
                s.add(VendorBankAccount(workspace_id=ws, vendor_id=v.id, last4=crypto.last4(acct), acct_hmac=h, acct_enc=crypto.encrypt(acct),
                                        ifsc=c["ifsc"].upper() or None, verified=verified, verified_method="import" if verified else None,
                                        verified_by=user.id if verified else None))
            elif verified and not existing.verified:
                existing.verified, existing.verified_method, existing.verified_by = True, "import", user.id
        if c["contact_email"]:
            email = c["contact_email"].lower()
            existing_c = s.scalars(select(VendorContact).where(VendorContact.vendor_id == v.id, VendorContact.email == email)).first()
            if existing_c is None:
                s.add(VendorContact(workspace_id=ws, vendor_id=v.id, name=c["contact_name"] or None, email=email, phone=c["contact_phone"] or None,
                                    verified=_yes(c["contact_verified"])))
    if commit:
        s.flush()
        graph_rel.index_vendor_master(s, ws)
    return rep


# ---------------------------------------------------------------- invoice history

def import_invoices(s: Session, user: User, rows: list[dict], commit: bool) -> Report:
    rep = Report("invoices", rows_total=len(rows))
    ws = user.workspace_id
    cache: dict = {}
    groups: dict[tuple[str, str], dict] = {}
    for i, r in enumerate(rows, start=2):
        try:
            c = {k: _clean(r.get(k)) for k in TEMPLATES["invoices"]["columns"]}
        except ValueError as e:
            rep.err(i, "*", str(e))
            continue
        ok = True
        v = _find_vendor(s, ws, c["vendor_gstin"], c["vendor_name"], cache)
        if v is None:
            rep.err(i, "vendor_gstin", "vendor not found — import vendors first (match by GSTIN or exact name)")
            ok = False
        d = parse_date(c["invoice_date"])
        if d is None:
            rep.err(i, "invoice_date", f"unrecognized date '{c['invoice_date']}' (use YYYY-MM-DD or DD/MM/YYYY)")
            ok = False
        elif d > date.today():
            rep.err(i, "invoice_date", "date is in the future")
            ok = False
        total = parse_money_minor(c["total"])
        if total is None or total < 0:
            rep.err(i, "total", f"not an amount: '{c['total']}'")
            ok = False
        qty = price = None
        if c["item_description"]:
            try:
                qty = int(float(c["qty"].replace(",", ""))) if c["qty"] else 1
            except ValueError:
                rep.err(i, "qty", f"not a number: '{c['qty']}'")
                ok = False
            price = parse_money_minor(c["unit_price"]) if c["unit_price"] else None
            if price is None:
                rep.err(i, "unit_price", "required when item_description is set")
                ok = False
        if not c["invoice_number"]:
            rep.err(i, "invoice_number", "required")
            ok = False
        acct = re.sub(r"[\s-]", "", c["bank_account_number"])
        if not ok:
            continue
        rep.rows_ok += 1
        key = (v.id, normalize_invoice_number(c["invoice_number"]))  # type: ignore[union-attr]
        g = groups.setdefault(key, {"vendor": v, "number": c["invoice_number"], "date": d, "total": total, "acct": acct, "po": c["po_number"], "items": [], "row": i})
        if c["item_description"]:
            g["items"].append({"description": c["item_description"], "qty": qty, "unit_price_minor": price, "amount_minor": (qty or 0) * (price or 0)})
    for (vid, norm), g in groups.items():
        rep.preview.append({"row": g["row"], "vendor": g["vendor"].name, "invoice_number": g["number"], "date": g["date"].isoformat(), "total_minor": g["total"], "lines": len(g["items"])})
        if not commit:
            continue
        existing = s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.workspace_id == ws, HistoricalInvoice.vendor_id == vid, HistoricalInvoice.invoice_number_norm == norm)).first()
        fields = dict(invoice_number=g["number"], invoice_date=g["date"], total_minor=g["total"], po_number=g["po"] or None, line_items=g["items"],
                      bank_last4=crypto.last4(g["acct"]) if g["acct"] else None, bank_hmac=crypto.account_hmac(g["acct"]) if g["acct"] else None)
        if existing:
            for k, val in fields.items():
                setattr(existing, k, val)
            rep.updated += 1
        else:
            s.add(HistoricalInvoice(workspace_id=ws, vendor_id=vid, invoice_number_norm=norm, **fields))
            rep.created += 1
        if g["acct"]:  # paid history establishes the account as *seen* (not verified) for this vendor
            h = crypto.account_hmac(g["acct"])
            acc = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == vid, VendorBankAccount.acct_hmac == h)).first()
            if acc is None:
                s.add(VendorBankAccount(workspace_id=ws, vendor_id=vid, last4=crypto.last4(g["acct"]), acct_hmac=h, acct_enc=crypto.encrypt(g["acct"]),
                                        verified=False, first_seen=g["date"], last_seen=g["date"]))
            else:
                acc.first_seen = min(filter(None, [acc.first_seen, g["date"]]))
                acc.last_seen = max(filter(None, [acc.last_seen, g["date"]]))
            s.flush()
    return rep


# ---------------------------------------------------------------- purchase orders

def import_purchase_orders(s: Session, user: User, rows: list[dict], commit: bool) -> Report:
    rep = Report("purchase_orders", rows_total=len(rows))
    ws = user.workspace_id
    cache: dict = {}
    groups: dict[str, dict] = {}
    for i, r in enumerate(rows, start=2):
        try:
            c = {k: _clean(r.get(k)) for k in TEMPLATES["purchase_orders"]["columns"]}
        except ValueError as e:
            rep.err(i, "*", str(e))
            continue
        ok = True
        v = _find_vendor(s, ws, c["vendor_gstin"], c["vendor_name"], cache)
        if v is None:
            rep.err(i, "vendor_gstin", "vendor not found — import vendors first")
            ok = False
        d = parse_date(c["po_date"])
        if d is None:
            rep.err(i, "po_date", f"unrecognized date '{c['po_date']}'")
            ok = False
        try:
            qty = int(float(c["qty"].replace(",", "")))
            if qty <= 0:
                raise ValueError
        except ValueError:
            rep.err(i, "qty", f"not a positive number: '{c['qty']}'")
            ok = False
            qty = 0
        price = parse_money_minor(c["unit_price"]) if c["unit_price"] else None
        if not c["po_number"]:
            rep.err(i, "po_number", "required")
            ok = False
        if not ok:
            continue
        g = groups.setdefault(c["po_number"], {"vendor": v, "date": d, "lines": [], "row": i})
        if g["vendor"].id != v.id:  # type: ignore[union-attr]
            rep.err(i, "po_number", f"{c['po_number']} already used for vendor {g['vendor'].name} in this file")
            continue
        rep.rows_ok += 1
        g["lines"].append({"description": c["item_description"], "qty": qty, "unit_price_minor": price})
    for po_no, g in groups.items():
        rep.preview.append({"row": g["row"], "po_number": po_no, "vendor": g["vendor"].name, "date": g["date"].isoformat(), "lines": len(g["lines"])})
        if not commit:
            continue
        existing = s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == ws, PurchaseOrder.po_number == po_no)).first()
        if existing:
            existing.vendor_id, existing.po_date, existing.lines = g["vendor"].id, g["date"], g["lines"]
            rep.updated += 1
        else:
            s.add(PurchaseOrder(workspace_id=ws, vendor_id=g["vendor"].id, po_number=po_no, po_date=g["date"], lines=g["lines"]))
            rep.created += 1
    return rep


def run_import(s: Session, user: User, kind: str, data: bytes, commit: bool, can_verify: bool) -> Report:
    rows = read_rows(data, kind)
    if kind == "vendors":
        return import_vendors(s, user, rows, commit, can_verify)
    if kind == "invoices":
        return import_invoices(s, user, rows, commit)
    return import_purchase_orders(s, user, rows, commit)
