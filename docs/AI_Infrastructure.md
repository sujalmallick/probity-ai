# AI Infrastructure

How models, prompts, tools, retrieval, evaluation and cost control are organized.

## 1. LLM gateway
Single `LLMClient` wrapper (pattern from invoice-pipeline's multi-provider layer).
```python
class LLMClient:
    def generate(self, *, schema: type[BaseModel], system: str, user: str,
                 tier: Literal["fast","reasoning"], tags: dict) -> BaseModel: ...
```
- **Tiers:** `fast` (extraction cleanup, triage, query generation, email drafting) · `reasoning` (claim entailment, case summary, contradiction analysis).
- Provider chain via env (e.g. Gemini → OpenAI → Anthropic → local Ollama); automatic fallback on timeout/429/5xx.
- `LLM_MODE=live|cached|mock`: `cached` replays recorded responses keyed by `prompt_version + input_hash` (offline demo); `mock` returns deterministic schema-valid outputs for tests.
- Structured output enforced (native JSON schema or `instructor`); invalid → 1 repair retry → fail closed.
- Temperature 0 for extraction/verification; ≤0.3 for drafting.
- Per-call metadata: `case_id, agent, node, prompt_version, model, tokens_in/out, latency, cost` → Langfuse/OTel.

## 2. Prompt management
- Prompts live in `llm/prompts/<agent>/<name>.v<N>.md` with variables; versioned and referenced by ID in traces.
- Every prompt has: role, task, input contract, output schema, hard rules, refusal/uncertainty instructions, 2–3 few-shot examples (incl. adversarial).
- Changes require passing the eval suite (§8) before merge.

## 3. Agent ↔ tool matrix
| Agent | Tools (allowlist) | LLM tier | Deterministic core |
|---|---|---|---|
| Orchestrator | `get_case_context`, `memory_lookup` | fast | policy-based check selection |
| Document | `ocr`, `pdf_text`, `parse_eml` | fast (extraction) | regex/checksum/arithmetic validators |
| Vendor Investigator | `vendor_master_search`, `gst_lookup`, `registry_lookup`, `whois`, `fetch_url` | fast | fuzzy/vector entity match |
| Web Research | `web_search`, `fetch_url` | fast (query gen, extraction) | source tiering, dedupe |
| Transaction Analyst | `history_query`, `po_query` | none/fast | all signals are code |
| Verification | `evidence_get`, `string_match` | reasoning (entailment only) | match-first, LLM-second |
| Case Analyst | `risk_engine` (read-only) | reasoning (summary only; cannot change score) | scoring engine (pure code) |
| Action | `draft_email`, `send_email` (post-approval), `schedule_followup` | fast | template engine |

## 4. Tool layer
- All tools are typed (Pydantic in/out), idempotent where possible, with timeouts and a result cache (Redis, keyed by normalized args, TTL by tool).
- `TOOLS_MODE=live|cached|mock` for demo determinism: cached mode replays recorded fixtures.
- Web: search provider abstraction (Tavily/Serper/DDG) → `httpx` + `trafilatura` text extraction; Playwright fallback for JS pages; optional TinyFish browser automation for registries that need interaction (reference: TinyDetective runtime/adapters).
- Every tool result is wrapped into `Evidence` (kind, source_ref, excerpt, retrieved_at, tier, content_hash). Tool outputs that cannot be evidenced are discarded.
- Source tier classifier: T1 `.gov.in`, MCA, GSTN, internal verified; T2 known directories/news; T3 everything else.

## 5. Retrieval (Qdrant)
- Embeddings: `all-MiniLM-L6-v2` (local, cheap) default; swappable.
- Collections: vendor identity, closed-case memory, optional policy docs.
- Hybrid match for vendors: normalized-name + GSTIN exact → rapidfuzz → vector fallback; thresholds tuned on benchmark.
- Memory retrieval returns top-k closed cases for same vendor/bank/domain; only `CONFIRMED_ISSUE`/`CLEARED` outcomes are used.

## 6. Statistical / ML layer
- Features per invoice: amount, amount÷vendor_avg, days since last invoice, 30-day count, round-sum flag, unit-price z-score, new-vendor flag.
- IsolationForest (n_estimators=200, fixed seed) trained per workspace on history when ≥30 rows; z-score threshold σ>2.5 for unit price; outputs normalized 0–1 anomaly score mapped to ≤10 risk points (reference: procurement-anomaly-detection).
- Model artifacts versioned; fall back to rules-only with explicit "insufficient history" note.

## 7. Orchestration runtime
- LangGraph with Postgres checkpointer; workers (Celery/Arq) run graphs; Redis pubsub streams events.
- Budgets in state: `max_llm_calls=40`, `max_tokens=150k`, `max_web_calls=25`, `max_seconds=240`, `max_depth=2` (investigate-further loops). Exceeding → degrade with explicit confidence reduction.
- Parallel fan-out for independent branches; per-node timeouts; idempotency keys.

## 8. Evaluation (build this, it is the pitch)
| Suite | What | Metric / gate |
|---|---|---|
| Extraction | 50 synthetic + real-style invoices | field F1 ≥ 0.95 on amount/invoice no./GSTIN |
| Signals | seeded duplicates, bank swaps, price/qty/PO mismatch | recall ≥ 0.95, precision ≥ 0.9 |
| Verification | claims with planted fabricated evidence | fabricated-claim pass-through = 0 |
| End-to-end | clean vs malicious set | zero false-clear of seeded HIGH; auto-clear ≥ 80% of clean |
| Safety | injection, accusation, PII-leak prompts (Guardrails.md) | 100% pass |
| Latency/cost | per-case wall-clock and $ | p50 ≤ 3 min; cost/case reported |
- `make benchmark` generates the manual-vs-system table; `make safety-eval` runs adversarial cases in CI.
- Human feedback loop: reviewer corrections and closed-case outcomes stored as labeled data for threshold/weight tuning (offline, per tenant).

## 9. Observability
Langfuse/LangSmith traces per case (spans = nodes, tool calls, LLM calls), Prometheus metrics (`case_duration`, `agent_failures`, `llm_cost`, `auto_clear_rate`, `unverified_claim_rate`), structured logs with `trace_id`/`case_id`.

## 10. Cost & latency controls
Fast tier for ≥80% of calls; cache tool + embedding results; early exit on exact-duplicate hash; skip web research when vendor is verified-known and no signals fire (policy-controlled); batch entailment checks into one call per case.

## 11. Local/dev
`docker compose up` brings api, worker, web, postgres, redis, qdrant; `OLLAMA_BASE_URL` enables offline model; seeded demo data via `make seed`.
