# Reference map

MASTER_PROMPT.md lists six reference repositories. **None were cloned or copied in this build.** The patterns below were implemented from scratch, following how the specs describe each repository. Before adapting any code from these projects, check each repository's LICENSE.

| Pattern in Probity | Inspired by (per spec) | What we did / changed |
|---|---|---|
| Multi-agent LangGraph workflow with shared state, parallel branches | rakeshselvaraj0108/Proxy, luuisotorres/langgraph-multiagent-orchestration | `graph/build.py`: `StateGraph` with reducers (`checks`, `sources`, `failed`), fan-out to vendor + transaction agents, join, conditional web research |
| Deterministic claim-in-source check, evidence authority tiers | Proxy (`citation_verification.py`, `evidence_scoring.py`) | `evidence/verifier.py`: verbatim quote check, recomputation from structured evidence values, T1/T2/T3 tiers, single-T3 corroboration rule |
| Cite-or-refuse validator, deterministic termination, HITL node, grounding tests | ki11e6/langgraph-multi-agent | Cite-or-drop inside verification. Termination by counters in code (retries ≤ 2, depth ≤ 2, budgets). Human gate as durable status. Tests in `tests/` and `benchmark/safety_eval.py` |
| Investigation orchestrator, per-task state + retry, audit-friendly evidence items | darriusnjh/TinyDetective | `_guard` node wrapper (bounded retries, `agent.failed` events, FAILED_PARTIAL). Every tool result becomes an immutable, content-hashed `Evidence` row |
| Ingest → classify → extract → validate pipeline, per-field confidence + snippet, canonicalizers, vendor matching | Aayushdubey101/invoice-pipeline | `ingestion/`: magic-byte sniffing, pdfplumber, labelled-field parser with snippet per field, GSTIN checksum, IFSC, Indian money parsing, arithmetic checks. Vendor match = GSTIN exact → rapidfuzz |
| Feature engineering, z-score, rule flags (duplicate, round-sum, new vendor), synthetic generator | malavikasudheer42/procurement-anomaly-detection | `signals/detectors.py` (pure functions, Hypothesis-tested), `benchmark/run.py` seeded-anomaly generator. IsolationForest deferred (see DECISIONS) |
| Checkpointers, streaming, `interrupt` | luuisotorres/langgraph-multiagent-orchestration | SSE streaming of persisted agent events. `interrupt`/checkpointer deferred (see DECISIONS) |
