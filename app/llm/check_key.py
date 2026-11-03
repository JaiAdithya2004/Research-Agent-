"""`python -m app.llm.check_key`: check the provider keys in .env. Prints no key.

Each check is one cheap metadata request (no model call, no tokens). Safe on a free tier.
"""

import asyncio
import os
import sys
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

from app.envfile import load_env_file
from app.llm.providers import KeyCheck, check_gemini_key, check_openrouter_key
from app.logging_config import redact

PROVIDERS: dict[str, tuple[str, Callable[[str], Coroutine[Any, Any, KeyCheck]]]] = {
    "openrouter": ("OPENROUTER_API_KEY", check_openrouter_key),
    "gemini": ("GEMINI_API_KEY", check_gemini_key),
}


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    wanted = list(argv or []) or list(PROVIDERS)
    unknown = [w for w in wanted if w not in PROVIDERS]
    if unknown:
        print(f"unknown provider(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    worst = 0
    for name in wanted:
        env_name, checker = PROVIDERS[name]
        key = os.environ.get(env_name, "").strip()
        if not key:
            print(f"{name}: {env_name} is not set (put it in .env)", file=sys.stderr)
            worst = max(worst, 2)
            continue
        result = asyncio.run(checker(key))
        print(f"{name}: {result.status}: {redact(result.message, [key])}")
        if result.label:
            print(f"  label: {result.label}")
        if result.limit_remaining is not None:
            print(f"  credit remaining: ${result.limit_remaining:.2f}")
        if result.status != "valid":
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(main())
