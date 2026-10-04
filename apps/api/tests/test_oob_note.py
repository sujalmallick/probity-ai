"""Out-of-band confirmation needs a real written note and a channel already on file (G11).

A vendor reply alone never lowers the score; the approver's confirmation is the only path, so the
server rejects empty or token notes and an explicit "not a channel on file" attestation.
"""

from conftest import login
from test_demo_flow import API, upload_and_run

from probity.services import OOB_NOTE_MIN_CHARS


def _case_with_reply(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    assert client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify bank change"}).status_code == 200
    draft = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    assert client.post(f"{API}/cases/{case['id']}/drafts/{draft['id']}/send", headers=appr, json={}).status_code == 200
    reply = client.post(f"{API}/demo/vendor-reply/{case['id']}?kind=legit", headers=acc).json()
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
                    json={"claim_ids": claim_ids, "method": "phone_known_contact", "known_channel": True,
                          "note": "Called the accounts contact on the number in our vendor master; confirmed the new account."})
    assert r.status_code == 200, r.text
    assert _score(client, acc, case_id) == 20
    audit = client.get(f"{API}/cases/{case_id}/audit", headers=acc).json()
    oob = [a for a in audit["items"] if a["action"] == "verification.out_of_band"]
    assert oob and oob[-1]["data"]["known_channel"] is True


def test_demo_users_carry_workspace_name(client):
    users = client.get(f"{API}/auth/demo-users").json()
    assert users and all(u["workspace"]["id"] and u["workspace"]["name"] for u in users)
