"""Persist evidence (immutable, content-hashed) and claims. All access is workspace-scoped."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import iso, ClaimRow, EvidenceRow
from probity.evidence.models import AgentClaim, EvidenceIn


def _hash(ev: EvidenceIn) -> str:
    payload = json.dumps(ev.model_dump(exclude={"match_key"}), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def add_evidence(s: Session, workspace_id: str, case_id: str, agent: str, ev: EvidenceIn) -> EvidenceRow:
    h = _hash(ev)
    existing = s.scalars(
        select(EvidenceRow).where(EvidenceRow.workspace_id == workspace_id, EvidenceRow.case_id == case_id, EvidenceRow.content_hash == h)
    ).first()
    if existing:
        return existing
    row = EvidenceRow(
        workspace_id=workspace_id,
        case_id=case_id,
        source=ev.source,
        field=ev.field,
        value={"v": ev.value, "k": ev.match_key},
        source_ref=ev.source_ref or ev.source,
        excerpt=ev.excerpt,
        tier=ev.tier,
        content_hash=h,
        agent=agent,
    )
    s.add(row)
    s.flush()
    return row


def add_claim(s: Session, workspace_id: str, case_id: str, agent: str, c: AgentClaim) -> ClaimRow:
    ev_ids = list(c.evidence_ids)
    for ev in c.evidence:
        ev_ids.append(add_evidence(s, workspace_id, case_id, agent, ev).id)
    # Resolve positional references in the assertion ("$0", "$1") to stored evidence ids.
    assertion = _resolve_refs(c.assertion, ev_ids)
    row = ClaimRow(
        workspace_id=workspace_id,
        case_id=case_id,
        agent=agent,
        statement=c.claim,
        signal=c.signal,
        assertion=assertion,
        evidence_ids=ev_ids,
        status="unverified",
        confidence=c.confidence,
        severity=c.severity,
        data=c.data,
    )
    s.add(row)
    s.flush()
    return row


def _resolve_refs(obj: Any, ids: list[str]) -> Any:
    if isinstance(obj, str) and obj.startswith("$") and obj[1:].isdigit():
        i = int(obj[1:])
        return ids[i] if i < len(ids) else obj
    if isinstance(obj, list):
        return [_resolve_refs(o, ids) for o in obj]
    if isinstance(obj, dict):
        return {k: _resolve_refs(v, ids) for k, v in obj.items()}
    return obj


def evidence_value(row: EvidenceRow) -> Any:
    return (row.value or {}).get("v")


def evidence_key(row: EvidenceRow) -> Any:
    v = row.value or {}
    return v.get("k") if v.get("k") is not None else v.get("v")


def evidence_public(row: EvidenceRow) -> dict:
    return {
        "id": row.id,
        "source": row.source,
        "field": row.field,
        "value": evidence_value(row),
        "source_ref": row.source_ref,
        "excerpt": row.excerpt,
        "tier": row.tier,
        "retrieved_at": iso(row.retrieved_at),
        "agent": row.agent,
        "content_hash": row.content_hash,
    }
