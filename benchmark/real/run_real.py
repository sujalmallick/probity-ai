"""Run Probity on your own real invoices and compare its decisions with what you know the right answer was.

    cd apps/api
    ../../.venv/Scripts/python.exe ../../benchmark/real/run_real.py --workspace <ws_id> --as you@company.com

Inputs (both git-ignored, never committed):
  benchmark/real/invoices/   your PDFs / .eml files
  benchmark/real/labels.csv  file,expected,expected_signals,notes   (see labels.template.csv)

Each invoice is investigated in the workspace you name, exactly like an upload in the app (same live integrations,
same limits, same gate). A file that already has a case there is not re-run: its existing result is reported.
The report goes to benchmark/real/reports/<timestamp>.md and .json (git-ignored).

Nothing here is synthetic: the numbers are only as good as your labels and the size of your sample, and the report
says so.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPORTED = {".pdf", ".eml"}


@dataclass
class Label:
    expected: str  # hold | clear
    signals: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Row:
    file: str
    outcome: str  # held | cleared | failed | rejected | not_run
    case_id: str | None = None
    case_number: int | None = None
    score: int | None = None
    tier: str | None = None
    signals: list[str] = field(default_factory=list)
    could_not_verify: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    seconds: float | None = None
    tokens: int | None = None
    reused: bool = False
    error: str | None = None
    expected: str | None = None
    expected_signals: list[str] = field(default_factory=list)


def read_labels(path: Path) -> dict[str, Label]:
    if not path.exists():
        return {}
    out: dict[str, Label] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            name = (r.get("file") or "").strip()
            exp = (r.get("expected") or "").strip().lower()
            if not name:
                continue
            if exp not in ("hold", "clear"):
                raise SystemExit(f"labels.csv line {i}: expected must be 'hold' or 'clear', got '{exp}'")
            sigs = [x.strip() for x in (r.get("expected_signals") or "").replace(",", ";").split(";") if x.strip()]
            out[name] = Label(exp, sigs, (r.get("notes") or "").strip())
    return out


def _result(case: dict) -> dict:
    gate = (case.get("recommendation") or {}).get("gate") or {}
    contribs = (case.get("risk") or {}).get("contributions") or []
    return {
        "case_id": case["id"], "case_number": case.get("number"), "score": (case.get("risk") or {}).get("score"),
        "tier": (case.get("risk") or {}).get("tier"),
        "signals": sorted({c["signal"] for c in contribs if c.get("points")}),
        "could_not_verify": sorted(f"{u['check']}: {u['reason']}" for u in gate.get("could_not_verify") or []),
        "reasons": gate.get("reasons") or [],
        "tokens": (case.get("budget") or {}).get("tokens"),
        "outcome": {"AUTO_CLEARED": "cleared", "FAILED": "failed"}.get(case.get("status"), "held"),
    }


def investigate(workspace_id: str, user_email: str, files: list[Path]) -> list[Row]:
    from sqlalchemy import func, select

    from probity import services
    from probity.config import get_settings
    from probity.db.models import Case, Document, User
    from probity.db.session import session_scope
    from probity.ingestion.parse import UnsupportedDocument, extract_text, sha256, sniff_mime

    st = get_settings()
    st.validate_required()
    services.run_sync(True)  # each investigation finishes before the next starts
    with session_scope(workspace_id) as s:
        user = s.scalars(select(User).where(User.workspace_id == workspace_id, func.lower(User.email) == user_email.lower())).first()
        if user is None:
            raise SystemExit(f"no user {user_email} in workspace {workspace_id}")
        if services.ROLES.index(user.role) < services.ROLES.index("accountant"):
            raise SystemExit(f"{user_email} is a {user.role}; investigating needs accountant or above")
        user_id = user.id
    rows: list[Row] = []
    for path in files:
        data = path.read_bytes()
        row = Row(path.name, "not_run")
        t0 = time.perf_counter()
        try:
            with session_scope(workspace_id) as s:
                existing = s.scalars(select(Case).join(Document, Document.id == Case.document_id).where(
                    Case.workspace_id == workspace_id, Document.sha256 == sha256(data), Case.status.notin_(("REJECTED", "FAILED")))
                    .order_by(Case.created_at.desc())).first()
                if existing is not None:
                    row.__dict__.update(_result(services.serialize_case(s, existing, s.get(User, user_id))), reused=True)
                    rows.append(row)
                    continue
                start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                today = s.scalar(select(func.count()).select_from(Case).where(Case.workspace_id == workspace_id, Case.created_at >= start)) or 0
                if today >= st.workspace_daily_case_limit:
                    row.error = f"Limit reached: {st.workspace_daily_case_limit} investigations per workspace per day (WORKSPACE_DAILY_CASE_LIMIT)"
                    rows.append(row)
                    continue
                if len(data) > st.max_upload_mb * 1024 * 1024:
                    raise UnsupportedDocument(f"File exceeds {st.max_upload_mb} MB (MAX_UPLOAD_MB)")
                extract_text(data, sniff_mime(data, path.name))  # same refusal as the upload endpoint (scans, images)
                doc, _ = services.upload_document(s, s.get(User, user_id), path.name, data)
                s.commit()
                case = services.create_case(s, s.get(User, user_id), doc.id)
                case_id = case.id
            with session_scope(workspace_id) as s:
                row.__dict__.update(_result(services.serialize_case(s, s.get(Case, case_id), s.get(User, user_id))))
        except UnsupportedDocument as e:
            row.outcome, row.error = "rejected", str(e)
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
            row.outcome, row.error = "failed", f"{type(e).__name__}: {str(e)[:300]}"
        row.seconds = round(time.perf_counter() - t0, 1)
        rows.append(row)
    return rows


def score(rows: list[Row], labels: dict[str, Label]) -> dict:
    for r in rows:
        if r.file in labels:
            r.expected, r.expected_signals = labels[r.file].expected, labels[r.file].signals
    judged = [r for r in rows if r.expected and r.outcome in ("held", "cleared")]
    m = {"hold→held": 0, "hold→cleared": 0, "clear→cleared": 0, "clear→held": 0}
    for r in judged:
        m[f"{r.expected}→{r.outcome}"] += 1
    sig_tp = sig_fp = sig_fn = 0
    for r in judged:
        if not r.expected_signals and r.expected == "hold":
            continue  # a hold without named signals: no signal-level judgement
        exp, got = set(r.expected_signals), set(r.signals)
        sig_tp += len(exp & got)
        sig_fp += len(got - exp)
        sig_fn += len(exp - got)
    return {
        "invoices": len(rows), "labelled": len(judged), "unlabelled": sum(1 for r in rows if not r.expected),
        "matrix": m, "missed_holds": m["hold→cleared"],
        "signals": {"matched": sig_tp, "unexpected": sig_fp, "missed": sig_fn},
        "not_judged": {o: sum(1 for r in rows if r.outcome == o) for o in ("failed", "rejected", "not_run")},
    }


def render(rows: list[Row], summary: dict, workspace_id: str) -> str:
    m = summary["matrix"]
    out = [
        "# Probity on real invoices", "",
        f"Run {datetime.now(timezone.utc).isoformat(timespec='seconds')} · workspace `{workspace_id}` · {summary['invoices']} file(s), "
        f"{summary['labelled']} judged against your labels.", "",
        "> These numbers come only from your invoices and your labels. With a small sample they say little about accuracy in",
        "> general; read the per-invoice table, especially every *missed hold* and every *could not verify*.", "",
        "## Hold / clear against your labels", "",
        "| You expected | Probity held | Probity cleared |", "|---|---|---|",
        f"| hold | {m['hold→held']} | **{m['hold→cleared']}** (missed hold) |",
        f"| clear | {m['clear→held']} (extra review) | {m['clear→cleared']} |", "",
        f"Signals vs your expected signals: {summary['signals']['matched']} matched, {summary['signals']['unexpected']} not expected, "
        f"{summary['signals']['missed']} missed.", "",
        f"Not judged: {summary['not_judged']['failed']} failed, {summary['not_judged']['rejected']} rejected at upload, "
        f"{summary['not_judged']['not_run']} not run, {summary['unlabelled']} without a label.", "",
        "## Per invoice", "",
        "| File | Expected | Outcome | Score | Signals | Could not verify | Note |", "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        flag = " ⚠ missed hold" if r.expected == "hold" and r.outcome == "cleared" else ""
        note = r.error or ("existing case reused" if r.reused else "")
        case = f" (case #{r.case_number})" if r.case_number else ""
        out.append(f"| {r.file} | {r.expected or '—'} | {r.outcome}{case}{flag} | {'' if r.score is None else f'{r.score} {r.tier}'} | "
                   f"{', '.join(r.signals) or '—'} | {'; '.join(r.could_not_verify) or '—'} | {note} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", required=True, help="workspace id to investigate in (Settings → workspace, or /api/v1/me)")
    ap.add_argument("--as", dest="email", required=True, help="email of an accountant/approver/owner in that workspace")
    ap.add_argument("--invoices", type=Path, default=HERE / "invoices")
    ap.add_argument("--labels", type=Path, default=HERE / "labels.csv")
    ap.add_argument("--out", type=Path, default=HERE / "reports")
    args = ap.parse_args(argv)
    files = sorted(p for p in args.invoices.glob("*") if p.suffix.lower() in SUPPORTED) if args.invoices.exists() else []
    if not files:
        print(f"No invoices found in {args.invoices}. Put your PDFs / .eml files there (it is git-ignored).")
        return 2
    labels = read_labels(args.labels)
    missing = sorted(set(labels) - {f.name for f in files})
    if missing:
        print(f"note: {len(missing)} label(s) have no file: {', '.join(missing[:5])}")
    rows = investigate(args.workspace, args.email, files)
    summary = score(rows, labels)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    md = args.out / f"{stamp}.md"
    md.write_text(render(rows, summary, args.workspace), encoding="utf-8")
    (args.out / f"{stamp}.json").write_text(json.dumps({"summary": summary, "rows": [asdict(r) for r in rows]}, indent=1), encoding="utf-8")
    m = summary["matrix"]
    print(f"{summary['invoices']} invoice(s): held {sum(r.outcome == 'held' for r in rows)}, cleared {sum(r.outcome == 'cleared' for r in rows)}, "
          f"failed {summary['not_judged']['failed']}, rejected {summary['not_judged']['rejected']}")
    if summary["labelled"]:
        print(f"against your labels: missed holds {m['hold→cleared']}, extra reviews {m['clear→held']}")
    print(f"report: {md}")
    return 1 if m["hold→cleared"] else 0


if __name__ == "__main__":
    sys.exit(main())
