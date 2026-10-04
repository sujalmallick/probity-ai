# API keys and services

Probity talks to a few outside services. This page explains each one: what it's for, whether you need it, what it costs, how to get the key, and where the key goes.

> **Every contributor uses their OWN keys and their OWN Clerk development app.** Keys are never shared, not even with friends on the
> same project. See [Key safety](#key-safety).

All API settings go in **`apps/api/.env`**. The web app has one setting, in **`apps/web/.env.local`**. Both files are ignored by git.
The examples below use placeholders like `sk-ant-xxxxxxxx`. Replace them with your own values.

## Summary

| Service | Required? | Free to start? | Setting(s) | File |
|---|---|---|---|---|
| PostgreSQL (Docker) | **Required** | Yes (runs on your PC) | `DATABASE_URL`, `DATABASE_MIGRATE_URL` | `apps/api/.env` |
| App secrets (generated) | **Required** | Yes | `FIELD_KEY_B64`, `HMAC_KEY` | `apps/api/.env` |
| Anthropic (Claude) | **Required** | No, pay per use (a small top-up is enough for testing) | `ANTHROPIC_API_KEY` | `apps/api/.env` |
| Clerk (sign-in) | **Required** | Yes, free development instance | `CLERK_ISSUER`, `CLERK_SECRET_KEY`, `CLERK_AUTHORIZED_PARTIES` | `apps/api/.env` |
| | | | `VITE_CLERK_PUBLISHABLE_KEY` | `apps/web/.env.local` |
| Tavily (web research) | Optional | Has a free tier | `TAVILY_API_KEY` | `apps/api/.env` |
| Resend (sending email) | Optional | Has a free tier | `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_ALLOWLIST` | `apps/api/.env` |
| Inbound email (vendor replies) | Optional, advanced | Depends on your email service | `EMAIL_REPLY_DOMAIN`, `INBOUND_EMAIL_SECRET` | `apps/api/.env` |
| Redis (background worker) | Optional, advanced | Yes (Docker) | `TASK_BACKEND`, `REDIS_URL` | `apps/api/.env` |
| S3 / Cloudflare R2 (file storage) | Optional, advanced (required in production) | Has free tiers | `STORAGE_BACKEND`, `S3_*` | `apps/api/.env` |
| ClamAV (virus scan) | Optional, advanced (required in production) | Yes (open source) | `CLAMAV_HOST`, `CLAMAV_PORT` | `apps/api/.env` |
| GST registry | Not available | n/a | none | n/a |

Prices and free tiers change. **Always check the provider's own pricing page.** The notes above are only a guide.

## Minimum setup

The smallest set that lets you run the app and the tests:

1. **PostgreSQL** in Docker (free).
2. **App secrets** (one command, free).
3. **Anthropic API key**.
4. **Clerk development app** (free): issuer, secret key, publishable key.

Everything else is optional. Without it, Probity still runs and shows "could not verify" for the checks it can't do.

**Running the backend tests needs only PostgreSQL.** The test suite fakes the AI, Clerk, web search and email, so it never uses
your keys and costs nothing.

---

## PostgreSQL (required)

**What it's for:** the database for everything (cases, vendors, evidence, audit log).

**Cost:** free. It runs in Docker on your computer.

**Setup:** start it with Docker (details in [SETUP.md](SETUP.md), step 3):

```powershell
docker compose -f infra/docker-compose.dev.yml up -d --wait
```

This starts PostgreSQL 16 on **127.0.0.1:5434** and creates two database users:

| User | Password (local dev only) | Used for |
|---|---|---|
| `probity` | `probity` | Owner of the schema. Runs migrations. Goes in `DATABASE_MIGRATE_URL`. |
| `probity_app` | `probity_app` | What the app uses day to day. It can't bypass row-level security, which keeps workspaces apart. Goes in `DATABASE_URL`. |

These passwords are fixed in `infra/` and are **only for your own computer**. Never reuse them anywhere real.

```dotenv
# apps/api/.env
DATABASE_URL=postgresql+psycopg://probity_app:probity_app@127.0.0.1:5434/probity
DATABASE_MIGRATE_URL=postgresql+psycopg://probity:probity@127.0.0.1:5434/probity
```

Use `127.0.0.1`, not `localhost`: the container only listens on IPv4.

**Check it works:** from `apps/api`, run `python -m probity.check database`.

---

## App secrets: `FIELD_KEY_B64` and `HMAC_KEY` (required)

**What they're for:**
- `FIELD_KEY_B64` encrypts bank account and PAN numbers before they're stored. It is base64 of 32 random bytes.
- `HMAC_KEY` lets Probity match account numbers without decrypting them, and signs the audit log. It is at least 32 random characters.

**Cost:** free. You generate them yourself.

**Generate them safely** (from the `apps/api` folder, with the virtual environment active):

```powershell
python -m probity.bootstrap --generate-secrets
```

This creates `apps/api/.env` from `.env.example` if it doesn't exist, and fills the two values **only if they are empty**. It writes
them to the file and **never prints them**.

**Important:**
- **Don't change these after you have data.** A new `FIELD_KEY_B64` makes stored bank numbers unreadable. A new `HMAC_KEY` breaks account
  matching and the audit chain. If you must change them on a dev machine, reset the database (`python -m probity.bootstrap --reset --yes`)
  and start again.
- Each person generates their own. Never copy someone else's.

---

## Anthropic / Claude (required)

**What it's for:** the AI steps:
- filling gaps when reading invoices
- writing web search queries
- checking that a quoted source supports a claim
- writing the case summary
- drafting neutral vendor emails
- reading vendor replies

The risk score itself never uses the AI.

**Cost:** paid per use (tokens). New accounts may get a small credit; check the Anthropic pricing page. Probity limits each case to
40 AI calls and 200k tokens, and each workspace to 3M tokens and 25 cases per day (see `apps/api/.env.example`).

**Get a key:**
1. Go to the Anthropic Console (console.anthropic.com) and sign up.
2. Add billing (a small prepaid amount is enough for testing).
3. **Set a monthly spend limit** in the Console's limits or billing settings. Do this before anything else.
4. Open **API keys**, create a key, and name it something like `probity-dev-<yourname>`.
5. Copy it once. It starts with `sk-ant-`.

```dotenv
# apps/api/.env
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
```

The models are set by `LLM_MODEL_REASONING` and `LLM_MODEL_FAST` (already filled in `.env.example`).

**Check it works** (one small request; the key is never printed):

```powershell
cd apps/api
python -m probity.check ai
```

---

## Clerk (required): sign-in

**What it's for:** user sign-up and sign-in. Your first sign-in creates your own workspace, with you as **owner**.

**Cost:** free for a development instance (check Clerk's pricing page for limits).

**Set up your own development app:**

1. Sign up at clerk.com and **create an application** (name it e.g. `probity-dev-<yourname>`).
2. Under the sign-in options, turn on **Email address** with **Password**. Keep email verification on: Probity only accepts users whose
   email is verified.
3. Open the **API keys** page of your app and copy:
   - the **Publishable key** (starts with `pk_test_`)
   - the **Secret key** (starts with `sk_test_`)
   - the **Frontend API URL**. This is your **issuer**, and it looks like `https://<your-app>.clerk.accounts.dev`.
4. Put them in the right files:

```dotenv
# apps/api/.env
CLERK_ISSUER=https://your-app-name.clerk.accounts.dev
CLERK_SECRET_KEY=sk_test_xxxxxxxx
CLERK_AUTHORIZED_PARTIES=http://localhost:5180
```

```dotenv
# apps/web/.env.local   (create this file; there is no example file for it yet)
VITE_CLERK_PUBLISHABLE_KEY=pk_test_xxxxxxxx
```

**Authorized party:** Clerk puts the web page's address into each sign-in token. The API only accepts tokens from addresses listed in
`CLERK_AUTHORIZED_PARTIES`. For local development that is `http://localhost:5180`. If you open the app at `http://127.0.0.1:5180`
instead, add that too (comma-separated), or you'll get "token issued for an unauthorized origin".

Restart the web dev server after changing `.env.local`: Vite only reads it at start-up.

**Check it works:**

```powershell
cd apps/api
python -m probity.check sign_in
```

Then open http://localhost:5180 and sign up.

---

## Tavily (optional): web research

**What it's for:** searching the web for third-party information about a vendor (the "external reputation" check).

**Without it:** that check shows **"could not verify: web search not configured"**. It's an optional check, so it doesn't stop
auto-clear by itself.

**Cost:** has a free monthly allowance (check tavily.com).

**Get a key:** sign up at tavily.com, copy your API key from the dashboard (it usually starts with `tvly-`).

```dotenv
TAVILY_API_KEY=tvly-xxxxxxxx
```

**Check it works:** `python -m probity.check web_search` (from `apps/api`).

Domain age doesn't need a key: it uses the public RDAP service (`python -m probity.check domain_lookup`).

---

## Resend (optional): sending email, and `EMAIL_ALLOWLIST`

**What it's for:** sending verification emails to vendors (only after an approver approves), team invitations, and notification emails.

**Without it:** nothing is sent. Drafts are still created and shown in the app.

**Other providers:** **Resend is the only email provider supported today.** SMTP is not supported yet (planned).

**Cost:** has a free tier (check resend.com).

**Get a key:**
1. Sign up at resend.com and create an API key (starts with `re_`).
2. To send from your own address, **verify a domain** in Resend. For a quick test, Resend offers a test sender that can usually only
   deliver to your own Resend account email. Check Resend's docs.

```dotenv
RESEND_API_KEY=re_xxxxxxxx
EMAIL_FROM=Probity <probity@your-verified-domain.example>
EMAIL_ALLOWLIST=you@example.com,teammate@example.com
EMAIL_SEND_TO_ANY=false
```

**Why the allowlist?** While you're testing, you'll be working with vendor addresses taken from real-looking invoices. The allowlist
makes sure **only addresses you list get any email**. Everything else is blocked and recorded in the audit log. Leave
`EMAIL_SEND_TO_ANY=false` unless you are running Probity for real and mean to email vendors.

If `RESEND_API_KEY` is set without `EMAIL_FROM`, the API refuses to start.

**Check it works:** `python -m probity.check email --send-test-email` sends one message to the **first** address in `EMAIL_ALLOWLIST`.

---

## Advanced (optional)

### Inbound email (vendor replies arriving automatically)

Outgoing emails use a reply-to address like `case+<case-id>@<EMAIL_REPLY_DOMAIN>`. To receive replies automatically, you need an email
service that receives mail for that domain and forwards it as JSON (`{from, to, subject, text, dkim?}`) to:

```
POST /api/v1/webhooks/inbound-email
```

Each request must be signed. The `X-Probity-Timestamp` and `X-Probity-Signature` headers carry an HMAC-SHA256 of the timestamp and
body, made with `INBOUND_EMAIL_SECRET`. Requests older than 5 minutes are refused.

```dotenv
EMAIL_REPLY_DOMAIN=replies.your-domain.example
INBOUND_EMAIL_SECRET=<at least 32 random characters>
```

Setting `EMAIL_REPLY_DOMAIN` without `INBOUND_EMAIL_SECRET` stops the API from starting.

**You don't need this to try replies:** an accountant can paste a vendor's reply into the case by hand.

### Redis (background worker)

By default (`TASK_BACKEND=inline`), investigations run inside the API process. That is fine for development. To run them in a separate
Celery worker:

```powershell
docker compose -f infra/docker-compose.dev.yml --profile worker up -d --wait
```

```dotenv
TASK_BACKEND=celery
REDIS_URL=redis://127.0.0.1:6380/0
```

Then start a worker from `apps/api`:

```powershell
python -m celery -A probity.worker worker -B --loglevel=INFO
```

### S3 or Cloudflare R2 (file storage)

By default, uploaded invoices are stored on disk in `apps/api/data/uploads` (git-ignored). For cloud storage, first install the extra
package from the repo root (`pip install -e "apps/api[s3]"`), then:

```dotenv
STORAGE_BACKEND=s3
S3_BUCKET=probity-uploads-yourname
S3_REGION=auto
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com   # R2 only; leave empty for AWS S3
S3_ACCESS_KEY_ID=xxxxxxxx
S3_SECRET_ACCESS_KEY=xxxxxxxx
```

Check it with `python -m probity.check storage`. Production (`ENV=prod`) requires S3/R2.

### ClamAV (virus scanning)

Point Probity at a running ClamAV daemon (`clamd`). This repo doesn't include one, so you would run the official ClamAV Docker image
yourself:

```dotenv
CLAMAV_HOST=127.0.0.1
CLAMAV_PORT=3310
```

Without it, the scan is skipped and the audit log says "SKIPPED". Production requires it. Check it with `python -m probity.check antivirus`.

---

## GST registry: not available

There is **no free official API** for checking a GSTIN against the government registry. Full access needs a paid GST Suvidha
Provider (GSP), which Probity doesn't integrate. So:

- Probity checks that the GSTIN has the **right format and checksum**. That catches typos and made-up numbers, but it does **not**
  prove the business is registered or active.
- The "GST registry" check always shows **"could not verify"**.
- An accountant can record what they read on the GST portal (Active / Cancelled / Suspended) on the vendor page. It's shown as
  **"Entered manually by <name>"**, never as verified. Anything other than Active sends the invoice to a person.

---

## Key safety

- **Never commit keys.** `.env`, `.env.local` and every `.env.*` file except `.env.example` are git-ignored. Before committing, check
  `git status` and never `git add -f` an env file.
- **Never paste keys** in GitHub issues, pull requests, chat messages or screenshots. That includes terminal screenshots where `.env` is
  open. Probity's own commands never print secret values; keep it that way.
- **Use your own keys and your own Clerk dev app.** Don't share keys with teammates. Each person pays for and controls their own.
- **Set spending limits** on Anthropic (and any paid service) before using the key.
- **If a key leaks** (committed, pasted or shown on screen):
  1. **Rotate it immediately**: delete or revoke the key at the provider and create a new one.
  2. Update your `apps/api/.env` or `apps/web/.env.local`.
  3. If it was committed, removing the commit isn't enough, because it stays in the history and in forks. Rotating the key is what makes
     you safe. Tell the maintainer so the history can be cleaned if needed.
  4. Check the provider's usage page for anything you didn't do.
- `FIELD_KEY_B64` and `HMAC_KEY` protect stored data. Treat them like passwords.
