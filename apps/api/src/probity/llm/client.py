"""LLM gateway (AI_Infrastructure.md §1).

    generate(schema=..., system=..., user=..., tier="fast"|"reasoning", tags=..., mock=...)

Modes (LLM_MODE):
  mock    deterministic, schema-valid output from the caller's `mock` function (tests, offline demo)
  cached  replay recorded live responses keyed by prompt_version + input hash; miss → mock
  live    Anthropic Messages API with structured output (`messages.parse`)

Every output is validated against the Pydantic schema. Invalid → one repair attempt → fail closed
(LLMFailed); the calling agent is then marked failed, never guessed. LLM output never reaches the
risk engine — it only produces claims, which still need evidence and verification.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from probity.config import API_ROOT, get_settings
from probity.db.models import LLMCall
from probity.db.session import telemetry_scope
from probity.guardrails.text import redact
from probity.tools.base import Budget

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
CACHE_PATH = API_ROOT / "data" / "llm_cache.json"
_cache_lock = threading.Lock()


class LLMFailed(RuntimeError):
    pass


def load_prompt(agent: str, name: str, version: int = 1) -> tuple[str, str]:
    """Return (prompt_version_id, text) with the shared preamble prepended."""
    preamble = (PROMPTS_DIR / "_shared" / "preamble.v1.md").read_text(encoding="utf-8")
    body = (PROMPTS_DIR / agent / f"{name}.v{version}.md").read_text(encoding="utf-8")
    return f"{agent}/{name}.v{version}", preamble + "\n\n" + body


def _key(prompt_version: str, system: str, user: str) -> str:
    return prompt_version + ":" + hashlib.sha256((system + "\x00" + user).encode()).hexdigest()[:24]


def _cache_read(key: str) -> dict | None:
    if not CACHE_PATH.exists():
        return None
    with _cache_lock:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8")).get(key)


def _cache_write(key: str, value: dict) -> None:
    with _cache_lock:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
        data[key] = value
        CACHE_PATH.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")


def _record(tags: dict, model: str, mode: str, ok: bool, started: float, tin: int = 0, tout: int = 0) -> None:
    try:
        with telemetry_scope(tags.get("workspace_id")) as s:
            s.add(
                LLMCall(
                    workspace_id=tags.get("workspace_id", "-"),
                    case_id=tags.get("case_id"),
                    agent=tags.get("agent", "-"),
                    prompt_version=tags.get("prompt_version", "-"),
                    model=model,
                    mode=mode,
                    ok=ok,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    tokens_in=tin,
                    tokens_out=tout,
                )
            )
    except Exception:  # noqa: BLE001 - telemetry must never break a case
        pass


def generate(
    *,
    schema: type[T],
    system: str,
    user: str,
    tier: Literal["fast", "reasoning"],
    tags: dict,
    mock: Callable[[], T],
    budget: Budget | None = None,
    redact_input: bool = True,
) -> T:
    settings = get_settings()
    mode = settings.llm_mode
    started = time.monotonic()
    model = settings.llm_model_reasoning if tier == "reasoning" else settings.llm_model_fast
    if redact_input:
        user = redact(user).text
    key = _key(tags.get("prompt_version", "-"), system, user)

    if mode == "cached":
        hit = _cache_read(key)
        if hit is not None:
            try:
                out = schema.model_validate(hit)
                _record(tags, model, "cached", True, started)
                return out
            except ValidationError:
                pass
        mode = "mock"

    if mode == "mock":
        out = schema.model_validate(mock().model_dump())  # re-validate even our own mocks
        _record(tags, "mock", "mock", True, started)
        return out

    # live
    if budget is not None:
        budget.charge_llm(tokens=len(system + user) // 4)
    last_err: Exception | None = None
    prompt = user
    for attempt in range(2):  # original + one repair
        try:
            out, tin, tout = _anthropic_parse(schema, system, prompt, model)
            _record(tags, model, "live", True, started, tin, tout)
            _cache_write(key, out.model_dump(mode="json"))
            return out
        except (ValidationError, ValueError) as e:
            last_err = e
            prompt = user + f"\n\nYour previous output was invalid: {str(e)[:500]}. Return ONLY JSON matching the schema."
            if budget is not None and attempt == 0:
                budget.charge_llm()
    _record(tags, model, "live", False, started)
    raise LLMFailed(f"{tags.get('agent')}: invalid structured output after repair: {last_err}")


def _anthropic_parse(schema: type[T], system: str, user: str, model: str) -> tuple[T, int, int]:
    import anthropic

    client = anthropic.Anthropic(api_key=get_settings().anthropic_api_key or None, max_retries=2, timeout=90)
    resp = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    if resp.stop_reason == "refusal":
        raise ValueError("model refused")
    parsed = resp.parsed_output
    if parsed is None:
        raise ValueError("no parsed output")
    return schema.model_validate(parsed.model_dump()), resp.usage.input_tokens, resp.usage.output_tokens
