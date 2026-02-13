"""Temporal client for batch abstract extraction.

This module provides:
- Batch workflow execution with controlled concurrency
- CSV loading from local or GCS storage
- Three-tier status reporting (success / partial_success / failed)
- Retry CSV generation for failed/partial abstracts

Usage:
    # Run batch extraction
    python -m src.temporal.client --input data/abstracts.csv --storage_path data/output

    # Retry failed items from a previous batch
    python -m src.temporal.client --input data/output/failed_20260206_123456.csv --storage_path data/output

For workflow status inspection, use the Temporal UI or CLI:
    temporal workflow list --query "ExecutionStatus = 'Failed'"
"""

import argparse
import asyncio
import csv
import io
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Optional

from temporalio.client import Client

from src.temporal.config import (
    TaskQueues,
    Timeouts,
    TEMPORAL_HOST,
    TEMPORAL_NAMESPACE,
)
from src.temporal.workflows import (
    AbstractExtractionWorkflow,
    AbstractExtractionInput,
    AbstractExtractionOutput,
)

logger = logging.getLogger(__name__)


# =============================================================================
# HELPERS
# =============================================================================

def generate_workflow_id(abstract_id: str) -> str:
    """Generate a consistent workflow ID from abstract ID."""
    return f"entity-extraction-{abstract_id}"


def _parse_firms(value: str) -> list[str]:
    """Parse firms from CSV value.
    
    Handles multiple formats:
    - JSON arrays: ["firm1", "firm2"]
    - Double semicolon separated: "firm1;;firm2"
    - Comma separated (backward compatibility): "firm1,firm2"
    - Single value: "firm1"
    
    Args:
        value: Raw string from CSV firm column
        
    Returns:
        List of firm names
    """
    if not value or not value.strip():
        return []
    
    value = value.strip()
    
    # Try JSON array first
    if value.startswith('['):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(f).strip() for f in parsed if f and str(f).strip()]
        except json.JSONDecodeError:
            pass
    
    # Use double semicolon as primary separator
    if ';;' in value:
        return [f.strip() for f in value.split(';;') if f.strip()]
    
    # Fall back to comma separated for backward compatibility
    if ',' in value:
        return [f.strip() for f in value.split(',') if f.strip()]
    
    # Single value
    return [value.strip()] if value.strip() else []


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BatchItem:
    """Single item for batch extraction."""
    abstract_id: str
    abstract_title: str
    session_title: str = ""
    full_abstract: str = ""
    firms: list[str] = None
    
    def __post_init__(self):
        if self.firms is None:
            self.firms = []


@dataclass
class BatchResult:
    """Result of a batch extraction item."""
    abstract_id: str
    abstract_title: str
    workflow_id: str
    output: Optional[AbstractExtractionOutput] = None
    error: Optional[str] = None
    
    @property
    def status(self) -> str:
        """Three-tier status: success, partial_success, or failed."""
        if self.error or self.output is None:
            return "failed"
        return self.output.status


# =============================================================================
# CSV LOADING
# =============================================================================

def load_batch_items(csv_path: str, limit: Optional[int] = None) -> list[BatchItem]:
    """Load CSV (local or GCS) and convert to BatchItem objects.
    
    Args:
        csv_path: Path to CSV file (local path or gs://bucket/path)
        limit: Optional limit on number of items to load
        
    Returns:
        List of BatchItem objects
        
    Expected CSV columns:
        - abstract_id (required)
        - abstract_title (required)
        - session_title (optional)
        - full_abstract (optional)
        - firm (optional)
    """
    # Load CSV content
    if csv_path.startswith("gs://"):
        from src.agents.core.storage import get_storage_client, parse_gcs_path
        bucket, prefix = parse_gcs_path(csv_path)
        storage = get_storage_client(f"gs://{bucket}")
        content = storage.download_text(prefix)
    else:
        content = Path(csv_path).read_text(encoding="utf-8-sig")
    
    # Parse CSV
    reader = csv.DictReader(io.StringIO(content))
    
    # Build column mapping (case-insensitive)
    fieldnames = list(reader.fieldnames or [])
    header_map = {h.lower().strip(): h for h in fieldnames}
    
    # Map expected columns
    id_col = header_map.get("abstract_id") or header_map.get("id")
    title_col = header_map.get("abstract_title") or header_map.get("title")
    session_col = header_map.get("session_title")
    abstract_col = header_map.get("full_abstract")
    firm_col = header_map.get("firm")
    
    items = []
    for row in reader:
        abstract_id = row.get(id_col, "") if id_col else ""
        if not abstract_id:
            continue  # Skip rows without ID
        
        # Parse firms (handles ;; separated, JSON array, comma separated)
        firm_value = str(row.get(firm_col, "")).strip() if firm_col else ""
        firms = _parse_firms(firm_value)
            
        items.append(BatchItem(
            abstract_id=str(abstract_id).strip(),
            abstract_title=str(row.get(title_col, "")).strip() if title_col else "",
            session_title=str(row.get(session_col, "")).strip() if session_col else "",
            full_abstract=str(row.get(abstract_col, "")).strip() if abstract_col else "",
            firms=firms,
        ))
        
        if limit and len(items) >= limit:
            break
    
    return items


# =============================================================================
# BATCH EXTRACTION
# =============================================================================

async def start_batch_extraction(
    items: list[BatchItem],
    max_concurrent: int = 50,
    storage_path: str = "",
    pipelines: list[str] = None,
) -> AsyncIterator[BatchResult]:
    """Start batch extraction with controlled concurrency.
    
    Processes items using a semaphore to limit concurrent workflows.
    Yields results as they complete (not necessarily in order).
    
    For workflow status, retries, and cancellation, use Temporal UI.
    
    Args:
        items: List of BatchItem objects to process
        max_concurrent: Maximum concurrent workflow executions (default 50)
        storage_path: Base path for checkpoints (gs://bucket/prefix or local path)
        pipelines: Which pipelines to run (default: all three)
        
    Yields:
        BatchResult objects as workflows complete
    """
    if pipelines is None:
        pipelines = ["drug", "drug_class", "indication"]
    # Connect to Temporal
    logger.info(f"Connecting to Temporal at {TEMPORAL_HOST}, namespace: {TEMPORAL_NAMESPACE}")
    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
    logger.info("Connected to Temporal")
    
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def process_item(item: BatchItem) -> BatchResult:
        """Process a single item with semaphore control."""
        async with semaphore:
            workflow_id = generate_workflow_id(item.abstract_id)
            
            try:
                # Build workflow input (single dataclass per Temporal best practice)
                input_data = AbstractExtractionInput(
                    abstract_id=item.abstract_id,
                    abstract_title=item.abstract_title,
                    session_title=item.session_title,
                    full_abstract=item.full_abstract,
                    firms=item.firms,
                    storage_path=storage_path,
                    pipelines=pipelines,
                )
                
                # Execute workflow and wait for result
                output = await client.execute_workflow(
                    AbstractExtractionWorkflow.run,
                    input_data,
                    id=workflow_id,
                    task_queue=TaskQueues.WORKFLOWS,
                    execution_timeout=Timeouts.WORKFLOW_EXECUTION,
                    run_timeout=Timeouts.WORKFLOW_RUN,
                )
                
                return BatchResult(
                    abstract_id=item.abstract_id,
                    abstract_title=item.abstract_title,
                    workflow_id=workflow_id,
                    output=output,
                )
                
            except Exception as e:
                logger.error(f"Batch item {item.abstract_id} failed: {e}")
                return BatchResult(
                    abstract_id=item.abstract_id,
                    abstract_title=item.abstract_title,
                    workflow_id=workflow_id,
                    error=str(e),
                )
    
    # Create tasks for all items
    tasks = [asyncio.create_task(process_item(item)) for item in items]
    
    logger.info(
        f"Started batch extraction for {len(items)} items "
        f"(max concurrent: {max_concurrent})"
    )
    
    # Yield results as they complete (order not guaranteed)
    for completed in asyncio.as_completed(tasks):
        yield await completed


# =============================================================================
# BATCH SUMMARY AND RETRY CSV WRITING
# =============================================================================

def _write_retry_csv(filepath: str, results: list[BatchResult]) -> None:
    """Write a CSV of abstract IDs for retry (supports local and GCS paths).
    
    Output has the same columns as the input CSV so it can be
    passed directly as --input.
    """
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["abstract_id", "abstract_title"])
    for r in results:
        writer.writerow([r.abstract_id, r.abstract_title])
    csv_content = output.getvalue()

    if filepath.startswith("gs://"):
        from src.agents.core.storage import parse_gcs_path, GCSStorageClient
        bucket, full_prefix = parse_gcs_path(filepath)
        if "/" in full_prefix:
            base_prefix = "/".join(full_prefix.split("/")[:-1])
            csv_filename = full_prefix.split("/")[-1]
        else:
            base_prefix = ""
            csv_filename = full_prefix
        storage = GCSStorageClient(bucket, base_prefix)
        storage.upload_text(csv_filename, csv_content)
    else:
        local_path = Path(filepath)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        with open(local_path, "w", newline="", encoding="utf-8") as f:
            f.write(csv_content)


def _print_batch_summary(
    total: int,
    success_results: list[BatchResult],
    partial_results: list[BatchResult],
    failed_results: list[BatchResult],
    storage_path: str,
) -> None:
    """Print batch summary and write retry CSVs if needed."""
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\n" + "=" * 50)
    print("Batch Complete")
    print("=" * 50)
    print(f"  Total:    {total}")
    print(f"  Success:  {len(success_results)}")
    print(f"  Partial:  {len(partial_results)}")
    print(f"  Failed:   {len(failed_results)}")

    # Determine base path for retry CSVs
    base_path = storage_path if storage_path else "data/output"

    # Build retry CSV paths (works for both local and GCS)
    if base_path.startswith("gs://"):
        failed_path = f"{base_path.rstrip('/')}/failed_{timestamp}.csv"
        partial_path = f"{base_path.rstrip('/')}/partial_{timestamp}.csv"
    else:
        failed_path = str(Path(base_path) / f"failed_{timestamp}.csv")
        partial_path = str(Path(base_path) / f"partial_{timestamp}.csv")

    # Write retry CSVs
    if failed_results:
        _write_retry_csv(failed_path, failed_results)
        print(f"\n  Retry (failed):  {failed_path} ({len(failed_results)} items)")

    if partial_results:
        _write_retry_csv(partial_path, partial_results)
        print(f"  Retry (partial): {partial_path} ({len(partial_results)} items)")

    if not failed_results and not partial_results:
        print("\n  All abstracts processed successfully!")

    print("=" * 50)


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

async def main():
    """CLI entry point for batch extraction."""
    parser = argparse.ArgumentParser(
        description="Start batch abstract extraction workflows",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run batch extraction
    python -m src.temporal.client --input data/abstracts.csv --storage_path data/output

    # Limit concurrency and abstracts
    python -m src.temporal.client --input data/abstracts.csv --storage_path data/output --max_concurrent 100 --limit 10

    # Run specific pipelines
    python -m src.temporal.client --input data/abstracts.csv --storage_path data/output --pipelines drug,indication

    # Retry failed items from a previous batch
    python -m src.temporal.client --input data/output/failed_20260206_123456.csv --storage_path data/output
        """,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="CSV path (local or gs://bucket/path)",
    )
    parser.add_argument(
        "--max_concurrent",
        type=int,
        default=50,
        help="Maximum concurrent workflows (default: 50)",
    )
    parser.add_argument(
        "--storage_path",
        default="",
        help="Base path for checkpoints (gs://bucket/prefix or local path)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of abstracts to process (for testing)",
    )
    parser.add_argument(
        "--pipelines",
        default="drug,drug_class,indication",
        help="Comma-separated pipelines to run (default: drug,drug_class,indication)",
    )
    args = parser.parse_args()

    # Parse pipelines
    pipelines = [p.strip() for p in args.pipelines.split(",") if p.strip()]
    valid_pipelines = {"drug", "drug_class", "indication"}
    invalid = set(pipelines) - valid_pipelines
    if invalid:
        print(f"Invalid pipeline(s): {invalid}. Valid options: {valid_pipelines}")
        return

    # Load items from CSV
    print(f"Loading abstracts from {args.input}...")
    items = load_batch_items(args.input, args.limit)
    print(f"Loaded {len(items)} abstracts")

    if not items:
        print("No items to process")
        return

    # Run batch extraction
    print(f"Starting batch extraction (max_concurrent: {args.max_concurrent})...")
    print(f"Pipelines: {pipelines}")
    if args.storage_path:
        print(f"Checkpoints: {args.storage_path}")

    # --- EMS: batch_started ---
    from src.agents.core.ems_logger import get_logger as _get_ems_logger

    ems_logger = _get_ems_logger("batch")
    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_start = time.time()

    ems_logger.info(
        "batch_started",
        batch_id=batch_id,
        total_items=len(items),
        max_concurrent=args.max_concurrent,
        pipelines=pipelines,
        input_csv_path=args.input,
        storage_path=args.storage_path,
    )

    success_results = []
    partial_results = []
    failed_results = []

    async for result in start_batch_extraction(
        items,
        max_concurrent=args.max_concurrent,
        storage_path=args.storage_path,
        pipelines=pipelines,
    ):
        status = result.status
        if status == "success":
            success_results.append(result)
            print(f"  [OK]      {result.abstract_id}")
        elif status == "partial_success":
            partial_results.append(result)
            errors = result.output.errors if result.output else []
            print(f"  [PARTIAL] {result.abstract_id}: {errors}")
        else:
            failed_results.append(result)
            print(f"  [FAILED]  {result.abstract_id}: {result.error}")

    # --- EMS: batch_completed ---
    duration_ms = int((time.time() - batch_start) * 1000)

    if not failed_results and not partial_results:
        batch_outcome = "success"
    elif failed_results and not success_results and not partial_results:
        batch_outcome = "failure"
    else:
        batch_outcome = "partial_success"

    ems_logger.info(
        "batch_completed",
        batch_id=batch_id,
        total_items=len(items),
        success_count=len(success_results),
        partial_count=len(partial_results),
        failed_count=len(failed_results),
        outcome=batch_outcome,
        duration_ms=duration_ms,
    )

    _print_batch_summary(
        total=len(items),
        success_results=success_results,
        partial_results=partial_results,
        failed_results=failed_results,
        storage_path=args.storage_path,
    )


if __name__ == "__main__":
    asyncio.run(main())
