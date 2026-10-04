"""Vendor master management (the internal baseline every invoice is compared against).

Trust rule: marking a bank account, domain or contact as *verified* asserts it was confirmed out-of-band.
That requires an approver and a written note, and is audited. Verified contacts are where verification
emails go; verified accounts/domains are what bank-change and new-domain signals compare against.
"""

from __future__ import annotations

import re
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from probity import graph_rel
from probity import services as svc
from probity.api.deps import current_user, db, require_mfa_for_approvals
from probity.db.audit import audit
from probity.db.models import (
    Case, CaseMemory, GraphEdge, HistoricalInvoice, PurchaseOrder, User, Vendor, VendorBankAccount, VendorContact, VendorDomain, iso,
)
from probity.guardrails import crypto
from probity.ingestion.validators import normalize_domain, valid_gstin, valid_ifsc

router = APIRouter(prefix="/api/v1", tags=["vendors"])
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _vendor(s: Session, user: User, vendor_id: str) -> Vendor:
    v = s.get(Vendor, vendor_id)
    if v is None or v.workspace_id != user.workspace_id:
        raise LookupError("vendor not found")
    return v


def _check_gstin(s: Session, ws: str, gstin: str | None, exclude_id: str | None = None) -> str | None:
    if not gstin:
        return None
    g = gstin.strip().upper()
    if not valid_gstin(g):
        raise svc.BadRequest(f"GSTIN {g} fails the checksum")
    dup = s.scalars(select(Vendor).where(Vendor.workspace_id == ws, Vendor.gstin == g, Vendor.id != (exclude_id or ""))).first()
    if dup:
        raise svc.Conflict(f"GSTIN {g} already belongs to vendor '{dup.name}'")
    return g


def _verify_gate(user: User, verified: bool, note: str | None) -> None:
    if verified:
        svc.require_role(user, "approver")
        if not note or len(note.strip()) < 5:
            raise svc.BadRequest("explain how this was verified out-of-band (e.g. called the known contact, bank letter)")


def vendor_summary(s: Session, v: Vendor) -> dict:
    q = lambda stmt: s.scalar(stmt) or 0  # noqa: E731
    last = s.scalar(select(func.max(HistoricalInvoice.invoice_date)).where(HistoricalInvoice.vendor_id == v.id))
    return {
        "id": v.id, "name": v.name, "gstin": v.gstin, "pan": v.pan, "address": v.address, "website": v.website,
        "archived": v.archived, "notes": v.notes, "created_at": iso(v.created_at),
        "invoices": q(select(func.count()).select_from(HistoricalInvoice).where(HistoricalInvoice.vendor_id == v.id)),
        "last_invoice_date": last.isoformat() if last else None,
        "cases": q(select(func.count()).select_from(Case).where(Case.vendor_id == v.id)),
        "open_cases": q(select(func.count()).select_from(Case).where(Case.vendor_id == v.id, Case.status.in_(["AWAITING_HUMAN", "AWAITING_VENDOR"]))),
        "verified_bank_accounts": q(select(func.count()).select_from(VendorBankAccount).where(VendorBankAccount.vendor_id == v.id, VendorBankAccount.verified.is_(True))),
        "verified_contacts": q(select(func.count()).select_from(VendorContact).where(VendorContact.vendor_id == v.id, VendorContact.verified.is_(True))),
        "previously_flagged": bool(s.scalar(select(func.count()).select_from(CaseMemory).where(
            CaseMemory.vendor_id == v.id, or_(CaseMemory.outcome == "CONFIRMED_ISSUE", CaseMemory.peak_tier.in_(["HIGH", "CRITICAL"]))))),
    }


# ---------------------------------------------------------------- vendors

@router.get("/vendors")
def list_vendors(q: str = "", include_archived: bool = False, limit: int = Query(200, le=1000), user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    stmt = select(Vendor).where(Vendor.workspace_id == user.workspace_id)
    if not include_archived:
        stmt = stmt.where(Vendor.archived.is_(False))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Vendor.name.ilike(like), Vendor.gstin.ilike(like)))
    return {"items": [vendor_summary(s, v) for v in s.scalars(stmt.order_by(Vendor.name).limit(limit))]}


class VendorIn(BaseModel):
    name: str = Field(min_length=2, max_length=300)
    gstin: str | None = Field(default=None, max_length=15)
    pan: str | None = Field(default=None, max_length=10)
    address: str | None = Field(default=None, max_length=1000)
    website: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)


@router.post("/vendors", status_code=201)
def create_vendor(body: VendorIn, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    gstin = _check_gstin(s, user.workspace_id, body.gstin)
    v = Vendor(workspace_id=user.workspace_id, name=body.name.strip(), gstin=gstin, pan=(body.pan or (gstin[2:12] if gstin else None)),
               address=body.address, website=normalize_domain(body.website) if body.website else None, notes=body.notes)
    s.add(v)
    s.flush()
    if v.website:
        s.add(VendorDomain(workspace_id=user.workspace_id, vendor_id=v.id, domain=v.website, verified=False))
    graph_rel.index_vendor_master(s, user.workspace_id)
    audit(s, user.workspace_id, user.id, "vendor.created", v.id, {"name": v.name, "gstin": gstin}, request.state.request_id)
    return vendor_summary(s, v)


@router.get("/vendors/{vendor_id}")
def get_vendor(vendor_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    v = _vendor(s, user, vendor_id)
    q = lambda M: s.scalars(select(M).where(M.vendor_id == v.id))  # noqa: E731
    hist = list(s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.vendor_id == v.id).order_by(HistoricalInvoice.invoice_date)))
    return {
        **vendor_summary(s, v),
        "accounts": [{"id": a.id, "account": crypto.mask(a.last4), "ifsc": a.ifsc, "verified": a.verified, "verified_method": a.verified_method,
                      "first_seen": a.first_seen.isoformat() if a.first_seen else None, "last_seen": a.last_seen.isoformat() if a.last_seen else None} for a in q(VendorBankAccount)],
        "domains": [{"id": d.id, "domain": d.domain, "verified": d.verified, "verified_method": d.verified_method} for d in q(VendorDomain)],
        "contacts": [{"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "verified": c.verified} for c in q(VendorContact)],
        "price_history": [{"date": h.invoice_date.isoformat(), "invoice_number": h.invoice_number, "total_minor": h.total_minor, "items": h.line_items} for h in hist],
        "purchase_orders": [{"po_number": p.po_number, "po_date": p.po_date.isoformat(), "lines": p.lines} for p in q(PurchaseOrder)],
        "prior_cases": [{"case_id": m.case_id, "outcome": m.outcome, "summary": m.summary, "peak_score": m.peak_score, "peak_tier": m.peak_tier, "at": iso(m.created_at)}
                        for m in s.scalars(select(CaseMemory).where(CaseMemory.vendor_id == v.id))],
    }


class VendorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=300)
    gstin: str | None = Field(default=None, max_length=15)
    address: str | None = Field(default=None, max_length=1000)
    website: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=4000)
    archived: bool | None = None


@router.patch("/vendors/{vendor_id}")
def update_vendor(vendor_id: str, body: VendorPatch, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    v = _vendor(s, user, vendor_id)
    before = {"name": v.name, "gstin": v.gstin, "address": v.address, "website": v.website, "archived": v.archived}
    data = body.model_dump(exclude_unset=True)
    if "gstin" in data:
        svc.require_role(user, "approver")  # identity data drives the identity-mismatch signal
        v.gstin = _check_gstin(s, user.workspace_id, data["gstin"], exclude_id=v.id)
    for k in ("name", "address", "notes", "archived"):
        if k in data:
            setattr(v, k, data[k])
    if "website" in data:
        v.website = normalize_domain(data["website"]) if data["website"] else None
    graph_rel.index_vendor_master(s, user.workspace_id)
    audit(s, user.workspace_id, user.id, "vendor.updated", v.id, {"before": before, "changes": data}, request.state.request_id)
    return vendor_summary(s, v)


# ---------------------------------------------------------------- bank accounts

class BankIn(BaseModel):
    account_number: str = Field(min_length=6, max_length=34)
    ifsc: str | None = Field(default=None, max_length=11)
    verified: bool = False
    verification_note: str | None = Field(default=None, max_length=1000)

    @field_validator("account_number")
    @classmethod
    def _digits(cls, v: str) -> str:
        n = re.sub(r"[\s-]", "", v)
        if not re.fullmatch(r"[0-9A-Za-z]{6,34}", n):
            raise ValueError("account number must be 6–34 letters/digits")
        return n


@router.post("/vendors/{vendor_id}/bank-accounts", status_code=201)
def add_bank_account(vendor_id: str, body: BankIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    _verify_gate(user, body.verified, body.verification_note)
    v = _vendor(s, user, vendor_id)
    if body.ifsc and not valid_ifsc(body.ifsc):
        raise svc.BadRequest(f"IFSC {body.ifsc} is not valid")
    h = crypto.account_hmac(body.account_number)
    if s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == v.id, VendorBankAccount.acct_hmac == h)).first():
        raise svc.Conflict("this account is already on the vendor")
    a = VendorBankAccount(workspace_id=user.workspace_id, vendor_id=v.id, last4=crypto.last4(body.account_number), acct_hmac=h,
                          acct_enc=crypto.encrypt(body.account_number), ifsc=(body.ifsc or "").upper() or None, verified=body.verified,
                          verified_method="manual" if body.verified else None, verified_by=user.id if body.verified else None)
    s.add(a)
    s.flush()
    graph_rel.add_edge(s, user.workspace_id, v.id, "has_bank", "bank", h, crypto.mask(a.last4))
    audit(s, user.workspace_id, user.id, "vendor.bank_added", v.id, {"account": crypto.mask(a.last4), "verified": a.verified, "note": body.verification_note}, request.state.request_id)
    return {"id": a.id, "account": crypto.mask(a.last4), "ifsc": a.ifsc, "verified": a.verified}


class VerifyPatch(BaseModel):
    verified: bool
    verification_note: str | None = Field(default=None, max_length=1000)


@router.patch("/vendors/{vendor_id}/bank-accounts/{account_id}")
def verify_bank_account(vendor_id: str, account_id: str, body: VerifyPatch, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "approver")
    _verify_gate(user, body.verified, body.verification_note)
    v = _vendor(s, user, vendor_id)
    a = s.get(VendorBankAccount, account_id)
    if a is None or a.vendor_id != v.id:
        raise LookupError("bank account not found")
    a.verified, a.verified_method, a.verified_by = body.verified, ("manual" if body.verified else None), (user.id if body.verified else None)
    audit(s, user.workspace_id, user.id, "vendor.bank_verified" if body.verified else "vendor.bank_unverified", v.id,
          {"account": crypto.mask(a.last4), "note": body.verification_note}, request.state.request_id)
    return {"id": a.id, "account": crypto.mask(a.last4), "verified": a.verified}


@router.delete("/vendors/{vendor_id}/bank-accounts/{account_id}", status_code=204)
def remove_bank_account(vendor_id: str, account_id: str, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> None:
    svc.require_role(user, "approver")
    v = _vendor(s, user, vendor_id)
    a = s.get(VendorBankAccount, account_id)
    if a is None or a.vendor_id != v.id:
        raise LookupError("bank account not found")
    for e in s.scalars(select(GraphEdge).where(GraphEdge.workspace_id == user.workspace_id, GraphEdge.src_id == v.id, GraphEdge.dst_type == "bank", GraphEdge.dst_id == a.acct_hmac)):
        s.delete(e)
    s.delete(a)
    audit(s, user.workspace_id, user.id, "vendor.bank_removed", v.id, {"account": crypto.mask(a.last4)}, request.state.request_id)


# ---------------------------------------------------------------- domains

class DomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    verified: bool = False
    verification_note: str | None = Field(default=None, max_length=1000)


@router.post("/vendors/{vendor_id}/domains", status_code=201)
def add_domain(vendor_id: str, body: DomainIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    _verify_gate(user, body.verified, body.verification_note)
    v = _vendor(s, user, vendor_id)
    dom = normalize_domain(body.domain)
    if not dom or not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", dom):
        raise svc.BadRequest("not a valid domain")
    if s.scalars(select(VendorDomain).where(VendorDomain.vendor_id == v.id, VendorDomain.domain == dom)).first():
        raise svc.Conflict("domain already on the vendor")
    d = VendorDomain(workspace_id=user.workspace_id, vendor_id=v.id, domain=dom, verified=body.verified, verified_method="manual" if body.verified else None)
    s.add(d)
    s.flush()
    graph_rel.add_edge(s, user.workspace_id, v.id, "uses_domain", "domain", dom, dom)
    audit(s, user.workspace_id, user.id, "vendor.domain_added", v.id, {"domain": dom, "verified": d.verified, "note": body.verification_note}, request.state.request_id)
    return {"id": d.id, "domain": dom, "verified": d.verified}


@router.patch("/vendors/{vendor_id}/domains/{domain_id}")
def verify_domain(vendor_id: str, domain_id: str, body: VerifyPatch, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "approver")
    _verify_gate(user, body.verified, body.verification_note)
    v = _vendor(s, user, vendor_id)
    d = s.get(VendorDomain, domain_id)
    if d is None or d.vendor_id != v.id:
        raise LookupError("domain not found")
    d.verified, d.verified_method = body.verified, ("manual" if body.verified else None)
    audit(s, user.workspace_id, user.id, "vendor.domain_verified" if body.verified else "vendor.domain_unverified", v.id, {"domain": d.domain, "note": body.verification_note}, request.state.request_id)
    return {"id": d.id, "domain": d.domain, "verified": d.verified}


@router.delete("/vendors/{vendor_id}/domains/{domain_id}", status_code=204)
def remove_domain(vendor_id: str, domain_id: str, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> None:
    svc.require_role(user, "approver")
    v = _vendor(s, user, vendor_id)
    d = s.get(VendorDomain, domain_id)
    if d is None or d.vendor_id != v.id:
        raise LookupError("domain not found")
    s.delete(d)
    audit(s, user.workspace_id, user.id, "vendor.domain_removed", v.id, {"domain": d.domain}, request.state.request_id)


# ---------------------------------------------------------------- contacts

class ContactIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    email: str = Field(max_length=200, pattern=EMAIL_RE)
    phone: str | None = Field(default=None, max_length=40)
    verified: bool = False
    verification_note: str | None = Field(default=None, max_length=1000)


@router.post("/vendors/{vendor_id}/contacts", status_code=201)
def add_contact(vendor_id: str, body: ContactIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    _verify_gate(user, body.verified, body.verification_note)
    v = _vendor(s, user, vendor_id)
    c = VendorContact(workspace_id=user.workspace_id, vendor_id=v.id, name=body.name, email=body.email.strip().lower(), phone=body.phone, verified=body.verified)
    s.add(c)
    s.flush()
    audit(s, user.workspace_id, user.id, "vendor.contact_added", v.id, {"email": c.email, "verified": c.verified, "note": body.verification_note}, request.state.request_id)
    return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "verified": c.verified}


class ContactPatch(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    verified: bool | None = None
    verification_note: str | None = Field(default=None, max_length=1000)


@router.patch("/vendors/{vendor_id}/contacts/{contact_id}")
def update_contact(vendor_id: str, contact_id: str, body: ContactPatch, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    v = _vendor(s, user, vendor_id)
    c = s.get(VendorContact, contact_id)
    if c is None or c.vendor_id != v.id:
        raise LookupError("contact not found")
    if body.verified is not None:
        if body.verified != c.verified:
            svc.require_role(user, "approver")
        _verify_gate(user, bool(body.verified), body.verification_note)
        c.verified = body.verified
    if body.name is not None:
        c.name = body.name
    if body.phone is not None:
        c.phone = body.phone
    audit(s, user.workspace_id, user.id, "vendor.contact_updated", v.id, {"email": c.email, "verified": c.verified, "note": body.verification_note}, request.state.request_id)
    return {"id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "verified": c.verified}


@router.delete("/vendors/{vendor_id}/contacts/{contact_id}", status_code=204)
def remove_contact(vendor_id: str, contact_id: str, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> None:
    svc.require_role(user, "approver")
    v = _vendor(s, user, vendor_id)
    c = s.get(VendorContact, contact_id)
    if c is None or c.vendor_id != v.id:
        raise LookupError("contact not found")
    s.delete(c)
    audit(s, user.workspace_id, user.id, "vendor.contact_removed", v.id, {"email": c.email}, request.state.request_id)


# ---------------------------------------------------------------- graph

@router.get("/vendors/{vendor_id}/graph")
def vendor_graph(vendor_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return graph_rel.vendor_graph(s, user.workspace_id, vendor_id)


@router.get("/graph/shared-attributes")
def shared_attrs(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return {"items": graph_rel.shared_attributes(s, user.workspace_id)}

