"""Outbound email (Feature F9, Security.md §7).

EMAIL_BACKEND:
  outbox  store only (the in-app simulated vendor inbox) — default, offline
  resend  Resend HTTP API (RESEND_API_KEY)
  smtp    any SMTP relay with STARTTLS (SES, Postmark, Gmail Workspace, ...)

Verification emails carry Reply-To: case+<case_id>@EMAIL_REPLY_DOMAIN so the inbound webhook can route the
vendor's reply back to its case. Sending is only ever triggered by an approver approving a draft.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import make_msgid

import httpx

from probity.config import get_settings
from probity.logging import get_logger

log = get_logger("mailer")


class MailError(RuntimeError):
    pass


def reply_address(case_id: str) -> str | None:
    dom = get_settings().email_reply_domain
    return f"case+{case_id}@{dom}" if dom else None


def _send(to: str, subject: str, text: str, reply_to: str | None = None, tags: dict | None = None) -> str:
    st = get_settings()
    if st.email_backend == "outbox":
        return make_msgid(domain="probity.local")
    if st.email_backend == "resend":
        payload = {"from": st.email_from, "to": [to], "subject": subject, "text": text}
        if reply_to:
            payload["reply_to"] = reply_to
        if tags:
            payload["tags"] = [{"name": k, "value": str(v)[:256]} for k, v in tags.items()]
        try:
            r = httpx.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {st.resend_api_key}"}, timeout=15)
        except httpx.HTTPError as e:
            raise MailError(f"email provider unreachable: {e}") from e
        if r.status_code >= 300:
            raise MailError(f"email provider rejected the message ({r.status_code}): {r.text[:200]}")
        return r.json().get("id", "")
    # smtp
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = st.email_from, to, subject
    msg["Message-ID"] = make_msgid()
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    try:
        with smtplib.SMTP(st.smtp_host or "localhost", st.smtp_port, timeout=15) as smtp:
            smtp.starttls()
            if st.smtp_username:
                smtp.login(st.smtp_username, st.smtp_password or "")
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"SMTP send failed: {e}") from e
    return msg["Message-ID"]


def send_case_email(case_id: str, to: str, subject: str, body: str) -> str:
    mid = _send(to, subject, body, reply_to=reply_address(case_id), tags={"case": case_id})
    log.info("mail.sent", case_id=case_id, backend=get_settings().email_backend)
    return mid


def send_invitation(email: str, inviter: str, role: str) -> None:
    st = get_settings()
    if st.email_backend == "outbox":
        return
    try:
        _send(email, f"{inviter} invited you to Probity",
              f"{inviter} invited you to join their Probity workspace as {role}.\n\nSign in with this email address at {st.public_app_url} to accept.\n\nProbity — Evidence before payment.")
    except MailError as e:  # the invitation itself is stored; the owner can share the link manually
        log.warning("mail.invitation_failed", error=str(e))
