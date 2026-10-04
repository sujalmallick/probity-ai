"""Celery worker: investigations run here when TASK_BACKEND=celery (production).

    celery -A probity.worker worker -B --loglevel=INFO --concurrency=4      (Linux / Docker)
    celery -A probity.worker worker -B --pool=solo --loglevel=INFO          (Windows dev)

Tasks are acked late and re-queued if a worker dies; each task checks the case status first, so a
redelivered message never re-runs a finished investigation.
"""

from __future__ import annotations

from datetime import datetime, timezone

from celery import Celery
from sqlalchemy import select

from probity.config import get_settings
from probity.logging import configure_logging, get_logger

configure_logging()
log = get_logger("worker")
_settings = get_settings()

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
    """Flag verification emails whose follow-up date passed without a vendor reply (never auto-sends)."""
    from probity.db.models import Case, Draft, Workspace
    from probity.db.session import session_scope
    from probity.events import emit

    now = datetime.now(timezone.utc)
    flagged = 0
    with session_scope() as s:
        workspaces = [w.id for w in s.scalars(select(Workspace))]
    for ws in workspaces:
        with session_scope(ws) as s:
            due = s.scalars(select(Draft).where(Draft.workspace_id == ws, Draft.status == "sent", Draft.followup_at <= now))
            for d in due:
                case = s.get(Case, d.case_id)
                if case is None or case.status != "AWAITING_VENDOR":
                    continue
                d.status = "followup_due"
                from probity.notify import notify

                notify(s, ws, "approver", "followup_due", f"No vendor reply on case #{case.number}",
                       f"Verification email to {d.to_email} sent {d.sent_at:%Y-%m-%d} has no reply. Call the known contact or send a reminder.", d.case_id)
                flagged += 1
                emit(ws, d.case_id, "action.sent", agent="action", status="waiting",
                     message=f"No reply from {d.to_email} since {d.sent_at:%Y-%m-%d}; follow-up due — draft a reminder or call the known contact")
    log.info("followups.checked", flagged=flagged)
    return flagged
