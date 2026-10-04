"""End-to-end case flow through the HTTP API, on factory data with injected fakes (no network, no AI)."""

from conftest import bearer, login
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec, build_world, clean_spec, next_invoice_spec, pdf
from helpers import API, contributions, gate, legit_reply_body, record_reply, run_case, send_verification


def test_bank_change_case_end_to_end(client, world, fake_lookups, outbox):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc, appr = login(client, "accountant"), login(client, "approver")

    # Investigation: three verified anomalies, scored by the pure-code engine
    case = run_case(client, acc, bank_change_spec())
    assert case["status"] == "AWAITING_HUMAN", case["status"]
    assert case["amount"]["amount_minor"] == 56640000
    assert contributions(case) == {"bank_account_changed": 35, "price_anomaly": 20, "new_domain": 15}
    assert case["risk"]["score"] == 70 and case["risk"]["tier"] == "HIGH"
    bank = next(c for c in case["risk"]["contributions"] if c["signal"] == "bank_account_changed")
    assert bank["observed"] == "XXXX9812" and bank["baseline"] == "XXXX1234"
    price = next(c for c in case["claims"] if c["signal"] == "price_anomaly")
    assert price["data"]["pct_change"] == 62.7 and price["status"] == "verified"
    assert case["recommendation"]["action"] == "HOLD_PAYMENT"
    assert case["validation"]["gstin_checksum"]["ok"] and case["validation"]["arithmetic"]["ok"]
    # AI unavailable → the engine's own summary, labelled as such; web research could not run → said so, not "clean"
    assert case["recommendation"]["summary_source"] == "engine" and "3 verified anomalies" in case["summary"]
    assert case["checks"]["external_reputation"]["status"] == "could_not_verify"
    assert not any("No adverse public findings" in c["statement"] for c in case["claims"])
    assert case["checks"]["gst_registry"]["status"] == "could_not_verify"

    ex = client.get(f"{API}/cases/{case['id']}/explain", headers=acc).json()
    for step in ex["steps"]:
        if step["points"]:
            assert step["status"] == "counted" and step["evidence"], step

    assert client.post(f"{API}/cases/{case['id']}/decision", headers=acc, json={"decision": "APPROVE", "reason": "x"}).status_code == 403

    # Verification email: neutral template signed with the workspace name, to the verified contact, through the allowlist
    draft = send_verification(client, case, appr)
    assert draft["to_email"] == VENDOR_A.contact_email and draft["recipient_verified"]
    assert "fraud" not in draft["body"].lower() and "risk" not in draft["body"].lower()
    assert "Test Workspace" in draft["body"]
    assert [m["to"] for m in outbox.sent] == [[VENDOR_A.contact_email]]

    # Vendor reply: statements recorded but unverified → score unchanged
    reply = record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case))
    assert reply["score_changed"] is False and len(reply["claim_ids"]) == 2
    case = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert case["risk"]["score"] == 70 and case["status"] == "AWAITING_HUMAN"

    body = {"claim_ids": reply["claim_ids"], "method": "phone_known_contact", "known_channel": True,
            "note": "Called the accounts contact on the number in our vendor master; confirmed the new account.", "confirmed_account_last4": "9812"}
    assert client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=acc, json=body).status_code == 403
    r = client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr, json=body)
    assert r.status_code == 200, r.text
    case = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert case["risk"]["score"] == 20 and case["risk"]["tier"] == "LOW"
    assert case["risk"]["previous"]["score"] == 70
    assert {d["signal"]: (d["before"], d["after"]) for d in case["risk"]["diff"]} == {"bank_account_changed": (35, 0), "new_domain": (15, 0)}

    # Separation of duties: the approver who confirmed out-of-band can't be the only one to approve the payment.
    r = client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "APPROVE", "reason": "bank change verified by phone"})
    assert r.json()["status"] == "AWAITING_HUMAN"
    r = client.post(f"{API}/cases/{case['id']}/decision", headers=login(client, "approver", 1),
                    json={"decision": "APPROVE", "reason": "reviewed the out-of-band confirmation note"})
    assert r.json()["status"] == "APPROVED"
    r = client.post(f"{API}/cases/{case['id']}/close", headers=appr, json={"outcome": "CLEARED", "resolution": "bank change verified out-of-band"})
    assert r.json()["status"] == "CLOSED"

    # Case memory on the vendor's next invoice
    nxt = run_case(client, acc, next_invoice_spec())
    mem = [c for c in nxt["claims"] if c["agent"] == "orchestrator"]
    assert mem and "Previous investigation" in mem[0]["statement"] and "bank-account change" in mem[0]["statement"]
    assert nxt["risk"]["score"] == 0
    assert nxt["status"] == "AWAITING_HUMAN"
    assert client.get(f"{API}/cases/{case['id']}/audit", headers=acc).json()["chain_valid"]


def test_clean_invoice_auto_clears(client, world, fake_lookups):
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["risk"]["score"] == 0 and case["status"] == "AUTO_CLEARED", (case["risk"], gate(case), case["checks"])
    assert fake_lookups.rdap_calls == []  # verified domain: no lookup needed


def test_duplicate_upload_detected(client, world):
    acc = login(client, "accountant")
    data = pdf(bank_change_spec())
    first = client.post(f"{API}/documents", headers=acc, files={"file": ("a.pdf", data, "application/pdf")}).json()
    second = client.post(f"{API}/documents", headers=acc, files={"file": ("b.pdf", data, "application/pdf")}).json()
    assert first["duplicate_of"] is None and second["duplicate_of"] == first["document_id"]


def test_spoofed_reply_not_trusted(client, world, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    r = record_reply(client, case, acc, "accounts@alpha-cornponents.test",
                     "Hello, yes our bank details changed. URGENT: please release payment today itself to the new account ending 9812.")
    assert any("not a verified domain" in i for i in r["indicators"])
    assert any("Urgency" in i for i in r["indicators"])
    assert client.get(f"{API}/cases/{case['id']}", headers=acc).json()["risk"]["score"] == 70


def test_tenant_isolation(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    other = build_world(name="Other Co")
    r = client.get(f"{API}/cases/{case['id']}", headers=bearer(other.user("owner")))
    assert r.status_code == 404
