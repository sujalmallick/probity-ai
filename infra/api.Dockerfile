FROM python:3.12-slim
RUN useradd -m probity
WORKDIR /app
# Exact dependency versions from the lock (runtime + worker + s3), then the app itself without re-resolving.
COPY apps/api/requirements-full.lock apps/api/requirements-full.lock
RUN pip install --no-cache-dir -r apps/api/requirements-full.lock
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/api/src apps/api/src
RUN pip install --no-cache-dir --no-deps ./apps/api
# Writable folder for uploads when STORAGE_BACKEND=local (production uses S3/R2).
RUN mkdir -p /app/apps/api/data/uploads && chown -R probity /app/apps/api/data
USER probity
WORKDIR /app/apps/api
EXPOSE 8000
# bootstrap prints the live/missing checklist and creates/upgrades the schema (it never inserts data);
# the API then refuses to start if a required setting is missing.
CMD ["sh", "-c", "python -m probity.bootstrap && uvicorn probity.api.main:app --host 0.0.0.0 --port 8000"]
