# Abstract Extraction Workflow

Technical reference for the `AbstractExtractionWorkflow` — the Temporal workflow that orchestrates entity extraction from congress session abstracts.

**Source:** `src/temporal/workflows/abstract_extraction.py`

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Database Tables](#database-tables)
3. [Workflow Design](#workflow-design)
4. [Entity Pipelines](#entity-pipelines)
5. [DB Update Sequence](#db-update-sequence)
6. [Failure Handling & Retry](#failure-handling--retry)
7. [Batch Finalization](#batch-finalization)
8. [XLSX Export](#xlsx-export)
9. [GCS Storage Layout](#gcs-storage-layout)
10. [Worker Architecture](#worker-architecture)
11. [Timeout & Retry Configuration](#timeout--retry-configuration)
12. [End-to-End Data Flow Example](#end-to-end-data-flow-example)
13. [AP Server API Endpoints](#ap-server-api-endpoints)

---

## Architecture Overview

```
AP Server (congress-ap-server)
  │
  │  POST /batches → seeds DB rows, starts Temporal workflows
  │
  ▼
Temporal Server
  │
  ├── extraction-workflows          (workflow worker — orchestration only)
  │     └── AbstractExtractionWorkflow
  │           │
  │           ├── drug-activities           (drug worker)
  │           ├── drug-class-activities     (drug class worker)
  │           ├── indication-extraction     (indication extraction worker)
  │           ├── indication-validation-slow (indication validation worker)
  │           ├── entity-mapping-progress   (progress worker — SQL updates)
  │           ├── result-storage            (storage worker — GCS uploads)
  │           └── entity-mapping-progress   (batch finalization — XLSX + notify)
  │
  ▼
MySQL DB                    GCS Bucket (fc-{env}-entity-extraction-agent)
(entity_mapping_* tables)   (step JSONs, result XLSXs, rules CSVs)
```

Each workflow is **one long-lived execution per (session, entity)** combination. The entity is either `"drug"` (which also runs the drug class pipeline) or `"indication"`.

---

## Database Tables

### `entity_mapping_batches`

Batch-level metadata. **Created by AP server, finalized by the extraction agent.**

| Column | Type | Notes |
|--------|------|-------|
| id | BigInteger (PK) | Auto-increment |
| congress_id | Integer | FK to congresses |
| entities | JSON | e.g. `["drug", "drug_class"]` |
| rules_file_path | String(512) | GCS path to rules CSV (required for indication) |
| status | String(16) | `pending` → `running` → `completed` / `partial` / `failed` / `aborted` |
| triggered_by_id | String(200) | User ID who triggered |
| completed_at | DateTime | Set by `check_and_finalize_batch` activity |
| created_at | Timestamp | |
| last_modified_at | Timestamp | |

**Constraint:** Only one batch with status `pending` or `running` per congress (enforced by AP server).

**Who writes:**
- AP server: creates row with `status=pending`, updates to `running` after workflow starts
- `check_and_finalize_batch` activity: sets final status + `completed_at`
- AP server abort endpoint: sets `status=aborted`

---

### `entity_mapping_batches_sessions`

Per-session, per-entity progress within a batch.

| Column | Type | Notes |
|--------|------|-------|
| id | BigInteger (PK) | Auto-increment |
| batch_id | BigInteger | FK to batches |
| session_id | Integer | FK to sessions |
| entity | String(16) | `drug`, `drug_class`, `indication` |
| status | String(16) | `pending` → `running` → `success` / `failed` / `aborted` |
| created_at | DateTime | |
| last_modified_at | DateTime | |

**Unique constraint:** `(batch_id, session_id, entity)`

One row per (batch, session, entity) combination. For a `"drug"` entity workflow, **two rows** exist: one for `drug` and one for `drug_class`.

**Who writes:**
- AP server: bulk-inserts rows with `status=pending` when batch is created
- `update_extraction_progress` activity: updates status as workflow progresses
- AP server abort endpoint: bulk-updates to `aborted`

---

### `entity_mapping_sessions`

Congress-level latest status per (session, entity). Provides the "current state" view across all batches.

| Column | Type | Notes |
|--------|------|-------|
| id | BigInteger (PK) | Auto-increment |
| congress_id | Integer | |
| session_id | Integer | FK to sessions |
| entity | String(16) | `drug`, `drug_class`, `indication` |
| last_batch_id | BigInteger | Most recent batch that processed this session+entity |
| status | String(16) | `pending`, `running`, `success`, `failed`, `aborted` |
| created_at | DateTime | |
| last_modified_at | DateTime | |

**Unique constraint:** `(congress_id, session_id, entity)`

**Key principle:** Only the Temporal workflow writes to this table (via `update_extraction_progress` activity). AP server reads it for dashboard stats, retry preview, and retry targeting.

**Write mechanism:** MySQL `INSERT ... ON DUPLICATE KEY UPDATE` (upsert). First run inserts; subsequent runs (retries, new batches) update status and `last_batch_id`.

---

### Read-Only Tables (owned by AP server)

| Table | Columns Used | Purpose |
|-------|-------------|---------|
| `sessions` | id, congress_id, title, abstract, full_abstract_text | XLSX export, workflow input |
| `congresses` | id, name | Teams notification display |
| `users` | id, first_name, last_name | Teams notification display |

---

## Workflow Design

### Registration

| Setting | Value |
|---------|-------|
| Workflow name | `AbstractExtractionWorkflow` |
| Task queue | `extraction-workflows` |
| Workflow ID format | `entity_mapping_{workflow_entity}_{session_id}` |
| Conflict policy | `TERMINATE_EXISTING` |
| Execution timeout | 72 hours |

### Entity-to-Workflow Mapping

`drug` and `drug_class` entities both run under a single `"drug"` workflow. `indication` has its own workflow.

```
Entity "drug"       → workflow entity = "drug"   → runs drug pipeline + drug class pipeline
Entity "drug_class"  → workflow entity = "drug"   → (handled within same workflow)
Entity "indication"  → workflow entity = "indication" → runs indication pipeline
```

### Input

```python
@dataclass
class AbstractExtractionInput:
    abstract_id: int           # session ID
    abstract_title: str        # session title (used for extraction)
    entity: str                # "drug" or "indication"
    congress_id: int
    batch_id: int
    session_title: str = ""    # separate session title field
    full_abstract: str = ""    # full abstract text
    firms: list[str] = []      # sponsor firms (for drug class search)
    rules_file_path: str = ""  # GCS path to indication rules CSV
```

### Instance State

The workflow maintains state across retry loops (not checkpointed to external storage — Temporal event history handles durability):

| State Variable | Type | Purpose |
|----------------|------|---------|
| `_output` | `AbstractExtractionOutput` | Accumulated extraction results |
| `_current_status` | `str` | pending/running/failed/success/aborted |
| `_retry_requested` | `bool` | Set by retry signal |
| `_abort_requested` | `bool` | Set by abort signal |
| `_current_entity` | `str` | Currently executing entity (drug/drug_class/indication) |
| `_completed_steps` | `set[str]` | Completed pipelines: `drug_pipeline`, `drug_class_pipeline`, `indication_pipeline` |
| `_dc_per_drug_data` | `dict` | Drug class steps 1-3 results keyed by drug name |
| `_dc_step4_result` | `dict\|None` | Explicit extraction result |
| `_dc_step5_output` | `dict\|None` | Consolidation result (`None`=pending, `{}`=skipped) |
| `_dc_validation_data` | `dict\|None` | Drug class validation results |

### Signals & Queries

| Type | Name | Purpose |
|------|------|---------|
| Signal | `retry()` | Admin triggers retry of failed pipeline |
| Signal | `abort()` | Admin triggers workflow abort |
| Query | `status()` | Returns current status string |
| Query | `get_output()` | Returns accumulated `AbstractExtractionOutput` |

---

## Entity Pipelines

### Drug Pipeline (`entity="drug"`)

Runs two sub-pipelines sequentially:

#### 1. Drug Extraction + Validation

```
extract_drugs(DrugInput) → dict
  ├── Output: primary_drugs, secondary_drugs, comparator_drugs, reasoning
  ├── Save to GCS: congress/{cid}/batches/{bid}/drug/{sid}/extraction.json
  └── Store in: self._output.drug.extraction

validate_drugs(DrugValidationInput) → dict
  ├── Output: validation_status (PASS/REVIEW/FAIL), missed_drugs, issues_found
  ├── Save to GCS: congress/{cid}/batches/{bid}/drug/{sid}/validation.json
  └── Store in: self._output.drug.validation
```

**Task queue:** `drug-activities` | **Timeout:** 4 min | **Max retries:** 3

#### 2. Drug Class Pipeline (5 steps + validation)

Branches based on whether primary drugs were found:

**Case A: Primary drugs exist** → Full pipeline (steps 1-5 + validation)

**Steps 1-3 (per-drug loop over primary_drugs):**

```
For each primary drug:
  Step 1: step1_regimen(drug)
    → Decompose regimen into components (e.g. "R-CHOP" → ["rituximab", ...])
    → Single drugs return [drug]

  For each component:
    Step 2a: step2_fetch_search_results(component, firms, congress_id, abstract_id)
      → Fetch drug class info via Tavily API (cached at congress level in GCS)

    Step 2b: step2_extract_with_tavily(component, search_results)
      → LLM extracts drug classes from search results
      → If returns NA/empty: FALLBACK to step2_extract_with_grounded()
        → Uses LLM built-in web search (web_search_preview)

    Step 3: step3_selection(component, extraction_details)
      → Select best drug class if multiple found
      → Priority: MoA > Chemical > Mode > Therapeutic

  Store immediately in self._dc_per_drug_data[drug]
  (available even if next drug fails on retry)

Save aggregate: congress/{cid}/batches/{bid}/drug_class/{sid}/steps1_3.json
```

**Step 4: Explicit extraction from title**

```
step4_explicit(abstract_title)
  → LLM extracts drug classes explicitly mentioned in the title
  → Save: congress/{cid}/batches/{bid}/drug_class/{sid}/step4.json
```

**Step 5: Consolidation**

```
step5_consolidation(explicit_classes, drug_selections)
  → Merge explicit classes with drug-derived classes, remove duplicates
  → Skipped if explicit is empty/NA (sets _dc_step5_output = {})
  → Save: congress/{cid}/batches/{bid}/drug_class/{sid}/step5.json
```

**Step 6: Validation (per component)**

```
For each component with extracted drug classes (not NA):
  Re-fetch search results (uses cache)
  validate_drug_class_activity(component, extraction, selections, explicit, refined)
    → 5 validation checks: hallucination, omission, rule compliance,
      title extraction compliance, selection rule compliance

Save: congress/{cid}/batches/{bid}/drug_class/{sid}/validation.json
```

**Task queue:** `drug-class-activities` | **Timeout:** 4 min (search: 4 min) | **Max retries:** 3

**Case B: No primary drugs** → Explicit-only pipeline

```
Step 4: step4_explicit() → Extract explicit classes from title
Step 5: No consolidation — explicit classes used directly, saved for exporter
Step 6: Validation runs even with empty/NA classes (single validation, drug_name="")
```

---

### Indication Pipeline (`entity="indication"`)

```
extract_indication(IndicationInput) → dict
  ├── LangGraph agent with tool calling for rules retrieval from GCS CSV
  ├── Output: selected_source, generated_indication, rules_retrieved, reasoning
  ├── Save to GCS: congress/{cid}/batches/{bid}/indication/{sid}/extraction.json
  └── Store in: self._output.indication.extraction

validate_indication(IndicationInput, extraction_result) → dict
  ├── LangGraph agent with 7 validation checks:
  │   source selection, hallucination, omission, rule application,
  │   exclusion compliance, formatting, abbreviation
  ├── Output: validation_status (PASS/REVIEW/FAIL), issues_found, checks_performed
  ├── Save to GCS: congress/{cid}/batches/{bid}/indication/{sid}/validation.json
  └── Store in: self._output.indication.validation
```

**Extraction task queue:** `indication-extraction` | **Timeout:** 4 min | **Max retries:** 3
**Validation task queue:** `indication-validation-slow` | **Timeout:** 5 min | **Max retries:** 2

---

## DB Update Sequence

Every DB write goes through the `update_extraction_progress` activity on the `entity-mapping-progress` task queue. Each call performs a single transaction:

1. **UPDATE** `entity_mapping_batches_sessions` — set status for `(batch_id, session_id, entity)`
2. **UPSERT** `entity_mapping_sessions` — insert or update congress-level latest status

### Drug Entity Workflow (`entity="drug"`)

```
Workflow starts
  │
  ├─ update_extraction_progress(drug, "running")
  │    DB: batches_sessions[drug] = running
  │    DB: sessions[drug] = running
  │
  ├─ ... drug extraction + validation activities ...
  │
  ├─ update_extraction_progress(drug, "success")
  │    DB: batches_sessions[drug] = success
  │    DB: sessions[drug] = success
  │
  ├─ update_extraction_progress(drug_class, "running")
  │    DB: batches_sessions[drug_class] = running
  │    DB: sessions[drug_class] = running
  │
  ├─ ... drug class pipeline activities ...
  │
  ├─ update_extraction_progress(drug_class, "success")
  │    DB: batches_sessions[drug_class] = success
  │    DB: sessions[drug_class] = success
  │
  └─ check_and_finalize_batch()
       DB: batches.status = completed/partial/failed (if all sessions done)
       GCS: XLSX files generated and uploaded
```

### Indication Entity Workflow (`entity="indication"`)

```
Workflow starts
  │
  ├─ update_extraction_progress(indication, "running")
  │    DB: batches_sessions[indication] = running
  │    DB: sessions[indication] = running
  │
  ├─ ... indication extraction + validation activities ...
  │
  ├─ update_extraction_progress(indication, "success")
  │    DB: batches_sessions[indication] = success
  │    DB: sessions[indication] = success
  │
  └─ check_and_finalize_batch()
       DB: batches.status = completed/partial/failed (if all sessions done)
       GCS: XLSX files generated and uploaded
```

### On Failure

```
Pipeline activity throws exception
  │
  ├─ update_extraction_progress(current_entity, "failed")
  │    DB: batches_sessions[entity] = failed
  │    DB: sessions[entity] = failed
  │
  ├─ If drug pipeline failed and drug_class not yet completed:
  │    update_extraction_progress(drug_class, "failed")
  │    (prevents batch finalization from being blocked by "pending" drug_class)
  │
  ├─ check_and_finalize_batch()
  │    (may finalize if this was the last session)
  │
  └─ Workflow pauses via workflow.wait_condition()
       Waits for retry or abort signal...
```

### On Abort (via signal)

```
Abort signal received
  │
  ├─ update_extraction_progress(current_entity, "aborted")    [fire-and-forget]
  │
  ├─ If drug entity and drug_class not completed:
  │    update_extraction_progress(drug_class, "aborted")      [fire-and-forget]
  │
  ├─ check_and_finalize_batch()                               [fire-and-forget]
  │
  └─ Workflow returns output
```

### On Cancellation (Temporal cancel)

```
asyncio.CancelledError caught in run()
  │
  ├─ asyncio.shield(update_extraction_progress(entity, "aborted"))
  │    (shielded so it runs despite cancellation)
  │
  ├─ If drug entity and drug_class not completed:
  │    asyncio.shield(update_extraction_progress(drug_class, "aborted"))
  │
  └─ Re-raises CancelledError (workflow terminates as CANCELLED)
```

### On Retry (via signal)

```
Retry signal received (after pause)
  │
  ├─ Clear errors list
  │
  ├─ If drug entity failed and drug_class was force-failed:
  │    update_extraction_progress(drug_class, "pending")
  │    (reset so it can run after drug succeeds)
  │
  └─ Loop restarts from the top
       Each pipeline method checks self._output / self._dc_* state
       to skip already-completed steps and resume from failure point
```

---

## Failure Handling & Retry

### Pause/Resume Pattern

This is **not** Temporal replay. It is application-level skip logic using workflow instance state:

1. Activity fails → exception propagates to `_execute_pipeline`
2. Workflow updates DB status to `failed` (blocking await)
3. Calls `check_and_finalize_batch` (may finalize if last session)
4. Pauses via `workflow.wait_condition()` — stays alive indefinitely
5. Admin sends `retry` signal → `_retry_requested = True` → condition unblocks
6. `while True` loop restarts — each pipeline method checks `self._output` and `self._dc_*` state to skip completed steps
7. Execution resumes from the failed step

### Skip Logic

| Pipeline | Skip Condition |
|----------|----------------|
| Drug extraction | `self._output.drug.extraction` is truthy |
| Drug validation | `self._output.drug.validation` is truthy |
| Drug class steps 1-3 (per drug) | `drug in self._dc_per_drug_data` |
| Drug class step 4 | `self._dc_step4_result is not None` |
| Drug class step 5 | `self._dc_step5_output is not None` (`{}` = skipped, `{data}` = ran) |
| Drug class validation | `self._dc_validation_data is not None` |
| Drug pipeline (whole) | `"drug_pipeline" in self._completed_steps` |
| Drug class pipeline (whole) | `"drug_class_pipeline" in self._completed_steps` |
| Indication extraction | `self._output.indication.extraction` is truthy |
| Indication validation | `self._output.indication.validation` is truthy |
| Indication pipeline (whole) | `"indication_pipeline" in self._completed_steps` |

### Cross-Entity Failure Cascade

When the drug pipeline fails, the drug_class `batches_sessions` row is also marked `failed` to prevent batch finalization from being blocked by a `pending` drug_class row. On retry, it is reset to `pending`.

---

## Batch Finalization

**Activity:** `check_and_finalize_batch` in `src/temporal/activities/check_and_finalize_batch.py`

Called after every workflow completes (success or failure). Multiple workflows may call it concurrently.

### Logic

```
1. Query batch — return early if status is NOT 'running'
2. Count sessions with status IN ('pending', 'running') — return early if > 0
3. GROUP BY status to determine final batch status:
     All 'success'         → 'completed'
     All 'failed'          → 'failed'
     All 'aborted'         → 'aborted'
     Mix with any 'success' → 'partial'
     Other mix              → 'failed'
4. Atomic UPDATE: SET status + completed_at WHERE id=X AND status='running'
     (second concurrent call gets rows_updated=0 and returns)
5. If status is failed/aborted → skip XLSX generation
6. Generate & upload XLSX for each entity (batch-level + congress-level)
7. Send Teams notification with per-entity status counts
```

### Concurrency Safety

- Step 1-2: Natural guard — returns early if batch not ready
- Step 4: Atomic `WHERE status='running'` ensures exactly one caller wins
- Steps 5-7: XLSX upload is idempotent (overwrites same blob)

### Teams Notification

Sent after finalization with:
- Congress name, batch ID, final status
- Total sessions count
- Per-entity success/failed/aborted counts
- Triggered by (user name)
- Total duration

---

## XLSX Export

**Module:** `src/temporal/utils/entity_mapping_export.py`

### Generation Flow

1. Query successful session IDs from DB
2. For each session, load step JSON files from GCS
3. Transform step data to flat row using entity-specific transform functions
4. Write all rows to XLSX using openpyxl

### Two Scopes

| Scope | Query Source | GCS Path | Output Path |
|-------|-------------|----------|-------------|
| Batch-level | `batches_sessions WHERE batch_id AND entity AND status='success'` | Same `batch_id` for all sessions | `congress/{cid}/batches/{bid}/results/{entity}.xlsx` |
| Congress-level | `sessions WHERE congress_id AND entity AND status='success'` | Each session's `last_batch_id` | `congress/{cid}/results/{entity}.xlsx` |

**Important:** Congress-level XLSX uses `last_batch_id` per session (not a single batch_id), since different sessions may have succeeded in different batches.

### Step Files Loaded Per Entity

| Entity | GCS Folders | Step Files |
|--------|-------------|------------|
| `indication` | `indication/` | `extraction.json`, `validation.json` |
| `drug` | `drug/` | `extraction.json`, `validation.json` |
| `drug_class` | `drug/` + `drug_class/` | `extraction.json`, `validation.json`, `steps1_3.json`, `step4.json`, `step5.json`, `validation.json` |

Step data keys are namespaced as `{entity_folder}__{step_name}` (e.g. `drug__validation` vs `drug_class__validation`).

---

## GCS Storage Layout

**Bucket:** `fc-{env}-entity-extraction-agent`

```
rules/
  {entity_type}/
    template.csv                              ← rules template (indication)
    {filename}.csv                            ← uploaded rules files

congress/
  {congress_id}/
    search_cache/                             ← Tavily search cache (drug class)
      {drug_name}.json
    batches/
      {batch_id}/
        drug/
          {session_id}/
            extraction.json                   ← drug extraction output
            validation.json                   ← drug validation output
        drug_class/
          {session_id}/
            steps1_3.json                     ← per-drug regimen + extraction + selection
            step4.json                        ← explicit drug classes from title
            step5.json                        ← consolidation result
            validation.json                   ← per-component validation
        indication/
          {session_id}/
            extraction.json                   ← indication extraction output
            validation.json                   ← indication validation output
        results/
          drug.xlsx                            ← batch-level results
          drug_class.xlsx
          indication.xlsx
    results/
      drug.xlsx                               ← congress-level results (cumulative)
      drug_class.xlsx
      indication.xlsx
```

**Activity:** `save_step_output` in `src/temporal/activities/result_storage.py`
- Strips `_token_usage` and `_llm_calls` metadata before persisting
- Purpose: downloadable result files from admin portal (NOT for checkpointing)

---

## Worker Architecture

Each task queue has a dedicated worker process. Workers are launched via `src/temporal/workers/run_all.py`.

| Worker | Task Queue | Activities | Max Concurrent |
|--------|-----------|------------|----------------|
| Workflow | `extraction-workflows` | None (orchestration only) | 100 workflows, 50 cached |
| Drug | `drug-activities` | `extract_drugs`, `validate_drugs` | 15 |
| Drug Class | `drug-class-activities` | `step1_regimen`, `step2_fetch_search_results`, `step2_extract_with_tavily`, `step2_extract_with_grounded`, `step3_selection`, `step4_explicit`, `step5_consolidation`, `validate_drug_class_activity` | 10 |
| Indication Extraction | `indication-extraction` | `extract_indication` | 15 |
| Indication Validation | `indication-validation-slow` | `validate_indication` | 10 |
| Extraction Progress | `entity-mapping-progress` | `update_extraction_progress`, `check_and_finalize_batch` | 20 |
| Result Storage | `result-storage` | `save_step_output` | 30 |

---

## Timeout & Retry Configuration

**Source:** `src/temporal/config.py`

| Activity Type | Timeout | Max Attempts | Backoff | Non-Retryable Errors |
|---------------|---------|-------------|---------|---------------------|
| RESULT_STORAGE | 3 min | 3 | 2.0x, max 10s | ValueError, PermissionError |
| ENTITY_MAPPING_PROGRESS | 3 min | 5 | 2.0x, max 60s | ValueError, IntegrityError |
| FAST_LLM | 4 min | 3 | 2.0x, max 30s | ValueError, ValidationError |
| SLOW_LLM | 5 min | 2 | 2.0x, max 60s | ValueError, ValidationError |
| SEARCH | 4 min | 3 | 2.0x, max 15s | ValueError |
| BATCH_FINALIZATION | 60 min | 3 | 2.0x, max 2min | ValueError |
| WORKFLOW_EXECUTION | 72 hours | - | - | - |

---

## End-to-End Data Flow Example

**Scenario:** Drug entity extraction for session 719267 in congress 42, batch 7

```
INPUT:
  abstract_id: 719267
  abstract_title: "Phase 3 study of pembrolizumab vs placebo in NSCLC"
  entity: "drug"
  congress_id: 42, batch_id: 7
  firms: ["Merck"]

EXECUTION:

1. update_extraction_progress(drug, "running")
   DB: batches_sessions(7, 719267, drug) = running
   DB: sessions(42, 719267, drug) = running

2. extract_drugs() → {primary_drugs: ["pembrolizumab"], comparator_drugs: ["placebo"]}
   GCS: congress/42/batches/7/drug/719267/extraction.json

3. validate_drugs() → {validation_status: "PASS"}
   GCS: congress/42/batches/7/drug/719267/validation.json

4. update_extraction_progress(drug, "success")
   DB: batches_sessions(7, 719267, drug) = success
   DB: sessions(42, 719267, drug) = success

5. update_extraction_progress(drug_class, "running")
   DB: batches_sessions(7, 719267, drug_class) = running
   DB: sessions(42, 719267, drug_class) = running

6. Drug Class Steps 1-3 for "pembrolizumab":
   step1_regimen → ["pembrolizumab"] (not a regimen)
   step2_fetch_search_results → {drug_class_results: [...]}
   step2_extract_with_tavily → {drug_classes: ["PD-1 inhibitor"]}
   step3_selection → {selected_drug_classes: ["PD-1 inhibitor"]}
   GCS: congress/42/batches/7/drug_class/719267/steps1_3.json

7. step4_explicit → {explicit_drug_classes: ["PD-1 inhibitor"]}
   GCS: congress/42/batches/7/drug_class/719267/step4.json

8. step5_consolidation → {refined_explicit_classes: [...], removed: [...]}
   GCS: congress/42/batches/7/drug_class/719267/step5.json

9. validate_drug_class (per component) → {validation_status: "PASS"}
   GCS: congress/42/batches/7/drug_class/719267/validation.json

10. update_extraction_progress(drug_class, "success")
    DB: batches_sessions(7, 719267, drug_class) = success
    DB: sessions(42, 719267, drug_class) = success

11. check_and_finalize_batch(batch_id=7, congress_id=42)
    - All sessions done? → Yes
    - Final status: "completed"
    - DB: batches(7).status = completed, completed_at = NOW()
    - GCS: congress/42/batches/7/results/drug.xlsx
    - GCS: congress/42/batches/7/results/drug_class.xlsx
    - GCS: congress/42/results/drug.xlsx
    - GCS: congress/42/results/drug_class.xlsx
    - Teams notification sent
```

---

## AP Server API Endpoints

**Base Path:** `/congresses/{congress_id}/sessions/entity-mapping`

### Batch Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Dashboard stats (total, unmapped, per-entity breakdown) |
| GET | `/batches` | Paginated list of batch runs with per-entity progress |
| GET | `/batches/{batch_id}` | Single batch detail |
| POST | `/batches` | Create new batch (multipart: entities, rules_file_path, target_sessions_csv) |
| PUT | `/batches/{batch_id}/abort` | Abort batch (cancels all live workflows) |
| GET | `/batches/session-template` | Download blank session CSV template |

### Retry

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/batches/retry/preview` | Preview retry count by entity |
| PUT | `/batches/retry` | Signal paused workflows to resume (no new batch created) |

### Results

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/batches/{batch_id}/results/{entity}` | Signed URL for batch-level XLSX |
| GET | `/results/{entity}` | Signed URL for congress-level XLSX |

### Rules

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/rules/{entity_type}` | List uploaded rules files |
| POST | `/rules/{entity_type}` | Upload rules CSV |
| GET | `/rules/{entity_type}/template-file` | Download rules template |
| GET | `/rules/{entity_type}/{filename}` | Download specific rules file |
| DELETE | `/rules/{entity_type}/{filename}` | Delete rules file |

### Authorization

| Permission | Scope |
|------------|-------|
| `entity_mapping.read` | Dashboard, list batches, batch detail, retry preview, download results/rules |
| `entity_mapping.create` | Create batch, retry, abort, upload/delete rules |

Permissions assigned via `ap_permissions` → `ap_permissions_groups` → `ap_groups_users`. The `congress_agent_admin` group (ID 15) has both permissions.
