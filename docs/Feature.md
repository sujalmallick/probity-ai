# Feature Specification

Priority: **P0** = demo-critical, **P1** = strong value, **P2** = stretch. Each feature lists acceptance criteria (AC).

## F1. Invoice Ingestion & Document Intelligence (P0)
*Reference: invoice-pipeline.*
- Accept PDF, PNG/JPG, EML. SHA-256 idempotency (same file → cached case).
- Pipeline: classify → text extract (pdfplumber) → OCR fallback (PaddleOCR/Tesseract) → schema extraction → validation → confidence.
- Extracted fields: vendor, invoice no., date, due date, currency, subtotal, tax, total, GSTIN, PAN, bank account/IFSC, PO no., line items (desc, qty, unit price), address, email/domain.
- **AC:** every field has `value`, `confidence`, `evidence_snippet` (+ page/bbox when available). GSTIN checksum, IFSC format, and tax arithmetic validated deterministically. LLM is never the source of truth for numbers; OCR/text is.

## F2. Orchestrator / Planner (P0)
- Classifies case type, determines missing info, builds the task graph (which checks, parallel vs sequential), sets priority.
- Dynamic: skips checks that lack inputs, adds checks when signals appear (e.g. new bank → deeper bank check).
- **AC:** emits a `plan` JSON stored on the case; plan visible in dashboard.

## F3. Vendor Investigator (P0)
*Reference: TinyDetective entity discovery.*
- Resolve the entity: internal vendor master (fuzzy + vector match), GSTIN lookup (pluggable provider; mock for demo), MCA/registry lookup, website + WHOIS/domain age, email-domain vs website match.
- **AC:** returns `entity_match` {same_entity: true/false/unknown, evidence[]}; handles "unknown vendor" path.

## F4. Web Research Agent (P1; P0 for demo story)
- Targeted searches (complaints, scam, directors, address, domain), page fetch + extraction.
- Output is **structured findings with source URL, excerpt, retrieval time, source tier** — never prose-only.
- **AC:** each finding has URL + excerpt; sources are tiered; zero findings without a source.

## F5. Transaction Analyst (P0)
*Reference: procurement-anomaly-detection.*
- Deterministic: exact/near duplicate (vendor+amount+date window, invoice no. normalized, fuzzy line items), bank-account change vs vendor history, PO match (qty, price, total, date ordering), round-sum bias, new vendor + large amount, payment-terms anomaly, temporal anomaly (invoice before PO).
- Statistical: z-score of unit price vs vendor history, Isolation Forest on vendor-behaviour features once ≥N history rows.
- **AC:** each signal returns `{signal, fired, severity, value, baseline, evidence_ids}`.

## F6. Evidence Store & Verification Agent (P0)
*Reference: PROXY evidence scoring + citation verification; langgraph-multi-agent cite-or-refuse.*
- **Evidence is a first-class object everywhere.** Agents never return prose findings like `{"finding": "Vendor is suspicious"}`. Every agent output is a **Claim** with structured **Evidence**:
  ```json
  {
    "claim": "Vendor bank account differs from historical records",
    "evidence": [
      {"source": "invoice", "field": "bank_account", "value": "XXXX9812"},
      {"source": "vendor_history", "field": "bank_account", "value": "XXXX1234"}
    ],
    "confidence": 0.99
  }
  ```
  The verifier, not the agent, decides whether the claim is supported.
- Verification: (1) evidence exists, (2) source authenticity/tier, (3) claim actually entailed by evidence (deterministic string/number match first, LLM entailment second), (4) contradiction scan, (5) confidence.
- Status: `verified | refuted | unverified`. Only `verified` claims reach the risk engine at full weight; `unverified` are shown as "unconfirmed" with reduced/zero weight.
- **AC:** unverified claims contribute 0 points and cannot trigger HOLD on their own; refuted claims are discarded and logged.

## F7. Risk Engine — pure code (P0)
- **Not an AI agent.** Agents discover signals; code decides the score. No LLM output (free text, adjustment, or otherwise) is an input to the score. Only signals backed by `verified` claims count.
- Core weights (`w1`, configurable per tenant, sum = 100):
  | Signal | Points |
  |---|---|
  | `bank_account_changed` | +35 |
  | `price_anomaly` | +20 |
  | `new_domain` (unverified domain < 90 days old) | +15 |
  | `identity_mismatch` | +10 |
  | `duplicate_invoice` | +10 |
  | `address_mismatch` | +5 |
  | `missing_po` | +5 |
- Supplementary signals (added on top, total clamped to 100): `suspicious_instruction_in_document` +15, `prior_confirmed_issue` +15 (only from `CONFIRMED_ISSUE` outcomes), `quantity_po_mismatch` +10, `no_history` +5, statistical anomaly ≤10 (only with ≥30 history rows).
- Tiers: **LOW 0–29, MEDIUM 30–59, HIGH 60–79, CRITICAL 80–100.**
- A signal stops firing once its underlying fact is resolved in verified master data (e.g. approver adds the bank account as verified after out-of-band confirmation).
- **AC:** score fully reproducible from stored signals + weights version; `explain()` returns the contribution list; a test proves LLM output cannot change the score.

## F8. Human Decision Gate (P0)
*Reference: langgraph `interrupt` HITL.*
- Policy-driven: LOW + all checks complete → auto-clear (configurable); MEDIUM+ → human review; CRITICAL → hold + require approver role.
- Actions: Approve, Reject, Request Verification, Investigate Further, Add Note.
- **AC:** graph pauses via checkpointer; decision, actor, timestamp, and reason stored in immutable audit log.

## F9. Action Agent — Vendor Verification Workflow (P0 for demo)
- Generates email (templated + LLM-personalized, evidence attached, **no accusations**), sends via SMTP/Gmail API, creates follow-up timer, ingests reply, re-verifies, re-scores.
- **AC:** every outbound message requires human approval of the draft (toggle to auto for low-risk templates); reply analysis updates risk with traceable evidence. Reply claims about bank details or identity stay `unverified` (0 points) until an approver records an out-of-band confirmation (Guardrails G11).

## F10. Investigation Dashboard (P0)
- Case list, live agent timeline (SSE), score gauge, findings with +/- contributions, evidence drawer, "Why?" explainer, recommendation, action buttons. See UIUX.md.

## F11. Case Memory (P1)
*Reference: PROXY memory_service / Qdrant.*
- On case closure store structured outcome (vendor, issue, outcome, evidence IDs, resolution, confidence).
- On new invoice: retrieve similar past cases, surface "previously flagged" + adjust risk (`prior_confirmed_issue +N`).
- **AC:** only human-confirmed outcomes are written as "ground truth" memory. `CONFIRMED_ISSUE` adds `prior_confirmed_issue` points; `CLEARED` outcomes are shown as context only (e.g. "Previous investigation found a bank-account change for this vendor") and add 0 points.

## F12. Relationship Graph (P2)
- Postgres edge tables (vendor–bank, vendor–domain, vendor–address, vendor–phone, invoice–employee). Detect shared attributes across supposedly unrelated vendors.
- Optional Neo4j projection. Dashboard graph view.

## F13. Policy & Settings (P1)
- Auto-clear threshold, approver roles, amount limits requiring dual approval, weight overrides, allowed email domains.

## F14. Audit & Export (P1)
- Case PDF/JSON export with evidence and decision trail.

## F15. Benchmark Harness (P0 for judging)
- Synthetic dataset: clean + seeded-fraud invoices (bank swap, duplicate, price inflation, quantity mismatch, fake domain).
- Reports: investigation time, sources checked, precision/recall on seeded anomalies, human-intervention rate, false-clear count.
- **AC:** `make benchmark` outputs the comparison table used in the pitch.

## Phase mapping
| Phase | Features |
|---|---|
| 1 Core | F1, F2, F3 (internal only), F5, F7, F10 |
| 2 Investigation | F3 (external), F4, F6 |
| 3 Agentic action | F8, F9, F13 |
| 4 Memory | F11, F14; F12 stretch only |
| Cross-cutting | F15 |
