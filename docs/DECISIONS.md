# Decisions log

Where a spec was ambiguous, I chose the more conservative behaviour: more human review, less autonomy. Where the build simplifies the spec, it is listed here so nothing is overstated.

## Product decisions (agreed with the team)

| # | Decision | Why |
|---|---|---|
| D1 | Core weights are **bank 35, price 20, domain 15, identity 10, duplicate 10, address 5, missing PO 5 (= 100)**. Tiers are LOW 0–29, MEDIUM 30–59, HIGH 60–79, CRITICAL 80–100. | The original demo said "87 HIGH" from three anomalies worth 50 points. That was arithmetically impossible, and 87 would be CRITICAL. Rebalancing keeps the "3 verified anomalies" story honest: 70 HIGH → 20 LOW. |
| D2 | The risk engine is **pure code**. No LLM adjustment exists (the earlier ±10 was removed). | The judging line: "The LLM can investigate, but it cannot manipulate the risk score." A test asserts the engine has no parameter through which text can enter. |
| D3 | A vendor reply never lowers the score by itself. Its claims stay `unverified` until an approver records an **out-of-band confirmation**. | Whoever sent a tampered invoice may control the reply channel too (Guardrails G11). It costs one extra click on stage. |
| D4 | The demo is **one invoice end to end**. Clean-invoice auto-clear is shown through the benchmark. | Tells a story instead of touring features. |
| D5 | Evidence is **structured** (`{source, field, value}`, plus `excerpt` for text sources). Agents emit claims with a machine-checkable `assertion`, and the verifier recomputes from evidence values rather than trusting the agent's numbers. | Evidence is first-class everywhere. |

## Conservative interpretations

- **Auto-clear requires every condition**: tier LOW, no risk indicator fired (any fired signal except `no_history` sends the case to review even at LOW), checks complete, amount ≤ policy limit, validations pass, and the vendor was not previously flagged. Single-anomaly cases (e.g. a duplicate at 10 points) are therefore held, never auto-cleared.
- **Previously flagged vendor** means any prior case that peaked HIGH or CRITICAL, or closed `CONFIRMED_ISSUE`. Such vendors are routed to a human even when the score is LOW.
- **Memory**: only human-closed outcomes are written. `CONFIRMED_ISSUE` adds `prior_confirmed_issue` (+15). `CLEARED` is shown as context and adds 0 points.
- **T3-only web evidence** cannot verify a claim on its own and is capped at `warn` severity.

## Simplifications in this build (honest list)

| Spec | This build | Path to full |
|---|---|---|
| Postgres 16 + Alembic, RLS | SQLAlchemy `create_all`. SQLite by default (Postgres URL supported, compose file provided, **not exercised in this build because Docker was not running**). Telemetry (agent events, LLM calls) goes to a separate SQLite file so live progress never waits on a write lock. Workspace scoping is enforced in the app layer and tested; Postgres RLS policies are not written. | Add Alembic migrations, `SET app.workspace_id` + RLS policies |
| Redis + Celery workers, Redis pub/sub for SSE | In-process thread pool. SSE polls persisted events (supports `Last-Event-ID`). | Swap `_submit` for a Celery/Arq task; publish events to Redis |
| LangGraph `interrupt` + Postgres checkpointer at the human gate | The investigation is a LangGraph `StateGraph` (parallel fan-out, deterministic routing, per-node retries ≤ 2). The human gate is a **durable case status** (`AWAITING_HUMAN`), and decisions resume via the services layer. | Add `langgraph-checkpoint-postgres` and model post-gate steps as graph nodes |
| Qdrant vector search (vendors, case memory) | rapidfuzz name matching + GSTIN exact match. Memory lookup by vendor / bank HMAC / domain in SQL. | Embed vendor names and case summaries into Qdrant with a `workspace_id` filter |
| LLM-backed agents | `LLM_MODE=mock` (default) gives deterministic, schema-valid agent outputs. `live` calls the Anthropic SDK (`messages.parse`) with redaction, one repair, and fail-closed. **Live mode was not exercised in this build** (no API key used). Sampling parameters are not set because current Claude models reject `temperature`. | Run the eval suite in `LLM_MODE=live`, record `cached` fixtures |
| Live web search provider | Cached fixtures. Live RDAP goes through the SSRF-safe fetcher. Tavily/Serper adapter not wired. | Implement `web_search` live branch |
| OCR (PaddleOCR / Tesseract) for images and scanned PDFs | Text-layer PDFs, EML and text are supported. Images need `pytesseract` + tesseract on the host; scanned PDFs are rejected with a clear error. | Add an OCR worker |
| Sandboxed parsing, ClamAV, PDF active-content stripping | Magic-byte MIME check, 15 MB / 50-page caps. **No AV scan or sandbox.** | Isolated parser container |
| Isolation Forest (≥ 30 history rows) | Amount z-score ≥ 3 with ≥ 30 rows (≤ 10 points). Demo vendors have 12 rows, so it is skipped and noted. | scikit-learn model per workspace |
| Clerk / Supabase auth | `AUTH_MODE=local` signed JWTs for seeded demo users (refused when `ENV=prod`). SSE passes the token as a query parameter because EventSource cannot set headers. | JWKS verification |
| Investigate-further depth ≤ 2 | Implemented: deeper web queries (directors, official domain), with 409 at depth 2. | — |
| Extraction preview + correction UI before launch | `corrections` are accepted by the API and override parsed fields, but there is no preview UI yet. | Preview step in `/cases/new` |
| Relationship graph (F12), PDF export | Not built (F12 is stretch). Export is JSON only. | — |
| Rate limits | In-memory per process. | Redis token bucket |
| Follow-up timer | `followup_at` is stored and shown; no scheduler fires reminders. | Scheduled job |

## Build environment notes

- Python 3.13 locally (the Dockerfile uses 3.12), Node 24, Vite 8, Tailwind 4, React 18.
- `make` is not available on stock Windows; the README gives the equivalent commands.
