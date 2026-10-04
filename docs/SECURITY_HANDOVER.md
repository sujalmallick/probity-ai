# Security handover (pre-production audit, October 2026)

**Status:**
- The pre-production security audit is complete.
- Both Critical findings and 12 of the 13 High findings are fixed.
- **H11 (data deletion and retention) is still open.** It waits on a product decision about retention periods.
- The Mediums are fixed or assigned.
- Low findings are parked until Phases 2–6 are done.

**From here on:** one backend session owns the backend (Phases 2–6). This note lists:
- what was fixed and which tests guard each fix;
- what is still open, in priority order.

**The full audit report:**
- It holds exploit detail and is **not** in this repo.
- It was written to the audit session's scratch folder as `PROBITY_SECURITY_AUDIT_PHASE_A.md`.
- Keep a private copy of it; the scratch folder may be cleared.

Every test named below runs in the normal suite: `cd apps/api && pytest -q`, against a throwaway Postgres database. **If one of these tests starts failing, treat it as a security regression, not a flaky test.**

## 1. Still open, in priority order

### 1. Supply-chain pinning — do this before the first push
The Python dependencies are now pinned to exact versions (see the table below). Three things remain, and all three need network access:
- **Lock hashes.** Regenerate the locks with hashes, then install with `--require-hashes` in CI and Docker. Either of:
  - `pip-compile --generate-hashes`
  - `uv pip compile --generate-hashes`
- **Base-image digests.** Pin each base image to `image@sha256:…`: `python:3.12-slim`, `node:22-alpine`, `nginx:1.27-alpine`, `postgres:16`, `redis:7-alpine`. Change `nginx:1.27-alpine` to 1.28 stable while you're there.
- **GitHub Actions.** Pin `actions/checkout`, `actions/setup-python` and `actions/setup-node` to full commit SHAs, with a `# vX.Y.Z` comment. Then add `.github/dependabot.yml` covering pip, npm, github-actions and docker so they stay current.

What the lock files are:

| File | Contents | Used by |
|---|---|---|
| `apps/api/requirements.lock` | runtime only | — |
| `apps/api/requirements-dev.lock` | runtime + dev | CI |
| `apps/api/requirements-full.lock` | runtime + worker + s3 | Docker and `pip-audit` |

**How the locks were generated:**
- Offline, from the dev virtualenv.
- Environment markers were evaluated for Linux and CPython 3.12.
- **`uvloop` is unpinned**, because it is Linux-only and isn't installed on the Windows dev machine.
- **Not yet confirmed on Python 3.12:** they were generated under Python 3.13, so confirm on the first CI run.

### 1b. Data deletion and retention (H11) — needs the user's retention periods
Nothing can be deleted today: workspaces, cases, documents, messages, telemetry, and stored files on disk or S3. `docs/Security.md` promises retention and right-to-delete, and the immutable audit log holds personal data, which conflicts with DPDP erasure.

Plan:
1. Retention jobs.
2. Workspace and user deletion that also removes stored files.
3. Keep personal data in an erasable side table and put only pseudonymous IDs in the hash chain.
4. Document the legal retention basis (GST: 8 years).

### 2. Stronger PDF active-content check — fixed (user decision: no resident virus scanner)
Every uploaded PDF is now parsed with pikepdf (qpdf) and rewritten before it is stored (`ingestion/scan.clean_pdf`):
active content anywhere in the object graph, including inside compressed object streams (`/ObjStm`), is refused with 415
(JavaScript, Launch, embedded files, rich media, XFA, auto-actions); fill-in forms, link and open actions, and encryption
are removed; the result has no object streams and passes the byte-level check a second time. Only the clean copy is
stored and served. PDFs needing a password, and files that don't parse, are refused. An emailed invoice's PDF
attachment gets the same structural check. ClamAV is now **optional** (used only if `CLAMAV_HOST` is set; no longer
required for `ENV=prod`), because a resident scanner needs 1–3 GB of RAM. Remaining gap: without ClamAV, a *known*
malware sample that is a valid, inert PDF is stored (cleaned) rather than named as malware.

### 3. Per-workspace cost quotas (M16) — fixed; see section 2
Remaining gaps (not requested by the user): no separate daily caps on previews or re-scores, and `MAX_SECONDS` is
checked at each AI/web call rather than killing a run mid-call (Celery's task time limit does that when the worker is used).

### 4. Frontend dependency majors (M24) — frontend
- `react-router-dom` 6 → 7.18+. Two moderate advisories, neither reachable today.
- `@clerk/clerk-react` → `@clerk/react` (Core 3).

### 5. Low-severity findings — parked until Phases 2–6 are done
These are listed in the full report (L1–L19). Examples:
- token hygiene;
- per-IP rate limits;
- SSE re-authorisation;
- SSRF guard: pin the resolved IP, block 100.64/10, allow only port 443;
- verifier edge cases;
- privacy items (L15–L16), including synthetic demo identities.

### Being handled in Phase 3 (backend session)
- **M8:** `known_channel` becomes required, and a note copied from a vendor reply is rejected.
- **M9:** imported or manually entered history and POs count toward baselines only after an approver approves them; `importer._find_vendor` uses an exact match.
- **M10:** overriding an unverified recipient needs a written reason.

### Production configuration the code cannot enforce
- `ENV=prod` refuses to start without:
  - S3 storage
  - ClamAV
  - a `METRICS_TOKEN` of at least 32 characters
  - `sslmode=require` on both database URLs
  - an authenticated Redis connection (password or TLS)
  - an `INBOUND_EMAIL_SECRET` of at least 32 characters
- The production database role must not be a superuser and must not have BYPASSRLS. Check with `\du`.
- Restart the dev compose stack so Postgres and Redis bind to 127.0.0.1 only.
- Terminate TLS in front of nginx. The HSTS header in `infra/nginx.conf` only takes effect over HTTPS.
- **Not syntax-tested:** `infra/nginx.conf`. Run `nginx -t` in the web image once.
- The sending domain needs SPF, DKIM and DMARC (`p=reject`).
- Configure the inbound email provider to pass DKIM results.
- Set Anthropic and Tavily spend caps.
- Turn on Sentry server-side scrubbing.
- Turn on GitHub secret scanning and push protection.
- `tests/test_secret_leaks.py` deliberately contains **fake** key-shaped markers. If push protection flags them, allow them as "used in tests".

## 2. What was fixed, and the tests that guard it

### Critical
| ID | Fix | Guarded by |
|---|---|---|
| C1 | Auto-clear needs positive evidence. An unknown vendor, a required check that is skipped or could not verify, no transaction history, or a human correction all hold the case. A PO counts only if it was raised for this vendor. | `test_autoclear_gate.py` |
| C2 | Pre-investigation corrections are limited to 9 identity/reference fields; payment routing and money cannot be corrected. The original value is kept, every correction is audited before and after, and any correction blocks auto-clear. | `test_corrections.py` |

### High
| ID | Fix | Guarded by |
|---|---|---|
| H1 | Demo "sign in as anyone" and local HS256 auth removed entirely; Clerk with a verified email is the only sign-in. | `test_auth.py` |
| H2 | Out-of-band confirmation is bound to the invoice: only the invoice's own account (the approver re-types its last 4 digits) and its own sender domain can be verified. Reply statements are typed and must quote the reply verbatim, and each statement confirms once. | `test_oob_binding.py`, `test_oob_note.py` |
| H3 | Approval rules use the peak tier. A reason (at least 10 letters or digits) is required for approve or reject at HIGH/CRITICAL. Approvals count only after the latest score. The out-of-band confirmer can't be the only approver. A REJECTED case can't close as CLEARED, and only paid cases enter history. | `test_sod_mfa.py`, `test_case_flow.py`, `test_platform.py` |
| H4 | When the workspace requires MFA, it also covers policy, invitations, workspace rename, imports, vendor edits, domain/contact removal and case close. Verified rows in an import need a `verification_note` and are audited row by row. | `test_sod_mfa.py`, `test_onboarding.py` |
| H5 | An accountant editing a verified contact's name or phone un-verifies it; an approver must give a note to keep it verified. | `test_sod_mfa.py` |
| H6 | Account numbers are masked as XXXX + last 4 in extraction snippets, claim text, vendor messages, evidence excerpts and exports, for every role. The full number is available only through the audited reveal endpoint. | `test_masking.py`, `test_ingestion_hardening.py`, `test_case_flow.py` |
| H7 | Uploads (max 10 MB, `MAX_UPLOAD_MB`; PDFs rewritten without active content) are parsed in a killable helper process (30 s limit, plus a memory cap on POSIX) with a cheap page count. The scan and email regexes are linear. A request-body cap (`api/limits.py`) applies before auth. | `test_ingestion_hardening.py`, `test_body_limit.py` |
| H8 | Amount parsing never reads low (it takes the largest well-formed amount; lakh/crore and grouped formats are understood). An unknown, zero or low-confidence total holds the case and requires dual approval. | `test_ingestion_hardening.py`, `test_autoclear_gate.py` |
| H9 | The audit chain is an HMAC keyed from HMAC_KEY and covers every column. A per-workspace advisory lock prevents forks. Each head is logged as an out-of-database witness, and `verify_chain(anchor=)` detects truncation. Rotating HMAC_KEY invalidates verification of older rows. | `test_audit_chain.py`, `test_platform.py` |
| H10 | `Settings` repr hides secrets. Sentry runs with no frame locals and no request bodies. Tracebacks are scrubbed. | `test_secret_leaks.py` |
| H11 | **Open:** see section 1, item 1b. | — |
| H12 | `PUT /workspace/policy` is a strict typed model with bounds. | `test_policy_validation.py` |
| H13 | State changes lock the case row (`SELECT … FOR UPDATE`), so one concurrent decision wins. | `test_case_locking.py` |

### Medium (fixed)
| ID | Fix | Guarded by |
|---|---|---|
| M3 | Production start-up checks (see above). | `test_medium_hardening.py` |
| M4 | One cached engine for metrics scrapes. | `test_medium_hardening.py` |
| M5 | Invitations expire after 7 days, and joining needs a Clerk-verified email. | `test_auth.py` |
| M7 | Migration 0009: `probity_case_workspace()` has a pinned `search_path`, a schema-qualified table, and EXECUTE for `probity_app` only. | `test_medium_hardening.py` |
| M12 (part) | Hex-escaped names and email attachments are now checked. | `test_ingestion_hardening.py` |
| M13 | The sender is the real `From` address, never a display name. | `test_ingestion_hardening.py` |
| M14 | Documents render in a sandboxed iframe (PDF) or as `<img>`; anything else is offered as a download. | web |
| M15 | Every prompt wraps invoice- and reply-derived text as untrusted. The case analyst gets structured facts only. Invoice/PO numbers that look like links never reach the vendor email. | `test_medium_hardening.py` |
| M17 | `infra/nginx.conf` sets the security headers and the upload size limit. | not syntax-tested |
| M18 | Worker tasks are validated (workspace, depth, state) and serializers are JSON-only. | `test_medium_hardening.py` |
| M19 | Inbound webhook: timestamp window, replay de-duplication, payload validation, and DKIM indicators stored on the message. | `test_production.py` |
| M20 | Grouped account numbers and phone numbers are redacted before AI calls; PAN is masked below approver. | `test_medium_hardening.py` |
| M16 | Usage limits, all configurable in `.env` (user's defaults): 25 investigations per workspace per day (429 `limit_reached`), 3,000,000 AI tokens per workspace per day, 200,000 AI tokens per case (input **and** output tokens counted), 10 web searches per case, `MAX_SECONDS` per run, and a 10 MB upload cap (`MAX_UPLOAD_MB`; the request-body cap is upload + 1 MB, and `infra/nginx.conf` uses 11m). Reaching a per-case limit stops the investigation cleanly with "Limit reached: <limit> (<SETTING>)". | `test_currency_and_limits.py`, `test_body_limit.py`, `test_platform.py` |
| M23 (part) | Exact-version lock files, CI `permissions: contents: read`, `pip-audit` and `npm audit` in CI, a root `.dockerignore`. | CI |

### Post-deploy review (October 2026)
| ID | Fix | Guarded by |
|---|---|---|
| D1 (High) | Invitations are never joined automatically: anyone can sign up and invite any email, so a first sign-in with pending invitations gets 409 `invitation_pending` (workspace, role, inviter) and the person accepts one or starts their own workspace (`POST /me/join`). Inviting an email that's in another workspace no longer answers 409, so it can't reveal who uses Probity. | `test_auth.py` |
| D2 | A new Clerk identity with an existing user's email is re-linked only when Clerk confirms the old identity no longer exists; otherwise the sign-in is refused and audited (`auth.relink_refused`). | `test_auth.py` |
| D3 | Uploads check the role before any parsing; PDF cleaning runs in the isolated helper (time and memory limits) and off the event loop. `PARSE_HELPERS` / `PARSE_MEMORY_MB` size helpers to the machine (Render: 1 × 320 MB), and the kernel's OOM killer picks a helper before the API. | `test_deploy_hardening.py`, `test_ingestion_hardening.py` |
| D4 | Parser helpers start with a bare environment (no database URLs, keys or app secrets). The API and workers run without `DATABASE_MIGRATE_URL`; only the migration step gets the owner URL (Render CMD, compose `migrate`). Cross-tenant `/metrics` aggregates therefore read through the app role and RLS, and show no rows. | `test_deploy_hardening.py` |
| D5 | MFA (when the policy requires it) also covers the invoice-risk policy and deleting approved history or POs. | `test_sod_mfa.py` |
| D6 | Production serves no `/docs`, `/redoc` or `/openapi.json`; a rejected token says only "invalid session token". | `test_deploy_hardening.py`, `test_auth.py` |
| D7 | Download filenames are header-safe (ASCII fallback plus RFC 5987 `filename*`), so non-Latin or quoted names no longer break the file route. | `test_deploy_hardening.py` |

Still open from that review (low): `--forwarded-allow-ips '*'` lets a client spoof the logged IP (only logs use it); MFA has no freshness window (`fva` age); set `CLERK_FRONTEND_API` in production so the web CSP names one Clerk host.

### Not part of the code
- **Repo hygiene:** `.gitignore` covers env files, keys, logs, local databases and real invoices.
- **Commit email:** local commits use the GitHub noreply address, and the repo's git config uses it by default.
