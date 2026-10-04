"""Runtime settings, read from environment variables and apps/api/.env (git-ignored).

There are no offline, demo or mock modes: every integration is either configured (live) or missing. Required settings
must be present or the app refuses to start; optional integrations that are missing make the affected checks report
"could not verify" instead of guessing. Test doubles are injected by the test suite only, never switched on here.
"""

from __future__ import annotations

import base64
import binascii
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]  # apps/api
REPO_ROOT = API_ROOT.parents[1]
ENV_FILE = API_ROOT / ".env"

# Never shown by repr()/str(): error reporters serialise local variables with repr.
_SECRET_FIELDS = frozenset({
    "field_key_b64", "hmac_key", "anthropic_api_key", "tavily_api_key", "clerk_secret_key", "resend_api_key",
    "s3_secret_access_key", "inbound_email_secret", "metrics_token", "sentry_dsn",
})
_URL_FIELDS = frozenset({"database_url", "database_migrate_url", "redis_url"})  # may embed a password
_URL_PASSWORD = re.compile(r"(://[^:/@\s]*:)[^@\s]*@")


class ConfigError(RuntimeError):
    """Required configuration is missing or invalid; the message lists what to fix (never secret values)."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    public_app_url: str = "http://localhost:5180"

    # --- required: database
    database_url: str | None = None  # PostgreSQL only
    database_migrate_url: str | None = None  # schema owner for Alembic; the app itself connects as a non-owner role
    db_pool_size: int = 10
    db_pool_overflow: int = 10

    # --- required: AI (Anthropic)
    anthropic_api_key: str | None = None
    llm_model_reasoning: str = "claude-opus-5-5"
    llm_model_fast: str = "claude-sonnet-5-5"
    llm_timeout_seconds: float = 90.0
    llm_max_output_tokens: int = 8000

    # --- required: sign-in (Clerk)
    clerk_issuer: str | None = None  # https://<your-app>.clerk.accounts.dev
    clerk_secret_key: str | None = None  # Backend API: resolves the user's email on first sign-in
    clerk_authorized_parties: str = ""  # comma-separated allowed `azp` origins, e.g. http://localhost:5180
    clerk_jwks_url: str | None = None  # defaults to {issuer}/.well-known/jwks.json
    clerk_frontend_api: str | None = None  # custom Clerk domain, added to the web CSP

    # --- required: app secrets
    field_key_b64: str | None = None  # base64 of 32 random bytes: AES-GCM key for bank account / PAN fields
    hmac_key: str | None = None  # ≥ 32 random characters: matches account numbers without decrypting

    # --- optional: web research (Tavily). Missing → external reputation checks report "could not verify".
    tavily_api_key: str | None = None

    # --- optional: email (Resend). Missing → nothing is sent. Sending is restricted to EMAIL_ALLOWLIST unless
    # EMAIL_SEND_TO_ANY=true is set explicitly.
    resend_api_key: str | None = None
    email_from: str | None = None  # "Accounts Payable <ap@yourdomain.com>" (a sender your Resend domain allows)
    email_allowlist: str = ""  # comma-separated addresses that may receive email while EMAIL_SEND_TO_ANY is false
    email_send_to_any: bool = False
    email_reply_domain: str | None = None  # replies to case+<id>@<domain> route back to the case
    inbound_email_secret: str | None = None  # HMAC secret for the inbound-reply webhook

    # --- optional: background jobs. Default runs investigations inside the API process; "celery" needs REDIS_URL.
    task_backend: Literal["inline", "celery"] = "inline"
    redis_url: str | None = None

    # --- optional: storage (local disk by default, or any S3-compatible bucket)
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = API_ROOT / "data" / "uploads"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # --- optional: upload virus scanning (ClamAV clamd)
    clamav_host: str | None = None
    clamav_port: int = 3310

    # --- observability
    log_level: str = "INFO"
    log_json: bool = False
    sentry_dsn: str | None = None
    metrics_token: str | None = None  # if set, /metrics requires "Authorization: Bearer <token>"

    # --- hard per-case budgets (Guardrails G7)
    max_depth: int = 2
    max_retries: int = 2
    max_llm_calls: int = 40
    max_tokens: int = 150_000
    max_web_calls: int = 25
    max_seconds: int = 240

    show_landing_page: bool = True
    cors_origins: str = "http://localhost:5180,http://127.0.0.1:5180"

    def __repr_args__(self):  # type: ignore[no-untyped-def]
        for k, v in super().__repr_args__():
            if k in _SECRET_FIELDS and v:
                v = "**********"
            elif k in _URL_FIELDS and isinstance(v, str):
                v = _URL_PASSWORD.sub(r"\1***@", v)
            yield k, v

    @field_validator("database_url", "database_migrate_url", mode="before")
    @classmethod
    def _sqlalchemy_scheme(cls, v: str | None) -> str | None:
        """Managed providers hand out postgres:// URLs; use the psycopg 3 driver."""
        if isinstance(v, str):
            v = v.strip() or None
            if v:
                for prefix in ("postgres://", "postgresql://"):
                    if v.startswith(prefix):
                        return "postgresql+psycopg://" + v[len(prefix):]
        return v

    # ---------------------------------------------------------------- checklist

    @property
    def email_allowlist_set(self) -> set[str]:
        return {a.strip().lower() for a in self.email_allowlist.split(",") if a.strip()}

    def required_problems(self) -> list[str]:
        """Missing or invalid required settings. Messages name the variable, never its value."""
        p: list[str] = []
        if not self.database_url:
            p.append("DATABASE_URL is not set (PostgreSQL connection string)")
        elif not self.database_url.startswith("postgresql"):
            p.append("DATABASE_URL must be a PostgreSQL URL (postgresql://...)")
        if not self.anthropic_api_key:
            p.append("ANTHROPIC_API_KEY is not set")
        if not self.clerk_issuer:
            p.append("CLERK_ISSUER is not set (https://<your-app>.clerk.accounts.dev)")
        elif not self.clerk_issuer.startswith("https://"):
            p.append("CLERK_ISSUER must start with https://")
        if not self.clerk_secret_key:
            p.append("CLERK_SECRET_KEY is not set")
        if not self.clerk_authorized_parties.strip():
            p.append("CLERK_AUTHORIZED_PARTIES is not set (the web app origin, e.g. http://localhost:5180)")
        if not self.field_key_b64:
            p.append("FIELD_KEY_B64 is not set (base64 of 32 random bytes; `python -m probity.bootstrap --generate-secrets` creates it)")
        else:
            try:
                if len(base64.b64decode(self.field_key_b64, validate=True)) != 32:
                    p.append("FIELD_KEY_B64 must decode to exactly 32 bytes")
            except (binascii.Error, ValueError):
                p.append("FIELD_KEY_B64 is not valid base64")
        if not self.hmac_key:
            p.append("HMAC_KEY is not set (at least 32 random characters; `python -m probity.bootstrap --generate-secrets` creates it)")
        elif len(self.hmac_key) < 32:
            p.append("HMAC_KEY must be at least 32 characters")
        if self.task_backend == "celery" and not self.redis_url:
            p.append("TASK_BACKEND=celery needs REDIS_URL")
        if self.storage_backend == "s3" and not (self.s3_bucket and self.s3_access_key_id and self.s3_secret_access_key):
            p.append("STORAGE_BACKEND=s3 needs S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY")
        if self.resend_api_key and not self.email_from:
            p.append("RESEND_API_KEY is set but EMAIL_FROM is not")
        if self.email_reply_domain and not self.inbound_email_secret:
            p.append("EMAIL_REPLY_DOMAIN is set but INBOUND_EMAIL_SECRET is not")
        if self.env == "prod":
            if self.storage_backend != "s3":
                p.append("ENV=prod needs STORAGE_BACKEND=s3")
            if not self.clamav_host:
                p.append("ENV=prod needs CLAMAV_HOST (uploads are virus-scanned)")
        return p

    def integration_status(self) -> list[tuple[str, str, str]]:
        """(name, status, detail) for the startup checklist and /app/config. Never contains secret values."""
        email = "missing"
        if self.resend_api_key and self.email_from:
            email = "live (any recipient)" if self.email_send_to_any else f"live (allowlist: {len(self.email_allowlist_set)} address(es))"
        return [
            ("database", "live" if self.database_url else "missing", "PostgreSQL"),
            ("ai", "live" if self.anthropic_api_key else "missing", f"Anthropic ({self.llm_model_fast} / {self.llm_model_reasoning})"),
            ("sign_in", "live" if (self.clerk_issuer and self.clerk_secret_key) else "missing", "Clerk"),
            ("app_secrets", "live" if (self.field_key_b64 and self.hmac_key) else "missing", "FIELD_KEY_B64, HMAC_KEY"),
            ("web_search", "live" if self.tavily_api_key else "missing", "Tavily - without it, web research reports 'could not verify'"),
            ("domain_lookup", "live", "RDAP (public, no key)"),
            ("gst_registry", "unavailable", "No GST registry provider - checksum only; status 'could not verify'"),
            ("email", email, "Resend - without it, nothing is sent"),
            ("background_jobs", self.task_backend if self.task_backend == "inline" else ("worker" if self.redis_url else "missing"),
             "inline = inside the API process"),
            ("storage", "cloud" if self.storage_backend == "s3" else "local", str(self.storage_dir) if self.storage_backend == "local" else "S3"),
            ("antivirus", "on" if self.clamav_host else "off", "ClamAV"),
        ]

    def checklist_text(self) -> str:
        lines = ["Probity configuration (apps/api/.env):"]
        for name, status, detail in self.integration_status():
            mark = "-- " if status in ("missing", "unavailable", "off") else "OK "
            lines.append(f"  {mark}{name:<16}{status:<34}{detail}")
        problems = self.required_problems()
        if problems:
            lines.append("Missing required settings:")
            lines += [f"  !! {p}" for p in problems]
        return "\n".join(lines)

    def validate_required(self) -> None:
        problems = self.required_problems()
        if problems:
            raise ConfigError("Probity cannot start - fix these in apps/api/.env:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()
