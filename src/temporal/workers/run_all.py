"""Run All Workers - Development helper to run all workers in one process.

This script runs all workers concurrently in a single process.
Useful for local development and testing.

For production, run each worker separately for:
- Independent scaling
- Better resource isolation
- Easier monitoring and deployment

Workers started:
1. Workflow worker - orchestration only (entity workflows with signals)
2. Result storage worker - GCS uploads for downloadable results
3. Extraction progress worker - SQL status updates (stub)
4. Drug worker - drug extraction/validation
5. Drug class worker - 5-step drug class pipeline
6. Indication extraction worker - fast LLM extraction
7. Indication validation worker - slow LLM validation

Usage:
    python -m src.temporal.workers.run_all
    
    # With idle shutdown (env var)
    IDLE_SHUTDOWN_MINUTES=5 python -m src.temporal.workers.run_all

Environment Variables:
    TEMPORAL_HOST: Temporal server address (default: localhost:7233)
    TEMPORAL_NAMESPACE: Temporal namespace (default: default)
    IDLE_SHUTDOWN_MINUTES: Auto-shutdown after N minutes of inactivity (optional)
"""

import asyncio
import concurrent.futures
import logging
import os
import signal
from datetime import timedelta
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from temporalio.client import Client
from temporalio.worker import Worker

from src.temporal.config import (
    TaskQueues,
    WorkerSettings,
    TEMPORAL_HOST,
    TEMPORAL_NAMESPACE,
)
from src.temporal.workflows import AbstractExtractionWorkflow
from src.temporal.activities import (
    RESULT_STORAGE_ACTIVITIES,
    EXTRACTION_PROGRESS_ACTIVITIES,
    DRUG_ACTIVITIES,
    DRUG_CLASS_ACTIVITIES,
    INDICATION_ACTIVITIES,
)

logger = logging.getLogger(__name__)


async def run_all_workers(
    client: Optional[Client] = None,
    idle_shutdown_minutes: Optional[float] = None,
    graceful_shutdown_timeout: timedelta = timedelta(seconds=60),
) -> None:
    """Run all workers concurrently in a single process.

    Note: For production, run each worker separately using the individual
    worker scripts. This combined approach is for development only.

    Args:
        client: Optional pre-existing Temporal client
        idle_shutdown_minutes: Auto-shutdown after N minutes of inactivity
        graceful_shutdown_timeout: Time to wait for in-flight activities on shutdown
    """
    # Create client if not provided
    if client is None:
        logger.info(f"Connecting to Temporal at {TEMPORAL_HOST}")
        client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
        logger.info("Connected to Temporal")

    # Setup idle tracker if idle shutdown is enabled
    watchdog_task = None
    if idle_shutdown_minutes is not None and idle_shutdown_minutes > 0:
        from src.temporal.idle_shutdown import IdleTracker, set_tracker
        tracker = IdleTracker()
        set_tracker(tracker)
        logger.info(f"Idle shutdown enabled: {idle_shutdown_minutes} minutes")

    # Create thread pool for activities
    total_max_activities = (
        WorkerSettings.RESULT_STORAGE.get("max_concurrent_activities", 30) +
        WorkerSettings.ENTITY_MAPPING_PROGRESS.get("max_concurrent_activities", 20) +
        WorkerSettings.DRUG.get("max_concurrent_activities", 15) +
        WorkerSettings.DRUG_CLASS.get("max_concurrent_activities", 10) +
        WorkerSettings.INDICATION_EXTRACTION.get("max_concurrent_activities", 20) +
        WorkerSettings.INDICATION_VALIDATION.get("max_concurrent_activities", 5)
    )

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=total_max_activities
    ) as activity_executor:

        # Create workers for each task queue
        workers = []

        # 1. Workflow worker (orchestration only, no activities)
        workflow_settings = WorkerSettings.WORKFLOWS
        workflow_worker = Worker(
            client,
            task_queue=TaskQueues.WORKFLOWS,
            workflows=[AbstractExtractionWorkflow],
            max_concurrent_workflow_tasks=workflow_settings.get(
                "max_concurrent_workflow_tasks", 100
            ),
            max_cached_workflows=workflow_settings.get("max_cached_workflows", 50),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("workflows", workflow_worker))

        # 2. Result storage worker (GCS uploads for downloadable results)
        result_storage_settings = WorkerSettings.RESULT_STORAGE
        result_storage_worker = Worker(
            client,
            task_queue=TaskQueues.RESULT_STORAGE,
            activities=RESULT_STORAGE_ACTIVITIES,
            activity_executor=activity_executor,
            max_concurrent_activities=result_storage_settings.get(
                "max_concurrent_activities", 30
            ),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("result_storage", result_storage_worker))

        # 3. Extraction progress worker (SQL status updates)
        progress_settings = WorkerSettings.ENTITY_MAPPING_PROGRESS
        progress_worker = Worker(
            client,
            task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
            activities=EXTRACTION_PROGRESS_ACTIVITIES,
            activity_executor=activity_executor,
            max_concurrent_activities=progress_settings.get(
                "max_concurrent_activities", 20
            ),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("extraction_progress", progress_worker))

        # 4. Drug activities worker
        drug_settings = WorkerSettings.DRUG
        drug_worker = Worker(
            client,
            task_queue=TaskQueues.DRUG,
            activities=DRUG_ACTIVITIES,
            activity_executor=activity_executor,
            max_concurrent_activities=drug_settings.get(
                "max_concurrent_activities", 15
            ),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("drug", drug_worker))

        # 5. Drug class activities worker
        drug_class_settings = WorkerSettings.DRUG_CLASS
        drug_class_worker = Worker(
            client,
            task_queue=TaskQueues.DRUG_CLASS,
            activities=DRUG_CLASS_ACTIVITIES,
            activity_executor=activity_executor,
            max_concurrent_activities=drug_class_settings.get(
                "max_concurrent_activities", 10
            ),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("drug_class", drug_class_worker))

        # 6. Indication extraction worker
        indication_extraction_settings = WorkerSettings.INDICATION_EXTRACTION
        indication_extraction_worker = Worker(
            client,
            task_queue=TaskQueues.INDICATION_EXTRACTION,
            activities=[INDICATION_ACTIVITIES[0]],  # extract_indication only
            activity_executor=activity_executor,
            max_concurrent_activities=indication_extraction_settings.get(
                "max_concurrent_activities", 20
            ),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("indication_extraction", indication_extraction_worker))

        # 7. Indication validation worker (slow)
        indication_validation_settings = WorkerSettings.INDICATION_VALIDATION
        indication_validation_worker = Worker(
            client,
            task_queue=TaskQueues.INDICATION_VALIDATION,
            activities=[INDICATION_ACTIVITIES[1]],  # validate_indication only
            activity_executor=activity_executor,
            max_concurrent_activities=indication_validation_settings.get(
                "max_concurrent_activities", 5
            ),
            graceful_shutdown_timeout=graceful_shutdown_timeout,
        )
        workers.append(("indication_validation", indication_validation_worker))

        # Log worker configuration
        logger.info("=" * 60)
        logger.info("Starting All Workers (Development Mode)")
        logger.info("=" * 60)
        for name, worker in workers:
            logger.info(f"  - {name}: {worker.task_queue}")
        logger.info(f"  Total thread pool workers: {total_max_activities}")
        if idle_shutdown_minutes:
            logger.info(f"  Idle shutdown: {idle_shutdown_minutes} minutes")
        logger.info("=" * 60)
        logger.info("Press Ctrl+C to shutdown gracefully")

        # Setup shutdown handling
        shutdown_triggered = asyncio.Event()

        async def trigger_shutdown():
            """Trigger shutdown of all workers."""
            if shutdown_triggered.is_set():
                return
            shutdown_triggered.set()
            logger.info("Shutting down all workers...")
            await asyncio.gather(
                *[w.shutdown() for _, w in workers],
                return_exceptions=True,
            )

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(_handle_signal(s, trigger_shutdown))
            )

        # Start idle watchdog if enabled
        if idle_shutdown_minutes is not None and idle_shutdown_minutes > 0:
            from src.temporal.idle_shutdown import get_tracker
            
            async def watchdog_with_all_shutdown():
                """Idle watchdog that shuts down all workers."""
                tracker = get_tracker()
                idle_secs = idle_shutdown_minutes * 60
                
                while not shutdown_triggered.is_set():
                    await asyncio.sleep(30)
                    if tracker.is_idle(idle_secs):
                        elapsed = tracker.time_since_last_activity()
                        logger.info(
                            f"All workers idle for {elapsed:.1f}s "
                            f"(threshold: {idle_secs}s), shutting down..."
                        )
                        await trigger_shutdown()
                        return
            
            watchdog_task = asyncio.create_task(watchdog_with_all_shutdown())

        # Run all workers concurrently
        async def run_single_worker(name: str, worker: Worker):
            async with worker:
                logger.info(f"Worker '{name}' started")
                await shutdown_triggered.wait()
                logger.info(f"Worker '{name}' shutting down...")

        await asyncio.gather(
            *[run_single_worker(name, worker) for name, worker in workers]
        )

        # Clean up watchdog
        if watchdog_task is not None and not watchdog_task.done():
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

        logger.info("All workers shut down gracefully")


async def _handle_signal(sig: signal.Signals, shutdown_fn) -> None:
    """Handle shutdown signal."""
    logger.info(f"Received signal {sig.name}, initiating shutdown...")
    await shutdown_fn()


def main():
    """Entry point."""
    idle_shutdown = os.getenv("IDLE_SHUTDOWN_MINUTES")
    idle_minutes = float(idle_shutdown) if idle_shutdown else None
    
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    
    asyncio.run(run_all_workers(idle_shutdown_minutes=idle_minutes))


if __name__ == "__main__":
    main()
