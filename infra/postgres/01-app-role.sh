#!/bin/sh
# Runtime role for the API/worker: not a superuser, not the table owner, so row-level security applies.
# Runs once, when the Postgres data volume is first initialised. The password comes from PROBITY_APP_PASSWORD
# (production compose sets it from infra/.env.prod); local dev falls back to "probity_app".
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_pw="${PROBITY_APP_PASSWORD:-probity_app}" -v db="$POSTGRES_DB" <<'EOSQL'
CREATE ROLE probity_app LOGIN PASSWORD :'app_pw' NOSUPERUSER NOBYPASSRLS;
GRANT CONNECT ON DATABASE :"db" TO probity_app;
EOSQL
