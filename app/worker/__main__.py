"""`python -m app.worker`: start the ARQ worker (selects the right event loop on Windows)."""

from arq.worker import run_worker

from app.worker.arq_worker import WorkerSettings

if __name__ == "__main__":
    run_worker(WorkerSettings)  # type: ignore[arg-type]
