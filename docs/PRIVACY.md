# Personal data: masking, retention and erasure

Probity holds business records about vendors. Most of it is company data (names, GSTINs, bank accounts, invoices),
but a vendor's **contacts** are people: a name, an email address and a phone number. This page says how Probity
protects them, how long it keeps data, and how to erase a person on request.

## Who sees contact details

| Role | Contact emails and phones |
|---|---|
| Viewer | Masked: `a***@vendor.com`, `XXXX1234`. The domain stays visible because the spoofing checks compare it. |
| Accountant, approver, owner | In full. They need them to send verification emails and call the contact on file. |

For a viewer, masking applies wherever a contact's details can appear: the vendor's contact list, verification
emails and drafts, vendor replies, claims, evidence excerpts, the case audit trail, the live investigation timeline,
the per-agent trace, case notes and notifications. Emails, international-format phone numbers (`+91 …`) and Indian
mobile numbers are masked in free text.

Not masked: the invoice itself. Anyone in the workspace can open the uploaded file, so the fields read from it are
shown as printed. Bank account numbers are protected separately for every role (last 4 digits only; the full number
through an audited reveal for approvers).

## Retention

`RETENTION_YEARS` (default **8**) is how long invoices and their investigations are kept. Indian GST law requires
invoices and books of account to be kept for 72 months after the due date of the annual return for that year, which
can mean almost 8 years after the invoice date; 8 years covers it.

Nothing is deleted automatically. An owner can see what is past the retention period with
`GET /api/v1/workspace/retention` (number of cases older than the cut-off, and the oldest case date) and decide.

## Erasing a vendor contact

When a contact asks to have their details erased (DPDP Act 2023 / GDPR right to erasure), an **owner** opens the
vendor, finds the contact and chooses **Erase**, giving a reason. API: `POST /api/v1/vendors/{vendor_id}/contacts/{contact_id}/erase`
with `{"reason": "..."}`. It needs MFA when the workspace requires MFA for approvals. It cannot be undone.

What happens, in one transaction:

1. The audit chain is verified first. If it already fails verification, nothing is erased: re-signing a tampered
   row would hide the tampering.
2. The contact is deleted, along with any other contact row in the workspace with the same email (the same person).
3. Their name, email addresses and phone number (in any spacing, with or without `+91`) are replaced with
   `[erased]` in: sent and received emails, drafts, notifications, case notes, claims, case summaries and
   resolutions, decision reasons, vendor memory, import error reports, the investigation timeline, evidence that
   didn't come from the invoice, and the audit log.
4. One audit entry, `vendor.contact_erased`, records who erased, when, why, and how many rows changed. It does not
   record who was erased.
5. The audit chain is verified again; if it would fail, the whole erasure is rolled back.

What is kept: the invoice file and what was read from it (`cases.extraction`, evidence quoted from the invoice). An
invoice is a tax record that must be kept for the retention period, which is a legal basis for keeping it.

### How the audit log stays tamper-evident

The audit log is append-only and hash-chained: each entry's hash is an HMAC over all its columns and the previous
entry's hash (`db/audit.py`). Erasing details from an entry changes it, so its original hash can no longer be
recomputed. Instead:

- The app's database role still cannot update the audit log or evidence (no `UPDATE` grant, and a trigger). The only
  way to change a row is through two database functions, `probity_redact_audit` and `probity_redact_evidence`, which
  work only inside the caller's workspace and only for a row that already has a `privacy_redactions` record.
- Each `privacy_redactions` record carries an HMAC (keyed from `HMAC_KEY`) over the row exactly as it reads after the
  erasure, including the row's original hash and the previous row's hash.
- `verify_chain` checks that HMAC for erased rows and the original hash for every other row, and the chain links
  (`prev_hash`) for all of them. Someone with database access but not the key can't change an erased row, move a
  redaction to another row, or delete a redaction record without verification failing.

What's given up: after an erasure, nobody can prove what the erased text originally said. That's the point.
