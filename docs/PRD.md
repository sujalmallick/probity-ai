# PRD — Probity: AI Business Investigation Agent

> Name: **Probity** — *Evidence before payment.* One-line pitch: *an AI investigation team for small-business payments. It researches each invoice, cross-checks internal and external evidence, explains its reasoning, and asks a human only when risk warrants it.*

## 1. Problem
Small businesses receive invoices, vendor requests and payment requests daily. They lack fraud/risk teams. An accountant manually checks vendor identity, GSTIN, bank account changes, duplicates, price/quantity vs PO, domain legitimacy, and public complaints. That takes ~20–30 min per invoice, checks only 2–4 sources, and is inconsistent.

**Problem statement:** Small businesses struggle to detect fraudulent, duplicated, manipulated or suspicious vendor invoices *before payment*.

## 2. Positioning (non-negotiable wording)
- We **identify anomalies and investigate vendor/payment risk, producing evidence-backed recommendations for human review.**
- We never claim "our AI detects fraud." The AI is an investigator, not a judge.
- Every user-facing risk statement must trace to a verified evidence item.
- Judging line: **"The LLM can investigate, but it cannot manipulate the risk score."** Every AI conclusion has to earn its way into the score through evidence and verification.

## 3. Target users
| Persona | Need |
|---|---|
| Owner / Finance lead (SMB, India-first) | Stop wrong payments without hiring analysts |
| Accountant / AP clerk | Fast, explainable triage; fewer manual lookups |
| Approver (owner/CFO) | One screen: risk, reasons, evidence, action |

## 4. Goals & success metrics
| Metric | Manual baseline | Target |
|---|---|---|
| Investigation time / invoice | 20–30 min | ≤ 3 min |
| Sources checked | 2–4 | 10+ |
| Duplicate detection | manual | automatic, 100% on exact/near dup in test set |
| Human intervention rate (low-risk invoices) | 100% | ≤ 20% (i.e. ≥80% auto-cleared) |
| Evidence coverage | none | ≥95% of risk statements carry verified evidence |
| False-clear rate on seeded fraud set | n/a | 0 high-risk cases auto-cleared |

**North-star metric:** human-intervention rate on low-risk invoices, with zero false-clears on the seeded set.

## 5. Scope
### MVP (hackathon)
1. Upload PDF/image invoice → structured extraction with per-field confidence.
2. Vendor Investigator + Web Research with evidence items.
3. Transaction Analyst (duplicate, price, quantity, bank, temporal checks) against internal history.
4. Evidence Verification Agent (claim → evidence → verified/refuted/unverified).
5. Deterministic risk engine — **pure code, no LLM input** (0–100, explainable contributions). Agents discover signals; code decides the score.
6. Human Decision Gate: Approve / Reject / Request Verification / Investigate Further.
7. Live investigation dashboard with agent activity stream.
8. Action: generate + send vendor verification email, track reply, re-score.
9. Case memory (confirmed outcomes reused on next invoice from same vendor).

### Stretch (only if MVP is done)
Relationship graph on Postgres edge tables (shared bank/address/domain across vendors) — Feature F12, P2.

### Post-MVP
Neo4j graph projection, ML anomaly layer retraining, purchase-order/contract anomalies, expense fraud, ERP/Tally/Zoho connectors, payment-rail integration.

### Out of scope
Executing payments, legal conclusions, accusing named entities publicly, KYC/AML compliance certification.

## 6. Key user stories
1. As an accountant, I upload an invoice and see a risk verdict with reasons in under 3 minutes.
2. As an approver, I see *why* (evidence links), not just a score.
3. As an owner, low-risk invoices clear automatically per my policy; high-risk ones are held.
4. As an approver, I click "Request Verification" and the system emails the vendor and re-scores on reply.
5. As a user, when a vendor was previously flagged, the system tells me and weights it.

## 7. Demo script (acceptance narrative)
The demo is **one complete case**, not a tour of features. Invoice: `invoice_4821.pdf`, ABC Supplies, 500 × ₹960 = ₹4,80,000 + 18% GST ₹86,400 = **₹5,66,400**.

1. **Upload** `invoice_4821.pdf`.
2. **Live investigation** (agent timeline): ✓ Invoice extracted · ✓ Vendor identified · ✓ Historical invoices searched · ✓ External sources searched · ✓ Transaction analyzed · ✓ Evidence verified.
3. **Result:** 🔴 **70 / 100 — HIGH**, then reveal **3 verified anomalies**:
   | Anomaly | Evidence | Points |
   |---|---|---|
   | Bank account changed | Historical `XXXX1234` → current `XXXX9812` | +35 |
   | Price anomaly | Historical average ₹590 → current ₹960 (+62.7%) | +20 |
   | Domain age | Sender domain registered 21 days ago | +15 |
4. **Explainability:** click each anomaly → its evidence items (source, field, value, retrieved-at).
5. **Human gate:** approver clicks **Request Vendor Verification**.
6. **Action:** agent drafts a neutral email to the vendor's *verified master contact*; approver approves; it is sent.
7. **Resolution:** vendor reply arrives → agent extracts its claims (shown as *Unverified — awaiting approver*, no score change) → approver clicks **Confirm out-of-band** (called the known contact) → bank account and domain marked verified → re-score animates **70 → 20 LOW** (bank −35, domain −15). The price finding (+20) stays visible for review.
8. **Memory:** approver closes the case as `CLEARED`. On the next invoice from ABC Supplies: *"Previous investigation found a bank-account change for this vendor (verified out-of-band on <date>)."*

The clean-invoice auto-clear path is demonstrated through `/benchmark` (≥80% of clean invoices auto-cleared), not on stage.

## 8. Risks
| Risk | Mitigation |
|---|---|
| LLM hallucinated claims | Claim→evidence verification; deterministic checks first; cite-or-drop (see Guardrails.md) |
| Web results unreliable / defamatory | Source tiering; only report verifiable facts; never publish accusations |
| Overclaiming "fraud detection" | Wording rules in Guardrails.md |
| Data privacy (bank/GST data) | Encryption, redaction before LLM, tenant isolation (Security.md) |
| Demo flakiness from live web | Cached fixtures + recorded fallback mode |

## 9. Milestones
Phase 1 Core → Phase 2 Investigation → Phase 3 Agentic action → Phase 4 Memory/Graph. See Feature.md for the breakdown.
