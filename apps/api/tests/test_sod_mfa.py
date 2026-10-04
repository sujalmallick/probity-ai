"""Decision integrity (H3), MFA step-up coverage (H4) and verified-contact edits (H5)."""

import csv
import io

from conftest import login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec
from helpers import API, legit_reply_body, record_reply, run_case, send_verification
from sqlalchemy import select

from probity.db.models import AuditLog, HistoricalInvoice, VendorContact, Workspace

REASON = "verified with the vendor by phone"


def _policy(world, **kw):
    with owner_session() as s:
        ws = s.get(Workspace, world.workspace_id)
        ws.policy = {**(ws.policy or {}), **kw}


def _decide(client, h, case_id, decision, reason=REASON):
    return client.post(f"{API}/cases/{case_id}/decision", headers=h, json={"decision": decision, "reason": reason})


def _confirmed_case(client, world, fake_lookups, critical=False):
    """A bank-change case whose statements approver #0 confirmed out-of-band (score lowered)."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    if critical:
        _policy(world, weight_overrides={"bank_account_changed": 50})
    acc, appr = login(client, "accountant"), login(client, "approver", 0)
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    reply = record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case))
    r = client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr,
                    json={"claim_ids": reply["claim_ids"], "method": "phone_known_contact", "known_channel": True, "confirmed_account_last4": "9812",
                          "note": "Called the accounts contact on the number in our vendor master; confirmed the new account."})
    assert r.status_code == 200, r.text
    return case


# ---------------------------------------------------------------- H3

def test_confirmer_cannot_be_the_only_approver(client, world, fake_lookups):
    case = _confirmed_case(client, world, fake_lookups)
    assert _decide(client, login(client, "approver", 0), case["id"], "APPROVE").json()["status"] == "AWAITING_HUMAN"
    assert _decide(client, login(client, "approver", 1), case["id"], "APPROVE").json()["status"] == "APPROVED"


def test_dual_approval_uses_peak_tier(client, world, fake_lookups):
    """A once-CRITICAL case still needs two approvers after an out-of-band confirmation lowered its score."""
    case = _confirmed_case(client, world, fake_lookups, critical=True)
    now = client.get(f"{API}/cases/{case['id']}", headers=login(client, "viewer")).json()
    assert now["risk"]["tier"] != "CRITICAL"
    assert _decide(client, login(client, "approver", 1), case["id"], "APPROVE").json()["status"] == "AWAITING_HUMAN"  # 1 of 2
    assert _decide(client, login(client, "approver", 0), case["id"], "APPROVE").json()["status"] == "APPROVED"


def test_approvals_before_a_rescore_do_not_count(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    _policy(world, weight_overrides={"bank_account_changed": 50})
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    assert case["risk"]["tier"] == "CRITICAL"
    a1, a2 = login(client, "approver", 0), login(client, "approver", 1)
    assert _decide(client, a1, case["id"], "APPROVE").json()["status"] == "AWAITING_HUMAN"
    assert client.post(f"{API}/cases/{case['id']}/rescore", headers=login(client, "accountant")).status_code == 200
    assert _decide(client, a2, case["id"], "APPROVE").json()["status"] == "AWAITING_HUMAN"  # a1's approval predates the new score
    assert _decide(client, a1, case["id"], "APPROVE").json()["status"] == "APPROVED"


def test_reject_of_high_case_needs_a_real_reason(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    appr = login(client, "approver")
    for reason in ("", "     ", ".....", "no"):
        assert _decide(client, appr, case["id"], "REJECT", reason).status_code == 400, reason
    assert _decide(client, appr, case["id"], "REJECT", "bank change not confirmed by the vendor").json()["status"] == "REJECTED"


def test_rejected_case_closes_only_as_an_issue_and_never_enters_history(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    appr = login(client, "approver")
    _decide(client, appr, case["id"], "REJECT", "bank change not confirmed by the vendor")
    close = lambda outcome, res: client.post(f"{API}/cases/{case['id']}/close", headers=appr, json={"outcome": outcome, "resolution": res})  # noqa: E731
    assert close("CLEARED", "vendor called back and it was fine").status_code == 400
    assert close("CONFIRMED_ISSUE", "").status_code == 400  # a resolution is required
    assert close("CONFIRMED_ISSUE", "payment diversion attempt, vendor notified").status_code == 200
    with owner_session() as s:
        assert s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.case_id == case["id"])).first() is None


# ---------------------------------------------------------------- H4

def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def test_mfa_required_everywhere_it_matters(client, world, fake_lookups):
    _policy(world, require_mfa_for_approvals=True)
    owner, owner_mfa = login(client, "owner"), login(client, "owner", mfa=True)
    vid = world.vendors["A"].id
    with owner_session() as s:
        contact_id = s.scalars(select(VendorContact.id).where(VendorContact.vendor_id == vid)).first()
    csv_body = _csv(["name"], [["Brand New Supplier Pvt Ltd"]])
    calls = [
        ("put", "/workspace/policy", {"json": {"require_mfa_for_approvals": False}}),
        ("post", "/workspace/invitations", {"json": {"email": "new.owner@users.test", "role": "owner"}}),
        ("patch", "/workspace", {"json": {"name": "Renamed"}}),
        ("post", "/imports/vendors?dry_run=false", {"files": {"file": ("v.csv", csv_body, "text/csv")}}),
        ("patch", f"/vendors/{vid}", {"json": {"address": "Somewhere else"}}),
        ("delete", f"/vendors/{vid}/contacts/{contact_id}", {}),
    ]
    for method, path, kw in calls:
        assert getattr(client, method)(f"{API}{path}", headers=owner, **kw).status_code == 403, path
    assert client.put(f"{API}/workspace/policy", headers=owner_mfa, json={"auto_clear_enabled": True}).status_code == 200
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    r = client.post(f"{API}/cases/{case['id']}/close", headers=login(client, "approver"), json={"outcome": "INCONCLUSIVE", "resolution": "closing without decision"})
    assert r.status_code == 403


# ---------------------------------------------------------------- H5

def _contact(world):
    with owner_session() as s:
        c = s.scalars(select(VendorContact).where(VendorContact.vendor_id == world.vendors["A"].id, VendorContact.verified.is_(True))).first()
        return c.id


def test_accountant_phone_edit_drops_verification(client, world):
    cid = _contact(world)
    r = client.patch(f"{API}/vendors/{world.vendors['A'].id}/contacts/{cid}", headers=login(client, "accountant"), json={"phone": "+44 7700 900123"})
    assert r.status_code == 200 and r.json()["verified"] is False
    with owner_session() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "vendor.contact_updated").order_by(AuditLog.id.desc())).first()
        assert row.data["changes"]["phone"]["after"] == "+44 7700 900123"


def test_approver_must_explain_reverifying_a_changed_contact(client, world):
    cid, vid, appr = _contact(world), world.vendors["A"].id, login(client, "approver")
    assert client.patch(f"{API}/vendors/{vid}/contacts/{cid}", headers=appr, json={"phone": "+91 22 5555 0000"}).status_code == 400
    r = client.patch(f"{API}/vendors/{vid}/contacts/{cid}", headers=appr,
                     json={"phone": "+91 22 5555 0000", "verification_note": "New number confirmed on a call to the old number on file"})
    assert r.status_code == 200 and r.json()["verified"] is True
