"""Workflow input/output schemas for the abstract extraction workflow.

These are pure data classes with no dependency on Temporal's workflow module.
They are imported by the workflow via `workflow.unsafe.imports_passed_through()`.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AbstractExtractionInput:
    """Input for the abstract extraction workflow.

    Best Practice: Single dataclass input allows adding fields
    without breaking existing workflow executions.
    """
    abstract_id: str
    abstract_title: str
    session_title: str = ""
    full_abstract: str = ""
    firms: list[str] = field(default_factory=list)

    # Which entity pipeline to run: "drug" (includes drug_class) or "indication"
    entity: str = ""

    # Batch/congress context for SQL status tracking and GCS result/cache paths.
    # GCS paths are constructed from GCS_BUCKET_NAME env + congress_id/batch_id.
    congress_id: int = 0
    batch_id: int = 0

    # Relative path to indication rules CSV within GCS_BUCKET_NAME
    # (e.g. "rules/indication/v3_rules.csv"). Only used when entity="indication".
    rules_file_path: str = ""


@dataclass
class StepResult:
    """Result of a single pipeline step execution.

    Wraps the activity output with status tracking.
    Token usage metadata is embedded by activities in their output dicts.
    """
    status: str  # "success" or "failed"
    output: Optional[dict] = None
    error: Optional[str] = None
    token_usage: Optional[dict] = None
    llm_calls: int = 1


@dataclass
class DrugResult:
    """Result of drug extraction and validation."""
    extraction: dict = field(default_factory=dict)
    validation: Optional[dict] = None


@dataclass
class DrugClassResult:
    """Result of drug class pipeline."""
    drug_results: list[dict] = field(default_factory=list)
    explicit_classes: list[str] = field(default_factory=list)
    refined_explicit_classes: list[str] = field(default_factory=list)
    validation_results: list[dict] = field(default_factory=list)


@dataclass
class IndicationResult:
    """Result of indication extraction and validation."""
    extraction: dict = field(default_factory=dict)
    validation: Optional[dict] = None


@dataclass
class AbstractExtractionOutput:
    """Output from the abstract extraction workflow."""
    abstract_id: str
    drug: DrugResult = field(default_factory=DrugResult)
    drug_class: DrugClassResult = field(default_factory=DrugClassResult)
    indication: IndicationResult = field(default_factory=IndicationResult)
    completed: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        """Derive status from completion state and errors."""
        if not self.completed:
            return "failed"
        if self.errors:
            return "partial_success"
        return "success"
