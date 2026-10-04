FROM python:3.12-slim
RUN useradd -m probity
WORKDIR /app
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/api/src apps/api/src
RUN pip install --no-cache-dir -e "apps/api[worker,s3]"
USER probity
WORKDIR /app/apps/api
EXPOSE 8000
# bootstrap prints the live/missing checklist and creates/upgrades the schema (it never inserts data);
# the API then refuses to start if a required setting is missing.
CMD ["sh", "-c", "python -m probity.bootstrap && uvicorn probity.api.main:app --host 0.0.0.0 --port 8000"]
