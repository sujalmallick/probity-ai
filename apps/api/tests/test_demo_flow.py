"""End-to-end: the one-case demo (PRD.md §7) through the HTTP API, offline (cached tools, mock LLM)."""

from conftest import login

from probity.demo.seed import DEMO_DIR

API = "/api/v1"


def upload_and_run(client, h, name):
    r = client.post(f"{API}/documents", headers=h, files={"file": (name, (DEMO_DIR / name).read_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text
    doc = r.json()
    r = client.post(f"{API}/cases", headers=h, json={"document_id": doc["document_id"]})
    assert r.status_code == 201, r.text
    return client.get(f"{API}/cases/{r.json()['case_id']}", headers=h).json()


def contributions(case):
    return {c["signal"]: c["points"] for c in case["risk"]["contributions"] if c["points"]}


def test_one_case_demo(client):
    acc, appr = login(client, "accountant"), login(client, "approver")

    # 1–3. Upload → live investigation → 70 HIGH with 3 verified anomalies
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    assert case["status"] == "AWAITING_HUMAN", case["status"]
    assert case["amount"]["amount_minor"] == 56640000
    assert case["risk"]["score"] == 70 and case["risk"]["tier"] == "HIGH"
    assert contributions(case) == {"bank_account_changed": 35, "price_anomaly": 20, "new_domain": 15}
    bank = next(c for c in case["risk"]["contributions"] if c["signal"] == "bank_account_changed")
    assert bank["observed"] == "XXXX9812" and bank["baseline"] == "XXXX1234"
    price = next(c for c in case["claims"] if c["signal"] == "price_anomaly")
    assert price["data"]["pct_change"] == 62.7 and price["status"] == "verified"
    assert "3 verified anomalies" in case["summary"]
    assert case["recommendation"]["action"] == "HOLD_PAYMENT"
    assert case["validation"]["gstin_checksum"]["ok"] and case["validation"]["arithmetic"]["ok"]

    # 4. Explainability: every counted point traces to a verified claim with evidence
    ex = client.get(f"{API}/cases/{case['id']}/explain", headers=acc).json()
    for step in ex["steps"]:
        if step["points"]:
            assert step["status"] == "counted" and step["evidence"], step

    # Accountants cannot decide
    r = client.post(f"{API}/cases/{case['id']}/decision", headers=acc, json={"decision": "APPROVE", "reason": "x"})
    assert r.status_code == 403

    # 5–6. Human gate → request verification → neutral draft to verified master contact → send
    r = client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify bank change"})
    assert r.status_code == 200, r.text
    draft = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    assert draft["to_email"] == "accounts@abcsupplies.in" and draft["recipient_verified"]
    assert "fraud" not in draft["body"].lower() and "risk" not in draft["body"].lower()
    r = client.post(f"{API}/cases/{case['id']}/drafts/{draft['id']}/send", headers=appr, json={})
    assert r.status_code == 200, r.text

    # 7. Vendor reply → claims recorded but UNVERIFIED → score unchanged
    r = client.post(f"{API}/demo/vendor-reply/{case['id']}?kind=legit", headers=acc)
    assert r.status_code == 200, r.text
    reply = r.json()
    assert reply["score_changed"] is False and len(reply["claim_ids"]) == 2
    case = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert case["risk"]["score"] == 70 and case["status"] == "AWAITING_HUMAN"

    # Out-of-band confirmation is approver-only
    body = {"claim_ids": reply["claim_ids"], "method": "phone_known_contact", "note": "Called R. Kulkarni on +91 20 4000 1000 (number on file)"}
    assert client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=acc, json=body).status_code == 403
    r = client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr, json=body)
    assert r.status_code == 200, r.text
    case = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert case["risk"]["score"] == 20 and case["risk"]["tier"] == "LOW"
    assert case["risk"]["previous"]["score"] == 70
    assert {d["signal"]: (d["before"], d["after"]) for d in case["risk"]["diff"]} == {"bank_account_changed": (35, 0), "new_domain": (15, 0)}
    assert contributions(case) == {"price_anomaly": 20}

    # 8. Approve → close CLEARED → memory referenced on next invoice
    r = client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "APPROVE", "reason": "bank change verified by phone; rate card revision accepted"})
    assert r.json()["status"] == "APPROVED"
    r = client.post(f"{API}/cases/{case['id']}/close", headers=appr, json={"outcome": "CLEARED", "resolution": "bank change verified out-of-band"})
    assert r.json()["status"] == "CLOSED"

    nxt = upload_and_run(client, acc, "invoice_4822.pdf")
    mem = [c for c in nxt["claims"] if c["agent"] == "orchestrator"]
    assert mem and "Previous investigation" in mem[0]["statement"] and "bank-account change" in mem[0]["statement"]
    assert nxt["risk"]["score"] == 0  # bank + domain now verified; memory note carries no points (CLEARED)
    assert nxt["status"] == "AWAITING_HUMAN"  # previously flagged vendor → conservative human review
    audit = client.get(f"{API}/cases/{case['id']}/audit", headers=acc).json()
    assert audit["chain_valid"]


def test_clean_invoice_auto_clears(client):
    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "invoice_kaveri_clean.pdf")
    assert case["risk"]["score"] == 0 and case["status"] == "AUTO_CLEARED", (case["risk"], case["recommendation"].get("gate"))


def test_duplicate_upload_detected(client):
    acc = login(client, "accountant")
    data = (DEMO_DIR / "invoice_4821.pdf").read_bytes()
    first = client.post(f"{API}/documents", headers=acc, files={"file": ("a.pdf", data, "application/pdf")}).json()
    second = client.post(f"{API}/documents", headers=acc, files={"file": ("b.pdf", data, "application/pdf")}).json()
    assert first["duplicate_of"] is None and second["duplicate_of"] == first["document_id"]


def test_spoofed_reply_not_trusted(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    d = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    client.post(f"{API}/cases/{case['id']}/drafts/{d['id']}/send", headers=appr, json={})
    r = client.post(f"{API}/demo/vendor-reply/{case['id']}?kind=spoof", headers=acc).json()
    assert any("not a verified domain" in i for i in r["indicators"])
    assert any("Urgency" in i for i in r["indicators"])
    case = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert case["risk"]["score"] == 70


def test_tenant_isolation(client):
    from probity.db.session import session_scope
    from probity.demo import seed

    acc = login(client, "accountant")
    case = upload_and_run(client, acc, "invoice_kaveri_clean.pdf")
    with session_scope() as s:
        other = seed.seed_workspace(s, name="Other Co")
        from probity.db.models import User
        from sqlalchemy import select

        uid = s.scalars(select(User).where(User.workspace_id == other.id, User.role == "owner")).first().id
    tok = client.post(f"{API}/auth/demo-login", json={"user_id": uid}).json()["token"]
    r = client.get(f"{API}/cases/{case['id']}", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404
