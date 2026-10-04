"""Notifications and the searchable, paginated case queue."""

import pytest
from conftest import login
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec, clean_spec, injection_spec
from helpers import API, legit_reply_body, record_reply, run_case, send_verification

SPECS = {"bank_change": bank_change_spec, "clean": clean_spec, "injection": injection_spec}


@pytest.fixture(autouse=True)
def _world(world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21


def upload_and_run(client, h, name):
    return run_case(client, h, SPECS[name]())


def test_held_case_and_vendor_reply_notify_approvers(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = upload_and_run(client, acc, "bank_change")
    n = client.get(f"{API}/notifications", headers=appr).json()
    assert n["unread"] == 1 and n["items"][0]["kind"] == "case_held" and n["items"][0]["case_id"] == case["id"]
    assert "70/100 HIGH" in n["items"][0]["title"]
    assert client.get(f"{API}/notifications", headers=acc).json()["unread"] == 0  # accountants aren't asked to decide

    send_verification(client, case, appr)
    record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case))
    n = client.get(f"{API}/notifications?unread_only=true", headers=appr).json()
    assert n["unread"] == 2 and n["items"][0]["kind"] == "vendor_replied"

    assert client.post(f"{API}/notifications/{n['items'][0]['id']}/read", headers=appr).json()["read"]
    assert client.get(f"{API}/notifications", headers=appr).json()["unread"] == 1
    assert client.post(f"{API}/notifications/read-all", headers=appr).json()["marked"] == 1
    other = client.get(f"{API}/notifications", headers=login(client, "approver", 1)).json()["items"][0]["id"]
    assert client.post(f"{API}/notifications/{other}/read", headers=appr).status_code == 404  # can't touch someone else's


def test_auto_cleared_case_does_not_notify(client):
    upload_and_run(client, login(client, "accountant"), "clean")
    assert client.get(f"{API}/notifications", headers=login(client, "approver")).json()["unread"] == 0


def test_case_queue_search_filter_and_pagination(client):
    acc = login(client, "accountant")
    held = upload_and_run(client, acc, "bank_change")
    clean = upload_and_run(client, acc, "clean")
    upload_and_run(client, acc, "injection")
    items = client.get(f"{API}/cases?q=beta", headers=acc).json()["items"]
    assert [c["id"] for c in items] == [clean["id"]]
    assert [c["id"] for c in client.get(f"{API}/cases?q=AC-4821", headers=acc).json()["items"]] == [held["id"]]
    assert {c["id"] for c in client.get(f"{API}/cases?tier=HIGH,CRITICAL", headers=acc).json()["items"]} == {held["id"]}
    assert {c["status"] for c in client.get(f"{API}/cases?status=AUTO_CLEARED", headers=acc).json()["items"]} == {"AUTO_CLEARED"}
    p1 = client.get(f"{API}/cases?limit=2", headers=acc).json()
    assert len(p1["items"]) == 2 and p1["next_cursor"]
    p2 = client.get(f"{API}/cases?limit=2&cursor={p1['next_cursor']}", headers=acc).json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None
    assert not {c["id"] for c in p1["items"]} & {c["id"] for c in p2["items"]}
    assert client.get(f"{API}/cases?cursor=garbage", headers=acc).status_code == 400
