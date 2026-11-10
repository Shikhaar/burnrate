"""burnrate CLI: `burnrate <command>`."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

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

    sub.add_parser("list-keys", help="List ingest API keys with when each was last used")
    sub.add_parser("generate-secret-key", help="Print a new BURNRATE_SECRET_KEY for encrypting saved secrets")

    u = sub.add_parser("create-user", help="Create a dashboard login (login becomes required once one exists)")
    u.add_argument("email")
    u.add_argument("--name")
    u.add_argument("--role", choices=["admin", "member", "viewer"], default="admin")
    u.add_argument("--teams", help="Comma-separated teams this user may see (default: all). Not for admins.")
    u.add_argument("--password-stdin", action="store_true", help="Read the password from stdin instead of prompting")

    sub.add_parser("list-users", help="List dashboard users")

    rp = sub.add_parser("reset-password", help="Set a new password for a user and sign them out everywhere")
    rp.add_argument("email")
    rp.add_argument("--password-stdin", action="store_true")

    for cmd, help_ in (("disable-user", "Block a user from signing in and end their sessions"),
                       ("enable-user", "Let a disabled user sign in again")):
        x = sub.add_parser(cmd, help=help_)
        x.add_argument("email")

    r = sub.add_parser("revoke-key", help="Revoke an ingest API key immediately")
    r.add_argument("key", help="Key id from list-keys, or the full br_live_ key")

    p = sub.add_parser("pricing", help="Load a price registry YAML and reprice events")
    p.add_argument("file", type=Path, nargs="?")
    p.add_argument("--since", type=date.fromisoformat)

    c = sub.add_parser("sync", help="Pull billed costs (openai, anthropic) or coding-tool usage (cursor, copilot)")
    c.add_argument("provider", choices=["openai", "anthropic", "cursor", "copilot", "all"])
    c.add_argument("--days", type=int, default=31)

    sub.add_parser("alerts", help="Evaluate budgets/anomalies and send new alerts to the webhook")

    args = parser.parse_args(argv)
    if args.cmd == "generate-secret-key":  # needs no database
        from burnrate.services.secrets import generate_key

        print(generate_key())
        print("Set it as BURNRATE_SECRET_KEY on the API and sync jobs. Keep it out of the database and backups.",
              file=sys.stderr)
        return 0
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
        elif args.cmd == "list-keys":
            from burnrate.security import list_api_keys

            keys = list_api_keys(session)
            if not keys:
                print("No API keys. Create one with `burnrate create-key <name>`.")
            for key in keys:
                print(_key_row(key))
        elif args.cmd == "revoke-key":
            from burnrate.security import revoke_api_key

            key = revoke_api_key(session, args.key)
            if key is None:
                # Don't echo the argument: it may be a raw key.
                print("No matching key. Use the id from `burnrate list-keys` or the full key.", file=sys.stderr)
                return 1
            print(f"Revoked: {_key_row(key)}")
        elif args.cmd in ("create-user", "list-users", "reset-password", "disable-user", "enable-user"):
            return _users(session, args)
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


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    import getpass

    first = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != first:
        raise SystemExit("Passwords don't match.")
    return first


def _users(session, args) -> int:
    from burnrate import auth

    if args.cmd == "list-users":
        users = session.scalars(select(auth.User).order_by(auth.User.id)).all()
        if not users:
            print("No users: the dashboard is open to anyone who can reach it. Add one with `burnrate create-user`.")
        for user in users:
            state = f"disabled {_when(user.disabled_at)}" if user.disabled_at else "active"
            teams = ",".join(user.teams) if user.teams is not None else "all teams"
            if not user.password_hash:
                state += " (invite pending)"
            print(f"{user.id:>4}  {user.email:<32}  {user.name or '':<20}  {user.role:<6}  {teams:<20}  "
                  f"last sign-in {_when(user.last_login_at)}  {state}")
        return 0
    if args.cmd == "create-user":
        try:
            teams = [t for t in args.teams.split(",")] if args.teams else None
            user = auth.create_user(session, args.email, _read_password(args.password_stdin), args.name,
                                    args.role, teams)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Created {user.email}. The dashboard now requires signing in.")
        return 0

    user = auth.find_user(session, args.email)
    if user is None:
        print(f"No user with email {auth.normalize_email(args.email)}.", file=sys.stderr)
        return 1
    if args.cmd == "reset-password":
        try:
            auth.set_password(session, user, _read_password(args.password_stdin))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"Password updated for {user.email}; their existing sessions were signed out.")
    else:
        auth.set_disabled(session, user, args.cmd == "disable-user")
        print(f"{user.email} {'disabled and signed out' if args.cmd == 'disable-user' else 'enabled'}.")
    return 0


def _when(ts: datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC") if ts else "never"


def _key_row(key) -> str:
    state = f"revoked {_when(key.revoked_at)}" if key.revoked_at else "active"
    return (f"{key.id:>4}  {key.prefix}…  {key.name:<24}  created {_when(key.created_at)}  "
            f"last used {_when(key.last_used_at)}  {state}")


if __name__ == "__main__":
    raise SystemExit(main())
