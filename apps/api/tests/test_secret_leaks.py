"""Secrets never leave the process through repr(), logs or error reports (H10)."""

import io

import structlog

from probity.config import Settings
from probity.logging import scrub, sentry_options

MARKERS = {
    "FIELD_KEY_B64": "fieldkey-marker-0123456789abcdef",
    "HMAC_KEY": "hmac-marker-0123456789",
    "ANTHROPIC_API_KEY": "sk-ant-marker0123456789",
    "TAVILY_API_KEY": "tvly-marker0123456789",
    "CLERK_SECRET_KEY": "sk_test_marker0123456789abcdef",
    "RESEND_API_KEY": "re_marker01_0123456789abcdef",
    "S3_SECRET_ACCESS_KEY": "s3-marker-secret-0123456789",
    "INBOUND_EMAIL_SECRET": "inbound-marker-secret",
    "METRICS_TOKEN": "metrics-marker-token",
    "SENTRY_DSN": "https://publickey-marker@o1.ingest.sentry.io/1",
}


def test_settings_repr_hides_every_secret(monkeypatch):
    for k, v in MARKERS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://app:db-marker-password@db:5432/probity")
    monkeypatch.setenv("REDIS_URL", "redis://:redis-marker-password@cache:6379/0")
    st = Settings(_env_file=None)
    text = repr(st) + str(st)
    for v in [*MARKERS.values(), "db-marker-password", "redis-marker-password"]:
        assert v not in text, v
    assert "@db:5432/probity" in text  # the non-secret parts stay readable
    assert st.hmac_key == MARKERS["HMAC_KEY"]  # values themselves are unchanged


def test_sentry_never_sends_frame_locals_or_bodies():
    opts = sentry_options("prod")
    assert opts["include_local_variables"] is False
    assert opts["send_default_pii"] is False
    assert opts["max_request_body_size"] == "never"
    event = {"extra": {"smtp_password": "p", "clerk_secret_key": "k", "Authorization": "Bearer abc.def", "note": "postgresql://u:pw@h/db"}}
    out = opts["before_send"](event, {})
    assert out["extra"]["smtp_password"] == out["extra"]["clerk_secret_key"] == out["extra"]["Authorization"] == "<redacted>"
    assert "pw@" not in out["extra"]["note"]


def test_scrub_masks_provider_keys_and_url_passwords():
    s = scrub("tvly-abcdef0123456789 AKIAABCDEFGHIJKLMNOP redis://:hunter2@cache:6379")
    assert "tvly-abcdef" not in s and "AKIAABCDEFGHIJKLMNOP" not in s and "hunter2" not in s


def test_traceback_text_is_scrubbed():
    from probity.logging import _scrub_processor

    buf = io.StringIO()
    log = structlog.wrap_logger(
        structlog.PrintLogger(buf),
        processors=[structlog.processors.format_exc_info, _scrub_processor, structlog.processors.JSONRenderer()],
    )
    try:
        raise RuntimeError("connect failed: postgresql://app:tb-marker-pw@db/probity")
    except RuntimeError:
        log.exception("boom")
    assert "tb-marker-pw" not in buf.getvalue()
