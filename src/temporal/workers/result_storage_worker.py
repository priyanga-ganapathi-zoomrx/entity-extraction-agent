"""Result Storage Worker - Saves step outputs to GCS for admin portal download.

This worker:
- Polls the RESULT_STORAGE task queue
- Executes save_step_output to write extraction results to GCS

Usage:
    python -m src.temporal.workers.result_storage_worker

    # With idle shutdown (env var)
    IDLE_SHUTDOWN_MINUTES=5 python -m src.temporal.workers.result_storage_worker
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from src.temporal.config import TaskQueues, WorkerSettings
from src.temporal.activities.result_storage import save_step_output
from src.temporal.workers.base import run_worker

logger = logging.getLogger(__name__)


async def run_result_storage_worker(idle_shutdown_minutes: float | None = None) -> None:
    """Run the result storage worker."""
    settings = WorkerSettings.RESULT_STORAGE

    logger.info("Starting Result Storage Worker")

    await run_worker(
        task_queue=TaskQueues.RESULT_STORAGE,
        workflows=None,
        activities=[save_step_output],
        max_concurrent_activities=settings.get("max_concurrent_activities", 30),
        idle_shutdown_minutes=idle_shutdown_minutes,
    )


def main():
    """Entry point."""
    idle_shutdown = os.getenv("IDLE_SHUTDOWN_MINUTES")
    idle_minutes = float(idle_shutdown) if idle_shutdown else None

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(run_result_storage_worker(idle_shutdown_minutes=idle_minutes))


if __name__ == "__main__":
    main()
