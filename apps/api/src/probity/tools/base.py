"""Per-case budgets for tool and LLM calls (Guardrails G7)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from probity.config import get_settings


LIMITS = {  # code → (setting, human description); used in the message shown on the case
    "tokens": ("CASE_TOKEN_LIMIT", "AI tokens per case"),
    "searches": ("CASE_WEB_SEARCH_LIMIT", "web searches per case"),
    "web_calls": ("MAX_WEB_CALLS", "web requests per case"),
    "llm_calls": ("MAX_LLM_CALLS", "AI calls per case"),
    "wall_clock": ("MAX_SECONDS", "seconds per investigation"),
    "workspace_daily_tokens": ("WORKSPACE_DAILY_TOKEN_LIMIT", "AI tokens per workspace per day"),
}


class BudgetExceeded(RuntimeError):
    """A usage limit was reached. `str(e)` is the user-facing message; `limit` is the code."""

    def __init__(self, limit: str, value: int | None = None):
        setting, what = LIMITS.get(limit, (limit.upper(), limit.replace("_", " ")))
        amount = f"{value:,} " if value is not None else ""
        super().__init__(f"Limit reached: {amount}{what} ({setting})")
        self.limit = limit


@dataclass
class Budget:
    """Hard per-case caps (Guardrails G7). Termination is decided here, in code — never by an LLM."""

    max_llm_calls: int
    max_tokens: int
    max_web_calls: int
    max_seconds: int
    max_searches: int = 10
    llm_calls: int = 0
    tokens: int = 0
    web_calls: int = 0
    searches: int = 0
    started: float = field(default_factory=time.monotonic)
    exhausted: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_settings(cls) -> Budget:
        s = get_settings()
        return cls(s.max_llm_calls, s.case_token_limit, s.max_web_calls, s.max_seconds, max_searches=s.case_web_search_limit)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _trip(self, what: str) -> None:
        if what not in self.exhausted:
            self.exhausted.append(what)
        cap = {"tokens": self.max_tokens, "searches": self.max_searches, "web_calls": self.max_web_calls,
               "llm_calls": self.max_llm_calls, "wall_clock": self.max_seconds}.get(what)
        raise BudgetExceeded(what, cap)

    def charge_web(self, n: int = 1, *, search: bool = False) -> None:
        with self._lock:
            if search and self.searches + n > self.max_searches:
                self._trip("searches")
            if self.web_calls + n > self.max_web_calls:
                self._trip("web_calls")
            if self.elapsed() > self.max_seconds:
                self._trip("wall_clock")
            self.web_calls += n
            if search:
                self.searches += n

    def charge_llm(self, tokens: int = 0) -> None:
        with self._lock:
            if self.llm_calls + 1 > self.max_llm_calls:
                self._trip("llm_calls")
            if self.tokens + tokens > self.max_tokens:
                self._trip("tokens")
            if self.elapsed() > self.max_seconds:
                self._trip("wall_clock")
            self.llm_calls += 1
            self.tokens += tokens

    def add_tokens(self, tokens: int) -> None:
        """Record tokens actually used (input + output) beyond the pre-call estimate; the next call trips if over."""
        with self._lock:
            self.tokens += max(0, tokens)

    def snapshot(self) -> dict[str, Any]:
        return {"llm_calls": self.llm_calls, "tokens": self.tokens, "web_calls": self.web_calls, "searches": self.searches,
                "seconds": round(self.elapsed(), 2), "exhausted": list(self.exhausted)}
