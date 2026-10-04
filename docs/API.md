# API Specification (v1)

Base URL: `/api/v1` · JSON · JWT bearer auth · every request carries `X-Request-ID` (generated if absent) · all resources are tenant-scoped (`workspace_id` derived from the token, never from the body).

## Conventions
- Errors: `{ "error": { "code": "string", "message": "string", "details": {}, "request_id": "..." } }`
- Pagination: `?limit=25&cursor=...` → `{ items, next_cursor }`
- Idempotency: `Idempotency-Key` header on POST create/action endpoints.
- Money: integer minor units + `currency` (`{"amount_minor": 48500000, "currency": "INR"}`).
- Timestamps: ISO-8601 UTC.
- Rate limits: 60 req/min/user default; 10 uploads/min.

## 1. Auth & workspace
| Method | Path | Description |
|---|---|---|
| GET | `/me` | Current user, role, workspace |
| GET | `/workspace/policy` | Risk policy (thresholds, weights, limits) |
| PUT | `/workspace/policy` | Update policy (role: owner) |

## 2. Documents & cases
| Method | Path | Description |
|---|---|---|
| POST | `/documents` | Multipart upload (pdf/png/jpg/eml, ≤15 MB). Returns `document_id`, `sha256`, `duplicate_of` if seen |
| GET | `/documents/{id}` | Metadata + signed URL |
| POST | `/cases` | Create case from `document_id` → starts investigation. Body: `{document_id, corrections?: {...}}`. Returns `201 {case_id, status:"QUEUED"}` |
| GET | `/cases` | List/filter: `tier, status, vendor_id, from, to` |
| GET | `/cases/{id}` | Full case: extraction, plan, score, findings, recommendation, state |
| GET | `/cases/{id}/events` | **SSE** stream of agent events (see §6) |
| GET | `/cases/{id}/evidence` | All evidence items with verification status |
| GET | `/cases/{id}/explain` | "Why?" – ordered claim→evidence→baseline/observed |
| GET | `/cases/{id}/audit` | Immutable audit trail |
| GET | `/cases/{id}/export?format=json|pdf` | Export case file |

### Case object (abridged)
```json
{
  "id": "case_1842",
  "status": "AWAITING_HUMAN",
  "invoice": {
    "invoice_number": {"value":"INV-4821","confidence":0.99,"evidence":"…"},
    "vendor_name": {"value":"ABC Supplies Pvt Ltd","confidence":0.97},
    "total": {"value":{"amount_minor":56640000,"currency":"INR"},"confidence":0.99},
    "bank_account": {"value":"XXXXXX9812","confidence":0.93},
    "line_items":[{"description":"Industrial Components","qty":500,"unit_price_minor":96000}]
  },
  "plan": {"case_type":"invoice","priority":"high","required_checks":["duplicate_detection","bank_account_verification","price_anomaly","vendor_identity","external_reputation"]},
  "risk": {
    "score": 70, "tier": "HIGH", "provisional": false, "weights_version": "w1",
    "contributions": [
      {"signal":"bank_account_changed","points":35,"claim_id":"clm_1"},
      {"signal":"price_anomaly","points":20,"claim_id":"clm_2"},
      {"signal":"new_domain","points":15,"claim_id":"clm_3"}
    ]
  },
  "recommendation": {"action":"HOLD_PAYMENT","summary":"3 verified anomalies identified.","requires_role":"approver"}
}
```

## 3. Decisions & actions
| Method | Path | Description |
|---|---|---|
| POST | `/cases/{id}/decision` | `{decision: "APPROVE"|"REJECT"|"REQUEST_VERIFICATION"|"INVESTIGATE_FURTHER", reason: string, draft_id?: string}` → resumes graph. 409 if case not `AWAITING_HUMAN` |
| POST | `/cases/{id}/notes` | Add reviewer note |
| POST | `/cases/{id}/corrections` | Correct extracted fields (pre-run or re-run) |
| GET | `/cases/{id}/drafts` | Drafted outbound messages |
| PATCH | `/cases/{id}/drafts/{draft_id}` | Edit draft |
| POST | `/cases/{id}/drafts/{draft_id}/send` | Approve + send (role-gated, audited) |
| POST | `/cases/{id}/vendor-reply` | Ingest reply (also called by inbound-mail webhook) → re-verify + re-score |
| POST | `/cases/{id}/out-of-band-confirmation` | Approver only. `{claim_ids, method: "phone_known_contact"|"bank_letter"|"in_person", note}` → marks the bank account/domain verified in the vendor master, flips the reply claims to `verified`, audits, then re-scores. Vendor replies alone never lower the score |
| POST | `/cases/{id}/rescore` | Recompute from stored signals (deterministic) |
| POST | `/cases/{id}/close` | `{outcome: "CONFIRMED_ISSUE"|"CLEARED"|"INCONCLUSIVE", resolution}` → writes case memory |

## 4. Vendors, memory, graph
| Method | Path | Description |
|---|---|---|
| GET/POST | `/vendors` | List / create vendor master |
| GET | `/vendors/{id}` | Profile: GSTIN, accounts (with first/last seen), domains, price history, prior cases |
| POST | `/vendors/{id}/bank-accounts` | Add verified account (role: approver) |
| GET | `/vendors/{id}/graph` | Nodes/edges within N hops |
| GET | `/graph/shared-attributes` | Vendors sharing bank/address/domain/phone |
| GET | `/memory/cases?q=` | Semantic search over closed cases |

## 5. Ops
| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness |
| GET | `/ready` | Postgres, Redis, Qdrant, LLM provider |
| GET | `/metrics` | Prometheus |
| GET | `/benchmark/summary` | Manual vs system metrics |
| POST | `/demo/seed` | Load demo invoices/vendors (non-prod only) |

## 6. SSE event contract
`GET /cases/{id}/events` → `text/event-stream`. Each event:
```json
{"seq":14,"ts":"2026-10-04T10:00:03Z","case_id":"case_1842","type":"agent.progress",
 "agent":"web_research","status":"running","message":"Checking domain registration…","data":{}}
```
Types: `case.created`, `plan.created`, `agent.started`, `agent.progress`, `agent.completed`, `agent.failed`, `agent.skipped`, `evidence.added`, `claim.verified`, `verification.confirmed_out_of_band`, `claim.refuted`, `risk.updated`, `gate.waiting`, `decision.recorded`, `action.sent`, `vendor.reply_received`, `case.closed`. Supports `Last-Event-ID` for resume. Never includes raw PII beyond what the viewer is permitted to see.

## 7. Status machine
`QUEUED → EXTRACTING → INVESTIGATING → VERIFYING → SCORING → (AUTO_CLEARED | AWAITING_HUMAN) → (APPROVED | REJECTED | AWAITING_VENDOR | INVESTIGATING) → CLOSED`. `FAILED_PARTIAL` is allowed (score returned with reduced confidence). Illegal transitions → 409.

## 8. Core schemas (Pydantic)
```python
class Evidence(BaseModel):
    id: str; case_id: str
    source: Literal["invoice","vendor_history","vendor_master","purchase_order","registry","web","domain","vendor_reply"]
    field: str | None          # structured fact, e.g. "bank_account"
    value: Any | None          # e.g. "XXXX9812" (masked per role)
    source_ref: str            # URL or record id
    excerpt: str | None        # verbatim text for web/registry/reply sources
    retrieved_at: datetime
    tier: Literal[1,2,3]       # authority
    content_hash: str

class Claim(BaseModel):
    id: str; case_id: str; agent: str
    statement: str
    signal: str | None
    evidence_ids: list[str]
    status: Literal["verified","refuted","unverified"]
    confidence: float          # 0..1
    verifier_notes: str | None

class RiskSignal(BaseModel):
    signal: str; fired: bool
    severity: Literal["info","warn","high"]
    points: int; value: Any | None; baseline: Any | None
    claim_id: str | None
```

## 9. Webhooks (outbound, optional)
`case.held`, `case.auto_cleared`, `case.decided` → HMAC-signed (`X-Signature`), retried with backoff.
