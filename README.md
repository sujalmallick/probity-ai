# Probity

**Evidence before payment.**

Probity is an AI investigation team for small-business payments. It researches each invoice, cross-checks internal and external evidence, explains its reasoning, and asks a human only when risk warrants it.

> **The LLM can investigate, but it cannot manipulate the risk score.**
> Agents discover signals. Every claim must carry structured evidence and pass verification. Code decides the score. Humans decide the payment.

```
Claim ──► Evidence ──► Verification ──► Risk engine (pure code) ──► Policy gate ──► Human
```

Probity runs on **your real data only**. There is no demo workspace, no seeded vendors, no offline or mock mode. When a source can't be
checked (no key, network failure, no registry provider, not enough history) the check says **"could not verify"** in the result and the
agent timeline, adds no risk points, and holds the invoice for a person if the check was required.

## How a case works

| Step | What happens |
|---|---|
| 1. Upload | A PDF or emailed invoice (scans are not read yet). Fields are parsed deterministically; Claude fills low-confidence gaps, and only text that appears verbatim in the document is accepted |
| 2. Investigation | Vendor match against *your* vendor list · bank account vs your verified accounts · prices vs your invoice history · PO match · domain age (RDAP) · GSTIN checksum · web research (Tavily) |
| 3. Result | A 0–100 score from verified signals only, with the evidence for every point. Missing evidence never adds points |
| 4. Gate | Auto-clear only when every required check was verified and nothing fired; otherwise held, with the reason (e.g. *Could not verify: bank account verification — no bank history for vendor*) |
| 5. Action | A neutral verification email to the vendor's *verified* contact, sent only after an approver approves it, and only to `EMAIL_ALLOWLIST` until you enable real sending |
| 6. Resolution | Vendor replies stay *unverified* until an approver confirms out-of-band through a channel already on file |
| 7. Memory | Closed cases inform the vendor's next invoice |

## Run it locally (Windows)

Prerequisites: **Docker Desktop** (running), **Python 3.12+**, **Node 20+**, an **Anthropic API key** and a **Clerk** application.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
```

The first run starts Postgres in Docker, creates `apps/api/.env` with generated app secrets, prints the live/missing checklist and
creates the empty database schema. Fill in the missing keys in `apps/api/.env` (and `VITE_CLERK_PUBLISHABLE_KEY` in
`apps/web/.env.local`), run it again, and it opens the **API** (http://127.0.0.1:8010, docs at `/docs`) and the **web app**
(http://localhost:5180). Sign up with Clerk: your first sign-in creates your workspace with you as owner. Add vendors, verified bank
accounts and contacts, and import past invoices and POs before investigating.

macOS/Linux: `make install`, `make db`, `cd apps/api && ../../.venv/bin/python -m probity.bootstrap --generate-secrets`, fill in
`apps/api/.env`, then `make dev`.

| Setting (`apps/api/.env`) | Required | Without it |
|---|---|---|
| `DATABASE_URL`, `DATABASE_MIGRATE_URL` | yes | the API will not start |
| `ANTHROPIC_API_KEY` | yes | the API will not start |
| `CLERK_ISSUER`, `CLERK_SECRET_KEY`, `CLERK_AUTHORIZED_PARTIES` | yes | the API will not start |
| `FIELD_KEY_B64`, `HMAC_KEY` | yes (generated) | the API will not start |
| `TAVILY_API_KEY` | no | external reputation: "could not verify" |
| `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_ALLOWLIST` | no | no email is sent; with them, only allowlisted addresses receive mail |
| `TASK_BACKEND=celery`, `REDIS_URL` | no | investigations run inside the API process |
| `STORAGE_BACKEND=s3`, `CLAMAV_HOST` | prod only | local disk, no virus scan |

GST: there is no free official GSTIN lookup API, so Probity validates the GSTIN format and checksum and always shows registry status as
"could not verify". A user can record what they read on the GST portal; it is shown as *"Entered manually by &lt;name&gt;"*, never as
registry-verified.

## Verification

```
make db && make test
```

The backend suite builds its own data in `apps/api/tests/factories`, runs against a throwaway PostgreSQL database with row-level
security enforced, and replaces the AI, RDAP, web search, email and Clerk with fakes injected by the test suite (the app has no switch
for this). It covers the full case flow, the auto-clear gate and "could not verify" paths, Clerk-only sign-in, the email allowlist,
RBAC, tenant isolation, the audit hash chain and the Guardrails safety cases (injection, fabrication, overclaiming, SSRF, PII,
auto-clear abuse, loops, spoofed replies).

## Architecture

```
React (Vite, Tailwind) ──SSE/HTTPS──► FastAPI ──► LangGraph investigation graph
                                         │         document → orchestrator ─┬─ vendor_investigator ─┐
                                         │                                  └─ transaction_analyst ─┴► [web_research] → verification
                                         │         → risk_engine (code) → case_analyst → policy_gate → AUTO_CLEARED | AWAITING_HUMAN
                                         ▼
                           PostgreSQL (row-level security; cases, evidence, claims, signals, audit hash chain)
```

| Agent | Answers | Notes |
|---|---|---|
| Document Intelligence | What does the invoice say? | Deterministic parser first. GSTIN checksum, IFSC, arithmetic. Bank number stored as last4 + HMAC + AES-GCM |
| Orchestrator | What must be checked? | Policy-driven plan, vendor match, case-memory lookup |
| Vendor Investigator | Is it the same entity? | Vendor list, GSTIN checksum (+ manual GST entry), RDAP domain age |
| Transaction Analyst | Is it abnormal vs our data? | Bank change, duplicate, price, PO quantity, dates. All pure functions |
| Web Research | What external evidence exists? | Tavily search; findings carry URL + verbatim excerpt + source tier. SSRF-safe fetch |
| Evidence Verification | Is each claim supported? | Cite-or-drop, recomputes from evidence values, verbatim quote check |
| Risk engine | How serious? | **Not an agent.** Versioned weights, core weights sum to 100, tiers 0–29/30–59/60–79/80–100 |
| Case Analyst | How do we explain it? | Writes the summary; cannot change the score. If the AI is unavailable, the engine's own summary is shown and labelled |
| Action | What happens next? | Neutral drafts, reply analysis. Reply claims need approver out-of-band confirmation |

Repository layout: `apps/api` (FastAPI + agents), `apps/web` (React), `docs/` (PRD, specs, decisions), `infra/` (Docker).

## Docs

[PRD](docs/PRD.md) · [Features](docs/Feature.md) · [Architecture](docs/Architecture.md) · [Guardrails](docs/Guardrails.md) · [Security](docs/Security.md) · [API](docs/API.md) · [Decisions](docs/DECISIONS.md) · [Web real-data checklist](docs/WEB_REAL_DATA_CHECKLIST.md)

Probity identifies anomalies and recommends; it never executes payments and never makes accusations.
