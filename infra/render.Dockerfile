# Render free web service: one container serves the API and the built web app from the same origin.
# Investigations and the watchdog run inside the API process (TASK_BACKEND=inline): no worker, scheduler or Redis.
# Database: Neon. Files: Cloudflare R2. Setup: infra/render/README.md.

FROM node:22-alpine AS web
WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web ./
# Render passes service environment variables as build args. Only this public key is read at build time.
ARG VITE_CLERK_PUBLISHABLE_KEY
ENV VITE_CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLISHABLE_KEY
RUN test -n "$VITE_CLERK_PUBLISHABLE_KEY" || (echo "VITE_CLERK_PUBLISHABLE_KEY is required (sign-in won't work without it)" && exit 1)
RUN npm run build

FROM python:3.12-slim
RUN useradd -m probity
WORKDIR /app
COPY apps/api/requirements-full.lock apps/api/requirements-full.lock
RUN pip install --no-cache-dir -r apps/api/requirements-full.lock
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/api/src apps/api/src
RUN pip install --no-cache-dir --no-deps ./apps/api
# api/main.py serves REPO_ROOT/apps/web/dist (REPO_ROOT is /app when started from /app/apps/api).
COPY --from=web /web/dist apps/web/dist
USER probity
WORKDIR /app/apps/api
# Fewer glibc malloc arenas: keeps a threaded Python process well inside the 512 MB free instance.
# One document parser helper capped at 320 MB: API (~110 MB) + helper stays under 512 MB (config.py parse_*).
ENV MALLOC_ARENA_MAX=2 PARSE_HELPERS=1 PARSE_MEMORY_MB=320
# Render sets PORT (10000 by default) and is the only thing that can reach the container, so trusting its
# X-Forwarded-* headers is safe. Run exactly one process: inline investigations live in it.
# Only the migration step gets the schema owner's URL; the API runs without it (as probity_app, row-level security on).
CMD ["sh", "-c", "python -m probity.bootstrap && exec env -u DATABASE_MIGRATE_URL uvicorn probity.api.main:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips '*'"]
