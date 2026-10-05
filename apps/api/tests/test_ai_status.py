"""AI account problems are named and surfaced (credits/quota used up, key rejected, not configured), and a low score
the gate holds is never presented as "proceed"."""

from dataclasses import replace

import anthropic
import httpx
import pytest
from conftest import login
from factories import clean_spec
from helpers import API, gate, run_case
from pydantic import BaseModel

from probity import ai_health
from probity.llm import client as llm_client

REAL_TRANSPORT = llm_client.transport


class Ping(BaseModel):
    ok: bool


@pytest.fixture(autouse=True)
def _clean_health():
    ai_health.record_success()
    yield
    ai_health.record_success()


def _anthropic_raising(monkeypatch, message: str, status: int = 400):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    err = anthropic.BadRequestError(message, response=httpx.Response(status, request=req), body={"error": {"message": message}})

    class FakeClient:
        def __init__(self, *a, **k):
            self.messages = self

        def parse(self, **kw):
            raise err

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)


def test_anthropic_out_of_credits_is_named(settings, monkeypatch):
    settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
    _anthropic_raising(monkeypatch, "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.")
    with pytest.raises(llm_client.LLMFailed) as e:
        llm_client._anthropic_transport(Ping, "s", "u", "claude-sonnet-5-5")
    assert e.value.code == "credits_exhausted" and "credits have run out" in e.value.reason


def test_anthropic_spend_limit_is_named(settings, monkeypatch):
    settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
    _anthropic_raising(monkeypatch, "You have reached your specified API usage limits. You will regain access on 2026-11-01.")
    with pytest.raises(llm_client.LLMFailed) as e:
        llm_client._anthropic_transport(Ping, "s", "u", "claude-sonnet-5-5")
    assert e.value.code == "quota_exhausted"


def test_gemini_used_up_quota_is_named(settings, monkeypatch):
    settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="k")
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota."}}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(429, json=body))
    monkeypatch.setattr(llm_client, "_sleep", lambda s: None)
    with pytest.raises(llm_client.LLMFailed) as e:
        llm_client._gemini_transport(Ping, "s", "u", "gemini-3.5-flash-lite")
    assert e.value.code == "quota_exhausted" and "quota is used up" in e.value.reason


def test_status_endpoint_reports_and_clears_account_problems(client, world, monkeypatch, db):
    h = login(client, "viewer")
    assert client.get(f"{API}/ai/status", headers=h).json()["ok"] is True

    def out_of_credits(*a):
        raise llm_client.LLMFailed(llm_client.CREDITS_MSG, "credits_exhausted")

    monkeypatch.setattr(llm_client, "transport", out_of_credits)
    with pytest.raises(llm_client.LLMFailed):
        llm_client.generate(schema=Ping, system="s", user="u", tier="fast", tags={"agent": "t", "prompt_version": "t"})
    status = client.get(f"{API}/ai/status", headers=h).json()
    assert status["ok"] is False and status["problem"]["code"] == "credits_exhausted" and "Anthropic console" in status["problem"]["reason"]

    monkeypatch.setattr(llm_client, "transport", lambda *a: (Ping(ok=True), 1, 1))
    llm_client.generate(schema=Ping, system="s", user="u", tier="fast", tags={"agent": "t", "prompt_version": "t"})
    assert client.get(f"{API}/ai/status", headers=h).json() == {"ok": True, "provider": "anthropic", "problem": None}


def test_one_off_failures_do_not_raise_the_banner(db, monkeypatch):
    def flaky(*a):
        raise llm_client.LLMFailed("AI request timed out", "timeout")

    monkeypatch.setattr(llm_client, "transport", flaky)
    with pytest.raises(llm_client.LLMFailed):
        llm_client.generate(schema=Ping, system="s", user="u", tier="fast", tags={"agent": "t", "prompt_version": "t"})
    assert ai_health.current() is None


def test_missing_key_shows_as_not_configured(client, world, settings):
    settings(ANTHROPIC_API_KEY="")
    status = client.get(f"{API}/ai/status", headers=login(client, "viewer")).json()
    assert status["problem"]["code"] == "not_configured"


def test_held_low_score_recommends_review_not_proceed(client, world):
    acc = login(client, "accountant")
    held = run_case(client, acc, replace(clean_spec(), vendor_name="Totally Unknown Traders", gstin="", account_number="99887766554433"))
    assert held["status"] == "AWAITING_HUMAN" and held["risk"]["tier"] == "LOW" and gate(held)["auto_cleared"] is False
    assert held["recommendation"]["action"] == "REVIEW"
    cleared = run_case(client, acc, clean_spec())
    assert cleared["status"] == "AUTO_CLEARED" and cleared["recommendation"]["action"] == "PROCEED"
