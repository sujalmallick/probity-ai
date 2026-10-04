"""Agent 5 — Transaction Analyst: "Is the transaction abnormal vs our data?"

All signals are computed by deterministic code (signals/detectors.py). This agent only packages each
fired signal as a claim with structured evidence: observed (invoice) vs baseline (our records).
"""

from __future__ import annotations

import statistics
from dataclasses import replace

from sqlalchemy import select

from probity.agents.common import fsnip, fv, load_case, record_claim, today, vendor_bundle
from probity.db.models import Case, HistoricalInvoice, PurchaseOrder
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.guardrails.crypto import mask
from probity.ingestion.validators import format_inr, normalize_invoice_number, parse_date
from probity.signals import detectors as d

AGENT = "transaction_analyst"


NOT_APPLICABLE = {"no bank account on invoice", "no sender domain on invoice"}


def _status(ctx: CaseCtx, check: str, res: d.SignalResult) -> dict:
    """A check that had nothing to compare against (no history, no bank records) is "could not verify" and is
    announced in the timeline; one that simply doesn't apply to this invoice is "skipped"."""
    if res.skipped and res.skipped not in NOT_APPLICABLE:
        return {**ctx.unverifiable(AGENT, check, res.skipped), "signal": res.signal}
    return {"status": "fired" if res.fired else ("skipped" if res.skipped else "passed"), "reason": res.skipped or "", "signal": res.signal}


def run(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Analyzing transaction against history")
    checks: dict[str, dict] = {}
    with session_scope() as s:
        case = load_case(s, ctx)
        ex = case.extraction
        b = vendor_bundle(s, ctx.workspace_id, case.vendor_id)
        all_rows = (
            list(s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.workspace_id == ctx.workspace_id, HistoricalInvoice.vendor_id == case.vendor_id).order_by(HistoricalInvoice.invoice_date)))
            if case.vendor_id else []
        )
        # Only approved history is a baseline (prices, totals, "no history"). Pending rows still count for duplicates.
        hist_rows = [h for h in all_rows if h.approved_at is not None]
        pending_hist = len(all_rows) - len(hist_rows)
        ctx.progress(AGENT, f"Searched {len(hist_rows)} approved historical invoices"
                     + (f" ({pending_hist} more waiting for approver approval are used only for duplicate checks)" if pending_hist else ""))
        history = [
            d.PastInvoice(h.invoice_number_norm, h.invoice_date, h.total_minor, h.bank_hmac, tuple((li["description"], li["qty"], li["unit_price_minor"]) for li in h.line_items))
            for h in hist_rows
        ]
        # Duplicate detection also looks at this vendor's other cases: open, awaiting a decision, auto-cleared or
        # approved but not closed yet. Paid history alone would let the same invoice be submitted again while the
        # first copy is still in flight. Price baselines stay on paid history only (`history` above).
        dup_refs = _duplicate_candidates(s, ctx, case, all_rows)
        if len(dup_refs) > len(all_rows):
            ctx.progress(AGENT, f"Also compared with {len(dup_refs) - len(all_rows)} other cases for this vendor")
        items = [(li["description"], li["qty"], li["unit_price_minor"]) for li in (fv(ex, "line_items") or [])]
        inv_no = fv(ex, "invoice_number")
        inv_date = parse_date(fv(ex, "invoice_date"))
        total = fv(ex, "total")
        bank = ex.get("bank_account") or {}
        currency = fv(ex, "currency")
        # History is in INR. Amounts in another (or an unstated) currency are never compared with it, and never converted.
        foreign = currency != "INR"
        foreign_reason = (f"invoice amounts are in {currency}; your history is in INR and Probity never converts" if currency
                          else "the invoice does not state its currency, so its amounts are not compared with your INR history")
        if foreign:
            total = None  # duplicate detection then matches on invoice number only

        # --- bank account
        ctx.progress(AGENT, "Comparing bank account with vendor history")
        known = [d.KnownAccount(a.last4, a.acct_hmac, a.verified) for a in b["accounts"]]
        res = d.bank_account_changed(bank.get("hmac"), bank.get("last4"), known)
        checks["bank_account_verification"] = _status(ctx, "bank_account_verification", res)
        if res.fired:
            base_accts = [a for a in b["accounts"] if a.verified] or b["accounts"]
            ev = [EvidenceIn(source="invoice", field="bank_account", value=mask(bank["last4"]), source_ref="invoice", excerpt=fsnip(ex, "bank_account"), tier=1, match_key=bank["hmac"])]
            for a in base_accts:
                seen = f"first seen {a.first_seen}, last seen {a.last_seen}" if a.first_seen else ""
                ev.append(EvidenceIn(source="vendor_history", field="bank_account", value=mask(a.last4), source_ref=f"vendor_bank_account:{a.id}", excerpt=f"Verified account {mask(a.last4)} ({seen})".strip(), tier=1, match_key=a.acct_hmac))
            record_claim(s, ctx, AGENT, AgentClaim(
                claim="Vendor bank account differs from historical records.",
                signal="bank_account_changed",
                evidence=ev,
                confidence=0.99, severity="high",
                assertion={"op": "not_in", "subject": "$0", "set": [f"${i}" for i in range(1, len(ev))]},
                data={"observed": mask(bank["last4"]), "baseline": ", ".join(mask(a.last4) for a in base_accts), "label_observed": "Current", "label_baseline": "Historical"},
            ))

        # --- duplicate
        ctx.progress(AGENT, "Checking for duplicate invoices")
        res = d.duplicate_invoice(normalize_invoice_number(inv_no), total, inv_date, [p for p, _ in dup_refs])
        checks["duplicate_detection"] = _status(ctx, "duplicate_detection", res)
        if res.fired:
            by_number = res.detail["match"] == "invoice_number"
            match = next(ref for p, ref in dup_refs if (by_number and p.invoice_number_norm == res.value) or (not by_number and p.total_minor == res.value))
            seen = f"{match['number']} dated {match['date']}, {format_inr(match['total'])}{match['where']}"
            if by_number:
                ev = [
                    EvidenceIn(source="invoice", field="invoice_number", value=inv_no, source_ref="invoice", excerpt=fsnip(ex, "invoice_number"), tier=1, match_key=normalize_invoice_number(inv_no)),
                    EvidenceIn(source="vendor_history", field="invoice_number", value=match["number"], source_ref=match["ref"], excerpt=seen, tier=1, match_key=match["norm"]),
                ]
                stmt = f"Invoice number {inv_no} was already received on {match['date']}{match['where']}."
            else:
                ev = [
                    EvidenceIn(source="invoice", field="total", value=total, source_ref="invoice", excerpt=fsnip(ex, "total"), tier=1),
                    EvidenceIn(source="vendor_history", field="total", value=match["total"], source_ref=match["ref"], excerpt=seen, tier=1),
                ]
                stmt = f"Same amount {format_inr(total)} was invoiced on {match['date']} ({match['number']}{match['where']}) within the duplicate window."
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=stmt, signal="duplicate_invoice", evidence=ev, confidence=0.97, severity="high",
                assertion={"op": "equals", "a": "$0", "b": "$1"},
                data={"observed": inv_no if by_number else format_inr(total), "baseline": match["number"]},
            ))

        # --- price
        ctx.progress(AGENT, "Comparing unit prices with vendor history")
        res = d.price_anomaly(items if not foreign else [], history)
        if res.skipped and pending_hist:
            res = replace(res, skipped=f"{res.skipped}; {pending_hist} past invoice(s) are waiting for approver approval")
        checks["price_anomaly"] = ({**ctx.unverifiable(AGENT, "price_anomaly", foreign_reason), "signal": "price_anomaly"} if foreign
                                   else _status(ctx, "price_anomaly", res))
        if res.fired:
            item = res.detail["item"]
            past = [p for h in history for (dd, _q, p) in h.line_items if dd.lower() == item.lower()] or [res.baseline]
            span = f"{hist_rows[0].invoice_date} to {hist_rows[-1].invoice_date}" if hist_rows else ""
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"Unit price for '{item}' is {res.detail['pct_change']}% above the vendor's historical average.",
                signal="price_anomaly",
                evidence=[
                    EvidenceIn(source="invoice", field="unit_price", value=res.value, source_ref="invoice", excerpt=fsnip(ex, "line_items"), tier=1),
                    EvidenceIn(source="vendor_history", field="avg_unit_price", value=res.baseline, source_ref=f"historical_invoices:{case.vendor_id}", excerpt=f"{res.detail['history_points']} prior invoices ({span}); mean {format_inr(res.baseline)}, min {format_inr(min(past))}, max {format_inr(max(past))}", tier=1),
                ],
                confidence=0.95, severity="high",
                assertion={"op": "pct_above", "observed": "$0", "baseline": "$1", "min_pct": 25, "claimed_pct": res.detail["pct_change"]},
                data={"observed": format_inr(res.value), "baseline": format_inr(res.baseline), "pct_change": res.detail["pct_change"], "z_score": res.detail["z_score"], "label_observed": "Current", "label_baseline": "Historical average"},
            ))

        # --- PO match / missing PO / quantity / temporal
        po_no = fv(ex, "po_number")
        # A PO only counts if it was raised for this vendor; quoting another vendor's PO number is a red flag.
        pos = list(s.scalars(select(PurchaseOrder).where(PurchaseOrder.workspace_id == ctx.workspace_id, PurchaseOrder.po_number == po_no))) if po_no else []
        own = [p for p in pos if case.vendor_id and p.vendor_id == case.vendor_id]
        po = next((p for p in own if p.approved_at is not None), None)  # only an approved PO is evidence
        pending_po = po is None and any(p.approved_at is None for p in own)
        other_vendor_po = po is None and not pending_po and bool(pos)
        res = d.missing_po(po_no, po is not None)
        if pending_po:
            checks["po_present"] = {**ctx.unverifiable(AGENT, "po_present", f"PO {po_no} is waiting for approver approval"), "signal": "missing_po"}
        else:
            checks["po_present"] = _status(ctx, "po_present", res)
        if res.fired and not pending_po:
            if not po_no:
                stmt = "Invoice carries no purchase order number."
            elif other_vendor_po:
                stmt = f"PO {po_no} on the invoice was raised for a different vendor."
            else:
                stmt = f"PO {po_no} on the invoice was not found in purchase records."
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=stmt,
                signal="missing_po",
                evidence=[
                    EvidenceIn(source="invoice", field="po_number", value=po_no, source_ref="invoice", excerpt=fsnip(ex, "po_number") or "(no PO field on invoice)", tier=1),
                    EvidenceIn(source="purchase_order", field="po_lookup", value=("other vendor" if other_vendor_po else "not found") if po_no else "n/a", source_ref=f"purchase_orders?po={po_no}", tier=1),
                ],
                confidence=0.9, severity="warn", assertion={"op": "info"}, data={"observed": po_no or "none", "baseline": "PO on file"},
            ))
        if po is not None:
            ctx.progress(AGENT, f"Matching quantities against {po.po_number}")
            po_lines = [(pl["description"], pl["qty"], pl["unit_price_minor"]) for pl in po.lines]
            res = d.quantity_po_mismatch(items, po_lines)
            checks["quantity_po_match"] = _status(ctx, "quantity_po_match", res)
            if res.fired:
                record_claim(s, ctx, AGENT, AgentClaim(
                    claim=f"Invoiced quantity {res.value} exceeds PO quantity {res.baseline} for '{res.detail['item']}'.",
                    signal="quantity_po_mismatch",
                    evidence=[
                        EvidenceIn(source="invoice", field="qty", value=res.value, source_ref="invoice", excerpt=fsnip(ex, "line_items"), tier=1),
                        EvidenceIn(source="purchase_order", field="qty", value=res.baseline, source_ref=f"purchase_order:{po.id}", excerpt=f"{po.po_number} dated {po.po_date}", tier=1),
                    ],
                    confidence=0.95, severity="warn", assertion={"op": "gt", "observed": "$0", "baseline": "$1"},
                    data={"observed": res.value, "baseline": res.baseline},
                ))
            res = d.temporal_anomaly(inv_date, po.po_date, today())
        else:
            reason = ("no PO number on the invoice" if not po_no else f"PO {po_no} is waiting for approver approval" if pending_po
                      else ("PO was raised for a different vendor" if other_vendor_po else "PO not found in your purchase orders"))
            checks["quantity_po_match"] = {**ctx.unverifiable(AGENT, "quantity_po_match", reason), "signal": "quantity_po_mismatch"}
            res = d.temporal_anomaly(inv_date, None, today())
        checks["dates"] = _status(ctx, "dates", res)
        if res.fired:
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=f"Date anomaly: {res.detail['reason']} ({res.value} vs {res.baseline}).",
                signal="temporal_anomaly",
                evidence=[EvidenceIn(source="invoice", field="invoice_date", value=res.value, source_ref="invoice", excerpt=fsnip(ex, "invoice_date"), tier=1)],
                confidence=0.9, severity="warn", assertion={"op": "info"}, data={"observed": res.value, "baseline": res.baseline},
            ))

        # --- history / statistical
        res = d.no_history(len(hist_rows))
        checks["history"] = {"status": "fired" if res.fired else "passed", "reason": f"{len(hist_rows)} prior invoices", "signal": "no_history"}
        if res.fired:
            record_claim(s, ctx, AGENT, AgentClaim(
                claim="No prior transaction history with this vendor." if not pending_hist else
                      f"No approved transaction history with this vendor ({pending_hist} past invoice(s) are waiting for approver approval).",
                signal="no_history",
                evidence=[EvidenceIn(source="vendor_history", field="invoice_count", value=0, source_ref=f"historical_invoices:{case.vendor_id}", tier=1)],
                confidence=0.99, severity="info", assertion={"op": "info"},
            ))
        if len(hist_rows) >= 30 and isinstance(total, int):
            amounts = [h.total_minor for h in hist_rows]
            sd = statistics.pstdev(amounts) or 1
            z = (total - statistics.fmean(amounts)) / sd
            checks["statistical"] = {"status": "fired" if z >= 3 else "passed", "reason": f"amount z-score {z:.2f}", "signal": "statistical_anomaly"}
            if z >= 3:
                record_claim(s, ctx, AGENT, AgentClaim(
                    claim=f"Invoice amount is a statistical outlier for this vendor (z = {z:.1f}).", signal="statistical_anomaly",
                    evidence=[EvidenceIn(source="invoice", field="total", value=total, source_ref="invoice", tier=1), EvidenceIn(source="vendor_history", field="mean_total", value=round(statistics.fmean(amounts)), source_ref=f"historical_invoices:{case.vendor_id}", tier=1)],
                    confidence=0.8, severity="warn", assertion={"op": "gt", "observed": "$0", "baseline": "$1"}, data={"z_score": round(z, 2)},
                ))
        else:
            checks["statistical"] = {"status": "skipped", "reason": f"insufficient history ({len(hist_rows)} < 30 rows)", "signal": "statistical_anomaly"}
        rs = d.round_sum(total if isinstance(total, int) else None)
        checks["round_sum"] = {"status": "fired" if rs.fired else "passed", "reason": "informational, 0 points", "signal": "round_sum"}

    fired = [k for k, v in checks.items() if v["status"] == "fired"]
    ctx.emit("agent.completed", agent=AGENT, status="done", message=f"{len(fired)} signal(s) fired: {', '.join(fired) or 'none'}")
    return {"checks": checks, "history_count": len(hist_rows)}


# Statuses whose invoice could still be (or already was) paid. Rejected and failed cases are left out.
_DUPLICATE_EXCLUDED = ("REJECTED", "FAILED")


def _duplicate_candidates(s, ctx: CaseCtx, case, hist_rows: list) -> list[tuple[d.PastInvoice, dict]]:  # type: ignore[no-untyped-def]
    """Everything this invoice must not duplicate: paid history plus the vendor's other non-rejected cases.
    Returns (comparable invoice, how to cite it) pairs."""
    out: list[tuple[d.PastInvoice, dict]] = [
        (d.PastInvoice(h.invoice_number_norm, h.invoice_date, h.total_minor, h.bank_hmac, ()),
         {"ref": f"historical_invoice:{h.id}", "number": h.invoice_number, "norm": h.invoice_number_norm, "date": h.invoice_date, "total": h.total_minor, "where": ""})
        for h in hist_rows
    ]
    if not case.vendor_id:
        return out
    in_history = {h.case_id for h in hist_rows if h.case_id}
    others = s.scalars(select(Case).where(
        Case.workspace_id == ctx.workspace_id, Case.vendor_id == case.vendor_id, Case.id != case.id,
        Case.status.notin_(_DUPLICATE_EXCLUDED),
    ))
    for c in others:
        if c.id in in_history:
            continue
        cx = c.extraction or {}
        number = fv(cx, "invoice_number")
        norm = normalize_invoice_number(number) if number else ""
        if not norm and c.amount_minor is None:
            continue  # nothing comparable was read from that invoice
        when = parse_date(fv(cx, "invoice_date")) or c.created_at.date()
        status = c.status.replace("_", " ").lower()
        out.append((
            d.PastInvoice(norm, when, c.amount_minor if c.amount_minor is not None else -1, None, ()),
            {"ref": f"case:{c.id}", "number": number or f"case #{c.number}", "norm": norm, "date": when, "total": c.amount_minor or 0,
             "where": f" (case #{c.number}, {status})"},
        ))
    return out
