"""Production crash f1571ca9… (preview 500): when the parser couldn't read the line-item table, the AI gap-filler stored
its verbatim answer string as `line_items`, and the arithmetic check then iterated a string. Line items are structured;
the AI never fills them, and anything that isn't a well-formed list is dropped (held for review) instead of crashing."""

import pytest

from probity.agents.document import PreviewCtx, extract_document, well_formed_line_items
from probity.ingestion.validators import check_arithmetic, validate_extraction
from probity.tools.base import Budget

INVOICE = (
    "Acme Traders Pvt Ltd\n"
    "Invoice No: INV-77\n"
    "Invoice Date: 01/10/2026\n"
    "Item: Corrugated boxes, two hundred pieces at rupees forty two each\n"
    "Total: 8,400.00\n"
)
ITEM_TEXT = "Corrugated boxes, two hundred pieces at rupees forty two each"


def test_ai_answer_for_line_items_cannot_crash_the_preview(db, llm):
    llm.answer("LLMExtraction", {"fields": [{"name": "line_items", "raw": ITEM_TEXT, "confidence": 0.9}], "observations": []})
    fields, validation, _text, _inj = extract_document(PreviewCtx("ws_test", "preview:doc_test", Budget.from_settings()),
                                                       INVOICE.encode(), "text/plain", {})
    assert "line_items" not in fields  # left missing → the case is held for a person, never a string in a list's place
    assert validation["arithmetic"]["ok"] is True  # nothing to sum, so nothing inconsistent
    assert fields["invoice_number"]["value"] == "INV-77"


def test_ai_is_not_asked_for_line_items(db, llm):
    llm.answer("LLMExtraction", {"fields": [], "observations": []})
    seen: list[str] = []
    from probity.llm import client

    real = client.transport

    def spy(schema, system, content, model):  # type: ignore[no-untyped-def]
        seen.append(content if isinstance(content, str) else str(content))
        return real(schema, system, content, model)

    import probity.llm.client as mod

    mod.transport = spy
    no_date = INVOICE.replace("Invoice Date: 01/10/2026\n", "")  # a missing scalar field, so the AI is asked for something
    try:
        extract_document(PreviewCtx("ws_test", "preview:doc_test", Budget.from_settings()), no_date.encode(), "text/plain", {})
    finally:
        mod.transport = real
    asked = [s.split("\n", 1)[0] for s in seen]  # "Fields needed: [...]"
    assert asked and all("invoice_date" in a and "line_items" not in a for a in asked), asked


@pytest.mark.parametrize("bad", ["Boxes x 200 @ 42", ["Boxes x 200"], [{"description": "Boxes", "qty": "200", "unit_price_minor": 4200}], [{}]])
def test_malformed_line_items_are_flagged_not_crashed(bad):
    assert not well_formed_line_items(bad)
    issues = check_arithmetic(bad, 840000, 0, 840000)
    assert issues and issues[0]["check"] == "line_items_unreadable"
    v = validate_extraction({"line_items": {"value": bad}, "subtotal": {"value": 840000}, "tax": {"value": 0}, "total": {"value": 840000}}, __import__("datetime").date.today())
    assert v["arithmetic"]["ok"] is False  # unreadable items hold the case instead of raising


def test_well_formed_line_items_still_checked():
    items = [{"description": "Boxes", "qty": 200, "unit_price_minor": 4200}]
    assert well_formed_line_items(items)
    assert check_arithmetic(items, 840000, 0, 840000) == []
    assert check_arithmetic(items, 900000, 0, 900000)[0]["check"] == "line_items_sum"
