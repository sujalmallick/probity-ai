"""Runtime settings (12-factor: everything from env). Every external dependency has an offline fallback,
and ENV=prod refuses to start with dev secrets or demo-only modes."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # apps/api
REPO_ROOT = API_ROOT.parents[1]

_DEV_JWT = "dev-only-change-me-please-32-bytes-min"
_DEV_FIELD_KEY = "ZGV2LW9ubHktZmllbGQta2V5LTMyLWJ5dGVzLWxvbmc="
_DEV_HMAC = "dev-only-hmac-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", API_ROOT / ".env"), extra="ignore")

    env: Literal["dev", "demo", "test", "prod"] = "dev"
    public_app_url: str = "http://localhost:5180"

    # --- data
    database_url: str = f"sqlite:///{(API_ROOT / 'data' / 'probity.db').as_posix()}"
    database_migrate_url: str | None = None  # schema owner for Alembic; the app itself connects as a non-owner role
    db_pool_size: int = 10
    db_pool_overflow: int = 10
    redis_url: str | None = None  # enables Celery workers, Redis event fan-out, distributed rate limits

    # --- execution: "inline" (thread pool in the API process) or "celery" (separate worker service)
    task_backend: Literal["inline", "celery"] = "inline"

    # --- object storage for uploads: local disk (dev) or any S3-compatible bucket (S3, R2, MinIO)
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = API_ROOT / "data" / "uploads"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- integrations (each falls back to an offline mode when unset)
    tools_mode: Literal["live", "cached", "mock"] = "cached"
    llm_mode: Literal["live", "cached", "mock"] = "mock"
    auth_mode: Literal["local", "clerk"] = "local"
    llm_model_reasoning: str = "claude-opus-5-5"
    llm_model_fast: str = "claude-sonnet-5-5"
    anthropic_api_key: str | None = None
    tavily_api_key: str | None = None
    clerk_issuer: str | None = None  # e.g. https://<your-app>.clerk.accounts.dev
    clerk_jwks_url: str | None = None  # defaults to {issuer}/.well-known/jwks.json
    clerk_authorized_parties: str = ""  # comma-separated allowed `azp` origins
    clerk_secret_key: str | None = None  # Backend API (fetch user email on first sign-in)

    # --- email: "outbox" (stored only, simulated inbox) | "resend" | "smtp"
    email_backend: Literal["outbox", "resend", "smtp"] = "outbox"
    email_from: str = "Probity Accounts Payable <ap@probity.local>"
    email_reply_domain: str | None = None  # replies to case+<id>@<domain> route back to the case
    resend_api_key: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    inbound_email_secret: str | None = None  # HMAC secret for the inbound-reply webhook

    # --- upload security
    clamav_host: str | None = None  # clamd TCP host; required when ENV=prod
    clamav_port: int = 3310
    ocr_enabled: bool = True

    # --- secrets
    jwt_secret: str = _DEV_JWT
    jwt_ttl_minutes: int = 8 * 60  # local demo auth only
    field_key_b64: str = _DEV_FIELD_KEY
    hmac_key: str = _DEV_HMAC

    # --- observability
    log_level: str = "INFO"
    log_json: bool = False
    sentry_dsn: str | None = None

    # --- hard per-case budgets (Guardrails G7)
    max_depth: int = 2
    max_retries: int = 2
    max_llm_calls: int = 40
    max_tokens: int = 150_000
    max_web_calls: int = 25
    max_seconds: int = 240

    agent_delay_ms: int = 0
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    def problems_for_prod(self) -> list[str]:
        p = []
        if self.auth_mode == "local":
            p.append("AUTH_MODE=local is not allowed in prod (use clerk)")
        if self.jwt_secret == _DEV_JWT or self.field_key_b64 == _DEV_FIELD_KEY or self.hmac_key == _DEV_HMAC:
            p.append("JWT_SECRET / FIELD_KEY_B64 / HMAC_KEY must be set to real secrets")
        if not self.database_url.startswith("postgresql"):
            p.append("DATABASE_URL must be PostgreSQL")
        if self.storage_backend != "s3":
            p.append("STORAGE_BACKEND must be s3 (uploads must be shared by api and worker)")
        if not self.clamav_host:
            p.append("CLAMAV_HOST must be set (uploads are virus-scanned)")
        if self.auth_mode == "clerk" and not self.clerk_issuer:
            p.append("CLERK_ISSUER is required with AUTH_MODE=clerk")
        if self.llm_mode == "live" and not self.anthropic_api_key:
            p.append("ANTHROPIC_API_KEY is required with LLM_MODE=live")
        if self.email_backend == "resend" and not self.resend_api_key:
            p.append("RESEND_API_KEY is required with EMAIL_BACKEND=resend")
        if self.email_reply_domain and not self.inbound_email_secret:
            p.append("INBOUND_EMAIL_SECRET is required when EMAIL_REPLY_DOMAIN is set")
        return p

    def validate_for_env(self) -> None:
        if self.env == "prod":
            problems = self.problems_for_prod()
            if problems:
                raise RuntimeError("Refusing to start in ENV=prod:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_env()
    return s
