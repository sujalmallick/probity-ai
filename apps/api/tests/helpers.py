"""HTTP helpers shared by the API tests."""

from __future__ import annotations

from factories import InvoiceSpec, pdf

API = "/api/v1"


def upload(client, h: dict, spec_or_bytes, name: str = "invoice.pdf") -> str:  # type: ignore[no-untyped-def]
    data = pdf(spec_or_bytes) if isinstance(spec_or_bytes, InvoiceSpec) else spec_or_bytes
    r = client.post(f"{API}/documents", headers=h, files={"file": (name, data, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["document_id"]


def run_case(client, h: dict, spec_or_bytes, corrections: dict | None = None) -> dict:  # type: ignore[no-untyped-def]
    doc = upload(client, h, spec_or_bytes)
    body = {"document_id": doc, **({"corrections": corrections} if corrections else {})}
    r = client.post(f"{API}/cases", headers=h, json=body)
    assert r.status_code == 201, r.text
    return client.get(f"{API}/cases/{r.json()['case_id']}", headers=h).json()


def contributions(case: dict) -> dict:
    return {c["signal"]: c["points"] for c in case["risk"]["contributions"] if c["points"]}


def gate(case: dict) -> dict:
    return case["recommendation"].get("gate") or {}


def send_verification(client, case: dict, appr: dict) -> dict:  # type: ignore[no-untyped-def]
    r = client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify bank change"})
    assert r.status_code == 200, r.text
    draft = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    r = client.post(f"{API}/cases/{case['id']}/drafts/{draft['id']}/send", headers=appr, json={})
    assert r.status_code == 200, r.text
    return draft


def legit_reply_body(case: dict) -> str:
    last4 = (case["invoice"].get("bank_account") or {}).get("last4") or "9812"
    return ("Dear Accounts Payable team,\n\nThank you for checking. We confirm that this invoice was issued by us.\n"
            f"We moved our banking to a new account ending {last4} last month; the bank letter on our letterhead is attached.\n"
            "Our billing team now also sends invoices from a new email domain.\n\nRegards,\nA. Contact")


def record_reply(client, case: dict, h: dict, from_email: str, body: str) -> dict:  # type: ignore[no-untyped-def]
    r = client.post(f"{API}/cases/{case['id']}/vendor-reply", headers=h, json={"from_email": from_email, "body": body})
    assert r.status_code == 200, r.text
    return r.json()
