"""Out-of-band confirmation is bound to the invoice, never to the reply (H2): it can only verify the account the invoice
pays and the domain it came from, the approver re-types the confirmed digits, and a statement confirms once."""

from types import SimpleNamespace

import pytest
from conftest import login
from pydantic import ValidationError
from sqlalchemy import select
from test_demo_flow import API, upload_and_run

from probity.agents import action
from probity.db.models import VendorBankAccount, VendorDomain
from probity.db.session import session_scope

NOTE = "Called R. Kulkarni on the number in our vendor master; confirmed the account."


def _awaiting_reply(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    assert client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify bank change"}).status_code == 200
    draft = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    assert client.post(f"{API}/cases/{case['id']}/drafts/{draft['id']}/send", headers=appr, json={}).status_code == 200
    return case, acc, appr


def _reply(client, acc, case, body):
    r = client.post(f"{API}/cases/{case['id']}/vendor-reply", headers=acc, json={"from_email": "accounts@abcsupplies.in", "body": body})
    assert r.status_code == 200, r.text
    return r.json()["claim_ids"]


def _oob(client, appr, case, claim_ids, **extra):
    return client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr,
                       json={"claim_ids": claim_ids, "method": "phone_known_contact", "known_channel": True, "note": NOTE, **extra})


def _verified_9812(case):
    with session_scope() as s:
        return any(a.verified for a in s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == case["vendor_id"], VendorBankAccount.last4 == "9812")))


def test_bank_confirmation_requires_retyped_invoice_digits(client):
    case, acc, appr = _awaiting_reply(client)
    claims = client.post(f"{API}/demo/vendor-reply/{case['id']}?kind=legit", headers=acc).json()["claim_ids"]
    assert _oob(client, appr, case, claims).status_code == 400
    assert _oob(client, appr, case, claims, confirmed_account_last4="1234").status_code == 400
    assert not _verified_9812(case)
    assert _oob(client, appr, case, claims, confirmed_account_last4="9812").status_code == 200
    assert _verified_9812(case)


def test_reply_naming_another_account_cannot_whitelist_the_invoice_account(client):
    """The fraudster's reply names the real account; the approver's call confirms it; the invoice's account must stay unverified."""
    case, acc, appr = _awaiting_reply(client)
    claims = _reply(client, acc, case, "Please confirm our bank account ending 1234 is the one we always use.")
    r = _oob(client, appr, case, claims, confirmed_account_last4="9812")
    assert r.status_code == 400 and "XXXX1234" in r.json()["error"]["message"]
    assert not _verified_9812(case)


def test_domain_from_reply_is_never_written_to_vendor_master(client):
    case, acc, appr = _awaiting_reply(client)
    claims = _reply(client, acc, case, "Our billing email domain is now attacker-pay.com for all invoices.")
    assert _oob(client, appr, case, claims).status_code == 400
    with session_scope() as s:
        assert s.scalars(select(VendorDomain).where(VendorDomain.domain == "attacker-pay.com")).first() is None


def test_statement_confirms_only_once(client):
    case, acc, appr = _awaiting_reply(client)
    claims = client.post(f"{API}/demo/vendor-reply/{case['id']}?kind=legit", headers=acc).json()["claim_ids"]
    assert _oob(client, appr, case, claims, confirmed_account_last4="9812").status_code == 200
    assert _oob(client, appr, case, claims, confirmed_account_last4="9812").status_code in (409,)


def test_reply_statements_must_quote_the_reply():
    with pytest.raises(ValidationError):
        action.ReplyStatement(kind="domain", quote="", value="attacker-pay.example")
    with pytest.raises(ValidationError):
        action.ReplyStatement(kind="wire", quote="please wire to the new account", value="1234")


def test_value_not_in_quote_is_dropped(monkeypatch):
    body = "We confirm our billing domain is abcsupplies.in as always."
    crafted = action.ReplyAnalysis(statements=[action.ReplyStatement(kind="domain", quote=body, value="attacker-pay.example")])
    monkeypatch.setattr(action.llm, "generate", lambda **kw: crafted)
    ctx = SimpleNamespace(tags={}, budget=None)
    case = SimpleNamespace(extraction={"sender_domain": {"value": "abcsupplies-pay.in"}})
    claims, _ = action.analyze_reply(ctx, case, {"contacts": [], "domains": []}, "accounts@abcsupplies.in", body)
    assert len(claims) == 1 and claims[0].data["value"] is None
