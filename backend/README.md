# Burnrate backend

FastAPI service: ingestion (`/v1`), dashboard API (`/api`), pricing engine, analytics, billing connectors.
See the [root README](../README.md) and [docs/](../docs).

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/burnrate seed
.venv/bin/uvicorn burnrate.main:app --reload
.venv/bin/pytest -q
```
