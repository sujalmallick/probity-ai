"""Agent 2 — Document Intelligence: "What does this invoice contain?"

Deterministic labelled-field parser first; the AI only fills low-confidence gaps, and an AI value is accepted
only if its raw text appears verbatim in the document. If the AI can't help, the fields stay flagged for review
and the timeline says why.
"""

from __future__ import annotations

import base64
import re

from pydantic import BaseModel, Field

from probity import storage
from probity.agents.common import load_case, record_claim, today
from probity.db.audit import audit
from probity.db.models import Document
from probity.db.session import session_scope
from probity.events import CaseCtx, rule_based_fallback
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.guardrails import crypto
from probity.guardrails.text import detect_injection, wrap_untrusted
from probity.ingestion.parse import CORRECTABLE_FIELDS, email_attachment_pdf, email_meta, extract, extract_tables, low_confidence, parse_fields
from probity.ingestion.validators import normalize_domain
from probity.ingestion.validators import parse_money_minor, validate_extraction
from probity.llm import client as llm

AGENT = "document"


class LLMField(BaseModel):
    name: str
    raw: str | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_snippet: str | None = None


class LLMExtraction(BaseModel):
    fields: list[LLMField] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


def _llm_fill(ctx: CaseCtx, text: str, fields: dict, missing: list[str]) -> None:
    pv, system = llm.load_prompt("document", "extract")
    try:
        out = llm.generate(
            schema=LLMExtraction,
            system=system,
            user=f"Fields needed: {missing}\n" + wrap_untrusted("invoice", text),
            tier="fast",
            tags={**ctx.tags, "agent": AGENT, "prompt_version": pv},
            budget=ctx.budget,
        )
    except llm.LLMFailed as e:
        ctx.unverifiable(AGENT, "ai_extraction", f"{e.reason}; {', '.join(missing)} left for review")
        for name in missing:  # what the parser read stays, labelled; nothing is filled in
            if name in fields:
                fields[name]["fallback"] = rule_based_fallback(f"parser value, not double-checked by AI: {e.reason}")
        return
    for f in out.fields:
        if f.name in missing and f.raw and f.raw in text:  # grounding: must appear verbatim
            val = parse_money_minor(f.raw) if f.name in ("subtotal", "tax", "total") else f.raw
            fields[f.name] = {"value": val, "raw": f.raw, "confidence": min(f.confidence, 0.85), "evidence_snippet": (f.evidence_snippet or f.raw)[:300], "page": 1, "via": "llm"}


def extract_document(ctx: CaseCtx, data: bytes, mime: str, corrections: dict) -> tuple[dict, dict, str, list[str]]:
    """Text → fields → corrections → bank-number protection → deterministic validation → injection scan.
    Shared by the investigation and the pre-launch preview."""
    ctx.progress(AGENT, "Extracting text layer")
    pages, _ = extract(data, mime)
    text = "\n".join(pages)
    pdf_bytes = email_attachment_pdf(data) if mime == "message/rfc822" else (data if mime == "application/pdf" else None)
    fields = parse_fields(pages, extract_tables(pdf_bytes, "application/pdf") if pdf_bytes else None)
    if mime == "message/rfc822":
        # The envelope sender is what matters for spoofing — it overrides any email printed on the invoice.
        meta = email_meta(data)
        if meta["from"]:
            fields["sender_domain"] = {"value": normalize_domain(meta["from"]), "raw": meta["from"], "confidence": 0.99,
                                       "evidence_snippet": f"From: {meta['from']}", "page": 0, "via": "email_header"}
            fields.setdefault("vendor_email", {"value": meta["from"], "raw": meta["from"], "confidence": 0.95, "evidence_snippet": f"From: {meta['from']}", "page": 0})
        if meta["dkim"] and meta["dkim"] != "pass":
            fields["email_dkim"] = {"value": meta["dkim"], "raw": meta["dkim"], "confidence": 0.99, "evidence_snippet": f"dkim={meta['dkim']}", "page": 0}

    missing = low_confidence(fields)
    if missing and ctx.budget is not None:
        ctx.progress(AGENT, f"Filling {len(missing)} low-confidence fields")
        _llm_fill(ctx, text, fields, missing)

    for k, v in corrections.items():  # human corrections win, are marked as such, and keep what the document said
        if k not in CORRECTABLE_FIELDS:  # defence in depth: the API already refuses these
            continue
        before = fields.get(k)
        if before is not None and str(before.get("value", "")).strip() == str(v).strip():
            continue  # unchanged value: not a correction
        original = {kk: before.get(kk) for kk in ("value", "raw", "confidence")} if before else None
        fields[k] = {**(before or {}), "value": v, "raw": str(v), "confidence": 1.0, "via": "human_correction", "original": original}

    # Protect bank account: keep last4 + HMAC + ciphertext, never the raw number in case JSON.
    if "bank_account" in fields:
        raw = str(fields["bank_account"]["value"])
        l4 = crypto.last4(raw)
        snippet = fields["bank_account"].get("evidence_snippet") or ""
        fields["bank_account"] = {
            **fields["bank_account"],
            "value": crypto.mask(l4),
            "raw": crypto.mask(l4),
            "evidence_snippet": re.sub(r"\d{5,}", lambda m: crypto.mask(m.group(0)[-4:]), snippet),
            "last4": l4,
            "hmac": crypto.account_hmac(raw),
            "enc": base64.b64encode(crypto.encrypt(raw)).decode(),
        }

    ctx.progress(AGENT, "Validating GSTIN checksum, IFSC and arithmetic")
    validation = validate_extraction(fields, today())
    injection = detect_injection(text)
    validation["injection"] = {"ok": not injection, "detail": injection}
    if injection:
        ctx.progress(AGENT, "Instruction-like text found in document — neutralized and flagged")

    return fields, validation, text, injection


class PreviewCtx(CaseCtx):
    """Extraction preview before a case exists: same pipeline, no events."""

    def emit(self, type_: str, **kw) -> None:  # type: ignore[no-untyped-def, override]
        return None


def run(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Reading invoice")
    with session_scope() as s:
        case = load_case(s, ctx)
        doc = s.get(Document, case.document_id)
        assert doc is not None
        data = storage.get(doc.storage_path)
        mime = doc.mime
        corrections = case.corrections or {}

    fields, validation, text, injection = extract_document(ctx, data, mime, corrections)

    with session_scope() as s:
        case = load_case(s, ctx)
        case.extraction = fields
        case.validation = validation
        corrected = {k: f for k, f in fields.items() if f.get("via") == "human_correction"}
        if corrected:
            audit(s, ctx.workspace_id, "system", "case.corrections_applied", case.id,
                  {k: {"before": (f.get("original") or {}).get("raw"), "after": f.get("raw")} for k, f in corrected.items()})
        total = (fields.get("total") or {}).get("value")
        case.amount_minor = total if isinstance(total, int) else None
        for hit in injection[:3]:
            record_claim(s, ctx, AGENT, AgentClaim(
                claim="Document contains instruction-like text addressed to a reader or automated system.",
                signal="suspicious_instruction_in_document",
                evidence=[EvidenceIn(source="invoice", field="document_text", value=None, source_ref="invoice", excerpt=hit, tier=1)],
                confidence=0.9, severity="warn", assertion={"op": "quote", "evidence": "$0", "quote": hit},
                data={"observed": hit[:120]},
            ))
    low = low_confidence(fields)
    ctx.emit("agent.completed", agent=AGENT, status="done", message=f"{len(fields)} fields extracted" + (f", {len(low)} need review" if low else ""), data={"low_confidence": low})
    return {"extraction": fields, "validation": validation, "text": text}
