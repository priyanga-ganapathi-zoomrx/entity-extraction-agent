"""Temporal workers for abstract extraction.

This module provides workers for each task queue:
- WorkflowWorker: Orchestrates entity workflows with signal-based pause/resume
- ResultStorageWorker: Saves step outputs to GCS for admin portal download
- ExtractionProgressWorker: Updates entity_mapping SQL tables with progress
- DrugWorker: Executes drug extraction/validation activities
- DrugClassWorker: Executes drug class pipeline activities
- IndicationExtractionWorker: Executes indication extraction activities
- IndicationValidationWorker: Executes indication validation (slow, Sonnet 4.5)

Best Practice: Run separate workers per task queue to:
- Enable independent scaling
- Isolate slow activities from fast ones
- Apply different concurrency settings per workload
"""

from src.temporal.workers.base import run_worker
from src.temporal.workers.workflow_worker import run_workflow_worker
from src.temporal.workers.result_storage_worker import run_result_storage_worker
from src.temporal.workers.extraction_progress_worker import run_extraction_progress_worker
from src.temporal.workers.drug_worker import run_drug_worker
from src.temporal.workers.drug_class_worker import run_drug_class_worker
from src.temporal.workers.indication_extraction_worker import run_indication_extraction_worker
from src.temporal.workers.indication_validation_worker import run_indication_validation_worker

__all__ = [
    # Utilities
    "run_worker",
    # Worker runners
    "run_workflow_worker",
    "run_result_storage_worker",
    "run_extraction_progress_worker",
    "run_drug_worker",
    "run_drug_class_worker",
    "run_indication_extraction_worker",
    "run_indication_validation_worker",
]
