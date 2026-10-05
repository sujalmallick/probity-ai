"""Personal details of vendor contacts (docs/PRIVACY.md).

Two protections:
- Viewers see contact emails and phone numbers masked, wherever they appear (vendor contacts, correspondence,
  evidence, audit entries, notifications). Accountants and above need them to do the work and see them in full.
- An owner can erase one contact on request: the contact is deleted and its name, email and phone are replaced by
  "[erased]" everywhere Probity wrote them, including the append-only audit log, whose chain still verifies through
  a signed redaction record per changed row. Copies of an invoice (the stored file and what was read from it) are
  kept: invoices are retained by law for RETENTION_YEARS.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import Text, cast, delete, func, or_, select, text
from sqlalchemy.orm import Session

from probity.db.audit import audit, audit_row_content, redaction_mac, verify_chain
from probity.db.models import (
    AgentEvent, AuditLog, Case, CaseMemory, CaseNote, ClaimRow, Decision, Draft, EvidenceRow, ImportJob, Message, Notification,
    PrivacyRedaction, User, VendorContact, new_id, utcnow,
)
from probity.ingestion.validators import EMAIL_RE

ERASED = "[erased]"
_INTL_PHONE = re.compile(r"\+\d{1,3}(?:[ -]?\d){7,12}(?![\w])")
_MOBILE = re.compile(r"(?<![\w+-])0?[6-9]\d{4}[ -]?\d{5}(?![\w-])")  # 98200 12345, 9820012345, 09820012345


# ---------------------------------------------------------------- masking for viewers

def sees_contacts(user: User | None) -> bool:
    from probity.services import ROLES  # services serialises cases through this module

    return user is not None and ROLES.index(user.role) >= ROLES.index("accountant")


def mask_email(addr: str | None) -> str | None:
    """'accounts@alpha.test' → 'a***@alpha.test'. The domain stays: it is what the spoofing checks compare."""
    if not addr:
        return addr
    local, _, domain = addr.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    return "XXXX" + digits[-4:] if len(digits) > 4 else "XXXX"


def mask_text(value: str | None) -> str | None:
    """Emails, international-format phone numbers and Indian mobile numbers in free text."""
    if not value:
        return value
    value = EMAIL_RE.sub(lambda m: mask_email(m.group(0)) or "", value)
    value = _INTL_PHONE.sub(lambda m: mask_phone(m.group(0)) or "", value)
    return _MOBILE.sub(lambda m: mask_phone(m.group(0)) or "", value)


def mask_contacts(obj: Any) -> Any:
    """mask_text over every string in a JSON-like value (keys untouched)."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask_contacts(v) for v in obj]
    if isinstance(obj, dict):
        return {k: mask_contacts(v) for k, v in obj.items()}
    return obj


def for_viewer(user: User | None, obj: Any) -> Any:
    """`obj` unchanged for accountant and above; contact details masked for a viewer."""
    return obj if sees_contacts(user) else mask_contacts(obj)


# ---------------------------------------------------------------- erasure

class Scrubber:
    """Finds one person's name, email addresses and phone numbers in text, however a phone number is spaced."""

    def __init__(self, names: list[str], emails: list[str], phones: list[str]) -> None:
        pats: list[str] = []
        for e in {e.strip().lower() for e in emails if e and e.strip()}:
            pats.append(re.escape(e))
        for p in {re.sub(r"\D", "", p or "") for p in phones}:
            if len(p) >= 7:  # the subscriber number (last 10 digits), with or without a country code and separators
                core = p[-10:]
                pats.append(r"(?<![\d+])(?:\+\d{1,3}[\s.-]*)?" + r"[\s().-]*".join(core) + r"(?!\d)")
        for n in {(n or "").strip() for n in names}:
            if len(re.sub(r"\W", "", n)) >= 3:  # a lone initial would erase every "A." in the workspace
                pats.append(r"(?<!\w)" + r"\s+".join(map(re.escape, n.split())) + r"(?!\w)")
        self.regex = re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE) if pats else None
        # Cheap SQL prefilter (a superset): the email, the phone's last 4 digits, the longest word of the name.
        words = [max(n.split(), key=len) for n in names if n and len(re.sub(r"\W", "", n)) >= 3]
        self.needles = sorted({e.strip().lower() for e in emails if e} | {re.sub(r"\D", "", p)[-4:] for p in phones if p and len(re.sub(r"\D", "", p)) >= 7}
                              | {w for w in words if len(w) >= 3})

    def text(self, value: str | None) -> str | None:
        return self.regex.sub(ERASED, value) if value and self.regex else value

    def obj(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.obj(v) for v in value]
        if isinstance(value, dict):
            return {k: self.obj(v) for k, v in value.items()}
        return value

    def candidates(self, column: Any) -> Any:
        """SQL condition that every row mentioning this person satisfies (rows are then checked in Python)."""
        col = cast(column, Text)
        return or_(*[col.ilike(f"%{n}%") for n in self.needles]) if self.needles else text("false")


# Free text Probity wrote about cases and correspondence. Not listed on purpose: cases.extraction/validation (what the
# invoice itself says; the invoice is a statutory record) and evidence quoted from the invoice (handled below).
_SCRUB: list[tuple[Any, tuple[str, ...]]] = [
    (Message, ("from_email", "to_email", "subject", "body")),
    (Draft, ("to_email", "subject", "body")),
    (Notification, ("title", "body")),
    (CaseNote, ("text",)),
    (ClaimRow, ("statement", "supporting_quote", "verifier_notes", "data")),
    (Case, ("summary", "resolution", "recommendation")),
    (CaseMemory, ("summary", "resolution", "issues")),
    (Decision, ("reason",)),
    (ImportJob, ("errors",)),
    (AgentEvent, ("message", "data")),
]


class ChainBroken(Exception):
    pass


def erase_contact(s: Session, user: User, contact: VendorContact, reason: str) -> dict[str, int | str]:
    """Delete the contact (and any other contact row in the workspace with the same email: the same person) and replace
    their name, email and phone with "[erased]" in everything Probity wrote. Runs in the caller's transaction."""
    ws = user.workspace_id
    ok, bad = verify_chain(s, ws)
    if not ok:  # never re-sign a row that already fails verification: that would launder tampering
        raise ChainBroken(f"the audit log fails verification at entry {bad}; investigate before erasing anything")
    same = list(s.scalars(select(VendorContact).where(VendorContact.workspace_id == ws, func.lower(VendorContact.email) == contact.email.lower())))
    scrub = Scrubber([c.name or "" for c in same], [c.email for c in same], [c.phone or "" for c in same])
    erasure_id = new_id("era")
    counts: dict[str, int | str] = {"erasure_id": erasure_id, "contacts": len(same)}

    for model, cols in _SCRUB:
        n = 0
        cond = or_(*[scrub.candidates(getattr(model, c)) for c in cols])
        for row in s.scalars(select(model).where(model.workspace_id == ws, cond)):
            changed = False
            for c in cols:
                before = getattr(row, c)
                after = scrub.obj(before)
                if after != before:
                    setattr(row, c, after)
                    changed = True
            n += changed
        counts[model.__tablename__] = n

    # Evidence and the audit log are immutable for the app: each changed row gets a signed redaction record first,
    # then a database function (the only way to change these rows) rewrites it.
    n = 0
    for ev in s.scalars(select(EvidenceRow).where(EvidenceRow.workspace_id == ws, EvidenceRow.source != "invoice",
                                                  or_(scrub.candidates(EvidenceRow.source_ref), scrub.candidates(EvidenceRow.excerpt), scrub.candidates(EvidenceRow.value)))):
        new = {"source_ref": scrub.text(ev.source_ref) or "", "excerpt": scrub.text(ev.excerpt), "value": _jsonable(scrub.obj(ev.value))}
        if new == {"source_ref": ev.source_ref, "excerpt": ev.excerpt, "value": ev.value}:
            continue
        content = {"ws": ws, "case_id": ev.case_id, "source": ev.source, "field": ev.field, "content_hash": ev.content_hash, **new}
        _record(s, ws, "evidence", ev.id, erasure_id, user.id, content)
        s.execute(text("SELECT probity_redact_evidence(:id, :ref, :ex, CAST(:val AS jsonb))"),
                  {"id": ev.id, "ref": new["source_ref"], "ex": new["excerpt"], "val": json.dumps(new["value"])})
        s.expire(ev)
        n += 1
    counts["evidence"] = n

    n = 0
    for row in s.scalars(select(AuditLog).where(AuditLog.workspace_id == ws, scrub.candidates(AuditLog.data)).order_by(AuditLog.id)):
        data = _jsonable(scrub.obj(row.data))
        if data == row.data:
            continue
        content = {**audit_row_content(row), "data": data}
        _record(s, ws, "audit_log", str(row.id), erasure_id, user.id, content)
        s.execute(text("SELECT probity_redact_audit(:id, CAST(:data AS jsonb))"), {"id": row.id, "data": json.dumps(data)})
        s.expire(row)
        n += 1
    counts["audit_log"] = n

    for c in same:
        s.execute(delete(VendorContact).where(VendorContact.id == c.id))
    s.flush()
    # The erasure itself is audited with counts only: writing who was erased would undo it.
    audit(s, ws, user.id, "vendor.contact_erased", contact.vendor_id, {**counts, "reason": scrub.text(reason)})
    ok, bad = verify_chain(s, ws)
    if not ok:  # belt and braces: abort the whole transaction rather than commit a log that no longer verifies
        raise ChainBroken(f"erasure would leave the audit log failing verification at entry {bad}")
    return counts


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _record(s: Session, ws: str, table: str, row_id: str, erasure_id: str, by: str, content: dict[str, Any]) -> None:
    mac = redaction_mac(table, row_id, erasure_id, by, content)
    existing = s.scalars(select(PrivacyRedaction).where(PrivacyRedaction.table_name == table, PrivacyRedaction.row_id == row_id)).first()
    if existing is not None:  # a row can mention two erased people: the newest record describes it as it now reads
        existing.erasure_id, existing.erased_by, existing.erased_at, existing.mac = erasure_id, by, utcnow(), mac
    else:
        s.add(PrivacyRedaction(workspace_id=ws, table_name=table, row_id=row_id, erasure_id=erasure_id, erased_by=by, mac=mac))
    s.flush()
