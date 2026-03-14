"""Temporal configuration for abstract extraction workflows.

This module defines:
- Task queue names for routing work to appropriate workers
- Timeout configurations for different activity types
- Retry policies following Temporal best practices
- Worker settings for concurrency control

Task Queue Design:
- WORKFLOWS: Lightweight orchestration only
- RESULT_STORAGE: GCS uploads for step output files (download from admin portal)
- ENTITY_MAPPING_PROGRESS: SQL status updates to entity_mapping tables
- DRUG: Drug extraction LLM activities
- DRUG_CLASS: Drug class classification pipeline
- INDICATION_EXTRACTION: Fast indication extraction
- INDICATION_VALIDATION: Slow validation (Sonnet 4.5)

Best Practices Applied:
- Separate queues by workload characteristics (latency, resource needs)
- LLM queues separated by latency profile
- Non-retryable errors for validation/parsing failures
"""

import os
from datetime import timedelta

from dotenv import load_dotenv
from temporalio.common import RetryPolicy

load_dotenv()

# =============================================================================
# ENVIRONMENT / CONNECTION
# =============================================================================

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")


# =============================================================================
# TASK QUEUES
# =============================================================================
# Design principles:
# - Workflows on dedicated queue (lightweight orchestration)
# - Checkpoint activities on shared queue (fast storage, cross-cutting)
# - LLM activities grouped by latency profile and entity type
# - Slow activities isolated to prevent blocking fast ones


class TaskQueues:
    """Task queue constants for consistent routing."""
    
    # Workflow orchestration - no activities, just coordination
    WORKFLOWS = "extraction-workflows"
    
    # Result storage operations - saves step outputs to GCS for download
    RESULT_STORAGE = "result-storage"
    
    # Extraction progress tracking - updates entity_mapping SQL tables
    ENTITY_MAPPING_PROGRESS = "entity-mapping-progress"
    
    # Drug pipeline activities
    DRUG = "drug-activities"
    DRUG_CLASS = "drug-class-activities"
    
    # Indication pipeline activities (separated by latency)
    INDICATION_EXTRACTION = "indication-extraction"
    INDICATION_VALIDATION = "indication-validation-slow"


# =============================================================================
# TIMEOUTS
# =============================================================================
# Timeout categories by activity type:
# - STORAGE: Fast local/GCS operations (<1s typical)
# - FAST_LLM: GPT-4 calls (5-15s typical)
# - SLOW_LLM: Sonnet 4.5 calls (30-60s typical)
# - SEARCH: External search APIs (2-10s typical)
#
# Set timeouts with buffer for retries and network variability.


class Timeouts:
    """Timeout configurations by activity type."""
    
    # Result storage activities - GCS upload (typically <1s)
    RESULT_STORAGE = timedelta(minutes=1)
    
    # Extraction progress SQL updates (allow for cold-start connection pool)
    ENTITY_MAPPING_PROGRESS = timedelta(minutes=3)
    
    # Fast LLM activities - Gemini (typically 5-15s, preview models can spike to 90s+)
    FAST_LLM = timedelta(minutes=4)
    
    # Slow LLM activities - Sonnet 4.5 (typically 30-60s)
    SLOW_LLM = timedelta(minutes=5)
    
    # Search activities - Tavily API (typically 2-10s)
    SEARCH = timedelta(seconds=45)
    
    # Batch finalization — reads step JSONs from GCS for all sessions in batch,
    # generates XLSX. drug_class with 20k sessions = ~120k GCS reads (~50 min).
    BATCH_FINALIZATION = timedelta(minutes=60)

    # Workflow execution timeout — entity workflows can pause for days waiting
    # for a retry signal, so this must be long.
    WORKFLOW_EXECUTION = timedelta(hours=72)
    
    # Workflow run timeout (single run including pause time)
    WORKFLOW_RUN = timedelta(hours=72)


# =============================================================================
# RETRY POLICIES
# =============================================================================
# Retry design:
# - Storage: Quick retries for transient failures
# - Fast LLM: Moderate retries with backoff
# - Slow LLM: Fewer retries (expensive)
# - Search: Quick retries for rate limits
#
# Non-retryable errors: validation failures, bad input data


class RetryPolicies:
    """Retry policies by activity type."""
    
    # Result storage activities - quick retries for transient GCS failures
    RESULT_STORAGE = RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=10),
        maximum_attempts=3,
        non_retryable_error_types=[
            "ValueError",
            "PermissionError",
        ],
    )
    
    # Extraction progress SQL updates - handle transient DB unavailability
    ENTITY_MAPPING_PROGRESS = RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=60),
        maximum_attempts=5,
        non_retryable_error_types=[
            "ValueError",
            "IntegrityError",
        ],
    )
    
    # Fast LLM activities - moderate retries
    FAST_LLM = RetryPolicy(
        initial_interval=timedelta(seconds=5),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=30),
        maximum_attempts=3,
        non_retryable_error_types=[
            "ValueError",
            "ValidationError",
        ],
    )
    
    # Slow LLM activities - fewer retries (expensive, high latency)
    SLOW_LLM = RetryPolicy(
        initial_interval=timedelta(seconds=15),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=1),
        maximum_attempts=2,
        non_retryable_error_types=[
            "ValueError",
            "ValidationError",
        ],
    )
    
    # Batch finalization — GCS reads + XLSX generation + upload
    BATCH_FINALIZATION = RetryPolicy(
        initial_interval=timedelta(seconds=10),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(minutes=2),
        maximum_attempts=3,
        non_retryable_error_types=[
            "ValueError",
        ],
    )

    # Search activities - quick retries for rate limits
    SEARCH = RetryPolicy(
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=15),
        maximum_attempts=3,
        non_retryable_error_types=[
            "ValueError",
        ],
    )


# =============================================================================
# WORKER SETTINGS
# =============================================================================
# Worker configuration by queue:
# - Workflows: High concurrency (lightweight coordination)
# - Result Storage: Moderate concurrency (fast GCS I/O bound)
# - Extraction Progress: Moderate concurrency (SQL writes)
# - LLM queues: Tuned for API rate limits
#
# Each queue should have its own dedicated worker process.


class WorkerSettings:
    """Worker configuration per task queue."""
    
    # Workflow worker - lightweight orchestration
    WORKFLOWS = {
        "max_concurrent_workflow_tasks": 100,
        "max_cached_workflows": 50,
    }
    
    # Result storage worker - GCS uploads (I/O bound, can be high)
    RESULT_STORAGE = {
        "max_concurrent_activities": 30,
    }
    
    # Extraction progress worker - SQL updates (I/O bound)
    ENTITY_MAPPING_PROGRESS = {
        "max_concurrent_activities": 20,
    }
    
    # Drug activities - fast LLM calls
    DRUG = {
        "max_concurrent_activities": 15,
    }
    
    # Drug class activities - multi-step pipeline
    DRUG_CLASS = {
        "max_concurrent_activities": 10,
    }
    
    # Indication extraction - fast LLM calls
    INDICATION_EXTRACTION = {
        "max_concurrent_activities": 15,
    }
    
    # Indication validation - slow (Sonnet 4.5)
    INDICATION_VALIDATION = {
        "max_concurrent_activities": 10,
    }
