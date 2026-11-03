"""`python -m app.api`: run the API.

psycopg's async mode cannot use Windows' default Proactor event loop. Newer uvicorn versions pick
their own loop and ignore the asyncio policy, so on Windows we hand uvicorn the selector loop
class directly.
"""

import os
import sys

import uvicorn


def loop_setting(platform: str = sys.platform) -> str:
    return "asyncio:SelectorEventLoop" if platform == "win32" else "asyncio"


if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        loop=loop_setting(),
    )
