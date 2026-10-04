# UI / UX Specification

**Design principle:** a judge (or approver) must understand *what happened, how risky it is, why, and what to do* within 5 seconds. The UI sells "investigation," not "chatbot."

## 1. Information architecture
```
/login
/dashboard            Case queue + KPIs
/cases/new            Upload invoice
/cases/:id            Investigation workspace (primary screen)
/vendors              Vendor directory
/vendors/:id          Vendor profile, history, graph, prior cases
/memory               Past cases (searchable)
/settings/policy      Thresholds, weights, roles
/benchmark            Manual vs system metrics
```

## 2. Primary screen — Investigation Workspace (`/cases/:id`)
```
┌──────────────────────────────────────────────────────────────┐
│ CASE #1842 · INV-4821 · ABC Supplies · ₹5,66,400             │
│                                  [RISK GAUGE 70 · HIGH]      │
├───────────────┬──────────────────────────┬───────────────────┤
│ AGENT TIMELINE│ FINDINGS                 │ RECOMMENDATION    │
│ ✓ Document    │ 🔴 Bank changed    +35   │ HOLD PAYMENT      │
│ ✓ Vendor      │ 🔴 Price +62.7%    +20   │ [Approve]         │
│ ◐ Web (3/6)   │ 🟡 Domain 21d old  +15   │ [Reject]          │
│ ✓ Transaction │ 🟢 Invoice no. new   0   │ [Request Verif.]  │
│ ◌ Verification│ [Why?]                   │ [Investigate+]    │
├───────────────┴──────────────────────────┴───────────────────┤
│ TABS: Evidence · Document (PDF + highlights) · Graph · Audit │
└──────────────────────────────────────────────────────────────┘
```
### Components
- **Risk Gauge** — animated 0–100 ring; color by tier (LOW green, MEDIUM amber, HIGH red, CRITICAL deep red). Shows "Provisional" until verification completes.
- **Agent Timeline** — live via SSE. States: queued, running (spinner + current action text, e.g. "Checking domain registration…"), done, failed (retry button), skipped (with reason).
- **Findings list** — each row: severity chip, title, score contribution, verification badge (`Verified` / `Unconfirmed`), historical vs current value (e.g. `XXXX1234 → XXXX9812`), expand for evidence.
- **"Why?" panel** — numbered explanation: claim → evidence → baseline vs observed (e.g. `₹590 → ₹960, +62.7%`).
- **Evidence drawer** — source type icon (internal record / registry / web / domain), URL or record ID, excerpt, retrieved-at, tier, confidence.
- **Document viewer** — PDF with field highlights; click a field to see confidence + snippet; low-confidence fields flagged for correction.
- **Decision bar** — sticky; buttons disabled with tooltip when the user lacks role; confirm modal requires a reason for Approve on HIGH/CRITICAL.
- **Re-score animation** — after the approver confirms the vendor reply out-of-band, gauge animates 70 → 20 with a diff of changed findings.
- **Graph tab** — force-directed graph (vendor, bank, domain, address, invoices); shared nodes highlighted red.

## 3. Dashboard (`/dashboard`)
- KPI strip: invoices processed, auto-cleared %, avg investigation time, held ₹ amount, open reviews.
- Queue table: risk tier, vendor, amount, age, status, assignee; filters (tier/status/vendor); sort by risk.
- Empty state with "Upload invoice" and "Load demo data" buttons.

## 4. Upload flow (`/cases/new`)
Drag-drop → instant checksum duplicate notice → extraction preview with confidence → "Start investigation". Low-confidence fields editable before launch (human correction feeds back).

## 5. Request Verification flow
Modal shows drafted email (editable), evidence attachments checklist, follow-up timer, recipient (from *verified* vendor master contact, **not** from the invoice, with warning if only invoice contact exists). Send → case state `AWAITING_VENDOR`. When the reply arrives its claims show as *Unverified — awaiting approver* with a **Confirm out-of-band** button (approver only; records method + note). The score changes only after that confirmation.

## 6. Language & tone rules
- Use "anomaly", "risk", "unconfirmed", "recommend hold". **Never** "fraud detected", "scammer", "fake vendor" as system statements.
- Always show what is verified vs unconfirmed.
- Money formatted in Indian grouping (₹4,85,000); dates ISO + relative.

## 7. States
Loading skeletons, partial results streaming, failed-agent banner ("Web research unavailable — score computed without it; confidence reduced"), offline/fallback demo mode badge.

## 8. Accessibility & responsiveness
WCAG AA contrast; color never sole indicator (icons + text); keyboard-navigable decision bar; responsive down to tablet; mobile read-only approval view.

## 9. Visual style
Clean, dense-but-calm fintech look: neutral background, one accent color, tier colors reserved for risk. Light/dark. Font: Inter. Components: shadcn/ui + Tailwind; charts: Recharts; graph: react-force-graph or Cytoscape.

## 10. Demo-mode requirements
- "Demo" toggle seeds vendor history, `invoice_4821.pdf` and a simulated vendor inbox. The stage demo is **one case end to end** (PRD.md §7), not a feature tour.
- Speed control for agent animation so the timeline is readable on stage.
