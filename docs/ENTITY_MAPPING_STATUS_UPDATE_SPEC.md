# Entity Mapping: Status Update & XLSX Generation Fix

## Problem

Two bugs in how `entity_mapping_sessions` is updated by `update_extraction_progress`:

### 1. Abort erases prior success

When a session succeeds in batch 1 and is re-run in batch 2 which gets aborted, the upsert blindly overwrites the status:

```
Batch 1: S1 drug → success  →  entity_mapping_sessions: success, last_batch_id=1
Batch 2: S1 drug → running  →  entity_mapping_sessions: running, last_batch_id=2  ← success lost
Batch 2: S1 drug → aborted  →  entity_mapping_sessions: aborted, last_batch_id=2
```

Dashboard now shows S1 as "unmapped" even though valid extraction data exists from batch 1. Congress-level XLSX also misses S1.

### 2. Running batch drops dashboard stats to zero

When a batch re-runs previously extracted sessions, `entity_mapping_sessions.status` is set to `running` and `last_batch_id` points to the new (running) batch. The dashboard filters out running batches (`batch.status NOT IN (pending, running)`), so ALL re-running sessions disappear from stats.

```
100 sessions extracted in batch 1 (completed)
Batch 2 re-runs all 100 → entity_mapping_sessions: running, last_batch_id=2
Dashboard: extracted=0, unmapped=100  ← WRONG (should still show 100 extracted)
```

### 3. No XLSX generated on abort

`check_and_finalize_batch` skips XLSX generation entirely for aborted batches. But an aborted batch may have sessions that completed successfully before the abort — their data should be exported.

---

## Solution: Terminal-Only Updates to `entity_mapping_sessions`

Redefine the role of the two tables:

| Table | Role | Updated on |
|---|---|---|
| `entity_mapping_batches_sessions` | **Real-time** per-batch tracking | All states (pending, running, success, failed, aborted) |
| `entity_mapping_sessions` | **Effective completed status** | Terminal states only: success, failed, conditional aborted |

### Update Rules for `entity_mapping_sessions`

| New status | Current status in entity_mapping_sessions | Action |
|---|---|---|
| `running` | any | **Skip** — don't update entity_mapping_sessions |
| `pending` | any | **Skip** — don't update entity_mapping_sessions |
| `success` | any | **Update** — always overwrite |
| `failed` | any | **Update** — always overwrite |
| `aborted` | `success` | **Skip** — preserve prior success |
| `aborted` | `failed` | **Update** to aborted (clear dead failed state — workflow was cancelled, can't retry) |
| `aborted` | `aborted` / no row | **Update** / insert as aborted |

---

## Changes Required

### 1. `update_extraction_progress`

**File:** `src/temporal/activities/extraction_progress.py`

```python
# Step 1: ALWAYS update entity_mapping_batches_sessions (real-time tracking, unchanged)
update_batches_sessions(batch_id, session_id, entity, status)

# Step 2: Conditionally update entity_mapping_sessions
if status in ("running", "pending"):
    # Skip — don't update entity_mapping_sessions for transitional states
    return

if status == "aborted":
    # Only overwrite if current status is NOT "success"
    current = get_current_entity_mapping_sessions(congress_id, session_id, entity)
    if current and current.status == "success":
        return  # preserve prior success
    # else: update to aborted (current is failed, aborted, or no row)

# For success, failed, and aborted (when current != success):
upsert_sessions(congress_id, session_id, entity, status, batch_id)
```

### 2. `check_and_finalize_batch`

**File:** `src/temporal/activities/check_and_finalize_batch.py`

Currently returns early when `batch.status != 'running'` and skips XLSX for `failed`/`aborted`. The AP server sets `batch.status = 'aborted'` before workflows' cancel handlers fire `check_and_finalize_batch`, so the activity always exits early for aborted batches.

**Changes:**

a) Add `elif batch.status == "aborted"` branch after the existing `if batch.status == "running"` check:
   - Guard: verify all batch_sessions are in terminal state (not pending/running) — prevents premature XLSX generation before all workflows finish cleanup
   - Guard: verify at least one `success` session exists in this batch — skip if nothing to export
   - If guards pass: proceed to XLSX generation
   - **Skip Teams notification** — abort is a deliberate user action, not an event needing alerts

b) Change XLSX skip condition:
   - `failed` → skip (all sessions failed, nothing to export)
   - `aborted` with successes → **generate XLSX only**, no Teams notification
   - `completed` / `partial` → generate XLSX + send Teams notification (unchanged)

**Key safety:** The AP server abort endpoint only updates `pending/running/failed` batch_sessions to `aborted` — sessions already at `success` in `entity_mapping_batches_sessions` are preserved. The batch XLSX query (`WHERE status='success' AND batch_id=X`) naturally picks up these sessions.

**Congress XLSX:** `entity_mapping_sessions` already has the correct status thanks to the terminal-only update rules. The existing query (`WHERE status='success'`) works without modification.

### No changes needed to

| Component | Why |
|---|---|
| `_generate_batch_xlsx` | Already queries `batches_sessions WHERE status='success' AND batch_id=X` |
| `_generate_congress_xlsx` | Uses `entity_mapping_sessions WHERE status='success'`; terminal-only updates ensure correctness |
| `entity_mapping_export.py` | Takes `session_batch_map` as input, unchanged |
| AP Server dashboard / unmapped_only / retry | `entity_mapping_sessions` now always reflects effective terminal status, existing queries work |

---

## Scenario Walkthrough

### Case 1: Success → Re-run → Aborted (the key fix)

```
Batch 1: S1 drug → success

  batches_sessions (batch 1, S1, drug)    sessions (S1, drug)
  ─────────────────────────────────────    ─────────────────────
  success                                 success, batch_id=1

Batch 2: S1 drug → re-run → aborted

  batches_sessions (batch 2, S1, drug)    sessions (S1, drug)
  ─────────────────────────────────────    ─────────────────────
  pending                                 (skip)
  running                                 (skip) ← stays success, batch_id=1
  aborted                                 current is "success" → SKIP ← preserved!

Dashboard: extracted=1 (batch 1 data visible) ✓
Congress CSV: S1 included (batch 1 data) ✓
Batch 2 CSV: only sessions that completed before abort
```

### Case 2: Success → Failed → Re-run → Aborted

```
Batch 1: S1 → success       →  sessions: success, batch_id=1
Batch 2: S1 → failed        →  sessions: failed, batch_id=2  (user re-extracted intentionally)

Batch 3 created → cancels batch 2's paused workflow:
  cancel handler             →  current is "failed" → UPDATE → aborted, batch_id=2

Batch 3: S1 → aborted       →  current is "aborted" → UPDATE → aborted, batch_id=3

Dashboard: unmapped ✓ (user intentionally re-extracted, needs to run again)
```

### Case 3: Failure → Re-run → Aborted

```
Batch 1: S1 → failed        →  sessions: failed, batch_id=1

Batch 2 created → cancels batch 1's paused workflow:
  cancel handler             →  current is "failed" → UPDATE → aborted, batch_id=1

Batch 2: S1 → aborted       →  current is "aborted" → stays aborted

Dashboard: unmapped ✓ (no valid data exists)
```

### Case 4: Dashboard during running batch

```
100 sessions extracted in batch 1 (completed)
Batch 2 re-runs all 100

  batches_sessions (batch 2): running     sessions: NOT updated → still success, batch_id=1

Dashboard: extracted=100 ✓ (last_batch_id=1 points to completed batch, join filter passes)
```

### Case 5: Partial abort (mixed results in same batch)

```
Batch 1: S1 → success, S2 → success, S3 still running → batch aborted

  S1: batches_sessions = success          sessions = success, batch_id=1  ✓
  S2: batches_sessions = success          sessions = success, batch_id=1  ✓
  S3: batches_sessions = aborted          sessions: no prior row → aborted  → unmapped

Batch CSV: includes S1, S2 ✓
Congress CSV: includes S1, S2 ✓
```

### Case 6: Retry within a batch

```
Batch 1: S1 drug fails → sessions: failed, batch_id=1
         S1 drug_class force-failed → sessions: failed, batch_id=1

Admin triggers retry:
  drug_class reset to pending  →  sessions: (skip) ← stays failed
  drug retries → running       →  sessions: (skip) ← stays failed
  drug succeeds                →  sessions: success, batch_id=1  ✓
  drug_class runs → running    →  sessions: (skip)
  drug_class succeeds          →  sessions: success, batch_id=1  ✓
```

### Case 7: Happy path (no change in behavior)

```
Batch 1: S1 → success       →  sessions: success, batch_id=1  ✓
```

Terminal updates (success, failed) behave identically to current implementation.

---

## Summary

| Scenario | entity_mapping_sessions | Dashboard | Congress CSV |
|---|---|---|---|
| Success | success | extracted | included |
| Failed | failed | failed | excluded |
| Success → Abort | **success (preserved)** | **extracted** | **included** |
| Failed → Abort | aborted | unmapped | excluded |
| Success → Failed → Abort | aborted | unmapped | excluded |
| Never processed | no row | unmapped | excluded |
| During re-run (batch running) | **success (unchanged)** | **extracted** | **included** |
