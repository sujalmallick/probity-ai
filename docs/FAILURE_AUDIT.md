# Probity failure audit

Phase 1 of the failure-handling plan: what can fail across the pipeline (upload → document reader → planner → vendor investigator → transaction analyst → web researcher → evidence verifier → case analyst → risk engine → decision → email → reply → confirmation → memory) and the web app, what the code does today, and the proposed behaviour. Static reading of `real-data` at 70f25f0 plus uncommitted work, 4 Oct 2026. Line numbers drift as sessions commit; search for the quoted code if a line moved.

**117 findings:** 6 Critical · 24 High · 54 Medium · 33 Low.

Interactive version (filters by severity and step): https://claude.ai/artifact/WdjSm3MawF9d64w4fKL1pi (private to the owner).

## Status

The five Critical issues (six table rows: stuck cases has two causes) were fixed in one batch before Phase 2, each with regression tests in `apps/api/tests/test_critical_batch.py`:

- **Audit write while another request waits…** db/audit.py: process lock dropped on Postgres; 15 s lock timeout
- **Search queries without the word…** agents/web.py: every page read is scanned; "passed" needs pages read and all searches done
- **API restarts with cases running…** services.recover_after_restart() at start-up; watchdog every minute
- **Celery worker dies or task is redelivered…** watchdog (Celery beat task probity.watchdog) ends the case after 15 min without progress
- **Required check never recorded a result…** agents/risk_case.py: gate walks REQUIRED_CHECKS; a missing result holds
- **Same invoice submitted again before…** agents/transaction.py: compares with all non-rejected cases; create_case refuses a second case on one document

Everything else below is open and assigned to a phase.

## Decisions (user, 4 Oct 2026)

1. **Required for auto-clear:** document read, vendor identity, bank comparison, price/PO comparison, duplicate check (`risk_case.REQUIRED_CHECKS`; the PO comparison applies when the invoice quotes a PO). **Optional:** domain age and web reputation (`risk_case.OPTIONAL_CHECKS`): a failure adds an "Incomplete: …" note to the gate but does not block. Missing bank details, or a vendor with no history, hold the invoice. The gate holds when a required check has no recorded result. *(Done in the Critical batch.)*
2. **No Tesseract.** Reject images and scans at upload time, before anything is stored, with a clear message. Claude vision comes in the real-integrations phase. *(Phase 5.)*
3. **Currency:** hold every invoice that is not INR or has no stated currency; show the currency on the case; never convert silently; fix dollars being read as rupees. *(Phase 5.)*
4. **Ownership:** one backend session does Phases 2–6 (SECURITY finishes its fixes, then hands over). FRONTEND does Phase 7 after the error format is agreed.

## Error response format (approved)

Every API error, so the web app can show the right message and nothing raw reaches users:

```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "AI usage limit reached. The workspace owner can raise it in Billing.",
    "retryable": false,
    "ref": "req_7f3a2c91"
  }
}
```

`code` is stable and comes from the error taxonomy; `message` is safe to show; `retryable` tells the UI whether to offer Retry; `ref` is also in the `X-Request-ID` header and the server log. Optional extras discussed: `action` (reload, retry_later, sign_in, contact_owner, fix_input), `retry_after_s`, and `fields` for 422.

## Findings

### Critical (6)

| Step | What can fail | What happens today | Where | Proposed behaviour | Phase |
|---|---|---|---|---|---|
| Platform | Audit write while another request waits | Process lock plus Postgres lock held until commit: two requests wait on each other forever; all audit writes in the process hang. **Fixed:** db/audit.py: process lock dropped on Postgres; 15 s lock timeout | `db/audit.py:76-80` | Remove the process lock or take it once per transaction; add a lock timeout. | P6 |
| Web researcher | Search queries without the word "complaints" | Fetched pages are never scanned, then "No adverse public findings" and check passed. **Fixed:** agents/web.py: every page read is scanned; "passed" needs pages read and all searches done | `agents/web.py:116` | Scan every fetched page; report findings only from pages actually read. | P3 |
| Platform | API restarts with cases running (inline mode) | In-memory work is lost; cases stay QUEUED or INVESTIGATING forever. **Fixed:** services.recover_after_restart() at start-up; watchdog every minute | `services.py:96,115` | Watchdog plus startup sweep: no progress past the limit moves the case to "needs retry". | P4 |
| Platform | Celery worker dies or task is redelivered | Redelivery is refused because the case is no longer QUEUED; the case is stuck for good. **Fixed:** watchdog (Celery beat task probity.watchdog) ends the case after 15 min without progress | `worker.py:26-30,51-53` | Make tasks resumable by step; accept redelivery for the case's current step. | P4 |
| Risk engine and gate | Required check never recorded a result | Gate only examines checks that exist, so a missing required check doesn't hold the invoice. **Fixed:** agents/risk_case.py: gate walks REQUIRED_CHECKS; a missing result holds | `agents/risk_case.py:166-172` | Iterate the required list; absent means "could not verify: did not run". | P4 |
| Transaction analyst | Same invoice submitted again before the first is closed | History holds only closed, cleared, paid cases; open and auto-cleared ones are invisible, so it can auto-clear twice. **Fixed:** agents/transaction.py: compares with all non-rejected cases; create_case refuses a second case on one document | `services.py:543,558 · transaction.py:44` | Compare against all non-rejected cases; block a second case on the same document. | P5 |

### High (24)

| Step | What can fail | What happens today | Where | Proposed behaviour | Phase |
|---|---|---|---|---|---|
| Platform | Queueing fails after the case is saved (Redis down) | Case committed, then the queue call raises: 500, and the case stays QUEUED (or INVESTIGATING after investigate-further). | `services.py:248,326-328` | Save case and job together (outbox) or mark FAILED_TO_START with a retry action. | P4 |
| Platform | Celery soft time limit | Caught by the node guard and narration as an ordinary error and retried; the hard limit then kills the task and the case stays running. | `graph/build.py:64 · worker.py:29` | Let time-limit errors through; record "timed out" and end the case cleanly. | P4 |
| Platform | A step hangs (inline mode) | No deadline on the graph or any step; the budget is only checked when a call is charged. | `graph/build.py:186 · tools/base.py:49` | Hard per-step and per-case deadlines enforced by the runner. | P4 |
| Decision | A re-run of a waiting case fails | Case becomes FAILED, which allows no transitions: it can't be retried, decided or closed. | `services.py:62,182` | FAILED can move to "needs retry"; keep the last good result visible. | P4 |
| Platform | Raw exception text | Audit log, notification email, timeline and case checks get str(e), which can include SQL and parameters or provider replies. | `services.py:183-188 · graph/build.py:68,75` | Typed errors with a safe message and reference ID; details only in the server log. | P2 |
| Platform | Production deploy forgets ENV=prod | ENV defaults to dev: virus scan skipped, local storage, /metrics open. | `config.py:40,176-189` | Require ENV explicitly; refuse unsafe combinations outside local development. | P6 |
| AI layer (all steps) | Anthropic credits exhausted | Arrives as HTTP 400 and is reported as "AI request was rejected (400)", same as other bad requests. No owner alert. | `llm/client.py:88-89` | Detect the billing error type: QuotaExceeded, stop AI steps, tell the owner, keep the case resumable. | P3 |
| AI layer (all steps) | Budget exceeded | BudgetExceeded isn't an LLM failure, so every labelled fallback is skipped; it fails the whole case in the verifier and leaves a stale summary in the analyst. | `llm/client.py:119,137` | Treat as a typed, handled failure with a "budget reached" label and a clean hold. | P3 |
| AI layer (all steps) | Slow or stalled AI call | 90 s timeout × 3 SDK attempts × 2 with the repair: up to about 9 minutes for one call. | `llm/client.py:72,123` | Shorter per-call timeout and a total time budget per step. | P3 |
| AI layer (all steps) | Cost runaway | Only an estimate of input tokens is charged; output tokens and real usage aren't. No daily workspace cap, no circuit breaker. | `llm/client.py:117-119` | Charge real usage; per-case and per-workspace daily caps; breaker per provider. | P3 |
| Web researcher | Some searches fail | One success out of six marks the check passed; failures go only into warnings, which the gate ignores. | `agents/web.py:144-146` | Report partial coverage; hold when the check is required and coverage is incomplete. | P3 |
| Web researcher | Search returns 0 usable hits | Counted as searched and turned into a verified "no adverse findings" claim. Hits dropped by a DNS failure look the same. | `agents/web.py:101,138-143 · lookups.py:128` | Separate "nothing found" from "results unusable"; no claim when nothing was read. | P3 |
| Web researcher | Page fetch to an internal address | SSRF check resolves DNS separately from the fetch (rebinding), and misses 100.64.0.0/10 (incl. 100.100.100.200) and some IPv6 ranges. | `tools/fetch.py:31-38,54,68` | Pin the resolved IP for the request; block anything that isn't globally routable. | P6 |
| Transaction analyst | No invoice number, date or history | Duplicate check reports passed. | `signals/detectors.py:72 · transaction.py:33` | "No history to compare" or "could not verify"; never passed. | P5 |
| Document reader | Credit note, quotation, proforma or statement | Not classified; read as a payable invoice; "Balance due" becomes the total. | `ingestion/parse.py:182,189` | Classify the document type; anything that isn't an invoice is never auto-cleared and says what it is. | P5 |
| Document reader | Two bank accounts or GSTINs in one document | First match wins silently. A known account printed first passes the bank check while a second account is printed for payment. | `ingestion/parse.py:369` | Collect all values; flag "conflicting values" with evidence and hold. | P5 |
| Document reader | Hidden text (white, tiny, under an image) | Extracted like visible text at 0.99 confidence and can win the first match for account number or total. | `ingestion/parse.py:133,374` | Detect invisible text, exclude it and flag it as an anomaly. | P5 |
| Document reader | Invoice in dollars or with no currency | Only ₹/Rs/INR recognised; $4,00,000 reads as ₹4,00,000; missing currency becomes INR; the gate never checks currency. | `parse.py:242,268 · services.py:588` | Detect currency; non-INR or unknown holds the invoice with the reason. | P5 |
| Document reader | Email with no readable sender | Sender domain falls back to the email address printed inside the document. | `ingestion/parse.py:395-397 · document.py:78` | Sender unknown; never use document content as the sender. | P5 |
| Verification email | Database fails after the email went out | Email is sent first, then the draft is saved. A failure leaves it as a draft, so it can be sent again. No idempotency key. | `services.py:380-393 · mailer.py:65` | Mark "sending" first, send with the draft ID as idempotency key, record the outcome. | P6 |
| Vendor reply | Inbound webhook fails after the dedupe mark | The delivery is marked seen before processing; the provider's retry gets 409 and the vendor reply is lost. | `api/main.py:640,678` | Mark as seen only after processing succeeds. | P6 |
| Vendor reply | Out-of-office auto-reply | Not detected; it moves the case to awaiting decision, and the real reply later gets 409 and is dropped. | `services.py:411 · api/main.py:674` | Detect auto-replies (Auto-Submitted, X-Autoreply); record them without changing status. | P6 |
| Out-of-band confirmation | Re-check fails after an out-of-band confirmation | Confirmation commits, then the rescore turns off earlier risk claims in its own commit; if the re-run fails, they stay off and later scores come out lower. | `services.py:497,513-514` | Rescore in one transaction, or restore the claims on failure. | P4 |
| Web app | A component crashes while rendering | No error boundary anywhere: white page. | `apps/web/src (no ErrorBoundary)` | Global boundary with a reload button and reference ID. | P7 |

### Medium (54)

| Step | What can fail | What happens today | Where | Proposed behaviour | Phase |
|---|---|---|---|---|---|
| Upload | Same document used for a second case | Upload dedupes the file, but nothing stops a second case on it. | `services.py:237-251` | Return the existing case. | P5 |
| Upload | Photo or image-only PDF | No OCR; images are stored at upload and only fail later at preview. | `ingestion/parse.py:93-105,134` | Refuse at upload with a clear message, or add OCR (decision 2). | P5 |
| Upload | PDF with hidden script content in compressed streams | The raw-byte check can't see /JS or /Launch inside compressed object streams. | `ingestion/scan.py:14,30` | Inspect decompressed objects. | P5 |
| Upload | Many previews at once | Two parser helpers per process; waiting for a slot has no timeout; preview isn't rate limited. | `ingestion/isolate.py:62 · api/main.py:359` | Bounded wait then "busy, try again"; rate-limit preview. | P4 |
| Upload | Malformed email upload | Email parsing runs inside the API process with no isolation or time limit. | `ingestion/parse.py:55,84 · scan.py:27` | Parse emails in the isolated helper too. | P5 |
| Upload | No virus scanner configured | Returns SKIPPED and the upload is accepted. | `ingestion/scan.py:39-40` | Refuse outside local development; label "not scanned" when allowed. | P6 |
| Document reader | PDF mixing text pages and scanned pages | Only an all-empty PDF is refused; scanned pages are silently empty. | `ingestion/parse.py:134` | Report "N pages could not be read" and hold. | P5 |
| Document reader | Several invoices in one PDF | Not detected; fields take the first match and line items pile up across pages. | `ingestion/parse.py:336-376` | Detect multiple invoice numbers or totals; say so and hold. | P5 |
| Document reader | Invalid GSTIN or IFSC | Dropped instead of flagged, so the check reports "no GSTIN". | `ingestion/parse.py:216,222` | Keep the raw value, mark it invalid, fail the check. | P5 |
| Document reader | Totals with missing parts | Arithmetic check reports ok when it had nothing to compare. | `ingestion/validators.py:128` | Report "could not check". | P5 |
| Document reader | Non-English invoice | All labels are English, so fields go missing; the injection check is English-only. | `ingestion/parse.py:165-185 · guardrails/text.py:66` | Note the language; add common Hindi labels; never follow embedded instructions. | P5 |
| Document reader | AI fills a missing field | Grounding only checks that the text appears anywhere; an AI-picked total at 0.85 passes the 0.8 gate; values aren't type-checked. | `agents/document.py:62-64` | Stronger grounding, type validation, and AI-filled totals need a person to confirm. | P3 |
| Document reader | AI field fill fails | A "could not verify" event is emitted, but the result never reaches the case checks, so the gate can't see it. | `agents/document.py:55-60` | Store it as a check the gate reads. | P4 |
| Document reader | Very long document | Invoice text is never shortened; the call fails as a generic 400. | `agents/document.py:50` | Chunk or truncate with a visible "document was shortened" note. | P3 |
| Document reader | Embedded instructions (prompt injection) | Flagged by English phrase regex only; easy to rephrase. | `guardrails/text.py:79-86` | Keep the warning; strengthen detection; instructions are never followed. | P5 |
| Document reader | Email from a spoofed sender | DKIM is read only from a header inside the uploaded file; if absent nothing is flagged. Reply-To is never read. | `ingestion/parse.py:58 · document.py:82` | "Sender authenticity: could not verify"; compare Reply-To with From. | P5 |
| Document reader | Email with a logo image before the PDF | The first image attachment refuses the whole email. | `ingestion/parse.py:143-144` | Skip images and find the PDF. | P5 |
| Planner | Vendor name close to a real vendor | Fuzzy match at 90 accepts any name that contains all the real vendor's words. | `agents/orchestrator.py:53-55` | Exact or GSTIN match; otherwise ask a person to confirm the vendor. | P5 |
| Planner | Required inputs missing | plan.missing_info is never consulted by the gate. | `agents/orchestrator.py:91-116` | Missing required input holds the invoice. | P4 |
| Vendor investigator | No GSTIN on the invoice | Identity passes on a loose name match ("ABC" vs the full name scores 100). | `signals/detectors.py:118-126` | Identity "could not verify" without a GSTIN. | P5 |
| Transaction analyst | New line item on a known vendor | Items with fewer than three history points are ignored if any other item was compared. | `signals/detectors.py:81-103` | Show "not compared" per item. | P5 |
| Transaction analyst | Items not on the purchase order | Ignored; unit price never compared with the PO. | `signals/detectors.py:147-151` | Flag extra items and price differences. | P5 |
| Transaction analyst | Invoice with no bank details | Treated as not applicable, so it can auto-clear (decision 1). | `agents/transaction.py:25` | Hold when bank details are missing, if you agree. | P4 |
| Web researcher | Slow page drip-feeds bytes | Timeout is per read, not total; DNS lookup has no timeout. | `tools/fetch.py:54,65` | Total deadline per fetch and a resolver timeout. | P2 |
| Evidence verifier | Verifier fails or hits the budget | The node is fatal, so the whole case becomes FAILED. | `graph/build.py:160` | Mark claims "not checked" and hold instead of failing the case. | P4 |
| Case analyst | AI summary contradicts the score | The case summary isn't checked against the engine (narration is). | `agents/risk_case.py:107-115` | Run the same consistency check; fall back to the labelled engine summary. | P3 |
| Case analyst | Analyst fails on a re-run | Summary and recommendation stay from the previous round, or empty. | `graph/build.py:162` | Reset before filling; label what's stale. | P4 |
| AI layer (all steps) | Missing API key | Each step falls back separately; nothing stops the remaining calls. | `llm/client.py:70-71` | ConfigMissing once per case; banner; skip AI steps cleanly. | P3 |
| AI layer (all steps) | Rate limited (429) | The SDK retries twice and honours Retry-After; nothing queues or slows down after that. | `llm/client.py:72,82` | Shared retry helper with a time budget; slow the case rather than fail it. | P3 |
| AI layer (all steps) | Unexpected SDK errors | Response-validation and other API errors aren't caught, so labelled fallbacks don't run. | `llm/client.py:78-91` | Map every SDK error to a typed error. | P3 |
| Risk engine and gate | Manual rescore | No lock or status check; adds a score row each time, which resets pending approvals (an accountant can reset dual approval). | `api/main.py:593-598 · services.py:510-526` | Restrict to approvers and allowed states; lock the case; skip if unchanged. | P4 |
| Decision | Events published before the commit | If the commit fails, the timeline still says Approved, Sent or Closed. | `services.py:333,393,419,566` | Publish after commit. | P4 |
| Decision | Locks held during slow calls | Row locks held across AI, email and HTTP calls, with no database lock timeout. | `services.py:318,367-404` | Do slow I/O outside the lock; set lock and statement timeouts. | P6 |
| Verification email | Provider timeout after it accepted | Reported as not sent; a retry sends a duplicate. | `mailer.py:65-67` | Idempotency key; record "outcome unknown" and check before resending. | P6 |
| Verification email | Resend 429 or 5xx | One attempt; Retry-After ignored. | `mailer.py:65-75` | Shared retry helper. | P2 |
| Verification email | Bounce or complaint | Not handled; the draft stays sent until the 2-day follow-up. | `services.py:129-133` | Bounce webhook; show "not delivered" on the case. | P6 |
| Verification email | Notification emails | Sent one by one inside the request transaction, before commit. | `notify.py:26-34` | Outbox: send after commit, in the background. | P6 |
| Vendor reply | Empty or attachment-only reply | Accepted through the webhook; the case moves on with no statements. | `api/main.py:674` | Record it with "no text" and keep waiting. | P6 |
| Vendor reply | Reply from an unexpected address | Never compared with the address the email went to. | `agents/action.py:134` | Compare and show a warning. | P6 |
| Out-of-band confirmation | Approval during the rescore window | The lock is released between the confirmation and the rescore. | `services.py:497-519` | Hold the case lock or use a version check through the rescore. | P4 |
| Memory and imports | CSV vendor name containing % or _ | Matched with an unescaped LIKE, so history or a verified account can attach to the wrong vendor. | `importer.py:136` | Exact, escaped match. | P5 |
| Platform | Pipeline failure handler fails (database down) | The exception disappears inside the thread pool; the case stays running. | `services.py:179-188` | Watchdog covers it; log the handler failure. | P4 |
| Platform | Node retries | Every exception is retried, including our own bugs; claims, evidence and events duplicate; sources double-count. | `graph/build.py:57-67,121` | Retry only retryable errors; make steps idempotent. | P2 |
| Platform | Unhandled server error | Plain-text 500 with no reference ID; KeyError bugs come back as 404 with the key name; validation errors echo input; raw virus-scan and key-set text reaches clients. | `api/main.py:76,119-131` | One JSON error shape for everything (see below). | P2 |
| Platform | Database slow or saturated | No connect, statement or idle timeouts; 10+10 pool while connections are held during network calls. | `db/session.py:46 · web.py:84` | Timeouts; release connections before network calls. | P6 |
| Platform | Redis down during live events | The stream dies with no fallback; the async client has no timeouts. | `api/main.py:491-499 · redis_client.py:26` | Fall back to database polling; set timeouts. | P6 |
| Platform | Server shutdown | No shutdown hook; in-flight inline cases are lost; the scheduler thread is killed mid-sweep. | `services.py:96,165` | Stop intake, mark in-flight cases "needs retry", then exit. | P4 |
| Platform | Clerk key set unreachable | Returns 401 instead of "sign-in temporarily unavailable"; 30 s default timeout; random key IDs trigger refetches before any rate limit. | `auth.py:55,68-69` | 503 with a clear message; short timeout; cache. | P6 |
| Platform | Logs | Standard library logging bypasses the scrubber (server tracebacks, SQL parameters); the account-number pattern misses spaced numbers and IBANs; decision reasons aren't scrubbed. | `logging.py:21,76 · services.py:290` | Route all logs through the scrubber; widen patterns. | P6 |
| Web app | Session expires | 401 isn't wired to re-sign-in under Clerk; the user sees "Unauthorized". | `apps/web/src/lib/api.ts:61` | Send to sign-in with a message. | P7 |
| Web app | Policy, team, memory, audit and graph loads fail | No error handling: stuck spinner, or a blank Policy page. | `Settings.tsx:10 · Team.tsx:26 · Memory.tsx:12 · CaseView.tsx:588,717` | Error state with Retry on each. | P7 |
| Web app | Live events drop | Reconnects with Last-Event-ID, but stops silently on 401/403/404 and never shows "reconnecting". | `apps/web/src/lib/api.ts:97` | Visible reconnecting state; re-auth on 401. | P7 |
| Web app | Offline or server error | "Failed to fetch" shown raw; 409, 422, 429 and 5xx share one message; no inline field errors. | `apps/web/src/lib/api.ts:44-62` | Distinct message per status; field errors from 422. | P7 |
| Web app | Clerk script fails to load | Signed-in and signed-out views both render nothing: blank page. | `apps/web/src/main.tsx` | Timeout with "sign-in unavailable" and retry. | P7 |

### Low (33)

| Step | What can fail | What happens today | Where | Proposed behaviour | Phase |
|---|---|---|---|---|---|
| Upload | Empty (0-byte) file | Accepted as plain text, then held later. | `ingestion/parse.py:42` | Reject: "the file is empty". | P5 |
| Upload | HTML, SVG, JSON or CSV uploaded as an invoice | Any UTF-8 file is accepted as plain text. | `ingestion/parse.py:42` | Accept only invoice formats. | P5 |
| Upload | Hindi or other non-ASCII text file | The first 2048 bytes can cut a character, so it's refused as unsupported. | `ingestion/parse.py:42` | Decode tolerantly at the boundary. | P5 |
| Upload | Password-protected PDF | Reported as "could not be opened". | `ingestion/parse.py:118` | Say it's password-protected and ask for an unlocked copy. | P5 |
| Upload | Two identical uploads at once | Unique constraint error: plain 500. | `db/models.py:183` | Return the existing document. | P5 |
| Upload | Virus scanner unreachable | Generic 500 (correctly refuses the file). | `ingestion/scan.py:41` | "Virus scan unavailable, try again shortly" with reference ID. | P2 |
| Upload | Disk full or storage error | Generic 500; local writes aren't atomic; an orphan file stays if the database fails. | `storage.py:38 · services.py:211-215` | Typed storage error; write then rename; clean up orphans. | P6 |
| Document reader | Forwarded-as-attachment email | Nested message not opened; the outer body is parsed. | `ingestion/parse.py:86-89` | Look one level inside. | P5 |
| Document reader | Table extraction error | Silently empty. | `ingestion/parse.py:426,438` | Log and note "tables not read". | P5 |
| Document reader | Bug in our parser | Looks like a corrupt file, unlogged. | `ingestion/isolate.py:130` | Log with reference ID; tell "internal error" apart from "corrupt file". | P2 |
| Vendor investigator | RDAP returns a malformed date or odd JSON | Uncaught; the step retries three times then fails. | `tools/lookups.py:75,82` | Parse defensively; "could not verify". | P3 |
| Vendor investigator | RDAP creation date in the future | A negative age can fire "new domain". | `agents/vendor.py:140` | Treat as invalid: "could not verify". | P3 |
| Vendor investigator | TLD not supported by RDAP | Worded "registry has no record". | `tools/lookups.py:62-63` | "Registration date not available". | P3 |
| Transaction analyst | No invoice date or total | Temporal and round-sum checks report passed. | `signals/detectors.py:154 · transaction.py:199` | "Could not verify". | P5 |
| Web researcher | Tavily returns unexpected JSON | Uncaught AttributeError fails the step. | `tools/lookups.py:122,128` | Parse defensively. | P3 |
| Evidence verifier | Informational claims | Always verified with no check, including "no adverse findings". | `evidence/verifier.py:126-127` | Never treat an info claim as proof of a negative. | P3 |
| Case analyst | Narration text rejected | Template returned without the fallback label. | `risk/narrate.py:106-108` | Label it. | P3 |
| AI layer (all steps) | Output cut off at the token limit | Repair is sent with the same limit and will likely be cut again. | `llm/client.py:94-95` | Raise the limit for the repair. | P3 |
| Decision | Request verification clicked twice | Each creates a new draft and AI call. | `services.py:317-321` | Reuse the open draft. | P4 |
| Verification email | Send fails | No audit row; up to 200 characters of provider text reach the client. | `services.py:385 · mailer.py:72-75` | Audit "email.failed"; safe message and reference ID. | P6 |
| Verification email | Provider returns non-JSON success | Parsing fails after the email was sent: 500. | `mailer.py:76` | Guard the parse. | P6 |
| Vendor reply | Several API instances without Redis | Webhook dedupe is per process. | `api/main.py:694-702` | Log and surface degraded dedupe. | P6 |
| Memory and imports | CSV with a huge field or "inf" | csv.Error and OverflowError aren't caught: 500. | `importer.py:258,327 · workspace.py:132` | Row-level errors in the import report. | P5 |
| Memory and imports | Follow-up sweep | One workspace failing stops the rest; several inline instances send duplicates. | `services.py:127-165` | Isolate per workspace; one scheduler. | P6 |
| Platform | Database down at startup | Crashes with a raw traceback, no retry. | `db/migrate.py:43-48` | Retry briefly, then a clear message. | P6 |
| Platform | First sign-in twice at once | Unique constraint error: 500. | `auth.py:98-125` | Upsert. | P6 |
| Platform | Two cases created at once | Duplicate case numbers. | `services.py:243` | Sequence or unique constraint. | P4 |
| Platform | Live event stream | Non-numeric Last-Event-ID gives 500; streams never close. | `api/main.py:472,497` | Validate the header; close when the case ends. | P6 |
| Platform | Status endpoints | /ready is public and shows env and migration details; /metrics uses owner credentials and is open in dev. | `api/main.py:164 · observability.py:52` | Trim /ready; owner-only status page. | P6 |
| Platform | Redis down for rate limits | Falls back per instance; unauthenticated paths aren't limited. | `api/deps.py:79` | Log; limit public paths. | P6 |
| Web app | Degraded integrations | Only a tooltip on the "Live" indicator. | `apps/web/src/main.tsx` | Banner naming what's unavailable and its effect. | P7 |
| Web app | Dashboard polling | Every 4 s with no backoff; an error is never cleared after recovery. | `apps/web/src/pages/Dashboard.tsx` | Back off on errors; clear on success. | P7 |
| Web app | Double clicks | Most actions are guarded; vendor archive isn't. | `apps/web/src/pages/Vendors.tsx` | Guard every action button. | P7 |

## Code hotspots

There are no bare `except:` clauses in the backend; every broad handler is `except Exception`.

### Broad exception handlers

- **fix** Pipeline failure handler: sets FAILED but copies raw `str(e)` into audit, notification email and timeline, and nothing protects the handler itself. (`services.py:178-188`)
- **fix** Node guard retries every exception, including our own bugs and Celery's soft time limit, with no jitter. (`graph/build.py:64`)
- **fix** Event publish errors dropped with no log or metric. (`events.py:34`)
- **fix** LLM telemetry errors dropped; the call record has no error reason. (`llm/client.py:61`)
- **fix** Metrics refresh errors dropped, so stale gauges look healthy. (`observability.py:89`)
- **fix** Parser helper turns our own bugs into "could not be read", unlogged. (`ingestion/isolate.py:130`)
- **fix** Table extraction errors become an empty list, silently. (`ingestion/parse.py:426,438`)
- **fix** Redis down: webhook dedupe and rate limits fall back per process, unlogged. (`api/main.py:694 · api/deps.py:79`)
- ok Narration falls back to a template (but should also label rejected text). (`risk/narrate.py:103`)
- ok Network failures become "could not verify". (`tools/lookups.py:69,155`)
- ok Rollback and re-raise. (`db/session.py:91,106 · api/deps.py:24`)
- ok Scheduler loop logs and continues. (`services.py:162`)

### Missing timeouts

- Whole pipeline and every step in inline mode: none. The time budget is only checked when an AI or web call is charged. (`graph/build.py:186 · tools/base.py:49`)
- AI call: 90 s × 3 SDK attempts × 2 with repair, so up to about 9 minutes for one call. (`llm/client.py:72`)
- Database: no connect, statement, lock or idle-in-transaction timeout, while row locks are held across AI and email calls. (`db/session.py:46 · services.py:367-404`)
- Page fetch: 8 s per operation but no total deadline; DNS lookup has none. (`tools/fetch.py:54,65`)
- Async Redis client for live events: no socket timeouts. (`redis_client.py:26`)
- Parser helper slot: waits forever when both helpers are busy. (`ingestion/isolate.py:62`)
- Email parsing runs in the API process with no isolation or limit. (`ingestion/parse.py:55,84`)
- Clerk key set fetch: library default 30 s. Storage client: boto3 defaults. (`auth.py:55 · storage.py:24`)

### Missing retries

- No app-level retry or Retry-After handling anywhere. Only the Anthropic SDK retries (twice).
- Tavily, RDAP, Resend, the Clerk API and the virus scan: one attempt each. (`tools/lookups.py:60,111 · mailer.py:65 · auth.py:83 · scan.py:41`)
- Database connect at startup: none.
- Celery tasks: `max_retries=0`, and redelivery is refused anyway. (`worker.py:51,58`)
- Where retries do happen (the node guard), they repeat non-idempotent work. (`graph/build.py:57-67`)

### Loops and loop guards

- ok The agent graph is acyclic; no planner replans exist.
- ok Investigate-further depth is capped at 2. (`services.py:323 · worker.py:49`)
- ok Per case: 6 searches, 2 pages per search, 25 web calls, 40 AI calls.
- ok Virus scan reply loop stops on a closed socket. (`ingestion/scan.py:48`)
- **fix** Live event stream never ends after a case finishes; backfill on reconnect is unbounded. (`api/main.py:497 · services.py:658`)
- **fix** No per-workspace daily AI cap and no circuit breaker.

### "Fake OK" results

- Press scan skipped unless the query says "complaints", then "passed". (`agents/web.py:116`)
- One successful search of six is "passed"; a search with 0 usable hits becomes a verified "no adverse findings" claim. (`agents/web.py:101,138-146`)
- Duplicate check "passed" with no invoice number, no date or no history. (`signals/detectors.py:72`)
- Arithmetic check "ok" when it had nothing to compare. (`ingestion/validators.py:128`)
- Invalid GSTIN or IFSC silently dropped, so the check says "no GSTIN" instead of "invalid". (`ingestion/parse.py:216,222`)
- Price check ignores new line items; PO check ignores items not on the PO and never compares price. (`signals/detectors.py:81-103,147-151`)
- No GSTIN on the invoice plus a loose name match counts as identity "passed". (`signals/detectors.py:118-126`)
- No bank details on the invoice is "not applicable", so it can auto-clear. (`agents/transaction.py:25`)
- Missing currency becomes INR; dollars are read as rupees. (`services.py:588 · parse.py:242`)
- An email with no readable sender uses the domain printed in the document as the sender. (`ingestion/parse.py:395 · agents/document.py:78`)
- Temporal and round-sum checks "passed" with no date or total. (`signals/detectors.py:154 · transaction.py:199`)

### Races and repeated work

- Out-of-band confirmation commits, releases the lock, then rescores: an approval can land in between, and two confirmations can interleave. (`services.py:497-519`)
- Manual rescore has no lock or status check, and resets pending approvals. (`api/main.py:593-598`)
- Email is sent before the database commit; a later failure lets it be sent again. No idempotency key. (`services.py:380 · mailer.py:65`)
- Notifications email people inside the transaction, before commit. (`notify.py:26-34`)
- Events are published before the request commits, so a rollback leaves "Approved" in the timeline. (`services.py:333,393,419,566`)
- Each request for verification creates a new draft and AI call. (`services.py:317-321`)
- Retries and re-runs duplicate claims, events and document claims, and double-count sources. (`graph/build.py:121 · services.py:340`)
- Case numbers come from max + 1 with no unique constraint. (`services.py:243`)

## Already handled well

- Sign-in fails closed: Clerk token signature, issuer, expiry and audience are all required, and a key-set outage gives 401.
- The email allowlist blocks and audits sends outside it.
- A draft is marked sent only after the provider returns success.
- RDAP with no creation date (privacy-redacted) becomes "could not verify", never "new domain".
- Tavily keeps "search failed" separate from "search returned nothing".
- Decisions, sends, replies, confirmations and close lock the case row and re-check its status, so double clicks are rejected.
- Document parsing runs in a separate process with a 30 s limit that is killed on timeout; page count and size are capped.
- Uploads are deduplicated by content hash; files are stored before the database row.
- An unreachable virus scanner refuses the upload instead of accepting it.
- CSV imports reject formula cells and cap size and row count.
- Evidence is deduplicated by content hash.
- Secrets are masked in config output; Sentry sends no locals or request bodies.
- A non-fatal step failure marks the case incomplete and blocks auto-clear.

## Phase map

- **P2** error taxonomy, shared retry helper, circuit breakers, idempotency, the error format above.
- **P3** Anthropic and other provider failures (keys, quota, 429, timeouts, invalid output, cost caps); web research honesty.
- **P4** pipeline resilience: step status, partial failure, retry failed steps, watchdog follow-ups, concurrency, deadlines.
- **P5** input and document edge cases (types, encryption, multi-invoice, credit notes, currency, hidden text, EML).
- **P6** infrastructure: database, storage, Clerk, email delivery and bounces, inbound replies, startup checks, logs.
- **P7** web app error handling (FRONTEND).
- **P8** fault-injection tests; **P9** failure matrix and live drills.

Method: four parallel read-only reviews of the backend (AI and agents, external services and infrastructure, documents and imports, orchestration and case state), a direct sweep for exception handlers, timeouts and loops, and a review of the web app. Every Critical item was re-checked against the source.
