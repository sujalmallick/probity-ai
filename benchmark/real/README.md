# Real-invoice harness

Run Probity on **your own** invoices and compare its decisions with what you know the right answer was. There is no
synthetic data here and no stored accuracy number: every figure in a report comes from your files and your labels.

## Set up (once)

1. Start Probity normally (`scripts\dev.ps1`), sign in, and in the workspace you'll test with:
   add your vendors, verify their bank accounts / domains / contacts, import or enter past invoices and POs, and have
   an approver approve them (only approved records are compared against).
2. Find the workspace id: open `http://127.0.0.1:8010/api/v1/me` while signed in, or Settings → Workspace.
   Tip: use a separate workspace for testing (sign in with a second account) so test cases don't mix with real work.

## Add invoices and labels (both git-ignored)

- Put PDFs (with selectable text) or `.eml` files in `benchmark/real/invoices/`.
- Copy `labels.template.csv` to `labels.csv` and add one line per file:

| column | meaning |
|---|---|
| `file` | the file name in `invoices/` |
| `expected` | `hold` if a person should have looked at it, `clear` if it was a normal invoice that should pass |
| `expected_signals` | optional, `;`-separated: `bank_account_changed`, `price_anomaly`, `new_domain`, `identity_mismatch`, `duplicate_invoice`, `address_mismatch`, `missing_po`, `quantity_po_mismatch`, `temporal_anomaly`, `suspicious_instruction_in_document`, `prior_confirmed_issue`, `shared_attribute`, `no_history` |
| `notes` | anything that helps you read the report later |

## Run

```
cd apps/api
../../.venv/Scripts/python.exe ../../benchmark/real/run_real.py --workspace <ws_id> --as you@company.com
```

Each invoice is investigated exactly like an upload in the app: the same live AI, web search, RDAP, limits and gate.
Files that already have a case in the workspace are not re-run (their existing result is reported). The daily
investigation limit applies. The report is written to `benchmark/real/reports/<timestamp>.md` (and `.json`).

## Reading the report

- **Missed hold** (you said `hold`, Probity cleared it) is the number that matters most; the script exits with code 1
  when there is one.
- **Extra review** (you said `clear`, Probity held it) costs time, not money. Check the *could not verify* column:
  holds caused by missing data (no approved history, an unverified bank account, a PO waiting for approval) go away
  when that data is added, not by changing Probity.
- Failed and rejected files (scans, images, oversized) are listed but not judged.
- With a few dozen invoices, treat the counts as anecdotes, not rates.
