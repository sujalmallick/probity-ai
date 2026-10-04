"""Action Agent (post-gate): neutral verification email drafts + vendor reply analysis (Feature F9, G11).

Reply assertions about bank details or identity are recorded as claims with the reply text as evidence,
and stay UNVERIFIED (0 points) until an approver records an out-of-band confirmation.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from probity.agents.common import fv, vendor_bundle
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.guardrails.text import language_violations, neutralize_language, wrap_untrusted
from probity.ingestion.validators import format_inr, normalize_domain
from probity.llm import client as llm

AGENT = "action"


class DraftOut(BaseModel):
    subject: str = Field(max_length=300)
    body: str = Field(max_length=4000)
    requested_items: list[str] = Field(default_factory=list)


def draft_email(s, ctx: CaseCtx, case) -> dict:  # type: ignore[no-untyped-def]
    ex = case.extraction
    b = vendor_bundle(s, ctx.workspace_id, case.vendor_id)
    verified = [c for c in b["contacts"] if c.verified]
    if verified:
        to, recipient_verified, contact_name = verified[0].email, True, verified[0].name or "Accounts Team"
    else:
        to, recipient_verified, contact_name = (fv(ex, "vendor_email") or ""), False, "Accounts Team"
    vendor_name = b["vendor"].name if b["vendor"] else (fv(ex, "vendor_name") or "Vendor")
    inv_no, total = fv(ex, "invoice_number"), fv(ex, "total")
    po = fv(ex, "po_number")
    items = [
        "Confirmation of the bank account to be used for this payment, from your registered accounts contact",
        "A bank letter or cancelled cheque on company letterhead for that account",
        f"The purchase order reference for invoice {inv_no}" + (f" (we have {po})" if po else ""),
    ]

    def mock() -> DraftOut:
        body = (
            f"Dear {contact_name},\n\n"
            f"We are processing invoice {inv_no} dated {fv(ex, 'invoice_date')} for {format_inr(total)}. "
            "As part of our routine verification of payment details, we would be grateful if you could confirm the following:\n\n"
            + "\n".join(f"  {i + 1}. {it}" for i, it in enumerate(items))
            + "\n\nPlease reply from your registered email address. We will also call your accounts team on the number we have on file.\n\n"
            "Thank you for your help.\n\nAccounts Payable\nProbity Demo Traders"
        )
        return DraftOut(subject=f"Routine verification of payment details — Invoice {inv_no}", body=body, requested_items=items)

    pv, system = llm.load_prompt("action", "draft")
    out = llm.generate(schema=DraftOut, system=system, user=f"Vendor: {vendor_name}\nInvoice: {inv_no}\nAmount: {format_inr(total)}\nPO: {po}",
                       tier="fast", tags={**ctx.tags, "agent": AGENT, "prompt_version": pv}, mock=mock, budget=ctx.budget)
    # G2/G11: drafts must pass the language filter; violations are rewritten, never sent as-is.
    body, subject = out.body, out.subject
    if language_violations(body + " " + subject):
        body, subject = neutralize_language(body), neutralize_language(subject)
    return {"to_email": to, "recipient_verified": recipient_verified, "subject": subject, "body": body, "requested_items": out.requested_items}


_URGENCY = re.compile(r"(?i)\b(urgent(?:ly)?|immediately|asap|today itself|within (?:the )?hour|right away)\b")
_NEW_INSTR = re.compile(r"(?i)\b(pay(?:ment)? to (?:the )?new|use (?:the )?new account|change(?:d)? (?:our|the) (?:bank|account))\b")
_SENTENCES = re.compile(r".+?(?:[.!?](?=\s|$)|\n|$)")  # sentence ends at punctuation followed by space, so domains stay whole


class ReplyAnalysis(BaseModel):
    statements: list[dict] = Field(default_factory=list)
    indicators: list[str] = Field(default_factory=list)


def analyze_reply(ctx: CaseCtx, case, bundle: dict, from_email: str, body: str) -> tuple[list[AgentClaim], list[str]]:  # type: ignore[no-untyped-def]
    sender_dom = normalize_domain(from_email)
    contact_doms = {normalize_domain(c.email) for c in bundle["contacts"] if c.verified}
    verified_doms = {d.domain for d in bundle["domains"] if d.verified} | contact_doms
    ex = case.extraction
    inv_last4 = (ex.get("bank_account") or {}).get("last4")
    inv_dom = fv(ex, "sender_domain")

    def mock() -> ReplyAnalysis:
        stmts: list[dict] = []
        for sent in (m.group(0).strip() for m in _SENTENCES.finditer(body)):
            if not sent:
                continue
            low = sent.lower()
            if re.search(r"(?i)\b(bank|account|a/c|ifsc)\b", sent) and re.search(r"(?i)\b(new|changed|change|moved|switched|updated|confirm)\b", sent):
                m = re.search(r"(\d{4})\b(?!.*\d{4}\b)", sent)
                stmts.append({"kind": "bank", "quote": sent, "value": m.group(1) if m else inv_last4})
            elif re.search(r"(?i)\b(domain|email address|e-mail|website)\b", low) or (inv_dom and inv_dom in low):
                dm = re.search(r"\b([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:in|com|co|net|org))\b", low)
                stmts.append({"kind": "domain", "quote": sent, "value": dm.group(1) if dm else inv_dom})
        ind = []
        if sender_dom and verified_doms and sender_dom not in verified_doms:
            ind.append(f"Reply sent from {sender_dom}, which is not a verified domain for this vendor ({', '.join(sorted(verified_doms))})")
        if _URGENCY.search(body):
            ind.append("Urgency pressure in reply: " + _URGENCY.search(body).group(0))  # type: ignore[union-attr]
        if _NEW_INSTR.search(body):
            ind.append("Reply contains new payment instructions")
        return ReplyAnalysis(statements=stmts, indicators=ind)

    pv, system = llm.load_prompt("action", "reply")
    out = llm.generate(schema=ReplyAnalysis, system=system, user=wrap_untrusted("vendor_reply", f"From: {from_email}\n\n{body}"),
                       tier="fast", tags={**ctx.tags, "agent": AGENT, "prompt_version": pv}, mock=mock, budget=ctx.budget)
    claims = []
    for st in out.statements:
        if st.get("quote", "") not in body:  # quotes must be verbatim from the reply
            continue
        kind = st.get("kind")
        label = "bank account" if kind == "bank" else "domain"
        val = f"XXXX{st['value']}" if kind == "bank" and st.get("value") else st.get("value")
        claims.append(AgentClaim(
            claim=f"Vendor reply states the {label} {val or ''} is theirs (unverified until confirmed out-of-band).".replace("  ", " "),
            evidence=[EvidenceIn(source="vendor_reply", field=kind, value=val, source_ref=f"email:{from_email}", excerpt=st["quote"][:1000], tier=2 if not out.indicators else 3)],
            confidence=0.5, severity="info", assertion={"op": "requires_out_of_band"},
            data={"kind": kind, "value": val, "reply_from": from_email},
        ))
    return claims, out.indicators
