"""Shared utilities for Temporal workflow export scripts.

Common functions for:
- CSV input loading
- JSON file reading (with graceful fallback)
- Storage client setup (local or GCS)
- Data formatting helpers for CSV output
"""

import csv
import io
import json
from pathlib import Path
from typing import Any, Optional, Union

from src.agents.core.storage import (
    GCSStorageClient,
    LocalStorageClient,
    get_storage_client,
    parse_gcs_path,
)


# =============================================================================
# STORAGE HELPERS
# =============================================================================


def get_data_storage(data_dir: str) -> Union[GCSStorageClient, LocalStorageClient]:
    """Create a storage client for the Temporal workflow data directory.

    Args:
        data_dir: Base data directory (local path or gs://bucket/prefix)

    Returns:
        Appropriate storage client
    """
    return get_storage_client(data_dir)


def get_input_storage_and_filename(
    input_path: str,
) -> tuple[Union[GCSStorageClient, LocalStorageClient], str]:
    """Parse an input CSV path into a storage client and filename.

    Args:
        input_path: Full path to CSV file (local or gs://bucket/path/file.csv)

    Returns:
        tuple: (storage_client, csv_filename)
    """
    if input_path.startswith("gs://"):
        bucket, full_prefix = parse_gcs_path(input_path)
        if "/" in full_prefix:
            base_prefix = full_prefix.rsplit("/", 1)[0]
            csv_filename = full_prefix.rsplit("/", 1)[1]
        else:
            base_prefix = ""
            csv_filename = full_prefix
        storage = GCSStorageClient(bucket, base_prefix)
    else:
        input_path_obj = Path(input_path)
        parent = str(input_path_obj.parent) if input_path_obj.parent != Path(".") else "."
        storage = LocalStorageClient(parent)
        csv_filename = input_path_obj.name

    return storage, csv_filename


# =============================================================================
# CSV LOADING
# =============================================================================


def load_input_csv(
    csv_filename: str,
    input_storage: Union[GCSStorageClient, LocalStorageClient],
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
    """Find the abstract_id column name (case-insensitive).

    Returns:
        The matching column name, or None if not found
    """
    header_map = {h.lower().strip(): h for h in fieldnames}
    return header_map.get("abstract_id") or header_map.get("id")


# =============================================================================
# JSON LOADING
# =============================================================================


def safe_load_json(
    storage: Union[GCSStorageClient, LocalStorageClient],
    path: str,
) -> Optional[dict]:
    """Load a JSON file from storage, returning None if not found.

    Args:
        storage: Storage client
        path: Relative path to JSON file

    Returns:
        Parsed dict if file exists, None otherwise
    """
    try:
        return storage.download_json(path)
    except FileNotFoundError:
        return None


def load_status(
    storage: Union[GCSStorageClient, LocalStorageClient],
    abstract_id: str,
) -> Optional[dict]:
    """Load status.json for an abstract.

    Args:
        storage: Storage client for data directory
        abstract_id: The abstract ID

    Returns:
        Status dict if found, None otherwise
    """
    return safe_load_json(storage, f"abstracts/{abstract_id}/status.json")


def load_step_output(
    storage: Union[GCSStorageClient, LocalStorageClient],
    abstract_id: str,
    step_name: str,
) -> Optional[dict]:
    """Load a step output JSON for an abstract.

    Args:
        storage: Storage client for data directory
        abstract_id: The abstract ID
        step_name: Step name (e.g., "drug_extraction", "indication_validation")

    Returns:
        Step output dict if found, None otherwise
    """
    return safe_load_json(storage, f"abstracts/{abstract_id}/{step_name}.json")


# =============================================================================
# ABSTRACT ID DISCOVERY
# =============================================================================


def list_abstract_ids(
    data_dir: str,
) -> list[str]:
    """List all abstract IDs that have output data.

    Discovers abstract IDs by listing subdirectories under abstracts/.

    Args:
        data_dir: Base data directory (local path or gs://bucket/prefix)

    Returns:
        Sorted list of abstract ID strings
    """
    if data_dir.startswith("gs://"):
        return _list_abstract_ids_gcs(data_dir)
    return _list_abstract_ids_local(data_dir)


def _list_abstract_ids_local(data_dir: str) -> list[str]:
    """List abstract IDs from local filesystem."""
    abstracts_dir = Path(data_dir) / "abstracts"
    if not abstracts_dir.exists():
        return []

    return sorted(
        d.name
        for d in abstracts_dir.iterdir()
        if d.is_dir() and (d / "status.json").exists()
    )


def _list_abstract_ids_gcs(data_dir: str) -> list[str]:
    """List abstract IDs from GCS."""
    bucket_name, prefix = parse_gcs_path(data_dir)

    # Build the prefix for listing: {prefix}/abstracts/
    abstracts_prefix = f"{prefix}/abstracts/" if prefix else "abstracts/"

    # Reuse GCSStorageClient to get a properly authenticated client
    gcs_client = GCSStorageClient(bucket_name, prefix)
    bucket = gcs_client.bucket

    # List blobs with delimiter to get directory-like prefixes
    iterator = bucket.list_blobs(prefix=abstracts_prefix, delimiter="/")

    # We need to consume the iterator to populate prefixes
    # (list_blobs is lazy and prefixes aren't available until pages are consumed)
    _ = list(iterator)

    abstract_ids = []
    for prefix_path in iterator.prefixes:
        # prefix_path looks like "prefix/abstracts/719267/"
        # Extract the abstract ID (last non-empty segment)
        parts = prefix_path.rstrip("/").split("/")
        abstract_id = parts[-1]
        if abstract_id:
            abstract_ids.append(abstract_id)

    return sorted(abstract_ids)


# =============================================================================
# FORMATTING HELPERS
# =============================================================================


def to_json_string(data: Any) -> str:
    """Convert data to pretty-printed JSON string for CSV cell.

    Returns empty string for None values.
    """
    if data is None:
        return ""
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(data)


def format_list_as_semicolons(data: list) -> str:
    """Convert list to double-semicolon separated string.

    Example:
        ["Drug A", "Drug B"] -> "Drug A;;Drug B"
        [] -> ""
    """
    if not data:
        return ""
    items = [str(item).strip() for item in data if item]
    return ";;".join(items) if items else ""


def format_dict_as_key_value(data: dict) -> str:
    """Convert dict with array values to key-value lines.

    Example:
        {"Drug A": ["Class 1", "Class 2"], "Drug B": ["Class 3"]}
        ->
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


def format_dict_as_key_value_skip_empty(data: dict) -> str:
    """Convert dict with array values to key-value lines, skipping empty arrays.

    Example:
        {"Drug A": [], "Drug B": ["Missed Class"]}
        ->
        "Drug B": Missed Class

        {"Drug A": [], "Drug B": []} -> ""
    """
    if not data:
        return ""

    lines = []
    for key, values in data.items():
        if isinstance(values, list) and values:
            values_str = ";;".join(str(v).strip() for v in values if v)
            if values_str:
                lines.append(f'"{key}": {values_str}')
        elif not isinstance(values, list) and values:
            lines.append(f'"{key}": {str(values).strip()}')

    return "\n".join(lines) if lines else ""


def extract_combined_drug_classes(step3_selections: dict, step5_classes: list) -> str:
    """Combine drug classes from step3 and step5, removing duplicates.

    Args:
        step3_selections: Dict of {drug_name: {"selected_drug_classes": [...], ...}}
        step5_classes: List of refined explicit classes from step5

    Returns:
        Double-semicolon separated string of unique drug classes (sorted)
    """
    all_classes = set()

    if step3_selections:
        for drug_name, result in step3_selections.items():
            if isinstance(result, dict):
                classes = result.get("selected_drug_classes", [])
                if isinstance(classes, list):
                    for c in classes:
                        if c and c != "NA":
                            all_classes.add(c.strip())

    if step5_classes:
        for c in step5_classes:
            if c and c != "NA":
                all_classes.add(c.strip())

    return ";;".join(sorted(all_classes)) if all_classes else ""


# =============================================================================
# CSV OUTPUT
# =============================================================================


def export_csv(
    rows: list[dict],
    fieldnames: list[str],
    output_path: str,
) -> None:
    """Export data to CSV file (supports both local and GCS paths).

    Args:
        rows: List of row dicts to write
        fieldnames: Column names in order
        output_path: Path to output CSV file (local path or gs://bucket/path)
    """
    # Write CSV content to string buffer
    output_buffer = io.StringIO()
    writer = csv.DictWriter(output_buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    csv_content = output_buffer.getvalue()

    if output_path.startswith("gs://"):
        bucket_name, full_prefix = parse_gcs_path(output_path)
        if "/" in full_prefix:
            base_prefix = "/".join(full_prefix.split("/")[:-1])
            csv_filename = full_prefix.split("/")[-1]
        else:
            base_prefix = ""
            csv_filename = full_prefix
        output_storage = GCSStorageClient(bucket_name, base_prefix)
        output_storage.upload_text(csv_filename, csv_content)
        print(f"\nCSV file saved to GCS: {output_path}")
    else:
        output_dir = Path(output_path).parent
        if output_dir and str(output_dir) != "." and not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            f.write(csv_content)

        print(f"\nCSV file saved: {output_path}")
