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
  "env": "dev|test|prod", "version": "0.1.0",
  "auth": { "mode": "clerk", "sign_up": true, "demo_login": false },
  "features": { "landing_page": true, "demo": false, "benchmark": false, "simulated_inbox": false },
  "integrations": { "ai": "live|missing", "web_search": "live|missing", "domain_lookup": "live", "gst_registry": "unavailable",
                    "email": "live|missing", "email_allowlist_only": true, "storage": "cloud|local", "antivirus": "on|off",
                    "background_jobs": "inline|worker|missing" },
  "limits": { "max_upload_mb": 15, "max_import_mb": 5 }
}
```
There is no demo, offline or mock mode. `features.demo/benchmark/simulated_inbox` and `auth.demo_login` are always `false` and will
be removed once the web app stops reading them. Show a **Live** indicator; list integrations that are `missing` as
"not configured — affected checks report *could not verify*".

`GET /auth/config` → `{mode: "clerk", demo_login: false}` (kept for compatibility; prefer `/app/config`).
Removed (answer **410 Gone** until the web app no longer calls them, then deleted): `GET /auth/demo-users`, `POST /auth/demo-login`,
`POST /demo/seed`, `GET /demo/files/{name}`, `POST /demo/vendor-reply/{id}`, `PUT /demo/speed`, `GET /benchmark/summary`.

### Check results ("could not verify")
`case.checks[<name>] = {status, reason, ...}` with `status` one of `passed | fired | skipped | could_not_verify | failed`.
`skipped` = not applicable to this invoice; `could_not_verify` = a source failed or data was missing (never a pass, never adds points);
`failed` = the agent crashed. A **required** check that is `skipped`, `could_not_verify` or `failed` holds the invoice; the gate lists
them in `recommendation.gate.could_not_verify: [{check, status, reason}]` and as reasons `"Could not verify: <check> — <reason>"`.
SSE event `check.could_not_verify` `{agent, status: "warning", message, data: {check, reason}}` is emitted at the moment it happens.
`recommendation.summary_source`: `"ai"` or `"engine"` (AI unavailable — show "Summary written by the risk engine").

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
{ "accounts": [{"id","account":"XXXX1234","ifsc","verified","verified_method","verification","first_seen","last_seen"}],
  "domains":  [{"id","domain","verified","verified_method","verification"}],
  "contacts": [{"id","name","email","phone","verified","verified_method","verification"}],
  "price_history": [{"date","invoice_number","total_minor","items":[{"description","qty","unit_price_minor"}]}],
  "purchase_orders": [{"po_number","po_date","lines":[…]}],
  "prior_cases": [{"case_id","outcome","summary","peak_score","peak_tier","at"}] }
```
`verification` is `null` when the item is not verified, otherwise
`{ "by": {"id","name"} | null, "at": ISO | null, "method": "manual|import|phone_known_contact|bank_letter|in_person|onboarding_kyc", "note": string | null }`
— e.g. render "Verified by Vikram Mehta · 4 Oct · 'Called R. Kulkarni on the number on file'". `by` is `null` for items verified during
onboarding KYC (seed/migration); imports use the importing user and the note "Marked verified in a CSV import"; out-of-band
confirmations on a case carry that confirmation's note. Un-verifying clears it.

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
| `GET /cases?q=&status=&tier=&vendor_id=&limit=50&cursor=` | queue, newest first → `{items, next_cursor}`. `q` searches invoice number, vendor name and file name; `status`/`tier` take comma-separated values (e.g. `tier=HIGH,CRITICAL`); pass `next_cursor` back as `cursor` for the next page (`null` = last page); 400 on a bad cursor |
| `GET /cases/{id}` | full case (claims, risk, recommendation, drafts, messages, decisions, score_history) |
| `GET /cases/{id}/events` | SSE via `streamEvents()` (fetch + header; Last-Event-ID resume) |
| `GET /cases/{id}/explain`, `/evidence`, `/audit` | Why panel, evidence, audit trail |
| `GET /cases/{id}/export?format=pdf|json` | download |
| **new** `GET /cases/{id}/invoice-risk?narrate=false` | any role. Policy-driven second view of the invoice (does **not** change the case score): `{extracted, signals:[{signal, weight, score\|null, evidence}], final_score (0–1), coverage, tier, escalated_by, recommended_action, warnings, explanation:{contributions, top_drivers, tier_reason, escalations, unknown, text}, meta:{policy_source, history_size, vendor_matched}}`. `score: null` = could not be evaluated (show "unknown", never 0). `narrate=true` adds `narrative:{summary, key_points, source: "llm"\|"template", note}` (slower; always present, falls back to the template). 409 before the invoice has been read |
| `POST /cases/{id}/decision {decision, reason}` | approver · MFA |
| drafts: `GET /cases/{id}/drafts`, `PATCH …/drafts/{draft_id}`, `POST …/drafts/{draft_id}/send {override_unverified_recipient?}` | send = approver · MFA |
| `POST /cases/{id}/out-of-band-confirmation {claim_ids, method, note, known_channel?}` | approver · MFA. `note`: required, ≥ 20 chars after trimming (`OOB_NOTE_MIN_CHARS`), ≤ 2000 — "who did you contact, on which number already on file". `known_channel`: `true` = confirmed via a channel already on file; `false` → 400 (a channel taken from the invoice/email can't confirm anything); omitted = allowed (older clients). Recorded in the audit entry. UI: ask "Did you use a phone number/email that was already on file before this invoice?" |
| `POST /cases/{id}/close {outcome, resolution}` | approver (accountant for auto-cleared) |
| **new** `GET /cases/{id}/notes` → `{items:[{id,text,author,author_id,author_role,at}]}` | any |
| **new** `POST /cases/{id}/notes {text}` | accountant+ → note (201). Show as a comment thread on the case |
| `POST /cases/{id}/vendor-reply {from_email, subject?, body}` | accountant+. Records a reply received outside Probity (statements stay unverified) |

## 6. Notifications (per user)

| Endpoint | Notes |
|---|---|
| `GET /notifications?unread_only=false&limit=50` | → `{unread, items:[{id, kind, title, body, case_id, read, at}]}` newest first. Poll every ~30 s for a bell badge |
| `POST /notifications/{id}/read` | → `{id, read: true}` (404 for someone else's) |
| `POST /notifications/read-all` | → `{marked}` |

`kind`: `case_held` (approvers: an invoice needs a decision) · `vendor_replied` (approvers: confirm out-of-band) · `approval_needed`
(other approvers: second approval for dual-approval cases) · `followup_due` (approvers: no vendor reply after 2 days) · `case_failed`
(accountants+: document couldn't be investigated). Link to `/cases/{case_id}`. Also emailed to the user when email is live.

## 7. Team, policy, health

`GET /workspace/members`, `POST /workspace/invitations {email, role}`, `GET /workspace/invitations`, `DELETE /workspace/invitations/{id}`,
`PATCH /workspace/members/{id} {role?, active?}` (owner; 409 for last owner) · `GET|PUT /workspace/policy` (owner for PUT; response includes
`reviewed_at` once saved) · **new** `GET|PUT|DELETE /workspace/invoice-risk-policy` (owner for PUT/DELETE; → `{policy, source: "workspace"\|"default"}`;
PUT validates weights/tiers/escalation rules → 400 with the problems; DELETE reverts to the bundled default; both audited) · `GET /me` · `GET /dashboard/kpis` · `GET /memory/cases?q=` · `GET /ready` (public; 503 when degraded).

## Changelog
- 2026-10-04 — v1 of this contract: app config, onboarding, vendor CRUD + verification rules, CSV import, case notes, demo gating.
- 2026-10-04 — v1.1: out-of-band confirmation `known_channel` + 20-char note minimum (FRONTEND); emailed invoices (.eml) read from the
  attachment with envelope-sender domain; extracted `invoice_date`/`due_date` values are now normalized ISO dates (raw text kept in `raw`).
- 2026-10-04 — v1.2: notifications (§6); case queue search, multi-value filters and cursor pagination; `demo-users` items carry
  `workspace {id, name}` (FRONTEND).
- 2026-10-04 — v1.3: `verification {by, at, method, note}` on vendor accounts/domains/contacts (+ `verified_method` on contacts);
  `author_role` on case notes. Migration 0006 (provenance columns).
- 2026-10-04 — v1.4: `GET /cases/{id}/invoice-risk` (policy-driven, explainable invoice risk with optional LLM narrative) and
  `GET|PUT|DELETE /workspace/invoice-risk-policy`. Additive; the case score and existing fields are unchanged.
- 2026-10-04 — v2.0 (real data only): `/app/config` reshaped (Clerk only, integrations `live|missing`, `gst_registry: unavailable`);
  demo/benchmark endpoints → 410; check status `could_not_verify` + SSE `check.could_not_verify` + `gate.could_not_verify`;
  `GET /vendors/{id}` gains `gst {gstin_format, registry_status: "could_not_verify", registry_reason}` and
  `gst_manual {gstin, legal_name, status, note, entered_by:{id,name}, entered_at, stale, source:"manual", label}`;
  `PUT /vendors/{id}/gst-manual {legal_name?, status: Active|Cancelled|Suspended, note?}` (accountant+) and `DELETE` (204);
  invitations return `email_sent`/`email_note`; email to addresses outside `EMAIL_ALLOWLIST` → 400 and audited `email.blocked`.
  Migration 0007 (`vendors.gst_manual`).
