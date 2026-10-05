"""Vendor contacts' personal details (docs/PRIVACY.md): viewers see emails and phones masked; an owner can erase a
contact, which deletes it and replaces its name, email and phone with "[erased]" everywhere, audit log included,
while the audit chain still verifies."""

import json

import pytest
from conftest import login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec
from helpers import API, legit_reply_body, record_reply, run_case, send_verification
from sqlalchemy import select, text

from probity.db.models import AuditLog, EvidenceRow, Message, PrivacyRedaction, VendorContact
from probity.privacy import Scrubber, mask_text

PHONE = "+91 20 0000 0000"
REASON = "Erasure request received from the contact by email on 5 October"


@pytest.mark.parametrize("raw,expected", [
    ("write to accounts@alpha-components.test today", "write to a***@alpha-components.test today"),
    ("call +91 98200 12345 or 98200 12345", "call XXXX2345 or XXXX2345"),
    ("invoice INV-2026100512 for ₹4,85,000 dated 2026-10-05", "invoice INV-2026100512 for ₹4,85,000 dated 2026-10-05"),
    ("GSTIN 27AABCA1234F1Z9, PO-7710", "GSTIN 27AABCA1234F1Z9, PO-7710"),
])
def test_mask_text(raw, expected):
    assert mask_text(raw) == expected


def test_scrubber_finds_every_spelling_of_the_phone_and_the_name():
    s = Scrubber(["Ravi Kumar"], ["Ravi@Vendor.test"], ["+91 98200 12345"])
    assert s.text("Ravi  Kumar (ravi@vendor.test, +91-98200-12345, 9820012345, 98200.12345)") == \
        "[erased] ([erased], [erased], [erased], [erased])"
    assert s.text("Ravikumar 198200123456 Kumaran") == "Ravikumar 198200123456 Kumaran"  # whole words / numbers only
    assert s.obj({"to": "ravi@vendor.test", "n": 3, "items": ["Ravi Kumar"]}) == {"to": "[erased]", "n": 3, "items": ["[erased]"]}


def _vendor_contacts(client, h, vendor_id):
    return client.get(f"{API}/vendors/{vendor_id}", headers=h).json()["contacts"]


def test_viewer_sees_contact_details_masked(client, world):
    vid = world.vendors["A"].id
    viewer = _vendor_contacts(client, login(client, "viewer"), vid)[0]
    assert viewer["email"] == "a***@alpha-components.test" and viewer["phone"] == "XXXX0000" and viewer["masked"]
    acc = _vendor_contacts(client, login(client, "accountant"), vid)[0]
    assert acc["email"] == VENDOR_A.contact_email and acc["phone"] == PHONE and "masked" not in acc


@pytest.fixture()
def replied_case(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case) + f"\nPhone {PHONE}")
    client.post(f"{API}/cases/{case['id']}/notes", headers=acc, json={"text": f"Called {VENDOR_A.contact_name} at {PHONE}"})
    return case


def _case_text(client, h, case_id):
    paths = ("", "/evidence", "/audit", "/trace", "/explain", "/notes")
    return {p: client.get(f"{API}/cases/{case_id}{p}", headers=h).text for p in paths}


def test_viewer_sees_correspondence_masked(client, world, replied_case):
    for path, body in _case_text(client, login(client, "viewer"), replied_case["id"]).items():
        if path != "":  # the case JSON includes what the invoice says; the invoice file is visible to viewers anyway
            assert VENDOR_A.contact_email not in body, path
    case = client.get(f"{API}/cases/{replied_case['id']}", headers=login(client, "viewer")).json()
    shown = json.dumps([case["drafts"], case["messages"], case["claims"]])
    assert VENDOR_A.contact_email not in shown and "a***@alpha-components.test" in shown
    acc = client.get(f"{API}/cases/{replied_case['id']}", headers=login(client, "accountant")).json()
    assert acc["drafts"][0]["to_email"] == VENDOR_A.contact_email


def _erase(client, role="owner", mfa=True, reason=REASON, world=None):
    vid = world.vendors["A"].id
    contact = _vendor_contacts(client, login(client, "owner"), vid)[0]
    return client.post(f"{API}/vendors/{vid}/contacts/{contact['id']}/erase", headers=login(client, role, mfa=mfa), json={"reason": reason})


def test_only_an_owner_with_a_reason_can_erase(client, world):  # MFA per workspace policy: test_sod_mfa
    assert _erase(client, "approver", world=world).status_code == 403
    assert _erase(client, reason="because!!!!!", world=world).status_code in (400, 422)
    with owner_session() as s:
        assert s.scalar(select(VendorContact).where(VendorContact.email == VENDOR_A.contact_email)) is not None


def test_erasure_removes_the_contact_everywhere_and_the_chain_still_verifies(client, world, replied_case):
    r = _erase(client, world=world)
    assert r.status_code == 200, r.text
    counts = r.json()
    assert counts["contacts"] == 1 and counts["audit_log"] >= 2 and counts["messages"] >= 1 and counts["evidence"] >= 1

    owner = login(client, "owner")
    assert _vendor_contacts(client, owner, world.vendors["A"].id) == []
    texts = _case_text(client, owner, replied_case["id"])
    for path, body in texts.items():
        if path not in ("", "/evidence", "/explain"):  # these quote the invoice, which is kept as a tax record
            for needle in (VENDOR_A.contact_email, VENDOR_A.contact_name, PHONE):
                assert needle not in body, (path, needle)
    assert json.loads(texts["/audit"])["chain_valid"] is True
    case = json.loads(texts[""])
    corr = json.dumps([case["drafts"], case["messages"], case["claims"]])
    assert VENDOR_A.contact_email not in corr and VENDOR_A.contact_name not in corr and "[erased]" in corr

    with owner_session() as s:
        for row in s.scalars(select(AuditLog).where(AuditLog.workspace_id == world.workspace_id)):
            assert VENDOR_A.contact_email not in json.dumps(row.data), row.action
        for ev in s.scalars(select(EvidenceRow).where(EvidenceRow.source != "invoice")):
            assert VENDOR_A.contact_email not in f"{ev.source_ref} {ev.excerpt} {json.dumps(ev.value)}"
        assert all(VENDOR_A.contact_email not in m.from_email + m.to_email + m.body for m in s.scalars(select(Message)))
        erased = s.scalars(select(AuditLog).where(AuditLog.action == "vendor.contact_erased")).one()
        assert erased.data["reason"] == REASON and VENDOR_A.contact_email not in json.dumps(erased.data)


def test_redacted_audit_row_is_still_tamper_evident(client, world, replied_case):
    assert _erase(client, world=world).status_code == 200
    owner = login(client, "owner")
    with owner_session() as s:
        red = s.scalars(select(PrivacyRedaction).where(PrivacyRedaction.table_name == "audit_log")).first()
        s.execute(text("SET LOCAL session_replication_role = replica"))  # bypass the trigger, as a database attacker could
        s.execute(text("UPDATE audit_log SET data = jsonb_set(data, '{forged}', 'true') WHERE id = :id"), {"id": int(red.row_id)})
    audit_ = client.get(f"{API}/cases/{replied_case['id']}/audit", headers=owner).json()
    assert audit_["chain_valid"] is False and audit_["first_bad_id"] == int(red.row_id)


def test_app_role_cannot_rewrite_the_audit_log_directly(client, world):
    from probity.db.session import session_scope

    assert client.post(f"{API}/vendors", headers=login(client, "accountant"), json={"name": "Gamma Traders"}).status_code == 201

    with pytest.raises(Exception, match="permission denied|has no redaction record"):
        with session_scope(world.workspace_id) as s:
            row_id = s.scalars(select(AuditLog.id)).first()
            s.execute(text("SELECT set_config('probity.redacting', 'on', true)"))
            s.execute(text("UPDATE audit_log SET data = '{}'::jsonb WHERE id = :id"), {"id": row_id})
    with pytest.raises(Exception, match="has no redaction record"):
        with session_scope(world.workspace_id) as s:
            row_id = s.scalars(select(AuditLog.id)).first()
            s.execute(text("SELECT probity_redact_audit(:id, '{}'::jsonb)"), {"id": row_id})


def test_retention_report(client, world):
    r = client.get(f"{API}/workspace/retention", headers=login(client, "owner"))
    assert r.status_code == 200 and r.json()["retention_years"] == 8 and r.json()["automatic_deletion"] is False
    assert client.get(f"{API}/workspace/retention", headers=login(client, "approver")).status_code == 403
