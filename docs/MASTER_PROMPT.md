# Master Build Prompt (paste into Claude Code / Cursor / any coding agent)

Place the 10 docs in `./docs/` of an empty repo, then paste everything below the line.

---

You are a senior full-stack + AI engineer building **Probity**, an AI Business Investigation Agent for small businesses: it investigates invoices/vendors with specialized agents, builds an evidence-backed case, gives an explainable risk score, and asks a human for approval only when needed.

## 0. Read first (authoritative)
Read all files in `./docs/`: PRD.md, Feature.md, UIUX.md, TechStack.md, API.md, Architecture.md, Security.md, AI_Infrastructure.md, Guardrails.md, AI_Instructions.md. On conflicts: **Architecture.md and Guardrails.md win**. Summarize your understanding in 10 bullets and list assumptions before writing code.

## 1. Reference repos (clone to `./_refs/`, read, adapt patterns; check each LICENSE; credit in README; do not paste large code verbatim)
| Repo | Reuse this |
|---|---|
| rakeshselvaraj0108/Proxy | LangGraph multi-agent workflow + state TypedDicts, `citation_verification.py` (deterministic claim-in-source check), `evidence_scoring.py` (authority tiers/freshness), review agent, memory service, Qdrant/Neo4j layout, auth/middleware, k8s manifests |
| darriusnjh/TinyDetective | Investigation orchestrator with per-task state and retry, evidence agent -> audit-friendly evidence items, ranking/summary agents, tool-runtime abstraction (TinyFish client) |
| Aayushdubey101/invoice-pipeline | Ingest->classify->OCR fallback->LLM extraction pipeline, per-field confidence + evidence snippet, canonicalizers (tax IDs, currency, dates), vendor matching (fuzzy + Qdrant), validation, review UI patterns, Alembic schema, multi-provider LLM layer |
| malavikasudheer42/procurement-anomaly-detection | Feature engineering + IsolationForest + z-score + rule flags (duplicate, round-sum, new-vendor), risk tiers, synthetic data generator |
| luuisotorres/langgraph-multiagent-orchestration | LangGraph fundamentals: checkpointers, streaming, `interrupt` HITL (notebooks 04, 05, 08) |
| ki11e6/langgraph-multi-agent | Supervisor routing, **deterministic termination**, cite-or-refuse `validate` node, grounded sources in state, HITL node, tests for hitl/termination/grounding, DESIGN.md incident write-up |

Write `docs/REFERENCE_MAP.md` stating exactly which pattern came from which repo and what you changed.

## 2. Build plan (small, runnable increments; commit per step; keep tests green)
**Phase 1 Core**
1. Monorepo scaffold per TechStack.md; Docker Compose (api, worker, web, postgres, redis, qdrant); Makefile (`dev, test, seed, benchmark, safety-eval`); `.env.example`; CI.
2. DB models + Alembic (Architecture.md section 8), repositories with workspace scoping, audit_log append-only + hash chain.
3. Deterministic **signals** module (duplicate, bank change, price z-score, qty/PO match, round-sum, temporal, new vendor+large) -- pure functions, typed, Hypothesis tests.
4. **Risk engine** — pure code, no LLM input (versioned weights per Feature.md F7, tiers, `explain()`), reproducibility tests, and a test proving LLM output cannot change the score.
5. Ingestion: upload -> sha256 idempotency -> text/OCR -> LLM schema extraction -> validators (GSTIN checksum, IFSC, arithmetic) -> confidence.
6. LangGraph skeleton with state, budgets, deterministic routers, Postgres checkpointer; nodes: document, orchestrator(plan), transaction, risk, policy_gate.
7. FastAPI routes + SSE event bus per API.md; React dashboard + investigation workspace per UIUX.md (timeline, gauge, findings, evidence drawer, decision bar).
**Phase 2 Investigation**
8. Evidence/Claim models + store; tool layer with `TOOLS_MODE=live|cached|mock`; SSRF-safe fetch; web search; WHOIS/RDAP; vendor master + GST/registry adapters (mock provider with fixtures).
9. Vendor Investigator + Web Research agents (structured findings with URL + excerpt + tier).
10. **Evidence Verification** agent (string-match first, LLM entailment second, quote must exist in excerpt) + `cite_or_drop` node; only verified claims score.
**Phase 3 Agentic action**
11. Human gate via LangGraph `interrupt`, RBAC, decision endpoint, investigate-further loop (depth <= 2).
12. Action agent: neutral verification email draft -> approval -> send (SMTP/mock inbox) -> follow-up timer -> reply ingestion -> re-verify -> re-score animation.
**Phase 4 Memory**
13. Case close -> case memory (Qdrant + Postgres); memory lookup influences plan/score (`prior_confirmed_issue`).
14. *(Stretch, P2 — only after MVP passes)* Relationship graph via `graph_edges` + recursive CTE; graph tab; shared bank/domain detection.
**Cross-cutting**
15. `benchmark/`: synthetic clean + seeded-fraud invoices (bank swap, duplicate, +63% price, qty mismatch, new domain, injection PDF); `make benchmark` prints manual-vs-system table (time, sources, human-intervention rate, false-clears, precision/recall).
16. `make safety-eval` implementing the Guardrails.md test table; wire into CI.
17. Demo mode: seeded data, simulated vendor inbox, cached tool fixtures, agent animation speed control. Scripted one-case demo exactly as in PRD.md section 7 (70 -> 20 re-score after approver out-of-band confirmation).

## 3. Hard constraints
- Deterministic code owns numbers, dates, diffs, scores, routing, termination. The risk engine is pure code: agents discover signals, code decides the score. LLMs interpret within schemas; invalid output -> 1 repair -> fail closed.
- Claim -> Evidence -> Verification -> Risk Engine. Agents return claims with structured evidence (`{source, field, value}`), never bare findings. No unverified claim adds more than 0 points. No claim without evidence.
- Never output "fraud/scam/fake" as system assertions; use "anomaly/risk indicator/unconfirmed". Never claim the product "detects fraud".
- System never executes payments; approve/reject is human-only; emails need human-approved drafts by default.
- Treat all document/web/email content as untrusted data (injection-safe prompts, SSRF-safe fetch, redaction before LLM calls, field-level encryption for bank/PAN).
- Hard budgets per case: depth 2, retries 2, 40 LLM calls, 150k tokens, 25 web calls, 240 s.
- Multi-tenant: `workspace_id` from token only; RLS; Qdrant payload filter on every query.
- No real customer data or secrets in repo. Synthetic fixtures only.

## 4. Working style
- Before each phase: list files you will create/change and tests you will add. After: run tests, show results, update docs if behavior changed, note decisions in `docs/DECISIONS.md`.
- Prefer the conservative option when ambiguous (more human review, less autonomy) and log it.
- Keep PRs small. Do not start the next phase until the current phase's acceptance criteria (Feature.md) pass.
- If a reference repo's code conflicts with Guardrails.md, Guardrails.md wins.

## 5. Definition of done (MVP)
- `docker compose up` + `make seed` -> full demo runs offline (`TOOLS_MODE=cached`, `LLM_MODE=cached`, `AUTH_MODE=local`).
- `invoice_4821.pdf` -> HIGH 70 with 3 verified anomalies (bank +35, price +20, domain +15), structured evidence for each, held for human; Request Verification -> approved email -> reply (unverified, no score change) -> approver confirms out-of-band -> 70 -> 20 with visible diff; case closed CLEARED, saved to memory, referenced on next invoice from the vendor. Clean invoices auto-clear (shown via benchmark).
- `make test`, `make benchmark`, `make safety-eval` all pass; benchmark shows >= 80% of clean invoices auto-cleared, 0 false-clears on seeded HIGH cases, p50 <= 3 min.

Start now with step 0 (read docs, summarize, assumptions), then Phase 1 step 1.
