"""`python -m app.api`: run the API (selects the right event loop on Windows).

psycopg's async mode cannot use the default Proactor event loop on Windows, and uvicorn only
switches loops itself when --reload is used. This launcher always does the right thing.
"""

import asyncio
import os
import sys

import uvicorn

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        loop="asyncio",
    )
