"""Workflow status schemas for checkpointing.

Defines the structure of status.json that tracks workflow progress.
Used for resuming workflows from checkpoints.

Status Rules:
- Step entry only exists if the step has run (no "pending" status)
- "success" = step completed successfully
- "failed" = step completed with error

Example status.json:
{
  "abstract_id": "123",
  "abstract_title": "...",
  "status": "success",
  "last_updated": "2026-01-21T03:21:00Z",
  "metrics": {
    "duration_seconds": 45.2,
    "llm_calls": 8,
    "input_tokens": 15000,
    "output_tokens": 3500
  },
  "drug": {
    "extraction": {"status": "success", "llm_calls": 1, "tokens": 1550, "input_tokens": 1200, "output_tokens": 350},
    "validation": {"status": "success", "llm_calls": 1, "tokens": 1500, "input_tokens": 1100, "output_tokens": 400}
  },
  "drug_class": {
    "step1_regimen": {"status": "success", "llm_calls": 1, "tokens": 500, "input_tokens": 400, "output_tokens": 100},
    "step2_extraction": {"status": "success", "llm_calls": 2, "tokens": 3000, "input_tokens": 2500, "output_tokens": 500},
    "step3_selection": {"status": "success", "llm_calls": 1, "tokens": 800, "input_tokens": 600, "output_tokens": 200},
    "step4_explicit": {"status": "success", "llm_calls": 1, "tokens": 600, "input_tokens": 450, "output_tokens": 150},
    "step5_consolidation": {"status": "success", "llm_calls": 1, "tokens": 700, "input_tokens": 550, "output_tokens": 150},
    "validation": {"status": "success", "llm_calls": 1, "tokens": 1200, "input_tokens": 900, "output_tokens": 300}
  },
  "indication": {
    "extraction": {"status": "success", "llm_calls": 1, "tokens": 2000, "input_tokens": 1500, "output_tokens": 500},
    "validation": {"status": "success", "llm_calls": 1, "tokens": 2500, "input_tokens": 1800, "output_tokens": 700}
  },
  "errors": []
}
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


def _get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class StepStatus:
    """Status for a single step (extraction or validation)."""
    status: str  # "success" or "failed"
    llm_calls: int = 0
    tokens: int = 0  # Combined input + output tokens (backward compat)
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        result = {
            "status": self.status,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
        if self.error:
            result["error"] = self.error
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "StepStatus":
        return cls(
            status=data.get("status", "failed"),
            llm_calls=data.get("llm_calls", 0),
            tokens=data.get("tokens", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            error=data.get("error"),
        )
    
    @classmethod
    def success(
        cls,
        llm_calls: int = 1,
        tokens: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> "StepStatus":
        """Create a successful step status."""
        return cls(
            status="success",
            llm_calls=llm_calls,
            tokens=tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    
    @classmethod
    def failed(cls, error: str) -> "StepStatus":
        """Create a failed step status."""
        return cls(status="failed", error=error)

    @classmethod
    def skipped(cls) -> "StepStatus":
        """Create a skipped step status (step intentionally bypassed)."""
        return cls(status="skipped", llm_calls=0)


@dataclass
class PipelineMetrics:
    """Aggregate metrics for the workflow."""
    duration_seconds: float = 0.0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    
    def to_dict(self) -> dict:
        return {
            "duration_seconds": round(self.duration_seconds, 2),
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PipelineMetrics":
        return cls(
            duration_seconds=data.get("duration_seconds", 0.0),
            llm_calls=data.get("llm_calls", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
        )


@dataclass
class DrugPipelineStatus:
    """Status for the drug pipeline (extraction + validation)."""
    extraction: Optional[StepStatus] = None
    validation: Optional[StepStatus] = None
    
    def to_dict(self) -> dict:
        result = {}
        if self.extraction:
            result["extraction"] = self.extraction.to_dict()
        if self.validation:
            result["validation"] = self.validation.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "DrugPipelineStatus":
        extraction = None
        validation = None
        
        if "extraction" in data:
            extraction = StepStatus.from_dict(data["extraction"])
        if "validation" in data:
            validation = StepStatus.from_dict(data["validation"])
        
        return cls(extraction=extraction, validation=validation)
    
    def is_extraction_done(self) -> bool:
        """Check if extraction completed successfully."""
        return self.extraction is not None and self.extraction.status == "success"
    
    def is_validation_done(self) -> bool:
        """Check if validation completed successfully."""
        return self.validation is not None and self.validation.status == "success"
    
    def is_complete(self) -> bool:
        """Check if entire pipeline completed successfully."""
        return self.is_extraction_done() and self.is_validation_done()


@dataclass
class DrugClassPipelineStatus:
    """Status for the drug class pipeline (5 steps + validation)."""
    step1_regimen: Optional[StepStatus] = None
    step2_extraction: Optional[StepStatus] = None
    step3_selection: Optional[StepStatus] = None
    step4_explicit: Optional[StepStatus] = None
    step5_consolidation: Optional[StepStatus] = None
    validation: Optional[StepStatus] = None
    
    def to_dict(self) -> dict:
        result = {}
        if self.step1_regimen:
            result["step1_regimen"] = self.step1_regimen.to_dict()
        if self.step2_extraction:
            result["step2_extraction"] = self.step2_extraction.to_dict()
        if self.step3_selection:
            result["step3_selection"] = self.step3_selection.to_dict()
        if self.step4_explicit:
            result["step4_explicit"] = self.step4_explicit.to_dict()
        if self.step5_consolidation:
            result["step5_consolidation"] = self.step5_consolidation.to_dict()
        if self.validation:
            result["validation"] = self.validation.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "DrugClassPipelineStatus":
        def _get(key: str) -> Optional[StepStatus]:
            return StepStatus.from_dict(data[key]) if key in data else None
        return cls(
            step1_regimen=_get("step1_regimen"),
            step2_extraction=_get("step2_extraction"),
            step3_selection=_get("step3_selection"),
            step4_explicit=_get("step4_explicit"),
            step5_consolidation=_get("step5_consolidation"),
            validation=_get("validation"),
        )
    
    def is_steps1_3_done(self) -> bool:
        """Check if per-drug steps (1-3) completed successfully."""
        return (
            self.step1_regimen is not None and self.step1_regimen.status == "success"
            and self.step2_extraction is not None and self.step2_extraction.status == "success"
            and self.step3_selection is not None and self.step3_selection.status == "success"
        )
    
    def is_steps4_5_done(self) -> bool:
        """Check if per-abstract steps (4-5) completed successfully."""
        return (
            self.step4_explicit is not None and self.step4_explicit.status == "success"
            and self.step5_consolidation is not None and self.step5_consolidation.status == "success"
        )
    
    def is_validation_done(self) -> bool:
        """Check if validation completed successfully."""
        return self.validation is not None and self.validation.status == "success"
    
    def is_complete(self) -> bool:
        """Check if entire pipeline completed successfully."""
        steps1_3_done = self.is_steps1_3_done()
        steps1_3_skipped = (
            self.step1_regimen is not None and self.step1_regimen.status == "skipped"
            and self.step2_extraction is not None and self.step2_extraction.status == "skipped"
            and self.step3_selection is not None and self.step3_selection.status == "skipped"
        )
        return (steps1_3_done or steps1_3_skipped) and self.is_steps4_5_done() and self.is_validation_done()


@dataclass
class IndicationPipelineStatus:
    """Status for the indication pipeline (extraction + validation)."""
    extraction: Optional[StepStatus] = None
    validation: Optional[StepStatus] = None
    
    def to_dict(self) -> dict:
        result = {}
        if self.extraction:
            result["extraction"] = self.extraction.to_dict()
        if self.validation:
            result["validation"] = self.validation.to_dict()
        return result
    
    @classmethod
    def from_dict(cls, data: dict) -> "IndicationPipelineStatus":
        extraction = None
        validation = None
        if "extraction" in data:
            extraction = StepStatus.from_dict(data["extraction"])
        if "validation" in data:
            validation = StepStatus.from_dict(data["validation"])
        return cls(extraction=extraction, validation=validation)
    
    def is_extraction_done(self) -> bool:
        """Check if extraction completed successfully."""
        return self.extraction is not None and self.extraction.status == "success"
    
    def is_validation_done(self) -> bool:
        """Check if validation completed successfully."""
        return self.validation is not None and self.validation.status == "success"
    
    def is_complete(self) -> bool:
        """Check if entire pipeline completed successfully."""
        return self.is_extraction_done() and self.is_validation_done()


@dataclass
class WorkflowStatus:
    """Complete workflow status for an abstract.
    
    This is the structure saved to status.json for checkpointing.
    """
    abstract_id: str
    abstract_title: str = ""
    status: str = "running"  # "running", "success", "partial_success", "failed"
    last_updated: str = ""
    metrics: PipelineMetrics = field(default_factory=PipelineMetrics)
    drug: DrugPipelineStatus = field(default_factory=DrugPipelineStatus)
    drug_class: DrugClassPipelineStatus = field(default_factory=DrugClassPipelineStatus)
    indication: IndicationPipelineStatus = field(default_factory=IndicationPipelineStatus)
    errors: list[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.last_updated:
            self.last_updated = _get_timestamp()
    
    def to_dict(self) -> dict:
        return {
            "abstract_id": self.abstract_id,
            "abstract_title": self.abstract_title,
            "status": self.status,
            "last_updated": self.last_updated,
            "metrics": self.metrics.to_dict(),
            "drug": self.drug.to_dict(),
            "drug_class": self.drug_class.to_dict(),
            "indication": self.indication.to_dict(),
            "errors": self.errors,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowStatus":
        return cls(
            abstract_id=data.get("abstract_id", ""),
            abstract_title=data.get("abstract_title", ""),
            status=data.get("status", "running"),
            last_updated=data.get("last_updated", ""),
            metrics=PipelineMetrics.from_dict(data.get("metrics", {})),
            drug=DrugPipelineStatus.from_dict(data.get("drug", {})),
            drug_class=DrugClassPipelineStatus.from_dict(data.get("drug_class", {})),
            indication=IndicationPipelineStatus.from_dict(data.get("indication", {})),
            errors=data.get("errors", []),
        )
    
    def update_timestamp(self) -> None:
        """Update the last_updated timestamp."""
        self.last_updated = _get_timestamp()
    
    def mark_success(self) -> None:
        """Mark workflow as successful."""
        self.status = "success"
        self.update_timestamp()
    
    def mark_partial_success(self) -> None:
        """Mark workflow as partial success (some steps failed)."""
        self.status = "partial_success"
        self.update_timestamp()
    
    def mark_failed(self, error: str) -> None:
        """Mark workflow as failed with error."""
        self.status = "failed"
        self.errors.append(error)
        self.update_timestamp()
    
    def should_run_drug_pipeline(self) -> bool:
        """Check if drug pipeline needs to run."""
        return not self.drug.is_complete()
    
    def should_run_drug_class_pipeline(self) -> bool:
        """Check if drug class pipeline needs to run."""
        return not self.drug_class.is_complete()
    
    def should_run_indication_pipeline(self) -> bool:
        """Check if indication pipeline needs to run."""
        return not self.indication.is_complete()
    
    def aggregate_metrics(self, duration_seconds: float = 0.0) -> None:
        """Aggregate token usage from all steps into pipeline metrics.
        
        Sums llm_calls, input_tokens, and output_tokens from every step
        across all pipelines.
        
        Args:
            duration_seconds: Total workflow duration to record
        """
        all_steps: list[Optional[StepStatus]] = [
            # Drug pipeline
            self.drug.extraction,
            self.drug.validation,
            # Drug class pipeline
            self.drug_class.step1_regimen,
            self.drug_class.step2_extraction,
            self.drug_class.step3_selection,
            self.drug_class.step4_explicit,
            self.drug_class.step5_consolidation,
            self.drug_class.validation,
            # Indication pipeline
            self.indication.extraction,
            self.indication.validation,
        ]
        
        total_llm_calls = 0
        total_input_tokens = 0
        total_output_tokens = 0
        
        for step in all_steps:
            if step and step.status == "success":
                total_llm_calls += step.llm_calls
                total_input_tokens += step.input_tokens
                total_output_tokens += step.output_tokens
        
        self.metrics = PipelineMetrics(
            duration_seconds=duration_seconds,
            llm_calls=total_llm_calls,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )
