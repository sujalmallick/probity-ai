# Probity features

What Probity can do today, grouped by area. Every item is marked:

- **Working**: built end to end (API and web app).
- **Partial**: works in the API but has no screen in the web app yet, or works only in a limited way.
- **Planned**: not built. Listed so you know it's missing, and so you can pick it up (see the last section).

Probity identifies **anomalies** and **recommends a hold**. It never makes accusations and never pays anything.

---

## 1. Uploading an invoice and reading it

| Feature | Status | Notes |
|---|---|---|
| Upload a text-based **PDF** | Working | Up to 10 MB (`MAX_UPLOAD_MB`) and 50 pages. Text is read with pdfplumber. |
| Upload an **emailed invoice (.eml)** | Working | The first PDF attachment is read. The email's real sender address becomes the sender domain, and a failed DKIM check is recorded. |
| Upload a **plain-text** invoice | Working | |
| **Scanned PDFs and photos (PNG/JPG)** | Planned | Refused with a clear message. There is no OCR. An email whose attachment is an image (or has a logo image before the PDF) is refused too. |
| File type checked by content, not by extension | Working | A renamed file can't sneak through. |
| Dangerous PDF content refused | Working | Every PDF is parsed and rewritten before it's stored. JavaScript, launch actions, embedded files and similar are refused, even inside compressed parts; forms, link actions and encryption are stripped. Password-protected PDFs are refused. |
| Virus scan | Optional | Only when a ClamAV server is configured (`CLAMAV_HOST`, needs 1–3 GB RAM). Without it the scan is skipped and the audit log says so; PDF cleaning still applies. |
| Duplicate upload detection | Working | The same file uploaded twice links to the existing case. |
| Preview before investigating | Working | Shows the extracted fields, which ones are low-confidence, and whether instruction-like text was found. |
| Correct extracted fields by hand | Working | Allowed for vendor name, GSTIN, invoice number, dates, PO number, IFSC, vendor email and address. **Not** allowed for bank account, amounts, line items or sender domain. Corrections are labelled, audited, and stop auto-clear. |
| Field extraction | Working | A rule-based parser reads the fields first. The AI only fills gaps, and its value is accepted only if that exact text appears in the document. GSTIN checksum, IFSC format, arithmetic, dates and currency are all validated. |
| Non-INR invoices | Partial | Detected. Price comparison says "could not verify" and the case is held for a person. |

## 2. The investigation (agent pipeline)

Each case runs through these steps in order. Code does the checks. The AI only helps with reading, writing search queries and explaining.

| Step | Status | What it does |
|---|---|---|
| **Document reader** | Working | Reads the invoice (see above), protects the bank account number, flags instruction-like text. |
| **Planner** (orchestrator) | Working | Matches the vendor against *your* vendor list (exact GSTIN, or a very close name), looks up case memory, and decides which checks are required for this invoice. No AI. |
| **Vendor investigator** | Working | Compares GSTIN, name and address with your vendor record. Finds bank accounts, domains or addresses shared with *another* vendor. Checks domain age through RDAP. No AI. |
| **Transaction analyst** | Working | Bank account change, duplicate invoice, price compared with past invoices, purchase-order match and quantity, dates, unusual totals. No AI. |
| **Web researcher** | Working (needs a Tavily key) | Runs only when the plan asks for it (unknown vendor, large amount, unverified bank account, or a deeper re-run). Searches the web, reads pages through a safe fetcher, and reports third-party findings with the source. |
| **Evidence verifier** | Working | Throws away any claim without evidence ("cite-or-drop"), re-checks numbers and quotes against the evidence, and uses the AI only to check that a quoted source supports a claim. |
| **Risk engine** | Working | Pure code: turns verified findings into a score (see section 3). |
| **Case analyst** | Working | Writes a short plain-English summary from the engine's results. It cannot change the score. If the AI is down, the engine's own summary is shown and labelled. |
| **Policy gate** | Working | Decides between auto-clear and "needs a person" (section 4). |
| Retries and partial results | Working | Each step is retried. If an optional step still fails, the case continues and is marked incomplete. If a core step fails, the case is marked failed and can be retried. |
| "Investigate further" re-run | Working | An approver can re-run the investigation one level deeper (up to 2 levels). |
| Self-check before the gate | Working | Before deciding, the case is checked for consistency: the score equals the sum of its points, no points without verified evidence, AI fallbacks are labelled, and so on. Any problem holds the case. Also available as `python -m probity.sanity`. |
| Per-case trace | Partial | `GET /api/v1/cases/{id}/trace` shows timing, checks and AI calls. No screen yet. |
| Choice of AI provider | Working | Anthropic Claude (default) or Google Gemini, set by `LLM_PROVIDER`. The steps and safety rules are the same. |
| AI unavailable | Working | Every AI step has a rule-based fallback that is labelled "rule-based fallback, AI unavailable". Nothing is made up. |

## 3. The risk score

The score runs from **0 to 100**. Only findings that passed verification add points. An unverified finding shows as "unconfirmed" and adds **0** points. A finding contradicted by the evidence is discarded.

| Tier | Score | Recommendation |
|---|---|---|
| LOW | 0 to 29 | Proceed (may auto-clear) |
| MEDIUM | 30 to 59 | Review |
| HIGH | 60 to 79 | Recommend hold |
| CRITICAL | 80 to 100 | Recommend hold, needs two approvers |

| Signal | Points | In plain words |
|---|---|---|
| Bank account changed | 35 | The invoice's bank account is not one you've verified for this vendor. |
| Price anomaly | 20 | A unit price is much higher than this vendor normally charges (at least 25% above the average, and statistically unusual). |
| New domain | 15 | The sender's web domain is less than 90 days old and isn't a domain you've verified for this vendor. |
| Instruction-like text in document | 15 | The document contains text that tries to give orders to a reader or to an automated system. |
| Prior confirmed issue | 15 | A previous case for this vendor, bank account or domain was closed as a confirmed issue. |
| Vendor identity mismatch | 10 | The GSTIN differs from your vendor record, or the name is clearly different. |
| Duplicate invoice | 10 | Same invoice number as before, or the same amount within 10 days. |
| Quantity exceeds PO | 10 | More units invoiced than the purchase order allows. |
| Statistical outlier | up to 10 | The total is far outside this vendor's usual range. Needs at least 30 past invoices. |
| Address mismatch | 5 | The address is clearly different from your vendor record. |
| Missing PO | 5 | No PO number, the PO isn't in your records, or it belongs to a different vendor. |
| Date anomaly | 5 | The invoice is dated in the future, or before its purchase order. |
| No transaction history | 5 | You have no approved past invoices from this vendor. |
| Round-sum amount | 0 | Just a note: the total is an exact multiple of ₹1 lakh. |
| Shared with another vendor | 0 | Adds no points, but always sends the invoice to a person. |

The first seven core signals add up to 100. The total is capped at 100. Owners can change the weights in Settings. The AI has no way to change the score.

**Second view (Working, API only):** `GET /api/v1/cases/{id}/invoice-risk` gives a policy-driven 0 to 1 score with each signal's share, which signals couldn't be evaluated, and an optional plain-English explanation. It doesn't change the case score. Owners can edit its policy through `/api/v1/workspace/invoice-risk-policy` (no screen yet).

## 4. "Could not verify" and auto-clear

When a check can't run (no API key, a network failure, not enough history, no registry), Probity says **"could not verify"** with the reason. It adds **no** points and never invents a result.

**Required checks.** If any of these isn't answered, the invoice goes to a person:
- invoice validation
- vendor identity
- bank account verification
- price comparison
- duplicate detection
- PO quantity match (only when the invoice has a PO number)

**Optional checks.** A gap here only adds an "incomplete" note:
- domain verification
- external (web) reputation

**Auto-clear happens only when every one of these is true:**
- auto-clear is on
- the tier is LOW
- the vendor is in your list
- every required check passed
- the total was read reliably
- no field was corrected by hand
- no finding fired (apart from the informational ones)
- there is no unverified high-severity claim
- the amount is under the auto-clear limit (default ₹5,00,000)
- the vendor wasn't flagged before
- all document checks passed and the currency is INR
- no usage limit was hit
- the self-check found no problems
- the vendor has approved past invoices

Otherwise the case waits for a person, and the reasons are listed.

## 5. Decisions

| Action | Who | Written reason needed? |
|---|---|---|
| Approve | Approver | Yes, if the case ever reached HIGH or CRITICAL (at least 10 letters or digits) |
| Reject | Approver | Same as approve |
| Request verification (creates a draft email to the vendor) | Approver | No |
| Investigate further (re-run deeper) | Approver | No |
| Close and save to memory (outcome: confirmed issue / cleared / inconclusive) | Approver (accountant for auto-cleared cases) | Yes, a resolution of at least 10 letters or digits |

- **Two approvers** are needed when the case reached CRITICAL, the amount is ₹10,00,000 or more, or the total was unreliable. The person who confirmed out-of-band can't be the only approver.
- **Two-factor sign-in** can be required for approvers (owner setting, off by default).
- A rejected case can't be closed as "cleared".
- A failed case can be retried, which creates a new linked case.

## 6. Vendors, past invoices and purchase orders

| Feature | Status | Notes |
|---|---|---|
| Vendor list: add, edit, archive, search | Working | GSTIN checksum checked and unique. Changing name, GSTIN or address needs an approver. No delete, only archive. |
| Bank accounts, domains and contacts per vendor | Working | Anyone from accountant up can add them. Only an approver can mark one **verified**, with a note on how it was checked. Editing a verified contact removes its verified status. |
| Manual GST status | Working | Enter what you read on the GST portal. Shown as "Entered manually by <name>", never as verified. |
| Price history and prior cases per vendor | Working | |
| CSV import (vendors, past invoices, purchase orders) | Working | Downloadable templates, a dry run with per-row errors, fix rows inline, then commit. 5 MB / 20,000 rows. Verified rows need an approver and a note. Spreadsheet formula injection is refused. |
| Add, edit or approve single past invoices and POs | Partial | API only (`api/records.py`). Rows entered by an accountant wait for an approver. No screen yet. |

## 7. Email to vendors

| Feature | Status | Notes |
|---|---|---|
| Neutral verification email drafts | Working | Written by the AI, or by a fixed neutral template (labelled) if the AI is down. Accusatory words are rejected. |
| Send only after approval | Working | An approver sends it. Sending to an address that isn't a verified contact needs an explicit override and a reason. |
| Allowlist while testing | Working | Unless `EMAIL_SEND_TO_ANY=true`, only addresses in `EMAIL_ALLOWLIST` receive email. Blocked sends are audited. |
| Email provider | Working (Resend only) | SMTP and other providers: **Planned**. |
| Follow-up reminders | Working | After 2 days without a reply, a reminder notification is created. Nothing is sent automatically. |
| Vendor replies by email (inbound webhook) | Partial | Works when `EMAIL_REPLY_DOMAIN` and `INBOUND_EMAIL_SECRET` are set and an email service forwards replies to the webhook. Bounce and out-of-office handling: **Planned**. |
| Record a vendor reply by hand | Working | |
| Replies stay unverified | Working | Whatever the vendor says is a claim worth 0 points until an approver confirms it out-of-band. Urgency or new payment instructions are flagged. |
| Out-of-band confirmation | Working | Approver only. Must use a channel **already on file** (known phone number, bank letter, in person), with a note of at least 20 characters that isn't a copy of the reply. Confirms only the invoice's own bank account or domain, then re-scores the case. |

## 8. Roles and permissions

Roles from least to most access: **viewer < accountant < approver < owner**. Each role can do everything the roles before it can.

| What | Viewer | Accountant | Approver | Owner |
|---|:-:|:-:|:-:|:-:|
| See cases, evidence, explanations, exports, memory, vendors | ✓ | ✓ | ✓ | ✓ |
| Upload invoices, preview, correct fields, start or retry a case | | ✓ | ✓ | ✓ |
| Add vendors, unverified bank accounts, domains, contacts, manual GST status | | ✓ | ✓ | ✓ |
| Import CSV (unverified rows) | | ✓ | ✓ | ✓ |
| Edit drafts, record vendor replies, add case notes, re-score | | ✓ | ✓ | ✓ |
| Close an auto-cleared case | | ✓ | ✓ | ✓ |
| Approve, reject, request verification, investigate further | | | ✓ | ✓ |
| Send emails, confirm out-of-band, reveal a full account number (audited) | | | ✓ | ✓ |
| Mark bank accounts, domains or contacts verified; edit vendor name/GSTIN/address | | | ✓ | ✓ |
| Import verified rows, approve past invoices and POs | | | ✓ | ✓ |
| Change policy (auto-clear, limits, weights, two-factor) | | | | ✓ |
| Invite or remove members, change roles, rename workspace, export all data | | | | ✓ |

## 9. Memory, audit, exports, notifications, status

| Feature | Status | Notes |
|---|---|---|
| Case memory | Working | Closing a case saves its outcome. The next invoice from the same vendor, bank account or domain sees it. Searchable on the Memory page. |
| Audit log | Working | Every action is recorded in a tamper-evident hash chain. The database refuses edits or deletes. Shown per case. |
| Case export (PDF or JSON) | Working | |
| Whole-workspace export (JSON) | Partial | Owner only, API only. |
| Notifications (case held, vendor replied, second approval needed, follow-up due, case failed) | Partial | Created and emailed (allowlist applies). The web app has no bell/notification screen yet. |
| System status | Working | `/api/v1/health`, `/api/v1/ready` (database, migrations, integrations), a startup checklist, and `python -m probity.check` to test every integration. |
| Dashboard numbers | Working | Invoices processed, % auto-cleared, average investigation time, amount on hold, open reviews. |
| Onboarding checklist | Working | Guides a new workspace through adding vendors and history. |
| Relationship graph | Working | Shows vendors that share bank accounts, domains, GSTINs or addresses. |
| Sign-in | Working | Clerk only. Your first sign-in creates your own workspace with you as owner. Invited people join the inviter's workspace. |

## 10. Security and safety

| Feature | Status | Notes |
|---|---|---|
| Bank account protection | Working | Stored encrypted (AES-GCM) plus a fingerprint for matching. Shown as XXXX1234 everywhere. Revealing the full number is approver-only and audited. |
| Workspace isolation | Working | PostgreSQL row-level security: the app's database user can only see the current workspace's rows. |
| Safe web fetching (SSRF protection) | Working | HTTPS only. Private, internal and cloud-metadata addresses are blocked. Every redirect is re-checked. Size and time limits. |
| Prompt-injection handling | Working | Document and web text is treated as data, wrapped and defanged before reaching the AI, and instruction-like text is flagged as a finding. Personal data is redacted before AI calls. |
| Usage caps | Working | Per case: 40 AI calls, 200k tokens, 25 web calls, 10 searches, 240 seconds. Per workspace per day: 25 cases and 3M tokens. Rate limits per user. |
| Neutral language | Working | Words like "fraud" or "scam" are rewritten to neutral wording ("anomaly", "risk indicator"). |
| Upload safety | Working | Size limits, content-type sniffing, active-content check, parsing in an isolated helper process with a time limit. |
| Two-factor for approvers | Working | Optional owner setting. |
| Production start-up rules | Working | With `ENV=prod` the API refuses to start without cloud storage, a metrics token, a Redis password, and TLS for remote database connections. |
| Live hosted deployment | Working | https://probity-3xgk.onrender.com: Render (free) + Neon (database and files) + Clerk. Deployed automatically from the `real-data` branch. Guide: `infra/render/README.md`. |
| Production Docker stack | Working | `infra/docker-compose.yml`: PostgreSQL, password-protected Redis, migrations, API, worker, scheduler, web and a Cloudflare Tunnel (about 2 GB RAM). See `infra/cloudflare/README.md`. |
| Contact privacy | Working | Viewers see vendor contacts' emails and phones masked. An owner can erase a contact on request: their name, email and phone become "[erased]" everywhere, audit log included, and the audit chain still verifies. See [PRIVACY.md](PRIVACY.md). |
| Data retention | Partly | 8-year retention period (`RETENTION_YEARS`) and an owner report of what is past it. Nothing is deleted automatically yet. |

---

## Not built yet / ideas (contribution menu)

Pick one, open an issue saying you're on it, and see [CONTRIBUTING.md](../CONTRIBUTING.md).

**Reading documents**
- Read scanned PDFs and photos (planned: the AI provider's PDF/image reading).
- Accept an email whose logo image comes before the PDF attachment.
- Handle password-protected PDFs with a clear message.

**Checks and data sources**
- GST registry verification. There is no free official API, so this needs a paid GST Suvidha Provider.
- Better handling for non-INR invoices.

**Email**
- Email providers other than Resend (SMTP).
- Bounce handling, out-of-office detection, and ignoring empty replies.

**Web app**
- A notifications bell and list (the API exists).
- Screens to add or approve single past invoices and POs (the API exists).
- An owner screen for the invoice-risk policy, and a "why this score" panel for the second view.
- A global error boundary (today a crash shows a blank page).
- Clear error states with Retry on Memory, Settings, Team and the case graph/audit tabs.
- Friendlier API error messages by status (409, 422, 429, 5xx), and a visible "reconnecting" state for live updates.
- Correct the upload hint: it mentions images and 15 MB, but the server refuses images and the limit is 10 MB.

**Tooling and operations**
- A frontend linter and frontend tests (neither exists yet).
- Deleting cases and their files after the retention period (today an owner can only list them).
- Dependabot, and GitHub Actions pinned to commit SHAs.
