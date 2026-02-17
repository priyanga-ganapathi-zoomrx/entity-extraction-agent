"""Workflow Worker - Handles workflow orchestration tasks.

This worker:
- Polls the WORKFLOWS task queue
- Executes the AbstractExtractionWorkflow (single flat workflow)
- Lightweight orchestration only (no activities)

Best Practice: Workflow workers are separate from activity workers
to allow independent scaling and avoid resource contention.

Note: Checkpoint activities run on a dedicated CHECKPOINT queue,
not on this workflow queue.

Usage:
    python -m src.temporal.workers.workflow_worker
    
    # With idle shutdown (env var)
    IDLE_SHUTDOWN_MINUTES=5 python -m src.temporal.workers.workflow_worker
"""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

from src.temporal.config import TaskQueues, WorkerSettings
from src.temporal.workflows import AbstractExtractionWorkflow
from src.temporal.workers.base import run_worker

logger = logging.getLogger(__name__)


async def run_workflow_worker(idle_shutdown_minutes: float | None = None) -> None:
    """Run the workflow worker.

    This worker handles workflow orchestration for:
    - AbstractExtractionWorkflow: Main extraction workflow (flat, no child workflows)

    No activities run on this worker - it's purely for orchestration.
    Checkpoint activities have their own dedicated worker.

    Args:
        idle_shutdown_minutes: Auto-shutdown after N minutes of inactivity

    Configuration from WorkerSettings.WORKFLOWS:
    - max_concurrent_workflow_tasks: 100
    - max_cached_workflows: 50
    
    Note: Idle shutdown for workflow-only workers measures from startup time
    since there are no activities to track. Worker will shut down if no
    workflow tasks are received within the idle period.
    """
    settings = WorkerSettings.WORKFLOWS

    logger.info("Starting Workflow Worker")

    await run_worker(
        task_queue=TaskQueues.WORKFLOWS,
        workflows=[
            AbstractExtractionWorkflow,
        ],
        activities=None,  # No activities - orchestration only
        max_concurrent_workflow_tasks=settings.get("max_concurrent_workflow_tasks", 100),
        max_cached_workflows=settings.get("max_cached_workflows", 50),
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
    asyncio.run(run_workflow_worker(idle_shutdown_minutes=idle_minutes))


if __name__ == "__main__":
    main()
