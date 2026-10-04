"""Upload safety (Security.md §6): every PDF is parsed and rewritten without active content before it is stored
(clean_pdf); a ClamAV virus scan is added only when CLAMAV_HOST is configured (optional: it needs 1-3 GB of RAM)."""

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
    CLAMAV_HOST is configured; it is optional because every PDF is already cleaned by clean_pdf."""
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


# ---------------------------------------------------------------- PDF cleaning (replaces a resident virus scanner)

# Action types that run code, open other files or programs, or send data somewhere.
_ACTIVE_ACTIONS = {"/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToE", "/RichMediaExecute", "/Rendition", "/Sound", "/Movie"}
_ACTIVE_KEYS = {"/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/EmbeddedFiles", "/RichMedia", "/XFA", "/AA"}
_ACTIVE_TYPES = {"/EmbeddedFile", "/RichMedia", "/RichMediaExecute"}
_RISKY_ANNOTS = {"/FileAttachment", "/Movie", "/Sound", "/Screen", "/RichMedia", "/3D"}


def _pdf_problems(pdf) -> list[str]:  # type: ignore[no-untyped-def]
    """Active content anywhere in the parsed object graph, including inside compressed object streams, which a
    byte-level search can't see."""
    import pikepdf

    found: set[str] = set()
    for obj in pdf.objects:
        d = obj.stream_dict if isinstance(obj, pikepdf.Stream) else obj
        if not isinstance(d, pikepdf.Dictionary):
            continue
        for key in d.keys():
            if key in _ACTIVE_KEYS:
                found.add(key.lstrip("/"))
        if d.get("/S") is not None and str(d.get("/S")) in _ACTIVE_ACTIONS:
            found.add(str(d.get("/S")).lstrip("/"))
        if d.get("/Type") is not None and str(d.get("/Type")) in _ACTIVE_TYPES:
            found.add(str(d.get("/Type")).lstrip("/"))
        if d.get("/Subtype") is not None and str(d.get("/Subtype")) in _RISKY_ANNOTS and d.get("/Type") in (None, pikepdf.Name("/Annot")):
            found.add(str(d.get("/Subtype")).lstrip("/"))
    return sorted(found)


def clean_pdf(data: bytes) -> tuple[bytes, list[str]]:
    """Rewrite an uploaded PDF so it can't do anything but show pages, and return (clean bytes, what was removed).

    Refused (415): PDFs that need a password, can't be parsed, or carry active content (JavaScript, launch actions,
    embedded files, rich media, XFA forms, auto-actions), wherever it sits in the file. Removed quietly: fill-in form
    fields, link and auto-open actions, and encryption. The result is written without compressed object streams, so
    every later check sees all of it. Only the clean copy is stored and ever served."""
    import io

    import pikepdf

    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError as e:
        raise UnsupportedDocument("This PDF is password-protected. Upload a copy without a password.") from e
    except Exception as e:  # noqa: BLE001 - any parse failure is a refusal, never a guess
        raise UnsupportedDocument("The PDF could not be opened. Re-export it from the original program and upload again.") from e
    with pdf:
        problems = _pdf_problems(pdf)
        if problems:
            raise UnsupportedDocument(f"PDF contains active content ({', '.join(problems)}); re-export it as a plain PDF")
        removed: list[str] = []
        root = pdf.Root
        for key in ("/OpenAction", "/AcroForm", "/Names", "/URI"):
            if key in root:
                del root[key]
                removed.append(key.lstrip("/"))
        for page in pdf.pages:
            annots = page.obj.get("/Annots")
            if annots is None:
                continue
            for a in list(annots):
                if isinstance(a, pikepdf.Dictionary) and "/A" in a:
                    del a["/A"]  # links keep their box but no longer go anywhere
                    if "link actions" not in removed:
                        removed.append("link actions")
        out = io.BytesIO()
        pdf.save(out, object_stream_mode=pikepdf.ObjectStreamMode.disable, encryption=False, fix_metadata_version=False)
    clean = out.getvalue()
    reject_active_content(clean, "application/pdf")  # second, independent check on the rewritten file
    return clean, removed


def check_upload(data: bytes, mime: str) -> tuple[bytes, list[str]]:
    """What gets stored for an upload: a cleaned PDF, or an email whose PDF attachment passed the same structural
    check (the email itself is never rendered, only read as text and offered as a download)."""
    if mime == "application/pdf":
        return clean_pdf(data)
    if mime == "message/rfc822":
        from probity.ingestion.parse import email_attachment_pdf

        reject_active_content(data, mime)
        attachment = email_attachment_pdf(data)
        if attachment:
            clean_pdf(attachment)  # raises if the attachment is hostile
        return data, []
    return data, []
