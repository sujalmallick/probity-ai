"""Full bank account numbers never appear in case JSON, evidence or exports, whatever free text they arrive in (H6)."""

import pytest
from conftest import login
from factories import NEW_ACCOUNT, NEW_DOMAIN, VENDOR_A, bank_change_spec
from helpers import API, record_reply, run_case, send_verification

from probity.guardrails.text import mask_account_numbers


@pytest.mark.parametrize("raw,expected", [
    ("pay to 50100098129812 please", "pay to XXXX9812 please"),
    ("a/c 5010 0098 1298 12.", "a/c XXXX9812."),
    ("a/c 5010-0098-1298-12", "a/c XXXX9812"),
    ("invoice INV-4821 dated 2026-10-04 for ₹4,85,000.00", "invoice INV-4821 dated 2026-10-04 for ₹4,85,000.00"),
    ("GSTIN 27AABCA1234F1Z9", "GSTIN 27AABCA1234F1Z9"),
])
def test_mask_account_numbers(raw, expected):
    assert mask_account_numbers(raw) == expected


def test_reply_body_with_full_account_is_masked_for_every_role(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc = login(client, "accountant")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, login(client, "approver"))
    spaced = " ".join(NEW_ACCOUNT[i:i + 4] for i in range(0, len(NEW_ACCOUNT), 4))
    record_reply(client, case, acc, VENDOR_A.contact_email,
                 f"Please confirm our bank account {NEW_ACCOUNT} (also written {spaced}) is the one we moved to last month.")
    for role in ("viewer", "accountant", "approver", "owner"):
        h = login(client, role)
        for path in (f"/cases/{case['id']}", f"/cases/{case['id']}/evidence", f"/cases/{case['id']}/export"):
            body = client.get(f"{API}{path}", headers=h).text.replace(" ", "")
            assert NEW_ACCOUNT not in body, (role, path)
    assert "XXXX9812" in client.get(f"{API}/cases/{case['id']}", headers=login(client, "viewer")).text
