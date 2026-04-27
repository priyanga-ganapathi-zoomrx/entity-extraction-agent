# Per-Entity XLSX Emission

Emit batch-level and congress-level XLSX files as soon as all sessions for a given entity finish, instead of waiting for the entire batch to complete.

**Files changed:** `src/temporal/activities/check_and_finalize_batch.py`, `src/temporal/workflows/abstract_extraction.py`

---

## Table of Contents

1. [Problem](#problem)
2. [Solution](#solution)
3. [Activity Changes](#activity-changes)
4. [Workflow Changes](#workflow-changes)
5. [DB Query Comparison](#db-query-comparison)
6. [Concurrency Safety](#concurrency-safety)
7. [Scenario Walkthroughs](#scenario-walkthroughs)

---

## Problem

A batch has multiple entities (e.g. `["drug", "drug_class", "indication"]`) and thousands of sessions. Each (session, entity) pair runs as a Temporal workflow. Today, `check_and_finalize_batch` generates XLSX files only when **all** sessions across **all** entities reach a terminal state.

In a typical batch with 10,000 sessions, indication workflows finish hours before drug_class (which involves multi-step search + extraction). The indication XLSX sits ungenerated until the last drug_class workflow completes.

---

## Solution

Extend `CheckAndFinalizeInput` with a `completed_entities` field. After each entity reaches a terminal state (success, failed, aborted), the workflow passes that entity to the activity. The activity checks if all sessions for that specific entity are terminal, and generates XLSX immediately if so.

Batch-level finalization (status update + Teams notification) still only runs when all entities across all sessions are done. The XLSX loop is removed from the batch finalization section — it is now handled entirely by the per-entity step.

**No new activities, no worker registration changes, no config changes, no DB schema changes.**

---

## Activity Changes

**File:** `src/temporal/activities/check_and_finalize_batch.py`

### Input dataclass

```python
@dataclass
class CheckAndFinalizeInput:
    batch_id: int
    congress_id: int
    completed_entities: list[str] = field(default_factory=list)
```

`completed_entities` contains the entity or entities that reached a terminal state. All call sites must pass at least one entity — empty list triggers an `error`-level log and **returns immediately** (no per-entity step, no batch finalization). This is not a supported call pattern.

### New per-entity XLSX step

Inserted at the top of `check_and_finalize_batch`, before the existing batch-level logic. **No gate on `batch_status`** — the per-entity step runs whenever `completed_entities` is non-empty, regardless of whether the batch is running, completed, partial, failed, or aborted. This avoids a race condition where the batch is finalized by a concurrent caller between the per-entity step and the batch-level step (see [Concurrency Safety](#concurrency-safety) for the full trace).

```
if completed_entities is empty:
    activity.logger.error("called with empty completed_entities")
    return   ← hard early return, no batch finalization either

read batch metadata (entities list, batch_status)

for each entity in completed_entities:
    Q: SELECT COUNT(*) FROM entity_mapping_batches_sessions
       WHERE batch_id = :bid AND entity = :e AND status IN ('pending', 'running')

    if count > 0:
        skip — other sessions still running for this entity

    log "all sessions terminal (batch_status=...)" ← logs current batch_status
    generate batch-level XLSX for this entity  (reuses _generate_batch_xlsx)
    generate congress-level XLSX for this entity (reuses _generate_congress_xlsx)
```

Safety is provided by the per-entity count check (`status IN ('pending', 'running')`) and `_generate_batch_xlsx` returning early on zero successful sessions — not by batch status. The batch metadata query is shared with the existing step 0 (read once, used by both). The log includes `batch_status` so that post-finalization regenerations (expected due to the `run()` fallback) are easily identified in logs.

### Updated activity logic summary

The top-of-function docstring should reflect the new ordering:

```
Logic:
1. Per-entity XLSX: for each entity in completed_entities, check if all sessions
   are terminal and generate XLSX if so (no batch_status gate)
2. Query batch — return early if status is NOT 'running'
3. Query batch_sessions — return early if any are 'pending' or 'running'
4. Determine final batch status (completed/partial/failed/aborted)
5. Update batch status + completed_at in DB
6. Send Teams notification
```

### Removed from batch finalization section

The XLSX generation loop in the running flow (step 6) is removed:

```python
# REMOVED — now handled by per-entity step above
# for entity in entities:
#     _generate_batch_xlsx(storage, congress_id, batch_id, entity)
#     _generate_congress_xlsx(storage, congress_id, entity)
```

Batch finalization now only does: determine final status → atomic DB update → Teams notification.

### Changes to `_handle_aborted_batch`

**Removed.** Since the per-entity step now runs unconditionally (no `batch_status` gate), aborted batches get their XLSX from the per-entity step at the top of the activity — the same code path as running batches. The `_handle_aborted_batch` function is no longer needed.

The existing aborted-batch branch in the batch-level logic becomes a simple early return:

```python
if batch_status == "aborted":
    activity.logger.info(f"Batch {batch_id} is aborted, per-entity XLSX already handled above")
    return
```

This is strictly simpler than the current `_handle_aborted_batch` (which had its own incomplete-count and success-count guards). The per-entity step's count check + `_generate_batch_xlsx`'s zero-sessions early return provide equivalent safety.

---

## Workflow Changes

**File:** `src/temporal/workflows/abstract_extraction.py`

The workflow currently calls `check_and_finalize_batch` **once at the end** of `run()`. Two changes:

1. **Add an intermediate blocking call** inside `_execute_pipeline` after the drug entity succeeds (wrapped in try/except so XLSX failure doesn't mark the entity as failed)
2. **Keep the final blocking call** in `run()`, now passing all entities the workflow handles

All calls use `workflow.execute_activity` (blocking). The intermediate call is wrapped in try/except because it runs inside `_execute_pipeline`'s try block — without the wrapper, an XLSX generation failure would be caught by the pipeline's exception handler and incorrectly mark a successfully-extracted entity as "failed". The except clause re-raises `CancelledError` (via `is_cancelled_exception`) so that workflow cancellation during the intermediate call propagates promptly instead of being swallowed. The final call in `run()` is outside `_execute_pipeline` and does not need a wrapper (failure propagates cleanly, same as today).

### Success path — `entity="drug"` workflow

```
_execute_pipeline:
    try:
        drug pipeline succeeds
          → update_progress("drug", "success")
          → try:                                                    ← NEW
                check_and_finalize_batch(completed_entities=["drug"])
            except CancelledError: raise   ← preserve cancellation
            except: log warning, continue to drug_class

        drug_class pipeline succeeds
          → update_progress("drug_class", "success")
          → return output
    except: ...

run():
    output = await _execute_pipeline(input)
    check_and_finalize_batch(completed_entities=["drug", "drug_class"])  ← UPDATED (was no entities)
    return output
```

The `run()` call passes **all entities** the workflow handles. This serves three purposes:
- Generates drug_class XLSX (primary)
- Regenerates drug XLSX if the intermediate call failed (fallback, idempotent)
- Triggers batch finalization if all sessions are done

**Retry note:** The intermediate call is inside the `if "drug_pipeline" not in self._completed_steps` block, so it does not re-fire on a retry where drug already succeeded. This is correct — the `run()` fallback with all entities covers it. Do not move the intermediate call outside the skip-check block.

### Success path — `entity="indication"` workflow

```
_execute_pipeline:
    try:
        indication pipeline succeeds
          → update_progress("indication", "success")
          → return output
    except: ...

run():
    output = await _execute_pipeline(input)
    check_and_finalize_batch(completed_entities=["indication"])  ← UPDATED (was no entities)
    return output
```

No intermediate call needed — indication is the only entity, so the `run()` call handles everything.

### Failure path (exception handler in `_execute_pipeline`)

```
entity fails
  → update_progress(current_entity, "failed")
  → if drug failed and drug_class not yet completed:
      update_progress("drug_class", "failed")
  → check_and_finalize_batch(completed_entities=all_terminal_entities)
  → pause for retry/abort signal
```

Where `all_terminal_entities` includes **all entities that are now terminal**, not just the newly-failed ones:

```python
terminal = [current_entity]
if (input.entity == "drug"
    and self._current_entity == "drug"
    and "drug_class_pipeline" not in self._completed_steps):
    terminal.append("drug_class")
# Unconditional fallback — includes previously-succeeded drug entity
# even in unexpected states. Not gated on self._current_entity so it
# fires whenever drug_pipeline completed, regardless of which entity
# is currently active.
if (input.entity == "drug"
    and "drug_pipeline" in self._completed_steps
    and "drug" not in terminal):
    terminal.append("drug")
```

This handles two cases:
- **Cascading failure**: drug fails → drug_class force-failed → both need XLSX checked
- **Intermediate call failure fallback**: drug succeeded but its XLSX call failed → drug_class fails later → the failure handler re-checks drug XLSX (idempotent)

### Abort path (after abort signal)

```
abort signal received
  → update_progress(current_entity, "aborted")           [fire-and-forget]
  → if drug and drug_class not completed:
      update_progress("drug_class", "aborted")            [fire-and-forget]
  → check_and_finalize_batch(completed_entities=[...])    [fire-and-forget]
```

`completed_entities` follows the same unconditional fallback pattern as the failure path — includes all terminal entities (both newly-aborted and previously-succeeded).

### Cancellation path (`_shielded_abort_progress`)

```
CancelledError caught in run()
  → shielded update_progress(entity, "aborted")
  → if drug and drug_class not completed:
      shielded update_progress("drug_class", "aborted")
  → unconditional: if drug_pipeline completed and "drug" not already in list → add "drug"
  → shielded check_and_finalize_batch(completed_entities=[...])
```

Same `completed_entities` construction as above, all shielded from cancellation. Uses blocking `execute_activity` (not fire-and-forget `start_activity`) because `update_extraction_progress` must complete before the per-entity count query in `check_and_finalize_batch` runs — otherwise the count may see non-terminal rows. The signaled-abort path uses fire-and-forget for both because it returns immediately and ordering is left to Temporal's task queue.

---

## DB Query Comparison

### Current: per call to `check_and_finalize_batch`

| Scenario | Queries |
|----------|---------|
| Batch not found / not running | 1 |
| Running, sessions still in progress (common early return) | 2 |
| Batch finalizes (3 entities) | 8 (metadata + finalization) + 12 (XLSX: 4 per entity × 3) = **20** |
| Aborted batch (3 entities) | 3 (metadata + guards) + 12 (XLSX: 4 per entity × 3) = **15** |

### New: per call to `check_and_finalize_batch`

Batch metadata is read once (shared between per-entity step and batch-level logic). Per-entity XLSX = 4 queries per entity (session IDs + session info for batch XLSX, session IDs + session info for congress XLSX). Batch finalization = 7 queries (incomplete count + status GROUP BY + atomic UPDATE + congress name + user info + entity status counts + total sessions).

**Intermediate call** (drug entity, inside `_execute_pipeline`):

| Scenario | Queries |
|----------|---------|
| Entity not yet fully done | 1 (batch metadata) + 1 (per-entity count) + 1 (batch incomplete count, returns early) = **3** |
| Entity just finished, batch not done | 1 (batch metadata) + 1 (per-entity count) + 4 (XLSX) + 1 (batch incomplete count, returns early) = **7** |

**Final call** (in `run()`, passes all entities — e.g. `["drug", "drug_class"]`):

| Scenario | Queries |
|----------|---------|
| Batch not found | 1 (batch metadata) |
| Both entities done, batch finalizes | 1 (batch metadata) + 2 (per-entity counts) + 8 (drug XLSX regen + drug_class XLSX) + 7 (batch finalization) = **18** |
| Aborted batch, both entities done | 1 (batch metadata) + 2 (per-entity counts) + 8 (drug XLSX regen + drug_class XLSX) = **11** |
| Concurrent caller already finalized (batch_status read as non-running) | 1 (batch metadata) + 2 (per-entity counts) + 8 (XLSX regen) = **11** |
| Concurrent caller finalizes after our read (batch_status read as running, atomic UPDATE loses race) | 1 (batch metadata) + 2 (per-entity counts) + 8 (XLSX regen) + 3 (incomplete count + GROUP BY + UPDATE → 0 rows) = **14** |

Compared to **current** batch finalization (3 entities): 8 (metadata + finalization) + 12 (XLSX: 4 × 3) = **20** queries in a single call.

The new design does more total queries across all calls (intermediate + final), but the work is distributed: entity XLSXs are produced earlier, and the final call regenerates already-generated entities (idempotent overwrite). `_generate_batch_xlsx` does not check GCS existence — it unconditionally reads session data and re-uploads.

### Total XLSX generation work across the batch

Each entity's XLSX is generated once when the last workflow for that entity triggers the per-entity step, plus at most once more from the `run()` fallback call. The fallback regeneration produces identical output (same terminal DB rows → same XLSX bytes → idempotent GCS overwrite). This is at most one extra regeneration per intermediate entity — acceptable given the fallback benefit.

---

## Concurrency Safety

### Per-entity XLSX

XLSX for a given entity may be generated more than once:

1. **Concurrent last workflows**: Two workflows finishing at the same time, both find all sessions terminal, both generate XLSX.
2. **Intermediate + final call overlap**: The intermediate call inside `_execute_pipeline` generates drug XLSX, then the `run()` call regenerates it as part of its all-entities pass.

Both cases are safe: GCS upload overwrites the same blob, and the XLSX content is identical (both read the same terminal DB rows). The duplication is at most once per entity (from the `run()` fallback) and is harmless.

### Why no `batch_status` gate on the per-entity step

An earlier design gated the per-entity step on `batch_status IN ("running", "aborted")`. This introduces a race:

```
1. Workflow A (last indication) reads batch_status="running"
   → generates indication XLSX
   → wins atomic UPDATE → batch_status="completed"

2. Workflow B (last drug/drug_class) reads batch_status="completed"
   → gate: not in (running, aborted) → skip per-entity step
   → batch-level: status != "running" → rows_updated=0 → return
   → drug and drug_class XLSX silently lost
```

The window is real: A's full path (metadata read → per-entity XLSX → batch queries → atomic UPDATE) can take many seconds for large batches. Removing the gate and relying on per-entity count checks + idempotent GCS overwrite eliminates this race entirely.

### Batch finalization

Unchanged. The existing atomic `UPDATE entity_mapping_batches SET status = :final WHERE id = :bid AND status = 'running'` ensures exactly one caller wins. The second concurrent caller gets `rows_updated = 0` and returns.

---

## Scenario Walkthroughs

### 1. Normal batch — indication finishes before drug_class

```
Batch: 10,000 sessions, entities = ["drug", "drug_class", "indication"]

T+2h  Last indication workflow:
      → _execute_pipeline returns
      → run() calls check_and_finalize_batch(completed_entities=["indication"])
      → Per-entity: all 10k indication sessions terminal → generate indication XLSX ✓
      → Batch: drug/drug_class still running → return early

T+4h  Last drug workflow — drug pipeline succeeds:
      → intermediate call: check_and_finalize_batch(completed_entities=["drug"])
      → Per-entity: all 10k drug sessions terminal → generate drug XLSX ✓
      → Batch: drug_class still running → return early
      → drug_class pipeline starts immediately

T+6h  Last drug workflow — drug_class pipeline succeeds:
      → _execute_pipeline returns
      → run() calls check_and_finalize_batch(completed_entities=["drug", "drug_class"])
      → Per-entity "drug": all terminal → regenerate drug XLSX (idempotent) ✓
      → Per-entity "drug_class": all 10k terminal → generate drug_class XLSX ✓
      → Batch: all 30k rows terminal → finalize batch + Teams notification
```

Before: all three XLSXs appear at T+6h. After: indication at T+2h, drug at T+4h, drug_class at T+6h.

### 2. Drug failure with cascading drug_class force-fail

```
Session 10,000 (last one): drug pipeline fails
  → update_progress("drug", "failed")
  → update_progress("drug_class", "failed")     ← force-fail
  → check_and_finalize_batch(completed_entities=["drug", "drug_class"])
  → Per-entity "drug": all 10k terminal → generate drug XLSX (9,999 successful) ✓
  → Per-entity "drug_class": all 10k terminal → generate drug_class XLSX ✓
  → Batch: all done → finalize + notify
```

Without `completed_entities=["drug", "drug_class"]`, the drug_class XLSX would never be generated — nobody else would call `check_and_finalize_batch` for drug_class.

### 3. Partial success — some sessions fail

```
Session 5,000 (last indication workflow): fails
  → check_and_finalize_batch(completed_entities=["indication"])
  → Per-entity: all 10k indication sessions terminal
  → _generate_batch_xlsx queries WHERE status='success' → 9,999 rows
  → XLSX generated with 9,999 successful sessions ✓
```

### 4. All sessions fail for an entity

```
All 10k indication workflows fail
  → Last one calls check_and_finalize_batch(completed_entities=["indication"])
  → Per-entity: all terminal, but _generate_batch_xlsx finds 0 successful sessions
  → Logs "No successful sessions" and skips XLSX ✓
```

### 5. Aborted batch with some completed sessions

```
5,000 indication sessions succeed, then AP server aborts batch
AP server sets batch.status = "aborted", bulk-updates remaining sessions to "aborted"

Next workflow cancellation handler calls:
  → check_and_finalize_batch(completed_entities=["indication"])
  → Per-entity (no batch_status gate): all 10k indication sessions terminal
  → _generate_batch_xlsx queries WHERE status='success' → 5,000 rows
  → XLSX generated with 5,000 successful sessions ✓
  → Batch-level: batch_status == "aborted" → return early (no DB update, no Teams)
```

### 6. Retry after failure, then success

```
Session 100: drug fails → failure handler calls with completed_entities=["drug", "drug_class"]
  → drug XLSX generated (99 successful), drug_class XLSX generated

Admin retries session 100 → drug succeeds → drug_class succeeds
  → _execute_pipeline returns
  → run() calls check_and_finalize_batch(completed_entities=["drug", "drug_class"])
  → Per-entity: all terminal → regenerate both XLSXs (now 100 successful) ✓
  → GCS upload overwrites previous XLSXs with updated data
```

### 7. Drug succeeds, intermediate XLSX call fails, drug_class fails later

```
Session 100 (last workflow):
  → drug pipeline succeeds
  → intermediate check_and_finalize_batch(completed_entities=["drug"]) → FAILS (GCS down)
  → try/except catches, logs warning, continues
  → drug_class pipeline fails
  → failure handler: all_terminal_entities = ["drug_class", "drug"]  ← includes drug as fallback
  → check_and_finalize_batch(completed_entities=["drug_class", "drug"])
  → Per-entity "drug_class": all terminal → generate drug_class XLSX ✓
  → Per-entity "drug": all terminal → generate drug XLSX ✓ (recovered)
```

### 8. `completed_entities` is empty (defensive only)

```
check_and_finalize_batch(completed_entities=[])
  → activity.logger.error("called with empty completed_entities")
  → return immediately — no per-entity step, no batch finalization
```

This should never occur in normal operation — all call sites pass at least one entity. An `error`-level log is emitted so the condition is observable in monitoring. The activity returns immediately to fail loudly rather than proceeding to batch finalization that would push a Teams notification with no XLSX generated.
