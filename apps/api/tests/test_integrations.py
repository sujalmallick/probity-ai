"""Phase 4: `python -m probity.check` reports each integration honestly (ok / missing / failed / unavailable / skipped,
never a secret), and a FAILED case can be retried as a fresh, linked case."""

import socket
import threading

import pytest
from conftest import login, owner_session
from factories import clean_spec
from helpers import API, run_case

from probity import check
from probity.db.models import Case
from probity.tools import lookups


def _status(name, **kw):  # type: ignore[no-untyped-def]
    (r,) = check.run([name], **kw)
    return r


def test_database_check_runs_as_non_superuser_with_rls(db):
    r = _status("database")
    assert r.status == "ok" and "not superuser" in r.detail and "row-level security enforced" in r.detail


def test_ai_check(settings, llm):
    settings(ANTHROPIC_API_KEY="")
    assert _status("ai").status == "missing"
    settings(ANTHROPIC_API_KEY="sk-ant-test")
    r = _status("ai")
    assert r.status == "failed" and "AI is not available" in r.detail  # the fake transport fails like an unavailable API
    llm.answer("_Ping", {"ok": True, "word": "ready"})
    r = _status("ai")
    assert r.status == "ok" and llm.calls.count("_Ping") >= 3


class _R:
    def __init__(self, code, body):  # type: ignore[no-untyped-def]
        self.status_code, self._b = code, body

    def json(self):  # type: ignore[no-untyped-def]
        return self._b


def test_sign_in_check(settings, monkeypatch):
    settings(CLERK_ISSUER="", CLERK_SECRET_KEY="")
    assert _status("sign_in").status == "missing"
    settings(CLERK_ISSUER="https://clerk.example.test", CLERK_SECRET_KEY="sk_test_x", PUBLIC_APP_URL="http://testserver")
    answers = {"jwks": _R(200, {"keys": [{"kid": "k"}]}), "users": _R(200, [])}
    monkeypatch.setattr(check.httpx, "get", lambda url, **kw: answers["jwks" if "jwks" in url else "users"])
    r = _status("sign_in")
    assert r.status == "ok" and "secret key accepted" in r.detail and "sk_test_x" not in r.detail
    answers["users"] = _R(401, {})
    assert _status("sign_in").detail == "Clerk rejected CLERK_SECRET_KEY"
    answers["jwks"] = _R(404, {})
    assert _status("sign_in").status == "failed"


def test_web_search_check(settings, fake_lookups):
    settings(TAVILY_API_KEY="")
    r = _status("web_search")
    assert r.status == "missing" and "SSRF guard OK" in r.detail
    settings(TAVILY_API_KEY="tvly-test")
    fake_lookups.search_status = "ok"
    fake_lookups.search["bank"] = [lookups.SearchHit("https://www.rbi.org.in/", "RBI", "Reserve Bank of India", 1)]
    fake_lookups.pages["https://www.rbi.org.in/"] = "Reserve Bank of India home"
    r = _status("web_search")
    assert r.status == "ok" and "1 result(s)" in r.detail and "read https://www.rbi.org.in/" in r.detail
    fake_lookups.search_status = "error"
    assert _status("web_search").status == "failed"


def test_domain_lookup_check(fake_lookups):
    fake_lookups.domains["vendor.test"] = 400
    assert _status("domain_lookup", domain="vendor.test").status == "ok"
    fake_lookups.domains["gone.test"] = "not_found"
    r = _status("domain_lookup", domain="gone.test")
    assert r.status == "failed" and "no record" in r.detail


def test_gst_is_reported_unavailable_not_ok():
    r = _status("gst_registry")
    assert r.status == "unavailable" and "could not verify" in r.detail


def test_email_check(settings, outbox):
    settings(RESEND_API_KEY="")
    assert _status("email").status == "missing"
    settings(RESEND_API_KEY="re_test", EMAIL_ALLOWLIST="")
    assert _status("email").status == "failed"  # an empty allowlist would block everything
    settings(EMAIL_ALLOWLIST="me@mine.test, second@mine.test")
    r = _status("email")
    assert r.status == "ok" and "allowlist only (2" in r.detail and outbox.sent == []
    r = _status("email", send_test_email=True)
    assert r.status == "ok" and [m["to"] for m in outbox.sent] == [["me@mine.test"]]  # only the first allowlisted address


def test_storage_check(tmp_path, settings):
    settings(STORAGE_DIR=str(tmp_path))
    r = _status("storage")
    assert r.status == "ok" and "round trip OK" in r.detail
    assert not any(p.is_file() for p in tmp_path.rglob("*"))  # the test object is removed


def _clamd(replies):  # type: ignore[no-untyped-def]
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(len(replies))

    def serve():  # type: ignore[no-untyped-def]
        for reply in replies:
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


def test_antivirus_check(settings):
    settings(CLAMAV_HOST="")
    assert _status("antivirus").status == "skipped"
    settings(CLAMAV_HOST="127.0.0.1", CLAMAV_PORT=str(_clamd([b"stream: OK\0", b"stream: Eicar-Test-Signature FOUND\0"])))
    assert _status("antivirus").status == "ok"
    settings(CLAMAV_PORT=str(_clamd([b"stream: OK\0", b"stream: OK\0"])))
    r = _status("antivirus")
    assert r.status == "failed" and "EICAR" in r.detail


def test_main_exit_code_and_no_secrets(settings, capsys, fake_lookups):
    settings(ANTHROPIC_API_KEY="", TAVILY_API_KEY="")
    fake_lookups.domains["rbi.org.in"] = 8000
    assert check.main(["gst_registry", "domain_lookup", "ai"]) == 0
    fake_lookups.domains["rbi.org.in"] = "not_found"
    assert check.main(["domain_lookup"]) == 1
    out = capsys.readouterr().out
    from probity.config import get_settings

    st = get_settings()
    for secret in (st.field_key_b64, st.hmac_key, st.clerk_secret_key):
        assert secret and secret not in out
    with pytest.raises(SystemExit):
        check.main(["no_such_check"])


# ---------------------------------------------------------------- retry a FAILED case


def test_failed_case_is_retried_as_a_new_linked_case(client, world):
    acc = login(client, "accountant")
    case = run_case(client, acc, clean_spec())
    assert client.post(f"{API}/cases/{case['id']}/retry", headers=acc).status_code == 409  # not FAILED
    with owner_session() as s:
        s.get(Case, case["id"]).status = "FAILED"
    assert client.post(f"{API}/cases/{case['id']}/retry", headers=login(client, "viewer")).status_code == 403
    r = client.post(f"{API}/cases/{case['id']}/retry", headers=acc)
    assert r.status_code == 201, r.text
    new_id = r.json()["case_id"]
    new = client.get(f"{API}/cases/{new_id}", headers=acc).json()
    old = client.get(f"{API}/cases/{case['id']}", headers=acc).json()
    assert new["status"] == "AUTO_CLEARED" and new["recommendation"]["retry_of"] == case["id"]
    assert old["status"] == "FAILED" and old["recommendation"]["retried_as"] == new_id
    assert client.post(f"{API}/cases/{case['id']}/retry", headers=acc).status_code == 409  # only once


def test_crud_check_round_trips_and_leaves_nothing(db):
    from probity.db.models import Vendor, Workspace
    from sqlalchemy import func, select

    r = _status("crud")
    assert r.status == "ok", r.detail
    with owner_session() as s:
        assert s.scalar(select(func.count()).select_from(Workspace)) == 0
        assert s.scalar(select(func.count()).select_from(Vendor)) == 0
