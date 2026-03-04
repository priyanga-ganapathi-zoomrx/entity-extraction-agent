"""Extraction Progress Worker - Updates entity_mapping SQL tables.

This worker:
- Polls the ENTITY_MAPPING_PROGRESS task queue
- Executes update_extraction_progress to track workflow status in SQL

Usage:
    python -m src.temporal.workers.extraction_progress_worker

    # With idle shutdown (env var)
    IDLE_SHUTDOWN_MINUTES=5 python -m src.temporal.workers.extraction_progress_worker
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from src.temporal.config import TaskQueues, WorkerSettings
from src.temporal.activities.extraction_progress import update_extraction_progress
from src.temporal.workers.base import run_worker

logger = logging.getLogger(__name__)


async def run_extraction_progress_worker(idle_shutdown_minutes: float | None = None) -> None:
    """Run the extraction progress worker."""
    settings = WorkerSettings.ENTITY_MAPPING_PROGRESS

    logger.info("Starting Extraction Progress Worker")

    await run_worker(
        task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
        workflows=None,
        activities=[update_extraction_progress],
        max_concurrent_activities=settings.get("max_concurrent_activities", 20),
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
    asyncio.run(run_extraction_progress_worker(idle_shutdown_minutes=idle_minutes))


if __name__ == "__main__":
    main()
