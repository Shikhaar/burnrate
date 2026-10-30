"""burnrate CLI: `burnrate <command>`."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from burnrate.db import SessionLocal, init_db
from burnrate.pricing import reprice, sync_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="burnrate", description="Burnrate admin CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="Create tables and load the default price registry")

    s = sub.add_parser("seed", help="Load the deterministic demo dataset (replaces existing data)")
    s.add_argument("--days", type=int, default=90)

    k = sub.add_parser("create-key", help="Create an ingest API key")
    k.add_argument("name")

    p = sub.add_parser("pricing", help="Load a price registry YAML and reprice events")
    p.add_argument("file", type=Path, nargs="?")
    p.add_argument("--since", type=date.fromisoformat)

    c = sub.add_parser("sync", help="Pull billed costs from provider admin APIs")
    c.add_argument("provider", choices=["openai", "anthropic", "all"])
    c.add_argument("--days", type=int, default=31)

    args = parser.parse_args(argv)
    init_db()
    with SessionLocal() as session:
        sync_registry(session)
        if args.cmd == "init":
            print("Database ready.")
        elif args.cmd == "seed":
            from burnrate.seed import generate

            stats = generate(session, days=args.days)
            print(f"Seeded {stats['events']:,} events (${stats['estimated_cost_usd']:,.2f} estimated spend).")
        elif args.cmd == "create-key":
            from burnrate.security import create_api_key

            print(create_api_key(session, args.name))
            print("Store this key now; it cannot be shown again.", file=sys.stderr)
        elif args.cmd == "pricing":
            added = sync_registry(session, args.file) if args.file else 0
            n = reprice(session, since=args.since)
            print(f"Added {added} price entries; repriced {n:,} events.")
        elif args.cmd == "sync":
            from burnrate.connectors import sync_billing

            start = date.today() - timedelta(days=args.days)
            providers = ["openai", "anthropic"] if args.provider == "all" else [args.provider]
            for name in providers:
                rows = sync_billing(session, name, start)
                print(f"{name}: upserted {rows} billed-cost rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
