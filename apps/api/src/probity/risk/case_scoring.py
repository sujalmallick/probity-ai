"""Run the policy-driven invoice scorer (risk/invoice_scoring.py) on a real case.

Every input comes from the database, never from the client:
  INVOICE         the case's extraction (human corrections applied; the bank account only as last4 + HMAC)
  VENDOR_HISTORY  the workspace's accepted invoices (historical_invoices), minus this case's own record;
                  vendor rows are those of the vendor the pipeline matched, not a name guess
  RISK_POLICY     the workspace's own invoice risk policy, else the bundled example policy

The case's evidence-gated score (risk/engine.py) is not touched; this is a second, explainable view on demand.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.agents.common import fv, today
from probity.db.models import Case, HistoricalInvoice, Vendor, Workspace
from probity.risk.invoice_scoring import score_invoice

EXAMPLE_POLICY = Path(__file__).with_name("invoice_policy.example.json")
POLICY_KEY = "invoice_risk_policy"


def default_policy() -> dict:
    return json.loads(EXAMPLE_POLICY.read_text("utf-8"))


def workspace_policy(ws: Workspace | None) -> tuple[dict, str]:
    """(policy, "workspace" | "default")."""
    custom = (ws.policy or {}).get(POLICY_KEY) if ws else None
    return (custom, "workspace") if isinstance(custom, dict) and custom else (default_policy(), "default")


def _major(minor: Any) -> Decimal | None:
    return Decimal(int(minor)) / 100 if isinstance(minor, int | float) and not isinstance(minor, bool) else None


def case_invoice(case: Case) -> dict:
    ex = case.extraction or {}
    bank = ex.get("bank_account") or {}
    items = [{"desc": li.get("description"), "qty": li.get("qty"), "unit": _major(li.get("unit_price_minor")), "amount": _major(li.get("amount_minor"))}
             for li in fv(ex, "line_items") or [] if isinstance(li, dict)]
    return {
        "vendor": fv(ex, "vendor_name"), "invoice_no": fv(ex, "invoice_number"), "invoice_date": fv(ex, "invoice_date"),
        "due_date": fv(ex, "due_date"), "line_items": items, "tax": _major(fv(ex, "tax")), "total": _major(fv(ex, "total")),
        "bank_account": bank.get("value"), "bank_account_hmac": bank.get("hmac"), "bank_last4": bank.get("last4"),
    }


def workspace_history(s: Session, workspace_id: str, exclude_case_id: str | None = None) -> list[dict]:
    q = (select(HistoricalInvoice, Vendor.name).join(Vendor, Vendor.id == HistoricalInvoice.vendor_id, isouter=True)
         .where(HistoricalInvoice.workspace_id == workspace_id, HistoricalInvoice.approved_at.is_not(None)).order_by(HistoricalInvoice.invoice_date))
    return [
        {"vendor": name, "vendor_id": h.vendor_id, "invoice_no": h.invoice_number, "total": _major(h.total_minor), "invoice_date": h.invoice_date,
         "bank_account_hmac": h.bank_hmac, "bank_last4": h.bank_last4}
        for h, name in s.execute(q)
        if not (exclude_case_id and h.case_id == exclude_case_id)  # a cleared case is in history; it is not its own duplicate
    ]


def score_case(s: Session, case: Case, *, narrate: bool = False, budget=None) -> dict:  # type: ignore[no-untyped-def]
    policy, source = workspace_policy(s.get(Workspace, case.workspace_id))
    history = workspace_history(s, case.workspace_id, case.id)
    injection = ((case.validation or {}).get("injection") or {}).get("detail") or []
    out = score_invoice(case_invoice(case), policy, history, today=today(), vendor_id=case.vendor_id,
                        scan_text="\n".join(str(h) for h in injection))
    out["meta"] = {"case_id": case.id, "policy_source": source, "policy_version": policy.get("version"), "history_size": len(history),
                   "vendor_matched": case.vendor_id is not None}
    if narrate:
        out["narrative"] = cached_narrative(case, out, budget=budget)
    return out


NARRATIVE_KEY = "invoice_risk_narrative"


def narrative_fingerprint(result: dict) -> str:
    """Hash of exactly what the explainer is shown (score, tier, drivers, evidence): equal fingerprints get an
    equivalent explanation, so the AI is only asked again when one of those changes."""
    from probity.risk.narrate import facts

    return hashlib.sha256(json.dumps(facts(result), sort_keys=True, default=str).encode()).hexdigest()


def cached_narrative(case: Case, result: dict, budget=None) -> dict:  # type: ignore[no-untyped-def]
    """The AI explanation is written once per distinct score and kept on the case: opening the Invoice risk tab
    again reuses it instead of making another AI call. Only AI-written text is kept; a rule-based fallback is
    retried on the next view."""
    from probity.risk.narrate import narrate

    key = narrative_fingerprint(result)
    saved = (case.recommendation or {}).get(NARRATIVE_KEY) or {}
    if saved.get("fingerprint") == key:
        return {**saved["narrative"], "cached": True}
    n = narrate(result, tags={"workspace_id": case.workspace_id, "case_id": case.id}, budget=budget)
    if n.get("source") == "llm":
        case.recommendation = {**(case.recommendation or {}), NARRATIVE_KEY: {"fingerprint": key, "narrative": n}}
    return n
