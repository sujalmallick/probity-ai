"""Health, readiness and Prometheus metrics (AI_Infrastructure.md §9).

HTTP metrics are recorded per API process. Business metrics (cases by status/tier, auto-clear rate,
durations, agent failures, LLM calls) are computed from the database at scrape time so they are exact
across API and worker processes without a push gateway.
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sqlalchemy import func, select, text

from probity.config import get_settings

REGISTRY = CollectorRegistry()
HTTP_REQUESTS = Counter("probity_http_requests_total", "HTTP requests", ["method", "route", "status"], registry=REGISTRY)
HTTP_LATENCY = Histogram("probity_http_request_seconds", "HTTP latency", ["method", "route"], registry=REGISTRY,
                         buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
CASES = Gauge("probity_cases", "Cases by status and tier", ["status", "tier"], registry=REGISTRY)
AUTO_CLEAR_RATE = Gauge("probity_auto_clear_ratio", "Share of scored cases auto-cleared", registry=REGISTRY)
CASE_DURATION = Gauge("probity_case_duration_seconds_p50", "Median investigation wall-clock (last 500 cases)", registry=REGISTRY)
AGENT_FAILURES = Gauge("probity_agent_failures_24h", "agent.failed events in the last 24h", ["agent"], registry=REGISTRY)
LLM_CALLS = Gauge("probity_llm_calls_24h", "LLM calls in the last 24h", ["mode", "ok"], registry=REGISTRY)
UNVERIFIED_CLAIMS = Gauge("probity_unverified_claim_ratio", "Share of active claims left unverified", registry=REGISTRY)


def observe_request(method: str, route: str, status: int, seconds: float) -> None:
    HTTP_REQUESTS.labels(method, route, str(status)).inc()
    HTTP_LATENCY.labels(method, route).observe(seconds)


def _refresh_business_metrics() -> None:
    """Aggregate across all workspaces (operator view). Uses the migrate/owner connection on Postgres
    because row-level security intentionally hides cross-tenant rows from the app role."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine

    from probity.db.models import AgentEvent, Case, ClaimRow, LLMCall
    from probity.db.session import get_engine

    st = get_settings()
    eng = create_engine(st.database_migrate_url, pool_pre_ping=True) if st.database_migrate_url else get_engine()
    tel = eng
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    with eng.connect() as c:
        CASES.clear()
        rows = c.execute(select(Case.status, Case.risk, Case.duration_ms).order_by(Case.created_at.desc()).limit(5000)).all()
        counts: dict[tuple[str, str], int] = {}
        scored = auto = 0
        durations = []
        for status, risk, dur in rows:
            tier = (risk or {}).get("tier", "NONE")
            counts[(status, tier)] = counts.get((status, tier), 0) + 1
            if risk:
                scored += 1
                auto += status == "AUTO_CLEARED"
            if dur:
                durations.append(dur / 1000)
        for (status, tier), n in counts.items():
            CASES.labels(status, tier).set(n)
        AUTO_CLEAR_RATE.set(auto / scored if scored else 0)
        durations = sorted(durations[:500])
        CASE_DURATION.set(durations[len(durations) // 2] if durations else 0)
        active = c.execute(select(func.count()).select_from(ClaimRow).where(ClaimRow.active.is_(True))).scalar() or 0
        unver = c.execute(select(func.count()).select_from(ClaimRow).where(ClaimRow.active.is_(True), ClaimRow.status == "unverified")).scalar() or 0
        UNVERIFIED_CLAIMS.set(unver / active if active else 0)
    with tel.connect() as c:
        AGENT_FAILURES.clear()
        for agent, n in c.execute(select(AgentEvent.agent, func.count()).where(AgentEvent.type == "agent.failed", AgentEvent.ts >= since).group_by(AgentEvent.agent)):
            AGENT_FAILURES.labels(agent or "pipeline").set(n)
        LLM_CALLS.clear()
        for mode, ok, n in c.execute(select(LLMCall.mode, LLMCall.ok, func.count()).where(LLMCall.ts >= since).group_by(LLMCall.mode, LLMCall.ok)):
            LLM_CALLS.labels(mode, str(ok).lower()).set(n)


def metrics_payload() -> tuple[bytes, str]:
    try:
        _refresh_business_metrics()
    except Exception:  # noqa: BLE001 - never fail a scrape because of business metrics
        pass
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def readiness() -> tuple[bool, dict]:
    from probity.db.session import get_engine
    from probity.redis_client import sync_redis

    checks: dict[str, str] = {}
    ok = True
    t0 = time.perf_counter()
    try:
        with get_engine().connect() as c:
            c.execute(text("SELECT 1"))
            if c.dialect.name == "postgresql":
                current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
                from alembic.script import ScriptDirectory

                from probity.db.migrate import alembic_config

                head = ScriptDirectory.from_config(alembic_config()).get_current_head()
                if current != head:
                    ok = False
                    checks["migrations"] = f"at {current}, expected {head}"
                else:
                    checks["migrations"] = "ok"
        checks["database"] = f"ok ({(time.perf_counter() - t0) * 1000:.0f} ms)"
    except Exception as e:  # noqa: BLE001
        ok = False
        checks["database"] = f"error: {type(e).__name__}"
    r = sync_redis()
    if r is not None:
        try:
            r.ping()
            checks["redis"] = "ok"
        except Exception as e:  # noqa: BLE001
            ok = False
            checks["redis"] = f"error: {type(e).__name__}"
    st = get_settings()
    checks.update({"env": st.env, "integrations": {name: status for name, status, _ in st.integration_status()}})
    return ok, checks
