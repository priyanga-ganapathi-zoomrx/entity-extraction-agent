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
| **Drug Class Worker** | Calls Gemini for drug class classification + validation | No | Yes (Gemini) |
| **Result Storage Worker** | Saves AI outputs as JSON files to GCS | No | No |
| **Extraction Progress Worker** | Updates MySQL tables with workflow status; checks if batch is complete | **Yes** | No |

---

## 4. How a Single Workflow Executes

### Indication Workflow (per session)

```
Step 1:  Update DB → status = "running"              0.1s    ← DB connection
Step 2:  Call Gemini → extract indication            11.0s
Step 3:  Save result to GCS                           1.0s
Step 4:  Call Claude → validate indication           55.0s
Step 5:  Save result to GCS                           1.0s
Step 6:  Update DB → status = "success"              0.1s    ← DB connection
Step 7:  Check if batch is complete                   0.1s    ← DB connection
                                                     ──────
Total per workflow:                                  68.3s
DB time:                                              0.3s (~0.4%)
```

### Drug + Drug Class Workflow (per session)

**Typical path (drug class found via cache — ~97% of sessions):**

```
Step 1:   Update DB → drug status = "running"         0.1s    ← DB connection
Step 2:   Call Gemini → extract drugs                 10.0s
Step 3:   Save result to GCS                           1.0s
Step 4:   Call Gemini → validate drugs                15.0s
Step 5:   Save result to GCS                           1.0s
Step 6:   Update DB → drug status = "success"          0.1s    ← DB connection
Step 7:   Update DB → drug_class status = "running"    0.1s    ← DB connection
Step 8:   Call Gemini → step4 explicit classification   5.3s
Step 9:   Save result to GCS                           1.0s
Step 10:  Call Gemini → validate drug classes           5.6s
Step 11:  Save result to GCS                           1.0s
Step 12:  Update DB → drug_class status = "success"    0.1s    ← DB connection
Step 13:  Check if batch is complete                    0.1s    ← DB connection
                                                      ──────
Total per workflow (typical):                         39.5s
DB time:                                               0.5s (~1.3%)
```

**Slow path (drug class requires web search — ~3% of sessions):**

```
Additional steps between 7 and 8:
  Step 7a:  Call Gemini → step1 regimen identification  12.2s
  Step 7b:  Tavily web search + Gemini extraction       42.9s
  Step 7c:  Call Gemini → step3 selection                0.0s
  Step 7d:  Save results to GCS                          1.0s
  Step 7e:  Call Gemini → step5 consolidation             1.9s
  Step 7f:  Save result to GCS                           1.0s
                                                        ──────
Total per workflow (slow path):                       ~100s
```

> **Note:** The slow path is rare (~3% of sessions based on Zeus testing: 10 out of 347 drug workflows). It does not significantly impact overall throughput.

**Note on DB timing:** Measured in Zeus (production-like) environment. DB operations average ~0.1s including connection pool checkout, query execution, commit, and connection return. The original 5s estimate was a conservative upper bound — actual latency is 50× faster.

---

## 5. Concurrency & Throughput — Measured Results

### AI Model Rate Limits

| AI Model | Rate Limit | Used By |
|---|---|---|
| Claude Sonnet 4.5 | 3,000,000 tokens/min | Indication validation |
| Gemini | 10,000,000 tokens/min | All extraction + drug/drug_class validation |

### Measured Throughput (Zeus Environment, 1000 Sessions)

| Run Type | Sessions | Time | Throughput |
|---|---|---|---|
| Indication only | 504 | 4m 33s | **110.8/min** |
| Drug + Drug Class only | 504 | 4m 33s | **110.8/min** |
| Combined (all 3 entities) | 504 | 7m 59s | **~63 sessions/min** (~189 entity-completions/min) |
| Combined (all 3 entities) | 1000 | 16m 16s | **~61.5 sessions/min** (~185 entity-completions/min) |

### Measured Token Usage (1000-session combined run)

| Step | Model | Avg Tokens/Call | Count | Measured? |
|---|---|---|---|---|
| indication_extraction | Gemini | ~15,000 | 1000 | Estimated |
| indication_validation | Claude | 24,617 | 1000 | Yes |
| drug_extraction | Gemini | ~15,000 | 1000 | Estimated |
| drug_validation | Gemini | ~20,000 | 1000 | Estimated |
| drug_class_step4_explicit | Gemini | 6,924 | 1000 | Yes |
| drug_class_validation | Gemini | 22,633 | 1000 | Yes |
| drug_class_step1_regimen | Gemini | 1,531 | 10 | Yes |
| drug_class_step2_tavily | Gemini | 13,842 | 10 | Yes |
| drug_class_step5_consolidation | Gemini | 6,632 | 6 | Yes |

> **Logging format note:** `ActivityLogger` (indication/drug) writes tokens to nested `llm.*` fields in EMS. `get_logger()` (drug_class) writes to flat top-level `total_tokens` field. Both are queryable in Kibana — use `llm.input_tokens` for indication/drug, `total_tokens` for drug_class.

**Estimated rate limit utilization (combined run):**
- **Claude:** ~1.5M tokens/min (50% of 3M limit)
- **Gemini:** ~7-8M tokens/min (70-80% of 10M limit)

### Production Projections (Combined Run, All 3 Entities)

| Batch Size | Estimated Time |
|---|---|
| 500 | ~8 min |
| 1,000 | ~16 min |
| 2,000 | ~32 min |
| 5,000 | ~81 min |
| 10,000 | ~162 min (~2.7 hrs) |

Throughput scales linearly — no degradation observed from 504 to 1000 sessions. 10K projection is extrapolated and should be validated with a full-scale test (Phase 5 pending). At 10K scale, Temporal server memory, task queue depth, and MySQL table growth during `check_and_finalize` could introduce new bottlenecks.

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

### Measured DB Activity Throughput

At steady state in combined run (~185 workflows/min across all entities):

**Indication workflows (~62/min):**
- Each workflow makes 2 `update_progress` calls + 1 `check_and_finalize` call
- `update_progress` calls: ~124/min

**Drug workflows (~62/min):**
- Each workflow makes 4 `update_progress` calls + 1 `check_and_finalize` call
- `update_progress` calls: ~248/min

### Measured Concurrent DB Connections

| Source | Activities/Min | Avg Duration | Avg Concurrent Slots |
|---|---|---|---|
| `update_progress` (indication) | ~124/min | 0.1s | ~0.2 |
| `update_progress` (drug + drug_class) | ~248/min | 0.1s | ~0.4 |
| `check_and_finalize_batch` | ~124/min | 0.1s | ~0.2 |
| **Total** | **~496/min** | | **~0.8 slots needed** |

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│   Measured DB latency:              ~0.1s per operation          │
│                                                                 │
│   Measured peak DB connections:     6 (via Grafana)             │
│                                                                 │
│   DB activity tasks per minute:     ~496                        │
│                                                                 │
│   DB queries per minute:            ~620                        │
│                                                                 │
│   3 pods × 20 slots = 60 slots available                       │
│   Only 6 used at peak — massive headroom                        │
│                                                                 │
│   Conclusion: 3 pods is more than sufficient.                   │
│   Could run with 1 pod, but 3 provides redundancy.             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Why 3 Pods (Despite Only Needing ~6 Connections)

With 0.1s per DB operation, even 1 pod (20 slots) handles the load easily. We keep 3 pods for:
- **Redundancy:** If one pod crashes, the other 2 absorb the load
- **KEDA scaling:** 3 pods drain the task queue faster during burst periods
- **Batch finalization:** The last `check_and_finalize_batch` call runs heavier queries (GROUP BY, XLSX generation) — having spare capacity prevents queue backup

### DB Queries Breakdown Per Minute

| Activity | Calls/Min | Queries/Call | Total Queries/Min | Rows Read/Call |
|---|---|---|---|---|
| `update_progress` | ~372 | 1 (UPDATE or UPSERT) | ~372 | 1 |
| `check_and_finalize` (early return) | ~124 | 2 (SELECT batch + SELECT COUNT) | ~248 | **~0** (index count) |
| `check_and_finalize` (finalization) | once per batch | 5 (SELECT + COUNT + GROUP BY + UPDATE + GROUP BY) | ~5 | **~0** (aggregates) |
| **Total** | **~496** | | **~620** | |

**Optimization: `update_extraction_progress` uses 1 query per call** for running/pending statuses (terminal-only update pattern). `check_and_finalize_batch` uses `SELECT COUNT(*) WHERE status IN ('pending', 'running')` for fast index-only early returns.

---

## 7. Full Batch Lifetime Numbers (10K Sessions) — Updated with Measured Data

For a 10,000-session congress extracting all 3 entity types (combined run):

| Metric | Estimated (original) | Measured (Zeus) |
|---|---|---|
| Total duration | ~4 hrs | **~2.7 hrs** |
| Throughput | ~82 indication + 150 drug/min | **~185 workflows/min combined (~62/min per entity)** |
| Peak DB connections | 60-100 | **6** |
| DB queries/min | ~2,374 | **~620** |

| Phase | Duration | DB Activity Tasks | DB Queries |
|---|---|---|---|
| All entities (combined) | **~2.7 hrs** | ~80,000 | ~100,000 |

All of this runs through **at most 6 concurrent DB connections** at peak (measured via Grafana). The 3-pod configuration provides 10× headroom.

---

## 8. Pod Configuration

### Summary Table

| Worker | Task Queue | Purpose | Pods | Concurrency/Pod | DB? |
|---|---|---|---|---|---|
| workflow-worker | extraction-workflows | Orchestration | **3** | 100 workflow tasks | No |
| indication-extraction | indication-extraction | Gemini LLM | **2** | 15 activities | No |
| indication-validation | indication-validation-slow | Claude LLM | **7** | 10 activities | No |
| drug-worker | drug-activities | Gemini LLM | **7** | 15 activities | No |
| drug-class-worker | drug-class-activities | Gemini LLM | **5** | 10 activities | No |
| result-storage | result-storage | GCS uploads | **10** | 30 activities | No (see note) |
| extraction-progress | entity-mapping-progress | **DB writes** | **3** | 20 activities | **Yes** |
| **Total** | | | **37 pods** | | |

### How Pod Counts Were Calculated — Updated with Measured Data

Pod counts were validated through Zeus stress testing (production-like environment) with real LLM models.

**Indication workflow (measured: ~68s per abstract):**

```
    ┌──────────┐         ┌─────────────────────────────────────┐
    │Extraction│         │         Validation (Claude)         │
    │ (Gemini) │         │            ~55 seconds              │
    │  ~11s    │         │    avg 24,617 tokens/call           │
    └──────────┘         └─────────────────────────────────────┘
     16% of time                    81% of time

    DB operations: ~0.3s (<1% of time)

    2 extraction pods × 15 = 30 slots → capacity: ~164/min (not bottleneck)
    7 validation pods × 10 = 70 slots → capacity: ~76/min (rate-limit bound)
```

> **Key finding:** Scaling indication-extraction from 1 to 2 pods improved throughput from 68/min to 110/min. With 1 pod, the 15 extraction slots created a queue bottleneck that starved validation. 2 pods eliminated this.

**Drug + Drug Class workflow (measured: ~40s per abstract, typical path):**

```
    ┌──────────┐    ┌────────────┐    ┌─────────┐ ┌──────────┐
    │  Extract │    │  Validate  │    │  Step4  │ │ Validate │
    │  Drugs   │    │   Drugs    │    │ Explicit│ │Drug Class│
    │  ~10s    │    │   ~15s     │    │  ~5.3s  │ │  ~5.6s   │
    └──────────┘    └────────────┘    └─────────┘ └──────────┘
     drug-activities queue             drug-class-activities queue

    DB operations: ~0.5s (<2% of time)

    7 drug pods × 15 = 105 slots (extract + validate share same queue)
    5 drug-class pods × 10 = 50 slots (step4 + validation share same queue)
```

> **Key finding:** Drug extraction and validation share the `drug-activities` queue. Scaling from 5 to 7 pods didn't improve isolated throughput (~110/min), suggesting the ~110/min ceiling is driven by Gemini rate limits or KEDA startup overhead, not slot count. 7 pods kept for combined-run headroom.

**Result storage: Why 10 pods?**

During Phase 2 stress testing (5K sessions with cheap model), result-storage (1 pod × 30 slots) became a bottleneck — cheap models complete in <1s, flooding the save queue faster than 30 slots could drain. With real models (10-55s per LLM call), 1 pod was sufficient. 10 pods provides headroom for burst scenarios and prevents result-storage from ever becoming a bottleneck regardless of LLM speed.

**Combined run bottleneck: Gemini rate limit**

When all 3 entities run together, they share Gemini bandwidth. Indication extraction, drug extraction, drug validation, drug class steps, and drug class validation all use Gemini. Only indication validation uses Claude. This is why per-entity throughput drops from ~110/min (isolated) to ~62/min (combined) — Gemini's 10M tokens/min is the binding constraint.

---

## 9. Resource Sizing Recommendations — Updated with Measured Data

### MySQL Database

| Metric | Estimated (original) | Measured (Zeus) |
|---|---|---|
| Peak concurrent connections | 60-100 | **6** |
| Queries per second (sustained) | ~40 QPS | **~10 QPS** |
| Recommended connection pool | 80-120 | **60 (3 pods × 20 — provides 10× headroom)** |

| Metric | Value |
|---|---|
| Most frequent query | `UPDATE entity_mapping_batches_sessions SET status=? WHERE batch_id=? AND session_id=? AND entity=?` — ~372 times/min, 1 row per call (indexed) |
| Most frequent read | `SELECT COUNT(*) FROM entity_mapping_batches_sessions WHERE batch_id=? AND status IN ('pending','running')` — ~124 times/min, index-only count |
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
| indication-extraction | 2 | Tasks in `indication-extraction` queue |
| indication-validation | 7 | Tasks in `indication-validation-slow` queue |
| drug-worker | 7 | Tasks in `drug-activities` queue |
| drug-class-worker | 5 | Tasks in `drug-class-activities` queue |
| result-storage | 10 | Tasks in `result-storage` queue |
| extraction-progress | 3 | Tasks in `entity-mapping-progress` queue |

> **Note on KEDA startup:** Pods scaling from zero adds 30-90s of startup latency at the beginning of a batch. This reduces average throughput for small batches (<200 sessions) but has negligible impact on larger batches where steady-state throughput dominates.

---

## 10. Key Takeaways — Validated by Zeus Stress Testing

1. **DB connections are minimal.** Peak concurrent connections measured at **6** (via Grafana) despite 1000 sessions with all 3 entities. The 3-pod configuration (60 slot capacity) provides 10× headroom. DB latency is ~0.1s, not the estimated 5s.

2. **34 out of 37 pods have zero DB connections.** The entire DB footprint is 3 progress worker pods, and they use <10% of their capacity.

3. **Gemini rate limit is the binding constraint in combined runs.** When all 3 entities run together, most steps share Gemini's 10M tokens/min limit (~70-80% utilization). Per-entity throughput drops from ~110/min (isolated) to ~62/min (combined). Claude (3M/min) is only used for indication validation and runs at ~50% utilization.

4. **Throughput scales linearly.** No degradation observed from 504 to 1000 sessions. 10K sessions projected at ~2.7 hours (combined run, all 3 entities).

5. **No startup burst or connection leak.** Temporal's task queue absorbs all pending activities. DB connections return to baseline after batch completion. No connection leak detected across multiple stress test runs.

6. **KEDA startup adds fixed overhead.** First 30-90s of a batch has reduced throughput while pods scale from zero. Impact is negligible for batches >200 sessions.

---

## 11. Measured DB Latency (Zeus Stress Test Results)

DB latency was measured during Zeus stress testing with 504–1000 sessions across all entity types.

### Measured Results

| Metric | Value |
|---|---|
| `update_extraction_progress` avg latency | **~0.1s** |
| `check_and_finalize_batch` early return avg | **~0.1s** |
| Peak concurrent DB connections (Grafana) | **6** |
| Connection pool checkout time | **Negligible** (pool never saturated) |

### Conclusion

The original 5s estimate was 50× too conservative. At 0.1s per operation:
- **1 pod** (20 slots) would be sufficient for throughput
- **3 pods** kept for redundancy and burst handling
- No connection pool tuning needed — default settings work well

### Stress Test Summary

| Test | Sessions | Entities | Duration | Peak DB Conn | Errors |
|---|---|---|---|---|---|
| Smoke test | 50 | All | ~1 min | 4 | None |
| Queue burst (cheap model) | 5,000 | All | ~10 min | 6 | None |
| Abort flow | 500 | All | ~3 min | 4 | None |
| Indication only (real model) | 504 | Indication | 4m 33s | 4 | None |
| Drug+DC only (real model) | 504 | Drug+DC | 4m 33s | 5 | None |
| Combined (real model) | 504 | All | 7m 59s | 6 | None |
| Combined (real model) | 1000 | All | 16m 16s | 6 | None |
