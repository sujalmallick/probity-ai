"""Outbound email through Resend (Feature F9, Security.md §7).

- Not configured (RESEND_API_KEY / EMAIL_FROM missing) → nothing is sent and the caller gets a clear error.
- While EMAIL_SEND_TO_ANY is false (the default), only addresses in EMAIL_ALLOWLIST can receive mail. Anything
  else is blocked before it reaches Resend and logged; callers that have a session also write it to the audit log.
- Verification emails carry Reply-To: case+<case_id>@EMAIL_REPLY_DOMAIN so the inbound webhook can route the
  vendor's reply back to its case. Sending is only ever triggered by an approver approving a draft.
"""

from __future__ import annotations

import httpx

from probity.config import get_settings
from probity.logging import get_logger

log = get_logger("mailer")


class MailError(RuntimeError):
    pass


class MailNotConfigured(MailError):
    pass


class MailBlocked(MailError):
    """The recipient is not on EMAIL_ALLOWLIST and sending to any address has not been enabled."""


def masked(addr: str) -> str:
    local, _, domain = (addr or "").partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def reply_address(case_id: str) -> str | None:
    dom = get_settings().email_reply_domain
    return f"case+{case_id}@{dom}" if dom else None


def configured() -> bool:
    st = get_settings()
    return bool(st.resend_api_key and st.email_from)


def check_recipient(to: str) -> None:
    st = get_settings()
    if not configured():
        raise MailNotConfigured("Email isn't configured (set RESEND_API_KEY and EMAIL_FROM in apps/api/.env)")
    if not st.email_send_to_any and (to or "").strip().lower() not in st.email_allowlist_set:
        log.warning("mail.blocked_not_allowlisted", to=masked(to))
        raise MailBlocked(f"{to} is not on EMAIL_ALLOWLIST; sending to other addresses is disabled until EMAIL_SEND_TO_ANY=true")


def _send(to: str, subject: str, text: str, reply_to: str | None = None, tags: dict | None = None) -> str:
    check_recipient(to)
    st = get_settings()
    payload: dict = {"from": st.email_from, "to": [to], "subject": subject, "text": text}
    if reply_to:
        payload["reply_to"] = reply_to
    if tags:
        payload["tags"] = [{"name": k, "value": str(v)[:256]} for k, v in tags.items()]
    try:
        r = httpx.post("https://api.resend.com/emails", json=payload, headers={"Authorization": f"Bearer {st.resend_api_key}"}, timeout=15)
    except httpx.HTTPError as e:
        raise MailError(f"email provider unreachable ({type(e).__name__})") from e
    if r.status_code in (401, 403):
        raise MailError("email provider rejected the API key or sender (check RESEND_API_KEY and EMAIL_FROM's domain)")
    if r.status_code >= 300:
        try:
            detail = str(r.json().get("message", ""))[:200]
        except ValueError:
            detail = ""
        raise MailError(f"email provider rejected the message ({r.status_code}){': ' + detail if detail else ''}")
    return str(r.json().get("id", ""))


def send_case_email(case_id: str, to: str, subject: str, body: str) -> str:
    mid = _send(to, subject, body, reply_to=reply_address(case_id), tags={"case": case_id})
    log.info("mail.sent", case_id=case_id, to=masked(to))
    return mid


def send_invitation(email: str, inviter: str, role: str) -> str | None:
    """Returns None when sent, otherwise why not (the invitation itself is stored either way)."""
    st = get_settings()
    try:
        _send(email, f"{inviter} invited you to Probity",
              f"{inviter} invited you to join their Probity workspace as {role}.\n\nSign in with this email address at {st.public_app_url} to accept.\n\nProbity — Evidence before payment.")
    except MailError as e:
        log.warning("mail.invitation_not_sent", error=str(e))
        return str(e)
    return None
