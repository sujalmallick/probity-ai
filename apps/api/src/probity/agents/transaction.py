"""Agent 5 — Transaction Analyst: "Is the transaction abnormal vs our data?"

All signals are computed by deterministic code (signals/detectors.py). This agent only packages each
fired signal as a claim with structured evidence: observed (invoice) vs baseline (our records).
"""

from __future__ import annotations

import statistics

from sqlalchemy import select

from probity.agents.common import fsnip, fv, load_case, record_claim, today, vendor_bundle
from probity.db.models import HistoricalInvoice, PurchaseOrder
from probity.db.session import session_scope
from probity.events import CaseCtx
from probity.evidence.models import AgentClaim, EvidenceIn
from probity.guardrails.crypto import mask
from probity.ingestion.validators import format_inr, normalize_invoice_number, parse_date
from probity.signals import detectors as d

AGENT = "transaction_analyst"


def _status(res: d.SignalResult) -> dict:
    return {"status": "fired" if res.fired else ("skipped" if res.skipped else "passed"), "reason": res.skipped or "", "signal": res.signal}


def run(ctx: CaseCtx) -> dict:
    ctx.emit("agent.started", agent=AGENT, status="running", message="Analyzing transaction against history")
    checks: dict[str, dict] = {}
    with session_scope() as s:
        case = load_case(s, ctx)
        ex = case.extraction
        b = vendor_bundle(s, ctx.workspace_id, case.vendor_id)
        hist_rows = (
            list(s.scalars(select(HistoricalInvoice).where(HistoricalInvoice.workspace_id == ctx.workspace_id, HistoricalInvoice.vendor_id == case.vendor_id).order_by(HistoricalInvoice.invoice_date)))
            if case.vendor_id else []
        )
        ctx.progress(AGENT, f"Searched {len(hist_rows)} historical invoices")
        history = [
            d.PastInvoice(h.invoice_number_norm, h.invoice_date, h.total_minor, h.bank_hmac, tuple((li["description"], li["qty"], li["unit_price_minor"]) for li in h.line_items))
            for h in hist_rows
        ]
        items = [(li["description"], li["qty"], li["unit_price_minor"]) for li in (fv(ex, "line_items") or [])]
        inv_no = fv(ex, "invoice_number")
        inv_date = parse_date(fv(ex, "invoice_date"))
        total = fv(ex, "total")
        bank = ex.get("bank_account") or {}

        # --- bank account
        ctx.progress(AGENT, "Comparing bank account with vendor history")
        known = [d.KnownAccount(a.last4, a.acct_hmac, a.verified) for a in b["accounts"]]
        res = d.bank_account_changed(bank.get("hmac"), bank.get("last4"), known)
        checks["bank_account_verification"] = _status(res)
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
        res = d.duplicate_invoice(normalize_invoice_number(inv_no), total, inv_date, history)
        checks["duplicate_detection"] = _status(res)
        if res.fired:
            match = next(h for h in hist_rows if (res.detail["match"] == "invoice_number" and h.invoice_number_norm == res.value) or (res.detail["match"] != "invoice_number" and h.total_minor == res.value))
            if res.detail["match"] == "invoice_number":
                ev = [
                    EvidenceIn(source="invoice", field="invoice_number", value=inv_no, source_ref="invoice", excerpt=fsnip(ex, "invoice_number"), tier=1, match_key=normalize_invoice_number(inv_no)),
                    EvidenceIn(source="vendor_history", field="invoice_number", value=match.invoice_number, source_ref=f"historical_invoice:{match.id}", excerpt=f"{match.invoice_number} dated {match.invoice_date}, {format_inr(match.total_minor)}", tier=1, match_key=match.invoice_number_norm),
                ]
                stmt = f"Invoice number {inv_no} was already received on {match.invoice_date}."
            else:
                ev = [
                    EvidenceIn(source="invoice", field="total", value=total, source_ref="invoice", excerpt=fsnip(ex, "total"), tier=1),
                    EvidenceIn(source="vendor_history", field="total", value=match.total_minor, source_ref=f"historical_invoice:{match.id}", excerpt=f"{match.invoice_number} dated {match.invoice_date}, {format_inr(match.total_minor)}", tier=1),
                ]
                stmt = f"Same amount {format_inr(total)} was invoiced on {match.invoice_date} ({match.invoice_number}) within the duplicate window."
            record_claim(s, ctx, AGENT, AgentClaim(
                claim=stmt, signal="duplicate_invoice", evidence=ev, confidence=0.97, severity="high",
                assertion={"op": "equals", "a": "$0", "b": "$1"},
                data={"observed": inv_no if res.detail["match"] == "invoice_number" else format_inr(total), "baseline": match.invoice_number},
            ))

        # --- price
        ctx.progress(AGENT, "Comparing unit prices with vendor history")
        res = d.price_anomaly(items, history)
        checks["price_anomaly"] = _status(res)
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
        po = next((p for p in pos if case.vendor_id and p.vendor_id == case.vendor_id), None)
        other_vendor_po = po is None and bool(pos)
        res = d.missing_po(po_no, po is not None)
        checks["po_present"] = _status(res)
        if res.fired:
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
            checks["quantity_po_match"] = _status(res)
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
            checks["quantity_po_match"] = {"status": "skipped", "reason": "no matching PO", "signal": "quantity_po_mismatch"}
            res = d.temporal_anomaly(inv_date, None, today())
        checks["dates"] = _status(res)
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
                claim="No prior transaction history with this vendor.", signal="no_history",
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
