"""Phase 5: the real-invoice harness (benchmark/real/run_real.py) investigates files like an upload, reuses existing
cases, judges against labels, refuses what the app refuses, and writes an honest report."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from factories import NEW_DOMAIN, bank_change_spec, clean_spec, pdf

SCRIPT = Path(__file__).resolve().parents[3] / "benchmark" / "real" / "run_real.py"


def _image_only_pdf() -> bytes:
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.rect(50, 50, 400, 600, fill=1)  # a page with only a drawing, like a scan
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture()
def harness():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("run_real", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_real"] = mod  # dataclasses resolve their module by name
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def test_harness_end_to_end(harness, world, fake_lookups, tmp_path):
    fake_lookups.domains[NEW_DOMAIN] = 21
    inv = tmp_path / "invoices"
    inv.mkdir()
    (inv / "clean.pdf").write_bytes(pdf(clean_spec()))
    (inv / "bank_change.pdf").write_bytes(pdf(bank_change_spec()))
    (inv / "missed.pdf").write_bytes(pdf(clean_spec().__class__(**{**clean_spec().__dict__, "invoice_number": "BP-2026-312"})))
    (inv / "scan.pdf").write_bytes(_image_only_pdf())  # no text layer → refused like an upload
    (inv / "notes.txt").write_text("ignored: not a supported type")
    labels = tmp_path / "labels.csv"
    labels.write_text("file,expected,expected_signals,notes\n"
                      "clean.pdf,hold,,we knew this one was wrong\n"
                      "bank_change.pdf,hold,bank_account_changed;price_anomaly;new_domain,bank changed\n"
                      "missed.pdf,hold,duplicate_invoice,same amount again days later\n"
                      "gone.pdf,clear,,\n", encoding="utf-8")
    out = tmp_path / "reports"
    owner_email = world.user("owner").email
    code = harness.main(["--workspace", world.workspace_id, "--as", owner_email, "--invoices", str(inv), "--labels", str(labels), "--out", str(out)])
    assert code == 1  # a labelled hold was cleared → non-zero exit
    report = json.loads(next(out.glob("*.json")).read_text(encoding="utf-8"))
    rows = {r["file"]: r for r in report["rows"]}
    assert set(rows) == {"bank_change.pdf", "clean.pdf", "missed.pdf", "scan.pdf"}
    assert rows["clean.pdf"]["outcome"] == "cleared" and rows["bank_change.pdf"]["outcome"] == "held"
    assert rows["missed.pdf"]["outcome"] == "held" and "duplicate_invoice" in rows["missed.pdf"]["signals"]  # same amount, days apart
    assert set(rows["bank_change.pdf"]["signals"]) == {"bank_account_changed", "price_anomaly", "new_domain"}
    assert rows["scan.pdf"]["outcome"] == "rejected" and "scanned" in rows["scan.pdf"]["error"].lower()
    m = report["summary"]["matrix"]
    assert m == {"hold→held": 2, "hold→cleared": 1, "clear→cleared": 0, "clear→held": 0}
    assert report["summary"]["signals"]["matched"] == 4 and report["summary"]["signals"]["missed"] == 0
    md = next(out.glob("*.md")).read_text(encoding="utf-8")
    assert "missed hold" in md and "say little about accuracy" in md

    # a second run reuses the existing cases instead of creating duplicates
    harness.main(["--workspace", world.workspace_id, "--as", owner_email, "--invoices", str(inv), "--labels", str(labels), "--out", str(tmp_path / "r2")])
    again = json.loads(next((tmp_path / "r2").glob("*.json")).read_text(encoding="utf-8"))
    assert all(r["reused"] for r in again["rows"] if r["outcome"] in ("held", "cleared"))


def test_harness_refuses_bad_input(harness, world, tmp_path):
    inv = tmp_path / "invoices"
    inv.mkdir()
    assert harness.main(["--workspace", world.workspace_id, "--as", "x@y.test", "--invoices", str(inv)]) == 2  # nothing to run
    (inv / "a.pdf").write_bytes(pdf(clean_spec()))
    with pytest.raises(SystemExit, match="no user"):
        harness.main(["--workspace", world.workspace_id, "--as", "nobody@nowhere.test", "--invoices", str(inv), "--out", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="viewer"):
        harness.main(["--workspace", world.workspace_id, "--as", world.user("viewer").email, "--invoices", str(inv), "--out", str(tmp_path / "o")])
    bad = tmp_path / "labels.csv"
    bad.write_text("file,expected\na.pdf,maybe\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="must be 'hold' or 'clear'"):
        harness.read_labels(bad)


def test_harness_respects_the_daily_limit(harness, world, settings, tmp_path):
    settings(WORKSPACE_DAILY_CASE_LIMIT="1")
    inv = tmp_path / "invoices"
    inv.mkdir()
    (inv / "a.pdf").write_bytes(pdf(clean_spec()))
    (inv / "b.pdf").write_bytes(pdf(clean_spec().__class__(**{**clean_spec().__dict__, "invoice_number": "BP-2026-399"})))
    harness.main(["--workspace", world.workspace_id, "--as", world.user("owner").email, "--invoices", str(inv), "--out", str(tmp_path / "o")])
    rows = {r["file"]: r for r in json.loads(next((tmp_path / "o").glob("*.json")).read_text(encoding="utf-8"))["rows"]}
    assert rows["a.pdf"]["outcome"] == "cleared"
    assert rows["b.pdf"]["outcome"] == "not_run" and "WORKSPACE_DAILY_CASE_LIMIT" in rows["b.pdf"]["error"]
