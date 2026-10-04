"""Relational model (Architecture.md §8). Every row carries workspace_id; repositories always filter by it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    """ISO-8601 UTC. SQLite drops tzinfo, so naive values are treated as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


class TelemetryBase(DeclarativeBase):
    """Agent events + LLM call logs. Separate engine on SQLite so live progress never waits on a case write lock."""

    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ws"))
    name: Mapped[str] = mapped_column(String(200))
    policy: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("usr"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))  # viewer | accountant | approver | owner
    external_id: Mapped[str | None] = mapped_column(String(80), unique=True)  # Clerk user id
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invitation(Base):
    """Owner invites a teammate by email with a role; consumed on their first sign-in."""

    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("inv"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    email: Mapped[str] = mapped_column(String(200), index=True)
    role: Mapped[str] = mapped_column(String(20))
    invited_by: Mapped[str] = mapped_column(String(40))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ven"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    gstin: Mapped[str | None] = mapped_column(String(15))
    pan: Mapped[str | None] = mapped_column(String(10))
    address: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(300))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VendorBankAccount(Base):
    __tablename__ = "vendor_bank_accounts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("vba"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    last4: Mapped[str] = mapped_column(String(4))
    acct_hmac: Mapped[str] = mapped_column(String(64), index=True)
    acct_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    ifsc: Mapped[str | None] = mapped_column(String(11))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_method: Mapped[str | None] = mapped_column(String(40))
    verified_by: Mapped[str | None] = mapped_column(String(40))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_note: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[date | None] = mapped_column(Date)
    last_seen: Mapped[date | None] = mapped_column(Date)


class VendorDomain(Base):
    __tablename__ = "vendor_domains"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("vdm"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    domain: Mapped[str] = mapped_column(String(253))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_method: Mapped[str | None] = mapped_column(String(40))
    verified_by: Mapped[str | None] = mapped_column(String(40))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_note: Mapped[str | None] = mapped_column(Text)


class VendorContact(Base):
    __tablename__ = "vendor_contacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("vct"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_method: Mapped[str | None] = mapped_column(String(40))
    verified_by: Mapped[str | None] = mapped_column(String(40))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_note: Mapped[str | None] = mapped_column(Text)


class HistoricalInvoice(Base):
    """Paid/accepted invoices — the internal baseline the Transaction Analyst compares against."""

    __tablename__ = "historical_invoices"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("hin"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(80))
    invoice_number_norm: Mapped[str] = mapped_column(String(80), index=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    total_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    bank_last4: Mapped[str | None] = mapped_column(String(4))
    bank_hmac: Mapped[str | None] = mapped_column(String(64))
    po_number: Mapped[str | None] = mapped_column(String(80))
    line_items: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    case_id: Mapped[str | None] = mapped_column(String(40))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("po"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    vendor_id: Mapped[str] = mapped_column(ForeignKey("vendors.id"), index=True)
    po_number: Mapped[str] = mapped_column(String(80), index=True)
    po_date: Mapped[date] = mapped_column(Date)
    lines: Mapped[list[Any]] = mapped_column(JSONType, default=list)  # [{description, qty, unit_price_minor}]


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("workspace_id", "sha256"),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("doc"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(80))
    size: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("case"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    number: Mapped[int] = mapped_column(Integer, default=0)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    vendor_id: Mapped[str | None] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="QUEUED", index=True)
    extraction: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    corrections: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    plan: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    checks: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # check -> {status, reason}
    risk: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    summary: Mapped[str | None] = mapped_column(Text)
    memory_hits: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    amount_minor: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str | None] = mapped_column(String(30))
    resolution: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    sources_checked: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EvidenceRow(Base):
    """Immutable, content-hashed. Verification flips claim status; it never edits evidence."""

    __tablename__ = "evidence"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ev"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    source: Mapped[str] = mapped_column(String(30))
    field: Mapped[str | None] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(JSONType, nullable=True)
    source_ref: Mapped[str] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[int] = mapped_column(Integer, default=1)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(40))


class ClaimRow(Base):
    __tablename__ = "claims"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("clm"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    statement: Mapped[str] = mapped_column(Text)
    signal: Mapped[str | None] = mapped_column(String(60))
    assertion: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    evidence_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="unverified")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    severity: Mapped[str] = mapped_column(String(10), default="info")
    verifier_notes: Mapped[str | None] = mapped_column(Text)
    supporting_quote: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RiskScoreRow(Base):
    __tablename__ = "risk_scores"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("rs"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    score: Mapped[int] = mapped_column(Integer)
    tier: Mapped[str] = mapped_column(String(10))
    weights_version: Mapped[str] = mapped_column(String(10))
    signals: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    contributions: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    reason: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dec"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("dft"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    to_email: Mapped[str] = mapped_column(String(200))
    recipient_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    requested_items: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | sent
    approved_by: Mapped[str | None] = mapped_column(String(40))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """Mock mailbox (outbound sent mail + inbound vendor replies)."""

    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("msg"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # out | in
    from_email: Mapped[str] = mapped_column(String(200))
    to_email: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    indicators: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaseMemory(Base):
    __tablename__ = "case_memory"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mem"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    vendor_id: Mapped[str | None] = mapped_column(String(40), index=True)
    case_id: Mapped[str] = mapped_column(String(40))
    issues: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    outcome: Mapped[str] = mapped_column(String(30))
    resolution: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    peak_score: Mapped[int] = mapped_column(Integer, default=0)
    peak_tier: Mapped[str] = mapped_column(String(10), default="LOW")
    evidence_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    bank_hmacs: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    domains: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GraphEdge(Base):
    """Relationship graph (Feature F12): entity → attribute edges, e.g. vendor —has_bank→ hmac."""

    __tablename__ = "graph_edges"
    __table_args__ = (UniqueConstraint("workspace_id", "src_type", "src_id", "rel", "dst_type", "dst_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    src_type: Mapped[str] = mapped_column(String(20))
    src_id: Mapped[str] = mapped_column(String(80), index=True)
    rel: Mapped[str] = mapped_column(String(30))
    dst_type: Mapped[str] = mapped_column(String(20))
    dst_id: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str | None] = mapped_column(String(200))
    case_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CaseNote(Base):
    __tablename__ = "case_notes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("note"))
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ImportJob(Base):
    """CSV imports of vendor master, invoice history and purchase orders (validated, then committed)."""

    __tablename__ = "imports"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("imp"))
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # vendors | invoices | purchase_orders
    filename: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))  # committed | rejected
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_ok: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    actor_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    """In-app notification for one user (also emailed when an email backend is live)."""

    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("ntf"))
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(40))  # case_held | vendor_replied | approval_needed | followup_due | case_failed
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    case_id: Mapped[str | None] = mapped_column(String(40))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AgentEvent(TelemetryBase):
    """Persisted SSE stream; supports Last-Event-ID resume."""

    __tablename__ = "agent_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str] = mapped_column(String(40), index=True)
    type: Mapped[str] = mapped_column(String(40))
    agent: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str | None] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """Append-only, hash-chained. UPDATE/DELETE are blocked at the ORM layer (and by grants in Postgres)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(60))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LLMCall(TelemetryBase):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str | None] = mapped_column(String(40), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    mode: Mapped[str] = mapped_column(String(10))
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(AuditLog, "before_update")
def _no_audit_update(*_: Any) -> None:
    raise PermissionError("audit_log is append-only")


@event.listens_for(AuditLog, "before_delete")
def _no_audit_delete(*_: Any) -> None:
    raise PermissionError("audit_log is append-only")


@event.listens_for(EvidenceRow, "before_update")
def _no_evidence_update(*_: Any) -> None:
    raise PermissionError("evidence is immutable")
