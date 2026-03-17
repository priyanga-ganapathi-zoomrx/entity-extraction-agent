# EMS Logging ECS Format Integration Plan

## Overview

This plan outlines the integration of EMS (Event Management System) logging using the standardized ECS (Elastic Common Schema) format across the entity-extraction-agent codebase. The implementation will update logging statements to directly log in nested ECS format and propagate transaction context throughout the workflow.

## Required ECS Format

```
transaction -> id, name, congress_id, session_id, session_title, workflow_id, batch_id
event_details -> entity, activity, input, output, error, attempt, labels, llm_calls, status, duration
llm -> model, prompt_file, input_tokens, output_tokens, total_tokens
```

**Field Definitions:**
- `entity`: "indication" | "drug" | "drug_class"
- `activity`: "extraction" | "validation"
- `labels`: dict with configurable key-value pairs (JSON serialized)
- `status`: "success" | "failure" | "skipped"
- `prompt_file`: GCS path for indication, "inline" for drug/drug_class

## Architecture Overview

```
CLI Input (congress_id, batch_id)
    ↓
Workflow (AbstractExtractionInput)
    ↓
Activities (pass context via input schemas)
    ↓
Build ECS Log Schema Directly
    ↓
EMS Logger (already handles ECS via ecs_logging)
    ↓
Nested ECS JSON → Pub/Sub
```

## Implementation Strategy

### 1. Add Logging Schemas and Helper to EMS Logger Module

**File:** `src/agents/core/ems_logger.py`

**Purpose:** Define structured schemas for ECS logging and provide a smart helper class that abstracts log event building from activity logic.

**Add at top of file after imports:**

```python
from dataclasses import dataclass, asdict
from typing import Optional, Protocol
from temporalio import activity

@dataclass
class TransactionLogSchema:
    """Transaction context for ECS logging."""
    id: str              # workflow_run_id
    name: str            # "entity_extraction"
    congress_id: int
    session_id: int
    session_title: str
    workflow_id: str
    batch_id: int

@dataclass
class LLMLogSchema:
    """LLM metadata for ECS logging."""
    model: str
    prompt_file: str
    input_tokens: int
    output_tokens: int
    total_tokens: int

@dataclass
class EventDetailsLogSchema:
    """Event details for ECS logging."""
    entity: str          # "indication" | "drug" | "drug_class"
    activity: str        # "extraction" | "validation"
    input: dict
    output: dict
    error: str
    attempt: int
    labels: dict
    llm_calls: int
    status: str          # "success" | "failure" | "skipped"
    duration: int        # milliseconds

def build_ecs_log_event(
    transaction: TransactionLogSchema,
    event_details: EventDetailsLogSchema,
    llm: LLMLogSchema,
) -> dict:
    """Build a complete ECS log event from schemas.

    Returns a dict ready to be passed to logger.info() or logger.error().
    """
    return {
        "transaction": asdict(transaction),
        "event_details": asdict(event_details),
        "llm": asdict(llm),
    }


class ActivityLogger:
    """Smart logger wrapper that handles ECS log event building automatically.

    Usage:
        logger = ActivityLogger(
            step_name="indication_extraction",
            entity="indication",
            activity="extraction",
            input_data=input_data,
            model="gemini/gemini-2.5-pro",
            prompt_file="gcs://prompts/...",
        )

        # In success case:
        logger.log_success(
            output={"generated_indication": "NSCLC"},
            labels={"num_rules": 5},
            tracker=tracker,
            duration_ms=1234,
        )

        # In error case:
        logger.log_error(
            error=e,
            labels={"rules_file": "..."},
            tracker=tracker,
            duration_ms=1234,
        )
    """

    def __init__(
        self,
        step_name: str,
        entity: str,
        activity: str,
        input_data,  # BaseActivityInput or subclass
        model: str,
        prompt_file: str = "inline",
    ):
        self.step_name = step_name
        self.entity = entity
        self.activity = activity
        self.input_data = input_data
        self.model = model
        self.prompt_file = prompt_file
        self.logger = get_logger(step_name)
        self.info = activity.info()

    def _build_transaction_schema(self) -> TransactionLogSchema:
        """Build transaction schema from input data and activity info."""
        return TransactionLogSchema(
            id=self.info.workflow_run_id,
            name="entity_extraction",
            congress_id=self.input_data.congress_id,
            session_id=self.input_data.abstract_id,
            session_title=self.input_data.abstract_title,
            workflow_id=f"entity_mapping_{self.entity}_{self.input_data.abstract_id}",
            batch_id=self.input_data.batch_id,
        )

    def _build_input_dict(self) -> dict:
        """Build input dict from input_data for logging."""
        # Base fields always included
        input_dict = {
            "abstract_id": self.input_data.abstract_id,
            "abstract_title": self.input_data.abstract_title,
        }

        # Add entity-specific fields if present
        if hasattr(self.input_data, "session_title"):
            input_dict["session_title"] = self.input_data.session_title
        if hasattr(self.input_data, "drug"):
            input_dict["drug"] = self.input_data.drug
        if hasattr(self.input_data, "full_abstract") and self.input_data.full_abstract:
            input_dict["full_abstract_present"] = True

        return input_dict

    def log_success(
        self,
        output: dict,
        labels: dict,
        tracker,  # TokenUsageCallbackHandler
        duration_ms: int,
    ) -> None:
        """Log a successful activity completion."""
        log_event = build_ecs_log_event(
            transaction=self._build_transaction_schema(),
            event_details=EventDetailsLogSchema(
                entity=self.entity,
                activity=self.activity,
                input=self._build_input_dict(),
                output=output,
                error="",
                attempt=self.info.attempt,
                labels=labels,
                llm_calls=tracker.llm_calls,
                status="success",
                duration=duration_ms,
            ),
            llm=LLMLogSchema(
                model=self.model,
                prompt_file=self.prompt_file,
                input_tokens=tracker.usage.input_tokens,
                output_tokens=tracker.usage.output_tokens,
                total_tokens=tracker.usage.total_tokens,
            ),
        )
        self.logger.info("step_completed", **log_event)

    def log_error(
        self,
        error: Exception,
        labels: dict,
        tracker,  # TokenUsageCallbackHandler
        duration_ms: int,
    ) -> None:
        """Log a failed activity execution."""
        log_event = build_ecs_log_event(
            transaction=self._build_transaction_schema(),
            event_details=EventDetailsLogSchema(
                entity=self.entity,
                activity=self.activity,
                input=self._build_input_dict(),
                output={},
                error=str(error),
                attempt=self.info.attempt,
                labels=labels,
                llm_calls=tracker.llm_calls,
                status="failure",
                duration=duration_ms,
            ),
            llm=LLMLogSchema(
                model=self.model,
                prompt_file=self.prompt_file,
                input_tokens=tracker.usage.input_tokens,
                output_tokens=tracker.usage.output_tokens,
                total_tokens=tracker.usage.total_tokens,
            ),
        )
        self.logger.error("step_failed", exc_info=True, **log_event)

    def log_skipped(
        self,
        reason: str,
        duration_ms: int,
    ) -> None:
        """Log a skipped activity (no LLM call made)."""
        log_event = build_ecs_log_event(
            transaction=self._build_transaction_schema(),
            event_details=EventDetailsLogSchema(
                entity=self.entity,
                activity=self.activity,
                input=self._build_input_dict(),
                output={},
                error="",
                attempt=self.info.attempt,
                labels={"skip_reason": reason},
                llm_calls=0,
                status="skipped",
                duration=duration_ms,
            ),
            llm=LLMLogSchema(
                model=self.model,
                prompt_file=self.prompt_file,
                input_tokens=0,
                output_tokens=0,
                total_tokens=0,
            ),
        )
        self.logger.info("step_skipped", **log_event)
```

**Rationale:**
- **ActivityLogger class:** Encapsulates all log building logic, keeping activity code clean
- **Automatic schema building:** Transaction and input schemas built from input_data automatically
- **Three methods:** `log_success()`, `log_error()`, `log_skipped()` cover all cases
- **No repetition:** Common logic (transaction schema, input dict) centralized
- **Type safety:** Still uses dataclasses under the hood
- **Readability:** Activity code becomes extremely clean and focused on business logic

### 2. Create Base Input Schema with Common Transaction Context

**Strategy:** Create a base schema with common fields (`abstract_id`, `abstract_title`, `congress_id`, `batch_id`) that all activity input schemas inherit from.

**File:** `src/agents/core/schemas.py` (NEW)

```python
from dataclasses import dataclass

@dataclass
class BaseActivityInput:
    """Base schema for all activity inputs with common transaction context."""
    abstract_id: int
    abstract_title: str
    congress_id: int = 0
    batch_id: int = 0
```

**Affected Schemas:**

#### A. Drug Schemas (`src/agents/drug/schemas.py`)

```python
from src.agents.core.schemas import BaseActivityInput

@dataclass
class DrugInput(BaseActivityInput):
    """Input for drug extraction - inherits transaction context."""
    pass  # No additional fields needed
```

#### B. Drug Class Schemas (`src/agents/drug_class/schemas.py`)

```python
from src.agents.core.schemas import BaseActivityInput

@dataclass
class RegimenInput(BaseActivityInput):
    """Input for drug class regimen step."""
    drug: str

@dataclass
class ExplicitMentionInput(BaseActivityInput):
    """Input for drug class explicit mention step."""
    drug: str
    full_abstract: str

@dataclass
class DrugClassValidationInput(BaseActivityInput):
    """Input for drug class validation."""
    full_abstract: str
    drug: str
    drug_classes: list[str]
```

#### C. Indication Schemas (`src/agents/indication/schemas.py`)

```python
from src.agents.core.schemas import BaseActivityInput

@dataclass
class IndicationInput(BaseActivityInput):
    """Input for indication extraction."""
    session_title: str = ""
    rules_file_path: str = ""
```

**Benefits:**
- DRY principle - common fields defined once
- Type safety inherited automatically
- Easy to add new common fields in future
- Clear inheritance hierarchy

### 3. Update Workflow Activity Invocations

**File:** `src/temporal/workflows/abstract_extraction.py`

**Pattern:** Pass `congress_id` and `batch_id` from `input` to all activity calls.

**Example for Drug Extraction (line ~487):**

```python
# BEFORE
extraction_result = await workflow.execute_activity(
    extract_drugs,
    DrugInput(
        abstract_id=input.abstract_id,
        abstract_title=input.abstract_title,
    ),
    ...
)

# AFTER
extraction_result = await workflow.execute_activity(
    extract_drugs,
    DrugInput(
        abstract_id=input.abstract_id,
        abstract_title=input.abstract_title,
        congress_id=input.congress_id,    # ADD
        batch_id=input.batch_id,          # ADD
    ),
    ...
)
```

**Apply to all activity calls:**
- Drug extraction (~line 487)
- Drug validation (~line 527)
- Drug class steps 1-5 and validation (~lines 589-900)
- Indication extraction (~line 534)
- Indication validation (~line 558)

### 4. Update Activity Implementations

**Pattern for all activities:**

1. Import `ActivityLogger` from ems_logger
2. Initialize `ActivityLogger` with step info at start of activity
3. Call `logger.log_success()` on success with output and labels
4. Call `logger.log_error()` on exception with error and labels
5. (Optional) Call `logger.log_skipped()` if activity skipped

**Reference Implementation - Indication Extraction:**

**File:** `src/temporal/activities/indication.py`

```python
# ADD import
from src.agents.core.ems_logger import ActivityLogger

@activity.defn(name="extract_indication")
def extract_indication(input_data: IndicationInput) -> dict:
    """Extract indication from abstract title and session title."""

    tracker = TokenUsageCallbackHandler()
    start = time.time()

    try:
        rules_data = _get_rules_data(input_data.rules_file_path)
        agent = IndicationAgent(rules_data=rules_data)

        # INITIALIZE LOGGER (abstracts all ECS complexity)
        logger = ActivityLogger(
            step_name="indication_extraction",
            entity="indication",
            activity="extraction",
            input_data=input_data,
            model=ind_config.LLM_MODEL,
            prompt_file=agent.prompt_file or "unknown",
        )

        raw_result = agent.invoke(
            abstract_title=input_data.abstract_title,
            session_title=input_data.session_title,
            abstract_id=input_data.abstract_id,
            callbacks=[tracker],
        )

        messages = raw_result.get("messages", [])
        result = _extract_result_from_messages(messages, ExtractionLLMResponse)
        duration_ms = int((time.time() - start) * 1000)

        # CLEAN LOGGING - just pass output and labels
        logger.log_success(
            output={
                "generated_indication": result.get("generated_indication"),
                "selected_source": result.get("selected_source"),
            },
            labels={
                "rules_file_path": input_data.rules_file_path,
                "num_rules_retrieved": len(result.get("rules_retrieved", [])),
            },
            tracker=tracker,
            duration_ms=duration_ms,
        )

        result["_token_usage"] = tracker.usage.to_dict()
        result["_llm_calls"] = tracker.llm_calls
        return result

    except Exception as e:
        duration_ms = int((time.time() - start) * 1000)

        # CLEAN ERROR LOGGING
        logger.log_error(
            error=e,
            labels={"rules_file_path": input_data.rules_file_path},
            tracker=tracker,
            duration_ms=duration_ms,
        )
        raise
```

**Comparison:**

**Before (verbose, 90+ lines):**
- Manually build TransactionLogSchema with 7 fields
- Manually build EventDetailsLogSchema with 10 fields
- Manually build LLMLogSchema with 5 fields
- Duplicate all this for error case
- Hard to read, focus on logging instead of business logic

**After (clean, ~50 lines):**
- Initialize ActivityLogger once with key params
- Call `log_success()` or `log_error()` with just output/labels
- All schema building happens automatically
- Business logic is clear and prominent

### 5. Prompt File Tracking

**For Indication Agent:**

Modify `src/agents/indication/extraction_agent.py` to expose prompt file path:

```python
class IndicationAgent:
    def __init__(self, rules_data: str):
        self.system_prompt, self.prompt_file = load_prompt(
            "MEDICAL_INDICATION_EXTRACTION_SYSTEM_PROMPT"
        )
        # Store prompt_file for logging
        # load_prompt returns (content, version_or_path)
```

Update `src/agents/core/prompts.py` to return path:

```python
def load_prompt(prompt_name: str) -> tuple[str, str]:
    """Load prompt and return (content, file_path)."""
    # If from GCS: return (content, f"gcs://{blob.name}")
    # If local: return (content, "inline")
```

**For Drug/Drug Class:**
Use `prompt_file="inline"` in all logging calls.

### 6. Labels Implementation

**Labels Guidelines:**
- Activity-specific metadata that doesn't fit standard ECS fields
- Store as dict, will be JSON serialized
- Keep concise (avoid large data)

**Examples per Activity:**

```python
# Indication extraction
labels = {
    "rules_file_path": input_data.rules_file_path,
    "num_rules_retrieved": len(result.get("rules_retrieved", [])),
}

# Drug extraction
labels = {
    "num_primary_drugs": len(result.get("primary_drugs", [])),
    "num_secondary_drugs": len(result.get("secondary_drugs", [])),
    "num_comparator_drugs": len(result.get("comparator_drugs", [])),
}

# Drug class step 2 (search)
labels = {
    "search_type": "tavily" or "grounded",
    "num_firms": len(firms),
    "cache_hit": True/False,
}

# Drug class validation
labels = {
    "num_drug_classes": len(drug_classes),
    "validation_passed": result.get("validation_status") == "PASS",
}
```

## Implementation Steps

### Phase 1: EMS Logger Enhancement
1. Add logging schemas to `src/agents/core/ems_logger.py`:
   - `TransactionLogSchema`
   - `EventDetailsLogSchema`
   - `LLMLogSchema`
   - `build_ecs_log_event()` helper function
   - `ActivityLogger` class with `log_success()`, `log_error()`, `log_skipped()`
2. Test ActivityLogger with mock data

### Phase 2: Base Schema Creation
3. Create `src/agents/core/schemas.py` with `BaseActivityInput`
4. Test base schema inheritance

### Phase 3: Schema Updates
5. Update `src/agents/drug/schemas.py` - extend BaseActivityInput
6. Update `src/agents/drug_class/schemas.py` - extend BaseActivityInput for all inputs
7. Update `src/agents/indication/schemas.py` - extend BaseActivityInput

### Phase 4: Prompt File Tracking
8. Modify `src/agents/core/prompts.py` - return tuple with file path
9. Update `src/agents/indication/extraction_agent.py` - store prompt_file
10. Update `src/agents/indication/validation_agent.py` - store prompt_file

### Phase 5: Workflow Updates
11. Update all activity invocations in `src/temporal/workflows/abstract_extraction.py`:
    - Drug extraction (line ~487)
    - Drug validation (line ~527)
    - Indication extraction (line ~534)
    - Indication validation (line ~558)
    - Drug class step1_regimen (line ~611)
    - Drug class step2_fetch_search_results (line ~645)
    - Drug class step3_selection (line ~689)
    - Drug class step4_explicit (line ~745)
    - Drug class step5_consolidation (line ~797)
    - Drug class validation (line ~849)

### Phase 6: Activity Updates - Indication (Reference Implementation)
12. Update `src/temporal/activities/indication.py`:
    - Import ActivityLogger from ems_logger
    - `extract_indication()`: Initialize ActivityLogger, call log_success/log_error
    - `validate_indication()`: Same pattern

### Phase 7: Activity Updates - Drug
13. Update `src/temporal/activities/drug.py`:
    - Import ActivityLogger from ems_logger
    - `extract_drugs()`: Initialize ActivityLogger (prompt_file="inline"), call log_success/log_error
    - `validate_drugs()`: Same pattern

### Phase 8: Activity Updates - Drug Class
14. Update `src/temporal/activities/drug_class.py`:
    - Import ActivityLogger from ems_logger
    - All 7 steps: Initialize ActivityLogger, call appropriate log method
    - step2_fetch_search_results: Use dummy tracker if no LLM call

## Testing Strategy

1. **Unit Test ECS Processor:**
   - Test with complete event_dict
   - Test with missing fields (defaults)
   - Test without transaction context

2. **Integration Test (Local):**
   - Run single indication extraction with EMS_ENABLED=false
   - Verify log structure in stdout
   - Confirm all nested fields present

3. **End-to-End Test (Dev):**
   - Run full workflow with all 3 entities
   - Verify Pub/Sub messages in GCP console
   - Confirm transaction context propagates correctly

## Migration Checklist

### EMS Logger
- [ ] Add `TransactionLogSchema` dataclass
- [ ] Add `EventDetailsLogSchema` dataclass
- [ ] Add `LLMLogSchema` dataclass
- [ ] Add `build_ecs_log_event()` helper function
- [ ] Add `ActivityLogger` class with methods:
  - [ ] `__init__()` - store activity context
  - [ ] `_build_transaction_schema()` - auto-build transaction
  - [ ] `_build_input_dict()` - auto-build input from input_data
  - [ ] `log_success()` - success logging
  - [ ] `log_error()` - error logging
  - [ ] `log_skipped()` - skipped logging
- [ ] Test ActivityLogger with mock data

### Base Schema
- [ ] Create `src/agents/core/schemas.py`
- [ ] Add `BaseActivityInput` with common fields
- [ ] Test inheritance with sample schema

### Entity Schemas
- [ ] Update DrugInput to extend BaseActivityInput (drug/schemas.py)
- [ ] Update RegimenInput to extend BaseActivityInput (drug_class/schemas.py)
- [ ] Update ExplicitMentionInput to extend BaseActivityInput (drug_class/schemas.py)
- [ ] Update DrugClassValidationInput to extend BaseActivityInput (drug_class/schemas.py)
- [ ] Update IndicationInput to extend BaseActivityInput (indication/schemas.py)

### Prompt Tracking
- [ ] Update load_prompt() to return tuple (core/prompts.py)
- [ ] Store prompt_file in IndicationAgent (indication/extraction_agent.py)
- [ ] Store prompt_file in IndicationValidationAgent (indication/validation_agent.py)

### Workflow Activity Calls
- [ ] Drug extraction invocation
- [ ] Drug validation invocation
- [ ] Indication extraction invocation
- [ ] Indication validation invocation
- [ ] Drug class step1 invocation
- [ ] Drug class step2 invocation
- [ ] Drug class step3 invocation
- [ ] Drug class step4 invocation
- [ ] Drug class step5 invocation
- [ ] Drug class validation invocation

### Activities - Indication
- [ ] Import ActivityLogger from ems_logger
- [ ] extract_indication: Initialize ActivityLogger
- [ ] extract_indication: Call logger.log_success() with output and labels
- [ ] extract_indication: Call logger.log_error() in except block
- [ ] validate_indication: Same pattern as extraction

### Activities - Drug
- [ ] Import ActivityLogger from ems_logger
- [ ] extract_drugs: Initialize ActivityLogger (prompt_file="inline")
- [ ] extract_drugs: Call logger.log_success() with output and labels
- [ ] extract_drugs: Call logger.log_error() in except block
- [ ] validate_drugs: Same pattern as extraction

### Activities - Drug Class
- [ ] Import ActivityLogger from ems_logger
- [ ] step1_regimen: Initialize ActivityLogger, call log_success/log_error
- [ ] step2_fetch_search_results: Initialize ActivityLogger, handle no-LLM case
- [ ] step3_selection: Initialize ActivityLogger, call log_success/log_error
- [ ] step4_explicit: Initialize ActivityLogger, call log_success/log_error
- [ ] step5_consolidation: Initialize ActivityLogger, call log_success/log_error
- [ ] validate_drug_class: Initialize ActivityLogger, call log_success/log_error

### Testing
- [ ] Unit test ActivityLogger with mock BaseActivityInput
- [ ] Verify ActivityLogger.log_success() output structure
- [ ] Verify ActivityLogger.log_error() output structure
- [ ] Verify ActivityLogger.log_skipped() output structure
- [ ] Test BaseActivityInput inheritance with all entity schemas
- [ ] Local integration test (indication) with EMS_ENABLED=false
- [ ] Local integration test (drug) with EMS_ENABLED=false
- [ ] Local integration test (drug_class) with EMS_ENABLED=false
- [ ] Dev environment E2E test with EMS_ENABLED=true
- [ ] Verify Pub/Sub message format in GCP
- [ ] Verify all transaction fields populated correctly

## Example ECS Log Output

**Success Case:**
```json
{
  "@timestamp": "2026-03-16T12:34:56.789Z",
  "log.level": "info",
  "message": "step_completed",
  "service.name": "entity-extraction-agent",
  "step_name": "indication_extraction",
  "transaction": {
    "id": "abc-123-temporal-run-id",
    "name": "entity_extraction",
    "workflow_id": "entity_mapping_indication_719267",
    "batch_id": 456,
    "congress_id": 123,
    "session_id": 719267,
    "session_title": "Pembrolizumab in NSCLC"
  },
  "event_details": {
    "entity": "indication",
    "activity": "extraction",
    "input": {
      "abstract_id": 719267,
      "abstract_title": "Pembrolizumab...",
      "session_title": "Lung Cancer"
    },
    "output": {
      "generated_indication": "Non-Small Cell Lung Cancer",
      "selected_source": "abstract_title"
    },
    "error": "",
    "attempt": 1,
    "labels": {
      "rules_file_path": "rules/indication/v3_rules.csv",
      "num_rules_retrieved": 5
    },
    "llm_calls": 1,
    "status": "success",
    "duration": 2340
  },
  "llm": {
    "model": "gemini/gemini-2.5-pro",
    "prompt_file": "gcs://prompts/indication/MEDICAL_INDICATION_EXTRACTION_SYSTEM_PROMPT.md",
    "input_tokens": 1250,
    "output_tokens": 450,
    "total_tokens": 1700
  }
}
```

**Error Case:**
```json
{
  "@timestamp": "2026-03-16T12:35:12.123Z",
  "log.level": "error",
  "message": "step_failed",
  "service.name": "entity-extraction-agent",
  "step_name": "drug_class_validation",
  "transaction": {
    "id": "xyz-456-temporal-run-id",
    "name": "entity_extraction",
    "workflow_id": "entity_mapping_drug_719268",
    "batch_id": 456,
    "congress_id": 123,
    "session_id": 719268,
    "session_title": "Nivolumab Trial Results"
  },
  "event_details": {
    "entity": "drug_class",
    "activity": "validation",
    "input": {
      "abstract_id": 719268,
      "drug": "Nivolumab",
      "drug_classes": ["PD-1 Inhibitor"]
    },
    "output": {},
    "error": "ValidationError: LLM response doesn't match schema",
    "attempt": 2,
    "labels": {
      "num_drug_classes": 1
    },
    "llm_calls": 1,
    "status": "failure",
    "duration": 1890
  },
  "llm": {
    "model": "anthropic/claude-sonnet-4-5",
    "prompt_file": "inline",
    "input_tokens": 890,
    "output_tokens": 120,
    "total_tokens": 1010
  }
}
```

## Key Files Modified

**New Files:**
1. `src/agents/core/schemas.py` - BaseActivityInput

**Modified Files:**
2. `src/agents/core/ems_logger.py` - Add logging schemas and ActivityLogger class
3. `src/agents/core/prompts.py` - Return prompt file path
4. `src/agents/drug/schemas.py` - Extend BaseActivityInput
5. `src/agents/drug_class/schemas.py` - Extend BaseActivityInput
6. `src/agents/indication/schemas.py` - Extend BaseActivityInput
7. `src/agents/indication/extraction_agent.py` - Store prompt_file
8. `src/agents/indication/validation_agent.py` - Store prompt_file
9. `src/temporal/workflows/abstract_extraction.py` - Pass context to activities
10. `src/temporal/activities/indication.py` - Use ActivityLogger for clean logging
11. `src/temporal/activities/drug.py` - Use ActivityLogger for clean logging
12. `src/temporal/activities/drug_class.py` - Use ActivityLogger for clean logging

**Total:** 1 new file, 11 modified files

## Rationale for Key Decisions

1. **BaseActivityInput inheritance:** DRY principle for common fields (abstract_id, abstract_title, congress_id, batch_id), type safety, easy maintenance
2. **ActivityLogger class:** Abstracts all ECS complexity from activities, dramatically improves readability, centralizes logging logic
3. **Logging schemas in ems_logger.py (not cgutils):** Module-specific, avoids unnecessary abstraction in shared package
4. **Direct schema construction (not context vars):** Explicit is better than implicit, easier to debug, no thread-local state
5. **Explicit dataclass fields over Temporal headers:** Type safety, IDE support, easier testing
6. **build_ecs_log_event() helper:** Centralizes schema-to-dict conversion, ensures consistent structure
7. **Three logging methods (success/error/skipped):** Clear separation of concerns, easy to use, handles all cases
8. **Dataclass schemas with descriptive names (LogSchema suffix):** Clear intent, type safety, autocomplete support
9. **Indication as reference:** Most complex (GCS prompts, rules), good template for others
10. **No backward compatibility:** Clean break, simpler implementation, aligns with user decision

## Success Criteria

✅ All 11 activities log in nested ECS format
✅ Transaction context (congress_id, batch_id, workflow_id) appears in all logs
✅ LLM metadata properly nested with prompt_file tracking
✅ Labels provide activity-specific insights
✅ No breaking changes to non-logging logic
✅ Pub/Sub receives well-formed ECS JSON
