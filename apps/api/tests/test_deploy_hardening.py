"""Hardening from the post-deploy review: parser helpers start without secrets, uploads check the role before any
parsing, downloads survive any filename, and production doesn't publish its API docs."""

from conftest import login
from factories import clean_spec, pdf
from helpers import API, upload

from probity.api.main import content_disposition, docs_routes
from probity.ingestion import isolate

SECRETS = ("DATABASE_URL", "DATABASE_MIGRATE_URL", "CLERK_SECRET_KEY", "FIELD_KEY_B64", "HMAC_KEY", "ANTHROPIC_API_KEY",
           "GEMINI_API_KEY", "S3_SECRET_ACCESS_KEY", "METRICS_TOKEN", "INBOUND_EMAIL_SECRET")


def test_parser_helpers_start_without_secrets(monkeypatch):
    for name in SECRETS:
        monkeypatch.setenv(name, "secret-value")
    env = isolate.helper_env()
    assert "PATH" in env
    assert not set(SECRETS) & set(env) and "secret-value" not in env.values()


def test_cleaning_runs_in_a_helper_and_keeps_working(client, world):
    doc = upload(client, login(client, "accountant"), clean_spec())
    r = client.get(f"{API}/documents/{doc}/file", headers=login(client, "viewer"))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_viewer_upload_is_refused_before_parsing(client, world, monkeypatch):
    from probity.ingestion import parse

    def must_not_parse(*a, **k):  # type: ignore[no-untyped-def]
        raise AssertionError("parsed a viewer's upload")

    monkeypatch.setattr(parse, "extract_text", must_not_parse)
    r = client.post(f"{API}/documents", headers=login(client, "viewer"), files={"file": ("x.pdf", pdf(clean_spec()), "application/pdf")})
    assert r.status_code == 403


def test_download_works_for_any_filename(client, world):
    name = 'चालान "INV-7"; final\r\n.pdf'
    doc = upload(client, login(client, "accountant"), clean_spec(), name=name)
    r = client.get(f"{API}/documents/{doc}/file", headers=login(client, "viewer"))
    assert r.status_code == 200
    header = r.headers["content-disposition"]
    assert header.startswith('inline; filename="') and "filename*=UTF-8''" in header
    fallback = header.split('filename="', 1)[1].split('"', 1)[0]
    assert not set(fallback) & set('";\\\r\n')


def test_content_disposition_is_ascii():
    assert content_disposition("attachment", "बिल.pdf").isascii()
    assert content_disposition("inline", "").startswith('inline; filename="document"')


def test_api_docs_are_off_in_production():
    assert docs_routes("prod") == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert docs_routes("dev") == {}
