# Deploying Probity with Cloudflare

## What runs where

Cloudflare's free tier cannot run this stack's containers. Cloudflare Containers needs the paid Workers plan, and Cloudflare has no managed Postgres or Redis. So the containers in `infra/docker-compose.yml` run on **one small Linux server**, and Cloudflare sits in front of it.

**On your server (`infra/docker-compose.yml`):**

| Service | What it does | Note |
|---|---|---|
| `postgres` | Database | |
| `redis` | Job queue and live progress | |
| `migrate` | Creates or updates the database schema, then exits | Runs once per start |
| `api` | The FastAPI backend | |
| `worker` | Runs investigations | Celery; scale with `--scale worker=N` |
| `scheduler` | Follow-up reminders and the stuck-case watchdog | Celery beat; run exactly one |
| `web` | Serves the website | nginx |
| `tunnel` | Connects your server to Cloudflare | `cloudflared`; outbound connection only |

**On Cloudflare (free tier):**

| Service | What it does |
|---|---|
| Zero Trust Tunnel | Publishes `https://your-domain` with no open inbound ports on the server |
| R2 | Stores uploaded invoices (S3-compatible; 10 GB/month free, no egress fees) |
| DNS, TLS and WAF | Domain records, HTTPS certificates, attack filtering |
| Email Routing + an Email Worker | Optional: sends vendor replies to `case+<id>@replies.your-domain` into the app |
| Pages | Optional: can host the website instead of the `web` container. You'd then need to route `/api/*` to the tunnel |

## Server size

Plan for **2 GB of RAM** (4 GB if you add ClamAV, see below).

Uploads are made safe without a resident virus scanner: every PDF is parsed and rewritten without active content (JavaScript, embedded
files, auto-actions) before it's stored, and hostile PDFs are refused. If you also want signature-based virus scanning, run a
`clamav/clamav` container on the `backend` network and set `CLAMAV_HOST=clamav` in `infra/.env.prod`; it needs 1–3 GB of RAM.

## Steps

1. **Create the R2 bucket.**
   - In R2, create a bucket (for example `probity-invoices`).
   - Under **Manage R2 API tokens**, create a token with *Object Read & Write* on that bucket only.
   - In `infra/.env.prod`, set `S3_BUCKET`, `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`.
   - Set `S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com` and `S3_REGION=auto`.
   - Probity does not send the AWS server-side-encryption header to R2, because R2 encrypts every object at rest itself.
2. **Create the tunnel.**
   - In Zero Trust, go to **Networks → Tunnels → Create a tunnel** and choose Docker.
   - Copy the token into `CLOUDFLARE_TUNNEL_TOKEN`.
   - Under **Public hostname**, set `probity.your-domain` to Service `HTTP` → `web:80`.
3. **Fill in `infra/.env.prod`.** Start from `infra/env.prod.example`. Set `PUBLIC_APP_URL`, `CORS_ORIGINS` and `CLERK_AUTHORIZED_PARTIES` to `https://probity.your-domain`.
4. **Start the stack:**
   ```
   docker compose --env-file infra/.env.prod -f infra/docker-compose.yml up -d --build
   ```
   The `migrate` step creates the database schema first; the API, worker and scheduler start when it has finished.
5. **Check it:**
   ```
   docker compose --env-file infra/.env.prod -f infra/docker-compose.yml exec api python -m probity.check
   ```
   This tests the database (including a create/read/update/delete round trip), AI, sign-in, storage, email and search.
6. **Optional: vendor replies by email.**
   - Turn on Email Routing for `replies.your-domain`, with a catch-all rule pointing to an Email Worker.
   - The Worker POSTs to `https://probity.your-domain/api/v1/webhooks/inbound-email` with this JSON: `{from, to, subject, text, dkim}`.
   - It adds the header `X-Probity-Timestamp: <unix seconds>`.
   - It adds the header `X-Probity-Signature: hex(HMAC-SHA256(INBOUND_EMAIL_SECRET, "<timestamp>.<raw body>"))`.
   - Set `EMAIL_REPLY_DOMAIN` and `INBOUND_EMAIL_SECRET`.

## Security notes

- **No public ports.** Only the `tunnel` container talks to the internet.
- **Isolated networks.**
  - The `edge` network holds only `web` and `tunnel`.
  - The database, Redis and the Python services are on the `backend` network.
  - The tunnel cannot reach the backend network directly.
- **Database traffic.** Postgres traffic stays on the private Docker network, so no TLS is required for it. An external database (a hostname with dots) must use `sslmode=require`; the app refuses to start without it.
- **Back up these values** from `infra/.env.prod`:
  - `FIELD_KEY_B64` and `HMAC_KEY`. Losing `FIELD_KEY_B64` makes stored bank numbers unreadable, and changing `HMAC_KEY` breaks verification of older audit-log rows.
  - The Postgres volume.
- **Pin image versions** to digests before going live:
  - `cloudflare/cloudflared:latest`
  - the `postgres`, `redis`, `node` and `nginx` base images
