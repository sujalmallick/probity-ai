# Decisions log

Where a spec was ambiguous, I chose the more conservative behaviour: more human review, less autonomy. Where the build simplifies the spec, it is listed here so nothing is overstated.

## Product decisions (agreed with the team)

| # | Decision | Why |
|---|---|---|
| D1 | Core weights are **bank 35, price 20, domain 15, identity 10, duplicate 10, address 5, missing PO 5 (= 100)**. Tiers are LOW 0–29, MEDIUM 30–59, HIGH 60–79, CRITICAL 80–100. | The original demo said "87 HIGH" from three anomalies worth 50 points. That was arithmetically impossible, and 87 would be CRITICAL. Rebalancing keeps the "3 verified anomalies" story honest: 70 HIGH → 20 LOW. |
| D2 | The risk engine is **pure code**. No LLM adjustment exists (the earlier ±10 was removed). | The judging line: "The LLM can investigate, but it cannot manipulate the risk score." A test asserts the engine has no parameter through which text can enter. |
| D3 | A vendor reply never lowers the score by itself. Its claims stay `unverified` until an approver records an **out-of-band confirmation**. | Whoever sent a tampered invoice may control the reply channel too (Guardrails G11). It costs one extra click on stage. |
| D4 | ~~The demo is one invoice end to end.~~ Superseded 2026-10-04: Probity runs on real data only; there is no demo invoice or synthetic benchmark. | Real users, real evidence. |
| D5 | Evidence is **structured** (`{source, field, value}`, plus `excerpt` for text sources). Agents emit claims with a machine-checkable `assertion`, and the verifier recomputes from evidence values rather than trusting the agent's numbers. | Evidence is first-class everywhere. |

## Conservative interpretations

- **Auto-clear requires every condition**: tier LOW, no risk indicator fired (any fired signal except `no_history` sends the case to review even at LOW), checks complete, amount ≤ policy limit, validations pass, and the vendor was not previously flagged. Single-anomaly cases (e.g. a duplicate at 10 points) are therefore held, never auto-cleared.
- **Previously flagged vendor** means any prior case that peaked HIGH or CRITICAL, or closed `CONFIRMED_ISSUE`. Such vendors are routed to a human even when the score is LOW.
- **Memory**: only human-closed outcomes are written. `CONFIRMED_ISSUE` adds `prior_confirmed_issue` (+15). `CLEARED` is shown as context and adds 0 points.
- **T3-only web evidence** cannot verify a claim on its own and is capped at `warn` severity.

## Real data only (2026-10-04)

- No demo, seed, fixture, offline or mock mode exists in the app. Required settings (Postgres, an AI key for Anthropic or Gemini, Clerk, two app secrets) are
  checked at startup; the API prints a live/missing checklist and refuses to start without them. `JWT_SECRET` was removed with
  local sign-in.
- Test doubles (AI transport, RDAP/search/fetch, Resend, Clerk JWKS/profile) are injected only by `apps/api/tests/conftest.py`;
  tests build their own data in `tests/factories` on a throwaway Postgres database.
- **Never fabricate.** A failed tool or missing data gives `could_not_verify` (result + timeline), adds no points, and holds the
  invoice when the check is required. When the AI fails, a labelled deterministic fallback is used (engine summary, rule-based reply
  reading, neutral template draft signed with the workspace name, rule-based search queries).
- GST: no free official GSTIN API exists. GSTIN format + checksum are always validated; registry status is always "could not verify".
  Users may record a status read on the GST portal; it is shown as "Entered manually by <name>" and a manual Cancelled/Suspended holds
  the invoice (engine weights unchanged).
- Email: Resend only; only `EMAIL_ALLOWLIST` addresses receive mail until `EMAIL_SEND_TO_ANY=true`. Blocked sends are logged and audited.
- Background jobs run inside the API process (`TASK_BACKEND=inline`, plus an in-process follow-up sweep); Celery + Redis stay optional.
- Scans and images are refused with a clear message until Claude document reading lands (Phase 4). There is no Tesseract.

## Current limits (honest list)

| Spec | This build | Path to full |
|---|---|---|
| LangGraph `interrupt` + Postgres checkpointer at the human gate | The human gate is a durable case status (`AWAITING_HUMAN`); decisions resume via the services layer. | `langgraph-checkpoint-postgres` |
| Qdrant vector search | rapidfuzz name matching + GSTIN exact match; memory lookup by vendor / bank HMAC / domain in SQL. | Embeddings with a `workspace_id` filter |
| GST registry | Checksum only + manual entry (see above). | A paid GSP/registry provider |
| Scanned / image invoices | Refused with a clear message. | Claude PDF/image understanding (Phase 4) |
| Isolation Forest (≥ 30 history rows) | Amount z-score ≥ 3 with ≥ 30 rows; skipped below that. | scikit-learn model per workspace |
| Rate limits | In-memory per process. | Redis token bucket |

## Build environment notes

- Python 3.13 locally (the Dockerfile uses 3.12), Node 24, Vite 8, Tailwind 4, React 18.
- `make` is not available on stock Windows; the README gives the equivalent commands.
