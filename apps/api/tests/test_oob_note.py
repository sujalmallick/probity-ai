"""Out-of-band confirmation needs a real written note and a channel already on file (G11).

A vendor reply alone never lowers the score; the approver's confirmation is the only path, so the
server rejects empty or token notes and an explicit "not a channel on file" attestation.
"""

import pytest
from conftest import login
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec
from helpers import API, legit_reply_body, record_reply, run_case, send_verification

from probity.services import OOB_NOTE_MIN_CHARS


@pytest.fixture(autouse=True)
def _world(world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21


def _case_with_reply(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    reply = record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case))
    return case["id"], reply["claim_ids"], acc, appr


def _score(client, h, case_id):
    return client.get(f"{API}/cases/{case_id}", headers=h).json()["risk"]["score"]


def test_note_too_short_is_rejected_and_score_unchanged(client):
    case_id, claim_ids, acc, appr = _case_with_reply(client)
    for note in ["", "   ", "called them", "x" * (OOB_NOTE_MIN_CHARS - 1), " " * 30]:
        r = client.post(f"{API}/cases/{case_id}/out-of-band-confirmation", headers=appr,
                        json={"claim_ids": claim_ids, "method": "phone_known_contact", "note": note, "known_channel": True})
        assert r.status_code == 400, (note, r.text)
        assert str(OOB_NOTE_MIN_CHARS) in r.json()["error"]["message"]
    assert _score(client, acc, case_id) == 70


def test_channel_not_on_file_is_rejected(client):
    case_id, claim_ids, acc, appr = _case_with_reply(client)
    r = client.post(f"{API}/cases/{case_id}/out-of-band-confirmation", headers=appr,
                    json={"claim_ids": claim_ids, "method": "phone_known_contact", "known_channel": False,
                          "note": "Called the number printed in the vendor's reply email"})
    assert r.status_code == 400
    assert "already on file" in r.json()["error"]["message"]
    assert _score(client, acc, case_id) == 70


def test_valid_note_rescores_and_audits_attestation(client):
    case_id, claim_ids, acc, appr = _case_with_reply(client)
    r = client.post(f"{API}/cases/{case_id}/out-of-band-confirmation", headers=appr,
                    json={"claim_ids": claim_ids, "method": "phone_known_contact", "known_channel": True, "confirmed_account_last4": "9812",
                          "note": "Called the accounts contact on the number in our vendor master; confirmed the new account."})
    assert r.status_code == 200, r.text
    assert _score(client, acc, case_id) == 20
    audit = client.get(f"{API}/cases/{case_id}/audit", headers=acc).json()
    oob = [a for a in audit["items"] if a["action"] == "verification.out_of_band"]
    assert oob and oob[-1]["data"]["known_channel"] is True
