"""Baseline approval: past invoices and purchase orders are what every new invoice is compared against, so they can
be trusted only once an approver has accepted them (security finding M9).

- Rows entered or imported by an approver or owner count as approved by that person.
- Rows entered or imported by an accountant wait for an approver; until then they are left out of the price, PO and
  quantity comparisons (the check then says "could not verify" with the reason), but they still count for duplicate
  detection, because that can only add scrutiny.
- Editing an approved row as an accountant puts it back to pending. Rows written by a decided case are approved by that
  decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from probity.db.models import HistoricalInvoice, PurchaseOrder, User

ROLES = ["viewer", "accountant", "approver", "owner"]


def can_approve(user: User) -> bool:
    return ROLES.index(user.role) >= ROLES.index("approver")


def stamp_new(row: HistoricalInvoice | PurchaseOrder, user: User, source: str) -> None:
    row.source = source
    row.entered_by = user.id
    if can_approve(user):
        approve(row, user)
    else:
        row.approved_by = row.approved_at = None


def stamp_edit(row: HistoricalInvoice | PurchaseOrder, user: User) -> bool:
    """Returns True if the edit sent an approved row back to pending."""
    if can_approve(user):
        approve(row, user)
        return False
    was = row.approved_at is not None
    row.approved_by = row.approved_at = None
    return was


def approve(row: HistoricalInvoice | PurchaseOrder, user: User) -> None:
    row.approved_by = user.id
    row.approved_at = datetime.now(timezone.utc)


def is_approved(row: Any) -> bool:
    return getattr(row, "approved_at", None) is not None


def status(row: Any) -> str:
    return "approved" if is_approved(row) else "pending"
