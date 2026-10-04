"""Oversized request bodies are refused before parsing or authentication (H7)."""

from conftest import login

from helpers import API
from probity.api.limits import MB, limit_for


def test_limits_per_route():
    assert limit_for("POST", "/api/v1/documents") == 16 * MB
    assert limit_for("POST", "/api/v1/imports/vendors") == 6 * MB
    assert limit_for("POST", "/api/v1/cases") == 1 * MB


def test_oversized_upload_refused_without_auth(client):
    r = client.post(f"{API}/documents", files={"file": ("big.pdf", b"%PDF-1.4\n" + b"0" * (17 * MB), "application/pdf")})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_oversized_json_refused(client, world):
    r = client.post(f"{API}/cases", headers=login(client, "accountant"), json={"document_id": "x" * (2 * MB)})
    assert r.status_code == 413


def test_normal_requests_unaffected(client, world):
    assert client.get(f"{API}/me", headers=login(client, "viewer")).status_code == 200
