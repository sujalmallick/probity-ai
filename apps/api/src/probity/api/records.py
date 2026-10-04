"""Past invoices and purchase orders entered by hand (CSV import lives in api/workspace.py).

Both feed the comparisons every new invoice is checked against, so writes are audited and an accountant's rows wait for
an approver (see probity.baseline). Amounts are INR only: Probity never converts.
"""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from probity import baseline
from probity import services as svc
from probity.api.deps import current_user, db, require_mfa_for_approvals
from probity.db.audit import audit
from probity.db.models import HistoricalInvoice, PurchaseOrder, User, Vendor, VendorBankAccount, iso
from probity.guardrails import crypto
from probity.ingestion.validators import normalize_invoice_number, parse_date, parse_money_minor

router = APIRouter(prefix="/api/v1", tags=["records"])
MAX_LINES = 100


def _vendor(s: Session, user: User, vendor_id: str) -> Vendor:
    v = s.get(Vendor, vendor_id)
    if v is None or v.workspace_id != user.workspace_id:
        raise LookupError("vendor not found")
    return v


def _money(raw: str | int | float, field: str) -> int:
    minor = parse_money_minor(raw if not isinstance(raw, (int, float)) else f"{raw}")
    if minor is None or minor <= 0:
        raise svc.BadRequest(f"{field}: enter a positive amount in INR, e.g. 1,18,000.00")
    return minor


def _date(raw: str, field: str) -> date:
    d = parse_date(raw)
    if d is None:
        raise svc.BadRequest(f"{field}: unrecognised date '{raw}'")
    if d > date.today():
        raise svc.BadRequest(f"{field}: cannot be in the future")
    return d


class LineIn(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    qty: int = Field(gt=0, le=10_000_000)
    unit_price: str | float = Field(description="INR, e.g. 590 or 590.00")


class HistoryIn(BaseModel):
    invoice_number: str = Field(min_length=1, max_length=80)
    invoice_date: str
    total: str | float = Field(description="INR including tax, e.g. 1,18,000.00")
    currency: str = "INR"
    bank_account_number: str | None = Field(default=None, max_length=30)
    po_number: str | None = Field(default=None, max_length=80)
    line_items: list[LineIn] = Field(default_factory=list, max_length=MAX_LINES)

    @field_validator("currency")
    @classmethod
    def _inr(cls, v: str) -> str:
        if v.strip().upper() != "INR":
            raise ValueError("only INR history is accepted (Probity never converts currencies)")
        return "INR"


class HistoryPatch(BaseModel):
    invoice_date: str | None = None
    total: str | float | None = None
    po_number: str | None = Field(default=None, max_length=80)
    line_items: list[LineIn] | None = Field(default=None, max_length=MAX_LINES)


class POIn(BaseModel):
    vendor_id: str
    po_number: str = Field(min_length=1, max_length=80)
    po_date: str
    lines: list[LineIn] = Field(min_length=1, max_length=MAX_LINES)


class POPatch(BaseModel):
    po_date: str | None = None
    lines: list[LineIn] | None = Field(default=None, min_length=1, max_length=MAX_LINES)


class ApproveIn(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)


def _lines(lines: list[LineIn], with_amount: bool) -> list[dict]:
    out = []
    for i, li in enumerate(lines):
        price = _money(li.unit_price, f"line {i + 1} unit_price")
        row = {"description": li.description.strip(), "qty": li.qty, "unit_price_minor": price}
        if with_amount:
            row["amount_minor"] = li.qty * price
        out.append(row)
    return out


def _names(s: Session, ws: str) -> dict[str, str]:
    return {u.id: u.name for u in s.scalars(select(User).where(User.workspace_id == ws))}


def history_public(h: HistoricalInvoice, names: dict[str, str]) -> dict:
    return {"id": h.id, "vendor_id": h.vendor_id, "invoice_number": h.invoice_number, "invoice_date": h.invoice_date.isoformat(),
            "total_minor": h.total_minor, "currency": h.currency, "account": crypto.mask(h.bank_last4) if h.bank_last4 else None,
            "po_number": h.po_number, "line_items": h.line_items, "source": h.source, "case_id": h.case_id,
            "status": baseline.status(h), "entered_by": {"id": h.entered_by, "name": names.get(h.entered_by or "")} if h.entered_by else None,
            "approved_by": {"id": h.approved_by, "name": names.get(h.approved_by or "", h.approved_by)} if h.approved_by else None,
            "approved_at": iso(h.approved_at)}


def po_public(p: PurchaseOrder, names: dict[str, str], vendor_name: str | None = None) -> dict:
    return {"id": p.id, "vendor_id": p.vendor_id, "vendor": vendor_name, "po_number": p.po_number, "po_date": p.po_date.isoformat(), "lines": p.lines,
            "source": p.source, "status": baseline.status(p),
            "entered_by": {"id": p.entered_by, "name": names.get(p.entered_by or "")} if p.entered_by else None,
            "approved_by": {"id": p.approved_by, "name": names.get(p.approved_by or "", p.approved_by)} if p.approved_by else None,
            "approved_at": iso(p.approved_at)}


def _seen_account(s: Session, ws: str, vendor_id: str, acct: str, when: date) -> None:
    """Paid history shows the account was used (seen), never that it is verified."""
    h = crypto.account_hmac(acct)
    acc = s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == vendor_id, VendorBankAccount.acct_hmac == h)).first()
    if acc is None:
        s.add(VendorBankAccount(workspace_id=ws, vendor_id=vendor_id, last4=crypto.last4(acct), acct_hmac=h, acct_enc=crypto.encrypt(acct),
                                verified=False, first_seen=when, last_seen=when))
    else:
        acc.first_seen = min(filter(None, [acc.first_seen, when]))
        acc.last_seen = max(filter(None, [acc.last_seen, when]))


# ---------------------------------------------------------------- past invoices

@router.get("/vendors/{vendor_id}/history")
def list_history(vendor_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    v = _vendor(s, user, vendor_id)
    rows = s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.vendor_id == v.id).order_by(HistoricalInvoice.invoice_date.desc()))
    names = _names(s, user.workspace_id)
    items = [history_public(h, names) for h in rows]
    return {"items": items, "approved": sum(i["status"] == "approved" for i in items), "pending": sum(i["status"] == "pending" for i in items)}


@router.post("/vendors/{vendor_id}/history", status_code=201)
def add_history(vendor_id: str, body: HistoryIn, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    v = _vendor(s, user, vendor_id)
    norm = normalize_invoice_number(body.invoice_number)
    if s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.vendor_id == v.id, HistoricalInvoice.invoice_number_norm == norm)).first():
        raise svc.Conflict(f"invoice {body.invoice_number} is already in this vendor's history")
    when = _date(body.invoice_date, "invoice_date")
    acct = re.sub(r"[\s-]", "", body.bank_account_number or "")
    if acct and not re.fullmatch(r"\d{9,18}", acct):
        raise svc.BadRequest("bank_account_number: 9–18 digits")
    h = HistoricalInvoice(workspace_id=user.workspace_id, vendor_id=v.id, invoice_number=body.invoice_number.strip(), invoice_number_norm=norm,
                          invoice_date=when, total_minor=_money(body.total, "total"), currency="INR", po_number=(body.po_number or "").strip() or None,
                          line_items=_lines(body.line_items, with_amount=True),
                          bank_last4=crypto.last4(acct) if acct else None, bank_hmac=crypto.account_hmac(acct) if acct else None)
    baseline.stamp_new(h, user, "manual")
    s.add(h)
    if acct:
        _seen_account(s, user.workspace_id, v.id, acct, when)
    s.flush()
    audit(s, user.workspace_id, user.id, "history.added", h.id, {"vendor_id": v.id, "invoice_number": h.invoice_number, "total_minor": h.total_minor,
                                                                 "status": baseline.status(h)}, request.state.request_id)
    return history_public(h, _names(s, user.workspace_id))


def _history_row(s: Session, user: User, vendor_id: str, hid: str) -> HistoricalInvoice:
    h = s.get(HistoricalInvoice, hid)
    if h is None or h.workspace_id != user.workspace_id or h.vendor_id != vendor_id:
        raise LookupError("past invoice not found")
    if h.source == "case":
        raise svc.Conflict("this invoice came from a decided case; it can't be edited or removed here")
    return h


@router.patch("/vendors/{vendor_id}/history/{hid}")
def edit_history(vendor_id: str, hid: str, body: HistoryPatch, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    h = _history_row(s, user, vendor_id, hid)
    before = history_public(h, {})
    if body.invoice_date is not None:
        h.invoice_date = _date(body.invoice_date, "invoice_date")
    if body.total is not None:
        h.total_minor = _money(body.total, "total")
    if body.po_number is not None:
        h.po_number = body.po_number.strip() or None
    if body.line_items is not None:
        h.line_items = _lines(body.line_items, with_amount=True)
    reset = baseline.stamp_edit(h, user)
    audit(s, user.workspace_id, user.id, "history.edited", h.id, {"before": before, "after": history_public(h, {}), "approval_reset": reset},
          request.state.request_id)
    return history_public(h, _names(s, user.workspace_id))


@router.delete("/vendors/{vendor_id}/history/{hid}", status_code=204)
def delete_history(vendor_id: str, hid: str, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> None:
    svc.require_role(user, "accountant")
    h = _history_row(s, user, vendor_id, hid)
    if baseline.is_approved(h):
        svc.require_role(user, "approver")  # removing approved baseline changes what invoices are compared against
    audit(s, user.workspace_id, user.id, "history.deleted", h.id, history_public(h, {}), request.state.request_id)
    s.delete(h)


@router.post("/history/approve")
def approve_history(body: ApproveIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "approver")
    rows = list(s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.workspace_id == user.workspace_id, HistoricalInvoice.id.in_(body.ids))))
    if len(rows) != len(set(body.ids)):
        raise LookupError("one or more past invoices were not found")
    for h in rows:
        baseline.approve(h, user)
    audit(s, user.workspace_id, user.id, "history.approved", user.workspace_id, {"ids": sorted(body.ids)}, request.state.request_id)
    return {"approved": len(rows)}


# ---------------------------------------------------------------- purchase orders

@router.get("/purchase-orders")
def list_pos(vendor_id: str | None = None, status: str | None = Query(default=None, pattern="^(approved|pending)$"),
             user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    stmt = select(PurchaseOrder, Vendor.name).join(Vendor, Vendor.id == PurchaseOrder.vendor_id).where(PurchaseOrder.workspace_id == user.workspace_id)
    if vendor_id:
        stmt = stmt.where(PurchaseOrder.vendor_id == vendor_id)
    if status == "approved":
        stmt = stmt.where(PurchaseOrder.approved_at.is_not(None))
    elif status == "pending":
        stmt = stmt.where(PurchaseOrder.approved_at.is_(None))
    names = _names(s, user.workspace_id)
    return {"items": [po_public(p, names, vn) for p, vn in s.execute(stmt.order_by(PurchaseOrder.po_date.desc()).limit(1000))]}


@router.post("/purchase-orders", status_code=201)
def add_po(body: POIn, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    v = _vendor(s, user, body.vendor_id)
    po_no = body.po_number.strip()
    if s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == user.workspace_id, PurchaseOrder.po_number == po_no)).first():
        raise svc.Conflict(f"PO {po_no} already exists")
    p = PurchaseOrder(workspace_id=user.workspace_id, vendor_id=v.id, po_number=po_no, po_date=_date(body.po_date, "po_date"),
                      lines=_lines(body.lines, with_amount=False))
    baseline.stamp_new(p, user, "manual")
    s.add(p)
    s.flush()
    audit(s, user.workspace_id, user.id, "po.added", p.id, {"vendor_id": v.id, "po_number": po_no, "status": baseline.status(p)}, request.state.request_id)
    return po_public(p, _names(s, user.workspace_id), v.name)


def _po_row(s: Session, user: User, po_id: str) -> PurchaseOrder:
    p = s.get(PurchaseOrder, po_id)
    if p is None or p.workspace_id != user.workspace_id:
        raise LookupError("purchase order not found")
    return p


@router.patch("/purchase-orders/{po_id}")
def edit_po(po_id: str, body: POPatch, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    p = _po_row(s, user, po_id)
    before = po_public(p, {})
    if body.po_date is not None:
        p.po_date = _date(body.po_date, "po_date")
    if body.lines is not None:
        p.lines = _lines(body.lines, with_amount=False)
    reset = baseline.stamp_edit(p, user)
    audit(s, user.workspace_id, user.id, "po.edited", p.id, {"before": before, "after": po_public(p, {}), "approval_reset": reset}, request.state.request_id)
    return po_public(p, _names(s, user.workspace_id))


@router.delete("/purchase-orders/{po_id}", status_code=204)
def delete_po(po_id: str, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> None:
    svc.require_role(user, "accountant")
    p = _po_row(s, user, po_id)
    if baseline.is_approved(p):
        svc.require_role(user, "approver")
    audit(s, user.workspace_id, user.id, "po.deleted", p.id, po_public(p, {}), request.state.request_id)
    s.delete(p)


@router.post("/purchase-orders/approve")
def approve_pos(body: ApproveIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "approver")
    rows = list(s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == user.workspace_id, PurchaseOrder.id.in_(body.ids))))
    if len(rows) != len(set(body.ids)):
        raise LookupError("one or more purchase orders were not found")
    for p in rows:
        baseline.approve(p, user)
    audit(s, user.workspace_id, user.id, "po.approved", user.workspace_id, {"ids": sorted(body.ids)}, request.state.request_id)
    return {"approved": len(rows)}


# ---------------------------------------------------------------- approval queue

@router.get("/baseline/pending")
def pending(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    """What is waiting for an approver before it counts in comparisons."""
    ws = user.workspace_id
    names = _names(s, ws)
    vendors = {v.id: v.name for v in s.scalars(select(Vendor).where(Vendor.workspace_id == ws))}
    inv = list(s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.workspace_id == ws, HistoricalInvoice.approved_at.is_(None))
                         .order_by(HistoricalInvoice.invoice_date.desc()).limit(500)))
    pos = list(s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == ws, PurchaseOrder.approved_at.is_(None))
                         .order_by(PurchaseOrder.po_date.desc()).limit(500)))
    count = lambda M: s.scalar(select(func.count()).select_from(M).where(M.workspace_id == ws, M.approved_at.is_(None))) or 0  # noqa: E731
    return {"invoices": count(HistoricalInvoice), "purchase_orders": count(PurchaseOrder),
            "invoice_items": [{**history_public(h, names), "vendor": vendors.get(h.vendor_id)} for h in inv],
            "po_items": [po_public(p, names, vendors.get(p.vendor_id)) for p in pos]}
