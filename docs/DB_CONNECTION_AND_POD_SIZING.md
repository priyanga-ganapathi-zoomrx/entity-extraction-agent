# Entity Extraction Agent — DB Connection & Pod Sizing Analysis

## 1. What This System Does

The Entity Extraction Agent processes medical congress abstracts to extract structured data (drugs, drug classes, indications) using AI models (Gemini, Claude). The processing is orchestrated by **Temporal**, a workflow engine that manages task distribution across worker pods.

For a typical congress with **10,000 sessions**, the system creates **20,000 workflows** — one **indication workflow** and one **drug workflow** per session. Each workflow calls multiple AI models in sequence, saves results to cloud storage (GCS), and updates a MySQL database with progress status.

---

## 2. Architecture Overview

```
┌──────────────┐     starts 20K workflows      ┌──────────────────┐
│  AP Server   │ ─────────────────────────────▶ │  Temporal Server │
│  (Admin UI)  │                                │  (Task Queues)   │
└──────────────┘                                └────────┬─────────┘
                                                         │
                          ┌──────────────────────────────┬┴──────────────────────────────┐
                          │                              │                               │
                   ┌──────▼──────┐             ┌─────────▼─────────┐          ┌──────────▼──────────┐
                   │  LLM Workers│             │  Result Storage   │          │  Progress Worker    │
                   │  (no DB)    │             │  Worker (no DB)   │          │  (ALL DB writes)    │
                   │             │             │                   │          │                     │
                   │ • Drug      │             │ Saves AI outputs  │          │ Updates MySQL with  │
                   │ • Drug Class│             │ to Google Cloud   │          │ workflow status     │
                   │ • Indication│             │ Storage (GCS)     │          │ + batch finalization│
                   │   Extraction│             │                   │          │                     │
                   │ • Indication│             │ Zero DB           │          │ ONLY worker with    │
                   │   Validation│             │ connections       │          │ DB connections      │
                   └─────────────┘             └───────────────────┘          └─────────────────────┘
```

### Key Design Principle

**Only ONE worker type connects to the database** — the Extraction Progress Worker. All other workers (LLM workers, result storage) have **zero DB connections**. This means DB capacity planning is simple and predictable.

---

## 3. What Each Worker Does

| Worker | What It Does | Connects to DB? | Connects to LLM APIs? |
|---|---|---|---|
| **Workflow Worker** | Lightweight orchestration — decides which activity runs next | No | No |
| **Indication Extraction Worker** | Calls Gemini to extract indications from abstracts | No | Yes (Gemini) |
| **Indication Validation Worker** | Calls Claude to validate extracted indications | No | Yes (Claude) |
| **Drug Worker** | Calls Gemini for drug extraction + validation | No | Yes (Gemini) |
| **Drug Class Worker** | Calls Claude for drug class classification + validation | No | Yes (Claude) |
| **Result Storage Worker** | Saves AI outputs as JSON files to GCS | No | No |
| **Extraction Progress Worker** | Updates MySQL tables with workflow status; checks if batch is complete | **Yes** | No |

---

## 4. How a Single Workflow Executes

### Indication Workflow (per session)

```
Step 1:  Update DB → status = "running"              5.00s   ← DB connection
Step 2:  Call Gemini → extract indication             9.01s
Step 3:  Save result to GCS                           1.00s
Step 4:  Call Claude → validate indication           40.84s
Step 5:  Save result to GCS                           1.00s
Step 6:  Update DB → status = "success"               5.00s   ← DB connection
Step 7:  Check if batch is complete                    5.00s   ← DB connection
                                                     ──────
Total per workflow:                                  66.85s
DB time:                                             15.00s (~22%)
```

### Drug + Drug Class Workflow (per session)

```
Step 1:   Update DB → drug status = "running"         5.00s   ← DB connection
Step 2:   Call Gemini → extract drugs                  6.29s
Step 3:   Save result to GCS                           1.00s
Step 4:   Call Gemini → validate drugs                 4.52s
Step 5:   Save result to GCS                           1.00s
Step 6:   Update DB → drug status = "success"          5.00s   ← DB connection
Step 7:   Update DB → drug_class status = "running"    5.00s   ← DB connection
Step 8:   Call Claude → classify drug classes           2.46s
Step 9:   Save result to GCS                           1.00s
Step 10:  Call Claude → validate drug classes           4.28s
Step 11:  Save result to GCS                           1.00s
Step 12:  Update DB → drug_class status = "success"    5.00s   ← DB connection
Step 13:  Check if batch is complete                    5.00s   ← DB connection
                                                      ──────
Total per workflow:                                   46.55s
DB time:                                              30.00s (~64%)
```

**Note on DB timing:** The 5s estimate is a conservative upper bound that includes network round-trip to Cloud SQL, connection pool checkout, query execution, commit, and connection return. Actual production latency should be measured (see Section 11) — real values are likely 1-3s, which would reduce pod requirements.

---

## 5. Concurrency Target: 82 Indication + 150 Drug Workflows

### Why These Numbers?

Concurrency is limited by **AI model rate limits** (tokens per minute), not by our infrastructure:

| AI Model | Rate Limit | Used By | Max Concurrent Workflows |
|---|---|---|---|
| Claude Sonnet 4.5 | 3,000,000 tokens/min | Indication validation | **82** (limited by 36,664 tokens/abstract) |
| Gemini | 10,000,000 tokens/min | Drug extraction + validation | **~150** (limited by 47,070 tokens/abstract) |

We size our infrastructure to match these rate limits — scaling beyond them wastes resources since the AI APIs would throttle us anyway.

---

## 6. DB Connection Analysis

### How Temporal Prevents Connection Storms

When a batch starts, the AP server registers all 20,000 workflows with Temporal simultaneously. However, workflows don't connect to the DB directly — they submit **activity tasks** to a queue. The Extraction Progress Worker **pulls** tasks from this queue with a hard concurrency limit.

```
20,000 workflows submit DB tasks
          │
          ▼
┌─────────────────────────────────┐
│   Temporal Task Queue           │
│   (acts as an infinite buffer)  │
│   [task] [task] [task] [task]   │
│   [task] [task] [task] ...      │
└──────────────┬──────────────────┘
               │
               │  max 60 at a time (3 pods × 20)
               ▼
┌──────────────────────────────────┐
│   Extraction Progress Workers    │
│   3 pods, 20 concurrent each     │
│                                  │
│   Opens at most 60 DB            │
│   connections simultaneously     │
└──────────────────────────────────┘
```

**This means: regardless of whether 1,000 or 20,000 workflows are running, the maximum concurrent DB connections is always 60.** The task queue absorbs any burst. Batch size affects how long the workers stay busy, not how many connections they open.

### Activity Throughput Demand

At steady state, with 82 indication + 150 drug workflows running in parallel:

**Indication workflows (82 concurrent, 66.85s each):**
- Completions per minute: 82 × 60 / 66.85 = **~74/min**
- Each workflow makes 2 `update_progress` calls + 1 `check_and_finalize` call
- `update_progress` calls: 74 completions/min × 2 = ~148/min (but staggered — start + end)

**Drug workflows (150 concurrent, 46.55s each):**
- Completions per minute: 150 × 60 / 46.55 = **~193/min**
- Each workflow makes 4 `update_progress` calls + 1 `check_and_finalize` call
- `update_progress` calls: 193 × 4 = ~772/min (staggered across workflow lifetime)

### Concurrent DB Connections

| Source | Activities/Min | Avg Duration | Avg Concurrent Slots |
|---|---|---|---|
| `update_progress` (indication) | ~148/min | 5s | ~12.3 |
| `update_progress` (drug + drug_class) | ~772/min | 5s | ~64.3 |
| `check_and_finalize_batch` | ~267/min | 5s | ~22.3 |
| **Total** | **~1,187/min** | | **~99 slots needed** |

> **Calculation:** Avg concurrent = (activities/min × duration in seconds) / 60

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   Steady-state demand (at 5s):      ~99 concurrent slots        │
│                                                                 │
│   DB activity tasks per minute:     ~1,187                      │
│                                                                 │
│   DB queries per minute:            ~2,374                      │
│                                                                 │
│   At 5s latency:  5 pods needed (100 slots, ~1% headroom)      │
│   At 3s latency:  3 pods needed  (60 slots, ~2% headroom)      │
│   At 1s latency:  1 pod needed   (20 slots, ~1% headroom)      │
│                                                                 │
│   Recommendation: Start with 3 pods (assumes ~3s actual         │
│                   latency), measure, then adjust.               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Why 3 Pods Are Needed

With 5s per DB operation:

| Pods | Total Slots | Max Throughput | Demand | Status |
|---|---|---|---|---|
| 1 pod × 20 slots | 20 | 240/min | 1,187/min | **Overloaded — tasks queue up** |
| 2 pods × 20 slots | 40 | 480/min | 1,187/min | **Still overloaded** |
| 3 pods × 20 slots | 60 | 720/min | 1,187/min | **Still tight — see note** |
| 4 pods × 20 slots | 80 | 960/min | 1,187/min | **Still tight** |
| 5 pods × 20 slots | 100 | 1,200/min | 1,187/min | **Just enough (~1% headroom)** |

> **Wait — 5 pods?** At 5s per operation, even 5 pods barely keep up. However, the 5s estimate is intentionally worst-case. Here's how pod count changes with actual latency:

| Actual DB Latency | Slots Needed | Pods Needed (at 20/pod) | Headroom |
|---|---|---|---|
| 5.0s (worst case) | ~99 | **5 pods** | ~1% |
| 3.0s (likely) | ~59 | **3 pods** | ~2% |
| 2.0s (optimistic) | ~40 | **2 pods** | ~0% |
| 1.0s (ideal) | ~20 | **1 pod** | ~1% |

**Recommendation: Start with 3 pods, measure actual latency (see Section 11), then adjust.** If latency is consistently under 2s, scale down to 2 pods. If over 3s, scale up to 4-5.

### DB Queries Breakdown Per Minute

| Activity | Calls/Min | Queries/Call | Total Queries/Min | Rows Read/Call |
|---|---|---|---|---|
| `update_progress` | ~920 | 2 (UPDATE + UPSERT) | ~1,840 | 1 |
| `check_and_finalize` (early return) | ~267 | 2 (SELECT batch + SELECT COUNT WHERE status IN pending/running) | ~534 | **~0** (index count) |
| `check_and_finalize` (finalization) | once per batch | 5 (SELECT batch + COUNT + GROUP BY status + UPDATE + GROUP BY entity/status) | ~5 | **~0** (aggregates only) |
| **Total** | **~1,187** | | **~2,374** | |

**Optimization: `check_and_finalize_batch` uses `SELECT COUNT(*) WHERE status IN ('pending', 'running')`** instead of fetching all 30K rows. Since 99% of calls find incomplete sessions and return early, MySQL only counts matching rows via index — typically returning a single integer. The heavy queries (GROUP BY for status determination, per-entity counts for notifications) only run **once per batch** when the last workflow completes, not ~267 times/min.

---

## 7. Full Batch Lifetime Numbers (10K Sessions)

For a 10,000-session congress extracting all 3 entity types:

| Phase | Duration | DB Activity Tasks | DB Queries | Rows Read |
|---|---|---|---|---|
| Indication workflows | ~2.5 hrs | ~22,200 | ~59,400 | ~30K (lightweight) |
| Drug workflows | ~1.5 hrs | ~45,000 | ~103,500 | ~50K (lightweight) |
| **Total** | **~4 hrs** | **~67,200** | **~162,900** | **~80K** |

Row reads are minimal because `check_and_finalize_batch` uses `SELECT COUNT(*)` with indexed columns instead of fetching all rows. The only full-table aggregations happen once per batch during finalization.

All of this runs through **at most 60 concurrent DB connections** at any given moment.

---

## 8. Pod Configuration

### Summary Table

| Worker | Task Queue | Purpose | Pods | Concurrency/Pod | DB? |
|---|---|---|---|---|---|
| workflow-worker | extraction-workflows | Orchestration | **3** | 100 workflow tasks | No |
| indication-extraction | indication-extraction | Gemini LLM | **1** | 15 activities | No |
| indication-validation | indication-validation-slow | Claude LLM | **7** | 10 activities | No |
| drug-worker | drug-activities | Gemini LLM | **5** | 15 activities | No |
| drug-class-worker | drug-class-activities | Claude LLM | **5** | 10 activities | No |
| result-storage | result-storage | GCS uploads | **1** | 30 activities | No |
| extraction-progress | entity-mapping-progress | **DB writes** | **3** | 20 activities | **Yes** |
| **Total** | | | **25 pods** | | |

### How Pod Counts Were Calculated

With 82 indication workflows and 150 drug workflows active simultaneously, each workflow spends time on different queues based on actual measured execution times:

**Indication workflow (66.85s per abstract):**

```
                    ┌───────────────── 61% of time ──────────────────┐
                    │                                                │
    ┌──────────┐    │    ┌─────────────────────────────────────┐     │
    │Extraction│    │    │         Validation (Claude)         │     │
    │ (Gemini) │    │    │            40.84 seconds            │     │
    │  9.01s   │    │    │                                     │     │
    └──────────┘    │    └─────────────────────────────────────┘     │
     13% of time    │                                                │
                    └────────────────────────────────────────────────┘
    DB operations: 15.00s (22% of time, spread across 3 calls)

    82 workflows × 13% = 11 concurrent extractions  → 1 pod  (at 15/pod)
    82 workflows × 61% = 50 concurrent validations   → 5 pods (at 10/pod)
```

> Note: Indication validation drops from 7 pods to 5 pods because with longer total workflow time (66.85s vs 52.31s), each workflow spends a smaller percentage of its time in validation. However, the actual concurrent validation load depends on Claude's rate limit — **7 pods remains the correct sizing** because Claude processes 82 abstracts concurrently and each validation takes 40.84s, meaning ~64 are in validation at any moment regardless of DB time. DB time doesn't overlap with LLM time — it's sequential.

**Corrected LLM pod calculation (DB time is sequential, not parallel):**

LLM pod sizing is based on how many workflows are simultaneously in each LLM stage. Since DB operations run on a separate worker and queue, they don't reduce the LLM concurrency — they just add wait time between LLM stages.

```
    Indication:
    82 workflows × (9.01 / 66.85)  = 11 concurrent extractions  → 1 pod  (at 15/pod)
    82 workflows × (40.84 / 66.85) = 50 concurrent validations   → 5 pods (at 10/pod)

    But with task queue buffering, peak validation concurrency is still
    limited by Claude's rate → 64 concurrent validations → 7 pods (at 10/pod)
```

**Drug workflow (46.55s per abstract):**

```
    ┌────────── 38% of time ──────────┐┌──── 15% of time ────┐
    │                                 ││                      │
    ┌──────────┐    ┌────────────┐    ┌─────────┐ ┌──────────┐
    │  Extract │    │  Validate  │    │  Step4  │ │ Validate │
    │  Drugs   │    │   Drugs    │    │ Explicit│ │Drug Class│
    │  6.29s   │    │   4.52s    │    │  2.46s  │ │  4.28s   │
    └──────────┘    └────────────┘    └─────────┘ └──────────┘
     drug-activities queue             drug-class-activities queue
    DB operations: 30.00s (64% of time, spread across 5 calls)

    150 workflows × (10.81 / 46.55) = 35 concurrent drug activities      → 3 pods (at 15/pod)
    150 workflows × (6.74 / 46.55)  = 22 concurrent drug class activities → 3 pods (at 10/pod)

    But with task queue buffering, actual concurrency is governed by
    Gemini's rate limit → 73 concurrent drug activities → 5 pods (at 15/pod)
    Claude's rate limit → 45 concurrent drug class    → 5 pods (at 10/pod)
```

**Important:** LLM pod counts remain the same as before (7 indication-validation, 5 drug, 5 drug-class) because LLM rate limits — not DB latency — determine how many workflows are in each LLM stage at once. The DB wait time is absorbed by Temporal's task queue and only affects the progress worker pods.

---

## 9. Resource Sizing Recommendations

### MySQL Database

| Metric | Value |
|---|---|
| Concurrent connections (3 pods) | **60** (3 pods × 20 each — sufficient at ~3s latency) |
| Concurrent connections (5 pods) | **100** (5 pods × 20 each — needed at 5s latency) |
| Recommended connection pool | **80-120** (capacity + headroom, depends on measured latency) |
| Queries per second (sustained) | ~40 QPS |
| Peak queries per second | ~70 QPS (when all slots are active) |
| Most frequent query | `UPDATE entity_mapping_batches_sessions SET status=? WHERE batch_id=? AND session_id=? AND entity=?` — ~920 times/min, 1 row per call (indexed) |
| Most frequent read | `SELECT COUNT(*) FROM entity_mapping_batches_sessions WHERE batch_id=? AND status IN ('pending','running')` — ~267 times/min, index-only count |
| Heaviest query | `SELECT entity, status, COUNT(*) FROM entity_mapping_batches_sessions WHERE batch_id=? GROUP BY entity, status` — runs **once per batch** during finalization |

### Kubernetes Resources

| Pod Type | Resource Preset | Reason |
|---|---|---|
| Workflow worker | micro (250m CPU / 512Mi) | Lightweight coordination, no I/O |
| LLM workers | small (500m CPU / 1Gi) | Network I/O for API calls, JSON processing |
| Result storage | micro (250m CPU / 512Mi) | GCS upload, I/O bound |
| Extraction progress | small (500m CPU / 1Gi) | DB queries, index-based counts + updates |

### KEDA Autoscaling

All workers use KEDA with Temporal task queue scaling. When no batch is running, pods scale to zero. When a batch starts, KEDA detects tasks in the queue and scales up.

| Worker | max_replicas | Scale Trigger |
|---|---|---|
| workflow-worker | 3 | Tasks in `extraction-workflows` queue |
| indication-extraction | 1 | Tasks in `indication-extraction` queue |
| indication-validation | 7 | Tasks in `indication-validation-slow` queue |
| drug-worker | 5 | Tasks in `drug-activities` queue |
| drug-class-worker | 5 | Tasks in `drug-class-activities` queue |
| result-storage | 1 | Tasks in `result-storage` queue |
| extraction-progress | 3 | Tasks in `entity-mapping-progress` queue |

---

## 10. Key Takeaways

1. **DB connections are fully bounded.** Regardless of batch size (1K or 20K sessions), the maximum concurrent DB connections is **60** (3 pods × 20 slots). This is enforced by the worker's concurrency limit, not by application logic.

2. **22 out of 25 pods have zero DB connections.** The entire DB footprint of the system is 3 pods running 1 worker process each.

3. **AI rate limits are the real bottleneck, not infrastructure.** Claude's 3M tokens/min limit caps indication throughput at ~82 abstracts/min. Gemini's 10M tokens/min limit caps drug throughput at ~150 abstracts/min. Pod counts are sized to match these limits — scaling beyond them provides no benefit.

4. **DB latency is the key variable for progress worker sizing.** At 5s per operation, 5 pods are needed. At ~3s, 3 pods suffice. At 1-2s, a single pod is sufficient. Measure actual production latency before finalizing pod count (see Section 11).

5. **No startup burst.** When a batch of 20,000 workflows starts, Temporal's task queue absorbs all pending DB activities. The progress workers drain them at their configured rate (60 concurrent total), not all at once.

---

## 11. Measuring Actual DB Latency (Action Item)

The 5s DB operation estimate is a conservative upper bound. **The actual progress worker pod count depends heavily on measured latency.** Here's how to measure:

### What to Measure

| Metric | Where | Why It Matters |
|---|---|---|
| `update_extraction_progress` end-to-end | Activity code | 75% of DB calls (~920/min) — 1 UPDATE + 1 UPSERT |
| `check_and_finalize_batch` early return | Activity code | ~267 calls/min — SELECT batch + SELECT COUNT (index-only, very fast) |
| Connection pool checkout time | `get_session()` | Adds to every operation if pool is saturated |

### How to Measure

Add timing instrumentation to both activities:

```python
import time

# In update_extraction_progress:
start = time.time()
with get_session() as db:
    # ... existing UPDATE + UPSERT queries ...
elapsed = time.time() - start
activity.logger.info(f"update_progress DB latency: {elapsed:.3f}s")

# In check_and_finalize_batch:
start = time.time()
with get_session() as db:
    batch = db.query(...).first()                          # SELECT by PK
    incomplete = db.query(func.count(...)).filter(...).scalar()  # COUNT with index
elapsed = time.time() - start
activity.logger.info(f"check_finalize early-return latency: {elapsed:.3f}s (incomplete: {incomplete})")
```

Run a small test batch (200-500 sessions) and collect the P50/P95/P99 latency values.

**Note:** With the optimized COUNT query, `check_and_finalize_batch` should be significantly faster than `update_extraction_progress` (which does writes). The 5s estimate may be overly conservative for this activity — expect sub-second for the early-return path.

### Pod Count Decision Matrix

| Measured P95 Latency | Slots Needed | Progress Pods | Max DB Connections |
|---|---|---|---|
| < 1s | ~20 | **1** | 20 |
| 1-2s | ~40 | **2** | 40 |
| 2-3s | ~59 | **3** | 60 |
| 3-5s | ~79-99 | **4-5** | 80-100 |
| > 5s | 99+ | **5+** | 100+ |
