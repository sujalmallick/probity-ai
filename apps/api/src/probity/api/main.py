"""FastAPI application — API.md v1."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select  # noqa: F401
from sqlalchemy.orm import Session

from probity import services as svc
from probity.api.deps import bearer_token, current_user, db, require_mfa_for_approvals, upload_limit
from probity.auth import AuthError, InvitationPending, authenticate, verify_clerk_token
from probity.config import REPO_ROOT, ConfigError, get_settings
from probity.db.audit import audit, verify_chain
from probity.db.models import iso, AuditLog, Invitation, Case, CaseMemory, Document, User, Vendor, Workspace
from probity.db.session import session_scope
from probity.ingestion.parse import UnsupportedDocument
from probity.events import event_payload
from probity.logging import configure_logging, get_logger
from probity.observability import metrics_payload, observe_request, readiness
from probity.policy import get_policy
from probity.redis_client import async_redis, channel
from probity.risk.engine import WEIGHTS

configure_logging()
log = get_logger("api")


def docs_routes(env: str) -> dict:
    """The interactive API docs and schema are for development; production doesn't publish its API surface."""
    return {"docs_url": None, "redoc_url": None, "openapi_url": None} if env == "prod" else {}


settings = get_settings()
app = FastAPI(title="Probity API", version="1.0.0", description="Evidence before payment.", **docs_routes(settings.env))
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
from probity.api.limits import BodySizeLimit  # noqa: E402

app.add_middleware(BodySizeLimit)  # refuses oversized bodies before form parsing or authentication


@app.on_event("startup")
def _startup() -> None:
    """Print what is live and what is missing, then refuse to start if anything required is missing."""
    st = get_settings()
    print(st.checklist_text(), flush=True)
    st.validate_required()  # raises ConfigError → uvicorn reports "Application startup failed" and exits
    from probity.db.migrate import pending_migrations

    pending = pending_migrations()
    if pending:
        raise ConfigError(f"The database schema is not up to date ({pending}). Run: python -m probity.bootstrap")
    # Cases left "investigating" by a crash or restart end with a clear reason instead of staying stuck.
    svc.recover_after_restart()
    if st.task_backend == "inline":
        svc.start_inline_scheduler()


@app.middleware("http")
async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
    rid = "".join(ch for ch in (request.headers.get("X-Request-ID") or "")[:64] if ch.isalnum() or ch in "-_") or uuid.uuid4().hex
    request.state.request_id = rid
    from probity.request_context import request_id as _request_id_var

    _request_id_var.set(rid)  # audit rows and logs can read it without threading it through every call
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("http.unhandled", path=request.url.path, request_id=rid)
        raise
    elapsed = time.perf_counter() - t0
    route = getattr(request.scope.get("route"), "path", "unmatched")
    if request.url.path.startswith(API) and route not in (f"{API}/health", f"{API}/metrics"):
        observe_request(request.method, route, response.status_code, elapsed)
        log.info("http.request", method=request.method, route=route, status=response.status_code, ms=round(elapsed * 1000, 1),
                 request_id=rid, user_id=getattr(request.state, "user_id", None), workspace_id=getattr(request.state, "workspace_id", None))
    response.headers["X-Request-ID"] = rid
    # Declare UTF-8 explicitly: some clients (e.g. Windows PowerShell 5) fall back to Latin-1 for bare
    # application/json and turn "—" into "â€”".
    if response.headers.get("content-type") == "application/json":
        response.headers["content-type"] = "application/json; charset=utf-8"
    if get_settings().env == "prod":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith(API) and not request.url.path.endswith("/file"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        response.headers["X-Frame-Options"] = "DENY"
    return response


RETRYABLE = {408, 425, 429, 500, 502, 503, 504}


def _err(status: int, code: str, message: str, request: Request, retryable: bool | None = None) -> JSONResponse:
    """The one error shape: {error: {code, message (safe to show), retryable, ref}}. `ref` is the request id,
    also in the X-Request-ID header and the server logs."""
    return JSONResponse({"error": {"code": code, "message": message,
                                   "retryable": (status in RETRYABLE) if retryable is None else retryable,
                                   "ref": getattr(request.state, "request_id", None)}}, status_code=status)


class LimitReached(Exception):
    """A usage limit stops the request; the message names the limit and its setting."""


@app.exception_handler(LimitReached)
async def _limit(request: Request, e: LimitReached):  # type: ignore[no-untyped-def]
    return _err(429, "limit_reached", str(e), request, retryable=False)


@app.exception_handler(RequestValidationError)
async def _invalid(request: Request, e: RequestValidationError):  # type: ignore[no-untyped-def]
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        parts.append(f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid")))
    return _err(422, "validation_error", "; ".join(parts) or "invalid request", request)


@app.exception_handler(Exception)
async def _unexpected(request: Request, e: Exception):  # type: ignore[no-untyped-def]
    # Never echo internals: the reference ties the user's report to the logged traceback.
    return _err(500, "internal_error", f"Something went wrong on our side. Reference: {getattr(request.state, 'request_id', '-')}", request)


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


@app.exception_handler(InvitationPending)
async def _invitation_pending(request: Request, e: InvitationPending):  # type: ignore[no-untyped-def]
    """First sign-in with pending invitations: the usual error shape plus the invitations to choose from."""
    return JSONResponse({"error": {"code": "invitation_pending", "message": str(e), "retryable": False,
                                   "ref": getattr(request.state, "request_id", None), "invitations": e.invitations}}, status_code=409)


API = "/api/v1"

from probity.api import risk as _risk_api  # noqa: E402
from probity.api import vendors as _vendors_api  # noqa: E402
from probity.api import workspace as _workspace_api  # noqa: E402

app.include_router(_risk_api.router)
app.include_router(_vendors_api.router)
app.include_router(_workspace_api.router)

from probity.api import records as _records_api  # noqa: E402

app.include_router(_records_api.router)

# CSP for the web app (served from this origin). Clerk's frontend API, images and bot-protection need allowances.
_CLERK = " ".join(filter(None, [get_settings().clerk_frontend_api or "https://*.clerk.accounts.dev", "https://*.clerk.com"]))
WEB_CSP = (
    "default-src 'self'; "
    f"script-src 'self' {_CLERK} https://challenges.cloudflare.com; "
    f"connect-src 'self' {_CLERK} https://clerk-telemetry.com; "
    f"img-src 'self' data: blob: https://img.clerk.com; "
    "style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    "frame-src 'self' blob: https://challenges.cloudflare.com; worker-src 'self' blob:; "
    "object-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


# ---------------------------------------------------------------- ops

@app.get(f"{API}/health")
def health() -> dict:
    return {"status": "ok"}


@app.get(f"{API}/ready")
def ready() -> JSONResponse:
    ok, checks = readiness()
    return JSONResponse({"ok": ok, **checks}, status_code=200 if ok else 503)


@app.get(f"{API}/ai/status")
def ai_status(user: User = Depends(current_user)) -> dict:
    """Is the AI working? `problem` is set while an account-level failure is active (credits or quota used up, key
    rejected, not configured): the web app shows it as a banner. It clears on the next successful AI call."""
    from probity import ai_health

    st = get_settings()
    problem = ai_health.current()
    if problem is None and not st.llm_api_key:
        problem = {"code": "not_configured", "reason": f"AI is not configured ({st.llm_key_name} missing). Rules are used instead.", "since": None}
    return {"ok": problem is None, "provider": st.llm_provider, "problem": problem}


@app.get(f"{API}/metrics")
def metrics(authorization: str | None = Header(default=None)) -> Response:
    import hmac as _hmac

    token = get_settings().metrics_token
    if token and not _hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
        raise HTTPException(401, "metrics token required")
    body, ctype = metrics_payload()
    return Response(body, media_type=ctype)


# ---------------------------------------------------------------- auth (Clerk only; sign-in config is in /app/config)


@app.get(f"{API}/me")
def me(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    ws = s.get(Workspace, user.workspace_id)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "workspace": {"id": ws.id, "name": ws.name} if ws else None}


class JoinIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invitation_id: str | None = Field(default=None, max_length=40)
    own_workspace: bool = False


@app.post(f"{API}/me/join")
def join(body: JoinIn, request: Request, s: Session = Depends(db), authorization: str | None = Header(default=None)) -> dict:
    """First sign-in with pending invitations (409 invitation_pending): accept one of them, or start your own
    workspace. Only the invitee can accept, and only invitations addressed to their verified email."""
    if bool(body.invitation_id) == body.own_workspace:
        raise svc.BadRequest("send exactly one of invitation_id or own_workspace: true")
    token = bearer_token(authorization)
    try:
        if s.scalars(select(User).where(User.external_id == verify_clerk_token(token)["sub"])).first():
            raise svc.Conflict("you already belong to a workspace")
        p = authenticate(token, s, join=body.invitation_id or "own")
    except AuthError as e:
        raise HTTPException(401, str(e)) from e
    return me(p.user, s)


@app.get(f"{API}/workspace/policy")
def get_ws_policy(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    p = get_policy(s.get(Workspace, user.workspace_id))
    return {**p, "weights": {**WEIGHTS[p["weights_version"]], **p["weight_overrides"]}, "tiers": {"LOW": "0–29", "MEDIUM": "30–59", "HIGH": "60–79", "CRITICAL": "80–100"}}


MAX_AMOUNT_MINOR = 10**14  # ₹1,000 crore: far above any invoice; bounds typos and overflow


class PolicyIn(BaseModel):
    """Owner-editable policy. Strict types and bounds: a string where a number belongs used to be stored as-is and
    made every later investigation fail; negative weights silently lowered scores."""

    model_config = ConfigDict(extra="forbid", strict=True)
    auto_clear_enabled: bool | None = None
    auto_clear_max_amount_minor: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    external_research_amount_minor: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    dual_approval_amount_minor: int | None = Field(default=None, ge=0, le=MAX_AMOUNT_MINOR)
    weight_overrides: dict[str, int] | None = None
    require_mfa_for_approvals: bool | None = None

    @field_validator("weight_overrides")
    @classmethod
    def _known_signals(cls, v: dict[str, int] | None) -> dict[str, int] | None:
        if v is None:
            return v
        known = set().union(*WEIGHTS.values())
        unknown = sorted(set(v) - known)
        if unknown:
            raise ValueError(f"unknown signals: {unknown}")
        if any(not 0 <= w <= 100 for w in v.values()):
            raise ValueError("signal weights must be between 0 and 100")
        return v


@app.put(f"{API}/workspace/policy")
def put_ws_policy(body: PolicyIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    ws = s.get(Workspace, user.workspace_id)
    assert ws
    changes = body.model_dump(exclude_unset=True)
    before = dict(ws.policy or {})
    ws.policy = {**before, **changes, "reviewed_at": datetime.now(timezone.utc).isoformat()}
    audit(s, ws.id, user.id, "policy.updated", ws.id, {"before": before, "after": ws.policy}, request.state.request_id)
    return get_policy(ws)


# ---------------------------------------------------------------- team: members, invitations, roles

ROLE_NAMES = Literal["viewer", "accountant", "approver", "owner"]


@app.get(f"{API}/workspace/members")
def members(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    rows = s.scalars(select(User).where(User.workspace_id == user.workspace_id).order_by(User.created_at))
    return {"items": [{"id": u.id, "name": u.name, "email": u.email, "role": u.role, "active": u.active, "linked": bool(u.external_id)} for u in rows]}


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: ROLE_NAMES


@app.get(f"{API}/workspace/invitations")
def invitations(user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    rows = s.scalars(select(Invitation).where(Invitation.workspace_id == user.workspace_id, Invitation.accepted_at.is_(None)).order_by(Invitation.created_at.desc()))
    from probity.auth import INVITATION_TTL, invitation_expired

    return {"items": [{"id": i.id, "email": i.email, "role": i.role, "created_at": iso(i.created_at),
                       "expires_at": iso(i.created_at + INVITATION_TTL), "expired": invitation_expired(i)} for i in rows]}


@app.post(f"{API}/workspace/invitations", status_code=201)
def invite(body: InviteIn, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    email = body.email.strip().lower()
    # Only this workspace's members: an answer about other workspaces would tell any owner who uses Probity.
    if s.scalars(select(User).where(User.email == email, User.workspace_id == user.workspace_id)).first():
        raise svc.Conflict("this person is already a member of your workspace")
    inv = Invitation(workspace_id=user.workspace_id, email=email, role=body.role, invited_by=user.id)
    s.add(inv)
    s.flush()
    audit(s, user.workspace_id, user.id, "invitation.created", inv.id, {"email": email, "role": body.role}, request.state.request_id)
    from probity import mailer

    not_sent = mailer.send_invitation(email, user.name, body.role)
    if not_sent:
        audit(s, user.workspace_id, user.id, "email.not_sent", inv.id, {"to": mailer.masked(email), "reason": not_sent[:200]}, request.state.request_id)
    return {"id": inv.id, "email": email, "role": body.role, "sign_in_url": get_settings().public_app_url,
            "email_sent": not_sent is None, "email_note": not_sent}


@app.delete(f"{API}/workspace/invitations/{{inv_id}}", status_code=204)
def revoke_invite(inv_id: str, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> None:
    svc.require_role(user, "owner")
    inv = s.get(Invitation, inv_id)
    if not inv or inv.workspace_id != user.workspace_id or inv.accepted_at:
        raise LookupError("invitation not found")
    s.delete(inv)
    audit(s, user.workspace_id, user.id, "invitation.revoked", inv_id, {"email": inv.email}, request.state.request_id)


class MemberPatch(BaseModel):
    role: ROLE_NAMES | None = None
    active: bool | None = None


@app.patch(f"{API}/workspace/members/{{member_id}}")
def update_member(member_id: str, body: MemberPatch, request: Request, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "owner")
    m = s.get(User, member_id)
    if not m or m.workspace_id != user.workspace_id:
        raise LookupError("member not found")
    owners = [u for u in s.scalars(select(User).where(User.workspace_id == user.workspace_id, User.role == "owner", User.active.is_(True)))]
    losing_owner = m.role == "owner" and m.active and ((body.role and body.role != "owner") or body.active is False)
    if losing_owner and len(owners) <= 1:
        raise svc.Conflict("a workspace needs at least one active owner")
    before = {"role": m.role, "active": m.active}
    if body.role:
        m.role = body.role
    if body.active is not None:
        m.active = body.active
    audit(s, user.workspace_id, user.id, "member.updated", m.id, {"before": before, "after": {"role": m.role, "active": m.active}}, request.state.request_id)
    return {"id": m.id, "role": m.role, "active": m.active}


# ---------------------------------------------------------------- documents & cases

@app.post(f"{API}/documents", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), user: User = Depends(upload_limit), s: Session = Depends(db)) -> dict:
    from probity.ingestion.parse import extract_text, max_upload_bytes, sniff_mime

    svc.require_role(user, "accountant")  # before any parsing: viewers can't make the server read documents
    cap = max_upload_bytes()
    data = await file.read(cap + 1)
    if len(data) > cap:
        raise svc.BadRequest(f"File exceeds {cap // (1024 * 1024)} MB (MAX_UPLOAD_MB)")
    # Reject what can't be read before anything is stored: images and scans (no text layer) raise
    # UnsupportedDocument → 415 with a clear message. Probity has no OCR.
    await asyncio.to_thread(extract_text, data, sniff_mime(data, file.filename or "upload"))
    # Cleaning, scanning and storing take seconds: off the event loop, so other requests and live streams keep moving.
    doc, duplicate_of = await asyncio.to_thread(svc.upload_document, s, user, file.filename or "upload", data)
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
    from probity import storage

    return Response(storage.get(d.storage_path), media_type=d.mime, headers={
        "Content-Disposition": content_disposition("inline", d.filename), "Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"})


def content_disposition(kind: str, filename: str) -> str:
    """Header-safe for any uploaded name: an ASCII fallback (no quotes, semicolons or control characters) plus the
    exact name in RFC 5987 form. Headers are latin-1, so a raw Devanagari or curly-quote name would fail the request."""
    from urllib.parse import quote

    fallback = "".join(ch if 32 <= ord(ch) < 127 and ch not in '"\\;' else "_" for ch in filename).strip() or "document"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


@app.post(f"{API}/documents/{{doc_id}}/preview")
async def preview_document(doc_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    """Extract fields before launching an investigation so low-confidence values can be corrected."""
    from probity import storage
    from probity.agents.document import PreviewCtx, extract_document
    from probity.ingestion.parse import low_confidence
    from probity.tools.base import Budget

    svc.require_role(user, "accountant")
    d = s.get(Document, doc_id)
    if not d or d.workspace_id != user.workspace_id:
        raise LookupError("document not found")
    data, mime, ws = storage.get(d.storage_path), d.mime, user.workspace_id
    fields, validation, _text, injection = await asyncio.to_thread(extract_document, PreviewCtx(ws, f"preview:{doc_id}", Budget.from_settings()), data, mime, {})
    public = {k: {kk: vv for kk, vv in v.items() if kk not in ("hmac", "enc")} for k, v in fields.items()}
    return {"fields": public, "validation": validation, "low_confidence": low_confidence(fields), "injection_detected": bool(injection)}


class CreateCase(BaseModel):
    document_id: str
    corrections: dict[str, Any] | None = None


@app.post(f"{API}/cases", status_code=201)
def create_case(body: CreateCase, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    _check_daily_cases(s, user)
    c = svc.create_case(s, user, body.document_id, body.corrections)
    return {"case_id": c.id, "number": c.number, "status": c.status}


def _check_daily_cases(s: Session, user: User) -> None:
    limit = get_settings().workspace_daily_case_limit
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    started_today = s.scalar(select(func.count()).select_from(Case).where(Case.workspace_id == user.workspace_id, Case.created_at >= start)) or 0
    if started_today >= limit:
        raise LimitReached(f"Limit reached: {limit:,} investigations per workspace per day (WORKSPACE_DAILY_CASE_LIMIT). "
                           "It resets at 00:00 UTC.")


@app.post(f"{API}/cases/{{case_id}}/retry", status_code=201)
def retry_case(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    """A FAILED case is never revived: a new case runs the same document (and corrections) from the start, and the two
    are linked both ways. Counts against the daily investigation limit."""
    _check_daily_cases(s, user)
    c = svc.retry_case(s, user, case_id)
    return {"case_id": c.id, "number": c.number, "status": c.status, "retry_of": case_id}


@app.get(f"{API}/cases")
def list_cases(
    tier: str | None = None, status: str | None = None, vendor_id: str | None = None, q: str = "",
    limit: int = Query(50, ge=1, le=200), cursor: str | None = None,
    user: User = Depends(current_user), s: Session = Depends(db),
) -> dict:
    """Newest first. `status`/`tier` accept comma-separated values; `q` searches invoice number, vendor name
    and file name; pass `next_cursor` back as `cursor` for the next page."""
    import base64

    stmt = select(Case).where(Case.workspace_id == user.workspace_id)
    if status:
        stmt = stmt.where(Case.status.in_([x.strip().upper() for x in status.split(",") if x.strip()]))
    if tier:
        stmt = stmt.where(Case.risk["tier"].as_string().in_([x.strip().upper() for x in tier.split(",") if x.strip()]))
    if vendor_id:
        stmt = stmt.where(Case.vendor_id == vendor_id)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.outerjoin(Vendor, Vendor.id == Case.vendor_id).outerjoin(Document, Document.id == Case.document_id).where(or_(
            Case.extraction["invoice_number"]["value"].as_string().ilike(like),
            Case.extraction["vendor_name"]["value"].as_string().ilike(like),
            Vendor.name.ilike(like), Document.filename.ilike(like)))
    if cursor:
        try:
            ts, cid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            at = datetime.fromisoformat(ts)
        except Exception as e:  # noqa: BLE001
            raise svc.BadRequest("invalid cursor") from e
        stmt = stmt.where(or_(Case.created_at < at, (Case.created_at == at) & (Case.id < cid)))
    page = list(s.scalars(stmt.order_by(Case.created_at.desc(), Case.id.desc()).limit(limit + 1)))
    more = len(page) > limit
    page = page[:limit]
    nxt = base64.urlsafe_b64encode(f"{page[-1].created_at.isoformat()}|{page[-1].id}".encode()).decode() if more and page else None
    return {"items": [svc.serialize_case(s, c, user, full=False) for c in page], "next_cursor": nxt}


@app.get(f"{API}/cases/{{case_id}}")
def get_case(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return svc.serialize_case(s, svc.get_case(s, user.workspace_id, case_id), user)


@app.get(f"{API}/cases/{{case_id}}/evidence")
def get_evidence(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    return {"items": svc.case_evidence(s, svc.get_case(s, user.workspace_id, case_id))}


@app.get(f"{API}/cases/{{case_id}}/trace")
def case_trace(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    """Per-agent trace: timing, checks, could-not-verify, rule-based fallbacks, AI calls and claims, plus sanity results."""
    from probity import trace

    return trace.build(s, svc.get_case(s, user.workspace_id, case_id))


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
def export_case(case_id: str, request: Request, format: Literal["json", "pdf"] = "json", user: User = Depends(current_user), s: Session = Depends(db)) -> Response:
    case = svc.get_case(s, user.workspace_id, case_id)
    audit(s, user.workspace_id, user.id, "case.exported", case.id, {"format": format}, request.state.request_id)
    body = {"case": svc.serialize_case(s, case, user), "evidence": svc.case_evidence(s, case), "audit": case_audit(case_id, user, s)}
    if format == "pdf":
        from probity.report import build_case_pdf

        pdf = build_case_pdf(body["case"], body["evidence"], body["audit"])
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="probity-case-{case.number}.pdf"'})
    return JSONResponse(json.loads(json.dumps(body, default=str)), headers={"Content-Disposition": f'attachment; filename="probity-case-{case.number}.json"'})


@app.get(f"{API}/cases/{{case_id}}/events")
async def case_events(case_id: str, request: Request, user: User = Depends(current_user), req_session: Session = Depends(db)) -> StreamingResponse:
    """SSE agent stream. Backfills from the database after Last-Event-ID, then streams live events from
    Redis pub/sub (or polls the database when Redis is not configured). Keepalive every 15 s."""
    ws = user.workspace_id
    # Release the request's DB session now: a stream can stay open for minutes, and an open transaction
    # would sit "idle in transaction" holding locks and blocking vacuum/migrations.
    req_session.commit()
    req_session.close()
    with session_scope(ws) as s:
        svc.get_case(s, ws, case_id)
    last = int(request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id") or 0)

    def frame(p: dict) -> str:
        return f"id: {p['seq']}\nevent: message\ndata: {json.dumps(p, default=str)}\n\n"

    async def backfill():  # type: ignore[no-untyped-def]
        nonlocal last
        rows = await asyncio.to_thread(svc.events_after, ws, case_id, last)
        out = []
        for r in rows:
            last = r.id
            out.append(frame(event_payload(r)))
        return out

    async def gen():  # type: ignore[no-untyped-def]
        nonlocal last
        r = async_redis()
        pubsub = None
        if r is not None:
            pubsub = r.pubsub()
            await pubsub.subscribe(channel(ws, case_id))  # subscribe before backfill so nothing is missed
        try:
            for f in await backfill():
                yield f
            idle = 0.0
            while not await request.is_disconnected():
                if pubsub is not None:
                    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg and msg.get("type") == "message":
                        p = json.loads(msg["data"])
                        if p["seq"] > last:
                            last = p["seq"]
                            yield frame(p)
                        idle = 0.0
                        continue
                    idle += 1.0
                else:
                    frames = await backfill()
                    for f in frames:
                        yield f
                    idle = 0.0 if frames else idle + 0.5
                    await asyncio.sleep(0.5)
                if idle >= 15:
                    idle = 0.0
                    yield ": keepalive\n\n"
        finally:
            if pubsub is not None:
                await pubsub.unsubscribe()
                await pubsub.aclose()
                await r.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- decisions & actions

class DecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT", "REQUEST_VERIFICATION", "INVESTIGATE_FURTHER"]
    reason: str = Field(default="", max_length=4000)


@app.post(f"{API}/cases/{{case_id}}/decision")
def decision(case_id: str, body: DecisionIn, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    c = svc.decide(s, user, case_id, body.decision, body.reason)
    return {"status": c.status}


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
    override_reason: str | None = Field(default=None, max_length=500)  # required when the recipient isn't a verified contact


@app.post(f"{API}/cases/{{case_id}}/drafts/{{draft_id}}/send")
def send(case_id: str, draft_id: str, body: SendIn | None = None, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    body = body or SendIn()
    d = svc.send_draft(s, user, case_id, draft_id, body.override_unverified_recipient, body.override_reason)
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
    note: str = Field(max_length=2000)
    # Required attestation that the channel was already on file (not taken from the invoice or the reply); must be true.
    known_channel: bool | None = None
    # Required when confirming a bank statement: the last 4 digits the vendor confirmed (must be the invoice's account).
    confirmed_account_last4: str | None = Field(default=None, pattern=r"^[0-9]{4}$")


@app.post(f"{API}/cases/{{case_id}}/out-of-band-confirmation")
def oob(case_id: str, body: OOBIn, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    return svc.confirm_out_of_band(s, user, case_id, body.claim_ids, body.method, body.note, known_channel=body.known_channel,
                                   confirmed_account_last4=body.confirmed_account_last4)


@app.post(f"{API}/cases/{{case_id}}/rescore")
def rescore(case_id: str, user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    svc.require_role(user, "accountant")
    svc.get_case(s, user.workspace_id, case_id)
    s.commit()
    return svc.rescore(user.workspace_id, case_id)


class CloseIn(BaseModel):
    outcome: Literal["CONFIRMED_ISSUE", "CLEARED", "INCONCLUSIVE"]
    resolution: str = Field(default="", max_length=4000)


@app.post(f"{API}/cases/{{case_id}}/close")
def close(case_id: str, body: CloseIn, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    c = svc.close_case(s, user, case_id, body.outcome, body.resolution)
    return {"status": c.status, "outcome": c.outcome}


@app.get(f"{API}/cases/{{case_id}}/reveal-account")
def reveal(case_id: str, user: User = Depends(require_mfa_for_approvals), s: Session = Depends(db)) -> dict:
    return {"bank_account": svc.reveal_account(s, user, svc.get_case(s, user.workspace_id, case_id))}


# ---------------------------------------------------------------- inbound email webhook

@app.post(f"{API}/webhooks/inbound-email")
async def inbound_email(request: Request) -> dict:
    """Vendor replies. Your email provider (Resend inbound, SES → Lambda, Cloudflare Email Worker, Mailgun
    route) POSTs JSON {from, to, subject, text, dkim?}. Signed: X-Probity-Timestamp + X-Probity-Signature =
    hex HMAC-SHA256(INBOUND_EMAIL_SECRET, f"{timestamp}.{raw body}"). Timestamps may be up to 5 minutes old and 30 s in
    the future; an identical body is accepted once per 10 minutes (replay protection)."""
    import hashlib
    import hmac as _hmac
    import re as _re
    import time as _time

    st = get_settings()
    if not st.inbound_email_secret:
        raise HTTPException(404, "inbound email not configured")
    raw = await request.body()
    ts = request.headers.get("X-Probity-Timestamp", "")
    sig = request.headers.get("X-Probity-Signature", "")
    expected = _hmac.new(st.inbound_email_secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    now = _time.time()
    if not ts.isdigit() or not (now - 300 <= int(ts) <= now + 30) or not _hmac.compare_digest(expected, sig):
        raise HTTPException(401, "invalid signature")
    if not _first_delivery(hashlib.sha256(raw).hexdigest()):
        raise HTTPException(409, "duplicate delivery")
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise HTTPException(400, "body must be JSON") from e
    if not isinstance(msg, dict):
        raise HTTPException(400, "body must be a JSON object")
    rcpts = msg.get("to") if isinstance(msg.get("to"), list) else [msg.get("to", "")]
    case_id = next((m.group(1) for r in rcpts if (m := _re.search(r"case\+(case_[a-z0-9]+)@", str(r), _re.I))), None)
    if not case_id:
        raise HTTPException(422, "no case address in recipients")

    from probity.ingestion.parse import address_from_header

    sender = address_from_header(str(msg.get("from", "")))  # the real addr-spec, never a display name
    if not sender:
        raise HTTPException(422, "no valid sender address")

    dkim = str(msg.get("dkim") or "").lower()
    transport_indicators = []
    if not dkim:
        transport_indicators.append("Unauthenticated sender: no DKIM result from the email provider")
    elif dkim != "pass":
        transport_indicators.append("Sender failed DKIM verification")

    def _deliver() -> dict:
        from sqlalchemy import text as _text

        with session_scope() as s:
            ws = s.execute(_text("SELECT probity_case_workspace(:c)"), {"c": case_id}).scalar()
        if not ws:
            raise LookupError("case not found")
        with session_scope(ws) as s:
            body = str(msg.get("text") or "")[:20000]
            return svc.vendor_reply(s, ws, "inbound-email", case_id, sender, str(msg.get("subject", ""))[:300], body,
                                    extra_indicators=transport_indicators)

    return await asyncio.to_thread(_deliver)


_SEEN_DELIVERIES: dict[str, float] = {}


def _first_delivery(digest: str, ttl: int = 600) -> bool:
    """True the first time a webhook body is seen within `ttl` seconds (Redis when configured, else this process)."""
    import time as _time

    from probity.redis_client import sync_redis

    r = sync_redis()
    if r is not None:
        try:
            return bool(r.set(f"probity:inbound:{digest}", "1", nx=True, ex=ttl))
        except Exception:  # noqa: BLE001 - fall back to the in-process cache
            pass
    now = _time.time()
    for k in [k for k, exp in _SEEN_DELIVERIES.items() if exp < now]:
        _SEEN_DELIVERIES.pop(k, None)
    if digest in _SEEN_DELIVERIES:
        return False
    _SEEN_DELIVERIES[digest] = now + ttl
    return True


# ---------------------------------------------------------------- vendors, memory

@app.get(f"{API}/memory/cases")
def memory(q: str = "", user: User = Depends(current_user), s: Session = Depends(db)) -> dict:
    rows = list(s.scalars(select(CaseMemory).where(CaseMemory.workspace_id == user.workspace_id).order_by(CaseMemory.created_at.desc())))
    if q:
        from rapidfuzz import fuzz

        rows = [r for r in rows if fuzz.partial_ratio(q.lower(), (r.summary + " " + " ".join(r.issues) + " " + (r.resolution or "")).lower()) >= 70]
    names = {v.id: v.name for v in s.scalars(select(Vendor).where(Vendor.workspace_id == user.workspace_id))}
    return {"items": [{"case_id": r.case_id, "vendor": names.get(r.vendor_id or ""), "outcome": r.outcome, "issues": r.issues, "resolution": r.resolution, "summary": r.summary, "peak_score": r.peak_score, "peak_tier": r.peak_tier, "at": iso(r.created_at)} for r in rows]}


# ---------------------------------------------------------------- dashboard

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


# ---------------------------------------------------------------- static web build (optional)

import os as _os

_WEB = Path(_os.environ.get("WEB_DIST_DIR") or (REPO_ROOT / "apps" / "web" / "dist"))
if _WEB.exists():
    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class SPAStaticFiles(StaticFiles):
        """Serve the built web app; unknown non-API paths fall back to index.html (client-side routing)."""

        async def get_response(self, path, scope):  # type: ignore[no-untyped-def, override]
            try:
                resp = await super().get_response(path, scope)
            except StarletteHTTPException as e:
                # Unknown API paths stay 404 (never the web app's index.html). Use the request path: `path` is
                # OS-normalised and has backslashes on Windows.
                if e.status_code != 404 or scope.get("path", "").startswith("/api/"):
                    raise
                resp = await super().get_response("index.html", scope)
            if path.startswith("assets/"):
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif resp.media_type == "text/html":
                resp.headers["Cache-Control"] = "no-cache"
                resp.headers["Content-Security-Policy"] = WEB_CSP
            return resp

    app.mount("/", SPAStaticFiles(directory=_WEB, html=True), name="web")
