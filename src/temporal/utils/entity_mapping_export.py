"""XLSX generation for entity mapping batch results.

Reads step JSON files from GCS and transforms them into XLSX files
for batch-level and congress-level result downloads.

Reuses transform functions from src/scripts/temporal/ exporters.
"""

import io
import logging
from typing import Union

from openpyxl import Workbook

from src.agents.core.storage import GCSStorageClient, LocalStorageClient
from src.scripts.temporal.utils import safe_load_json
from src.scripts.temporal.indication_exporter import (
    EXTRACTION_COLUMNS as INDICATION_EXTRACTION_COLUMNS,
    VALIDATION_COLUMNS as INDICATION_VALIDATION_COLUMNS,
    transform_indication_extraction,
    transform_indication_validation,
)
from src.scripts.temporal.drug_drug_class_exporter import (
    NEW_COLUMNS as DRUG_DRUG_CLASS_COLUMNS,
    get_combined_classes,
    transform_drug_class_step1,
    transform_drug_class_step2,
    transform_drug_class_step3,
    transform_drug_class_step4,
    transform_drug_class_step5,
    transform_drug_class_validation,
    transform_drug_extraction,
    transform_drug_validation,
)

logger = logging.getLogger(__name__)


# ── Column definitions ──────────────────────────────────────────────

SESSION_COLUMNS = ["session_id", "title", "abstract"]

# Drug-only columns = first 10 of NEW_COLUMNS (4 extraction + 6 validation)
DRUG_COLUMNS = DRUG_DRUG_CLASS_COLUMNS[:10]

ENTITY_COLUMNS: dict[str, list[str]] = {
    "indication": SESSION_COLUMNS + INDICATION_EXTRACTION_COLUMNS + INDICATION_VALIDATION_COLUMNS,
    "drug": SESSION_COLUMNS + DRUG_COLUMNS,
    "drug_class": SESSION_COLUMNS + DRUG_DRUG_CLASS_COLUMNS,
}


# ── Step file mapping ───────────────────────────────────────────────
# Maps entity -> {gcs_entity_folder: [step_names]}
# The GCS path is: congress/{cid}/batches/{bid}/{entity_folder}/{session_id}/{step}.json

ENTITY_STEP_FILES: dict[str, dict[str, list[str]]] = {
    "indication": {"indication": ["extraction", "validation"]},
    "drug": {"drug": ["extraction", "validation"]},
    "drug_class": {
        "drug": ["extraction", "validation"],
        "drug_class": ["steps1_3", "step4", "step5", "validation"],
    },
}


# ── Transform functions ─────────────────────────────────────────────


def _transform_indication(step_data: dict) -> dict:
    """Transform indication step data to row columns."""
    row = {}
    row.update(transform_indication_extraction(step_data.get("indication__extraction")))
    row.update(transform_indication_validation(
        step_data.get("indication__validation"), status=None
    ))
    return row


def _transform_drug(step_data: dict) -> dict:
    """Transform drug step data to row columns."""
    row = {}
    row.update(transform_drug_extraction(step_data.get("drug__extraction")))
    row.update(transform_drug_validation(step_data.get("drug__validation")))
    return row


def _transform_drug_class(step_data: dict) -> dict:
    """Transform drug + drug_class step data to row columns."""
    row = _transform_drug(step_data)

    steps1_3 = step_data.get("drug_class__steps1_3")
    row.update(transform_drug_class_step1(steps1_3))
    row.update(transform_drug_class_step2(steps1_3))
    row.update(transform_drug_class_step3(steps1_3))
    row.update(transform_drug_class_step4(step_data.get("drug_class__step4")))
    row.update(transform_drug_class_step5(step_data.get("drug_class__step5")))
    row["drug_class_combined_all_classes"] = get_combined_classes(
        steps1_3, step_data.get("drug_class__step5")
    )
    row.update(transform_drug_class_validation(step_data.get("drug_class__validation")))
    return row


ENTITY_TRANSFORMS = {
    "indication": _transform_indication,
    "drug": _transform_drug,
    "drug_class": _transform_drug_class,
}


# ── XLSX helpers ────────────────────────────────────────────────────


def _rows_to_xlsx(columns: list[str], rows: list[dict]) -> bytes:
    """Convert list of row dicts to XLSX bytes."""
    wb = Workbook()
    ws = wb.active
    ws.append(columns)
    for row in rows:
        ws.append([row.get(col, "") for col in columns])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _load_step_data(
    storage: Union[GCSStorageClient, LocalStorageClient],
    congress_id: int,
    batch_id: int,
    entity: str,
    session_id: int,
) -> dict:
    """Load all step JSON files for a session and return namespaced dict.

    Keys are "{entity_folder}__{step_name}" to avoid collision
    (e.g. drug__validation vs drug_class__validation).
    """
    step_data = {}
    step_files = ENTITY_STEP_FILES.get(entity, {})

    for entity_folder, step_names in step_files.items():
        for step_name in step_names:
            path = (
                f"congress/{congress_id}/batches/{batch_id}"
                f"/{entity_folder}/{session_id}/{step_name}.json"
            )
            key = f"{entity_folder}__{step_name}"
            step_data[key] = safe_load_json(storage, path)

    return step_data


# ── Public API ──────────────────────────────────────────────────────


def generate_entity_xlsx(
    storage: Union[GCSStorageClient, LocalStorageClient],
    congress_id: int,
    entity: str,
    session_batch_map: dict[int, int],
    session_info: dict[int, dict[str, str]],
) -> bytes:
    """Generate XLSX content for an entity from GCS step JSON files.

    Args:
        storage: Storage client for reading step files
        congress_id: Congress ID
        entity: Entity type ("drug", "drug_class", "indication")
        session_batch_map: session_id -> batch_id (for GCS path lookup)
        session_info: session_id -> {"title": ..., "abstract": ...}

    Returns:
        XLSX file content as bytes
    """
    columns = ENTITY_COLUMNS[entity]
    transform_fn = ENTITY_TRANSFORMS[entity]
    rows = []

    for session_id, batch_id in session_batch_map.items():
        info = session_info.get(session_id, {})

        # Load step JSON files from GCS
        step_data = _load_step_data(storage, congress_id, batch_id, entity, session_id)

        # Transform to row
        row = {
            "session_id": session_id,
            "title": info.get("title", ""),
            "abstract": info.get("abstract", ""),
        }
        row.update(transform_fn(step_data))
        rows.append(row)

    logger.info(
        f"Generated {entity} XLSX: {len(rows)} rows, congress={congress_id}"
    )
    return _rows_to_xlsx(columns, rows)
