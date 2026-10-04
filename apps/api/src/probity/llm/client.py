"""LLM gateway (AI_Infrastructure.md §1): Anthropic Messages API with structured output.

    generate(schema=..., system=..., user=..., tier="fast"|"reasoning", tags=..., budget=...)

- The output must validate against the Pydantic schema. If it doesn't, one repair attempt is made; if that also
  fails, LLMFailed is raised ("fail closed"). The caller then reports "could not verify" / uses a labelled
  deterministic fallback. Model output is never guessed or patched.
- API problems (bad key, rate limit, timeout, network, refusal) also raise LLMFailed with a short, key-free reason.
- Every call is recorded (model, prompt version, tokens, latency, ok) for cost and audit; prompt text is not stored.
- LLM output never reaches the risk engine directly: it only produces claims, which still need evidence and
  verification.

`transport` is the single function that talks to the network. The test suite replaces it; app code never does.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from probity.config import get_settings
from probity.db.models import LLMCall
from probity.db.session import telemetry_scope
from probity.guardrails.text import redact
from probity.tools.base import Budget, BudgetExceeded

T = TypeVar("T", bound=BaseModel)
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


class LLMFailed(RuntimeError):
    """The model could not produce a valid answer. `reason` is safe to show to users."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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
    """One Messages API call with structured output. Returns (parsed, input_tokens, output_tokens)."""
    import anthropic

    st = get_settings()
    if not st.anthropic_api_key:
        raise LLMFailed("AI is not configured (ANTHROPIC_API_KEY missing)")
    client = anthropic.Anthropic(api_key=st.anthropic_api_key, max_retries=2, timeout=st.llm_timeout_seconds)
    try:
        resp = client.messages.parse(
            model=model, max_tokens=st.llm_max_output_tokens, system=system,
            messages=[{"role": "user", "content": content}], output_format=schema,
        )
    except anthropic.AuthenticationError as e:
        raise LLMFailed("AI key was rejected (check ANTHROPIC_API_KEY)") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMFailed("AI key lacks permission for this model") from e
    except anthropic.RateLimitError as e:
        raise LLMFailed("AI rate limit reached — try again shortly") from e
    except anthropic.APITimeoutError as e:
        raise LLMFailed("AI request timed out") from e
    except anthropic.APIConnectionError as e:
        raise LLMFailed("could not reach the AI service (network)") from e
    except anthropic.BadRequestError as e:
        raise LLMFailed(f"AI request was rejected ({getattr(e, 'status_code', 400)})") from e
    except anthropic.APIStatusError as e:
        raise LLMFailed(f"AI service error ({e.status_code})") from e
    if resp.stop_reason == "refusal":
        raise LLMFailed("the model declined this request")
    if resp.stop_reason == "max_tokens":
        raise _InvalidOutput("output was cut off at the token limit")
    if resp.parsed_output is None:
        raise _InvalidOutput("no structured output returned")
    return schema.model_validate(resp.parsed_output.model_dump()), resp.usage.input_tokens, resp.usage.output_tokens


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
    model = st.llm_model_reasoning if tier == "reasoning" else st.llm_model_fast
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
            return out
        except LLMFailed:
            _record(tags, model, False, started, tin, tout)
            raise
        except (ValidationError, _InvalidOutput) as e:
            last = e
            if attempt == 0:
                if budget is not None:
                    budget.charge_llm()
                note = f"\n\nYour previous output was invalid: {str(e)[:400]}. Return ONLY data matching the schema."
                content = (user + note) if isinstance(user, str) else [*user, {"type": "text", "text": note}]
    _record(tags, model, False, started, tin, tout)
    raise LLMFailed(f"the AI returned invalid output twice ({str(last)[:120]})")
