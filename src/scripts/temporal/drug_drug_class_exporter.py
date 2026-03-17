#!/usr/bin/env python3
"""Combined Drug and Drug Class QA Exporter for Temporal Workflow Outputs.

Reads Temporal workflow output files and combines them with input CSV data
into a single XLSX for QA review.

Output columns match the step-centric drug_drug_class_exporter.py for
consistency. Key difference: reads from a single unified data directory
instead of separate drug/ and drug_class/ directories.

File mapping (Temporal output -> columns):
- drug_extraction.json       -> drug extraction columns
- drug_validation.json       -> drug validation columns
- drug_class_steps1_3.json   -> drug class steps 1, 2, 3 columns
- drug_class_step4.json      -> drug class step 4 columns
- drug_class_step5.json      -> drug class step 5 columns (may not exist)
- drug_class_validation.json -> drug class validation columns

Usage:
    # Local storage
    python -m src.scripts.temporal.drug_drug_class_exporter \
        --input data/abstract_titles.csv \
        --data_dir data/output \
        --output drug_drug_class_export.xlsx

    # GCS storage
    python -m src.scripts.temporal.drug_drug_class_exporter \
        --input gs://bucket/Conference/abstract_titles.csv \
        --data_dir gs://bucket/Conference \
        --output gs://bucket/Conference/drug_drug_class_export.xlsx

    # With limit
    python -m src.scripts.temporal.drug_drug_class_exporter \
        --input data/abstract_titles.csv \
        --data_dir data/output \
        --output drug_drug_class_export.xlsx \
        --limit 10
"""

import argparse
from datetime import datetime

from tqdm import tqdm

from src.scripts.temporal.utils import (
    export_xlsx,
    extract_combined_drug_classes,
    format_dict_as_key_value,
    format_dict_as_key_value_skip_empty,
    format_list_as_semicolons,
    get_abstract_id_column,
    get_data_storage,
    get_input_storage_and_filename,
    load_input_csv,
    load_step_output,
    to_json_string,
)


# =============================================================================
# OUTPUT COLUMNS (same as step-centric exporter)
# =============================================================================

NEW_COLUMNS = [
    # Drug extraction
    "drug_extraction_primary_drugs",
    "drug_extraction_secondary_drugs",
    "drug_extraction_comparator_drugs",
    "drug_extraction_reasoning",
    # Drug validation
    "drug_validation_status",
    "drug_validation_grounded_search_performed",
    "drug_validation_search_results",
    "drug_validation_missed_drugs",
    "drug_validation_issues_found",
    "drug_validation_reasoning",
    # Drug class step 1
    "drug_class_step1_drug_to_components",
    # Drug class step 2
    "drug_class_step2_drug_classes",
    "drug_class_step2_extraction_details",
    # Drug class step 3
    "drug_class_step3_selected_drug_classes",
    "drug_class_step3_reasoning",
    # Drug class step 4
    "drug_class_step4_explicit_drug_classes",
    "drug_class_step4_extraction_details",
    # Drug class step 5
    "drug_class_step5_refined_explicit_classes",
    "drug_class_step5_removed_classes",
    "drug_class_step5_reasoning",
    # Combined drug classes (step 3 + step 5)
    "drug_class_implicit_drug_classes",
    "drug_class_combined_all_classes",
    # Drug class validation
    "drug_class_validation_overall_status",
    "drug_class_validation_status",
    "drug_class_validation_reasoning",
    "drug_class_validation_checks_performed",
    "drug_class_validation_issues_found",
    "drug_class_validation_missed_drug_classes",
]


# =============================================================================
# DRUG EXTRACTION TRANSFORM
# =============================================================================


def transform_drug_extraction(extraction_data: dict) -> dict:
    """Transform drug_extraction.json to CSV columns.

    Temporal output uses lowercase keys (primary_drugs, secondary_drugs, etc.)
    vs step-centric which may use "Primary Drugs".

    Args:
        extraction_data: Parsed drug_extraction.json

    Returns:
        Dict with drug extraction column values
    """
    if not extraction_data:
        return {
            "drug_extraction_primary_drugs": "",
            "drug_extraction_secondary_drugs": "",
            "drug_extraction_comparator_drugs": "",
            "drug_extraction_reasoning": "",
        }

    # Temporal uses lowercase snake_case keys
    primary = extraction_data.get("primary_drugs", []) or extraction_data.get("Primary Drugs", [])
    secondary = extraction_data.get("secondary_drugs", []) or extraction_data.get("Secondary Drugs", [])
    comparator = extraction_data.get("comparator_drugs", []) or extraction_data.get("Comparator Drugs", [])
    reasoning = extraction_data.get("reasoning", []) or extraction_data.get("Reasoning", [])

    return {
        "drug_extraction_primary_drugs": format_list_as_semicolons(primary),
        "drug_extraction_secondary_drugs": format_list_as_semicolons(secondary),
        "drug_extraction_comparator_drugs": format_list_as_semicolons(comparator),
        "drug_extraction_reasoning": to_json_string(reasoning),
    }


# =============================================================================
# DRUG VALIDATION TRANSFORM
# =============================================================================


def transform_drug_validation(validation_data: dict) -> dict:
    """Transform drug_validation.json to CSV columns.

    Args:
        validation_data: Parsed drug_validation.json

    Returns:
        Dict with drug validation column values
    """
    if not validation_data:
        return {
            "drug_validation_status": "",
            "drug_validation_grounded_search_performed": "",
            "drug_validation_search_results": "",
            "drug_validation_missed_drugs": "",
            "drug_validation_issues_found": "",
            "drug_validation_reasoning": "",
        }

    return {
        "drug_validation_status": validation_data.get("validation_status", ""),
        "drug_validation_grounded_search_performed": to_json_string(
            validation_data.get("grounded_search_performed", "")
        ),
        "drug_validation_search_results": to_json_string(
            validation_data.get("search_results", [])
        ),
        "drug_validation_missed_drugs": format_list_as_semicolons(
            validation_data.get("missed_drugs", [])
        ),
        "drug_validation_issues_found": to_json_string(
            validation_data.get("issues_found", [])
        ),
        "drug_validation_reasoning": validation_data.get("validation_reasoning", ""),
    }


# =============================================================================
# DRUG CLASS STEPS 1-3 TRANSFORMS
# =============================================================================


def transform_drug_class_step1(steps1_3_data: dict) -> dict:
    """Transform step 1 data from drug_class_steps1_3.json.

    Step 1 (regimen) maps drug -> components.
    In the Temporal output, this is stored as drug_results[].drug -> drug_results[].components.

    Args:
        steps1_3_data: Parsed drug_class_steps1_3.json

    Returns:
        Dict with step 1 column values
    """
    if not steps1_3_data:
        return {"drug_class_step1_drug_to_components": ""}

    drug_results = steps1_3_data.get("drug_results", [])

    drug_to_components = {}
    for entry in drug_results:
        drug_name = entry.get("drug", "")
        components = entry.get("components", [])
        if drug_name:
            drug_to_components[drug_name] = components

    return {
        "drug_class_step1_drug_to_components": format_dict_as_key_value(drug_to_components),
    }


def transform_drug_class_step2(steps1_3_data: dict) -> dict:
    """Transform step 2 data from drug_class_steps1_3.json.

    Step 2 (extraction) has per-drug extractions with drug_classes and extraction_details.
    In the Temporal output: drug_results[].extractions.{drug_name}.drug_classes/extraction_details

    Args:
        steps1_3_data: Parsed drug_class_steps1_3.json

    Returns:
        Dict with step 2 column values
    """
    if not steps1_3_data:
        return {
            "drug_class_step2_drug_classes": "",
            "drug_class_step2_extraction_details": "",
        }

    drug_results = steps1_3_data.get("drug_results", [])

    drug_classes = {}
    extraction_details = {}

    for entry in drug_results:
        extractions = entry.get("extractions", {})
        for drug_name, result in extractions.items():
            if isinstance(result, dict):
                drug_classes[drug_name] = result.get("drug_classes", [])
                extraction_details[drug_name] = result.get("extraction_details", [])

    return {
        "drug_class_step2_drug_classes": format_dict_as_key_value(drug_classes),
        "drug_class_step2_extraction_details": to_json_string(extraction_details),
    }


def transform_drug_class_step3(steps1_3_data: dict) -> dict:
    """Transform step 3 data from drug_class_steps1_3.json.

    Step 3 (selection) has per-drug selections with selected_drug_classes and reasoning.
    In the Temporal output: drug_results[].selections.{drug_name}.selected_drug_classes/reasoning

    Args:
        steps1_3_data: Parsed drug_class_steps1_3.json

    Returns:
        Dict with step 3 column values
    """
    if not steps1_3_data:
        return {
            "drug_class_step3_selected_drug_classes": "",
            "drug_class_step3_reasoning": "",
            "drug_class_implicit_drug_classes": "",
        }

    drug_results = steps1_3_data.get("drug_results", [])

    selected_drug_classes = {}
    reasoning = {}

    for entry in drug_results:
        selections = entry.get("selections", {})
        for drug_name, result in selections.items():
            if isinstance(result, dict):
                selected_drug_classes[drug_name] = result.get("selected_drug_classes", [])
                reasoning[drug_name] = result.get("reasoning", "")

    all_implicit = set()
    for classes in selected_drug_classes.values():
        if isinstance(classes, list):
            for c in classes:
                if c and c != "NA":
                    all_implicit.add(c.strip())
    implicit_str = ";;".join(sorted(all_implicit)) if all_implicit else ""

    return {
        "drug_class_step3_selected_drug_classes": format_dict_as_key_value(selected_drug_classes),
        "drug_class_step3_reasoning": to_json_string(reasoning),
        "drug_class_implicit_drug_classes": implicit_str,
    }


# =============================================================================
# DRUG CLASS STEP 4 TRANSFORM
# =============================================================================


def transform_drug_class_step4(step4_data: dict) -> dict:
    """Transform drug_class_step4.json to CSV columns.

    Args:
        step4_data: Parsed drug_class_step4.json

    Returns:
        Dict with step 4 column values
    """
    if not step4_data:
        return {
            "drug_class_step4_explicit_drug_classes": "",
            "drug_class_step4_extraction_details": "",
        }

    return {
        "drug_class_step4_explicit_drug_classes": format_list_as_semicolons(
            step4_data.get("explicit_drug_classes", [])
        ),
        "drug_class_step4_extraction_details": to_json_string(
            step4_data.get("extraction_details", [])
        ),
    }


# =============================================================================
# DRUG CLASS STEP 5 TRANSFORM
# =============================================================================


def transform_drug_class_step5(step5_data: dict) -> dict:
    """Transform drug_class_step5.json to CSV columns.

    This file may not exist (step 5 is skipped when step 4 returns "NA").

    Args:
        step5_data: Parsed drug_class_step5.json (may be None)

    Returns:
        Dict with step 5 column values
    """
    if not step5_data:
        return {
            "drug_class_step5_refined_explicit_classes": "",
            "drug_class_step5_removed_classes": "",
            "drug_class_step5_reasoning": "",
        }

    return {
        "drug_class_step5_refined_explicit_classes": format_list_as_semicolons(
            step5_data.get("refined_explicit_classes", [])
        ),
        "drug_class_step5_removed_classes": format_list_as_semicolons(
            step5_data.get("removed_classes", [])
        ),
        "drug_class_step5_reasoning": step5_data.get("reasoning", ""),
    }


# =============================================================================
# DRUG CLASS VALIDATION TRANSFORM
# =============================================================================


def transform_drug_class_validation(validation_data: dict) -> dict:
    """Transform drug_class_validation.json to CSV columns.

    Temporal validation output has a 'results' array with per-drug validation.
    Each entry has drug_name and validation details including status, reasoning,
    checks_performed, issues_found, and missed_drug_classes.

    Args:
        validation_data: Parsed drug_class_validation.json

    Returns:
        Dict with validation column values
    """
    if not validation_data:
        return {
            "drug_class_validation_overall_status": "",
            "drug_class_validation_status": "",
            "drug_class_validation_reasoning": "",
            "drug_class_validation_checks_performed": "",
            "drug_class_validation_issues_found": "",
            "drug_class_validation_missed_drug_classes": "",
        }

    results = validation_data.get("results", [])

    validation_status = {}
    validation_reasoning = {}
    checks_performed = {}
    issues_found = {}
    missed_drug_classes = {}

    for entry in results:
        drug_name = entry.get("drug_name", "") or "explicit_only"
        validation = entry.get("validation", {})
        if isinstance(validation, dict):
            validation_status[drug_name] = validation.get("validation_status", "")
            validation_reasoning[drug_name] = validation.get("validation_reasoning", "")
            checks_performed[drug_name] = validation.get("checks_performed", {})
            issues_found[drug_name] = validation.get("issues_found", [])
            missed_drug_classes[drug_name] = validation.get("missed_drug_classes", [])

    # Determine overall status: FAIL if ANY drug fails, PASS if all pass
    overall_status = ""
    if validation_status:
        statuses = list(validation_status.values())
        if any(s == "FAIL" for s in statuses):
            overall_status = "FAIL"
        elif all(s == "PASS" for s in statuses):
            overall_status = "PASS"
        else:
            # Handle edge cases (empty strings, other values)
            overall_status = "FAIL" if "FAIL" in statuses else "UNKNOWN"

    return {
        "drug_class_validation_overall_status": overall_status,
        "drug_class_validation_status": format_dict_as_key_value_skip_empty(
            validation_status
        ),
        "drug_class_validation_reasoning": to_json_string(validation_reasoning),
        "drug_class_validation_checks_performed": to_json_string(checks_performed),
        "drug_class_validation_issues_found": to_json_string(issues_found),
        "drug_class_validation_missed_drug_classes": format_dict_as_key_value_skip_empty(
            missed_drug_classes
        ),
    }


# =============================================================================
# COMBINED CLASSES
# =============================================================================


def get_combined_classes(steps1_3_data: dict, step5_data: dict) -> str:
    """Combine drug classes from step 3 selections and step 5 refinements.

    Args:
        steps1_3_data: Parsed drug_class_steps1_3.json (for step 3 selections)
        step5_data: Parsed drug_class_step5.json (may be None)

    Returns:
        Double-semicolon separated string of unique sorted drug classes
    """
    # Extract step 3 selections from the combined steps1_3 data
    step3_selections = {}
    if steps1_3_data:
        for entry in steps1_3_data.get("drug_results", []):
            selections = entry.get("selections", {})
            step3_selections.update(selections)

    # Extract step 5 refined classes
    step5_classes = step5_data.get("refined_explicit_classes", []) if step5_data else []

    return extract_combined_drug_classes(step3_selections, step5_classes)


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

    stats = {
        "drug_extraction_found": 0,
        "drug_validation_found": 0,
        "drug_class_steps1_3_found": 0,
        "drug_class_step4_found": 0,
        "drug_class_step5_found": 0,
        "drug_class_validation_found": 0,
    }

    for row in tqdm(rows, desc="Processing abstracts", unit="abstract"):
        abstract_id = row.get(id_col, "") if id_col else ""

        if not abstract_id:
            continue

        # Start with input columns
        output_row = dict(row)

        # --- Load all data files ---
        drug_extraction = load_step_output(data_storage, abstract_id, "drug_extraction")
        drug_validation = load_step_output(data_storage, abstract_id, "drug_validation")
        steps1_3_data = load_step_output(data_storage, abstract_id, "drug_class_steps1_3")
        step4_data = load_step_output(data_storage, abstract_id, "drug_class_step4")
        step5_data = load_step_output(data_storage, abstract_id, "drug_class_step5")
        dc_validation = load_step_output(data_storage, abstract_id, "drug_class_validation")

        # --- Track stats ---
        if drug_extraction:
            stats["drug_extraction_found"] += 1
        if drug_validation:
            stats["drug_validation_found"] += 1
        if steps1_3_data:
            stats["drug_class_steps1_3_found"] += 1
        if step4_data:
            stats["drug_class_step4_found"] += 1
        if step5_data:
            stats["drug_class_step5_found"] += 1
        if dc_validation:
            stats["drug_class_validation_found"] += 1

        # --- Drug Extraction ---
        output_row.update(transform_drug_extraction(drug_extraction))

        # --- Drug Validation ---
        output_row.update(transform_drug_validation(drug_validation))

        # --- Drug Class Step 1 (from steps1_3) ---
        output_row.update(transform_drug_class_step1(steps1_3_data))

        # --- Drug Class Step 2 (from steps1_3) ---
        output_row.update(transform_drug_class_step2(steps1_3_data))

        # --- Drug Class Step 3 (from steps1_3) ---
        output_row.update(transform_drug_class_step3(steps1_3_data))

        # --- Drug Class Step 4 ---
        output_row.update(transform_drug_class_step4(step4_data))

        # --- Drug Class Step 5 ---
        output_row.update(transform_drug_class_step5(step5_data))

        # --- Combined Drug Classes (Step 3 + Step 5) ---
        output_row["drug_class_combined_all_classes"] = get_combined_classes(
            steps1_3_data, step5_data
        )

        # --- Drug Class Validation ---
        output_row.update(transform_drug_class_validation(dc_validation))

        output_rows.append(output_row)

    # Print stats
    total = len(rows)
    print(f"\nData availability:")
    print(f"  Drug extraction:        {stats['drug_extraction_found']}/{total}")
    print(f"  Drug validation:        {stats['drug_validation_found']}/{total}")
    print(f"  Drug class steps 1-3:   {stats['drug_class_steps1_3_found']}/{total}")
    print(f"  Drug class step 4:      {stats['drug_class_step4_found']}/{total}")
    print(f"  Drug class step 5:      {stats['drug_class_step5_found']}/{total}")
    print(f"  Drug class validation:  {stats['drug_class_validation_found']}/{total}")

    return output_rows


# =============================================================================
# MAIN
# =============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Combined Drug & Drug Class QA Exporter for Temporal Outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Local storage
    python -m src.scripts.temporal.drug_drug_class_exporter \\
        --input data/abstract_titles.csv \\
        --data_dir data/output \\
        --output drug_drug_class_export.xlsx

    # GCS storage
    python -m src.scripts.temporal.drug_drug_class_exporter \\
        --input gs://bucket/Conference/abstract_titles.csv \\
        --data_dir gs://bucket/Conference \\
        --output gs://bucket/Conference/drug_drug_class_export.xlsx
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
        help="Output XLSX file path (local or gs://). Default: drug_drug_class_export_<timestamp>.xlsx",
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
        args.output = f"drug_drug_class_export_{timestamp}.xlsx"

    print("Combined Drug & Drug Class QA Exporter (Temporal)")
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
    output_fieldnames = fieldnames + NEW_COLUMNS

    # Export XLSX
    export_xlsx(output_rows, output_fieldnames, args.output)

    print(f"\nDone. Exported {len(output_rows)} rows.")


if __name__ == "__main__":
    main()
