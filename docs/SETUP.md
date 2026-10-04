# Setting up Probity from zero

This guide takes you from an empty computer to Probity running on http://localhost:5180. Commands are for **Windows PowerShell** first.
Where macOS/Linux differ, there's a second block.

Expect about 30 to 45 minutes the first time, most of it waiting for downloads and creating accounts.

**What you'll end up with:**

| Piece | Address |
|---|---|
| Web app | http://localhost:5180 |
| API | http://127.0.0.1:8010 (interactive docs at http://127.0.0.1:8010/docs) |
| PostgreSQL (in Docker) | 127.0.0.1:5434 |

---

## 1. Install the prerequisites

| Tool | Version | Check it's installed |
|---|---|---|
| Git | any recent | `git --version` |
| Python | **3.12 or newer** | `python --version` (macOS/Linux: `python3 --version`) |
| Node.js | **22 LTS** recommended (CI uses 22) | `node --version` and `npm --version` |
| Docker Desktop | any recent, **running** | `docker --version` and `docker info` (must not show an error) |

- **Windows:** install from python.org (tick "Add python.exe to PATH"), nodejs.org (LTS), docker.com (Docker Desktop) and git-scm.com.
  Open a **new** PowerShell window afterwards so the tools are found.
- **macOS:** `brew install python@3.12 node@22 git`, plus Docker Desktop from docker.com.
- **Linux:** use your package manager for Python 3.12+, Node 22 and Git, and install Docker Engine with the Compose plugin.

Before you start, also create the accounts you'll need. See [API_KEYS.md](API_KEYS.md); the minimum is **Anthropic** and **Clerk**.

## 2. Get the code

```powershell
git clone https://github.com/sujalmallick/probity-ai.git
cd probity-ai
```

If you plan to contribute, fork the repo on GitHub first and clone your fork (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

> **Fastest path on Windows:** `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1` does steps 3 to 7 for you. On the first run it
> stops and tells you which keys are missing. Fill them in (steps 4 and 5) and run it again. The manual steps below explain what it does,
> and are what you follow on macOS/Linux.

## 3. Start PostgreSQL

Make sure Docker Desktop is running (the whale icon is steady, not animating). Then:

```powershell
docker compose -f infra/docker-compose.dev.yml up -d --wait
```

This starts PostgreSQL 16 on **127.0.0.1:5434**. Port 5434 is used so it won't clash with a PostgreSQL you may already have. It also
creates two database users: `probity` (schema owner) and `probity_app` (what the app uses). Their passwords are `probity` /
`probity_app`, **for your own computer only**.

Check it's healthy:

```powershell
docker compose -f infra/docker-compose.dev.yml ps
```

The `postgres` row should say `healthy`. Your data lives in a Docker volume, so it survives restarts.

- Stop it: `docker compose -f infra/docker-compose.dev.yml down`
- Delete all data: add `-v` to that command.

## 4. Create the Python environment and the settings files

From the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e "apps/api[dev,worker]"
cd apps/web
npm install
cd ../..
```

macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e "apps/api[dev,worker]"
(cd apps/web && npm install)
```

(`make install` does the same on macOS/Linux.)

**Activate the environment** so `python` means the project's Python. Do this in every new terminal:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell says running scripts is disabled, run `Set-ExecutionPolicy -Scope Process Bypass` first. That only affects the current
window. On macOS/Linux: `source .venv/bin/activate`.

**The web settings file.** There's no example file for it yet, so create `apps/web/.env.local` containing one line:

```dotenv
VITE_CLERK_PUBLISHABLE_KEY=pk_test_xxxxxxxx
```

Without it the web app still opens, but sign-in shows as unavailable.

## 5. Generate the app secrets and fill in your keys

```powershell
cd apps/api
python -m probity.bootstrap --generate-secrets
```

This creates `apps/api/.env` from `apps/api/.env.example` and fills `FIELD_KEY_B64` and `HMAC_KEY` with random values. **It never prints
them**, and it never overwrites values that are already set.

Now open `apps/api/.env` in your editor and set:

```dotenv
# Database: the local Docker defaults from step 3
DATABASE_URL=postgresql+psycopg://probity_app:probity_app@127.0.0.1:5434/probity
DATABASE_MIGRATE_URL=postgresql+psycopg://probity:probity@127.0.0.1:5434/probity

# Your own keys (see docs/API_KEYS.md)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
CLERK_ISSUER=https://your-app-name.clerk.accounts.dev
CLERK_SECRET_KEY=sk_test_xxxxxxxx
CLERK_AUTHORIZED_PARTIES=http://localhost:5180
```

Leave the rest as they are for now. Tavily and email are optional.

## 6. Create the database schema

Still in `apps/api`:

```powershell
python -m probity.bootstrap
```

It prints a checklist of what's configured, then creates the tables. Expect output like this:

```
Probity configuration (apps/api/.env):
  OK  database ...
  OK  ai ...
  -- web_search ...
  ...
database schema created (revision 00xx)
All required settings are present. Start the API: ...
```

- `--` lines are **optional** things that aren't set up. That's fine.
- `!!` lines under "Missing required settings" must be fixed before the API will start.
- This creates an **empty** schema. There's no sample data. Every vendor, invoice and user comes from you.

Run `python -m probity.bootstrap` again whenever you pull new code: it applies new migrations. To wipe your local database and start
over: `python -m probity.bootstrap --reset --yes`. This deletes everything.

To test each integration with one small real request (secrets are never printed):

```powershell
python -m probity.check
```

## 7. Start the API and the web app

You need two terminals, both with the virtual environment active.

**Terminal 1: API** (from `apps/api`):

```powershell
python -m uvicorn probity.api.main:app --host 127.0.0.1 --port 8010 --reload
```

Wait for `Application startup complete`. Check http://127.0.0.1:8010/api/v1/ready: it should show `"ok": true`.

**Terminal 2: web app** (from `apps/web`):

```powershell
npm run dev
```

Open **http://localhost:5180**. The web dev server forwards `/api` calls to the API on port 8010.

On macOS/Linux, `make dev` starts both at once (and `make api` / `make web` start them one at a time).

## 8. First run: sign up and investigate your first invoice

1. Open http://localhost:5180 and **sign up** with your email and a password. Clerk sends a code to verify your email.
2. Your first sign-in creates **your own workspace with you as owner**. Your friends get their own workspaces when they sign up, unless
   you invite them from the **Team** page.
3. **What a first run looks like (this is normal, not broken):**
   - The dashboard is empty and shows an onboarding checklist.
   - There are no vendors, no history and no cases.
4. **Add a vendor** (Vendors → New vendor): name, and GSTIN if you have it. Add their bank account and contact. To mark a bank account
   **verified**, you need approver or owner rights and a note on how you checked it (e.g. "confirmed by phone with known contact").
5. **Add past invoices** for that vendor. Either import a CSV (Import page, download the template), or skip this for now.
6. **Upload an invoice** (New case): a text-based PDF, `.eml` or text file. Scanned PDFs and photos are refused (OCR isn't
   supported yet). Check the preview, then start the investigation and watch the timeline.

**On a new workspace, expect "could not verify" on several checks.** For example:
- "bank account verification: no bank history for vendor"
- "price comparison: not enough history"
- "external reputation: web search not configured" (if you skipped Tavily)
- "GST registry: could not verify" (always; there's no free registry API)

Because of this, the first invoices will be **held for a person** rather than auto-cleared. That's by design: Probity never treats
"we couldn't check" as "it's fine". As you add verified accounts and approved history, more checks can run.

## 9. Run the tests

**Backend.** The tests need the PostgreSQL container from step 3 running. They create their own throwaway database, fake every outside
service (AI, Clerk, web search, email), and **never use your keys or your `.env`**.

```powershell
cd apps/api
python -m pytest -q
```

Run a single file, a single test, or tests matching a word:

```powershell
python -m pytest tests/test_could_not_verify.py -q
python -m pytest tests/test_auth.py::<test_name> -q
python -m pytest -k "allowlist" -q
```

If your PostgreSQL isn't at the default place, set `TEST_POSTGRES_ADMIN_URL` (default:
`postgresql+psycopg://probity:probity@127.0.0.1:5434/postgres`).

**Frontend.** There are no frontend unit tests or linter yet. The checks are the type-checker and the build:

```powershell
cd apps/web
npm run typecheck
npm run build
```

On macOS/Linux, `make test` runs the backend tests and the frontend type check.

## 10. Troubleshooting

| Problem | What you see | Fix |
|---|---|---|
| Docker isn't running | `error during connect` / `Cannot connect to the Docker daemon` / dev.ps1 says "Docker Desktop is not running" | Start Docker Desktop, wait until it's ready, try again. |
| Port already in use | `address already in use` / `bind: Only one usage of each socket address` for 5434, 8010 or 5180 | Something else uses that port. Find it: `netstat -ano \| findstr :8010` (macOS/Linux: `lsof -i :8010`), then stop that program or the old Probity window. |
| API won't start | `Probity cannot start - fix these in apps/api/.env:` followed by a list | Fix each listed item (see the table below), then restart the API. |
| Database not migrated | `The database schema is not up to date (empty database). Run: python -m probity.bootstrap` | Run `python -m probity.bootstrap` from `apps/api`. Also after every `git pull` that adds migrations. |
| Can't reach PostgreSQL | `connection refused` / `could not connect to server` on 5434 | Start the container (step 3). Use `127.0.0.1`, not `localhost`, in the database URLs. |
| `python` not found or wrong version | `'python' is not recognized` or a 3.11 version | Install Python 3.12+, open a new terminal, use `.\.venv\Scripts\python.exe` directly or activate the venv. |
| `No module named probity` | When running `python -m probity...` | Activate the venv (step 4) and run from `apps/api`. Re-run `pip install -e "apps/api[dev,worker]"` from the repo root if needed. |
| Sign-in page says sign-in is unavailable | Web shows a "sign-in unavailable" page | `apps/web/.env.local` is missing or has no `VITE_CLERK_PUBLISHABLE_KEY`. Add it and **restart** `npm run dev`. |
| Signed in, but every request fails with 401 | "token issued for an unauthorized origin" in the API log | Add the exact web address you're using (e.g. `http://localhost:5180`) to `CLERK_AUTHORIZED_PARTIES`, restart the API. `localhost` and `127.0.0.1` count as different addresses. |
| Clerk keys from different apps | Sign-in works in the browser but the API rejects it | The publishable key, secret key and issuer must all come from the **same** Clerk application. |
| Bad or empty Anthropic key | `python -m probity.check ai` fails; case timelines show "rule-based fallback, AI unavailable" | Create a new key, check billing and the spend limit in the Anthropic Console, update `ANTHROPIC_API_KEY`, restart the API. |
| Upload refused | "This looks like a scanned or image-only invoice. Probity can't read scans yet…" or a size error | Use a PDF with selectable text, under 10 MB. Scans and photos aren't supported yet. |
| Tests stop immediately | `Tests need PostgreSQL. Start it (docker compose -f infra/docker-compose.dev.yml up -d) or set TEST_POSTGRES_ADMIN_URL.` | Start the container (step 3). |
| PowerShell won't run scripts | `running scripts is disabled on this system` | `Set-ExecutionPolicy -Scope Process Bypass`, or run dev.ps1 with `powershell -ExecutionPolicy Bypass -File scripts\dev.ps1`. |

### Startup messages and what they mean

When the API (or `python -m probity.bootstrap`) starts, it checks `apps/api/.env`. If something required is wrong, it refuses to start
and lists the problems:

| Message | Meaning and fix |
|---|---|
| `DATABASE_URL is not set (PostgreSQL connection string)` | Add `DATABASE_URL` (step 5). |
| `DATABASE_URL must be a PostgreSQL URL (postgresql://...)` | Only PostgreSQL is supported. Use the `postgresql+psycopg://...` form. |
| `ANTHROPIC_API_KEY is not set` | Add your key ([API_KEYS.md](API_KEYS.md#anthropic--claude-required)). |
| `CLERK_ISSUER is not set (...)` / `CLERK_ISSUER must start with https://` | Add your Clerk Frontend API URL, including `https://`. |
| `CLERK_SECRET_KEY is not set` | Add your Clerk secret key (`sk_test_...`). |
| `CLERK_AUTHORIZED_PARTIES is not set (...)` | Set it to `http://localhost:5180`. |
| `FIELD_KEY_B64 is not set (...)` / `must decode to exactly 32 bytes` / `is not valid base64` | Run `python -m probity.bootstrap --generate-secrets` (it fills an empty value; it never replaces one). |
| `HMAC_KEY is not set (...)` / `HMAC_KEY must be at least 32 characters` | Same command as above. |
| `TASK_BACKEND=celery needs REDIS_URL` | Set `REDIS_URL`, or go back to `TASK_BACKEND=inline`. |
| `STORAGE_BACKEND=s3 needs S3_BUCKET, S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY` | Fill those in, or use `STORAGE_BACKEND=local`. |
| `RESEND_API_KEY is set but EMAIL_FROM is not` | Add `EMAIL_FROM`, or remove the Resend key. |
| `EMAIL_REPLY_DOMAIN is set but INBOUND_EMAIL_SECRET is not` | Add the secret, or remove `EMAIL_REPLY_DOMAIN`. |
| `The database schema is not up to date (...). Run: python -m probity.bootstrap` | Run it from `apps/api`. |
| `ENV=prod needs ...` | Production-only rules (cloud storage, ClamAV, metrics token, TLS database). Use `ENV=dev` on your computer. |

The checklist also prints `--` lines for optional integrations that aren't configured (web search, email, storage, antivirus). Those
never block start-up. The GST registry always shows as unavailable (see [API_KEYS.md](API_KEYS.md#gst-registry-not-available)).

---

**Running everything in Docker** (`make up`, or `docker compose -f infra/docker-compose.yml up --build`) builds the API and web images.
It isn't the recommended development path yet: the web image is built without a Clerk publishable key, so sign-in shows as unavailable.
Use the steps above.
