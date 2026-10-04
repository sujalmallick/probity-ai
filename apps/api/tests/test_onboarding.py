"""Real-user path: empty workspace → vendor/history/PO import via CSV → investigate an invoice.
Plus vendor management rules, the onboarding checklist, manual GST entries and case notes."""

import csv
import io
from datetime import date, timedelta

import pytest
from conftest import login, owner_session
from factories import NEW_DOMAIN, VENDOR_A, VENDOR_B, bank_change_spec, clean_spec, make_user, make_workspace, pdf
from helpers import API, legit_reply_body, record_reply, run_case, send_verification
from sqlalchemy import select

from probity.db.models import User

BAD_GSTIN = VENDOR_A.gstin[:-1] + ("0" if VENDOR_A.gstin[-1] != "0" else "1")


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture()
def empty_ws(client, settings, fake_lookups):
    """A brand-new workspace with an owner, an approver and an accountant — no vendors, no history."""
    with owner_session() as s:
        ws = make_workspace(s, "Acme Traders")
        for name, email, role in [("Olivia Owner", "olivia@acme.test", "owner"), ("Arjun Approver", "arjun@acme.test", "approver"),
                                  ("Anita Accounts", "anita@acme.test", "accountant")]:
            make_user(s, ws.id, role, email=email, name=name)
    settings(EMAIL_ALLOWLIST=VENDOR_A.contact_email)
    fake_lookups.domains[NEW_DOMAIN] = 21
    return ws.id


def hdr(client, role):
    return login(client, role)


def upload(client, h, kind, data, **params):
    q = "&".join(f"{k}={str(v).lower()}" for k, v in params.items())
    return client.post(f"{API}/imports/{kind}?{q}", headers=h, files={"file": (f"{kind}.csv", data, "text/csv")})


def test_empty_workspace_to_first_investigation(client, empty_ws):
    owner, appr, acc = hdr(client, "owner"), hdr(client, "approver"), hdr(client, "accountant")
    ob = client.get(f"{API}/workspace/onboarding", headers=owner).json()
    assert not ob["complete"] and ob["steps"][0]["key"] == "vendors" and not ob["steps"][0]["done"]

    # --- vendors CSV: one good row, one bad GSTIN, one formula-injection cell
    vh = ["name", "gstin", "address", "website", "bank_account_number", "ifsc", "bank_verified", "contact_name", "contact_email", "contact_verified"]
    vendors = _csv(vh, [
        [VENDOR_A.name, VENDOR_A.gstin, VENDOR_A.address, VENDOR_A.domain, VENDOR_A.account, VENDOR_A.ifsc, "yes", VENDOR_A.contact_name, VENDOR_A.contact_email, "yes"],
        ["Broken GSTIN Ltd", BAD_GSTIN, "", "", "", "", "", "", "", ""],
        ["=HYPERLINK(\"http://evil\")", "", "", "", "", "", "", "", "", ""],
    ])
    r = upload(client, acc, "vendors", vendors)
    rep = r.json()
    assert r.status_code == 200 and rep["dry_run"] and rep["rows_ok"] == 0  # accountant can't import *verified* bank/contacts
    assert any(e["field"] == "bank_verified" for e in rep["errors"])
    rep = upload(client, owner, "vendors", vendors).json()
    assert rep["rows_ok"] == 1 and {e["field"] for e in rep["errors"]} == {"gstin", "*"}
    assert upload(client, owner, "vendors", vendors, dry_run=False).status_code == 400  # refuses while rows are invalid
    r = upload(client, owner, "vendors", vendors, dry_run=False, skip_invalid=True)
    assert r.status_code == 400 and "verification_note" in r.text  # verified rows need a written note, like manual verification
    rep = upload(client, owner, "vendors", vendors, dry_run=False, skip_invalid=True, verification_note="confirmed_by_phone_on_the_number_on_file").json()
    assert rep["committed"] and rep["created"] == 1

    # --- 12 months of paid invoice history (unit price averages exactly ₹590)
    ih = ["vendor_gstin", "invoice_number", "invoice_date", "total", "bank_account_number", "po_number", "item_description", "qty", "unit_price"]
    today = date.today()
    hist = [[VENDOR_A.gstin, f"AC-{4700 + k}", (today - timedelta(days=30 * (12 - k) + 5)).strftime("%d/%m/%Y"),
             f"{p * 300 * 118 // 10000:,}", VENDOR_A.account, f"PO-{7000 + k}", VENDOR_A.item, "300", f"{p / 100:.2f}"] for k, p in enumerate(VENDOR_A.prices)]
    rep = upload(client, acc, "invoices", _csv(ih, hist), dry_run=False).json()
    assert rep["committed"] and rep["created"] == 12, rep
    # --- the open PO the new invoice references
    ph = ["po_number", "vendor_gstin", "po_date", "item_description", "qty", "unit_price"]
    rep = upload(client, acc, "purchase_orders", _csv(ph, [["PO-7710", VENDOR_A.gstin, (today - timedelta(days=10)).isoformat(), VENDOR_A.item, "500", "590"]]), dry_run=False).json()
    assert rep["created"] == 1
    assert len(client.get(f"{API}/imports", headers=acc).json()["items"]) == 3

    ob = client.get(f"{API}/workspace/onboarding", headers=owner).json()
    done = {st["key"]: st["done"] for st in ob["steps"]}
    assert done["vendors"] and done["verified_bank"] and done["history"] and done["purchase_orders"] and not done["first_case"]

    # --- an invoice against the imported baseline: three verified anomalies
    doc = client.post(f"{API}/documents", headers=acc, files={"file": ("invoice.pdf", pdf(bank_change_spec()), "application/pdf")}).json()
    cid = client.post(f"{API}/cases", headers=acc, json={"document_id": doc["document_id"]}).json()["case_id"]
    case = client.get(f"{API}/cases/{cid}", headers=acc).json()
    assert case["risk"]["score"] == 70 and case["risk"]["tier"] == "HIGH", case["risk"]
    d = client.post(f"{API}/cases/{cid}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    assert d.status_code == 200
    draft = client.get(f"{API}/cases/{cid}/drafts", headers=appr).json()["items"][0]
    assert draft["to_email"] == VENDOR_A.contact_email and draft["recipient_verified"]  # the imported verified contact
    assert client.get(f"{API}/workspace/onboarding", headers=owner).json()["complete"]


def test_vendor_management_rules(client, empty_ws):
    owner, appr, acc = hdr(client, "owner"), hdr(client, "approver"), hdr(client, "accountant")
    assert client.post(f"{API}/vendors", headers=acc, json={"name": "Bad", "gstin": BAD_GSTIN}).status_code == 400
    v = client.post(f"{API}/vendors", headers=acc, json={"name": VENDOR_B.name, "gstin": VENDOR_B.gstin, "website": f"https://www.{VENDOR_B.domain}/"}).json()
    assert v["pan"] == VENDOR_B.pan
    assert client.post(f"{API}/vendors", headers=acc, json={"name": "Dup", "gstin": VENDOR_B.gstin}).status_code == 409
    vid = v["id"]
    # accountants may add *unverified* accounts; verifying requires an approver and a note
    assert client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": VENDOR_B.account, "verified": True, "verification_note": "called"}).status_code == 403
    spaced = " ".join(VENDOR_B.account[i:i + 4] for i in range(0, len(VENDOR_B.account), 4))
    a = client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": spaced, "ifsc": VENDOR_B.ifsc}).json()
    assert a["account"] == "XXXX5667" and not a["verified"]
    assert client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True}).status_code == 400  # note required
    assert client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True, "verification_note": "Called the contact on the number on file"}).json()["verified"]
    c = client.post(f"{API}/vendors/{vid}/contacts", headers=acc, json={"name": "B. Contact", "email": VENDOR_B.contact_email.upper()}).json()
    assert c["email"] == VENDOR_B.contact_email and not c["verified"]
    assert client.patch(f"{API}/vendors/{vid}/contacts/{c['id']}", headers=acc, json={"verified": True, "verification_note": "trust me"}).status_code == 403
    detail = client.get(f"{API}/vendors/{vid}", headers=acc).json()
    assert detail["domains"][0]["domain"] == VENDOR_B.domain and detail["verified_bank_accounts"] == 1
    assert client.patch(f"{API}/vendors/{vid}", headers=acc, json={"archived": True}).json()["archived"]
    assert client.get(f"{API}/vendors", headers=acc).json()["items"] == []
    assert len(client.get(f"{API}/vendors?include_archived=true", headers=acc).json()["items"]) == 1
    assert client.get(f"{API}/imports/templates/vendors", headers=acc).text.startswith("name,gstin")


def test_case_notes(client, empty_ws):
    acc = hdr(client, "accountant")
    case = run_case(client, acc, clean_spec())
    n = client.post(f"{API}/cases/{case['id']}/notes", headers=acc, json={"text": "Called the vendor, all fine."})
    assert n.status_code == 201
    notes = client.get(f"{API}/cases/{case['id']}/notes", headers=acc).json()["items"]
    assert notes[0]["text"] == "Called the vendor, all fine." and notes[0]["author"] == "Anita Accounts"
    with owner_session() as s:
        assert s.scalars(select(User).where(User.email == "anita@acme.test")).first() is not None


def test_json_declares_utf8_and_text_survives(client, empty_ws):
    r = client.get(f"{API}/workspace/onboarding", headers=hdr(client, "owner"))
    assert r.headers["content-type"] == "application/json; charset=utf-8"
    why = next(st["why"] for st in r.json()["steps"] if st["key"] == "verified_contacts")
    assert "\u2014" in why and "\u00e2\u20ac" not in why  # em dash intact, no mojibake
    err = client.get(f"{API}/vendors/nope", headers=hdr(client, "owner"))
    assert err.status_code == 404 and err.headers["content-type"] == "application/json; charset=utf-8"


def test_verification_provenance_and_note_roles(client, empty_ws):
    owner, appr, acc = hdr(client, "owner"), hdr(client, "approver"), hdr(client, "accountant")
    vid = client.post(f"{API}/vendors", headers=acc, json={"name": VENDOR_B.name, "gstin": VENDOR_B.gstin}).json()["id"]
    a = client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": VENDOR_B.account}).json()
    c = client.post(f"{API}/vendors/{vid}/contacts", headers=acc, json={"name": "B. Contact", "email": VENDOR_B.contact_email}).json()
    detail = client.get(f"{API}/vendors/{vid}", headers=acc).json()
    assert detail["accounts"][0]["verification"] is None and detail["contacts"][0]["verification"] is None
    client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True, "verification_note": "Called the contact on the number on file"})
    client.patch(f"{API}/vendors/{vid}/contacts/{c['id']}", headers=appr, json={"verified": True, "verification_note": "Confirmed at onboarding call"})
    detail = client.get(f"{API}/vendors/{vid}", headers=acc).json()
    va, vc = detail["accounts"][0]["verification"], detail["contacts"][0]["verification"]
    assert va["by"]["name"] == "Arjun Approver" and va["method"] == "manual" and va["note"] == "Called the contact on the number on file" and va["at"]
    assert vc["by"]["name"] == "Arjun Approver" and vc["note"] == "Confirmed at onboarding call" and detail["contacts"][0]["verified_method"] == "manual"
    client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": False})
    assert client.get(f"{API}/vendors/{vid}", headers=acc).json()["accounts"][0]["verification"] is None

    # imported as verified → method "import", by the importing user
    vh = ["name", "gstin", "bank_account_number", "ifsc", "bank_verified"]
    upload(client, owner, "vendors", _csv(vh, [[VENDOR_A.name, VENDOR_A.gstin, VENDOR_A.account, VENDOR_A.ifsc, "yes"]]), dry_run=False,
           verification_note="confirmed_by_phone_on_the_number_on_file")
    abc = next(v for v in client.get(f"{API}/vendors", headers=acc).json()["items"] if v["gstin"] == VENDOR_A.gstin)
    vi = client.get(f"{API}/vendors/{abc['id']}", headers=acc).json()["accounts"][0]["verification"]
    assert vi["method"] == "import" and vi["by"]["name"] == "Olivia Owner"

    # notes carry the author's role
    case = run_case(client, acc, clean_spec())
    assert client.post(f"{API}/cases/{case['id']}/notes", headers=appr, json={"text": "Checked with the vendor"}).json()["author_role"] == "approver"
    assert client.get(f"{API}/cases/{case['id']}/notes", headers=acc).json()["items"][0]["author_role"] == "approver"


def test_out_of_band_confirmation_provenance_on_vendor(client, world, fake_lookups):
    """Bank/domain verified through a case's out-of-band confirmation show the approver and their note."""
    fake_lookups.domains[NEW_DOMAIN] = 21
    acc, appr = login(client, "accountant"), login(client, "approver")
    case = run_case(client, acc, bank_change_spec())
    send_verification(client, case, appr)
    reply = record_reply(client, case, acc, VENDOR_A.contact_email, legit_reply_body(case))
    note = "Called the accounts contact on the number in our vendor master"
    assert client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr,
                       json={"claim_ids": reply["claim_ids"], "method": "phone_known_contact", "known_channel": True, "note": note,
                             "confirmed_account_last4": "9812"}).status_code == 200
    v = client.get(f"{API}/vendors/{case['vendor_id']}", headers=acc).json()
    new_acct = next(a for a in v["accounts"] if a["account"] == "XXXX9812")
    assert new_acct["verification"]["note"] == note and new_acct["verification"]["method"] == "phone_known_contact"
    me = client.get(f"{API}/me", headers=appr).json()
    assert new_acct["verification"]["by"] == {"id": me["id"], "name": me["name"]} and new_acct["verification"]["at"]
    known = next(a for a in v["accounts"] if a["account"] == "XXXX1234")["verification"]
    assert known["method"] == "bank_letter" and known["by"] is None


# ---------------------------------------------------------------- GST: checksum only, manual entries clearly labelled


def test_vendor_gst_section_never_claims_registry_verification(client, world):
    acc = login(client, "accountant")
    v = client.get(f"{API}/vendors/{world.vendors['B'].id}", headers=acc).json()
    assert v["gst"] == {"gstin_format": "valid", "registry_status": "could_not_verify",
                        "registry_reason": "No GST registry provider is configured (there is no free official GSTIN lookup API)"}
    assert v["gst_manual"] is None


def test_manual_gst_entry_is_labelled_audited_and_role_checked(client, world):
    vid = world.vendors["B"].id
    body = {"legal_name": VENDOR_B.name, "status": "Active", "note": "Read on the GST portal"}
    assert client.put(f"{API}/vendors/{vid}/gst-manual", headers=login(client, "viewer"), json=body).status_code == 403
    assert client.put(f"{API}/vendors/{vid}/gst-manual", headers=login(client, "accountant"), json={**body, "status": "Verified"}).status_code == 422
    r = client.put(f"{API}/vendors/{vid}/gst-manual", headers=login(client, "accountant"), json=body)
    assert r.status_code == 200, r.text
    m = client.get(f"{API}/vendors/{vid}", headers=login(client, "viewer")).json()["gst_manual"]
    assert m["status"] == "Active" and m["source"] == "manual" and m["label"].startswith("Entered manually by ")
    assert m["entered_by"]["id"] == world.user("accountant").id and not m["stale"]
    assert client.delete(f"{API}/vendors/{vid}/gst-manual", headers=login(client, "accountant")).status_code == 204
    assert client.get(f"{API}/vendors/{vid}", headers=login(client, "viewer")).json()["gst_manual"] is None


def test_manual_gst_status_reaches_the_case_as_manual_never_registry(client, world):
    vid = world.vendors["B"].id
    client.put(f"{API}/vendors/{vid}/gst-manual", headers=login(client, "accountant"), json={"status": "Active"})
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["checks"]["gst_registry"]["status"] == "could_not_verify"
    assert case["checks"]["gst_manual"]["status"] == "passed" and case["checks"]["gst_manual"]["manual"] is True
    claim = next(c for c in case["claims"] if "entered manually" in c["statement"])
    assert "not a registry verification" in claim["statement"]
    assert case["status"] == "AUTO_CLEARED"  # Active manual status does not hold a clean invoice


def test_manual_cancelled_gst_holds_the_invoice(client, world):
    vid = world.vendors["B"].id
    client.put(f"{API}/vendors/{vid}/gst-manual", headers=login(client, "accountant"), json={"status": "Cancelled"})
    case = run_case(client, login(client, "accountant"), clean_spec())
    assert case["status"] == "AWAITING_HUMAN"
    assert any(r.startswith("GST status entered manually as Cancelled") for r in case["recommendation"]["gate"]["reasons"])
    assert case["risk"]["score"] == 0  # the engine's weights are unchanged; the gate holds it for a person
