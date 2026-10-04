"""Upload safety (Security.md §6): active-content rejection and ClamAV scanning before anything is stored."""

from __future__ import annotations

import re
import socket
import struct

from probity.config import get_settings
from probity.ingestion.parse import UnsupportedDocument

# PDF features that execute or embed content. Legitimate invoices don't need them. A plain alternation is linear;
# an /OpenAction→JavaScript action is caught by /JavaScript (or /JS) itself.
_ACTIVE_PDF = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA|AA)\b")
# PDF names may hex-escape characters ("/J#61vaScript" is /JavaScript); decode them before matching.
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")


class InfectedFile(UnsupportedDocument):
    pass


def reject_active_content(data: bytes, mime: str) -> None:
    if mime == "message/rfc822":  # an emailed invoice's PDF attachment gets the same check
        from probity.ingestion.parse import email_attachment_pdf

        data, mime = email_attachment_pdf(data) or b"", "application/pdf"
    if mime != "application/pdf":
        return
    m = _ACTIVE_PDF.search(_NAME_ESCAPE.sub(lambda e: bytes([int(e.group(1), 16)]), data))
    if m:
        raise UnsupportedDocument(f"PDF contains active content ({m.group(1).decode(errors='replace')}); re-export it as a plain PDF")


def clamav_scan(data: bytes) -> str:
    """Scan via clamd INSTREAM. Returns 'OK' or raises InfectedFile. Skipped (returns 'SKIPPED') when no
    CLAMAV_HOST is configured — production refuses to start without one."""
    st = get_settings()
    if not st.clamav_host:
        return "SKIPPED"
    with socket.create_connection((st.clamav_host, st.clamav_port), timeout=30) as sock:
        sock.sendall(b"zINSTREAM\0")
        for i in range(0, len(data), 64 * 1024):
            chunk = data[i : i + 64 * 1024]
            sock.sendall(struct.pack("!L", len(chunk)) + chunk)
        sock.sendall(struct.pack("!L", 0))
        reply = b""
        while not reply.endswith(b"\0"):
            part = sock.recv(4096)
            if not part:
                break
            reply += part
    text = reply.rstrip(b"\0").decode(errors="replace")
    if text.endswith("OK"):
        return "OK"
    if "FOUND" in text:
        raise InfectedFile(f"upload rejected by antivirus: {text.split(':', 1)[-1].replace('FOUND', '').strip()}")
    raise UnsupportedDocument(f"antivirus scan failed: {text}")
