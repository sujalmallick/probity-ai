"""Real-user path: empty workspace → vendor/history/PO import via CSV → investigate an invoice.
Plus vendor management rules, onboarding checklist, app config, demo gating and case notes."""

import csv
import io
from datetime import date, timedelta

import pytest
from conftest import login
from sqlalchemy import select

from probity.config import get_settings
from probity.db.models import User, Workspace
from probity.db.session import session_scope, set_tenant
from probity.demo import seed
from probity.demo.seed import ABC_PRICES, DEMO_DIR

API = "/api/v1"


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


@pytest.fixture()
def empty_ws(client):
    """A brand-new workspace with an owner, an approver and an accountant — no vendors, no history."""
    seed.reset_db()
    seed.write_demo_files()
    with session_scope() as s:
        ws = Workspace(name="Acme Traders", policy={})
        s.add(ws)
        s.flush()
        set_tenant(s, ws.id)
        for name, email, role in [("Olivia Owner", "olivia@acme.test", "owner"), ("Arjun Approver", "arjun@acme.test", "approver"), ("Anita Accounts", "anita@acme.test", "accountant")]:
            s.add(User(workspace_id=ws.id, name=name, email=email, role=role))
        wsid = ws.id
    return wsid


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
        ["ABC Supplies Pvt Ltd", "27AABCA1234F1Z9", "Plot 14, MIDC Bhosari, Pune, Maharashtra 411026", "abcsupplies.in", "50200012341234", "HDFC0001234", "yes", "R. Kulkarni", "accounts@abcsupplies.in", "yes"],
        ["Broken GSTIN Ltd", "27AABCA1234F1Z0", "", "", "", "", "", "", "", ""],
        ["=HYPERLINK(\"http://evil\")", "", "", "", "", "", "", "", "", ""],
    ])
    r = upload(client, acc, "vendors", vendors)
    rep = r.json()
    assert r.status_code == 200 and rep["dry_run"] and rep["rows_ok"] == 0  # accountant can't import *verified* bank/contacts
    assert any(e["field"] == "bank_verified" for e in rep["errors"])
    rep = upload(client, owner, "vendors", vendors).json()
    assert rep["rows_ok"] == 1 and {e["field"] for e in rep["errors"]} == {"gstin", "*"}
    assert upload(client, owner, "vendors", vendors, dry_run=False).status_code == 400  # refuses while rows are invalid
    rep = upload(client, owner, "vendors", vendors, dry_run=False, skip_invalid=True).json()
    assert rep["committed"] and rep["created"] == 1

    # --- 12 months of paid invoice history (unit price averages exactly ₹590)
    ih = ["vendor_gstin", "invoice_number", "invoice_date", "total", "bank_account_number", "po_number", "item_description", "qty", "unit_price"]
    today = date.today()
    hist = [["27AABCA1234F1Z9", f"INV-{4700 + k}", (today - timedelta(days=30 * (12 - k) + 5)).strftime("%d/%m/%Y"),
             f"{p * 300 * 118 // 10000:,}", "50200012341234", f"PO-{7000 + k}", "Industrial Components", "300", f"{p / 100:.2f}"] for k, p in enumerate(ABC_PRICES)]
    rep = upload(client, acc, "invoices", _csv(ih, hist), dry_run=False).json()
    assert rep["committed"] and rep["created"] == 12, rep
    # --- the open PO the new invoice references
    ph = ["po_number", "vendor_gstin", "po_date", "item_description", "qty", "unit_price"]
    rep = upload(client, acc, "purchase_orders", _csv(ph, [["PO-7710", "27AABCA1234F1Z9", (today - timedelta(days=10)).isoformat(), "Industrial Components", "500", "590"]]), dry_run=False).json()
    assert rep["created"] == 1
    assert len(client.get(f"{API}/imports", headers=acc).json()["items"]) == 3

    ob = client.get(f"{API}/workspace/onboarding", headers=owner).json()
    done = {st["key"]: st["done"] for st in ob["steps"]}
    assert done["vendors"] and done["verified_bank"] and done["history"] and done["purchase_orders"] and not done["first_case"]

    # --- a real invoice against the imported baseline: same 70 HIGH result as the demo
    doc = client.post(f"{API}/documents", headers=acc, files={"file": ("invoice_4821.pdf", (DEMO_DIR / "invoice_4821.pdf").read_bytes(), "application/pdf")}).json()
    cid = client.post(f"{API}/cases", headers=acc, json={"document_id": doc["document_id"]}).json()["case_id"]
    case = client.get(f"{API}/cases/{cid}", headers=acc).json()
    assert case["risk"]["score"] == 70 and case["risk"]["tier"] == "HIGH", case["risk"]
    d = client.post(f"{API}/cases/{cid}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    assert d.status_code == 200
    draft = client.get(f"{API}/cases/{cid}/drafts", headers=appr).json()["items"][0]
    assert draft["to_email"] == "accounts@abcsupplies.in" and draft["recipient_verified"]  # the imported verified contact
    assert client.get(f"{API}/workspace/onboarding", headers=owner).json()["complete"]


def test_vendor_management_rules(client, empty_ws):
    owner, appr, acc = hdr(client, "owner"), hdr(client, "approver"), hdr(client, "accountant")
    assert client.post(f"{API}/vendors", headers=acc, json={"name": "Bad", "gstin": "27AABCA1234F1Z0"}).status_code == 400
    v = client.post(f"{API}/vendors", headers=acc, json={"name": "Kaveri Packaging Pvt Ltd", "gstin": "29AACCK5521M1Z9", "website": "https://www.kaveripack.in/"}).json()
    assert v["pan"] == "AACCK5521M"
    assert client.post(f"{API}/vendors", headers=acc, json={"name": "Dup", "gstin": "29AACCK5521M1Z9"}).status_code == 409
    vid = v["id"]
    # accountants may add *unverified* accounts; verifying requires an approver and a note
    assert client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": "91802004455667", "verified": True, "verification_note": "called"}).status_code == 403
    a = client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": "9180 2004 4556 67", "ifsc": "ICIC0000412"}).json()
    assert a["account"] == "XXXX5667" and not a["verified"]
    assert client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True}).status_code == 400  # note required
    assert client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True, "verification_note": "Called Meera on the number on file"}).json()["verified"]
    c = client.post(f"{API}/vendors/{vid}/contacts", headers=acc, json={"name": "Meera", "email": "Billing@KaveriPack.in"}).json()
    assert c["email"] == "billing@kaveripack.in" and not c["verified"]
    assert client.patch(f"{API}/vendors/{vid}/contacts/{c['id']}", headers=acc, json={"verified": True, "verification_note": "trust me"}).status_code == 403
    detail = client.get(f"{API}/vendors/{vid}", headers=acc).json()
    assert detail["domains"][0]["domain"] == "kaveripack.in" and detail["verified_bank_accounts"] == 1
    assert client.patch(f"{API}/vendors/{vid}", headers=acc, json={"archived": True}).json()["archived"]
    assert client.get(f"{API}/vendors", headers=acc).json()["items"] == []
    assert len(client.get(f"{API}/vendors?include_archived=true", headers=acc).json()["items"]) == 1
    assert client.get(f"{API}/imports/templates/vendors", headers=acc).text.startswith("name,gstin")


def test_app_config_and_demo_gating(client, empty_ws, monkeypatch):
    cfg = client.get(f"{API}/app/config").json()  # public: no auth
    assert cfg["features"]["demo"] and cfg["auth"]["demo_login"] and cfg["integrations"]["ai"] == "offline"
    monkeypatch.setattr(get_settings(), "demo_features", False)
    cfg = client.get(f"{API}/app/config").json()
    assert not cfg["features"]["demo"] and not cfg["auth"]["demo_login"] and not cfg["features"]["simulated_inbox"]
    assert client.get(f"{API}/auth/demo-users").status_code == 404


def test_case_notes(client, empty_ws):
    from test_demo_flow import upload_and_run

    acc, viewer = hdr(client, "accountant"), None
    case = upload_and_run(client, acc, "invoice_kaveri_clean.pdf")
    n = client.post(f"{API}/cases/{case['id']}/notes", headers=acc, json={"text": "Called Kaveri, all fine."})
    assert n.status_code == 201
    notes = client.get(f"{API}/cases/{case['id']}/notes", headers=acc).json()["items"]
    assert notes[0]["text"] == "Called Kaveri, all fine." and notes[0]["author"] == "Anita Accounts"
    with session_scope() as s:
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
    vid = client.post(f"{API}/vendors", headers=acc, json={"name": "Kaveri Packaging Pvt Ltd", "gstin": "29AACCK5521M1Z9"}).json()["id"]
    a = client.post(f"{API}/vendors/{vid}/bank-accounts", headers=acc, json={"account_number": "91802004455667"}).json()
    c = client.post(f"{API}/vendors/{vid}/contacts", headers=acc, json={"name": "Meera", "email": "billing@kaveripack.in"}).json()
    detail = client.get(f"{API}/vendors/{vid}", headers=acc).json()
    assert detail["accounts"][0]["verification"] is None and detail["contacts"][0]["verification"] is None
    client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": True, "verification_note": "Called Meera on the number on file"})
    client.patch(f"{API}/vendors/{vid}/contacts/{c['id']}", headers=appr, json={"verified": True, "verification_note": "Confirmed at onboarding call"})
    detail = client.get(f"{API}/vendors/{vid}", headers=acc).json()
    va, vc = detail["accounts"][0]["verification"], detail["contacts"][0]["verification"]
    assert va["by"]["name"] == "Arjun Approver" and va["method"] == "manual" and va["note"] == "Called Meera on the number on file" and va["at"]
    assert vc["by"]["name"] == "Arjun Approver" and vc["note"] == "Confirmed at onboarding call" and detail["contacts"][0]["verified_method"] == "manual"
    client.patch(f"{API}/vendors/{vid}/bank-accounts/{a['id']}", headers=appr, json={"verified": False})
    assert client.get(f"{API}/vendors/{vid}", headers=acc).json()["accounts"][0]["verification"] is None

    # imported as verified → method "import", by the importing user
    vh = ["name", "gstin", "bank_account_number", "ifsc", "bank_verified"]
    upload(client, owner, "vendors", _csv(vh, [["ABC Supplies Pvt Ltd", "27AABCA1234F1Z9", "50200012341234", "HDFC0001234", "yes"]]), dry_run=False)
    abc = next(v for v in client.get(f"{API}/vendors", headers=acc).json()["items"] if v["gstin"] == "27AABCA1234F1Z9")
    vi = client.get(f"{API}/vendors/{abc['id']}", headers=acc).json()["accounts"][0]["verification"]
    assert vi["method"] == "import" and vi["by"]["name"] == "Olivia Owner"

    # notes carry the author's role
    from test_demo_flow import upload_and_run

    case = upload_and_run(client, acc, "invoice_kaveri_clean.pdf")
    assert client.post(f"{API}/cases/{case['id']}/notes", headers=appr, json={"text": "Checked with Meera"}).json()["author_role"] == "approver"
    assert client.get(f"{API}/cases/{case['id']}/notes", headers=acc).json()["items"][0]["author_role"] == "approver"


def test_out_of_band_confirmation_provenance_on_vendor(client):
    """Bank/domain verified through a case's out-of-band confirmation show the approver and their note."""
    from conftest import login as demo_login
    from test_demo_flow import upload_and_run

    acc, appr = demo_login(client, "accountant"), demo_login(client, "approver")
    case = upload_and_run(client, acc, "invoice_4821.pdf")
    client.post(f"{API}/cases/{case['id']}/decision", headers=appr, json={"decision": "REQUEST_VERIFICATION", "reason": "verify"})
    d = client.get(f"{API}/cases/{case['id']}/drafts", headers=appr).json()["items"][0]
    client.post(f"{API}/cases/{case['id']}/drafts/{d['id']}/send", headers=appr, json={})
    reply = client.post(f"{API}/demo/vendor-reply/{case['id']}?kind=legit", headers=acc).json()
    note = "Called R. Kulkarni on +91 20 4000 1000 (number on file)"
    assert client.post(f"{API}/cases/{case['id']}/out-of-band-confirmation", headers=appr,
                       json={"claim_ids": reply["claim_ids"], "method": "phone_known_contact", "note": note, "confirmed_account_last4": "9812"}).status_code == 200
    v = client.get(f"{API}/vendors/{case['vendor_id']}", headers=acc).json()
    new_acct = next(a for a in v["accounts"] if a["account"] == "XXXX9812")
    assert new_acct["verification"]["note"] == note and new_acct["verification"]["method"] == "phone_known_contact"
    me = client.get(f"{API}/me", headers=appr).json()
    assert new_acct["verification"]["by"] == {"id": me["id"], "name": me["name"]} and new_acct["verification"]["at"]
    kyc = next(a for a in v["accounts"] if a["account"] == "XXXX1234")["verification"]
    assert kyc["method"] == "onboarding_kyc" and kyc["by"] is None
