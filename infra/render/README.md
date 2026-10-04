# Deploying Probity for free: Render + Neon

## What runs where

| Piece | Where | Free tier |
|---|---|---|
| API and web app | Render web service (`render.yaml`, `infra/render.Dockerfile`) | 750 instance hours a month, 512 MB RAM |
| Investigations, watchdog, follow-up reminders | Inside the API process (`TASK_BACKEND=inline`) | No worker, scheduler or Redis needed |
| Database | Neon Postgres | 0.5 GB storage, 100 compute hours a month |
| Uploaded invoices | Neon Object Storage (S3-compatible, same project) | 5 GB storage, 5 GB egress a month |
| Sign-in | Clerk | |

The API serves the built web app itself, so the site and `/api` share one origin: no CORS and no proxy.

## Limits of the free setup

- **Render sleeps** after 15 minutes without incoming requests. The next visit takes about a minute to wake it.
- **An open case page keeps it awake.** If the service sleeps or restarts during an investigation, the case ends with a clear "interrupted" reason when it starts again.
- **Follow-up reminders and the watchdog only run while the service is awake.**
- **Don't keep Render awake with an uptime pinger.** The watchdog queries the database every minute, so Neon never sleeps while Render is awake. Running all month needs about 180 Neon compute hours, which exceeds the 100 free hours. Hitting a Neon limit pauses the database until next month.
- **Run exactly one instance.** Inline investigations live inside the process.
- **File egress:** every invoice someone views or downloads counts toward Neon's 5 GB of storage egress a month.
- **Memory:** the API peaked at about 165 MB while parsing and rendering a 30-page PDF, well inside 512 MB.
- **Email:** Render's free plan blocks SMTP ports. Resend uses HTTPS, so outbound email still works.

## Steps

### 1. Generate app secrets

Use fresh values. Don't reuse the ones in your local `apps/api/.env`.

```
docker run --rm python:3.12-slim python -c "import base64,secrets; print('FIELD_KEY_B64=' + base64.b64encode(secrets.token_bytes(32)).decode()); print('HMAC_KEY=' + secrets.token_urlsafe(48)); print('PROBITY_APP_PASSWORD=' + secrets.token_urlsafe(32))"
```

Store all three in a password manager:
- Losing `FIELD_KEY_B64` makes stored bank numbers unreadable.
- Changing `HMAC_KEY` breaks verification of older audit-log rows.

### 2. Create the Neon database

1. Create a Neon project in **AWS Asia Pacific (Singapore)**, the same region as the Render service in `render.yaml`.
2. In the Neon **SQL Editor**, which runs as the owner role `neondb_owner`, create the runtime role. Use `PROBITY_APP_PASSWORD` from step 1:
   ```sql
   CREATE ROLE probity_app LOGIN PASSWORD '<PROBITY_APP_PASSWORD>' NOSUPERUSER NOBYPASSRLS;
   GRANT CONNECT ON DATABASE neondb TO probity_app;
   ```
   - Do this **before the first deploy**. The migrations only grant table access to `probity_app` if the role already exists.
   - Create the role in SQL, not on Neon's Roles page. Neon adds roles created there to `neon_superuser`, which has broader privileges.
3. Click **Connect**, turn **off** connection pooling, and copy the connection string for `neondb_owner`. This is `DATABASE_MIGRATE_URL`.
4. Copy the same string again, replacing the user and password with `probity_app` and `PROBITY_APP_PASSWORD`. This is `DATABASE_URL`.
5. Check that both strings include `sslmode=require`; the app refuses to start without it. Neon's `channel_binding=require` can stay. You can paste them as Neon shows them (`postgresql://...`): the app switches them to the psycopg driver itself.

### 3. Create the storage bucket

1. In the Neon project, turn on **Object storage** and create a **private** bucket named `uploads`.
2. Under **Connect → Storage**, reveal the credential. Neon shows the secret only once.
3. Neon names the values `AWS_*`. Probity reads them as `S3_*`:

   | Neon | Render |
   |---|---|
   | bucket name | `S3_BUCKET` |
   | `AWS_ENDPOINT_URL_S3` | `S3_ENDPOINT_URL` |
   | `AWS_REGION` | `S3_REGION` |
   | `AWS_ACCESS_KEY_ID` | `S3_ACCESS_KEY_ID` |
   | `AWS_SECRET_ACCESS_KEY` | `S3_SECRET_ACCESS_KEY` |

- The credential can read and write every bucket on that branch. Neon has no per-bucket keys.
- Tested with the app's boto3 version: uploads, downloads and deletes work as-is, including the AES256 encryption header.
- Cloudflare R2 or Backblaze B2 also work. Set the same five values from those providers instead, with `S3_REGION=auto` for R2.

### 4. Get the Clerk keys

- Use your Clerk **development** instance keys (`pk_test_...` and `sk_test_...`). They work on an `onrender.com` address.
- A Clerk production instance needs a domain you own.
- `CLERK_ISSUER` is `https://<your-app>.clerk.accounts.dev`.

### 5. Create the Render service

1. Push a branch containing `render.yaml` and `infra/render.Dockerfile` to GitHub.
2. In Render, go to **New → Blueprint**, pick the `probity-ai` repository and that branch.
3. Fill in the values Render asks for:
   - `PUBLIC_APP_URL`, `CORS_ORIGINS` and `CLERK_AUTHORIZED_PARTIES` are all the service URL, for example `https://probity.onrender.com`.
   - If the name is taken, Render adds a suffix. In that case, fix the three values once the service exists and redeploy.
4. Render builds the image, then each start runs the migrations (`python -m probity.bootstrap`) and the API.
5. Every push to the branch redeploys. Changing `VITE_CLERK_PUBLISHABLE_KEY` needs a redeploy, because the key is built into the web app.

### 6. Check it

- **Logs** start with the configuration checklist. Every required line must show `OK`.
- `https://<your-service>.onrender.com/api/v1/ready` returns `{"ok": true, ...}` when the database and schema are ready.
- Sign in on the site, upload an invoice, and keep the case page open while it runs.
