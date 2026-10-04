# Web checklist: real data only (for the FRONTEND session)

Context: the user approved converting Probity to **real data only**. FRONTEND removes the web demo items below;
IMPLEMENTATION (backend) does not edit `apps/web`. Branch: `real-data` (local; checkpoint `checkpoint/pre-real-data`). Do not push.

## Web items to remove or replace (`apps/web/src`)

1. **main.tsx**: delete `LocalApp` and the local-auth branch, so the app is Clerk only (`ClerkProvider` + `VITE_CLERK_PUBLISHABLE_KEY` from
   `apps/web/.env.local`, which the user supplies). Remove the Benchmark import, nav item and route, the "Offline mode" badge, the `WifiOff` icon
   and `offlineIntegrations`. Replace the badge with a small **"Live"** indicator from `/app/config.integrations`, with missing optional
   integrations in its tooltip.
2. **lib/config.ts**: use the new `AppConfig` shape (below). Drop `demo`, `benchmark`, `simulated_inbox`, `demo_login` and `offlineIntegrations`.
   `SAFE_CONFIG` must not contain demo flags.
3. **lib/api.ts**: remove `getLocalSession`, `setLocalSession`, the local-token path and the demo comment.
4. **lib/auth.tsx**: `mode` becomes `"clerk"` only.
5. **lib/launch.ts**: remove `launchDemoFile` (keep `launchCase` if it's used).
6. **pages/Login.tsx**: remove the demo-user picker, `/auth/demo-users`, `/auth/demo-login` and the "make seed" hint, leaving only Clerk
   `<SignIn/>` with sign-up enabled.
7. **pages/AuthHelp.tsx**: remove all demo-mode text.
8. **pages/NewCase.tsx**: remove the demo samples panel (`/demo/seed`, `launchDemoFile`).
9. **pages/CaseView.tsx**
   - Remove the "Simulated inbox" block (`/demo/vendor-reply` legit/spoof). Replace it with a **"Record vendor reply"** form
     (`from_email`, `subject`, `body`) → existing `POST /api/v1/cases/{id}/vendor-reply` (accountant+), for pasting a real reply.
   - Remove `OOB_EXAMPLE` ("Called R. Kulkarni…"), every "Demo: fill example" button and `CLOSE_EXAMPLE`. Placeholders must contain no
     names, numbers or vendors. The out-of-band note starts **empty and required**, plus a checkbox
     "I used a phone number/email already on file" → `known_channel` (user Phase 3).
   - Show check status `could_not_verify` in "Plan & checks" and the timeline as "Could not verify: <reason>"
     (event type `check.could_not_verify`).
10. **pages/Settings.tsx**: remove the demo speed block (`/demo/speed`, `demo_agent_delay_ms`).
11. **pages/Benchmark.tsx**: delete. No synthetic accuracy numbers anywhere in the UI.
12. **pages/Landing.tsx**: remove "Runs fully offline with demo data", the `#quick-start-offline-no-api-keys` link, the Benchmark link
    and any synthetic numbers.
13. **Empty dashboard**: show the onboarding checklist (`GET /workspace/onboarding`): add vendor, upload first invoice, invite teammate.
14. **Vendor detail** (Phase 3 is fine): a GST section with "Registry status: could not verify" and an optional manual-entry form
    (API below), labelled **"Entered manually by <name> · <date>"**. Never "verified".
15. **Rule-based fallback labels (user requirement: visible in the case view, not only logs).** Wherever a result carries a
    `fallback` object `{kind:"rule_based", label:"rule-based fallback, AI unavailable", reason}`, render a visible badge with
    `label` **next to that result** (tooltip = `reason`). Places:
    - case summary: `recommendation.summary_fallback` (also `recommendation.summary_source: "ai" | "engine"`)
    - checks: `checks.<name>.fallback` (e.g. `external_reputation` when search queries were written by rules)
    - extracted invoice fields: `invoice.<field>.fallback` (parser value the AI could not double-check)
    - vendor-reply statements: `claims[i].data.fallback`
    - drafts: `drafts[i].fallback` (standard neutral template used) — show above the draft body before "Send"
    - invoice-risk narrative: `narrative.fallback` (`GET /cases/{id}/invoice-risk?narrate=true`)
    Claims the AI could not check have `verifier_notes` starting "not checked — AI unavailable"; show that note on the claim.
16. **Done-check**: `grep -rniE "demo|mock|seed|offline|benchmark|kulkarni|abc supplies|probity-demo" apps/web/src` returns nothing.

## API changes this depends on (backend Phase 2)

- `GET /app/config` (public):
  `{env, version, auth:{mode:"clerk", sign_up:true}, features:{landing_page:true}, integrations:{ai:"live"|"missing",
  web_search:"live"|"missing", domain_lookup:"live", gst_registry:"unavailable", email:"live"|"missing", storage:"local"|"cloud",
  antivirus:"on"|"off", background_jobs:"inline"|"worker"}, limits:{…}}`.
  **Transition:** `auth.demo_login=false` and `features.demo/benchmark/simulated_inbox=false` are kept (always false) until FRONTEND confirms
  the UI no longer reads them, then they are deleted.
- `GET /auth/config` → `{mode:"clerk"}`.
- **Removed** (410 Gone now; routes deleted after FRONTEND confirms): `/auth/demo-users`, `/auth/demo-login`, `/demo/seed`,
  `/demo/files/{name}`, `/demo/vendor-reply/{id}`, `/demo/speed`, `/benchmark/summary`; policy key `demo_agent_delay_ms`.
- Check status values: `passed | fired | skipped` (not applicable) `| could_not_verify` (tool/data failure, has `reason`) `| failed`.
  New SSE event `check.could_not_verify` `{agent, message, data:{check, reason}}`. A required check that could not be verified **holds** the
  case; `recommendation.gate.reasons` includes "Could not verify: <check> — <reason>".
- Web research failures report `could_not_verify`. "No adverse public findings" appears only if at least one search actually ran.
- GST: `checks.gst_registry` is always `{status:"could_not_verify", reason:"No GST registry provider is configured"}`.
  `PUT /vendors/{id}/gst-manual {legal_name, status:"Active"|"Cancelled"|"Suspended", note}` (accountant+), `DELETE` clears it.
  `GET /vendors/{id}` gains `gst: {gstin_format, registry_status:"could_not_verify", registry_reason}` and
  `gst_manual: {gstin, legal_name, status, note, entered_by:{id,name}, entered_at, stale, source:"manual", label} | null` (render `label`).
- Email send (`POST …/drafts/{id}/send`) may return 400 "Email isn't configured…" or "Blocked: <addr> isn't on EMAIL_ALLOWLIST…".
  Show the message as-is.

**410 → delete protocol (done 2026-10-04: routes and transitional keys deleted):** the removed endpoints keep answering 410 only until the UI stops calling them. When your changes stop
calling them, message IMPLEMENTATION with the list; IMPLEMENTATION then deletes the routes and the transitional always-false config
keys, and you re-run the done-check. Never call a removed endpoint from new code.

When done, tell IMPLEMENTATION: (a) the UI no longer calls any removed endpoint, so the routes can be deleted, and (b) any field you need shaped differently.
