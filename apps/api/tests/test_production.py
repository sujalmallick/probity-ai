"""Production integrations: Clerk auth + provisioning, team management, inbound email webhook,
upload safety (active content, antivirus), mailer, storage."""

import hashlib
import hmac
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from conftest import login
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from probity import auth, mailer, storage
from probity.config import get_settings
from probity.db.models import Invitation, User
from probity.db.session import session_scope
from probity.ingestion.parse import UnsupportedDocument
from probity.ingestion.scan import InfectedFile, clamav_scan, reject_active_content

ISSUER = "https://clerk.probity.test"


# ---------------------------------------------------------------- Clerk


@pytest.fixture()
def clerk(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class _JWKS:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()

    st = get_settings()
    monkeypatch.setattr(st, "auth_mode", "clerk")
    monkeypatch.setattr(st, "clerk_issuer", ISSUER)
    monkeypatch.setattr(st, "clerk_authorized_parties", "https://app.probity.test")
    monkeypatch.setattr(auth, "_jwks_client", lambda: _JWKS())

    def make(sub, email=None, name=None, iss=ISSUER, exp_min=5, azp="https://app.probity.test", fva=(0, 0)):
        now = datetime.now(timezone.utc)
        claims = {"sub": sub, "iss": iss, "iat": now, "nbf": now, "exp": now + timedelta(minutes=exp_min), "azp": azp, "fva": list(fva)}
        if email:
            claims["email"], claims["name"] = email, name or email
        return jwt.encode(claims, key, algorithm="RS256")

    return make


def test_clerk_first_signin_creates_workspace_owner(client, clerk):
    tok = clerk("user_new1", "founder@acme.test", "Founder")
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["role"] == "owner" and me["workspace"]["name"] == "Founder's workspace"
    # second request maps to the same user (no duplicate provisioning)
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()["id"] == me["id"]


def test_clerk_invitation_joins_workspace_with_role(client, fresh_db, request):
    owner = login(client, "owner")
    r = client.post("/api/v1/workspace/invitations", headers=owner, json={"email": "Clerk.Approver@Acme.test", "role": "approver"})
    assert r.status_code == 201, r.text
    assert client.post("/api/v1/workspace/invitations", headers=login(client, "accountant"), json={"email": "x@y.test", "role": "owner"}).status_code == 403
    clerk = request.getfixturevalue("clerk")  # switch the API to Clerk auth
    tok = clerk("user_inv1", "clerk.approver@acme.test", "Invited Approver")
    me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert me["role"] == "approver" and me["workspace"]["id"] == fresh_db
    with session_scope() as s:
        assert s.scalars(select(Invitation).where(Invitation.email == "clerk.approver@acme.test")).first().accepted_at is not None


@pytest.mark.parametrize("kw,reason", [
    ({"iss": "https://evil.example"}, "issuer"),
    ({"exp_min": -5}, "expired"),
    ({"azp": "https://evil.example"}, "unauthorized origin"),
])
def test_clerk_rejects_bad_tokens(client, clerk, kw, reason):
    tok = clerk("user_bad", "bad@acme.test", **kw)
    r = client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401, (reason, r.text)


def test_no_token_in_url(client):
    tok = client.post("/api/v1/auth/demo-login", json={"user_id": _uid("viewer")}).json()["token"]
    assert client.get(f"/api/v1/me?token={tok}").status_code == 401  # tokens are accepted from the header only


def test_mfa_required_for_approvals_when_policy_on(client, fresh_db, request):
    owner = login(client, "owner")
    assert client.put("/api/v1/workspace/policy", headers=owner, json={"require_mfa_for_approvals": True}).status_code == 200
    clerk = request.getfixturevalue("clerk")
    with session_scope() as s:
        u = s.scalars(select(User).where(User.workspace_id == fresh_db, User.role == "approver")).first()
        u.external_id = "user_appr_mfa"
    no_mfa = clerk("user_appr_mfa", fva=(0, -1))
    r = client.post("/api/v1/cases/case_x/decision", headers={"Authorization": f"Bearer {no_mfa}"}, json={"decision": "APPROVE", "reason": "x"})
    assert r.status_code == 403 and "multi-factor" in r.json()["error"]["message"]
    with_mfa = clerk("user_appr_mfa", fva=(0, 2))
    r = client.post("/api/v1/cases/case_x/decision", headers={"Authorization": f"Bearer {with_mfa}"}, json={"decision": "APPROVE", "reason": "x"})
    assert r.status_code == 404  # passed the MFA gate; the case simply doesn't exist


def _uid(role):
    with session_scope() as s:
        return s.scalars(select(User).where(User.role == role).order_by(User.email)).first().id


# ---------------------------------------------------------------- team management


def test_last_owner_cannot_be_demoted(client, fresh_db):
    owner = login(client, "owner")
    members = client.get("/api/v1/workspace/members", headers=owner).json()["items"]
    me = next(m for m in members if m["role"] == "owner")
    r = client.patch(f"/api/v1/workspace/members/{me['id']}", headers=owner, json={"role": "viewer"})
    assert r.status_code == 409
    other = next(m for m in members if m["role"] == "viewer")
    r = client.patch(f"/api/v1/workspace/members/{other['id']}", headers=owner, json={"active": False})
    assert r.json()["active"] is False
    viewer_tok = client.post("/api/v1/auth/demo-login", json={"user_id": other["id"]}).json()["token"]
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {viewer_tok}"}).status_code == 401  # deactivated


# ---------------------------------------------------------------- inbound email


def _signed(client, payload, secret="s3cret", ts=None):
    raw = json.dumps(payload).encode()
    ts = str(int(ts if ts is not None else time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    return client.post("/api/v1/webhooks/inbound-email", content=raw, headers={"X-Probity-Timestamp": ts, "X-Probity-Signature": sig, "Content-Type": "application/json"})


def test_inbound_email_routes_reply_to_case(client, monkeypatch):
    from test_demo_flow import upload_and_run

    monkeypatch.setattr(get_settings(), "inbound_email_secret", "s3cret")
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    client.post(f"/api/v1/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    d = client.get(f"/api/v1/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    client.post(f"/api/v1/cases/{case['id']}/drafts/{d['id']}/send", headers=appr, json={})
    payload = {"from": "accounts@abcsupplies.in", "to": [f"case+{case['id']}@reply.probity.test"], "subject": "RE: verification",
               "text": "We moved our banking to a new HDFC Bank account ending 9812 last month.", "dkim": "fail"}
    assert _signed(client, payload, secret="wrong").status_code == 401
    assert _signed(client, payload, ts=time.time() - 3600).status_code == 401  # replay window
    r = _signed(client, payload)
    assert r.status_code == 200, r.text
    assert len(r.json()["claim_ids"]) == 1 and "Sender failed DKIM verification" in r.json()["indicators"]
    after = client.get(f"/api/v1/cases/{case['id']}", headers=acc).json()
    assert after["status"] == "AWAITING_HUMAN" and after["risk"]["score"] == 70  # reply alone never lowers the score


# ---------------------------------------------------------------- upload safety


def test_active_pdf_content_rejected():
    with pytest.raises(UnsupportedDocument):
        reject_active_content(b"%PDF-1.7\n1 0 obj << /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>", "application/pdf")
    reject_active_content(b"%PDF-1.7\n1 0 obj << /Type /Catalog >>", "application/pdf")


def test_active_pdf_upload_returns_415(client):
    r = client.post("/api/v1/documents", headers=login(client, "accountant"),
                    files={"file": ("x.pdf", b"%PDF-1.7\n<< /Names << /EmbeddedFile 3 0 R >> >>", "application/pdf")})
    assert r.status_code == 415


def _fake_clamd(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
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


def test_clamav_clean_and_infected(monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "clamav_host", "127.0.0.1")
    monkeypatch.setattr(st, "clamav_port", _fake_clamd(b"stream: OK\0"))
    assert clamav_scan(b"hello") == "OK"
    monkeypatch.setattr(st, "clamav_port", _fake_clamd(b"stream: Eicar-Test-Signature FOUND\0"))
    with pytest.raises(InfectedFile, match="Eicar"):
        clamav_scan(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR")


# ---------------------------------------------------------------- mailer & storage


def test_resend_backend_payload(monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "email_backend", "resend")
    monkeypatch.setattr(st, "resend_api_key", "re_test_123456789")
    monkeypatch.setattr(st, "email_reply_domain", "reply.probity.test")
    sent = {}

    class R:
        status_code = 200

        def json(self):
            return {"id": "msg_1"}

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.update(url=url, json=json, headers=headers)
        return R()

    monkeypatch.setattr(mailer.httpx, "post", fake_post)
    assert mailer.send_case_email("case_abc", "v@x.test", "Subject", "Body") == "msg_1"
    assert sent["json"]["reply_to"] == "case+case_abc@reply.probity.test" and sent["headers"]["Authorization"] == "Bearer re_test_123456789"


def test_local_storage_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_dir", tmp_path)
    ref = storage.put("ws_1", "abc", b"data", "application/pdf")
    assert ref.startswith("local:") and storage.get(ref) == b"data"


# ---------------------------------------------------------------- operations


def test_ready_and_metrics(client, monkeypatch):
    r = client.get("/api/v1/ready")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["database"].startswith("ok")
    from test_demo_flow import upload_and_run

    upload_and_run(client, login(client, "accountant"), "invoice_kaveri_clean.pdf")
    body = client.get("/api/v1/metrics").text
    assert "probity_http_requests_total" in body and 'probity_cases{status="AUTO_CLEARED",tier="LOW"}' in body
    monkeypatch.setattr(get_settings(), "metrics_token", "m-token")
    assert client.get("/api/v1/metrics").status_code == 401
    assert client.get("/api/v1/metrics", headers={"Authorization": "Bearer m-token"}).status_code == 200


def test_log_scrubbing():
    from probity.logging import scrub

    out = scrub({"msg": "acct 50100098129812 PAN AABCA1234F mail a@b.in Bearer abc.def", "token": "x", "nested": ["sk-ant-api03-abcdefghijkl"]})
    assert "50100098129812" not in str(out) and "AABCA1234F" not in str(out) and "a@b.in" not in str(out)
    assert out["token"] == "<redacted>" and "sk-ant" not in str(out)


# ---------------------------------------------------------------- relationship graph


def test_shared_bank_account_across_vendors_is_flagged(client, tmp_path):
    from datetime import date, timedelta

    from probity.demo import seed
    from probity.demo.invoice_pdf import InvoiceSpec, render
    from test_demo_flow import API

    v = seed.VENDORS[1]  # Kaveri Packaging … but paid into ABC Supplies' bank account
    today = date.today()
    spec = InvoiceSpec(vendor_name=v[0], vendor_address=v[2], gstin=v[1], email=v[4], phone="+91 80 4000 2000", invoice_number="KP-2026-999",
                       invoice_date=(today - timedelta(days=1)).isoformat(), due_date=(today + timedelta(days=20)).isoformat(), po_number="PO-7711",
                       items=[(v[8], 2000, 4200)], account_number="50200012341234", ifsc="HDFC0001234", bank_name="HDFC Bank")
    path = render(spec, tmp_path / "shared.pdf")
    acc = login(client, "accountant")
    doc = client.post(f"{API}/documents", headers=acc, files={"file": ("shared.pdf", path.read_bytes(), "application/pdf")}).json()
    cid = client.post(f"{API}/cases", headers=acc, json={"document_id": doc["document_id"]}).json()["case_id"]
    case = client.get(f"{API}/cases/{cid}", headers=acc).json()
    shared = [c for c in case["claims"] if c["signal"] == "shared_attribute"]
    assert shared and "ABC Supplies" in shared[0]["statement"] and shared[0]["status"] == "verified"
    assert case["status"] == "AWAITING_HUMAN"  # never auto-cleared, even though it adds 0 points
    graph = client.get(f"{API}/vendors/{case['vendor_id']}/graph", headers=acc).json()
    assert any(n["type"] == "vendor" and "ABC" in n["label"] for n in graph["nodes"])
    groups = client.get(f"{API}/graph/shared-attributes", headers=acc).json()["items"]
    assert any(g["type"] == "bank" and len(g["vendors"]) == 2 for g in groups)


def test_pdf_export_and_preview(client):
    from test_demo_flow import API, upload_and_run

    from probity.demo.seed import DEMO_DIR

    acc = login(client, "accountant")
    doc = client.post(f"{API}/documents", headers=acc, files={"file": ("p.pdf", (DEMO_DIR / "invoice_4821.pdf").read_bytes(), "application/pdf")}).json()
    pv = client.post(f"{API}/documents/{doc['document_id']}/preview", headers=acc).json()
    assert pv["fields"]["total"]["value"] == 56640000 and pv["fields"]["bank_account"]["value"] == "XXXX9812" and "hmac" not in pv["fields"]["bank_account"]
    assert pv["validation"]["gstin_checksum"]["ok"] and not pv["injection_detected"]
    case = upload_and_run(client, acc, "invoice_kaveri_clean.pdf")
    r = client.get(f"{API}/cases/{case['id']}/export?format=pdf", headers=acc)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    import io

    import pdfplumber

    text = " ".join((p.extract_text() or "").replace("\n", " ") for p in pdfplumber.open(io.BytesIO(r.content)).pages)
    assert "�" not in text  # no unrenderable glyphs
    assert "Kaveri Packaging" in text and "hash chain verified intact" in text and "never executes payments" in text
