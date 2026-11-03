"""`python -m app.worker`: start the ARQ worker (selects the right event loop on Windows)."""

import asyncio
import sys

from arq.worker import run_worker

from app.worker.arq_worker import WorkerSettings

if __name__ == "__main__":
    if sys.platform == "win32":  # psycopg async cannot use the Proactor loop
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    run_worker(WorkerSettings)  # type: ignore[arg-type]
