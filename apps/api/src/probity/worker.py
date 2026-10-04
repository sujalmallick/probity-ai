"""Celery worker (optional): investigations run here when TASK_BACKEND=celery. The default is inline (API process).

    celery -A probity.worker worker -B --loglevel=INFO --concurrency=4      (Linux / Docker)
    celery -A probity.worker worker -B --pool=solo --loglevel=INFO          (Windows dev)

Tasks are acked late and re-queued if a worker dies; each task checks the case status first, so a
redelivered message never re-runs a finished investigation.
"""

from __future__ import annotations


from celery import Celery

from probity.config import get_settings
from probity.logging import configure_logging, get_logger

configure_logging()
log = get_logger("worker")
_settings = get_settings()
print(_settings.checklist_text(), flush=True)
_settings.validate_required()  # the worker refuses to start with missing required settings, like the API

app = Celery("probity", broker=_settings.redis_url or "memory://")
app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=_settings.max_seconds + 120,
    task_soft_time_limit=_settings.max_seconds + 60,
    broker_connection_retry_on_startup=True,
    task_default_queue="probity",
    beat_schedule={"followups": {"task": "probity.followups", "schedule": 15 * 60.0}},
    timezone="UTC",
)


@app.task(name="probity.run_case", bind=True, max_retries=0)
def run_case(self, workspace_id: str, case_id: str, depth: int = 0) -> str:  # type: ignore[no-untyped-def]
    from probity import services
    from probity.db.models import Case
    from probity.db.session import session_scope

    with session_scope(workspace_id) as s:
        c = s.get(Case, case_id)
        if c is None:
            log.warning("case.missing", case_id=case_id)
            return "missing"
        if depth == 0 and c.status not in ("QUEUED",):
            log.info("case.already_processed", case_id=case_id, status=c.status)
            return "skipped"
    log.info("case.start", case_id=case_id, workspace_id=workspace_id, depth=depth)
    if depth == 0:
        services._run(workspace_id, case_id, depth)
    else:
        services._rerun_deeper(workspace_id, case_id, depth)
    return "done"


@app.task(name="probity.followups")
def followups() -> int:
    from probity import services

    return services.check_followups()
