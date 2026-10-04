"""Tool runtime: modes (live|cached|mock), per-case budgets, fixture replay."""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from probity.config import get_settings

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    """Hard per-case caps (Guardrails G7). Termination is decided here, in code — never by an LLM."""

    max_llm_calls: int
    max_tokens: int
    max_web_calls: int
    max_seconds: int
    llm_calls: int = 0
    tokens: int = 0
    web_calls: int = 0
    started: float = field(default_factory=time.monotonic)
    exhausted: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_settings(cls) -> Budget:
        s = get_settings()
        return cls(s.max_llm_calls, s.max_tokens, s.max_web_calls, s.max_seconds)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def _trip(self, what: str) -> None:
        if what not in self.exhausted:
            self.exhausted.append(what)
        raise BudgetExceeded(what)

    def charge_web(self, n: int = 1) -> None:
        with self._lock:
            if self.web_calls + n > self.max_web_calls:
                self._trip("web_calls")
            if self.elapsed() > self.max_seconds:
                self._trip("wall_clock")
            self.web_calls += n

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

    def snapshot(self) -> dict[str, Any]:
        return {"llm_calls": self.llm_calls, "tokens": self.tokens, "web_calls": self.web_calls, "seconds": round(self.elapsed(), 2), "exhausted": list(self.exhausted)}


_REL_DATE = re.compile(r"\{\{today([+-]\d+)d\}\}")


def _resolve_dates(obj: Any, today: date) -> Any:
    """Fixtures store dates relative to 'today' (e.g. {{today-21d}}) so cached replays stay true over time."""
    if isinstance(obj, str):
        return _REL_DATE.sub(lambda m: (today + timedelta(days=int(m.group(1)))).isoformat(), obj)
    if isinstance(obj, list):
        return [_resolve_dates(o, today) for o in obj]
    if isinstance(obj, dict):
        return {k: _resolve_dates(v, today) for k, v in obj.items()}
    return obj


@lru_cache
def _raw_fixture(name: str) -> dict[str, Any]:
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fixture(name: str) -> dict[str, Any]:
    return _resolve_dates(_raw_fixture(name), date.today())


def mode() -> str:
    return get_settings().tools_mode
