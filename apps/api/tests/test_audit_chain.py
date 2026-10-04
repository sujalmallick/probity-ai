"""Audit chain integrity (H9): a database-only attacker can't edit, reorder, forge or recompute the chain, and a
witnessed head detects truncation. Pure unit tests on in-memory SQLite (raw SQL bypasses the ORM immutability guard)."""

import hashlib
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from probity.db import audit as audit_mod
from probity.db.audit import GENESIS, audit, chain_head, verify_chain
from probity.db.models import AuditLog

WS = "ws_test"


@pytest.fixture()
def s(monkeypatch):
    monkeypatch.setattr(audit_mod, "_chain_key", lambda: b"k" * 32)
    engine = create_engine("sqlite://")
    AuditLog.__table__.create(engine)
    with Session(engine) as session:
        for i in range(4):
            audit(session, WS, "usr_1", "test.action", f"e{i}", {"i": i}, request_id=f"req{i}")
        session.commit()
        yield session


def _ids(s):
    return [r[0] for r in s.execute(text("SELECT id FROM audit_log ORDER BY id"))]


def test_untouched_chain_verifies(s):
    assert verify_chain(s, WS) == (True, None)
    assert verify_chain(s, WS, anchor=chain_head(s, WS)) == (True, None)


@pytest.mark.parametrize("column,value", [("data", "'{\"i\": 99}'"),("actor", "'usr_2'"), ("ts", "'2020-01-01 00:00:00.000000'"), ("request_id", "'forged'")])
def test_edit_of_any_column_detected(s, column, value):
    target = _ids(s)[1]
    s.execute(text(f"UPDATE audit_log SET {column} = {value} WHERE id = :id"), {"id": target})
    s.expire_all()
    assert verify_chain(s, WS) == (False, target)


def test_middle_delete_detected(s):
    ids = _ids(s)
    s.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": ids[1]})
    assert verify_chain(s, WS) == (False, ids[2])


def test_tail_truncation_detected_with_witnessed_head(s):
    head = chain_head(s, WS)
    s.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": head[0]})
    s.expire_all()
    assert verify_chain(s, WS) == (True, None)  # a shorter chain is internally consistent...
    assert verify_chain(s, WS, anchor=head) == (False, head[0])  # ...but the witnessed head is gone


def _append_raw(s, prev_hash, digest):
    s.execute(text("INSERT INTO audit_log (workspace_id, actor, action, entity, data, request_id, prev_hash, hash, ts) "
                   "VALUES (:ws, 'usr_x', 'case.approved', 'case_x', '{}', NULL, :prev, :h, '2026-01-01 00:00:00.000000')"),
              {"ws": WS, "prev": prev_hash, "h": digest})


def test_forged_append_without_the_key_detected(s):
    """The old chain was plain SHA-256, so anyone with DB access could append a valid-looking row."""
    prev = chain_head(s, WS)[1]
    payload = json.dumps({"prev": prev, "ws": WS, "actor": "usr_x", "action": "case.approved", "entity": "case_x", "data": {}}, sort_keys=True)
    _append_raw(s, prev, hashlib.sha256(payload.encode()).hexdigest())
    ok, bad = verify_chain(s, WS)
    assert not ok and bad == _ids(s)[-1]


def test_full_recompute_with_another_key_detected(s, monkeypatch):
    rows = list(s.execute(text("SELECT id, workspace_id, actor, action, entity, data, request_id, ts FROM audit_log ORDER BY id")))
    monkeypatch.setattr(audit_mod, "_chain_key", lambda: b"attacker-guess" * 3)
    prev = GENESIS
    for r in rows:
        from datetime import datetime

        h = audit_mod._digest(prev, r.workspace_id, r.actor, r.action, r.entity, json.loads(r.data), r.request_id, datetime.fromisoformat(r.ts))
        s.execute(text("UPDATE audit_log SET prev_hash = :p, hash = :h WHERE id = :id"), {"p": prev, "h": h, "id": r.id})
        prev = h
    monkeypatch.setattr(audit_mod, "_chain_key", lambda: b"k" * 32)
    s.expire_all()
    assert verify_chain(s, WS) == (False, rows[0].id)


def test_reorder_detected(s):
    a, b = _ids(s)[1:3]
    s.execute(text("UPDATE audit_log SET data = CASE id WHEN :a THEN '{\"i\": 2}' WHEN :b THEN '{\"i\": 1}' END WHERE id IN (:a, :b)"), {"a": a, "b": b})
    s.expire_all()
    assert verify_chain(s, WS)[0] is False


def test_rows_carry_the_request_id_from_context(s):
    from probity import request_context

    token = request_context.request_id.set("req-from-middleware")
    try:
        row = audit(s, WS, "usr_1", "case.approved", "case_1", {})
    finally:
        request_context.request_id.reset(token)
    assert row.request_id == "req-from-middleware"
    assert verify_chain(s, WS) == (True, None)
