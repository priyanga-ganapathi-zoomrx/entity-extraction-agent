#!/usr/bin/env python3
"""
Combined Drug and Drug Class QA Exporter

Reads drug extraction/validation outputs and drug class pipeline outputs,
combining them into a single CSV for QA review.

Output: Single CSV with one row per abstract containing:
- All input CSV columns
- Drug extraction columns (primary, secondary, comparator drugs, reasoning)
- Drug validation columns (status, search results, missed drugs, etc.)
- Drug class step 1-5 columns (drug_to_components, drug_classes, selections, etc.)
- Drug class validation columns (overall status, per-drug status, reasoning, checks_performed, issues_found, missed_drug_classes)

Usage:
    # Local storage
    python -m src.scripts.drug_drug_class_exporter \
        --input data/ASCO2025/input/abstract_titles.csv \
        --drug_output_dir data/ASCO2025/drug \
        --drug_class_output_dir data/ASCO2025/drug_class \
        --output qa_export.csv

    # GCS storage
    python -m src.scripts.drug_drug_class_exporter \
        --input gs://bucket/Conference/abstract_titles.csv \
        --drug_output_dir gs://bucket/Conference/drug \
        --drug_class_output_dir gs://bucket/Conference/drug_class \
        --output qa_export.csv
"""

import argparse
import csv
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from tqdm import tqdm

from src.agents.core.storage import LocalStorageClient, GCSStorageClient, get_storage_client


def _get_timestamp() -> str:
    """Get current timestamp for filename generation."""
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _to_json_string(data: Any) -> str:
    """Convert data to pretty-printed JSON string for CSV cell."""
    if data is None:
        return ""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(data)


def _format_list_as_semicolon_string(data: list) -> str:
    """Format Type A: Convert list to double-semicolon separated string.
    
    Example:
        ["Drug A", "Drug B"] → "Drug A;;Drug B"
        [] → ""
    """
    if not data:
        return ""
    # Filter out empty/None values and convert to strings
    items = [str(item).strip() for item in data if item]
    return ";;".join(items) if items else ""


def _format_dict_as_key_value(data: dict) -> str:
    """Format Type B: Convert dict with arrays to key-value lines.
    
    Example:
        {"Drug A": ["Class 1", "Class 2"], "Drug B": ["Class 3"]}
        →
        "Drug A": Class 1;;Class 2
        "Drug B": Class 3
    """
    if not data:
        return ""
    
    lines = []
    for key, values in data.items():
        if isinstance(values, list):
            values_str = ";;".join(str(v).strip() for v in values if v)
        else:
            values_str = str(values).strip() if values else ""
        lines.append(f'"{key}": {values_str}')
    
    return "\n".join(lines) if lines else ""


def _extract_combined_drug_classes(step3_selections: dict, step5_classes: list) -> str:
    """Combine drug classes from step3 and step5, removing duplicates.
    
    Args:
        step3_selections: Dict of {drug_name: {"selected_drug_classes": [...], ...}}
        step5_classes: List of refined explicit classes from step5
        
    Returns:
        Double-semicolon separated string of unique drug classes (sorted)
        
    Example:
        step3_selections = {"Drug A": {"selected_drug_classes": ["Class 1", "Class 2"]}}
        step5_classes = ["Class 2", "Class 3"]
        → "Class 1;;Class 2;;Class 3"
    """
    all_classes = set()
    
    # From step3: extract selected_drug_classes values (ignoring drug keys)
    if step3_selections:
        for drug_name, result in step3_selections.items():
            if isinstance(result, dict):
                classes = result.get("selected_drug_classes", [])
                if isinstance(classes, list):
                    for c in classes:
                        if c and c != "NA":
                            all_classes.add(c.strip())
    
    # From step5: add refined explicit classes
    if step5_classes:
        for c in step5_classes:
            if c and c != "NA":
                all_classes.add(c.strip())
    
    # Return sorted, deduplicated list as semicolon-separated string
    return ";;".join(sorted(all_classes)) if all_classes else ""


def _format_dict_as_key_value_skip_empty(data: dict) -> str:
    """Format Type C: Convert dict with arrays to key-value lines, skip empty arrays.
    
    Example:
        {"Drug A": [], "Drug B": ["Missed Class"]}
        →
        "Drug B": Missed Class
        
        {"Drug A": [], "Drug B": []} → ""
    """
    if not data:
        return ""
    
    lines = []
    for key, values in data.items():
        if isinstance(values, list) and values:  # Only include non-empty arrays
            values_str = ";;".join(str(v).strip() for v in values if v)
            if values_str:
                lines.append(f'"{key}": {values_str}')
        elif not isinstance(values, list) and values:  # Handle non-list values
            lines.append(f'"{key}": {str(values).strip()}')
    
    return "\n".join(lines) if lines else ""


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use in filenames.
    
    Replaces characters that are problematic in file paths.
    """
    return name.replace("/", "_").replace("\\", "_").replace(":", "_")


def load_abstracts_from_csv(
    csv_filename: str,
    input_storage: Union[LocalStorageClient, GCSStorageClient],
    limit: Optional[int] = None,
) -> tuple[list[dict], list[str]]:
    """Load abstracts from input CSV file.
    
    Args:
        csv_filename: Filename of the CSV within input_storage
        input_storage: Storage client for reading input CSV
        limit: Optional limit on rows to process
        
    Returns:
        tuple: (list of row dicts, list of fieldnames/column headers)
    """
    csv_content = input_storage.download_text(csv_filename)
    reader = csv.DictReader(io.StringIO(csv_content))
    fieldnames = list(reader.fieldnames or [])
    
    rows = list(reader)
    
    if limit:
        rows = rows[:limit]
    
    return rows, fieldnames


def get_abstract_id_column(fieldnames: list[str]) -> Optional[str]:
    """Find the abstract_id column name (case-insensitive)."""
    header_map = {h.lower().strip(): h for h in fieldnames}
    return header_map.get('abstract_id') or header_map.get('id')


def read_json_file(
    storage: Union[LocalStorageClient, GCSStorageClient],
    path: str,
) -> Optional[dict]:
    """Read a JSON file from storage.
    
    Args:
        storage: Storage client
        path: Relative path to file
        
    Returns:
        dict if file exists, None otherwise
    """
    try:
        return storage.download_json(path)
    except FileNotFoundError:
        return None


# =============================================================================
# DRUG EXTRACTION TRANSFORM
# =============================================================================

def transform_drug_extraction(
    drug_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug extraction output to CSV columns.
    
    Reads: drug_output_dir/abstracts/{abstract_id}/extraction.json
    
    Output columns:
    - drug_extraction_primary_drugs (Type A: double-semicolon separated)
    - drug_extraction_secondary_drugs (Type A: double-semicolon separated)
    - drug_extraction_comparator_drugs (Type A: double-semicolon separated)
    - drug_extraction_reasoning (JSON)
    """
    extraction_data = read_json_file(drug_storage, f"abstracts/{abstract_id}/extraction.json")
    
    if not extraction_data:
        return {
            "drug_extraction_primary_drugs": "",
            "drug_extraction_secondary_drugs": "",
            "drug_extraction_comparator_drugs": "",
            "drug_extraction_reasoning": "",
        }
    
    return {
        "drug_extraction_primary_drugs": _format_list_as_semicolon_string(extraction_data.get("Primary Drugs", [])),
        "drug_extraction_secondary_drugs": _format_list_as_semicolon_string(extraction_data.get("Secondary Drugs", [])),
        "drug_extraction_comparator_drugs": _format_list_as_semicolon_string(extraction_data.get("Comparator Drugs", [])),
        "drug_extraction_reasoning": _to_json_string(extraction_data.get("Reasoning", [])),
    }


# =============================================================================
# DRUG VALIDATION TRANSFORM
# =============================================================================

def transform_drug_validation(
    drug_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug validation output to CSV columns.
    
    Reads: drug_output_dir/abstracts/{abstract_id}/validation.json
    
    Output columns:
    - drug_validation_status
    - drug_validation_grounded_search_performed
    - drug_validation_search_results
    - drug_validation_missed_drugs (Type A: double-semicolon separated)
    - drug_validation_issues_found
    - drug_validation_reasoning
    """
    validation_data = read_json_file(drug_storage, f"abstracts/{abstract_id}/validation.json")
    
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
        "drug_validation_grounded_search_performed": _to_json_string(validation_data.get("grounded_search_performed", "")),
        "drug_validation_search_results": _to_json_string(validation_data.get("search_results", [])),
        "drug_validation_missed_drugs": _format_list_as_semicolon_string(validation_data.get("missed_drugs", [])),
        "drug_validation_issues_found": _to_json_string(validation_data.get("issues_found", [])),
        "drug_validation_reasoning": validation_data.get("validation_reasoning", ""),
    }


# =============================================================================
# DRUG CLASS STEP 1 TRANSFORM
# =============================================================================

def transform_drug_class_step1(
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug class Step 1 output to CSV columns.
    
    Reads: drug_class_output_dir/abstracts/{abstract_id}/step1_output.json
    
    Output columns:
    - drug_class_step1_drug_to_components (Type B: key-value format)
    """
    step1_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step1_output.json")
    
    if not step1_data:
        return {"drug_class_step1_drug_to_components": ""}
    
    return {
        "drug_class_step1_drug_to_components": _format_dict_as_key_value(step1_data.get("drug_to_components", {})),
    }


# =============================================================================
# DRUG CLASS STEP 2 TRANSFORM
# =============================================================================

def transform_drug_class_step2(
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug class Step 2 output to CSV columns.
    
    Reads: drug_class_output_dir/abstracts/{abstract_id}/step2_output.json
    
    Output columns (grouped by drug name):
    - drug_class_step2_drug_classes (Type B: key-value format)
    - drug_class_step2_extraction_details: JSON object {drug: [details]}
    """
    step2_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step2_output.json")
    
    if not step2_data:
        return {
            "drug_class_step2_drug_classes": "",
            "drug_class_step2_extraction_details": "",
        }
    
    extractions = step2_data.get("extractions", {})
    
    drug_classes = {}
    extraction_details = {}
    
    for drug_name, result in extractions.items():
        if isinstance(result, dict):
            drug_classes[drug_name] = result.get("drug_classes", [])
            extraction_details[drug_name] = result.get("extraction_details", [])
    
    return {
        "drug_class_step2_drug_classes": _format_dict_as_key_value(drug_classes),
        "drug_class_step2_extraction_details": _to_json_string(extraction_details),
    }


# =============================================================================
# DRUG CLASS STEP 3 TRANSFORM
# =============================================================================

def transform_drug_class_step3(
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug class Step 3 output to CSV columns.
    
    Reads: drug_class_output_dir/abstracts/{abstract_id}/step3_output.json
    
    Output columns (grouped by drug name):
    - drug_class_step3_selected_drug_classes (Type B: key-value format)
    - drug_class_step3_reasoning: JSON object {drug: reasoning}
    """
    step3_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step3_output.json")
    
    if not step3_data:
        return {
            "drug_class_step3_selected_drug_classes": "",
            "drug_class_step3_reasoning": "",
        }
    
    selections = step3_data.get("selections", {})
    
    selected_drug_classes = {}
    reasoning = {}
    
    for drug_name, result in selections.items():
        if isinstance(result, dict):
            selected_drug_classes[drug_name] = result.get("selected_drug_classes", [])
            reasoning[drug_name] = result.get("reasoning", "")
    
    return {
        "drug_class_step3_selected_drug_classes": _format_dict_as_key_value(selected_drug_classes),
        "drug_class_step3_reasoning": _to_json_string(reasoning),
    }


# =============================================================================
# DRUG CLASS STEP 4 TRANSFORM
# =============================================================================

def transform_drug_class_step4(
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug class Step 4 output to CSV columns.
    
    Reads: drug_class_output_dir/abstracts/{abstract_id}/step4_output.json
    
    Output columns:
    - drug_class_step4_explicit_drug_classes (Type A: double-semicolon separated)
    - drug_class_step4_extraction_details: JSON array
    """
    step4_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step4_output.json")
    
    if not step4_data:
        return {
            "drug_class_step4_explicit_drug_classes": "",
            "drug_class_step4_extraction_details": "",
        }
    
    return {
        "drug_class_step4_explicit_drug_classes": _format_list_as_semicolon_string(step4_data.get("explicit_drug_classes", [])),
        "drug_class_step4_extraction_details": _to_json_string(step4_data.get("extraction_details", [])),
    }


# =============================================================================
# DRUG CLASS STEP 5 TRANSFORM
# =============================================================================

def transform_drug_class_step5(
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
) -> dict:
    """Transform drug class Step 5 output to CSV columns.
    
    Reads: drug_class_output_dir/abstracts/{abstract_id}/step5_output.json
    
    Output columns:
    - drug_class_step5_refined_explicit_classes (Type A: double-semicolon separated)
    - drug_class_step5_removed_classes (Type A: double-semicolon separated)
    - drug_class_step5_reasoning: String
    """
    step5_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step5_output.json")
    
    if not step5_data:
        return {
            "drug_class_step5_refined_explicit_classes": "",
            "drug_class_step5_removed_classes": "",
            "drug_class_step5_reasoning": "",
        }
    
    return {
        "drug_class_step5_refined_explicit_classes": _format_list_as_semicolon_string(step5_data.get("refined_explicit_classes", [])),
        "drug_class_step5_removed_classes": _format_list_as_semicolon_string(step5_data.get("removed_classes", [])),
        "drug_class_step5_reasoning": step5_data.get("reasoning", ""),
    }


# =============================================================================
# DRUG CLASS VALIDATION TRANSFORM
# =============================================================================

def transform_drug_class_validation(
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
    abstract_id: str,
    step2_data: Optional[dict],
) -> tuple[dict, bool]:
    """Transform drug class validation output to CSV columns.
    
    Reads per-drug validation files: validation_{sanitized_drug}.json and
    aggregates them into the same CSV structure used by the Temporal exporter.
    
    Output columns:
    - drug_class_validation_overall_status
    - drug_class_validation_status (Type C: key-value format, skip empty)
    - drug_class_validation_reasoning (JSON)
    - drug_class_validation_checks_performed (JSON)
    - drug_class_validation_issues_found (JSON)
    - drug_class_validation_missed_drug_classes (Type C: key-value format, skip empty)
    
    Returns:
        tuple: (column_dict, validation_files_found)
    """
    # If we don't know which drugs were extracted, we can't look up per-drug validation
    if not step2_data:
        empty = {
            "drug_class_validation_overall_status": "",
            "drug_class_validation_status": "",
            "drug_class_validation_reasoning": "",
            "drug_class_validation_checks_performed": "",
            "drug_class_validation_issues_found": "",
            "drug_class_validation_missed_drug_classes": "",
        }
        return empty, False
    
    # Get drug names from step2 extractions
    extractions = step2_data.get("extractions", {})
    drug_names = list(extractions.keys())
    
    if not drug_names:
        empty = {
            "drug_class_validation_overall_status": "",
            "drug_class_validation_status": "",
            "drug_class_validation_reasoning": "",
            "drug_class_validation_checks_performed": "",
            "drug_class_validation_issues_found": "",
            "drug_class_validation_missed_drug_classes": "",
        }
        return empty, False
    
    validation_status: dict[str, str] = {}
    validation_reasoning: dict[str, str] = {}
    checks_performed: dict[str, dict] = {}
    issues_found: dict[str, list] = {}
    missed_drug_classes: dict[str, list] = {}
    validation_files_found = False
    
    # Read each per-drug validation file and aggregate fields
    for drug_name in drug_names:
        safe_drug_name = _sanitize_filename(drug_name)
        val_data = read_json_file(
            drug_class_storage,
            f"abstracts/{abstract_id}/validation_{safe_drug_name}.json",
        )
        if not val_data:
            continue
        
        validation_files_found = True
        validation_status[drug_name] = val_data.get("validation_status", "")
        validation_reasoning[drug_name] = val_data.get("validation_reasoning", "")
        checks_performed[drug_name] = val_data.get("checks_performed", {})
        issues_found[drug_name] = val_data.get("issues_found", [])
        missed_drug_classes[drug_name] = val_data.get("missed_drug_classes", [])
    
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
    
    columns = {
        "drug_class_validation_overall_status": overall_status,
        "drug_class_validation_status": _format_dict_as_key_value_skip_empty(
            validation_status
        ),
        "drug_class_validation_reasoning": _to_json_string(validation_reasoning),
        "drug_class_validation_checks_performed": _to_json_string(checks_performed),
        "drug_class_validation_issues_found": _to_json_string(issues_found),
        "drug_class_validation_missed_drug_classes": _format_dict_as_key_value_skip_empty(
            missed_drug_classes
        ),
    }
    
    return columns, validation_files_found


# =============================================================================
# MAIN PROCESSING
# =============================================================================

def process_abstracts(
    rows: list[dict],
    fieldnames: list[str],
    drug_storage: Union[LocalStorageClient, GCSStorageClient],
    drug_class_storage: Union[LocalStorageClient, GCSStorageClient],
) -> list[dict]:
    """Process all abstracts and build combined output rows.
    
    Args:
        rows: List of input CSV rows
        fieldnames: Column names from input CSV
        drug_storage: Storage client for drug pipeline outputs
        drug_class_storage: Storage client for drug class pipeline outputs
        
    Returns:
        List of combined output rows
    """
    id_col = get_abstract_id_column(fieldnames)
    
    output_rows = []
    
    stats = {
        "drug_extraction_found": 0,
        "drug_validation_found": 0,
        "drug_class_step1_found": 0,
        "drug_class_step2_found": 0,
        "drug_class_step3_found": 0,
        "drug_class_step4_found": 0,
        "drug_class_step5_found": 0,
        "drug_class_validation_found": 0,
    }
    
    for row in tqdm(rows, desc="Processing abstracts"):
        abstract_id = row.get(id_col, "") if id_col else ""
        
        if not abstract_id:
            continue
        
        # Start with input columns
        output_row = dict(row)
        
        # --- Drug Extraction ---
        drug_extraction_cols = transform_drug_extraction(drug_storage, abstract_id)
        if drug_extraction_cols.get("drug_extraction_primary_drugs"):
            stats["drug_extraction_found"] += 1
        output_row.update(drug_extraction_cols)
        
        # --- Drug Validation ---
        drug_validation_cols = transform_drug_validation(drug_storage, abstract_id)
        if drug_validation_cols.get("drug_validation_status"):
            stats["drug_validation_found"] += 1
        output_row.update(drug_validation_cols)
        
        # --- Drug Class Step 1 ---
        step1_cols = transform_drug_class_step1(drug_class_storage, abstract_id)
        if step1_cols.get("drug_class_step1_drug_to_components"):
            stats["drug_class_step1_found"] += 1
        output_row.update(step1_cols)
        
        # --- Drug Class Step 2 ---
        step2_cols = transform_drug_class_step2(drug_class_storage, abstract_id)
        if step2_cols.get("drug_class_step2_drug_classes"):
            stats["drug_class_step2_found"] += 1
        output_row.update(step2_cols)
        
        # --- Drug Class Step 3 ---
        step3_cols = transform_drug_class_step3(drug_class_storage, abstract_id)
        if step3_cols.get("drug_class_step3_selected_drug_classes"):
            stats["drug_class_step3_found"] += 1
        output_row.update(step3_cols)
        
        # --- Drug Class Step 4 ---
        step4_cols = transform_drug_class_step4(drug_class_storage, abstract_id)
        if step4_cols.get("drug_class_step4_explicit_drug_classes"):
            stats["drug_class_step4_found"] += 1
        output_row.update(step4_cols)
        
        # --- Drug Class Step 5 ---
        step5_cols = transform_drug_class_step5(drug_class_storage, abstract_id)
        if step5_cols.get("drug_class_step5_refined_explicit_classes"):
            stats["drug_class_step5_found"] += 1
        output_row.update(step5_cols)
        
        # --- Combined Drug Classes (Step 3 + Step 5) ---
        # Read raw JSON data for combining
        step3_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step3_output.json")
        step5_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step5_output.json")
        step3_selections = step3_data.get("selections", {}) if step3_data else {}
        step5_classes = step5_data.get("refined_explicit_classes", []) if step5_data else []
        combined_classes = _extract_combined_drug_classes(step3_selections, step5_classes)
        output_row["drug_class_combined_all_classes"] = combined_classes
        
        # --- Drug Class Validation ---
        # Need step2 data to know which drugs to look up validation for
        step2_data = read_json_file(drug_class_storage, f"abstracts/{abstract_id}/step2_output.json")
        validation_cols, validation_found = transform_drug_class_validation(drug_class_storage, abstract_id, step2_data)
        if validation_found:
            stats["drug_class_validation_found"] += 1
        output_row.update(validation_cols)
        
        output_rows.append(output_row)
    
    # Print stats
    total = len(rows)
    print("\nData availability:")
    print(f"  Drug extraction:        {stats['drug_extraction_found']}/{total}")
    print(f"  Drug validation:        {stats['drug_validation_found']}/{total}")
    print(f"  Drug class Step 1:      {stats['drug_class_step1_found']}/{total}")
    print(f"  Drug class Step 2:      {stats['drug_class_step2_found']}/{total}")
    print(f"  Drug class Step 3:      {stats['drug_class_step3_found']}/{total}")
    print(f"  Drug class Step 4:      {stats['drug_class_step4_found']}/{total}")
    print(f"  Drug class Step 5:      {stats['drug_class_step5_found']}/{total}")
    print(f"  Drug class validation:  {stats['drug_class_validation_found']}/{total}")
    
    return output_rows


def get_output_fieldnames(input_fieldnames: list[str]) -> list[str]:
    """Get complete list of output fieldnames in correct order."""
    new_columns = [
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
        "drug_class_combined_all_classes",
        # Drug class validation (match Temporal exporter)
        "drug_class_validation_overall_status",
        "drug_class_validation_status",
        "drug_class_validation_reasoning",
        "drug_class_validation_checks_performed",
        "drug_class_validation_issues_found",
        "drug_class_validation_missed_drug_classes",
    ]
    
    return input_fieldnames + new_columns


def export_to_csv(
    output_rows: list[dict],
    fieldnames: list[str],
    output_path: str,
) -> None:
    """Export data to CSV file (supports both local and GCS paths).
    
    Args:
        output_rows: List of row dicts to write
        fieldnames: Column names in order
        output_path: Path to output CSV file (local path or gs://bucket/path)
    """
    # Write CSV content to string buffer first
    output_buffer = io.StringIO()
    writer = csv.DictWriter(output_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(output_rows)
    csv_content = output_buffer.getvalue()
    
    # Handle GCS or local path
    if output_path.startswith("gs://"):
        from src.agents.core.storage import parse_gcs_path
        bucket, full_prefix = parse_gcs_path(output_path)
        if "/" in full_prefix:
            base_prefix = "/".join(full_prefix.split("/")[:-1])
            csv_filename = full_prefix.split("/")[-1]
        else:
            base_prefix = ""
            csv_filename = full_prefix
        output_storage = GCSStorageClient(bucket, base_prefix)
        output_storage.upload_text(csv_filename, csv_content)
        print(f"\nCSV file saved to GCS: {output_path}")
    else:
        # Local file
        output_dir = Path(output_path).parent
        if output_dir and str(output_dir) != "." and not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            f.write(csv_content)
        
        print(f"\nCSV file saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Combined Drug and Drug Class QA Exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Local storage
    python -m src.scripts.drug_drug_class_exporter \\
        --input data/ASCO2025/input/abstract_titles.csv \\
        --drug_output_dir data/ASCO2025/drug \\
        --drug_class_output_dir data/ASCO2025/drug_class \\
        --output qa_export.csv

    # GCS storage
    python -m src.scripts.drug_drug_class_exporter \\
        --input gs://bucket/Conference/abstract_titles.csv \\
        --drug_output_dir gs://bucket/Conference/drug \\
        --drug_class_output_dir gs://bucket/Conference/drug_class \\
        --output qa_export.csv
        
    # With limit
    python -m src.scripts.drug_drug_class_exporter \\
        --input data/ASCO2025/input/abstract_titles.csv \\
        --drug_output_dir data/ASCO2025/drug \\
        --drug_class_output_dir data/ASCO2025/drug_class \\
        --output qa_export.csv \\
        --limit 10
        """
    )
    
    parser.add_argument(
        "--input",
        default="gs://entity-extraction-agent-data-zeus/ASCO_GI_2026/abstract_titles.csv",
        help="Input CSV file path with abstract metadata"
    )
    parser.add_argument(
        "--drug_output_dir",
        default="gs://entity-extraction-agent-data-zeus/ASCO_GI_2026/drug",
        help="Drug pipeline output directory"
    )
    parser.add_argument(
        "--drug_class_output_dir",
        default="gs://entity-extraction-agent-data-zeus/ASCO_GI_2026/drug_class",
        help="Drug class pipeline output directory"
    )
    parser.add_argument(
        "--output",
        default=f"data/drug_drug_class_export_{_get_timestamp()}.csv",
        help=f"Output CSV file path (default: drug_drug_class_export_{_get_timestamp()}.csv)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of abstracts to process"
    )
    
    args = parser.parse_args()
    
    # Determine input storage client
    if args.input.startswith("gs://"):
        from src.agents.core.storage import parse_gcs_path
        bucket, full_prefix = parse_gcs_path(args.input)
        if "/" in full_prefix:
            base_prefix = "/".join(full_prefix.split("/")[:-1])
            csv_filename = full_prefix.split("/")[-1]
        else:
            base_prefix = ""
            csv_filename = full_prefix
        input_storage = GCSStorageClient(bucket, base_prefix)
    else:
        input_path = Path(args.input)
        input_storage = LocalStorageClient(str(input_path.parent) if input_path.parent != Path(".") else ".")
        csv_filename = input_path.name
    
    # Drug output storage client
    drug_storage = get_storage_client(args.drug_output_dir)
    
    # Drug class output storage client
    drug_class_storage = get_storage_client(args.drug_class_output_dir)
    
    # Generate output filename if not provided
    output_path = args.output
    if not output_path:
        output_path = f"qa_export_{_get_timestamp()}.csv"
    
    print("Combined Drug & Drug Class QA Exporter")
    print("=" * 60)
    print(f"Input CSV:            {args.input}")
    print(f"Drug output dir:      {args.drug_output_dir}")
    print(f"Drug class output dir:{args.drug_class_output_dir}")
    print(f"Output CSV:           {output_path}")
    if args.limit:
        print(f"Limit:                {args.limit}")
    print()
    
    # Load abstracts from CSV
    print("Loading abstracts from CSV...")
    rows, fieldnames = load_abstracts_from_csv(csv_filename, input_storage, args.limit)
    print(f"Loaded {len(rows)} abstracts")
    print(f"Input columns: {fieldnames}")
    print()
    
    if not rows:
        print("No abstracts to process.")
        return
    
    # Process abstracts and build output rows
    output_rows = process_abstracts(rows, fieldnames, drug_storage, drug_class_storage)
    
    # Get output fieldnames
    output_fieldnames = get_output_fieldnames(fieldnames)
    
    # Export to CSV
    export_to_csv(output_rows, output_fieldnames, output_path)
    
    print("\nDone!")


if __name__ == "__main__":
    main()
