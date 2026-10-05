"""LLM gateway (AI_Infrastructure.md §1): structured output from Anthropic (default) or Google Gemini.

    generate(schema=..., system=..., user=..., tier="fast"|"reasoning", tags=..., budget=...)

- The output must validate against the Pydantic schema. If it doesn't, one repair attempt is made; if that also
  fails, LLMFailed is raised ("fail closed"). The caller then reports "could not verify" / uses a labelled
  deterministic fallback. Model output is never guessed or patched.
- API problems (bad key, rate limit, timeout, network, refusal) also raise LLMFailed with a short, key-free reason.
- Every call is recorded (model, prompt version, tokens, latency, ok) for cost and audit; prompt text is not stored.
- LLM output never reaches the risk engine directly: it only produces claims, which still need evidence and
  verification.

Providers: LLM_PROVIDER=anthropic|gemini picks the API; everything above this paragraph is provider-neutral.
`transport` is the single function that talks to the network. It dispatches to one small adapter per provider
(`_anthropic_transport`, `_gemini_transport`), each of which turns (schema, system, content, model) into one API call
and returns (parsed object, input tokens, output tokens), mapping that provider's errors to LLMFailed. Adding a
provider = one adapter + one entry in _TRANSPORTS + its defaults in config.DEFAULT_MODELS. The test suite replaces
`transport`; app code never does.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from probity import ai_health
from probity.config import get_settings
from probity.db.models import LLMCall
from probity.db.session import telemetry_scope
from probity.guardrails.text import redact
from probity.tools.base import Budget, BudgetExceeded

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class LLMFailed(RuntimeError):
    """The model could not produce a valid answer. `reason` is safe to show to users; `code` says what kind of
    failure it was (e.g. credits_exhausted, quota_exhausted, key_invalid) so the UI can explain account problems."""

    def __init__(self, reason: str, code: str = "error"):
        super().__init__(reason)
        self.reason = reason
        self.code = code


CREDITS_MSG = "AI credits have run out — add credits in the Anthropic console (Plans & Billing). Rules are used until then."
ANTHROPIC_LIMIT_MSG = "AI usage limit reached on the Anthropic account — raise it in the Anthropic console. Rules are used until then."
GEMINI_QUOTA_MSG = "AI quota is used up on the Gemini key — wait for it to reset or enable billing in Google AI Studio. Rules are used until then."


class _InvalidOutput(ValueError):
    pass


def load_prompt(agent: str, name: str, version: int = 1) -> tuple[str, str]:
    """Return (prompt_version_id, text) with the shared preamble prepended."""
    preamble = (PROMPTS_DIR / "_shared" / "preamble.v1.md").read_text(encoding="utf-8")
    body = (PROMPTS_DIR / agent / f"{name}.v{version}.md").read_text(encoding="utf-8")
    return f"{agent}/{name}.v{version}", preamble + "\n\n" + body


def _record(tags: dict, model: str, ok: bool, started: float, tin: int = 0, tout: int = 0) -> None:
    try:
        with telemetry_scope(tags.get("workspace_id")) as s:
            s.add(LLMCall(
                workspace_id=tags.get("workspace_id", "-"), case_id=tags.get("case_id"), agent=tags.get("agent", "-"),
                prompt_version=tags.get("prompt_version", "-"), model=model, mode="live", ok=ok,
                latency_ms=int((time.monotonic() - started) * 1000), tokens_in=tin, tokens_out=tout,
            ))
    except Exception:  # noqa: BLE001 - telemetry must never break a case
        pass


def workspace_tokens_today(workspace_id: str) -> int:
    from datetime import datetime, timezone

    from sqlalchemy import func, select

    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    with telemetry_scope(workspace_id) as s:
        return int(s.scalar(select(func.coalesce(func.sum(LLMCall.tokens_in + LLMCall.tokens_out), 0))
                            .where(LLMCall.workspace_id == workspace_id, LLMCall.ts >= start)) or 0)


def transport(schema: type[T], system: str, content: str | list[dict[str, Any]], model: str) -> tuple[T, int, int]:
    """One structured-output call to the configured provider. Returns (parsed, input_tokens, output_tokens)."""
    st = get_settings()
    if not st.llm_api_key:
        raise LLMFailed(f"AI is not configured ({st.llm_key_name} missing)", "not_configured")
    return _TRANSPORTS[st.llm_provider](schema, system, content, model)


# ---------------------------------------------------------------- Anthropic

def _anthropic_transport(schema: type[T], system: str, content: str | list[dict[str, Any]], model: str) -> tuple[T, int, int]:
    import anthropic

    st = get_settings()
    client = anthropic.Anthropic(api_key=st.anthropic_api_key, max_retries=2, timeout=st.llm_timeout_seconds)
    try:
        resp = client.messages.parse(
            model=model, max_tokens=st.llm_max_output_tokens, system=system,
            messages=[{"role": "user", "content": content}], output_format=schema,
        )
    except anthropic.AuthenticationError as e:
        raise LLMFailed("AI key was rejected (check ANTHROPIC_API_KEY)", "key_invalid") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMFailed("AI key lacks permission for this model", "model_access") from e
    except anthropic.RateLimitError as e:
        raise LLMFailed("AI rate limit reached — try again shortly", "rate_limited") from e
    except anthropic.APITimeoutError as e:
        raise LLMFailed("AI request timed out", "timeout") from e
    except anthropic.APIConnectionError as e:
        raise LLMFailed("could not reach the AI service (network)", "network") from e
    except anthropic.BadRequestError as e:
        detail = str(getattr(e, "message", "") or e).lower()
        if "credit balance" in detail:  # Anthropic: "Your credit balance is too low to access the Anthropic API"
            raise LLMFailed(CREDITS_MSG, "credits_exhausted") from e
        if "usage limit" in detail or "spend limit" in detail:  # workspace / organisation spend limit reached
            raise LLMFailed(ANTHROPIC_LIMIT_MSG, "quota_exhausted") from e
        raise LLMFailed(f"AI request was rejected ({getattr(e, 'status_code', 400)})", "rejected") from e
    except anthropic.APIStatusError as e:
        raise LLMFailed(f"AI service error ({e.status_code})", "service_error") from e
    if resp.stop_reason == "refusal":
        raise LLMFailed("the model declined this request", "refused")
    if resp.stop_reason == "max_tokens":
        raise _InvalidOutput("output was cut off at the token limit")
    if resp.parsed_output is None:
        raise _InvalidOutput("no structured output returned")
    return schema.model_validate(resp.parsed_output.model_dump()), resp.usage.input_tokens, resp.usage.output_tokens


# ---------------------------------------------------------------- Google Gemini (REST generateContent, via httpx)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_sleep = time.sleep  # replaced in tests


def gemini_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON Schema with local $refs inlined, for generationConfig.responseJsonSchema. Inlining keeps the
    schema self-contained (no $defs), which every Gemini model version accepts."""
    raw = schema.model_json_schema()
    defs = raw.pop("$defs", {})

    def inline(node: Any, depth: int = 0) -> Any:
        if depth > 20:
            raise ValueError("schema nests too deeply to inline")
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                target = inline(defs[ref.split("/")[-1]], depth + 1)
                return {**target, **{k: inline(v, depth + 1) for k, v in node.items() if k != "$ref"}}
            return {k: inline(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v, depth + 1) for v in node]
        return node

    return inline(raw)


def _gemini_parts(content: str | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our content blocks (Anthropic shape: text / base64 image or document) → Gemini parts."""
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "text":
            parts.append({"text": block.get("text", "")})
        elif block.get("type") in ("image", "document") and (block.get("source") or {}).get("type") == "base64":
            src = block["source"]
            parts.append({"inlineData": {"mimeType": src.get("media_type", "application/octet-stream"), "data": src.get("data", "")}})
        else:
            raise _InvalidOutput(f"content block type {block.get('type')!r} is not supported for Gemini")
    return parts


def _gemini_transport(schema: type[T], system: str, content: str | list[dict[str, Any]], model: str) -> tuple[T, int, int]:
    import json

    import httpx

    st = get_settings()
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": _gemini_parts(content)}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": gemini_schema(schema),
                             "maxOutputTokens": st.llm_max_output_tokens},
    }
    for attempt in range(3):  # like the Anthropic SDK's max_retries=2: retry rate limits, 5xx and timeouts
        try:
            r = httpx.post(GEMINI_URL.format(model=model), json=body, timeout=st.llm_timeout_seconds,
                           headers={"x-goog-api-key": st.gemini_api_key or ""})
        except httpx.TimeoutException as e:
            if attempt < 2:
                _sleep(2 ** attempt)
                continue
            raise LLMFailed("AI request timed out", "timeout") from e
        except httpx.HTTPError as e:
            raise LLMFailed("could not reach the AI service (network)", "network") from e
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            _sleep(2 ** attempt)
            continue
        break
    if r.status_code in (401, 403):
        raise LLMFailed("AI key was rejected or lacks permission for this model (check GEMINI_API_KEY)", "key_invalid")
    if r.status_code == 429:  # still limited after retries: a used-up quota says so in the error body
        if "quota" in r.text.lower() or "resource_exhausted" in r.text.lower():
            raise LLMFailed(GEMINI_QUOTA_MSG, "quota_exhausted")
        raise LLMFailed("AI rate limit reached — try again shortly", "rate_limited")
    if r.status_code == 404:
        raise LLMFailed(f"AI model not found ({model})", "model_not_found")
    if r.status_code >= 400:
        raise LLMFailed(f"AI {'request was rejected' if r.status_code < 500 else 'service error'} ({r.status_code})",
                        "rejected" if r.status_code < 500 else "service_error")
    data = r.json()
    if (data.get("promptFeedback") or {}).get("blockReason"):
        raise LLMFailed("the model declined this request", "refused")
    cand = (data.get("candidates") or [{}])[0]
    finish = cand.get("finishReason", "")
    if finish in ("SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"):
        raise LLMFailed("the model declined this request", "refused")
    if finish == "MAX_TOKENS":
        raise _InvalidOutput("output was cut off at the token limit")
    text = "".join(part.get("text", "") for part in (cand.get("content") or {}).get("parts", []) if not part.get("thought"))
    if not text.strip():
        raise _InvalidOutput("no structured output returned")
    usage = data.get("usageMetadata") or {}
    tin = int(usage.get("promptTokenCount") or 0)
    tout = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)  # thinking is billed as output
    try:
        parsed = json.loads(text)
    except ValueError as e:
        raise _InvalidOutput(f"output was not valid JSON ({e})") from e
    return schema.model_validate(parsed), tin, tout


_TRANSPORTS = {"anthropic": _anthropic_transport, "gemini": _gemini_transport}


def generate(
    *,
    schema: type[T],
    system: str,
    user: str | list[dict[str, Any]],
    tier: Literal["fast", "reasoning"],
    tags: dict,
    budget: Budget | None = None,
    redact_input: bool = True,
) -> T:
    """Ask the model for an object matching `schema`. Raises LLMFailed if it can't (never returns a guess)."""
    st = get_settings()
    started = time.monotonic()
    model = st.llm_model(tier)
    if redact_input and isinstance(user, str):
        user = redact(user).text
    ws = tags.get("workspace_id")
    if ws and ws != "-":
        limit = st.workspace_daily_token_limit
        if workspace_tokens_today(ws) >= limit:
            if budget is not None and "workspace_daily_tokens" not in budget.exhausted:
                budget.exhausted.append("workspace_daily_tokens")
            raise BudgetExceeded("workspace_daily_tokens", limit)
    approx = (len(system) + (len(user) if isinstance(user, str) else 4000)) // 4
    if budget is not None:
        budget.charge_llm(tokens=approx)
    content: str | list[dict[str, Any]] = user
    tin = tout = 0
    last: Exception | None = None
    for attempt in range(2):  # original + one repair
        try:
            out, i, o = transport(schema, system, content, model)
            tin, tout = tin + i, tout + o
            if budget is not None:
                budget.add_tokens(i + o - approx)  # count real input + output tokens, not just the estimate
            out = schema.model_validate(out.model_dump())  # validate again: never trust a transport blindly
            _record(tags, model, True, started, tin, tout)
            ai_health.record_success()
            return out
        except LLMFailed as e:
            _record(tags, model, False, started, tin, tout)
            ai_health.record_failure(e.code, e.reason)
            raise
        except (ValidationError, _InvalidOutput) as e:
            last = e
            if attempt == 0:
                if budget is not None:
                    budget.charge_llm()
                note = f"\n\nYour previous output was invalid: {str(e)[:400]}. Return ONLY data matching the schema."
                content = (user + note) if isinstance(user, str) else [*user, {"type": "text", "text": note}]
    _record(tags, model, False, started, tin, tout)
    raise LLMFailed(f"the AI returned invalid output twice ({str(last)[:120]})", "invalid_output")
