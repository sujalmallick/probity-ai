"""Append-only, hash-chained audit log (Security.md §9, Guardrails G13).

Each row's hash is an HMAC over every column and the previous row's hash, keyed by a sub-key of HMAC_KEY, so
someone with database write access but not the key can neither edit, insert, reorder nor recompute the chain
without verify_chain noticing. Deleting the newest rows leaves a valid shorter chain, so every new head (id, hash)
is also written to the structured log, an out-of-database witness: pass the last logged head as `anchor` to
verify_chain to detect truncation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from probity import request_context
from probity.db.models import AuditLog

GENESIS = "0" * 64
_lock = threading.Lock()  # SQLite (single writer); Postgres uses a per-workspace advisory lock held until commit


@lru_cache
def _chain_key() -> bytes:
    from probity.config import get_settings

    secret = get_settings().hmac_key
    if not secret:
        raise RuntimeError("HMAC_KEY is required: the audit log is keyed with it")
    return hmac.new(secret.encode(), b"probity/audit-chain/v2", hashlib.sha256).digest()


def _ts(ts: datetime | None) -> str:
    """Stable text for a timestamp across databases (aware → UTC, naive treated as UTC)."""
    if ts is None:
        return ""
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return ts.isoformat(timespec="microseconds")


def _digest(prev_hash: str, workspace_id: str, actor: str, action: str, entity: str, data: dict[str, Any],
            request_id: str | None, ts: datetime | None) -> str:
    payload = json.dumps(
        {"prev": prev_hash, "ws": workspace_id, "actor": actor, "action": action, "entity": entity, "data": data,
         "request_id": request_id, "ts": _ts(ts)},
        sort_keys=True,
        default=str,
    )
    return hmac.new(_chain_key(), payload.encode(), hashlib.sha256).hexdigest()


def _row_digest(row: AuditLog, prev_hash: str) -> str:
    return _digest(prev_hash, row.workspace_id, row.actor, row.action, row.entity, row.data, row.request_id, row.ts)


def audit(
    s: Session,
    workspace_id: str,
    actor: str,
    action: str,
    entity: str,
    data: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    data = json.loads(json.dumps(data or {}, default=str))  # hash exactly what the JSON column will return
    request_id = request_id or request_context.request_id.get()  # set per API request by the middleware
    ts = datetime.now(timezone.utc)  # set here (not by the column default) because the hash covers it
    with _lock:
        if s.get_bind().dialect.name == "postgresql":
            # Serialise writers per workspace until this transaction commits: API and worker processes appending
            # concurrently would otherwise both chain from the same previous row (a false tamper alarm).
            s.execute(text("SELECT pg_advisory_xact_lock(hashtext(:ws))"), {"ws": workspace_id})
        last = s.scalars(
            select(AuditLog).where(AuditLog.workspace_id == workspace_id).order_by(AuditLog.id.desc()).limit(1)
        ).first()
        prev = last.hash if last else GENESIS
        row = AuditLog(
            workspace_id=workspace_id,
            actor=actor,
            action=action,
            entity=entity,
            data=data,
            request_id=request_id,
            prev_hash=prev,
            ts=ts,
            hash=_digest(prev, workspace_id, actor, action, entity, data, request_id, ts),
        )
        s.add(row)
        s.flush()
    from probity.logging import get_logger

    get_logger("audit").info("audit.head", workspace_id=workspace_id, audit_id=row.id, head=row.hash)
    return row


def verify_chain(s: Session, workspace_id: str, anchor: tuple[int, str] | None = None) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_id). With `anchor` = a previously witnessed (id, hash) head, also fails when that row
    is gone or changed (truncation); first_bad_id is then the anchor id."""
    prev = GENESIS
    anchored = anchor is None
    for row in s.scalars(select(AuditLog).where(AuditLog.workspace_id == workspace_id).order_by(AuditLog.id)):
        if row.prev_hash != prev or row.hash != _row_digest(row, prev):
            return False, row.id
        if anchor is not None and row.id == anchor[0]:
            if row.hash != anchor[1]:
                return False, row.id
            anchored = True
        prev = row.hash
    return (True, None) if anchored else (False, anchor[0] if anchor else None)


def chain_head(s: Session, workspace_id: str) -> tuple[int, str] | None:
    """The current (id, hash) head, for exporting as an anchor."""
    last = s.scalars(select(AuditLog).where(AuditLog.workspace_id == workspace_id).order_by(AuditLog.id.desc()).limit(1)).first()
    return (last.id, last.hash) if last else None
