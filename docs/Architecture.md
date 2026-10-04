# System Architecture

## 1. Principles
1. **Investigate, then act within boundaries.** AI produces evidence; humans/policy authorize actions.
2. **Specialist agents, ~7, no overlap.** Each answers exactly one question.
3. **Agents don't trust agents.** Claim → Evidence → Verification → Risk Engine. The risk engine only consumes verified claims.
4. **Deterministic first.** Checksums, arithmetic, duplicates, bank diffs run as code. LLMs interpret; they never decide facts.
5. **Durable & resumable.** Long-running, pausable workflow (human gate, vendor wait) via LangGraph checkpointer.
6. **Everything is explainable.** Score = sum of stored, versioned signal contributions.

## 2. High-level topology
```
 React Web ──HTTPS──► FastAPI ──► LangGraph runtime ──► LLM providers
     ▲  SSE              │             │  │  │
     └───────────────────┤             │  │  └─► Tools: search, fetch, WHOIS, registry, email
                         │             │  └────► Qdrant (vendors, cases, policies)
                         ▼             ▼
                    PostgreSQL ◄── Redis (queue, pubsub, cache)
              (cases, evidence, vendors,   ▲
               signals, audit, checkpoints)│
                                    Celery/Arq workers
```

## 3. Agent roster
| # | Agent | Question it answers | Inputs | Outputs |
|---|---|---|---|---|
| 1 | Orchestrator/Planner | What is this and what must be checked? | doc + case context | `plan` (task graph) |
| 2 | Document Intelligence | What does this invoice contain? | file | typed fields + confidence + snippets |
| 3 | Vendor Investigator | Who is this entity; same as claimed? | vendor fields, master data | entity match + claims |
| 4 | Web Research | What external evidence exists? | entity identifiers | sourced findings (evidence) |
| 5 | Transaction Analyst | Is the transaction abnormal vs our data? | invoice + history + PO | signals + claims (mostly deterministic) |
| 6 | Evidence Verification | Are the claims supported? | claims + evidence | verified/refuted/unverified |
| 7 | Case Analyst | How do we explain this; what next? | verified claims + engine output | summary, recommendation (score comes from the code-only risk engine, not this agent) |
| + | Action Agent (post-gate) | What should happen next? | decision | drafts/emails, follow-ups, reply analysis |

## 4. LangGraph workflow
```
START → ingest → document_agent → validate_fields ─┐ (low-confidence → human_correction interrupt, optional)
                                                   ▼
                                              orchestrator(plan)
                                                   │
                       ┌───────────────────────────┼─────────────────────────┐
                       ▼ (parallel fan-out)        ▼                         ▼
                 vendor_investigator          transaction_analyst      (memory_lookup)
                       │
                       ▼
                 web_research (conditional: vendor unknown / signals / policy)
                       └───────────────┬─────────────────────────┘
                                       ▼ (fan-in)
                               evidence_verification
                                       │  (refuted/unverified → bounded retry ≤2 → deeper_research)
                                       ▼
                                 risk_engine ──► case_analyst
                                       ▼
                                 policy_gate ──► LOW & complete → auto_clear ─► finalize
                                       │
                                       ▼
                               human_gate (interrupt)
                  ┌────────────┬───────┴───────┬─────────────────┐
                  ▼            ▼               ▼                 ▼
               approve      reject    request_verification   investigate_further
                  │            │               │                 │ (loop to planner, depth+1, ≤2)
                  │            │      action_agent → send → wait(interrupt/timer)
                  │            │               │ reply → verify → risk_engine (re-score)
                  └────────────┴───────┬───────┘
                                       ▼
                                  finalize → write_case_memory → END
```
### Shared state (TypedDict/Pydantic)
`case_id, workspace_id, document, extraction, plan, claims[], evidence[], signals[], risk, recommendation, decision, drafts[], depth, errors[], budget{tokens, calls, seconds}, trace_id`.

### Control rules (from langgraph-multi-agent lessons)
- **Deterministic termination:** loop counters (`depth`, `retries`) in state; routers end the graph when exceeded. No LLM decides to stop.
- **Grounded state:** research nodes store raw source text/URLs in state (never only prose summaries) so downstream nodes can verify.
- **Cite-or-drop validator node** before the risk engine rejects claims with no evidence.
- **Budgets:** per-case caps on LLM tokens, web calls, wall-clock; exceeding degrades to `FAILED_PARTIAL` with lowered confidence, never silent success.

## 5. Evidence model
`Evidence` (immutable, content-hashed) ← referenced by → `Claim` ← referenced by → `RiskSignal` ← summed by → `RiskScore`. Verification flips claim status, never edits evidence.
Evidence is structured, not prose: `{source, field, value}` for internal/document facts (e.g. `invoice.bank_account = XXXX9812` vs `vendor_history.bank_account = XXXX1234`), plus `source_ref` + `excerpt` for web/registry pages. Agents return claims with evidence attached; they never return bare findings. Source tiers: T1 government/registry/internal verified records, T2 reputable business/news/directories, T3 forums/unverified.

## 6. Verification pipeline (per claim)
1. Evidence present? else `unverified`.
2. Deterministic match (number/string/date present in excerpt or internal record) → `verified`.
3. Else LLM entailment judged against excerpt only (no parametric knowledge), returns `supports|contradicts|neutral` + quoted span; span must exist in excerpt (string check).
4. Contradiction scan across other evidence.
5. Confidence = f(tier, match type, corroboration count).

## 7. Risk engine
The risk engine is **pure code, not an agent**. `score = clamp( Σ points of fired signals backed by verified claims , 0, 100)`.
- Points from the versioned weights table (Feature.md F7; core weights sum to 100; tenant-overridable). Tiers: LOW 0–29, MEDIUM 30–59, HIGH 60–79, CRITICAL 80–100.
- Statistical: Isolation Forest anomaly score mapped to ≤10 points (only when ≥30 history rows, else skipped and noted). Unit-price z-score feeds the deterministic `price_anomaly` signal.
- No LLM adjustment of any kind. The Case Analyst explains the score; it cannot change it.
- Only `verified` claims count fully; `unverified` capped at 0 points (shown as "unconfirmed").
- `explain()` returns the contribution list; same inputs + weights version → same score.

## 8. Data model (PostgreSQL)
`workspaces, users, vendors, vendor_bank_accounts(first_seen,last_seen,verified), vendor_domains, vendor_addresses, vendor_contacts, documents, cases, invoices, invoice_fields, line_items, purchase_orders, po_lines, investigations (runs), agent_runs, evidence, claims, risk_signals, risk_scores, decisions, drafts, messages, case_memory, graph_edges(src_type,src_id,rel,dst_type,dst_id,case_id), audit_log (append-only), policy, weights_versions, checkpoints`.
Key constraints: unique `(workspace_id, sha256)` on documents; unique normalized `(workspace_id, vendor_id, invoice_number)` flagged not blocked; audit_log append-only (no UPDATE/DELETE grants).

## 9. Vector collections (Qdrant)
`vendors_{ws}` (name/address embeddings for fuzzy entity match), `cases_{ws}` (summaries for memory retrieval), `policy_{ws}` (optional). Payload carries `workspace_id` filter on every query.

## 10. Relationship graph
MVP: `graph_edges` table + recursive CTE for "vendors sharing bank/address/domain/phone within 2 hops". Optional Neo4j mirror fed by outbox events.

## 11. Async execution
API enqueues `run_case(case_id)` → worker executes graph with Postgres checkpointer → publishes events to Redis pubsub → API fan-outs SSE. Human gate and vendor wait are `interrupt()` pauses resumed by `/decision` and `/vendor-reply` or timer.

## 12. Failure handling
Per-agent timeout + retry (exp. backoff, ≤2). Tool failure → `agent.failed` event, graph continues with reduced-confidence flag. LLM provider fallback chain. Idempotent nodes (keyed by `case_id+node+input_hash`) so resume is safe.

## 13. Deployment
Docker Compose (api, worker, web, postgres, redis, qdrant) for dev/demo; K8s/Render manifests for prod (see reference repos). Stateless api/worker; secrets via env/secret manager.

## 14. Demo fallback mode
`TOOLS_MODE=live|cached|mock`. `cached` replays recorded web/registry/WHOIS responses keyed by query so the demo is deterministic offline.
`LLM_MODE=live|cached|mock`. `cached` replays recorded LLM responses keyed by `prompt_version + input_hash`; `mock` returns deterministic schema-valid outputs. `AUTH_MODE=local` (non-prod only) uses seeded demo users with locally signed JWTs instead of Clerk/Supabase. Together these make `docker compose up` + `make seed` run the full demo with no network.
