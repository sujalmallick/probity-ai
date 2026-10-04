"""LLM providers: LLM_PROVIDER=anthropic|gemini behind one transport. The Gemini adapter is exercised against a fake
HTTP layer (no network): request shape, structured-output schema, token accounting, error mapping and retries."""

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from probity import storage
from probity.config import DEFAULT_MODELS, Settings
from probity.llm import client as llm_client

REAL_TRANSPORT = llm_client.transport  # captured before the autouse `llm` fixture replaces it


class Item(BaseModel):
    name: str
    qty: int = Field(ge=0)


class Answer(BaseModel):
    ok: bool
    items: list[Item] = Field(default_factory=list)


def _reply(payload: dict | None = None, *, status: int = 200, finish: str = "STOP", text: str | None = None,
           usage: dict | None = None, parts: list | None = None) -> httpx.Response:
    body = {
        "candidates": [{"content": {"parts": parts or [{"text": text if text is not None else json.dumps(payload)}]}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 120, "candidatesTokenCount": 30, "thoughtsTokenCount": 10},
    }
    return httpx.Response(status, json=body if status == 200 else {"error": {"code": status}})


@pytest.fixture()
def gemini(settings, monkeypatch):
    settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="test-gemini-key")
    calls: list[dict] = []
    replies: list[httpx.Response] = []

    def fake_post(url, json=None, timeout=None, headers=None):  # type: ignore[no-untyped-def]
        calls.append({"url": url, "json": json, "headers": headers})
        return replies.pop(0)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(llm_client, "_sleep", lambda s: None)
    return calls, replies


def test_gemini_request_shape_and_token_accounting(gemini):
    calls, replies = gemini
    replies.append(_reply({"ok": True, "items": [{"name": "Boxes", "qty": 2}]}))
    out, tin, tout = llm_client._gemini_transport(Answer, "system prompt", "user text", "gemini-3.5-flash-lite")
    assert out == Answer(ok=True, items=[Item(name="Boxes", qty=2)])
    assert (tin, tout) == (120, 40)  # thinking tokens are billed as output, so they count toward the budget
    call = calls[0]
    assert call["url"].endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert call["headers"] == {"x-goog-api-key": "test-gemini-key"}
    body = call["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "system prompt"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "user text"}]}]
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and "$ref" not in json.dumps(cfg["responseJsonSchema"])
    assert cfg["responseJsonSchema"]["properties"]["items"]["items"]["properties"]["qty"]["minimum"] == 0


def test_gemini_ignores_thought_parts(gemini):
    _, replies = gemini
    replies.append(_reply(parts=[{"text": "thinking...", "thought": True}, {"text": json.dumps({"ok": True})}]))
    assert llm_client._gemini_transport(Answer, "s", "u", "m")[0].ok is True


@pytest.mark.parametrize("status,needle", [(401, "GEMINI_API_KEY"), (403, "GEMINI_API_KEY"), (404, "model not found"), (400, "rejected")])
def test_gemini_http_errors_become_safe_llm_failures(gemini, status, needle):
    _, replies = gemini
    replies.append(_reply(status=status))
    with pytest.raises(llm_client.LLMFailed, match=needle) as e:
        llm_client._gemini_transport(Answer, "s", "u", "m")
    assert "test-gemini-key" not in e.value.reason


def test_gemini_retries_rate_limits_and_server_errors(gemini):
    calls, replies = gemini
    replies.extend([_reply(status=429), _reply(status=503), _reply({"ok": True})])
    assert llm_client._gemini_transport(Answer, "s", "u", "m")[0].ok is True and len(calls) == 3
    replies.extend([_reply(status=429)] * 3)
    with pytest.raises(llm_client.LLMFailed, match="rate limit"):
        llm_client._gemini_transport(Answer, "s", "u", "m")


@pytest.mark.parametrize("kwargs,exc", [
    ({"finish": "SAFETY", "text": "{}"}, llm_client.LLMFailed),
    ({"finish": "MAX_TOKENS", "text": "{\"ok\": tr"}, llm_client._InvalidOutput),
    ({"text": "not json"}, llm_client._InvalidOutput),
    ({"text": ""}, llm_client._InvalidOutput),
])
def test_gemini_bad_outputs(gemini, kwargs, exc):
    _, replies = gemini
    replies.append(_reply(**kwargs))
    with pytest.raises(exc):
        llm_client._gemini_transport(Answer, "s", "u", "m")


def test_gemini_schema_violation_is_retried_then_fails_closed(gemini, db, monkeypatch):
    """generate() with LLM_PROVIDER=gemini end to end: invalid output → one repair attempt → LLMFailed."""
    _, replies = gemini
    monkeypatch.setattr(llm_client, "transport", REAL_TRANSPORT)
    replies.extend([_reply({"ok": "maybe"}), _reply({"ok": True, "items": [{"name": "x", "qty": 1}]})])
    out = llm_client.generate(schema=Answer, system="s", user="u", tier="fast", tags={"agent": "test", "prompt_version": "t"})
    assert out.items[0].qty == 1
    replies.extend([_reply({"ok": "maybe"}), _reply({"ok": "still no"})])
    with pytest.raises(llm_client.LLMFailed, match="invalid output twice"):
        llm_client.generate(schema=Answer, system="s", user="u", tier="fast", tags={"agent": "test", "prompt_version": "t"})


def test_image_blocks_are_sent_as_inline_data():
    parts = llm_client._gemini_parts([{"type": "text", "text": "read this"},
                                      {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}])
    assert parts == [{"text": "read this"}, {"inlineData": {"mimeType": "image/png", "data": "QUJD"}}]


def test_dispatch_follows_llm_provider(settings, monkeypatch):
    seen: list[str] = []
    monkeypatch.setitem(llm_client._TRANSPORTS, "anthropic", lambda *a: seen.append("anthropic") or (Answer(ok=True), 1, 1))
    monkeypatch.setitem(llm_client._TRANSPORTS, "gemini", lambda *a: seen.append("gemini") or (Answer(ok=True), 1, 1))
    settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="k")
    REAL_TRANSPORT(Answer, "s", "u", "m")
    settings(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="k")
    REAL_TRANSPORT(Answer, "s", "u", "m")
    assert seen == ["gemini", "anthropic"]
    settings(LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    with pytest.raises(llm_client.LLMFailed, match="GEMINI_API_KEY missing"):
        REAL_TRANSPORT(Answer, "s", "u", "m")


def test_config_defaults_and_checks_per_provider():
    s = Settings(_env_file=None, llm_provider="gemini")
    assert s.llm_model("fast") == DEFAULT_MODELS["gemini"]["fast"] and s.llm_model("reasoning") == DEFAULT_MODELS["gemini"]["reasoning"]
    problems = s.required_problems()
    assert "GEMINI_API_KEY is not set (LLM_PROVIDER=gemini)" in problems
    assert not any("ANTHROPIC_API_KEY" in p for p in problems)
    mixed = Settings(_env_file=None, llm_provider="gemini", gemini_api_key="k", llm_model_fast="claude-sonnet-5-5")
    assert any(p.startswith("LLM_MODEL_FAST=claude-sonnet-5-5 is not a gemini model") for p in mixed.required_problems())
    default = Settings(_env_file=None)
    assert default.llm_provider == "anthropic" and default.llm_model("reasoning") == "claude-opus-5-5"
    assert "test-key-value" not in repr(Settings(_env_file=None, gemini_api_key="test-key-value"))


def test_r2_uploads_skip_the_unsupported_sse_header():
    assert storage._is_r2("https://abc123.r2.cloudflarestorage.com")
    assert not storage._is_r2("https://s3.ap-south-1.amazonaws.com") and not storage._is_r2(None)
