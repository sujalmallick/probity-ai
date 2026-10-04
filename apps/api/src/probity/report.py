"""Case report PDF (Feature F14): what was found, the evidence for each point, who decided what."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from probity.ingestion.validators import format_inr

TIER_COLOR = {"LOW": "#1f8a4c", "MEDIUM": "#b26b00", "HIGH": "#c62f2f", "CRITICAL": "#8a1020"}


def _inr(minor: int | None) -> str:
    return format_inr(minor).replace("₹", "INR ")


_ASCII = str.maketrans({"—": "-", "–": "-", "·": "|", "→": "->", "“": '"', "”": '"', "’": "'", "₹": "INR "})


def _a(text: Any) -> str:
    """Built-in PDF fonts cover WinAnsi only; keep punctuation portable."""
    return str(text if text is not None else "-").translate(_ASCII)


def _p(text: Any, style) -> Paragraph:  # type: ignore[no-untyped-def]
    return Paragraph(escape(_a(text)).replace("\n", "<br/>"), style)


def build_case_pdf(case: dict, evidence: list[dict], audit: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Probity case #{case['number']}", author="Probity")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=10, textColor=colors.HexColor("#5d6573"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4)
    risk = case.get("risk") or {}
    tier = risk.get("tier", "—")
    ev = {e["id"]: e for e in evidence}
    claims = {c["id"]: c for c in case.get("claims", [])}
    out: list[Any] = []

    out.append(Paragraph("Probity - Evidence before payment.", small))
    out.append(Paragraph(escape(_a(f"Case #{case['number']} | {case.get('invoice_number') or ''} | {case.get('vendor_name') or 'Unknown vendor'}")), ss["Title"]))
    meta = [
        ["Amount", _inr((case.get("amount") or {}).get("amount_minor")), "Status", case["status"].replace("_", " ").title()],
        ["Risk", f"{risk.get('score', '-')}/100 | {tier}", "Weights", risk.get("weights_version", "-")],
        ["Recommendation", (case.get("recommendation") or {}).get("action", "-").replace("_", " "), "Outcome", case.get("outcome") or "open"],
    ]
    t = Table(meta, colWidths=[28 * mm, 60 * mm, 22 * mm, 60 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5d6573")),
                           ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#5d6573")), ("TEXTCOLOR", (1, 1), (1, 1), colors.HexColor(TIER_COLOR.get(tier, "#14171c"))),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    out += [t, Spacer(1, 4), _p(case.get("summary"), body)]

    out.append(Paragraph("Score contributions (computed by code from verified claims)", h2))
    rows = [["Signal", "Points", "Status", "Baseline → observed"]]
    for c in risk.get("contributions", []):
        rows.append([_p(c["label"], body), str(c["points"]), c["status"], _p(f"{c.get('baseline') or '-'} -> {c.get('observed') or '-'}", body)])
    t = Table(rows, colWidths=[52 * mm, 16 * mm, 24 * mm, 86 * mm], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8.5), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef0fd")),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e3e6eb")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    out.append(t)

    out.append(Paragraph("Evidence for each counted point", h2))
    for c in risk.get("contributions", []):
        cl = claims.get(c.get("claim_id") or "")
        if not cl or c["points"] <= 0:
            continue
        out.append(_p(f"{c['label']} (+{c['points']}): {cl['statement']}", body))
        out.append(_p(f"Verifier: {cl.get('verifier_notes')}", small))
        for eid in cl.get("evidence_ids", []):
            e = ev.get(eid)
            if e:
                val = e.get("value")
                val = _inr(val) if isinstance(val, int) and e.get("field") and any(k in e["field"] for k in ("price", "total", "amount")) else val
                out.append(_p(f"• [{e['source']}, tier {e['tier']}] {e.get('field') or ''} = {val} — {e.get('source_ref')}" + (f" — “{e['excerpt'][:240]}”" if e.get("excerpt") else ""), small))
        out.append(Spacer(1, 4))

    if case.get("decisions"):
        out.append(Paragraph("Human decisions", h2))
        for d in case["decisions"]:
            out.append(_p(f"{d['at'][:19].replace('T', ' ')} — {d['decision'].replace('_', ' ')} by {d['actor_id']}: {d['reason']}", body))
    if case.get("messages"):
        out.append(Paragraph("Vendor communication", h2))
        for m in case["messages"]:
            out.append(_p(f"{m['at'][:19].replace('T', ' ')} — {'sent to' if m['direction'] == 'out' else 'received from'} {m['to'] if m['direction'] == 'out' else m['from']}: {m['subject']}", body))
    if case.get("score_history"):
        out.append(Paragraph("Score history", h2))
        out.append(_p(" → ".join(f"{h['score']} ({h['reason'].replace('_', ' ')})" for h in case["score_history"]), body))

    out.append(Paragraph("Audit trail", h2))
    out.append(_p(f"{len(audit.get('items', []))} entries · hash chain {'verified intact' if audit.get('chain_valid') else 'BROKEN at entry ' + str(audit.get('first_bad_id'))}", body))
    out.append(Spacer(1, 10))
    out.append(Paragraph(
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Probity identifies anomalies and recommends actions for human review; "
        "it does not determine fraud and never executes payments. Account numbers are masked.", small))
    doc.build(out)
    return buf.getvalue()
