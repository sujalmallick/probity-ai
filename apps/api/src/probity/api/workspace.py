"""Workspace-level APIs for real users: public app config, onboarding checklist, CSV imports, case notes,
workspace settings and data export."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from probity import __version__, importer
from probity import services as svc
from probity.api.deps import current_user, db
from probity.config import get_settings
from probity.db.audit import audit
from probity.db.models import (
    Case, CaseNote, HistoricalInvoice, ImportJob, Notification, PurchaseOrder, User, Vendor, VendorBankAccount, VendorContact, Workspace, iso,
)
from probity.policy import get_policy

router = APIRouter(prefix="/api/v1", tags=["workspace"])
ImportKind = Literal["vendors", "invoices", "purchase_orders"]


# ---------------------------------------------------------------- public app config

@router.get("/app/config")
def app_config() -> dict:
    """Public, unauthenticated. Tells the web app what to show; contains no secrets."""
    st = get_settings()
    demo = st.demo_features and st.env != "prod"
    live = lambda ok: "live" if ok else "offline"  # noqa: E731
    return {
        "env": st.env,
        "version": __version__,
        "auth": {"mode": st.auth_mode, "demo_login": demo and st.auth_mode == "local", "sign_up": st.auth_mode == "clerk"},
        "features": {"landing_page": st.show_landing_page, "demo": demo, "benchmark": demo, "simulated_inbox": demo and st.email_backend == "outbox"},
        "integrations": {
            "ai": live(st.llm_mode == "live" and bool(st.anthropic_api_key)),
            "web_search": live(st.tools_mode == "live" and bool(st.tavily_api_key)),
            "email": live(st.email_backend != "outbox"),
            "storage": "cloud" if st.storage_backend == "s3" else "local",
            "antivirus": "on" if st.clamav_host else "off",
            "ocr": "on" if st.ocr_enabled else "off",
        },
        "limits": {"max_upload_mb": 15, "max_import_mb": 5},
    }


# ---------------------------------------------------------------- onboarding

@router.get("/workspace/onboarding")
def onboarding(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    ws = user.workspace_id
    count = lambda stmt: s.scalar(stmt) or 0  # noqa: E731
    vendors = count(select(func.count()).select_from(Vendor).where(Vendor.workspace_id == ws, Vendor.archived.is_(False)))
    verified_accts = count(select(func.count()).select_from(VendorBankAccount).where(VendorBankAccount.workspace_id == ws, VendorBankAccount.verified.is_(True)))
    verified_contacts = count(select(func.count()).select_from(VendorContact).where(VendorContact.workspace_id == ws, VendorContact.verified.is_(True)))
    history = count(select(func.count()).select_from(HistoricalInvoice).where(HistoricalInvoice.workspace_id == ws))
    pos = count(select(func.count()).select_from(PurchaseOrder).where(PurchaseOrder.workspace_id == ws))
    members = count(select(func.count()).select_from(User).where(User.workspace_id == ws, User.active.is_(True)))
    cases = count(select(func.count()).select_from(Case).where(Case.workspace_id == ws))
    policy_reviewed = bool(get_policy(s.get(Workspace, ws)).get("reviewed_at"))
    steps = [
        {"key": "vendors", "title": "Add your vendors", "required": True, "done": vendors > 0, "detail": f"{vendors} vendor(s)",
         "action": {"type": "import", "kind": "vendors"}, "why": "Invoices are matched to your vendor master by GSTIN and name."},
        {"key": "verified_bank", "title": "Verify vendor bank accounts", "required": True, "done": verified_accts > 0, "detail": f"{verified_accts} verified account(s)",
         "action": {"type": "route", "to": "/vendors"}, "why": "Bank-change detection compares every invoice with accounts you have verified."},
        {"key": "verified_contacts", "title": "Add verified vendor contacts", "required": False, "done": verified_contacts > 0, "detail": f"{verified_contacts} verified contact(s)",
         "action": {"type": "route", "to": "/vendors"}, "why": "Verification emails go only to contacts you trust — never to the address printed on an invoice."},
        {"key": "history", "title": "Import past invoices", "required": True, "done": history >= 10, "detail": f"{history} historical invoice(s); 10+ recommended",
         "action": {"type": "import", "kind": "invoices"}, "why": "Price anomalies and duplicates are judged against your own payment history."},
        {"key": "purchase_orders", "title": "Import purchase orders", "required": False, "done": pos > 0, "detail": f"{pos} PO(s)",
         "action": {"type": "import", "kind": "purchase_orders"}, "why": "Enables quantity-vs-PO and missing-PO checks."},
        {"key": "team", "title": "Invite your approver", "required": False, "done": members > 1, "detail": f"{members} member(s)",
         "action": {"type": "route", "to": "/settings/team"}, "why": "Decisions on held invoices need someone with the approver role."},
        {"key": "policy", "title": "Review risk policy", "required": False, "done": policy_reviewed, "detail": "auto-clear limit, research threshold, MFA",
         "action": {"type": "route", "to": "/settings/policy"}, "why": "Decide what may clear automatically."},
        {"key": "first_case", "title": "Investigate your first invoice", "required": True, "done": cases > 0, "detail": f"{cases} case(s)",
         "action": {"type": "route", "to": "/cases/new"}, "why": "Upload a PDF, image or email invoice."},
    ]
    required = [st for st in steps if st["required"]]
    return {"complete": all(st["done"] for st in required), "progress": f"{sum(st['done'] for st in steps)}/{len(steps)}", "steps": steps}


class WorkspacePatch(BaseModel):
    name: str = Field(min_length=2, max_length=200)


@router.patch("/workspace")
def rename_workspace(body: WorkspacePatch, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    ws = s.get(Workspace, user.workspace_id)
    assert ws
    before = ws.name
    ws.name = body.name.strip()
    audit(s, ws.id, user.id, "workspace.renamed", ws.id, {"before": before, "after": ws.name}, request.state.request_id)
    return {"id": ws.id, "name": ws.name}


# ---------------------------------------------------------------- CSV imports

@router.get("/imports/templates/{kind}")
def import_template(kind: ImportKind, user: User = Depends(current_user)) -> Response:
    return Response(importer.template_csv(kind), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="probity-{kind}-template.csv"'})


@router.get("/imports/spec")
def import_spec(user: User = Depends(current_user)) -> dict:
    return {k: {"columns": v["columns"], "required": v["required"], "help": v["help"]} for k, v in importer.TEMPLATES.items()}


@router.post("/imports/{kind}")
async def run_import(
    kind: ImportKind, request: Request, file: UploadFile = File(...), dry_run: bool = Query(True), skip_invalid: bool = Query(False),
    user: User = Depends(current_user), s: Session = Depends(db),
) -> dict:
    """dry_run=true (default) validates and returns a report without writing. dry_run=false commits:
    refused if any row is invalid unless skip_invalid=true (then only valid rows are written)."""
    svc.require_role(user, "accountant")
    data = await file.read(importer.MAX_BYTES + 1)
    try:
        rep = importer.run_import(s, user, kind, data, commit=False, can_verify=svc.ROLES.index(user.role) >= svc.ROLES.index("approver"))
    except (importer.ImportRejected, UnicodeDecodeError) as e:
        raise svc.BadRequest(f"cannot read CSV: {e}") from e
    if dry_run:
        return {**rep.as_dict(), "dry_run": True, "committed": False}
    if rep.errors and not skip_invalid:
        raise svc.BadRequest(f"{len(rep.errors)} row(s) have errors — fix them or retry with skip_invalid=true")
    rep = importer.run_import(s, user, kind, data, commit=True, can_verify=svc.ROLES.index(user.role) >= svc.ROLES.index("approver"))
    job = ImportJob(workspace_id=user.workspace_id, kind=kind, filename=(file.filename or "import.csv")[:300], status="committed",
                    rows_total=rep.rows_total, rows_ok=rep.rows_ok, created=rep.created, updated=rep.updated, errors=rep.errors[:200], actor_id=user.id)
    s.add(job)
    s.flush()
    audit(s, user.workspace_id, user.id, "import.committed", job.id, {"kind": kind, "rows_ok": rep.rows_ok, "created": rep.created, "updated": rep.updated,
                                                                       "skipped": len(rep.errors)}, request.state.request_id)
    return {**rep.as_dict(), "dry_run": False, "committed": True, "import_id": job.id}


@router.get("/imports")
def list_imports(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    rows = s.scalars(select(ImportJob).where(ImportJob.workspace_id == user.workspace_id).order_by(ImportJob.created_at.desc()).limit(100))
    names = {u.id: u.name for u in s.scalars(select(User).where(User.workspace_id == user.workspace_id))}
    return {"items": [{"id": j.id, "kind": j.kind, "filename": j.filename, "status": j.status, "rows_total": j.rows_total, "rows_ok": j.rows_ok,
                       "created": j.created, "updated": j.updated, "skipped": len(j.errors), "by": names.get(j.actor_id), "at": iso(j.created_at)} for j in rows]}


# ---------------------------------------------------------------- case notes

class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.get("/cases/{case_id}/notes")
def list_notes(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.get_case(s, user.workspace_id, case_id)
    names = {u.id: u.name for u in s.scalars(select(User).where(User.workspace_id == user.workspace_id))}
    rows = s.scalars(select(CaseNote).where(CaseNote.case_id == case_id).order_by(CaseNote.created_at))
    return {"items": [{"id": n.id, "text": n.text, "author": names.get(n.author_id, n.author_id), "author_id": n.author_id, "at": iso(n.created_at)} for n in rows]}


@router.post("/cases/{case_id}/notes", status_code=201)
def add_note(case_id: str, body: NoteIn, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    case = svc.get_case(s, user.workspace_id, case_id)
    n = CaseNote(workspace_id=user.workspace_id, case_id=case.id, author_id=user.id, text=body.text.strip())
    s.add(n)
    s.flush()
    audit(s, user.workspace_id, user.id, "note.added", case.id, {"note_id": n.id, "case_id": case.id}, request.state.request_id)
    return {"id": n.id, "text": n.text, "author": user.name, "author_id": user.id, "at": iso(n.created_at)}


# ---------------------------------------------------------------- notifications

@router.get("/notifications")
def list_notifications(unread_only: bool = False, limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    stmt = select(Notification).where(Notification.workspace_id == user.workspace_id, Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = s.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit))
    unread = s.scalar(select(func.count()).select_from(Notification).where(
        Notification.workspace_id == user.workspace_id, Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    return {"unread": unread, "items": [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "case_id": n.case_id,
                                         "read": n.read_at is not None, "at": iso(n.created_at)} for n in rows]}


@router.post("/notifications/{notification_id}/read")
def mark_read(notification_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    n = s.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise LookupError("notification not found")
    n.read_at = n.read_at or datetime.now(timezone.utc)
    return {"id": n.id, "read": True}


@router.post("/notifications/read-all")
def mark_all_read(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    now = datetime.now(timezone.utc)
    rows = list(s.scalars(select(Notification).where(Notification.workspace_id == user.workspace_id, Notification.user_id == user.id, Notification.read_at.is_(None))))
    for n in rows:
        n.read_at = now
    return {"marked": len(rows)}


# ---------------------------------------------------------------- data export (DPDP: portability)

@router.get("/workspace/export")
def export_workspace(request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> JSONResponse:
    """Owner-only machine-readable export of the workspace's business data (bank numbers stay masked)."""
    svc.require_role(user, "owner")
    from probity.api.vendors import get_vendor

    ws = user.workspace_id
    vendors = [get_vendor(v.id, user, s) for v in s.scalars(select(Vendor).where(Vendor.workspace_id == ws))]
    cases = [svc.serialize_case(s, c, user) for c in s.scalars(select(Case).where(Case.workspace_id == ws).order_by(Case.created_at))]
    audit(s, ws, user.id, "workspace.exported", ws, {"vendors": len(vendors), "cases": len(cases)}, request.state.request_id)
    body = json.loads(json.dumps({"workspace_id": ws, "vendors": vendors, "cases": cases}, default=str))
    return JSONResponse(body, headers={"Content-Disposition": f'attachment; filename="probity-workspace-{ws}.json"'})
