"""FastAPI application — API.md v1."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from probity import services as svc
from probity.api.deps import current_user, db, issue_token, upload_limit
from probity.config import REPO_ROOT, get_settings
from probity.db.audit import audit, verify_chain
from probity.db.models import iso, AuditLog, Case, CaseMemory, Document, HistoricalInvoice, User, Vendor, VendorBankAccount, VendorContact, VendorDomain, Workspace
from probity.db.session import init_db, session_scope, set_tenant
from probity.guardrails import crypto
from probity.ingestion.parse import UnsupportedDocument
from probity.policy import get_policy
from probity.risk.engine import WEIGHTS

app = FastAPI(title="Probity API", version="0.1.0", description="Evidence before payment.")
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if not request.url.path.endswith("/file"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["X-Frame-Options"] = "DENY"
    return response


def _err(status: int, code: str, message: str, request: Request) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, "details": {}, "request_id": getattr(request.state, "request_id", None)}}, status_code=status)


@app.exception_handler(svc.Conflict)
async def _conflict(request: Request, e: svc.Conflict):  # type: ignore[no-untyped-def]
    return _err(409, "conflict", str(e), request)


@app.exception_handler(svc.Forbidden)
async def _forbidden(request: Request, e: svc.Forbidden):  # type: ignore[no-untyped-def]
    return _err(403, "forbidden", str(e), request)


@app.exception_handler(svc.BadRequest)
async def _bad(request: Request, e: svc.BadRequest):  # type: ignore[no-untyped-def]
    return _err(400, "bad_request", str(e), request)


@app.exception_handler(LookupError)
async def _notfound(request: Request, e: LookupError):  # type: ignore[no-untyped-def]
    return _err(404, "not_found", str(e), request)


@app.exception_handler(UnsupportedDocument)
async def _unsupported(request: Request, e: UnsupportedDocument):  # type: ignore[no-untyped-def]
    return _err(415, "unsupported_document", str(e), request)


@app.exception_handler(HTTPException)
async def _http(request: Request, e: HTTPException):  # type: ignore[no-untyped-def]
    return _err(e.status_code, "http_error", str(e.detail), request)


API = "/api/v1"


# ---------------------------------------------------------------- ops

@app.get(f"{API}/health")
def health() -> dict:
    return {"status": "ok"}


@app.get(f"{API}/ready")
def ready(s: Session = Depends(db)) -> dict:
    s.execute(select(1))
    st = get_settings()
    return {"database": "ok", "tools_mode": st.tools_mode, "llm_mode": st.llm_mode, "auth_mode": st.auth_mode, "env": st.env}


# ---------------------------------------------------------------- auth (AUTH_MODE=local, non-prod)

@app.get(f"{API}/auth/demo-users")
def demo_users(s: Session = Depends(db)) -> list[dict]:
    if get_settings().auth_mode != "local":
        raise HTTPException(404, "not available")
    return [{"id": u.id, "name": u.name, "email": u.email, "role": u.role} for u in s.scalars(select(User).order_by(User.role))]


class DemoLogin(BaseModel):
    user_id: str


@app.post(f"{API}/auth/demo-login")
def demo_login(body: DemoLogin, request: Request, s: Session = Depends(db)) -> dict:
    if get_settings().auth_mode != "local":
        raise HTTPException(404, "not available")
    u = s.get(User, body.user_id)
    if not u:
        raise HTTPException(404, "user not found")
    set_tenant(s, u.workspace_id)
    audit(s, u.workspace_id, u.id, "auth.login", u.id, {"mode": "local"}, request.state.request_id)
    return {"token": issue_token(u), "user": {"id": u.id, "name": u.name, "role": u.role, "workspace_id": u.workspace_id}}


@app.get(f"{API}/me")
def me(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    ws = s.get(Workspace, user.workspace_id)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "workspace": {"id": ws.id, "name": ws.name} if ws else None}


@app.get(f"{API}/workspace/policy")
def get_ws_policy(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    p = get_policy(s.get(Workspace, user.workspace_id))
    return {**p, "weights": {**WEIGHTS[p["weights_version"]], **p["weight_overrides"]}, "tiers": {"LOW": "0–29", "MEDIUM": "30–59", "HIGH": "60–79", "CRITICAL": "80–100"}}


@app.put(f"{API}/workspace/policy")
def put_ws_policy(body: dict[str, Any], request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    ws = s.get(Workspace, user.workspace_id)
    assert ws
    allowed = {"auto_clear_enabled", "auto_clear_max_amount_minor", "external_research_amount_minor", "dual_approval_amount_minor", "weight_overrides", "demo_agent_delay_ms"}
    bad = set(body) - allowed
    if bad:
        raise svc.BadRequest(f"unknown policy keys: {sorted(bad)}")
    before = dict(ws.policy or {})
    ws.policy = {**before, **body}
    audit(s, ws.id, user.id, "policy.updated", ws.id, {"before": before, "after": ws.policy}, request.state.request_id)
    return get_policy(ws)


# ---------------------------------------------------------------- documents & cases

@app.post(f"{API}/documents", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), user: User = Depends(upload_limit), s: Session = Depends(db)) -> dict:
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise svc.BadRequest("file exceeds 15 MB")
    doc, duplicate_of = svc.upload_document(s, user, file.filename or "upload", data)
    return {"document_id": doc.id, "sha256": doc.sha256, "filename": doc.filename, "mime": doc.mime, "duplicate_of": duplicate_of}


@app.get(f"{API}/documents/{{doc_id}}")
def get_document(doc_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    d = s.get(Document, doc_id)
    if not d or d.workspace_id != user.workspace_id:
        raise LookupError("document not found")
    return {"id": d.id, "filename": d.filename, "mime": d.mime, "size": d.size, "sha256": d.sha256, "url": f"{API}/documents/{d.id}/file"}


@app.get(f"{API}/documents/{{doc_id}}/file")
def get_document_file(doc_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> FileResponse:
    d = s.get(Document, doc_id)
    if not d or d.workspace_id != user.workspace_id:
        raise LookupError("document not found")
    return FileResponse(d.storage_path, media_type=d.mime, filename=d.filename, content_disposition_type="inline")


class CreateCase(BaseModel):
    document_id: str
    corrections: dict[str, Any] | None = None


@app.post(f"{API}/cases", status_code=201)
def create_case(body: CreateCase, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    c = svc.create_case(s, user, body.document_id, body.corrections)
    return {"case_id": c.id, "number": c.number, "status": c.status}


@app.get(f"{API}/cases")
def list_cases(
    tier: str | None = None, status: str | None = None, vendor_id: str | None = None, limit: int = Query(50, le=200),
    user: User = Depends(current_user), s: Session = Depends(db),
) -> dict:
    q = select(Case).where(Case.workspace_id == user.workspace_id)
    if status:
        q = q.where(Case.status == status)
    if vendor_id:
        q = q.where(Case.vendor_id == vendor_id)
    rows = [svc.serialize_case(s, c, user, full=False) for c in s.scalars(q.order_by(Case.created_at.desc()).limit(limit))]
    if tier:
        rows = [r for r in rows if (r["risk"] or {}).get("tier") == tier]
    return {"items": rows, "next_cursor": None}


@app.get(f"{API}/cases/{{case_id}}")
def get_case(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return svc.serialize_case(s, svc.get_case(s, user.workspace_id, case_id), user)


@app.get(f"{API}/cases/{{case_id}}/evidence")
def get_evidence(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return {"items": svc.case_evidence(s, svc.get_case(s, user.workspace_id, case_id))}


@app.get(f"{API}/cases/{{case_id}}/explain")
def explain(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    case = svc.get_case(s, user.workspace_id, case_id)
    full = svc.serialize_case(s, case, user)
    ev = {e["id"]: e for e in svc.case_evidence(s, case)}
    claims = {c["id"]: c for c in full["claims"]}
    steps = []
    for i, ct in enumerate([c for c in (case.risk or {}).get("contributions", [])], start=1):
        cl = claims.get(ct["claim_id"] or "")
        steps.append({
            "n": i, "signal": ct["signal"], "label": ct["label"], "points": ct["points"], "status": ct["status"],
            "claim": cl["statement"] if cl else None, "verification": cl["verifier_notes"] if cl else ct["note"],
            "observed": ct.get("observed"), "baseline": ct.get("baseline"),
            "evidence": [ev[i] for i in (cl["evidence_ids"] if cl else []) if i in ev],
        })
    return {"score": case.risk.get("score"), "tier": case.risk.get("tier"), "weights_version": case.risk.get("weights_version"), "steps": steps}


@app.get(f"{API}/cases/{{case_id}}/audit")
def case_audit(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.get_case(s, user.workspace_id, case_id)
    rows = s.scalars(select(AuditLog).where(AuditLog.workspace_id == user.workspace_id, or_(AuditLog.entity == case_id, AuditLog.data["case_id"].as_string() == case_id)).order_by(AuditLog.id))
    ok, bad = verify_chain(s, user.workspace_id)
    return {"chain_valid": ok, "first_bad_id": bad, "items": [{"id": r.id, "actor": r.actor, "action": r.action, "data": r.data, "hash": r.hash[:16], "prev_hash": r.prev_hash[:16], "ts": iso(r.ts)} for r in rows]}


@app.get(f"{API}/cases/{{case_id}}/export")
def export_case(case_id: str, request: Request, format: Literal["json"] = "json", user: User = Depends(current_user), s: Session = Depends(db)) -> JSONResponse:
    case = svc.get_case(s, user.workspace_id, case_id)
    audit(s, user.workspace_id, user.id, "case.exported", case.id, {"format": format}, request.state.request_id)
    body = {"case": svc.serialize_case(s, case, user), "evidence": svc.case_evidence(s, case), "audit": case_audit(case_id, user, s)}
    return JSONResponse(json.loads(json.dumps(body, default=str)), headers={"Content-Disposition": f'attachment; filename="probity-case-{case.number}.json"'})


@app.get(f"{API}/cases/{{case_id}}/events")
async def case_events(case_id: str, request: Request, user: User = Depends(current_user)) -> StreamingResponse:
    ws = user.workspace_id
    with session_scope(ws) as s:
        svc.get_case(s, ws, case_id)
    last = int(request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id") or 0)

    async def gen():  # type: ignore[no-untyped-def]
        nonlocal last
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            rows = await asyncio.to_thread(svc.events_after, ws, case_id, last)
            for r in rows:
                last = r.id
                payload = {"seq": r.id, "ts": iso(r.ts), "case_id": case_id, "type": r.type, "agent": r.agent, "status": r.status, "message": r.message, "data": r.data}
                yield f"id: {r.id}\nevent: message\ndata: {json.dumps(payload, default=str)}\n\n"
            idle = 0 if rows else idle + 1
            if idle and idle % 30 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- decisions & actions

class DecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT", "REQUEST_VERIFICATION", "INVESTIGATE_FURTHER"]
    reason: str = ""


@app.post(f"{API}/cases/{{case_id}}/decision")
def decision(case_id: str, body: DecisionIn, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    c = svc.decide(s, user, case_id, body.decision, body.reason)
    return {"status": c.status}


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.post(f"{API}/cases/{{case_id}}/notes", status_code=201)
def add_note(case_id: str, body: NoteIn, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    c = svc.get_case(s, user.workspace_id, case_id)
    audit(s, user.workspace_id, user.id, "note.added", c.id, {"text": body.text}, request.state.request_id)
    return {"ok": True}


@app.get(f"{API}/cases/{{case_id}}/drafts")
def drafts(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return {"items": svc.serialize_case(s, svc.get_case(s, user.workspace_id, case_id), user)["drafts"]}


class DraftPatch(BaseModel):
    subject: str | None = None
    body: str | None = None


@app.patch(f"{API}/cases/{{case_id}}/drafts/{{draft_id}}")
def patch_draft(case_id: str, draft_id: str, body: DraftPatch, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    d = svc.update_draft(s, user, case_id, draft_id, body.subject, body.body)
    return {"id": d.id, "subject": d.subject, "body": d.body}


class SendIn(BaseModel):
    override_unverified_recipient: bool = False


@app.post(f"{API}/cases/{{case_id}}/drafts/{{draft_id}}/send")
def send(case_id: str, draft_id: str, body: SendIn | None = None, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    d = svc.send_draft(s, user, case_id, draft_id, (body or SendIn()).override_unverified_recipient)
    return {"status": d.status, "sent_at": d.sent_at}


class ReplyIn(BaseModel):
    from_email: str
    subject: str = ""
    body: str = Field(min_length=1, max_length=20000)


@app.post(f"{API}/cases/{{case_id}}/vendor-reply")
def vendor_reply(case_id: str, body: ReplyIn, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    return svc.vendor_reply(s, user.workspace_id, user.id, case_id, body.from_email, body.subject, body.body)


class OOBIn(BaseModel):
    claim_ids: list[str]
    method: Literal["phone_known_contact", "bank_letter", "in_person"]
    note: str


@app.post(f"{API}/cases/{{case_id}}/out-of-band-confirmation")
def oob(case_id: str, body: OOBIn, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return svc.confirm_out_of_band(s, user, case_id, body.claim_ids, body.method, body.note)


@app.post(f"{API}/cases/{{case_id}}/rescore")
def rescore(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    svc.get_case(s, user.workspace_id, case_id)
    s.commit()
    return svc.rescore(user.workspace_id, case_id)


class CloseIn(BaseModel):
    outcome: Literal["CONFIRMED_ISSUE", "CLEARED", "INCONCLUSIVE"]
    resolution: str = ""


@app.post(f"{API}/cases/{{case_id}}/close")
def close(case_id: str, body: CloseIn, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    c = svc.close_case(s, user, case_id, body.outcome, body.resolution)
    return {"status": c.status, "outcome": c.outcome}


@app.get(f"{API}/cases/{{case_id}}/reveal-account")
def reveal(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return {"bank_account": svc.reveal_account(s, user, svc.get_case(s, user.workspace_id, case_id))}


# ---------------------------------------------------------------- vendors, memory

@app.get(f"{API}/vendors")
def vendors(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    out = []
    for v in s.scalars(select(Vendor).where(Vendor.workspace_id == user.workspace_id).order_by(Vendor.name)):
        n = s.scalar(select(func.count()).select_from(HistoricalInvoice).where(HistoricalInvoice.vendor_id == v.id)) or 0
        cases = s.scalar(select(func.count()).select_from(Case).where(Case.vendor_id == v.id)) or 0
        out.append({"id": v.id, "name": v.name, "gstin": v.gstin, "address": v.address, "website": v.website, "invoices": n, "cases": cases})
    return {"items": out}


@app.get(f"{API}/vendors/{{vendor_id}}")
def vendor(vendor_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    v = s.get(Vendor, vendor_id)
    if not v or v.workspace_id != user.workspace_id:
        raise LookupError("vendor not found")
    q = lambda M: s.scalars(select(M).where(M.vendor_id == v.id))  # noqa: E731
    hist = list(s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.vendor_id == v.id).order_by(HistoricalInvoice.invoice_date)))
    return {
        "id": v.id, "name": v.name, "gstin": v.gstin, "address": v.address, "website": v.website,
        "accounts": [{"account": crypto.mask(a.last4), "ifsc": a.ifsc, "verified": a.verified, "verified_method": a.verified_method, "first_seen": a.first_seen, "last_seen": a.last_seen} for a in q(VendorBankAccount)],
        "domains": [{"domain": d.domain, "verified": d.verified} for d in q(VendorDomain)],
        "contacts": [{"name": c.name, "email": c.email, "verified": c.verified} for c in q(VendorContact)],
        "price_history": [{"date": h.invoice_date, "invoice_number": h.invoice_number, "total_minor": h.total_minor, "items": h.line_items} for h in hist],
        "prior_cases": [{"case_id": m.case_id, "outcome": m.outcome, "summary": m.summary, "peak_score": m.peak_score} for m in s.scalars(select(CaseMemory).where(CaseMemory.vendor_id == v.id))],
    }


class BankIn(BaseModel):
    account_number: str = Field(min_length=6, max_length=34)
    ifsc: str | None = None
    note: str = Field(min_length=5)


@app.post(f"{API}/vendors/{{vendor_id}}/bank-accounts", status_code=201)
def add_bank(vendor_id: str, body: BankIn, request: Request, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "approver")
    v = s.get(Vendor, vendor_id)
    if not v or v.workspace_id != user.workspace_id:
        raise LookupError("vendor not found")
    a = VendorBankAccount(workspace_id=user.workspace_id, vendor_id=v.id, last4=crypto.last4(body.account_number), acct_hmac=crypto.account_hmac(body.account_number),
                          acct_enc=crypto.encrypt(body.account_number), ifsc=body.ifsc, verified=True, verified_method="manual", verified_by=user.id)
    s.add(a)
    audit(s, user.workspace_id, user.id, "vendor.bank_verified", v.id, {"account": crypto.mask(a.last4), "note": body.note}, request.state.request_id)
    return {"account": crypto.mask(a.last4), "verified": True}


@app.get(f"{API}/memory/cases")
def memory(q: str = "", user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    rows = list(s.scalars(select(CaseMemory).where(CaseMemory.workspace_id == user.workspace_id).order_by(CaseMemory.created_at.desc())))
    if q:
        from rapidfuzz import fuzz

        rows = [r for r in rows if fuzz.partial_ratio(q.lower(), (r.summary + " " + " ".join(r.issues) + " " + (r.resolution or "")).lower()) >= 70]
    names = {v.id: v.name for v in s.scalars(select(Vendor).where(Vendor.workspace_id == user.workspace_id))}
    return {"items": [{"case_id": r.case_id, "vendor": names.get(r.vendor_id or ""), "outcome": r.outcome, "issues": r.issues, "resolution": r.resolution, "summary": r.summary, "peak_score": r.peak_score, "peak_tier": r.peak_tier, "at": iso(r.created_at)} for r in rows]}


# ---------------------------------------------------------------- dashboard, benchmark, demo

@app.get(f"{API}/dashboard/kpis")
def kpis(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    cases = list(s.scalars(select(Case).where(Case.workspace_id == user.workspace_id)))
    done = [c for c in cases if c.risk]
    auto = [c for c in cases if c.status == "AUTO_CLEARED" or (c.status == "CLOSED" and (c.recommendation.get("gate") or {}).get("auto_cleared"))]
    held = [c for c in cases if c.status in ("AWAITING_HUMAN", "AWAITING_VENDOR")]
    durations = sorted(c.duration_ms for c in cases if c.duration_ms)
    return {
        "processed": len(done),
        "auto_cleared_pct": round(100 * len(auto) / len(done)) if done else 0,
        "avg_investigation_seconds": round(sum(durations) / len(durations) / 1000, 1) if durations else None,
        "held_amount_minor": sum(c.amount_minor or 0 for c in held),
        "open_reviews": len(held),
    }


@app.get(f"{API}/benchmark/summary")
def benchmark_summary(user: User = Depends(current_user)) -> dict:
    p = REPO_ROOT / "benchmark" / "results.json"
    if not p.exists():
        return {"available": False, "hint": "run `make benchmark`"}
    return {"available": True, **json.loads(p.read_text(encoding="utf-8"))}


@app.post(f"{API}/demo/seed")
def demo_seed(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    if get_settings().env == "prod":
        raise HTTPException(404, "not available")
    svc.require_role(user, "accountant")
    from probity.demo import seed

    return seed.demo_files_info()


@app.get(f"{API}/demo/files/{{name}}")
def demo_file(name: str, user: User = Depends(current_user)) -> FileResponse:
    from probity.demo.seed import DEMO_DIR

    p = (DEMO_DIR / name).resolve()
    if p.parent != DEMO_DIR.resolve() or not p.exists():
        raise LookupError("demo file not found")
    return FileResponse(p, media_type="application/pdf", filename=p.name)


@app.post(f"{API}/demo/vendor-reply/{{case_id}}")
def demo_vendor_reply(case_id: str, kind: Literal["legit", "spoof"] = "legit", user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    """Simulated vendor inbox: deliver a scripted reply to the case's sent verification email."""
    if get_settings().env == "prod":
        raise HTTPException(404, "not available")
    from probity.demo.seed import scripted_reply

    case = svc.get_case(s, user.workspace_id, case_id)
    frm, subj, body = scripted_reply(s, case, kind)
    return svc.vendor_reply(s, user.workspace_id, user.id, case_id, frm, subj, body)


@app.put(f"{API}/demo/speed")
def demo_speed(delay_ms: int = Query(..., ge=0, le=5000), user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    ws = s.get(Workspace, user.workspace_id)
    assert ws
    ws.policy = {**(ws.policy or {}), "demo_agent_delay_ms": delay_ms}
    return {"delay_ms": delay_ms}


# ---------------------------------------------------------------- static web build (optional)

_WEB = REPO_ROOT / "apps" / "web" / "dist"
if _WEB.exists():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=_WEB, html=True), name="web")
