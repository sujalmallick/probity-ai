"""Notifications: in-app for every eligible user, plus email when an email backend is configured."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.config import get_settings
from probity.db.models import Notification, User
from probity.logging import get_logger

log = get_logger("notify")
ROLES = ["viewer", "accountant", "approver", "owner"]


def notify(s: Session, workspace_id: str, min_role: str, kind: str, title: str, body: str = "", case_id: str | None = None,
           exclude_user: str | None = None) -> int:
    users = [u for u in s.scalars(select(User).where(User.workspace_id == workspace_id, User.active.is_(True)))
             if ROLES.index(u.role) >= ROLES.index(min_role) and u.id != exclude_user]
    for u in users:
        s.add(Notification(workspace_id=workspace_id, user_id=u.id, kind=kind, title=title[:300], body=body[:4000], case_id=case_id))
    s.flush()
    st = get_settings()
    if st.email_backend != "outbox" and users:
        from probity import mailer

        link = f"{st.public_app_url.rstrip('/')}/cases/{case_id}" if case_id else st.public_app_url
        for u in users:
            try:
                mailer._send(u.email, f"Probity: {title}", f"{body}\n\nOpen: {link}\n\n— Probity · Evidence before payment.")
            except mailer.MailError as e:  # in-app notification still stands
                log.warning("notify.email_failed", kind=kind, error=str(e))
    return len(users)
