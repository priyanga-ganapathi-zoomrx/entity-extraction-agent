#!/usr/bin/env python3
"""Indication Extraction & Validation Exporter for Temporal Workflow Outputs.

Reads Temporal workflow output files (indication_extraction.json,
indication_validation.json) and combines them with input CSV data
into a single QA XLSX.

Output columns match the step-centric indication validation_processor.py
(save_results_csv) for consistency.

Usage:
    # Local storage
    python -m src.scripts.temporal.indication_exporter \
        --input data/abstract_titles.csv \
        --data_dir data/output \
        --output indication_export.xlsx

    # GCS storage
    python -m src.scripts.temporal.indication_exporter \
        --input gs://bucket/Conference/abstract_titles.csv \
        --data_dir gs://bucket/Conference \
        --output gs://bucket/Conference/indication_export.xlsx

    # With limit
    python -m src.scripts.temporal.indication_exporter \
        --input data/abstract_titles.csv \
        --data_dir data/output \
        --output indication_export.xlsx \
        --limit 100
"""

import argparse
from datetime import datetime

from tqdm import tqdm

from src.scripts.temporal.utils import (
    export_xlsx,
    get_abstract_id_column,
    get_data_storage,
    get_input_storage_and_filename,
    load_input_csv,
    load_status,
    load_step_output,
    to_json_string,
)


# =============================================================================
# COLUMN DEFINITIONS
# =============================================================================

EXTRACTION_COLUMNS = [
    "extraction_selected_source",
    "extraction_generated_indication",
    "extraction_reasoning",
    "extraction_rules_retrieved",  # JSON array
]

VALIDATION_COLUMNS = [
    "validation_status",
    "validation_issues_found",  # JSON array
    "validation_checks_performed",  # JSON object
    "validation_reasoning",
]


# =============================================================================
# TRANSFORMS
# =============================================================================


def transform_indication_extraction(extraction_data: dict) -> dict:
    """Transform indication_extraction.json to CSV columns.

    Args:
        extraction_data: Parsed indication_extraction.json

    Returns:
        Dict with extraction column values
    """
    if not extraction_data:
        return {col: "" for col in EXTRACTION_COLUMNS}

    return {
        "extraction_selected_source": extraction_data.get("selected_source", ""),
        "extraction_generated_indication": extraction_data.get("generated_indication", ""),
        "extraction_reasoning": extraction_data.get("reasoning", ""),
        "extraction_rules_retrieved": to_json_string(
            extraction_data.get("rules_retrieved", [])
        ),
    }


def transform_indication_validation(
    validation_data: dict,
    status: dict,
) -> dict:
    """Transform indication_validation.json to CSV columns.

    Falls back to status.json error info if validation file is missing.

    Args:
        validation_data: Parsed indication_validation.json (may be None)
        status: Parsed status.json for fallback error info

    Returns:
        Dict with validation column values
    """
    if validation_data:
        return {
            "validation_status": validation_data.get("validation_status", ""),
            "validation_issues_found": to_json_string(
                validation_data.get("issues_found", [])
            ),
            "validation_checks_performed": to_json_string(
                validation_data.get("checks_performed", {})
            ),
            "validation_reasoning": validation_data.get("validation_reasoning", ""),
        }

    # Fallback: check status.json for error info
    result = {col: "" for col in VALIDATION_COLUMNS}

    if status:
        indication = status.get("indication", {})
        validation_status = indication.get("validation", {})
        if validation_status.get("error"):
            result["validation_status"] = f"ERROR: {validation_status['error']}"
        elif validation_status.get("status") != "success":
            result["validation_status"] = "Validation not completed"
        else:
            result["validation_status"] = "Validation not completed"
    else:
        result["validation_status"] = "Validation not completed"

    return result


# =============================================================================
# PROCESSING
# =============================================================================


def process_abstracts(rows, fieldnames, data_storage) -> list[dict]:
    """Process all abstracts and build combined output rows.

    Args:
        rows: List of input CSV row dicts
        fieldnames: Column names from input CSV
        data_storage: Storage client for Temporal workflow data

    Returns:
        List of output row dicts
    """
    id_col = get_abstract_id_column(fieldnames)
    output_rows = []

    stats = {"extraction_found": 0, "validation_found": 0}

    for row in tqdm(rows, desc="Processing abstracts", unit="abstract"):
        abstract_id = row.get(id_col, "") if id_col else ""

        if not abstract_id:
            continue

        # Start with input columns
        output_row = dict(row)

        # Load data files
        extraction_data = load_step_output(data_storage, abstract_id, "indication_extraction")
        validation_data = load_step_output(data_storage, abstract_id, "indication_validation")
        status = load_status(data_storage, abstract_id)

        # Transform extraction
        extraction_cols = transform_indication_extraction(extraction_data)
        if extraction_data:
            stats["extraction_found"] += 1
        output_row.update(extraction_cols)

        # Transform validation
        validation_cols = transform_indication_validation(validation_data, status)
        if validation_data:
            stats["validation_found"] += 1
        output_row.update(validation_cols)

        output_rows.append(output_row)

    # Print stats
    total = len(rows)
    print(f"\nData availability:")
    print(f"  Indication extraction: {stats['extraction_found']}/{total}")
    print(f"  Indication validation: {stats['validation_found']}/{total}")

    return output_rows


# =============================================================================
# MAIN
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Indication Extraction & Validation Exporter for Temporal Outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Local storage
    python -m src.scripts.temporal.indication_exporter \\
        --input data/abstract_titles.csv \\
        --data_dir data/output \\
        --output indication_export.xlsx

    # GCS storage
    python -m src.scripts.temporal.indication_exporter \\
        --input gs://bucket/Conference/abstract_titles.csv \\
        --data_dir gs://bucket/Conference \\
        --output gs://bucket/Conference/indication_export.xlsx
        """,
    )

    parser.add_argument(
        "--input",
        default="data/abstract_titles.csv",
        help="Input CSV file path (local or gs://bucket/path)",
    )
    parser.add_argument(
        "--data_dir",
        default="data/output",
        help="Temporal workflow data directory (local or gs://bucket/prefix)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output XLSX file path (local or gs://). Default: indication_export_<timestamp>.xlsx",
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
        args.output = f"indication_export_{timestamp}.xlsx"

    print("Indication Extraction & Validation Exporter")
    print("=" * 60)
    print(f"Input:    {args.input}")
    print(f"Data dir: {args.data_dir}")
    print(f"Output:   {args.output}")
    if args.limit:
        print(f"Limit:    {args.limit}")
    print()

    # Load input CSV
    input_storage, csv_filename = get_input_storage_and_filename(args.input)
    print("Loading abstracts from CSV...")
    rows, fieldnames = load_input_csv(csv_filename, input_storage, args.limit)
    print(f"Loaded {len(rows)} abstracts")
    print(f"Input columns: {fieldnames}")
    print()

    if not rows:
        print("No abstracts to process.")
        return

    # Process
    data_storage = get_data_storage(args.data_dir)
    output_rows = process_abstracts(rows, fieldnames, data_storage)

    # Build output fieldnames
    output_fieldnames = fieldnames + EXTRACTION_COLUMNS + VALIDATION_COLUMNS

    # Export XLSX
    export_xlsx(output_rows, output_fieldnames, args.output)

    print(f"\nDone. Exported {len(output_rows)} rows.")


if __name__ == "__main__":
    main()
