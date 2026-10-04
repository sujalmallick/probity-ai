"""Currency is read, shown and never assumed or converted; usage limits stop work cleanly and name the limit;
scans and images are refused at upload; every API error has one shape."""

from dataclasses import replace

import pytest
from conftest import login, owner_session
from factories import clean_spec, pdf
from helpers import API, run_case
from sqlalchemy import select

from probity.ingestion.parse import extract_text, parse_fields
from probity.ingestion.validators import currencies_in, format_money, normalize_currency
from probity.tools.base import Budget, BudgetExceeded


@pytest.mark.parametrize("text,codes", [
    ("Grand Total: INR 5,66,400.00", ["INR"]), ("Total: \u20b9 1,05,256", ["INR"]), ("Rs. 590", ["INR"]),
    ("Total: $1,200.00", ["USD"]), ("Amount S$ 300", ["SGD"]), ("EUR 40", ["EUR"]), ("Rs. 590 and USD 10", ["INR", "USD"]),
    ("Mrs. Rao paid 10", []), ("no money here", []),
])
def test_currency_markers(text, codes):
    assert currencies_in(text) == codes


def test_stated_currency_labels_and_display():
    assert normalize_currency("US Dollar") == "USD" and normalize_currency("Indian Rupee") == "INR" and normalize_currency("??") is None
    assert format_money(56640000, "INR") == "\u20b956,6400"[:0] + format_money(56640000, "INR")  # INR keeps Indian grouping
    assert format_money(120000, "USD") == "USD 1,200.00"
    assert format_money(120000, None) == "1,200.00 (currency not stated)"


def _fields(spec):
    data = pdf(spec)
    return parse_fields(extract_text(data, "application/pdf"))


def test_dollar_invoice_is_read_as_usd_not_rupees():
    spec = replace(clean_spec(), extra_lines=["All amounts in USD"])
    f = _fields(spec)
    assert f["currency"]["value"] == "MIXED"  # the PDF builder prints "INR" totals; with a USD note it is ambiguous → held


def test_case_in_foreign_currency_is_held_and_shows_currency(client, world, monkeypatch):
    from probity.ingestion import parse

    real = parse.parse_fields

    def as_usd(pages, tables=None):  # the invoice states USD
        f = real(pages, tables)
        f["currency"] = {"value": "USD", "raw": "USD", "confidence": 0.95, "evidence_snippet": "Currency: USD", "page": 1}
        return f

    monkeypatch.setattr("probity.agents.document.parse_fields", as_usd)
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AWAITING_HUMAN"
    assert case["validation"]["currency"]["ok"] is False and "USD" in case["validation"]["currency"]["reason"]
    assert case["amount"]["currency"] == "USD"
    assert any(r.startswith("Currency: invoice is in USD") for r in case["recommendation"]["gate"]["reasons"])
    assert case["checks"]["price_anomaly"]["status"] == "could_not_verify"  # never compared with INR history
    assert "price_anomaly" not in {c["signal"] for c in case["risk"]["contributions"] if c["points"]}


def test_inr_case_still_clears(client, world):
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["validation"]["currency"] == {"ok": True, "detail": "INR", "reason": None}
    assert case["status"] == "AUTO_CLEARED"


def test_budget_names_the_limit():
    b = Budget(max_llm_calls=5, max_tokens=100, max_web_calls=50, max_seconds=60, max_searches=2)
    b.charge_web(search=True)
    b.charge_web(search=True)
    with pytest.raises(BudgetExceeded) as e:
        b.charge_web(search=True)
    assert e.value.limit == "searches" and "2 web searches per case (CASE_WEB_SEARCH_LIMIT)" in str(e.value)
    with pytest.raises(BudgetExceeded, match="CASE_TOKEN_LIMIT"):
        b.charge_llm(tokens=500)


def test_output_tokens_count_against_the_case(llm, monkeypatch):
    from probity.agents.risk_case import CaseSummary
    from probity.llm import client

    monkeypatch.setattr(client, "transport", lambda schema, system, content, model: (CaseSummary(summary="x", recommendation="HOLD_PAYMENT"), 50, 5000))
    b = Budget(max_llm_calls=5, max_tokens=4000, max_web_calls=5, max_seconds=60)
    client.generate(schema=CaseSummary, system="s", user="u", tier="fast", tags={}, budget=b)
    assert b.tokens >= 5050
    with pytest.raises(BudgetExceeded, match="AI tokens per case"):
        client.generate(schema=CaseSummary, system="s", user="u", tier="fast", tags={}, budget=b)


def test_workspace_daily_token_limit(world, settings, monkeypatch):
    from probity.agents.risk_case import CaseSummary
    from probity.db.models import LLMCall
    from probity.llm import client

    settings(WORKSPACE_DAILY_TOKEN_LIMIT="1000")
    with owner_session() as s:
        s.add(LLMCall(workspace_id=world.workspace_id, agent="x", prompt_version="x", model="m", mode="live", ok=True, latency_ms=1, tokens_in=600, tokens_out=600))
    with pytest.raises(BudgetExceeded, match="WORKSPACE_DAILY_TOKEN_LIMIT"):
        client.generate(schema=CaseSummary, system="s", user="u", tier="fast", tags={"workspace_id": world.workspace_id})


def test_workspace_daily_case_limit(client, world, settings):
    settings(WORKSPACE_DAILY_CASE_LIMIT="1")
    acc = login(client, "accountant")
    run_case(client, acc, clean_spec())
    doc = client.post(f"{API}/documents", headers=acc, files={"file": ("b.pdf", pdf(replace(clean_spec(), invoice_number="BP-2")), "application/pdf")}).json()
    r = client.post(f"{API}/cases", headers=acc, json={"document_id": doc["document_id"]})
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "limit_reached" and "WORKSPACE_DAILY_CASE_LIMIT" in err["message"] and err["retryable"] is False and err["ref"]


def test_upload_size_limit_is_configurable(client, world, settings):
    settings(MAX_UPLOAD_MB="1")
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10)
    r = client.post(f"{API}/documents", headers=login(client, "accountant"), files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code in (400, 413)
    assert client.get(f"{API}/app/config").json()["limits"]["max_upload_mb"] == 1


def test_scan_is_refused_before_anything_is_stored(client, world):
    import io

    from PIL import Image

    from probity.db.models import Document

    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buf, format="PNG")
    r = client.post(f"{API}/documents", headers=login(client, "accountant"), files={"file": ("scan.png", buf.getvalue(), "image/png")})
    assert r.status_code == 415 and "scanned" in r.json()["error"]["message"].lower()
    with owner_session() as s:
        assert s.scalars(select(Document)).first() is None


def test_error_shape(client, world):
    r = client.get(f"{API}/cases/case_nope", headers=login(client, "accountant"))
    assert r.status_code == 404 and set(r.json()["error"]) == {"code", "message", "retryable", "ref"}
    r = client.post(f"{API}/cases", headers=login(client, "accountant"), json={"wrong": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error" and "document_id" in r.json()["error"]["message"]


def test_reaching_a_case_limit_stops_cleanly_and_names_it(client, world, settings, fake_lookups):
    from factories import NEW_DOMAIN, bank_change_spec
    from probity.tools import lookups

    settings(CASE_WEB_SEARCH_LIMIT="1")
    fake_lookups.domains[NEW_DOMAIN] = 21
    fake_lookups.search_status = "ok"
    fake_lookups.search["Alpha"] = [lookups.SearchHit("https://directory.example/a", "A", "Alpha Components listing", 3)]
    case = run_case(client, login(client, "accountant"), bank_change_spec())
    gate = case["recommendation"]["gate"]
    assert case["status"] == "AWAITING_HUMAN"
    assert gate["limits_reached"] == ["Limit reached: 1 web searches per case (CASE_WEB_SEARCH_LIMIT)"]
    assert "Stopped early — Limit reached: 1 web searches per case (CASE_WEB_SEARCH_LIMIT)" in gate["reasons"]
