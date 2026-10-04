"""Append-only, hash-chained audit log (Security.md §9, Guardrails G13)."""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import AuditLog

GENESIS = "0" * 64
_lock = threading.Lock()


def _digest(prev_hash: str, workspace_id: str, actor: str, action: str, entity: str, data: dict[str, Any]) -> str:
    payload = json.dumps(
        {"prev": prev_hash, "ws": workspace_id, "actor": actor, "action": action, "entity": entity, "data": data},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def audit(
    s: Session,
    workspace_id: str,
    actor: str,
    action: str,
    entity: str,
    data: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> AuditLog:
    data = data or {}
    with _lock:
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
            hash=_digest(prev, workspace_id, actor, action, entity, data),
        )
        s.add(row)
        s.flush()
    return row


def verify_chain(s: Session, workspace_id: str) -> tuple[bool, int | None]:
    """Returns (ok, first_bad_id)."""
    prev = GENESIS
    for row in s.scalars(select(AuditLog).where(AuditLog.workspace_id == workspace_id).order_by(AuditLog.id)):
        if row.prev_hash != prev or row.hash != _digest(prev, row.workspace_id, row.actor, row.action, row.entity, row.data):
            return False, row.id
        prev = row.hash
    return True, None
