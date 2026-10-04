"""Runtime settings. Every external dependency has an offline mode so the demo runs with no network."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # apps/api
REPO_ROOT = API_ROOT.parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", API_ROOT / ".env"), extra="ignore")

    env: Literal["dev", "demo", "test", "prod"] = "dev"
    database_url: str = f"sqlite:///{(API_ROOT / 'data' / 'probity.db').as_posix()}"
    storage_dir: Path = API_ROOT / "data" / "uploads"

    tools_mode: Literal["live", "cached", "mock"] = "cached"
    llm_mode: Literal["live", "cached", "mock"] = "mock"
    auth_mode: Literal["local", "clerk"] = "local"

    jwt_secret: str = "dev-only-change-me-please-32-bytes-min"
    jwt_ttl_minutes: int = 15 * 4 * 8  # demo sessions last a working day; prod should use 15
    # 32-byte base64 key for AES-GCM field encryption + HMAC for matching. Dev default only.
    field_key_b64: str = "ZGV2LW9ubHktZmllbGQta2V5LTMyLWJ5dGVzLWxvbmc="
    hmac_key: str = "dev-only-hmac-key"

    # LLM provider (live mode). Tiers per AI_Infrastructure.md.
    llm_model_reasoning: str = "claude-opus-5-5"
    llm_model_fast: str = "claude-sonnet-5-5"

    # Hard per-case budgets (Guardrails G7).
    max_depth: int = 2
    max_retries: int = 2
    max_llm_calls: int = 40
    max_tokens: int = 150_000
    max_web_calls: int = 25
    max_seconds: int = 240

    # Demo: delay between agent steps so the timeline is readable on stage.
    agent_delay_ms: int = 0
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    def validate_for_env(self) -> None:
        if self.env == "prod":
            if self.auth_mode == "local":
                raise RuntimeError("AUTH_MODE=local is not allowed when ENV=prod")
            if "dev-only" in self.jwt_secret or "dev-only" in self.hmac_key:
                raise RuntimeError("Dev secrets must be replaced in prod")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_env()
    return s
