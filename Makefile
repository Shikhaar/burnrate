.PHONY: install seed api web dev test lint

PY := backend/.venv/bin

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"
	cd web && pnpm install

seed:
	cd backend && .venv/bin/burnrate seed

api:
	cd backend && .venv/bin/uvicorn burnrate.main:app --reload --port 8000

web:
	cd web && pnpm dev

dev:
	$(MAKE) -j2 api web

test:
	cd backend && .venv/bin/pytest -q
	cd sdk/python && ../../$(PY)/python -m pytest -q

lint:
	cd backend && .venv/bin/ruff check src tests
	cd web && pnpm lint
