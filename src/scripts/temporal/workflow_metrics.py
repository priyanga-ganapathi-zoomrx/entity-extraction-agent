#!/usr/bin/env python3
"""Workflow Metrics Exporter for Temporal Workflow Outputs.

Aggregates all status.json files across abstract outputs to produce an XLSX
with one row per abstract containing:
- Overall workflow status, timing, and error info
- Per-step status, LLM call counts, and token consumption
- Aggregated pipeline-level metrics

No input CSV required -- discovers abstract IDs from the data directory.

Usage:
    # Local storage
    python -m src.scripts.temporal.workflow_metrics \
        --data_dir data/output \
        --output workflow_metrics.xlsx

    # GCS storage
    python -m src.scripts.temporal.workflow_metrics \
        --data_dir gs://bucket/Conference \
        --output gs://bucket/Conference/workflow_metrics.xlsx

    # With limit
    python -m src.scripts.temporal.workflow_metrics \
        --data_dir data/output \
        --output workflow_metrics.xlsx \
        --limit 100
"""

import argparse
from datetime import datetime

from tqdm import tqdm

from src.scripts.temporal.utils import (
    export_xlsx,
    get_data_storage,
    list_abstract_ids,
    load_status,
    to_json_string,
)


# =============================================================================
# STEP DEFINITIONS
# =============================================================================

# Ordered list of (status_json_path, column_prefix) for each step
# status_json_path is dot-separated path into status.json
STEPS = [
    ("drug.extraction", "drug_extraction"),
    ("drug.validation", "drug_validation"),
    ("drug_class.step1_regimen", "drug_class_step1"),
    ("drug_class.step2_extraction", "drug_class_step2"),
    ("drug_class.step3_selection", "drug_class_step3"),
    ("drug_class.step4_explicit", "drug_class_step4"),
    ("drug_class.step5_consolidation", "drug_class_step5"),
    ("drug_class.validation", "drug_class_validation"),
    ("indication.extraction", "indication_extraction"),
    ("indication.validation", "indication_validation"),
]


def _get_nested(data: dict, dotted_path: str) -> dict:
    """Navigate a nested dict by dotted path (e.g., 'drug.extraction').

    Returns empty dict if any key is missing.
    """
    current = data
    for key in dotted_path.split("."):
        if not isinstance(current, dict):
            return {}
        current = current.get(key, {})
    return current if isinstance(current, dict) else {}


# =============================================================================
# FIELD NAMES
# =============================================================================


def get_fieldnames() -> list[str]:
    """Build the ordered list of CSV column names."""
    columns = [
        "abstract_id",
        "abstract_title",
        "overall_status",
        "last_updated",
        "duration_seconds",
        "total_llm_calls",
        "total_input_tokens",
        "total_output_tokens",
    ]

    # Per-step columns
    for _, col_prefix in STEPS:
        columns.extend([
            f"{col_prefix}_status",
            f"{col_prefix}_llm_calls",
            f"{col_prefix}_input_tokens",
            f"{col_prefix}_output_tokens",
            f"{col_prefix}_tokens",
        ])

    columns.append("errors")

    return columns


# =============================================================================
# ROW TRANSFORM
# =============================================================================


def status_to_row(status: dict) -> dict:
    """Transform a single status.json into a flat CSV row dict.

    Args:
        status: Parsed status.json dict

    Returns:
        Flat dict with column names matching get_fieldnames()
    """
    metrics = status.get("metrics", {})

    row = {
        "abstract_id": status.get("abstract_id", ""),
        "abstract_title": status.get("abstract_title", ""),
        "overall_status": status.get("status", ""),
        "last_updated": status.get("last_updated", ""),
        "duration_seconds": metrics.get("duration_seconds", ""),
        "total_llm_calls": metrics.get("llm_calls", ""),
        "total_input_tokens": metrics.get("input_tokens", ""),
        "total_output_tokens": metrics.get("output_tokens", ""),
    }

    # Per-step metrics
    for status_path, col_prefix in STEPS:
        step_data = _get_nested(status, status_path)
        row[f"{col_prefix}_status"] = step_data.get("status", "")
        row[f"{col_prefix}_llm_calls"] = step_data.get("llm_calls", "")
        row[f"{col_prefix}_input_tokens"] = step_data.get("input_tokens", "")
        row[f"{col_prefix}_output_tokens"] = step_data.get("output_tokens", "")
        row[f"{col_prefix}_tokens"] = step_data.get("tokens", "")

    # Errors
    errors = status.get("errors", [])
    row["errors"] = to_json_string(errors) if errors else ""

    return row


# =============================================================================
# MAIN
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Workflow Metrics Exporter for Temporal Outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Local storage
    python -m src.scripts.temporal.workflow_metrics \\
        --data_dir data/output \\
        --output workflow_metrics.xlsx

    # GCS storage
    python -m src.scripts.temporal.workflow_metrics \\
        --data_dir gs://bucket/Conference \\
        --output gs://bucket/Conference/workflow_metrics.xlsx
        """,
    )

    parser.add_argument(
        "--data_dir",
        default="data/output",
        help="Temporal workflow data directory (local or gs://bucket/prefix)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output XLSX file path (local or gs://). Default: workflow_metrics_<timestamp>.xlsx",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of abstracts to process",
    )

    args = parser.parse_args()

    # Default output filename with timestamp
    if not args.output:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        args.output = f"workflow_metrics_{timestamp}.xlsx"

    print("Workflow Metrics Exporter")
    print("=" * 60)
    print(f"Data dir: {args.data_dir}")
    print(f"Output:   {args.output}")
    if args.limit:
        print(f"Limit:    {args.limit}")
    print()

    # Discover abstract IDs
    print("Discovering abstract IDs...")
    abstract_ids = list_abstract_ids(args.data_dir)

    if args.limit:
        abstract_ids = abstract_ids[: args.limit]

    print(f"Found {len(abstract_ids)} abstracts")
    print()

    if not abstract_ids:
        print("No abstracts found. Check --data_dir path.")
        return

    # Load status and build rows
    storage = get_data_storage(args.data_dir)
    rows = []
    missing_count = 0

    for abstract_id in tqdm(abstract_ids, desc="Loading status files", unit="abstract"):
        status = load_status(storage, abstract_id)
        if status:
            rows.append(status_to_row(status))
        else:
            missing_count += 1

    if missing_count:
        print(f"\nWarning: {missing_count} abstracts had no status.json")

    # Export XLSX
    fieldnames = get_fieldnames()
    export_xlsx(rows, fieldnames, args.output)

    print(f"\nDone. Exported {len(rows)} rows.")


if __name__ == "__main__":
    main()
