"""Agent 2 — Document Intelligence: "What does this invoice contain?"

Deterministic labelled-field parser first; LLM extraction only fills gaps in live mode, and an LLM value
is accepted only if its raw text appears verbatim in the document.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from pydantic import BaseModel, Field

from probity import storage
from probity.agents.common import load_case, record_claim, today
from probity.db.models import Document
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.guardrails import crypto
from probity.guardrails.text import detect_injection, wrap_untrusted
from probity.ingestion.parse import _OCR_MARK as OCR_MARK
from probity.ingestion.parse import OCR_CONFIDENCE_PENALTY, extract, low_confidence, parse_fields
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
    out = llm.generate(
        schema=LLMExtraction,
        system=system,
        user=f"Fields needed: {missing}\n" + wrap_untrusted("invoice", text),
        tier="fast",
        tags={**ctx.tags, "agent": AGENT, "prompt_version": pv},
        mock=lambda: LLMExtraction(),
        budget=ctx.budget,
    )
    for f in out.fields:
        if f.name in missing and f.raw and f.raw in text:  # grounding: must appear verbatim
            val = parse_money_minor(f.raw) if f.name in ("subtotal", "tax", "total") else f.raw
            fields[f.name] = {"value": val, "raw": f.raw, "confidence": min(f.confidence, 0.85), "evidence_snippet": (f.evidence_snippet or f.raw)[:300], "page": 1, "via": "llm"}


def run(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Reading invoice")
    with session_scope() as s:
        case = load_case(s, ctx)
        doc = s.get(Document, case.document_id)
        assert doc is not None
        data = storage.get(doc.storage_path)
        mime = doc.mime
        corrections = case.corrections or {}

    ctx.progress(AGENT, "Extracting text layer")
    pages, used_ocr = extract(data, mime)
    pages = [p for p in pages if p != OCR_MARK]
    if used_ocr:
        ctx.progress(AGENT, "No text layer — running OCR")
    text = "\n".join(pages)
    fields = parse_fields(pages)
    if used_ocr:
        for f in fields.values():
            f["confidence"] = round(max(0.0, f.get("confidence", 0) - OCR_CONFIDENCE_PENALTY), 2)
            f["via"] = "ocr"

    missing = low_confidence(fields)
    if missing and ctx.budget is not None:
        ctx.progress(AGENT, f"Filling {len(missing)} low-confidence fields")
        _llm_fill(ctx, text, fields, missing)

    for k, v in corrections.items():  # human corrections win, and are marked as such
        fields[k] = {**fields.get(k, {}), "value": v, "raw": str(v), "confidence": 1.0, "via": "human_correction"}

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

    with session_scope() as s:
        case = load_case(s, ctx)
        case.extraction = fields
        case.validation = validation
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
