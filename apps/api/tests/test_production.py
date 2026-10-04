"""Production integrations: MFA for approvals, team management, inbound email webhook, upload safety (active content,
antivirus), the Resend mailer and its allowlist, storage, readiness/metrics, the relationship graph and PDF export."""

import hashlib
import hmac
import io
import json
import socket
import threading
import time
from dataclasses import replace

import pytest
from conftest import bearer, login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, VENDOR_B, bank_change_spec, clean_spec, pdf
from helpers import API, run_case, send_verification
from sqlalchemy import select

from probity import mailer, storage
from probity.db.models import AuditLog, User
from probity.ingestion.parse import UnsupportedDocument
from probity.ingestion.scan import InfectedFile, clamav_scan, reject_active_content


def test_no_token_in_url(client, world):
    tok = login(client, "viewer")["Authorization"].split()[1]
    assert client.get(f"{API}/me?token={tok}").status_code == 401  # tokens are accepted from the header only


def test_mfa_required_for_approvals_when_policy_on(client, world):
    owner = login(client, "owner")
    assert client.put(f"{API}/workspace/policy", headers=owner, json={"require_mfa_for_approvals": True}).status_code == 200
    appr = world.user("approver")
    r = client.post(f"{API}/cases/case_x/decision", headers=bearer(appr, mfa=False), json={"decision": "APPROVE", "reason": "x"})
    assert r.status_code == 403 and "multi-factor" in r.json()["error"]["message"]
    r = client.post(f"{API}/cases/case_x/decision", headers=bearer(appr, mfa=True), json={"decision": "APPROVE", "reason": "x"})
    assert r.status_code == 404  # passed the MFA gate; the case simply doesn't exist


# ---------------------------------------------------------------- team management


def test_last_owner_cannot_be_demoted_and_deactivation_revokes_access(client, world):
    owner = login(client, "owner")
    members = client.get(f"{API}/workspace/members", headers=owner).json()["items"]
    me = next(m for m in members if m["role"] == "owner")
    assert client.patch(f"{API}/workspace/members/{me['id']}", headers=owner, json={"role": "viewer"}).status_code == 409
    viewer = world.user("viewer")
    assert client.get(f"{API}/me", headers=bearer(viewer)).status_code == 200
    r = client.patch(f"{API}/workspace/members/{viewer.id}", headers=owner, json={"active": False})
    assert r.json()["active"] is False
    assert client.get(f"{API}/me", headers=bearer(viewer)).status_code == 401  # a valid Clerk session no longer gets in


# ---------------------------------------------------------------- inbound email


def _signed(client, payload, secret="s3cret", ts=None):  # type: ignore[no-untyped-def]
    raw = json.dumps(payload).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return client.post(f"{API}/webhooks/inbound-email", content=raw, headers={"X-Probity-Timestamp": ts, "X-Probity-Signature": sig, "Content-Type": "application/json"})


def test_inbound_email_routes_reply_to_case(client, world, settings, fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21
    settings(INBOUND_EMAIL_SECRET="s3cret", EMAIL_REPLY_DOMAIN="reply.probity-tests.invalid", EMAIL_ALLOWLIST=VENDOR_A.contact_email)
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    # A display name that looks like the vendor's address must not be taken as the sender.
    payload = {"from": f'"{VENDOR_A.contact_email}" <billing@attacker.test>', "to": [f"case+{case['id']}@reply.probity-tests.invalid"],
               "subject": "RE: verification", "text": "We moved our banking to a new account ending 9812 last month.", "dkim": "fail"}
    assert _signed(client, payload, secret="wrong").status_code == 401
    assert _signed(client, payload, ts=time.time() - 3600).status_code == 401  # replay window
    r = _signed(client, payload)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["claim_ids"]) == 1 and "Sender failed DKIM verification" in out["indicators"]
    assert any("attacker.test" in i for i in out["indicators"])
    after = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert after["status"] == "AWAITING_HUMAN" and after["risk"]["score"] == 70  # a reply alone never lowers the score


# ---------------------------------------------------------------- upload safety


def test_active_pdf_content_rejected():
    with pytest.raises(UnsupportedDocument):
        reject_active_content(b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>", "application/pdf")
    reject_active_content(b"%PDF-1.7\n1 0 obj << /Type /Catalog >>", "application/pdf")


def test_active_pdf_upload_returns_415(client, world):
    r = client.post(f"{API}/documents", headers=login(client, "accountant"),
                    files={"file": ("x.pdf", b"%PDF-1.7\n<< /Names << /EmbeddedFile 3 0 R >> >>", "application/pdf")})
    assert r.status_code == 415


def _fake_clamd(reply: bytes) -> int:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve() -> None:
        conn, _ = srv.accept()
        buf = b""
        while not buf.endswith(b"\0\0\0\0"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        conn.sendall(reply)
        conn.close()
        srv.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv.getsockname()[1]


def test_clamav_clean_and_infected(settings):
    settings(CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=str(_fake_clamd(b"stream: OK\0")))
    assert clamav_scan(b"hello") == "OK"
    settings(CLAMAV_PORT=str(_fake_clamd(b"stream: Eicar-Test-Signature FOUND\0")))
    with pytest.raises(InfectedFile, match="Eicar"):
        clamav_scan(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR")


# ---------------------------------------------------------------- mailer (Resend) and its allowlist


def test_resend_payload_and_reply_to(settings, outbox):
    settings(EMAIL_ALLOWLIST="v@x.test", EMAIL_REPLY_DOMAIN="reply.probity-tests.invalid")
    assert mailer.send_case_email("case_abc", "v@x.test", "Subject", "Body") == "msg_1"
    assert outbox.sent[0]["reply_to"] == "case+case_abc@reply.probity-tests.invalid" and outbox.sent[0]["to"] == ["v@x.test"]


def test_recipient_outside_allowlist_is_blocked(settings, outbox):
    settings(EMAIL_ALLOWLIST="me@mine.test")
    with pytest.raises(mailer.MailBlocked):
        mailer.send_case_email("case_abc", "vendor@elsewhere.test", "S", "B")
    assert outbox.sent == []
    settings(EMAIL_SEND_TO_ANY="true")
    mailer.send_case_email("case_abc", "vendor@elsewhere.test", "S", "B")
    assert outbox.sent[0]["to"] == ["vendor@elsewhere.test"]


def test_email_not_configured_sends_nothing(settings, outbox):
    settings(RESEND_API_KEY="", EMAIL_ALLOWLIST="me@mine.test")
    with pytest.raises(mailer.MailNotConfigured, match="isn't configured"):
        mailer.send_case_email("case_abc", "me@mine.test", "S", "B")
    assert outbox.sent == []


def test_blocked_verification_email_is_refused_and_audited(client, world, settings, fake_lookups, outbox):
    fake_lookups.domains[NEW_DOMAIN] = 21
    settings(EMAIL_ALLOWLIST="me@mine.test")  # the vendor contact is not on it
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    draft = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    r = client.post(f"{API}/cases/{case['id']}/drafts/{draft['id']}/send", headers=appr, json={})
    assert r.status_code == 400 and "EMAIL_ALLOWLIST" in r.json()["error"]["message"]
    assert outbox.sent == []
    assert client.get(f"{API}/cases/{case['id']}", headers=acc).json()["status"] == "AWAITING_HUMAN"
    with owner_session() as s:
        row = s.scalars(select(AuditLog).where(AuditLog.action == "email.blocked")).first()
        assert row is not None and VENDOR_A.contact_email not in json.dumps(row.data)  # masked


def test_local_storage_roundtrip(tmp_path, settings):
    settings(STORAGE_DIR=str(tmp_path))
    ref = storage.put("ws_1", "abc", b"data", "application/pdf")
    assert ref.startswith("local:") and storage.get(ref) == b"data"


# ---------------------------------------------------------------- operations


def test_ready_and_metrics(client, world, settings):
    r = client.get(f"{API}/ready")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["database"].startswith("ok"), r.json()
    assert r.json()["integrations"]["gst_registry"] == "unavailable"
    run_case(client, login(client, "accountant"), clean_spec())
    body = client.get(f"{API}/metrics").text
    assert "probity_http_requests_total" in body and 'probity_cases{status="AUTO_CLEARED",tier="LOW"}' in body
    settings(METRICS_TOKEN="m-token")
    assert client.get(f"{API}/metrics").status_code == 401
    assert client.get(f"{API}/metrics", headers={"Authorization": "Bearer m-token"}).status_code == 200


def test_log_scrubbing():
    from probity.logging import scrub

    out = scrub({"msg": "acct 50100098129812 PAN AABCA1234F mail a@b.in Bearer abc.def", "token": "x", "nested": ["sk-ant-api03-abcdefghijkl"]})
    assert "50100098129812" not in str(out) and "AABCA1234F" not in str(out) and "a@b.in" not in str(out)
    assert out["token"] == "<redacted>" and "sk-ant" not in str(out)


# ---------------------------------------------------------------- relationship graph, preview, export


def test_shared_bank_account_across_vendors_is_flagged(client, world):
    spec = replace(clean_spec(), invoice_number="BP-2026-999", account_number=VENDOR_A.account, ifsc=VENDOR_A.ifsc)  # vendor B paid into A's account
    acc = login(client, "accountant")
    case = run_case(client, acc, spec)
    shared = [c for c in case["claims"] if c["signal"] == "shared_attribute"]
    assert shared and VENDOR_A.name in shared[0]["statement"] and shared[0]["status"] == "verified"
    assert case["status"] == "AWAITING_HUMAN"  # never auto-cleared, even though it adds 0 points
    graph = client.get(f"{API}/vendors/{case['vendor_id']}/graph", headers=acc).json()
    assert any(n["type"] == "vendor" and VENDOR_A.name.split()[0] in n["label"] for n in graph["nodes"])
    groups = client.get(f"{API}/graph/shared-attributes", headers=acc).json()["items"]
    assert any(g["type"] == "bank" and len(g["vendors"]) == 2 for g in groups)


def test_pdf_preview_and_export(client, world):
    acc = login(client, "accountant")
    doc = client.post(f"{API}/documents", headers=acc, files={"file": ("p.pdf", pdf(bank_change_spec()), "application/pdf")}).json()
    pv = client.post(f"{API}/documents/{doc['document_id']}/preview", headers=acc).json()
    assert pv["fields"]["total"]["value"] == 56640000 and pv["fields"]["bank_account"]["value"] == "XXXX9812" and "hmac" not in pv["fields"]["bank_account"]
    assert pv["validation"]["gstin_checksum"]["ok"] and not pv["injection_detected"]
    case = run_case(client, acc, clean_spec())
    r = client.get(f"{API}/cases/{case['id']}/export?format=pdf", headers=acc)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    import pdfplumber

    text = " ".join((p.extract_text() or "").replace("\n", " ") for p in pdfplumber.open(io.BytesIO(r.content)).pages)
    assert "�" not in text  # no unrenderable glyphs
    assert VENDOR_B.name in text and "hash chain verified intact" in text and "never executes payments" in text


def test_scanned_image_is_refused_with_a_clear_message(client, world):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    acc = login(client, "accountant")
    r = client.post(f"{API}/documents", headers=acc, files={"file": ("scan.png", buf.getvalue(), "image/png")})
    if r.status_code == 201:  # stored; reading it must fail clearly, never with guessed fields
        r = client.post(f"{API}/documents/{r.json()['document_id']}/preview", headers=acc)
    assert r.status_code in (400, 415, 422), r.text
    assert "scanned" in r.json()["error"]["message"].lower()


def test_users_never_cross_workspaces(client, world):
    with owner_session() as s:
        assert {u.workspace_id for u in s.scalars(select(User))} == {world.workspace_id}
