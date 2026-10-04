"""Workspace policy values are typed and bounded (H12)."""

import pytest
from conftest import login

from helpers import API


@pytest.mark.parametrize("body", [
    {"auto_clear_max_amount_minor": "lots"},
    {"auto_clear_max_amount_minor": -1},
    {"dual_approval_amount_minor": 10**20},
    {"require_mfa_for_approvals": "no"},
    {"auto_clear_enabled": 1},
    {"weight_overrides": {"bank_account_changed": -100}},
    {"weight_overrides": {"bank_account_changed": 101}},
    {"weight_overrides": {"price_anomaly": "abc"}},
    {"weight_overrides": {"made_up_signal": 10}},
    {"demo_agent_delay_ms": 1_000_000_000},
    {"something_else": True},
])
def test_invalid_policy_rejected(client, world, body):
    assert client.put(f"{API}/workspace/policy", headers=login(client, "owner"), json=body).status_code == 422, body


def test_valid_policy_saved(client, world):
    owner = login(client, "owner")
    body = {"auto_clear_enabled": False, "auto_clear_max_amount_minor": 1_00_000_00, "weight_overrides": {"price_anomaly": 25}}
    r = client.put(f"{API}/workspace/policy", headers=owner, json=body)
    assert r.status_code == 200, r.text
    p = client.get(f"{API}/workspace/policy", headers=owner).json()
    assert p["auto_clear_enabled"] is False and p["weights"]["price_anomaly"] == 25


def test_non_owner_cannot_change_policy(client, world):
    assert client.put(f"{API}/workspace/policy", headers=login(client, "approver"), json={"auto_clear_enabled": False}).status_code == 403
