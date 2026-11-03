"""Manage API keys.

    uv run python -m app.auth create --name laptop --daily-budget 5 --rate-limit 60
    uv run python -m app.auth list
    uv run python -m app.auth revoke --id <uuid>

Needs DATABASE_URL and API_KEY_PEPPER. A new key is printed once and cannot be recovered.
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.keys import create_api_key, list_api_keys, revoke_api_key
from app.config import Settings
from app.db.session import make_session_factory

if sys.platform == "win32":  # psycopg's async mode cannot use the default Proactor loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.auth", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create a key (shown once)")
    create.add_argument("--name", required=True)
    create.add_argument("--daily-budget", type=float, help="USD per UTC day for this key")
    create.add_argument("--rate-limit", type=int, help="requests per minute for this key")
    sub.add_parser("list", help="list keys (prefixes only)")
    revoke = sub.add_parser("revoke", help="revoke a key")
    revoke.add_argument("--id", required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if not settings.api_key_pepper:
        print("error: API_KEY_PEPPER must be set", file=sys.stderr)
        return 2
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = make_session_factory(engine)
    try:
        if args.command == "create":
            try:
                created = await create_api_key(
                    factory,
                    name=args.name,
                    pepper=settings.api_key_pepper,
                    daily_budget_usd=args.daily_budget,
                    rate_limit_per_min=args.rate_limit,
                )
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"Created key {created.id} ({args.name}).")
            print("This key will not be shown again; store it now:")
            print(f"    {created.raw_key}")
            return 0
        if args.command == "list":
            for info in await list_api_keys(factory):
                budget = (
                    f"${info.daily_budget_usd:.2f}/day"
                    if info.daily_budget_usd is not None
                    else "-"
                )
                rate = f"{info.rate_limit_per_min}/min" if info.rate_limit_per_min else "-"
                state = "revoked" if info.revoked else "active"
                print(f"{info.id}  {info.name}  {info.key_prefix}...  {budget}  {rate}  {state}")
            return 0
        revoked = await revoke_api_key(factory, args.id)
        print(
            "revoked" if revoked else "no active key with that id",
            file=sys.stdout if revoked else sys.stderr,
        )
        return 0 if revoked else 1
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
