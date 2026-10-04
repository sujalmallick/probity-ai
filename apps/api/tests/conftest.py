"""Test harness. Test data lives only here and in tests/factories; app code never imports it.

- A throwaway PostgreSQL database is created for the session (from TEST_POSTGRES_ADMIN_URL), migrated with Alembic, and
  wiped between tests. The app connects as the non-owner `probity_app` role, so row-level security is enforced.
- apps/api/.env is never read: every setting comes from the environment set below.
- Outside services are replaced by fakes injected here (the app has no switch for this):
    AI           llm.client.transport raises LLMFailed unless a test queues answers (`llm` fixture)
    RDAP / web   tools.lookups functions answer from the `fake_lookups` fixture; nothing reaches the network
    email        mailer's HTTP client records messages in `outbox`
    Clerk        session tokens are RS256 JWTs signed by a key generated here; JWKS and the profile lookup are patched
"""

from __future__ import annotations

import base64
import os
import secrets
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ADMIN_URL = os.environ.get("TEST_POSTGRES_ADMIN_URL", "postgresql+psycopg://probity:probity@127.0.0.1:5434/postgres")
_DB = f"probity_test_{uuid.uuid4().hex[:10]}"
_tmp = Path(tempfile.mkdtemp(prefix="probity-test-"))
ISSUER = "https://clerk.probity-tests.invalid"
ORIGIN = "http://testserver"
_base = ADMIN_URL.rpartition("/")[0]
_host = _base.split("@", 1)[1]

os.environ.update({
    "ENV": "test",
    "DATABASE_URL": f"postgresql+psycopg://probity_app:probity_app@{_host}/{_DB}",
    "DATABASE_MIGRATE_URL": f"{_base}/{_DB}",
    "ANTHROPIC_API_KEY": "sk-ant-test-not-used",  # transport is replaced below; a real call is impossible
    "CLERK_ISSUER": ISSUER,
    "CLERK_SECRET_KEY": "sk_test_not_used",
    "CLERK_AUTHORIZED_PARTIES": ORIGIN,
    "FIELD_KEY_B64": base64.b64encode(secrets.token_bytes(32)).decode(),
    "HMAC_KEY": secrets.token_urlsafe(48),
    "TAVILY_API_KEY": "",
    "RESEND_API_KEY": "re_test_not_used",
    "EMAIL_FROM": "Accounts Payable <ap@probity-tests.invalid>",
    "EMAIL_ALLOWLIST": "",
    "EMAIL_SEND_TO_ANY": "false",
    "EMAIL_REPLY_DOMAIN": "",
    "INBOUND_EMAIL_SECRET": "",
    "TASK_BACKEND": "inline",
    "REDIS_URL": "",
    "STORAGE_BACKEND": "local",
    "STORAGE_DIR": str(_tmp / "uploads"),
    "CLAMAV_HOST": "",
    "SENTRY_DSN": "",
    "METRICS_TOKEN": "",
    "CORS_ORIGINS": ORIGIN,
})

from probity import config  # noqa: E402

config.Settings.model_config["env_file"] = None  # never the developer's apps/api/.env
config.get_settings.cache_clear()

import jwt  # noqa: E402
import pytest  # noqa: E402
from hypothesis import HealthCheck  # noqa: E402
from hypothesis import settings as hypothesis_settings  # noqa: E402

# Property tests check behaviour, not speed: a busy machine must not fail them.
hypothesis_settings.register_profile("probity", suppress_health_check=[HealthCheck.too_slow], deadline=None)
hypothesis_settings.load_profile("probity")
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from probity import auth, mailer, services  # noqa: E402
from probity.db.models import User, Workspace  # noqa: E402
from probity.llm import client as llm_client  # noqa: E402
from probity.tools import lookups  # noqa: E402

# ---------------------------------------------------------------- database

_owner_engine = None


@contextmanager
def owner_session():
    """Superuser session for test setup and assertions across workspaces (bypasses row-level security).
    The app itself never uses this connection."""
    global _owner_engine
    if _owner_engine is None:
        _owner_engine = create_engine(os.environ["DATABASE_MIGRATE_URL"])
    s = Session(_owner_engine, expire_on_commit=False)
    try:
        yield s
        s.commit()
    finally:
        s.close()


@pytest.fixture(scope="session", autouse=True)
def _database():
    eng = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    try:
        with eng.connect() as c:
            if not c.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'probity_app'")).scalar():
                c.execute(text("CREATE ROLE probity_app LOGIN PASSWORD 'probity_app' NOSUPERUSER NOBYPASSRLS"))
            c.execute(text(f'CREATE DATABASE "{_DB}"'))
    except Exception as e:  # noqa: BLE001
        pytest.exit(f"Tests need PostgreSQL. Start it (docker compose -f infra/docker-compose.dev.yml up -d) or set "
                    f"TEST_POSTGRES_ADMIN_URL. ({type(e).__name__}: {e})", returncode=3)
    from probity.db.migrate import upgrade

    upgrade()
    services.run_sync(True)
    yield
    from probity.db.session import get_engine

    get_engine().dispose()
    if _owner_engine is not None:
        _owner_engine.dispose()
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{_DB}" WITH (FORCE)'))
    eng.dispose()


def _wipe() -> None:
    with owner_session() as s:
        tables = [r[0] for r in s.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"))]
        s.execute(text("SET LOCAL session_replication_role = replica"))  # the audit log refuses TRUNCATE by trigger
        s.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))


@pytest.fixture()
def db():
    """An empty database (schema only)."""
    import shutil

    _wipe()
    shutil.rmtree(_tmp / "uploads", ignore_errors=True)
    yield


# ---------------------------------------------------------------- settings


@pytest.fixture()
def settings(monkeypatch):
    """settings(EMAIL_ALLOWLIST="a@b.test", ...) changes settings for one test."""
    def apply(**kw: str) -> config.Settings:
        for k, v in kw.items():
            monkeypatch.setenv(k, v)
        config.get_settings.cache_clear()
        return config.get_settings()

    yield apply
    monkeypatch.undo()
    config.get_settings.cache_clear()


# ---------------------------------------------------------------- AI


class FakeLLM:
    """Queue answers per schema name; anything not queued fails exactly like an unavailable AI."""

    def __init__(self) -> None:
        self.answers: dict[str, list] = {}
        self.calls: list[str] = []

    def answer(self, schema_name: str, value) -> None:  # type: ignore[no-untyped-def]
        self.answers.setdefault(schema_name, []).append(value)

    def transport(self, schema, system, content, model):  # type: ignore[no-untyped-def]
        self.calls.append(schema.__name__)
        queue = self.answers.get(schema.__name__)
        if not queue:
            raise llm_client.LLMFailed("AI is not available in this test")
        value = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(value, Exception):
            raise value
        return (value if isinstance(value, schema) else schema.model_validate(value)), 10, 10


@pytest.fixture(autouse=True)
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm_client, "transport", fake.transport)
    return fake


# ---------------------------------------------------------------- RDAP, web search, page fetch


class FakeLookups:
    def __init__(self) -> None:
        self.domains: dict[str, int | str] = {}  # domain → age in days, or "not_found"; anything else errors
        self.search: dict[str, list[lookups.SearchHit]] = {}  # substring of query → hits
        self.search_status = "not_configured"
        self.pages: dict[str, str] = {}
        self.rdap_calls: list[str] = []

    def rdap_lookup(self, domain, budget):  # type: ignore[no-untyped-def]
        from datetime import date, timedelta

        budget.charge_web()
        self.rdap_calls.append(domain)
        url = f"https://rdap.org/domain/{domain}"
        v = self.domains.get(domain, "error")
        if v == "not_found":
            return lookups.RdapOutcome("not_found", domain, source_ref=url, reason="the registry has no record of this domain")
        if v == "error":
            return lookups.RdapOutcome("error", domain, source_ref=url, reason="RDAP unreachable (network disabled in tests)")
        created = date.today() - timedelta(days=int(v))
        excerpt = f"Domain Name: {domain.upper()}\nRegistrar: Test Registrar\nCreation Date: {created.isoformat()}"
        return lookups.RdapOutcome("ok", domain, created, "Test Registrar", excerpt, url)

    def web_search(self, query, budget):  # type: ignore[no-untyped-def]
        if self.search_status == "not_configured":
            return lookups.SearchOutcome("not_configured", query, reason="web search is not configured (TAVILY_API_KEY missing)")
        budget.charge_web(search=True)  # same accounting as the real web_search
        if self.search_status != "ok":
            return lookups.SearchOutcome("error", query, reason="search service unreachable (test)")
        hits = [h for key, hs in self.search.items() if key.lower() in query.lower() for h in hs]
        return lookups.SearchOutcome("ok", query, hits)

    def fetch_page(self, url, budget):  # type: ignore[no-untyped-def]
        budget.charge_web()
        if url in self.pages:
            return lookups.FetchOutcome("ok", url, self.pages[url])
        return lookups.FetchOutcome("error", url, reason="page unreachable (network disabled in tests)")


@pytest.fixture(autouse=True)
def fake_lookups(monkeypatch):
    fake = FakeLookups()
    monkeypatch.setattr(lookups, "rdap_lookup", fake.rdap_lookup)
    monkeypatch.setattr(lookups, "web_search", fake.web_search)
    monkeypatch.setattr(lookups, "fetch_page", fake.fetch_page)
    return fake


# ---------------------------------------------------------------- email


class _Resp:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self._body = status, body

    def json(self) -> dict:
        return self._body


class FakeResend:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.status = 200

    def post(self, url, json=None, headers=None, timeout=None):  # type: ignore[no-untyped-def]
        assert url == "https://api.resend.com/emails"
        if self.status >= 300:
            return _Resp(self.status, {"message": "rejected by fake"})
        self.sent.append(json)
        return _Resp(200, {"id": f"msg_{len(self.sent)}"})


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    import httpx

    fake = FakeResend()

    class _Httpx:
        HTTPError = httpx.HTTPError
        post = staticmethod(fake.post)

    monkeypatch.setattr(mailer, "httpx", _Httpx)
    return fake


# ---------------------------------------------------------------- Clerk


_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CLERK_PROFILES: dict[str, tuple[str, str]] = {}  # Clerk user id → (verified email, name)


class _Jwks:
    def get_signing_key_from_jwt(self, token):  # type: ignore[no-untyped-def]
        class K:
            key = _KEY.public_key()
        return K()


def clerk_token(sub: str, *, azp: str = ORIGIN, issuer: str = ISSUER, mfa: bool = False, exp_in: int = 600, key=None) -> str:  # type: ignore[no-untyped-def]
    now = int(time.time())
    claims = {"sub": sub, "iss": issuer, "azp": azp, "iat": now, "nbf": now, "exp": now + exp_in, "fva": [1, 1 if mfa else -1]}
    return jwt.encode(claims, key or _KEY, algorithm="RS256")


@pytest.fixture(autouse=True)
def clerk(monkeypatch):
    monkeypatch.setattr(auth, "_jwks_client", lambda: _Jwks())

    def profile(user_id: str) -> tuple[str, str]:
        if user_id not in CLERK_PROFILES:
            raise auth.AuthError("unknown Clerk user (test)")
        return CLERK_PROFILES[user_id]

    monkeypatch.setattr(auth, "clerk_profile", profile)
    CLERK_PROFILES.clear()
    return CLERK_PROFILES


def bearer(user_or_external_id, **kw) -> dict:  # type: ignore[no-untyped-def]
    sub = user_or_external_id if isinstance(user_or_external_id, str) else user_or_external_id.external_id
    return {"Authorization": f"Bearer {clerk_token(sub, **kw)}"}


# ---------------------------------------------------------------- app + a populated workspace


@pytest.fixture()
def client(db):
    from probity.api.main import app

    services.run_sync(True)
    return TestClient(app)


@pytest.fixture()
def world(db, settings):
    """One workspace with users of every role and two vendors with history (built by tests/factories)."""
    from factories import build_world

    w = build_world()
    settings(EMAIL_ALLOWLIST=",".join(w.contact_emails))
    return w


def login(client, role: str, nth: int = 0, **kw) -> dict:  # type: ignore[no-untyped-def]
    """Authorization header for the nth user with `role` in the first workspace."""
    with owner_session() as s:
        first = s.scalars(select(Workspace).order_by(Workspace.created_at)).first()
        users = list(s.scalars(select(User).where(User.workspace_id == first.id, User.role == role).order_by(User.email)))
        return bearer(users[nth].external_id, **kw)
