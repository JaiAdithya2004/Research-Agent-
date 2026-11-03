"""Write the API's OpenAPI schema to a file, or check that the committed one is current.

    uv run python scripts/export_openapi.py            # writes web/openapi.json
    uv run python scripts/export_openapi.py --check    # exits 1 if web/openapi.json is stale

The frontend's typed client is generated from this file, never hand-written (AGENTS.md). CI runs
`--check`, so a backend change that alters the API cannot merge without regenerating the file
and, from it, the client.
"""

import argparse
import json
import sys
from pathlib import Path

DEFAULT_PATH = Path("web/openapi.json")


def render() -> str:
    from app.api.main import create_app

    schema = create_app().openapi()  # builds lazily: no database or Redis connection is made
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true", help="fail if the file is out of date")
    args = parser.parse_args(argv)

    fresh = render()
    if args.check:
        current = args.out.read_text(encoding="utf8") if args.out.is_file() else ""
        if current != fresh:
            print(
                f"{args.out} is stale. Run: uv run python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"{args.out} is up to date")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(fresh, encoding="utf8", newline="\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
