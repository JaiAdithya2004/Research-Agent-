"""`python -m app.llm.check_key`: check OPENROUTER_API_KEY. Costs nothing, prints no key."""

import asyncio
import os
import sys
from collections.abc import Sequence

from app.llm.providers import check_openrouter_key
from app.logging_config import redact


def main(argv: Sequence[str] | None = None) -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        print(
            "OPENROUTER_API_KEY is not set (put it in .env or your environment).", file=sys.stderr
        )
        return 2
    result = asyncio.run(check_openrouter_key(key))
    print(f"{result.status}: {redact(result.message, [key])}")
    if result.label:
        print(f"label: {result.label}")
    if result.limit_remaining is not None:
        print(f"credit remaining: ${result.limit_remaining:.2f}")
    if result.usage is not None:
        print(f"spent so far: ${result.usage:.2f}")
    return 0 if result.status == "valid" else 1


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(main())
