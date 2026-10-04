# Probity — Evidence before payment.
PY ?= $(if $(wildcard .venv/Scripts/python.exe),.venv/Scripts/python.exe,$(if $(wildcard .venv/bin/python),.venv/bin/python,python3))

.PHONY: install dev api web seed test benchmark safety-eval build up down

install:            ## create venv + install API and web deps
	python -m venv .venv
	$(PY) -m pip install -e "apps/api[dev]"
	cd apps/web && npm install

seed:               ## reset the local DB with demo workspace, vendors, history and demo PDFs
	cd apps/api && ../../$(PY) -m probity.demo.seed --reset

api:                ## FastAPI on :8000
	cd apps/api && ../../$(PY) -m uvicorn probity.api.main:app --host 127.0.0.1 --port 8000 --reload

web:                ## Vite dev server on :5173 (proxies /api to :8000)
	cd apps/web && npm run dev

dev:                ## run API + web together
	$(MAKE) -j2 api web

test:               ## backend unit + e2e tests, frontend typecheck
	cd apps/api && ../../$(PY) -m pytest -q
	cd apps/web && npx tsc --noEmit

benchmark:          ## manual vs system table → benchmark/results.json
	$(PY) benchmark/run.py

safety-eval:        ## Guardrails.md test table
	$(PY) benchmark/safety_eval.py

build:              ## production web build (served by the API at /)
	cd apps/web && npm run build

up:                 ## docker compose: postgres, redis, qdrant, api, web
	docker compose -f infra/docker-compose.yml up --build

down:
	docker compose -f infra/docker-compose.yml down
