"""Workspace risk policy (Feature F13). Owner-editable, audited."""

from __future__ import annotations

from typing import Any

from probity.db.models import Workspace
from probity.risk.engine import CURRENT_VERSION

DEFAULT_POLICY: dict[str, Any] = {
    "auto_clear_enabled": True,
    "auto_clear_max_amount_minor": 5_00_000_00,  # ₹5,00,000
    "external_research_amount_minor": 2_00_000_00,  # ₹2,00,000
    "dual_approval_amount_minor": 10_00_000_00,  # ₹10,00,000
    "require_reason_for_approve_tiers": ["HIGH", "CRITICAL"],
    "weights_version": CURRENT_VERSION,
    "weight_overrides": {},
    "previously_flagged_blocks_auto_clear": True,
    "require_mfa_for_approvals": False,  # turn on once approvers have MFA enrolled in Clerk
}


def get_policy(ws: Workspace | None) -> dict[str, Any]:
    return {**DEFAULT_POLICY, **((ws.policy if ws else None) or {})}
