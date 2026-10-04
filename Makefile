# Probity — Evidence before payment.
PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python3))

.PHONY: install db bootstrap dev api web worker test build up down

install:            ## create venv + install API and web deps
	python -m venv .venv
	$(PY) -m pip install -e "apps/api[dev,worker]"
	cd apps/web && npm install

db:                 ## start local Postgres (Docker) on :5434
	docker compose -f infra/docker-compose.dev.yml up -d --wait

bootstrap:          ## print the live/missing checklist and create the empty schema (first run: add --generate-secrets)
	cd apps/api && ../../$(PY) -m probity.bootstrap

api:                ## FastAPI on :8010 (refuses to start until apps/api/.env has every required setting)
	cd apps/api && ../../$(PY) -m uvicorn probity.api.main:app --host 127.0.0.1 --port 8010 --reload

worker:             ## optional: Celery worker, only when TASK_BACKEND=celery
	cd apps/api && ../../$(PY) -m celery -A probity.worker worker -B --loglevel=INFO

web:                ## Vite dev server on :5180 (proxies /api to :8010)
	cd apps/web && npm run dev

dev:                ## run API + web together
	$(MAKE) -j2 api web

test:               ## backend tests (throwaway Postgres DB; needs `make db`), frontend typecheck
	cd apps/api && ../../$(PY) -m pytest -q
	cd apps/web && npx tsc --noEmit

build:              ## production web build (served by the API at /)
	cd apps/web && npm run build

up:                 ## docker compose: postgres, api, web (settings from apps/api/.env)
	docker compose -f infra/docker-compose.yml up --build

down:
	docker compose -f infra/docker-compose.yml down
