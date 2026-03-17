"""Temporal configuration for abstract extraction workflows.

This module defines:
- Task queue names for routing work to appropriate workers
- Timeout configurations for different activity types
- Retry policies following Temporal best practices
- Worker settings for concurrency control

Task Queue Design:
- WORKFLOWS: Lightweight orchestration only
- CHECKPOINT: Cross-cutting storage operations (load/save status, step outputs)
- DRUG: Drug extraction LLM activities
- DRUG_CLASS: Drug class classification pipeline
- INDICATION_EXTRACTION: Fast indication extraction (GPT-4)
- INDICATION_VALIDATION: Slow validation (Sonnet 4.5)

Best Practices Applied:
- Separate queues by workload characteristics (latency, resource needs)
- Checkpoint queue shared across all pipelines for storage ops
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
    
    # Checkpoint/storage operations - fast, cross-cutting, used by all pipelines
    CHECKPOINT = "checkpoint-storage"
    
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
    
    # Storage/checkpoint activities (typically <1s)
    STORAGE = timedelta(minutes=3)
    
    # Fast LLM activities - Gemini (typically 5-15s, preview models can spike to 90s+)
    FAST_LLM = timedelta(minutes=4)
    
    # Slow LLM activities - Sonnet 4.5 (typically 30-60s)
    SLOW_LLM = timedelta(minutes=5)
    
    # Search activities - Tavily API (typically 2-10s)
    SEARCH = timedelta(minutes=4)
    
    # Workflow execution timeout (entire abstract processing)
    WORKFLOW_EXECUTION = timedelta(minutes=60)
    
    # Workflow run timeout (single run before continue-as-new)
    WORKFLOW_RUN = timedelta(minutes=60)


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
    
    # Storage activities - quick retries for transient failures
    STORAGE = RetryPolicy(
        initial_interval=timedelta(seconds=1),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=10),
        maximum_attempts=3,
        non_retryable_error_types=[
            "ValueError",
            "PermissionError",
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
# - Checkpoint: Moderate concurrency (fast I/O bound)
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
    
    # Checkpoint worker - fast storage operations
    CHECKPOINT = {
        "max_concurrent_activities": 30,  # I/O bound, can be high
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
