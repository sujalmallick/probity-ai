# Probity

**Evidence before payment.**

Probity is an AI investigation team for small-business payments. It researches each invoice, cross-checks internal and external evidence, explains its reasoning, and asks a human only when risk warrants it.

> **The LLM can investigate, but it cannot manipulate the risk score.**
> Agents discover signals. Every claim must carry structured evidence and pass verification. Code decides the score. Humans decide the payment.

```
Claim ──► Evidence ──► Verification ──► Risk engine (pure code) ──► Policy gate ──► Human
```

## The demo: one invoice, one complete story

| Step | What you see |
|---|---|
| 1. Upload | `invoice_4821.pdf`: ABC Supplies, 500 × ₹960 + 18% GST = **₹5,66,400** |
| 2. Live investigation | ✓ Invoice extracted · ✓ Vendor identified · ✓ Historical invoices searched · ✓ External sources searched · ✓ Transaction analyzed · ✓ Evidence verified |
| 3. Result | 🔴 **70 / 100 — HIGH**, with 3 verified anomalies: bank account `XXXX1234 → XXXX9812` (+35), price `₹590 → ₹960, +62.7%` (+20), sender domain registered 21 days ago (+15) |
| 4. Explainability | Click any anomaly, or **Why?**, to see claim → evidence (source, field, value) → verification |
| 5. Human gate | **Request vendor verification** |
| 6. Action | The agent drafts a neutral email to the vendor's *verified* master contact (not the one printed on the invoice). The approver sends it |
| 7. Resolution | The vendor reply arrives, and its statements stay *Unverified* (score unchanged). The approver confirms out-of-band and the score re-scores **70 → 20 LOW** (bank −35, domain −15) |
| 8. Memory | Close the case as CLEARED. The next invoice says: *"Previous investigation found a bank-account change … for this vendor"* |

## Run it locally (Windows)

Prerequisites: **Docker Desktop** (running), **Python 3.12+**, **Node 20+**.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1          # start everything
powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Reset   # start with a fresh demo database
```

The script installs dependencies on first run, starts Postgres + Redis in Docker, applies migrations, loads demo data,
then opens three windows: **API** (http://127.0.0.1:8010, docs at `/docs`), **worker** (runs the agents) and **web**
(http://localhost:5180). Close those windows to stop; `docker compose -f infra/docker-compose.dev.yml down` stops the databases.

| Service | Port |
|---|---|
| Web app | 5180 |
| API | 8010 |
| Postgres | 5434 (owner `probity` / app role `probity_app`) |
| Redis | 6380 |

Everything runs offline by default (recorded web results, rule-based agents, demo sign-in). Add keys to `.env` to go live:
`ANTHROPIC_API_KEY` + `LLM_MODE=live`, `TAVILY_API_KEY` + `TOOLS_MODE=live`, `AUTH_MODE=clerk` + Clerk keys, `EMAIL_BACKEND=resend` + key.

macOS/Linux: `make install`, `docker compose -f infra/docker-compose.dev.yml up -d`, `make seed`, then `make dev` and run the worker with
`cd apps/api && ../../.venv/bin/python -m celery -A probity.worker worker -B -Q probity`.

## Verification

| Command | Result in this build |
|---|---|
| `make test` | 54 backend tests pass (signals with Hypothesis, risk engine reproducibility, verifier, SSRF, RBAC, audit hash chain, tenant isolation, full demo flow); frontend typecheck passes |
| `make benchmark` | 30 clean + 15 seeded invoices: **100% of clean auto-cleared, 0 false clears**, precision/recall 1.00/1.00, p50 0.12 s |
| `make safety-eval` | **8/8** Guardrails cases: injection, fabrication, overclaim, SSRF, PII, auto-clear abuse, loop, reply spoof |

The benchmark data is synthetic and seeded, and the detectors are deterministic, so the perfect precision/recall shows the pipeline is wired correctly. It does not measure real-world accuracy. Timings exclude live web and LLM latency.

## Architecture

```
React (Vite, Tailwind) ──SSE/HTTPS──► FastAPI ──► LangGraph investigation graph
                                         │         document → orchestrator ─┬─ vendor_investigator ─┐
                                         │                                  └─ transaction_analyst ─┴► [web_research] → verification
                                         │         → risk_engine (code) → case_analyst → policy_gate → AUTO_CLEARED | AWAITING_HUMAN
                                         ▼
                           Postgres / SQLite (cases, evidence, claims, signals, audit hash chain)
```

| Agent | Answers | Notes |
|---|---|---|
| Document Intelligence | What does the invoice say? | Deterministic parser first. GSTIN checksum, IFSC, arithmetic. Bank number stored as last4 + HMAC + AES-GCM |
| Orchestrator | What must be checked? | Policy-driven plan, vendor match, case-memory lookup |
| Vendor Investigator | Is it the same entity? | Vendor master, GST registry, WHOIS/RDAP domain age |
| Transaction Analyst | Is it abnormal vs our data? | Bank change, duplicate, price z-score, PO quantity, dates. All pure functions |
| Web Research | What external evidence exists? | Structured findings with URL + verbatim excerpt + source tier. SSRF-safe fetch |
| Evidence Verification | Is each claim supported? | Cite-or-drop, recomputes from evidence values, verbatim quote check, T3 corroboration rule |
| Risk engine | How serious? | **Not an agent.** Versioned weights, core weights sum to 100, tiers 0–29/30–59/60–79/80–100 |
| Case Analyst | How do we explain it? | Writes the summary; cannot change the score |
| Action | What happens next? | Neutral drafts, reply analysis. Reply claims need approver out-of-band confirmation |

Repository layout: `apps/api` (FastAPI + agents), `apps/web` (React), `benchmark/` (benchmark + safety eval), `docs/` (PRD, specs, decisions), `infra/` (Docker).

## Modes

`TOOLS_MODE=live|cached|mock`, `LLM_MODE=live|cached|mock`, `AUTH_MODE=local|clerk`. The defaults (`cached`/`mock`/`local`) run the whole demo with no network. `LLM_MODE=live` uses the Anthropic SDK (`ANTHROPIC_API_KEY`) with schema-validated structured output, one repair attempt, then fail closed. See [docs/DECISIONS.md](docs/DECISIONS.md) for what is fully implemented and what is simplified.

## Docs

[PRD](docs/PRD.md) · [Features](docs/Feature.md) · [Architecture](docs/Architecture.md) · [Guardrails](docs/Guardrails.md) · [Security](docs/Security.md) · [API](docs/API.md) · [AI instructions](docs/AI_Instructions.md) · [Decisions](docs/DECISIONS.md) · [Reference map](docs/REFERENCE_MAP.md)

All data in this repository is synthetic. Probity identifies anomalies and recommends; it never executes payments and never makes accusations.
