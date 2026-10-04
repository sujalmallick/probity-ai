# Tech Stack

Principle: reuse what the reference repos already proved (FastAPI + LangGraph + Postgres + Qdrant + Next.js/React). Keep everything optional that can be deferred (Neo4j, Celery).

| Layer | Choice | Why / Reference |
|---|---|---|
| Backend API | **Python 3.12, FastAPI, Pydantic v2** | PROXY, invoice-pipeline |
| Agent orchestration | **LangGraph** (StateGraph, checkpointer, `interrupt`, parallel branches) | PROXY, langgraph-multi-agent |
| LLM access | Provider-agnostic wrapper (Gemini / OpenAI / Anthropic / Groq / Ollama) with structured output via `instructor` or native schemas | invoice-pipeline multi-provider layer |
| Primary DB | **PostgreSQL 16** (SQLAlchemy 2 async + Alembic) | invoice-pipeline |
| Vector search | **Qdrant** (vendors, past cases, policy docs) | PROXY, invoice-pipeline |
| Graph | Postgres edge tables for MVP; **Neo4j** optional projection | PROXY |
| Queue / cache | **Redis** + **Celery** (or Arq) for long investigations, rate limiting, SSE pub/sub | PROXY |
| OCR / parsing | pdfplumber → PaddleOCR → Tesseract fallback; `unstructured` for EML | invoice-pipeline |
| Web research | Search API (Tavily/Serper/DuckDuckGo) + `httpx` + `trafilatura`; Playwright for JS-heavy pages; optional TinyFish for browser agents | langgraph-multi-agent, TinyDetective, PROXY |
| Domain intel | `python-whois` / RDAP, DNS (dnspython) | — |
| Anomaly ML | scikit-learn IsolationForest, z-score, rule engine | procurement-anomaly-detection |
| Fuzzy matching | rapidfuzz + sentence-transformers (all-MiniLM-L6-v2) | invoice-pipeline |
| Email | SMTP / Gmail API (send + IMAP/webhook for replies) | — |
| Auth | Clerk or Supabase Auth (JWT, roles); `AUTH_MODE=local` seeded demo users for offline demo (non-prod only) | invoice-pipeline (Clerk), PROXY (Supabase) |
| Object storage | S3-compatible / Supabase Storage (encrypted) | PROXY |
| Frontend | **React + Vite + TypeScript** (or Next.js 15), Tailwind, shadcn/ui, TanStack Query, Zustand, Recharts, react-force-graph | invoice-pipeline, PROXY |
| Realtime | Server-Sent Events (agent timeline) | — |
| Observability | OpenTelemetry, Prometheus metrics, structlog, Langfuse/LangSmith for traces | invoice-pipeline metrics |
| Testing | pytest, pytest-asyncio, Hypothesis (signals), Playwright (e2e), DeepEval-style eval scripts | PROXY tests, langgraph-multi-agent tests |
| CI/CD | GitHub Actions; Docker Compose (dev), Render/Fly/K8s manifests (prod) | PROXY k8s/, invoice-pipeline render.yaml |
| Package mgmt | `uv` (Python), `pnpm` (JS) | TinyDetective, langgraph-multi-agent |

## Decisions
1. **Postgres first, Neo4j later.** Edge tables + recursive CTEs cover shared-bank/domain detection for the demo.
2. **Deterministic before LLM.** Regex/checksum/arithmetic/duplicate logic never uses an LLM.
3. **SSE over WebSockets.** One-way progress stream is enough and simpler to deploy.
4. **LangGraph checkpointer in Postgres** (`langgraph-checkpoint-postgres`) for durable pause/resume at the human gate and vendor-wait.
5. **Model abstraction:** `LLMClient.generate(schema, prompt, tier)` where tier ∈ {fast, reasoning}; fast for extraction/triage, reasoning for verification/case summary.

## Repo layout (monorepo)
```
probity/
├─ apps/
│  ├─ api/                     FastAPI service
│  │  └─ src/probity/
│  │     ├─ api/               routers, deps, middleware, SSE
│  │     ├─ agents/            orchestrator, document, vendor, web, transaction, verification, risk, action
│  │     ├─ graph/             LangGraph build, state, routing, checkpointer
│  │     ├─ signals/           deterministic + statistical detectors
│  │     ├─ risk/              scoring engine, weights, explain
│  │     ├─ evidence/          models, store, verifier
│  │     ├─ ingestion/         OCR, parsing, validation, canonicalizers
│  │     ├─ memory/            case memory, vendor history
│  │     ├─ tools/             search, fetch, whois, registry, email
│  │     ├─ llm/               provider wrappers, prompts, schemas
│  │     ├─ guardrails/        input/output validators, policy, redaction
│  │     └─ db/                models, repositories, alembic
│  └─ web/                     React app
├─ benchmark/                  synthetic data + harness
├─ docs/                       these markdown files
├─ infra/                      docker-compose, k8s
└─ Makefile
```
