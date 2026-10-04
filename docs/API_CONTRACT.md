# Probity API contract (for the web app)

Owner: IMPLEMENTATION (backend). Consumers: apps/web. Machine-readable schema: [`docs/openapi.json`](openapi.json)
(regenerate: `python -m probity.api.dump_openapi`), live at `http://127.0.0.1:8010/docs` in dev.

## Conventions

- Base path `/api/v1`. JSON. Auth: `Authorization: Bearer <token>` header **only** (never in URLs). Use `lib/api.ts`.
- Roles (server-enforced): `viewer < accountant < approver < owner`. UI should hide/disable actions below the role and show *why* (tooltip).
- Errors: `{ "error": { "code": "bad_request|forbidden|not_found|conflict|unsupported_document|http_error", "message": "human-readable", "request_id": "…" } }`.
  Show `message` to the user as-is — messages are written for end users.
  Status codes: 400 validation, 401 sign-in needed, 403 role/MFA, 404 not found, 409 state conflict, 415 bad upload, 429 rate limit, 503 degraded.
- Money: integer **minor units** (paise) + currency. Format with `inr()` in `lib/format.ts` (Indian grouping).
- Timestamps: ISO-8601 UTC (`…+00:00`). Bank accounts are always masked (`XXXX1234`).
- `require MFA` = may return 403 *"this action requires multi-factor authentication…"* when the workspace policy `require_mfa_for_approvals` is on (Clerk only).

## 1. Public (no auth) — app shell

### `GET /app/config`  ← fetch once at startup; **landing page must render even if this fails**
```json
{
  "env": "dev|demo|test|prod", "version": "0.1.0",
  "auth": { "mode": "local|clerk", "demo_login": true, "sign_up": false },
  "features": { "landing_page": true, "demo": true, "benchmark": true, "simulated_inbox": true },
  "integrations": { "ai": "live|offline", "web_search": "live|offline", "email": "live|offline",
                    "storage": "cloud|local", "antivirus": "on|off", "ocr": "on|off" },
  "limits": { "max_upload_mb": 15, "max_import_mb": 5 }
}
```
UI rules:
| Flag false → hide | |
|---|---|
| `features.demo` | Dashboard "Demo invoices" panel, Policy "Demo: agent animation speed", any demo copy |
| `features.simulated_inbox` | "Simulated vendor inbox / Deliver vendor reply" buttons on the case page |
| `features.benchmark` | Benchmark nav item + page |
| `auth.demo_login` | Demo-user picker on `/login` (in clerk mode render `<SignIn/>` instead) |
| `features.landing_page` | Public landing at `/` (signed-in users go to `/dashboard`) |
Show an "Offline mode" badge only when `features.demo` is true and some integration is `offline`.

`GET /auth/config` → `{mode, demo_login}` (kept for compatibility; prefer `/app/config`).
`GET /auth/demo-users`, `POST /auth/demo-login {user_id}` → `{token, user}` — 404 unless `auth.demo_login`.

## 2. Onboarding (new workspace checklist)

### `GET /workspace/onboarding` (any role)
```json
{ "complete": false, "progress": "3/8",
  "steps": [ { "key": "vendors", "title": "Add your vendors", "required": true, "done": false,
               "detail": "0 vendor(s)", "why": "Invoices are matched to your vendor master…",
               "action": { "type": "import", "kind": "vendors" } } ] }
```
Step keys (stable): `vendors`, `verified_bank`, `verified_contacts`, `history`, `purchase_orders`, `team`, `policy`, `first_case`.
`action.type`: `"import"` → open the importer for `kind`; `"route"` → navigate to `to`.
Suggested UX: show the checklist on the Dashboard while `complete` is false (dismissible), empty states point to it.

### `PATCH /workspace {name}` (owner) → `{id, name}`
### `GET /workspace/export` (owner) → JSON download of vendors + cases (data portability).

## 3. Vendors (vendor master = the baseline every invoice is checked against)

### `GET /vendors?q=&include_archived=false` → `{items: VendorSummary[]}`
```json
VendorSummary = { "id","name","gstin","pan","address","website","archived","notes","created_at",
  "invoices": 12, "last_invoice_date": "2026-09-01", "cases": 2, "open_cases": 1,
  "verified_bank_accounts": 1, "verified_contacts": 1, "previously_flagged": false }
```
### `POST /vendors` (accountant+) `{name, gstin?, pan?, address?, website?, notes?}` → VendorSummary (201)
400 bad GSTIN checksum · 409 GSTIN already used by another vendor. PAN is derived from GSTIN if omitted.
### `GET /vendors/{id}` → VendorSummary +
```json
{ "accounts": [{"id","account":"XXXX1234","ifsc","verified","verified_method","first_seen","last_seen"}],
  "domains":  [{"id","domain","verified","verified_method"}],
  "contacts": [{"id","name","email","phone","verified"}],
  "price_history": [{"date","invoice_number","total_minor","items":[{"description","qty","unit_price_minor"}]}],
  "purchase_orders": [{"po_number","po_date","lines":[…]}],
  "prior_cases": [{"case_id","outcome","summary","peak_score","peak_tier","at"}] }
```
### `PATCH /vendors/{id}` (accountant+; changing `gstin` needs approver) `{name?, gstin?, address?, website?, notes?, archived?}`

**Verification rule (important for UI):** setting `verified: true` on a bank account, domain or contact means
"confirmed out-of-band". It requires **approver** role + `verification_note` (≥5 chars, e.g. "Called Meera on the number on file").
Show a dialog asking *how* it was verified. Accountants can add unverified items.

| Endpoint | Role | Body |
|---|---|---|
| `POST /vendors/{id}/bank-accounts` | accountant (approver if verified) · MFA | `{account_number, ifsc?, verified?, verification_note?}` → `{id, account, ifsc, verified}` · 409 duplicate |
| `PATCH /vendors/{id}/bank-accounts/{account_id}` | approver · MFA | `{verified, verification_note}` |
| `DELETE /vendors/{id}/bank-accounts/{account_id}` | approver · MFA | — (204) |
| `POST /vendors/{id}/domains` | accountant (approver if verified) | `{domain, verified?, verification_note?}` |
| `PATCH /vendors/{id}/domains/{domain_id}` | approver | `{verified, verification_note}` |
| `DELETE /vendors/{id}/domains/{domain_id}` | approver | — |
| `POST /vendors/{id}/contacts` | accountant (approver if verified) | `{name?, email, phone?, verified?, verification_note?}` |
| `PATCH /vendors/{id}/contacts/{contact_id}` | accountant; approver to change `verified` | `{name?, phone?, verified?, verification_note?}` |
| `DELETE /vendors/{id}/contacts/{contact_id}` | approver | — |
| `GET /vendors/{id}/graph` | any | `{nodes:[{id,type:"vendor|bank|domain|gstin|address",label,root?,shared?}], edges:[{source,target,rel}]}` |
| `GET /graph/shared-attributes` | any | `{items:[{type,label,vendors:[{id,name}]}]}` — attributes used by >1 vendor |

## 4. CSV import (vendors, invoice history, purchase orders)

Flow: download template → upload with **dry run** → show report → user fixes file *or* confirms "import valid rows only" → commit.

| Endpoint | Notes |
|---|---|
| `GET /imports/spec` | `{vendors|invoices|purchase_orders: {columns[], required[], help}}` — render column help |
| `GET /imports/templates/{kind}` | CSV download (header + one example row) |
| `POST /imports/{kind}?dry_run=true` (multipart `file`) | validate only; accountant+ |
| `POST /imports/{kind}?dry_run=false[&skip_invalid=true]` | commit; 400 if any row invalid and not `skip_invalid` |
| `GET /imports` | history `{items:[{id,kind,filename,status,rows_total,rows_ok,created,updated,skipped,by,at}]}` |

Report (both modes):
```json
{ "kind": "vendors", "rows_total": 3, "rows_ok": 1, "error_count": 2,
  "errors": [{"row": 3, "field": "gstin", "message": "27AABCA1234F1Z0 fails the GSTIN checksum"}],
  "preview": [{"row": 2, "name": "ABC Supplies Pvt Ltd", "gstin": "27AABCA1234F1Z9", "bank": "XXXX1234", "bank_verified": true, "contact": "accounts@abcsupplies.in"}],
  "created": 0, "updated": 0, "dry_run": true, "committed": false, "import_id": null }
```
`row` numbers match spreadsheet rows (header = row 1). `preview` shape differs per kind (show as a generic table).
Rules: import vendors **before** invoices/POs (they reference vendors by GSTIN or exact name); only approvers can import
`bank_verified=yes` / `contact_verified=yes`; dates `YYYY-MM-DD` or `DD/MM/YYYY`; amounts like `2,08,860.00`; max 5 MB / 20k rows.

## 5. Cases (unchanged unless noted)

| Endpoint | Notes |
|---|---|
| `POST /documents` (multipart `file`) | → `{document_id, sha256, filename, mime, duplicate_of}`; 415 = rejected (wrong type, active PDF content, virus) — show message. Accepts PDF, PNG/JPG (OCR) and **.eml**: for emails the attached PDF/image is read, and the *envelope sender* becomes `sender_domain` (field `via: "email_header"`); a failed DKIM check appears as extracted field `email_dkim` |
| `POST /documents/{id}/preview` | → `{fields, validation, low_confidence[], injection_detected}` before launching |
| `POST /cases {document_id, corrections?}` | starts the investigation |
| `GET /cases?tier&status&vendor_id&limit` | queue |
| `GET /cases/{id}` | full case (claims, risk, recommendation, drafts, messages, decisions, score_history) |
| `GET /cases/{id}/events` | SSE via `streamEvents()` (fetch + header; Last-Event-ID resume) |
| `GET /cases/{id}/explain`, `/evidence`, `/audit` | Why panel, evidence, audit trail |
| `GET /cases/{id}/export?format=pdf|json` | download |
| `POST /cases/{id}/decision {decision, reason}` | approver · MFA |
| drafts: `GET /cases/{id}/drafts`, `PATCH …/drafts/{draft_id}`, `POST …/drafts/{draft_id}/send {override_unverified_recipient?}` | send = approver · MFA |
| `POST /cases/{id}/out-of-band-confirmation {claim_ids, method, note, known_channel?}` | approver · MFA. `note`: required, ≥ 20 chars after trimming (`OOB_NOTE_MIN_CHARS`), ≤ 2000 — "who did you contact, on which number already on file". `known_channel`: `true` = confirmed via a channel already on file; `false` → 400 (a channel taken from the invoice/email can't confirm anything); omitted = allowed (older clients). Recorded in the audit entry. UI: ask "Did you use a phone number/email that was already on file before this invoice?" |
| `POST /cases/{id}/close {outcome, resolution}` | approver (accountant for auto-cleared) |
| **new** `GET /cases/{id}/notes` → `{items:[{id,text,author,author_id,at}]}` | any |
| **new** `POST /cases/{id}/notes {text}` | accountant+ → note (201). Show as a comment thread on the case |
| `POST /demo/vendor-reply/{id}?kind=legit|spoof` | **only when `features.simulated_inbox`** |

## 6. Team, policy, health

`GET /workspace/members`, `POST /workspace/invitations {email, role}`, `GET /workspace/invitations`, `DELETE /workspace/invitations/{id}`,
`PATCH /workspace/members/{id} {role?, active?}` (owner; 409 for last owner) · `GET|PUT /workspace/policy` (owner for PUT; response includes
`reviewed_at` once saved) · `GET /me` · `GET /dashboard/kpis` · `GET /memory/cases?q=` · `GET /ready` (public; 503 when degraded).

## Changelog
- 2026-10-04 — v1 of this contract: app config, onboarding, vendor CRUD + verification rules, CSV import, case notes, demo gating.
- 2026-10-04 — v1.1: out-of-band confirmation `known_channel` + 20-char note minimum (FRONTEND); emailed invoices (.eml) read from the
  attachment with envelope-sender domain; extracted `invoice_date`/`due_date` values are now normalized ISO dates (raw text kept in `raw`).
