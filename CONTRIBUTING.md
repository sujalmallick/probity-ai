# Contributing to Probity

Thanks for helping! Probity is a student hackathon project, so first-time contributors are very welcome. Small, careful changes are
better than big ones.

**Be kind.** Be respectful in issues, reviews and chats, assume good intent, and help newcomers. We follow the spirit of the
[Contributor Covenant](https://www.contributor-covenant.org/). If someone makes you uncomfortable, tell the maintainer (@sujalmallick)
privately.

## 1. Get set up

Follow [docs/SETUP.md](docs/SETUP.md) and [docs/API_KEYS.md](docs/API_KEYS.md). Use **your own** AI key (Anthropic or Gemini) and **your own** Clerk
development app. Never borrow someone else's.

You don't need any keys to run the backend tests; they only need the PostgreSQL container.

## 2. Find something to work on

- The **"Not built yet / ideas"** list at the end of [docs/FEATURES.md](docs/FEATURES.md).
- The **starter tasks** at the bottom of this page.
- GitHub Issues labelled `good first issue`, `help wanted`, `frontend`, `backend` or `docs`.

Before you start, **comment on the issue** (or open one) so two people don't do the same thing. If you're unsure about an approach, ask
in the issue first.

## 3. Workflow

1. **Fork** the repo (or, if you're a collaborator, create a branch in it).
2. Create a branch from `main` with a clear name:
   - `feature/notifications-bell`
   - `fix/memory-page-error-state`
   - `docs/setup-macos-notes`
3. Make **small commits with clear messages** that say what changed and why, e.g. `Show an error with Retry when the Memory page fails to load`.
4. Push your branch and open a **pull request to `main`**. Fill in the template and link the issue (`Closes #12`).
5. Keep each PR focused on one thing. Several small PRs are easier to review than one big one.

## 4. Checks to run before opening a PR

**Backend** (PostgreSQL container running, virtual environment active, from `apps/api`):

```powershell
python -m pytest -q
```

**Safety tests.** These are already part of the full run above. Run them on their own when you touch agents, the risk engine, email,
uploads or anything security-related:

```powershell
python -m pytest tests/test_safety_eval.py tests/test_could_not_verify.py tests/test_secret_leaks.py tests/test_masking.py tests/test_autoclear_gate.py -q
```

**Frontend** (from `apps/web`):

```powershell
npm run typecheck
npm run build
```

There's no frontend linter or test runner yet. Adding one would be a welcome contribution.

**Before you push**, read your own diff (`git diff main...HEAD`) and make sure there are no keys, `.env` contents, real invoices or
personal data in it.

GitHub runs the backend tests and the frontend type check and build on every pull request.

## 5. Rules for changes

These protect the people who will trust Probity with their payments. PRs that break them can't be merged.

- **No secrets.** Never commit API keys, tokens, passwords or `.env` files. Use placeholders like `sk-ant-xxxxxxxx` in docs and examples.
- **No real invoices or personal data in the repo.** That covers real company invoices, bank account numbers, real people's names,
  emails, phone numbers or GSTINs. Use the test factories (below) and clearly fake values (`example.com` addresses).
- **Add or update tests** for every behaviour change, especially for a bug fix (a test that failed before your fix).
- **Wording:** say "anomaly", "risk indicator", "unconfirmed" and "recommend hold". Never call something "fraud", a "scam" or a "fake"
  vendor, in code, UI text, emails or docs.
- **The risk engine takes no AI input.** `risk/engine.py` only sees verified claims and their weights. Don't add any path for text,
  prompts or AI output to change a score or a tier.
- **Never invent results.** When a check can't run, it must say **"could not verify"** with a reason, add no points, and hold the
  invoice if the check is required. AI fallbacks must be labelled "rule-based fallback, AI unavailable".
- **Evidence or nothing.** A new signal must produce claims with structured evidence that the verifier can check.
- **New database migrations must be reversible.** Write a real `downgrade()` and test both directions on your local database.
- **The system never pays anything**, and only humans approve or reject.

### Test data

- Test data is built **in code** by `apps/api/tests/factories/` (workspaces, users, vendors, history, POs, invoice PDFs). Add new
  builders there.
- **No demo, mock or seed data in app code.** There's no demo mode and no fake AI switch, and tests fail if one is added. Fakes for the AI,
  Clerk, web search and email are injected only by `apps/api/tests/conftest.py`.

## 6. Keep your email private in commits

Your commit email is public on GitHub. To keep your personal email out of the history:

1. On GitHub, go to **Settings → Emails**. Turn on **Keep my email addresses private** and **Block command line pushes that expose my email**.
2. Copy your noreply address (it looks like `12345678+yourname@users.noreply.github.com`) and use it in this repo:

```powershell
git config user.email "12345678+yourname@users.noreply.github.com"
```

## 7. Reviews

- The maintainer (@sujalmallick) reviews every PR. Expect questions. They're about the code, not about you.
- A good PR description says:
  - **what** changed
  - **why** (link the issue)
  - **how you tested** it (commands you ran, and the test you added)
  - **screenshots** for any UI change, before and after
  - anything you're **unsure about**
- Reply to comments with a new commit, not a force-push, so reviewers can see what changed.
- Once the checks pass and the review is approved, the maintainer merges it.

## 8. Security problems

Please **don't open a public issue** for a security problem. See [SECURITY.md](SECURITY.md) for how to report it privately.

---

## Starter tasks

Small, well-scoped tasks from known gaps (mostly from [docs/FAILURE_AUDIT.md](docs/FAILURE_AUDIT.md)). Each lists the files involved.
Open an issue before you start.

1. **Fix the upload hint** *(frontend, good first issue)*. The New case page says "PDF, image or email file, up to 15 MB" and its file
   picker accepts images, but the server refuses images and the limit is 10 MB. Fix the text and the accepted types, and read the limit
   from `limits.max_upload_mb` in `/api/v1/app/config`.
   Files: `apps/web/src/pages/NewCase.tsx`, `apps/web/src/lib/config.ts`.
2. **Memory page: error and "no matches" states** *(frontend)*. The page shows no error if loading fails, and says "No closed cases yet"
   even when a search simply found nothing. Copy the pattern from the Vendors page ("No vendors match" vs "No vendors yet").
   Files: `apps/web/src/pages/Memory.tsx` (pattern in `apps/web/src/pages/Vendors.tsx`).
3. **Settings and Team pages: error states with Retry** *(frontend)*. Settings renders blank and Team spins forever if loading fails.
   Files: `apps/web/src/pages/Settings.tsx`, `apps/web/src/pages/Team.tsx`, `Spinner`/`Empty` in `apps/web/src/components/ui.tsx`.
4. **Add a global error boundary** *(frontend)*. A crash in any component currently shows a white page. Show a friendly message with a
   reload button instead.
   Files: `apps/web/src/main.tsx`, a new component in `apps/web/src/components/`.
5. **Stop double-clicks on "Archive vendor"** *(frontend)*. Disable the button while the request is in flight.
   File: `apps/web/src/pages/Vendors.tsx`.
6. **Add `apps/web/.env.example`** *(docs/setup, good first issue)*. Setup currently asks people to create `.env.local` by hand. Add an
   example file with `VITE_CLERK_PUBLISHABLE_KEY=pk_test_xxxxxxxx`, then update `docs/SETUP.md` step 4.
   Files: `apps/web/.env.example`, `docs/SETUP.md`.
7. **CSV import: bad cells should be row errors, not a server error** *(backend)*. A CSV with an enormous field or a value like `inf`
   causes a 500. It should show up as an error on that row in the import report. Add a test.
   Files: `apps/api/src/probity/importer.py`, `apps/api/src/probity/api/workspace.py`, a new test in `apps/api/tests/`.
8. **Refuse empty (0-byte) uploads with a clear message** *(backend)*. An empty file is currently accepted as text. Add a test.
   Files: `apps/api/src/probity/ingestion/parse.py` (`sniff_mime`), `apps/api/tests/test_ingestion_hardening.py`.
9. **Bad `Last-Event-ID` header shouldn't cause a 500** *(backend)*. The live-updates endpoint does `int(...)` on the header. Treat
   non-numbers as 0 and add a test.
   File: `apps/api/src/probity/api/main.py` (the `/cases/{case_id}/events` route).
10. **Doc fixes** *(docs)*. `docs/Guardrails.md` mentions a `make safety-eval` target that doesn't exist (the safety cases are in
    `apps/api/tests/test_safety_eval.py`). `docs/API.md`, `docs/PRD.md`, `docs/Feature.md`, `docs/TechStack.md` and `docs/UIUX.md`
    still describe the old demo/mock mode. Update or clearly mark those parts as historical.
