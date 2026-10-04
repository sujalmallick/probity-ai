"""Sanity checks: invariants every finished case must satisfy, checked from the stored data after the pipeline runs.

They catch bugs, not fraud: a score that doesn't add up, a point without verified evidence, evidence from another case,
"no adverse findings" without a search, an AI fallback without its label, a foreign-currency price compared with INR.
Any problem holds the case (never auto-clear) and is listed on it. `python -m probity.sanity` re-checks stored cases.

    python -m probity.sanity                 # every workspace
    python -m probity.sanity --workspace ws_… --days 7
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from probity.db.models import Case, ClaimRow, EvidenceRow, LLMCall
from probity.risk import engine

NO_ADVERSE = "No adverse public findings"


def problem(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def check_case(s: Session, case: Case, *, final: bool = True) -> list[dict[str, str]]:
    """Problems with a scored case. final=False skips the rules about the gate's own decision (used inside the gate)."""
    out: list[dict[str, str]] = []
    risk = case.risk or {}
    contribs = risk.get("contributions") or []
    claims = {c.id: c for c in s.scalars(select(ClaimRow).where(ClaimRow.case_id == case.id))}
    if risk:
        counted = sum(c["points"] for c in contribs if c.get("status") == "counted")
        if risk.get("score") != max(0, min(100, counted)):
            out.append(problem("score_mismatch", f"score {risk.get('score')} is not the sum of counted points ({counted})"))
        if risk.get("tier") != engine.tier_for(risk.get("score") or 0):
            out.append(problem("tier_mismatch", f"tier {risk.get('tier')} does not match score {risk.get('score')}"))
        for c in contribs:
            if not c.get("points"):
                continue
            cl = claims.get(c.get("claim_id") or "")
            if c.get("status") != "counted" or cl is None or cl.status != "verified" or not cl.active:
                out.append(problem("unverified_points", f"{c['signal']} adds {c['points']} points without an active verified claim"))
            elif not cl.evidence_ids:
                out.append(problem("points_without_evidence", f"{c['signal']} adds points but its claim cites no evidence"))
    # every cited evidence row belongs to this case and workspace
    cited = {e for c in claims.values() if c.active for e in (c.evidence_ids or [])}
    if cited:
        rows = {e.id: e for e in s.scalars(select(EvidenceRow).where(EvidenceRow.id.in_(cited)))}
        foreign = [e for e in cited if e in rows and (rows[e].case_id != case.id or rows[e].workspace_id != case.workspace_id)]
        if foreign:
            out.append(problem("foreign_evidence", f"{len(foreign)} claim citation(s) point at evidence from another case"))
    # "no adverse findings" only after a search actually ran
    if any(c.active and c.statement.startswith(NO_ADVERSE) for c in claims.values()):
        if ((case.checks or {}).get("external_reputation") or {}).get("status") != "passed":
            out.append(problem("unsupported_all_clear", "'No adverse public findings' is stated but web research did not pass"))
    # foreign or unstated currency is never compared with INR history
    currency = ((case.extraction or {}).get("currency") or {}).get("value")
    if currency != "INR" and any(c.get("signal") == "price_anomaly" and c.get("points") for c in contribs):
        out.append(problem("currency_compared", f"price compared with INR history although the invoice is in {currency or 'an unstated currency'}"))
    # an AI failure that produced a rule-based result must be labelled
    llm = list(s.scalars(select(LLMCall).where(LLMCall.case_id == case.id)))
    analyst_ok = any(c.ok for c in llm if c.agent == "case_analyst")
    rec = case.recommendation or {}
    if rec.get("summary") is not None:
        if rec.get("summary_source") == "ai" and not analyst_ok:
            out.append(problem("unlabelled_fallback", "summary is marked as AI-written but no AI summary call succeeded"))
        if rec.get("summary_source") == "engine" and not rec.get("summary_fallback"):
            out.append(problem("unlabelled_fallback", "engine summary is missing its 'rule-based fallback, AI unavailable' label"))
    # the amount on the case is the total read from the invoice
    total = ((case.extraction or {}).get("total") or {}).get("value")
    if isinstance(total, int) and case.amount_minor not in (None, total):
        out.append(problem("amount_mismatch", f"case amount {case.amount_minor} differs from the invoice total {total}"))
    if final:
        gate = rec.get("gate") or {}
        if case.status == "AUTO_CLEARED":
            if gate.get("reasons") or gate.get("could_not_verify") or (risk.get("tier") != "LOW"):
                out.append(problem("unsafe_auto_clear", "auto-cleared although the gate recorded reasons to hold or the tier is not LOW"))
    return out


def stamp(case: Case, problems: list[dict[str, str]]) -> None:
    case.recommendation = {**(case.recommendation or {}),
                           "sanity": {"ok": not problems, "problems": problems, "checked_at": datetime.now(timezone.utc).isoformat()}}


def main(argv: list[str] | None = None) -> int:
    from probity.db.models import Workspace
    from probity.db.session import session_scope

    ap = argparse.ArgumentParser(prog="python -m probity.sanity", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", help="only this workspace id")
    ap.add_argument("--days", type=int, default=30, help="cases created in the last N days (default 30)")
    args = ap.parse_args(argv)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    with session_scope() as s:
        workspaces = [args.workspace] if args.workspace else [w.id for w in s.scalars(select(Workspace))]
    checked = bad = 0
    for ws in workspaces:
        with session_scope(ws) as s:
            for case in s.scalars(select(Case).where(Case.workspace_id == ws, Case.created_at >= since)):
                if not case.risk:
                    continue  # not scored yet (queued, running or failed before scoring)
                checked += 1
                problems = check_case(s, case)
                if problems:
                    bad += 1
                    for p in problems:
                        print(f"{ws} case #{case.number} ({case.id}) [{p['code']}] {p['message']}")
    print(f"{checked} case(s) checked, {bad} with problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
