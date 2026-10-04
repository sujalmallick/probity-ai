"""Out-of-band confirmation is bound to the invoice, never to the reply (H2): it can only verify the account the invoice
pays and the domain it came from, the approver re-types the confirmed digits, and a statement confirms once."""

from types import SimpleNamespace

import pytest
from conftest import login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, bank_change_spec
from helpers import API, legit_reply_body, record_reply, run_case, send_verification
from pydantic import ValidationError
from sqlalchemy import select

from probity.agents import action
from probity.db.models import VendorBankAccount, VendorDomain

NOTE = "Called the accounts contact on the number in our vendor master; confirmed the account."


@pytest.fixture()
def _new_domain(fake_lookups):
    fake_lookups.domains[NEW_DOMAIN] = 21


def _awaiting_reply(client):
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    return case, acc, appr


def _reply(client, acc, case, body):
    return record_reply(client, case, acc, VENDOR_A.contact_email, body)["claim_ids"]


def _legit(client, acc, case):
    return _reply(client, acc, case, legit_reply_body(case))


def _oob(client, appr, case, claim_ids, **extra):
    return client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr,
                       json={"claim_ids": claim_ids, "method": "phone_known_contact", "known_channel": True, "note": NOTE, **extra})


def _verified_9812(case):
    with owner_session() as s:
        return any(a.verified for a in s.scalars(select(VendorBankAccount).where(VendorBankAccount.vendor_id == case["vendor_id"], VendorBankAccount.last4 == "9812")))


def test_bank_confirmation_requires_retyped_invoice_digits(client, world, _new_domain):
    case, acc, appr = _awaiting_reply(client)
    claims = _legit(client, acc, case)
    assert _oob(client, appr, case, claims).status_code == 400
    assert _oob(client, appr, case, claims, confirmed_account_last4="1234").status_code == 400
    assert not _verified_9812(case)
    assert _oob(client, appr, case, claims, confirmed_account_last4="9812").status_code == 200
    assert _verified_9812(case)


def test_reply_naming_another_account_cannot_whitelist_the_invoice_account(client, world, _new_domain):
    """The fraudster's reply names the real account; the approver's call confirms it; the invoice's account must stay unverified."""
    case, acc, appr = _awaiting_reply(client)
    claims = _reply(client, acc, case, "Please confirm our bank account ending 1234 is the one we always use.")
    r = _oob(client, appr, case, claims, confirmed_account_last4="9812")
    assert r.status_code == 400 and "XXXX1234" in r.json()["error"]["message"]
    assert not _verified_9812(case)


def test_domain_from_reply_is_never_written_to_vendor_master(client, world, _new_domain):
    case, acc, appr = _awaiting_reply(client)
    claims = _reply(client, acc, case, "Our billing email domain is now attacker-pay.com for all invoices.")
    assert _oob(client, appr, case, claims).status_code == 400
    with owner_session() as s:
        assert s.scalars(select(VendorDomain).where(VendorDomain.domain == "attacker-pay.com")).first() is None


def test_statement_confirms_only_once(client, world, _new_domain):
    case, acc, appr = _awaiting_reply(client)
    claims = _legit(client, acc, case)
    assert _oob(client, appr, case, claims, confirmed_account_last4="9812").status_code == 200
    assert _oob(client, appr, case, claims, confirmed_account_last4="9812").status_code in (409,)


def test_reply_statements_must_quote_the_reply():
    with pytest.raises(ValidationError):
        action.ReplyStatement(kind="domain", quote="", value="attacker-pay.example")
    with pytest.raises(ValidationError):
        action.ReplyStatement(kind="wire", quote="please wire to the new account", value="1234")


def test_value_not_in_quote_is_dropped(monkeypatch):
    body = f"We confirm our billing domain is {VENDOR_A.domain} as always."
    crafted = action.ReplyAnalysis(statements=[action.ReplyStatement(kind="domain", quote=body, value="attacker-pay.example")])
    monkeypatch.setattr(action.llm, "generate", lambda **kw: crafted)
    ctx = SimpleNamespace(tags={}, budget=None, progress=lambda *a, **k: None)
    case = SimpleNamespace(extraction={"sender_domain": {"value": NEW_DOMAIN}})
    claims, _ = action.analyze_reply(ctx, case, {"contacts": [], "domains": []}, VENDOR_A.contact_email, body)
    assert len(claims) == 1 and claims[0].data["value"] is None
