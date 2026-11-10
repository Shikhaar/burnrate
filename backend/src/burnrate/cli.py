"""burnrate CLI: `burnrate <command>`."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
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

    c = sub.add_parser("sync", help="Pull billed costs (openai, anthropic) or coding-tool usage (cursor, copilot)")
    c.add_argument("provider", choices=["openai", "anthropic", "cursor", "copilot", "all"])
    c.add_argument("--days", type=int, default=31)

    sub.add_parser("alerts", help="Evaluate budgets/anomalies and send new alerts to the webhook")

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
        elif args.cmd == "alerts":
            from burnrate.services.alerts import evaluate

            fired = evaluate(session)
            for a in fired:
                print(f"[{a.severity}] {a.title} — {a.detail}")
            print(f"{len(fired)} new alert(s).")
        elif args.cmd == "sync":
            from burnrate.config import get_settings
            from burnrate.connectors import FETCHERS, USAGE_PROVIDERS, sync_billing, sync_usage

            start = datetime.now(UTC).date() - timedelta(days=args.days)
            if args.provider == "all":
                # "all" covers whatever is configured, so a missing key skips that provider.
                s = get_settings()
                configured = {"openai": s.openai_admin_key, "anthropic": s.anthropic_admin_key,
                              "cursor": s.cursor_admin_key, "copilot": s.github_token and s.github_org}
                providers = [p for p in (*FETCHERS, *USAGE_PROVIDERS) if configured[p]]
                if not providers:
                    raise SystemExit("No connector is configured; see `burnrate sync --help` and docs/deploy.md.")
            else:
                providers = [args.provider]
            for name in providers:
                if name in USAGE_PROVIDERS:
                    r = sync_usage(session, name, start)
                    print(f"{name}: imported {r.accepted} usage events ({r.duplicates} already present)")
                else:
                    rows = sync_billing(session, name, start)
                    print(f"{name}: upserted {rows} billed-cost rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
