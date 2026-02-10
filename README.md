# Indication Extraction Agent

Batch processing system for extracting **indications**, **drugs**, and **drug classes** from medical conference abstracts using LLMs.

Two execution modes are available:

- **Temporal Workflows** (recommended) -- Production-grade orchestration with per-step checkpointing, fault tolerance, automatic retries, and token usage tracking
- **Step-Centric Scripts** -- Standalone batch scripts for running individual pipeline steps with parallel processing

Both modes share the same underlying LLM agents and support local filesystem or Google Cloud Storage (GCS).

## Table of Contents

- [Setup](#setup)
- [Input Format](#input-format)
- [Temporal Workflows](#temporal-workflows)
  - [Architecture](#architecture)
  - [Development](#development)
  - [Production](#production)
  - [Running Batch Extraction](#running-batch-extraction)
  - [Output Structure](#temporal-output-structure)
  - [Export Scripts](#temporal-export-scripts)
- [Step-Centric Scripts](#step-centric-scripts)
  - [Indication Pipeline](#indication-pipeline)
  - [Drug Pipeline](#drug-pipeline)
  - [Drug Class Pipeline](#drug-class-pipeline)
  - [Combined QA Export](#combined-qa-export)
  - [Output Structure](#script-output-structure)
- [Storage](#storage)
- [Environment Variables](#environment-variables)

---

## Setup

### 1. Install Poetry

```bash
curl -sSL https://install.python-poetry.org | python3 -
```

### 2. Install dependencies

```bash
poetry install
poetry shell
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your API keys and configuration
```

See [Environment Variables](#environment-variables) for the full list.

---

## Input Format

All pipelines expect a CSV file with at minimum these columns:

| Column | Required | Description |
|--------|----------|-------------|
| `abstract_id` | Yes | Unique identifier for the abstract |
| `abstract_title` | Yes | Title text to extract from |
| `session_title` | No | Conference session name (used by indication extraction) |
| `full_abstract` | No | Full abstract text (improves extraction quality) |
| `firm` | No | Pharmaceutical company names (`;;` separated) |

### Data Preprocessing

If your input CSV has malformed `firm` data (e.g., prefixed with `<0>`), clean it first:

```bash
python -m src.scripts.drug_class.clean_firms [input_path] [output_path]
```

---

## Temporal Workflows

The Temporal-based approach processes each abstract through a single orchestrated workflow that runs up to three pipelines sequentially:

1. **Drug Pipeline** -- extraction + validation
2. **Drug Class Pipeline** -- 5-step extraction + validation (depends on drug results)
3. **Indication Pipeline** -- extraction + validation

Each step is checkpointed. If a workflow fails mid-execution, it resumes from the last completed step rather than re-running everything.

### Architecture

The system uses 6 task queues, each served by a dedicated worker:

| Worker | Task Queue | Purpose | Concurrency |
|--------|------------|---------|-------------|
| Workflow | `extraction-workflows` | Lightweight orchestration | 100 workflows |
| Checkpoint | `checkpoint-storage` | Load/save status and step outputs | 50 activities |
| Drug | `drug-activities` | Drug extraction and validation | 15 activities |
| Drug Class | `drug-class-activities` | 5-step drug class pipeline + validation | 10 activities |
| Indication Extraction | `indication-extraction` | Indication extraction (fast LLM) | 20 activities |
| Indication Validation | `indication-validation-slow` | Indication validation (slow LLM) | 5 activities |

Queues are separated by workload characteristics -- fast LLM calls, slow LLM calls, search APIs, and storage I/O each get their own queue so they don't block each other.

**Retry and Timeouts:**

| Activity Type | Timeout | Max Retries | Backoff |
|---------------|---------|-------------|---------|
| Storage | 30s | 3 | 1s -> 10s |
| Fast LLM (GPT-4) | 2 min | 3 | 5s -> 30s |
| Slow LLM (Sonnet 4.5) | 5 min | 2 | 15s -> 60s |
| Search (Tavily) | 45s | 3 | 2s -> 15s |

Retries are managed entirely by Temporal -- no application-level retry logic.

**Token Tracking:**

Every LLM activity tracks token consumption (`input_tokens`, `output_tokens`, `total_tokens`, `llm_calls`) via a LangChain callback handler. These metrics are recorded per step in `status.json` and aggregated at the workflow level.

### Development

For local development, run the Temporal dev server and all workers in a single process:

```bash
# Terminal 1: Start Temporal dev server
temporal server start-dev

# Terminal 2: Start all workers (single process)
python -m src.temporal.workers.run_all
```

To auto-shutdown workers after a period of inactivity:

```bash
IDLE_SHUTDOWN_MINUTES=5 python -m src.temporal.workers.run_all
```

### Production

For production, run each worker as a separate process for independent scaling and resource isolation:

```bash
# Workflow orchestration (no LLM calls, lightweight)
python -m src.temporal.workers.workflow_worker

# Checkpoint storage (fast I/O)
python -m src.temporal.workers.checkpoint_worker

# Drug extraction + validation
python -m src.temporal.workers.drug_worker

# Drug class 5-step pipeline + validation
python -m src.temporal.workers.drug_class_worker

# Indication extraction (fast LLM)
python -m src.temporal.workers.indication_extraction_worker

# Indication validation (slow LLM)
python -m src.temporal.workers.indication_validation_worker
```

All activity workers support `IDLE_SHUTDOWN_MINUTES` for auto-shutdown after inactivity.

Point workers to your Temporal cluster via environment variables:

```bash
TEMPORAL_HOST=your-temporal-host:7233
TEMPORAL_NAMESPACE=your-namespace
```

### Running Batch Extraction

Use the Temporal client to submit workflows:

```bash
# Local storage
python -m src.temporal.client \
    --input data/abstract_titles.csv \
    --storage_path data/output \
    --limit 10

# GCS storage
python -m src.temporal.client \
    --input gs://bucket/Conference/abstract_titles.csv \
    --storage_path gs://bucket/Conference

# Run specific pipelines only
python -m src.temporal.client \
    --input data/abstract_titles.csv \
    --storage_path data/output \
    --pipelines drug,indication

# Control concurrency (default: 50)
python -m src.temporal.client \
    --input data/abstract_titles.csv \
    --storage_path data/output \
    --max_concurrent 100
```

**Client options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--input` | required | Input CSV path (local or `gs://`) |
| `--storage_path` | `""` | Base path for checkpoints and outputs |
| `--pipelines` | `drug,drug_class,indication` | Comma-separated pipelines to run |
| `--max_concurrent` | `50` | Maximum concurrent workflow executions |
| `--limit` | all | Limit number of abstracts to process |

If any workflows fail, the client writes a retry CSV (`failed_<timestamp>.csv`) that can be passed directly as `--input` for re-processing.

### Temporal Output Structure

All outputs are stored under a single directory per abstract:

```
{storage_path}/
├── abstracts/
│   └── {abstract_id}/
│       ├── status.json                 # Workflow status + token metrics
│       ├── drug_extraction.json        # Drug extraction result
│       ├── drug_validation.json        # Drug validation result
│       ├── drug_class_steps1_3.json    # Drug class steps 1-3 combined
│       ├── drug_class_step4.json       # Explicit drug class extraction
│       ├── drug_class_step5.json       # Consolidation (if step 4 found classes)
│       ├── drug_class_validation.json  # Drug class validation
│       ├── indication_extraction.json  # Indication extraction result
│       └── indication_validation.json  # Indication validation result
└── search_cache/
    └── {drug_name}.json                # Cached Tavily search results
```

**`status.json` example:**

```json
{
  "abstract_id": "719267",
  "abstract_title": "...",
  "status": "success",
  "drug": {
    "extraction": { "status": "success", "llm_calls": 1, "input_tokens": 4471, "output_tokens": 729, "tokens": 5200 },
    "validation": { "status": "success", "llm_calls": 1, "input_tokens": 9298, "output_tokens": 635, "tokens": 9933 }
  },
  "drug_class": {
    "step1_regimen": { "status": "success", "llm_calls": 1, "tokens": 842, "..." : "..." },
    "step2_extraction": { "..." : "..." },
    "step3_selection": { "..." : "..." },
    "step4_explicit": { "..." : "..." },
    "step5_consolidation": { "..." : "..." },
    "validation": { "..." : "..." }
  },
  "indication": {
    "extraction": { "status": "success", "llm_calls": 2, "tokens": 14719, "..." : "..." },
    "validation": { "..." : "..." }
  },
  "metrics": {
    "duration_seconds": 85.18,
    "llm_calls": 11,
    "input_tokens": 66596,
    "output_tokens": 6635
  },
  "errors": [],
  "last_updated": "2026-02-09T13:07:32.478146Z"
}
```

### Temporal Export Scripts

Three scripts read Temporal workflow outputs and produce CSV reports.

**Workflow Metrics** -- One row per abstract with status, timing, and per-step token consumption:

```bash
# Discovers abstracts automatically from data directory
python -m src.scripts.temporal.workflow_metrics \
    --data_dir data/output \
    --output workflow_metrics.csv

# GCS
python -m src.scripts.temporal.workflow_metrics \
    --data_dir gs://bucket/Conference \
    --output gs://bucket/Conference/workflow_metrics.csv
```

**Indication Export** -- Extraction + validation results flattened into a QA CSV:

```bash
python -m src.scripts.temporal.indication_exporter \
    --input data/abstract_titles.csv \
    --data_dir data/output \
    --output indication_export.csv
```

**Drug + Drug Class Export** -- Combined drug extraction/validation and drug class steps 1-5 in a single QA CSV:

```bash
python -m src.scripts.temporal.drug_drug_class_exporter \
    --input data/abstract_titles.csv \
    --data_dir data/output \
    --output drug_drug_class_export.csv
```

All export scripts support `--limit N` and both local and GCS paths.

---

## Step-Centric Scripts

Standalone batch scripts that run individual pipeline steps with parallel processing. Each script manages its own status tracking and retry logic.

### Indication Pipeline

**Extraction:**

```bash
python -m src.scripts.indication.extraction_processor \
    --input gs://bucket/Conference/abstract_titles.csv \
    --output_dir gs://bucket/Conference/indication
```

**Validation:**

```bash
python -m src.scripts.indication.validation_processor \
    --input gs://bucket/Conference/abstract_titles.csv \
    --output_dir gs://bucket/Conference/indication
```

### Drug Pipeline

**Extraction:**

```bash
python -m src.scripts.drug.extraction_processor \
    --input gs://bucket/Conference/abstract_titles.csv \
    --output_dir gs://bucket/Conference/drug
```

**Validation:**

```bash
python -m src.scripts.drug.validation_processor \
    --input gs://bucket/Conference/abstract_titles.csv \
    --output_dir gs://bucket/Conference/drug
```

### Drug Class Pipeline

**Extraction** (runs all 5 steps):

```bash
python -m src.scripts.drug_class.extraction_processor \
    --input gs://bucket/Conference/abstract_titles.csv \
    --drug_output_dir gs://bucket/Conference/drug \
    --output_dir gs://bucket/Conference/drug_class
```

**Validation:**

```bash
python -m src.scripts.drug_class.validation_processor \
    --input gs://bucket/Conference/abstract_titles.csv \
    --output_dir gs://bucket/Conference/drug_class
```

Individual steps can also be run separately (`step1_processor` through `step5_processor`).

### Combined QA Export

Export drug and drug class results into a single CSV for QA review:

```bash
python -m src.scripts.drug_drug_class_exporter \
    --input gs://bucket/Conference/abstract_titles.csv \
    --drug_output_dir gs://bucket/Conference/drug \
    --drug_class_output_dir gs://bucket/Conference/drug_class \
    --output qa_export.csv
```

### Script Output Structure

Step-centric scripts write to separate directories per pipeline:

```
{conference_name}/
├── input/
│   └── abstract_titles.csv
├── indication/
│   ├── batch_status.json
│   ├── extraction_*.csv
│   └── abstracts/{abstract_id}/
│       ├── extraction.json
│       ├── validation.json
│       └── status.json
├── drug/
│   └── (same structure)
└── drug_class/
    ├── extraction_batch_status.json
    ├── validation_batch_status.json
    └── abstracts/{abstract_id}/
        ├── step1_output.json ... step5_output.json
        ├── status.json
        └── validation_{drug}.json
```

---

## Storage

The system automatically detects storage type from path prefix.

**Local storage:**

```bash
--input data/Conference/abstract_titles.csv
--output_dir data/Conference/drug
--storage_path data/output
```

**GCS storage:**

```bash
--input gs://bucket-name/Conference/abstract_titles.csv
--output_dir gs://bucket-name/Conference/drug
--storage_path gs://bucket-name/Conference
```

GCS requires `GOOGLE_APPLICATION_CREDENTIALS` and `GCS_PROJECT_ID` in your `.env`.

---

## Environment Variables

Configure in `.env` (see `.env.example`):

| Variable | Description |
|----------|-------------|
| **LLM** | |
| `LLM_API_KEY` | API key for LLM provider |
| `LLM_BASE_URL` | Base URL for LLM API |
| `INDICATION_LLM_MODEL` | Model for indication extraction |
| `INDICATION_VALIDATION_LLM_MODEL` | Model for indication validation |
| `DRUG_EXTRACTION_MODEL` | Model for drug extraction |
| `DRUG_VALIDATION_MODEL` | Model for drug validation |
| `DRUG_CLASS_REGIMEN_MODEL` | Model for step 1 regimen identification |
| `DRUG_CLASS_EXTRACTION_MODEL` | Model for step 2 drug class extraction |
| `DRUG_CLASS_GROUNDED_MODEL` | Model for step 2 grounded search fallback |
| `DRUG_CLASS_SELECTION_MODEL` | Model for step 3 class selection |
| `DRUG_CLASS_EXPLICIT_MODEL` | Model for step 4 explicit extraction |
| `DRUG_CLASS_CONSOLIDATION_MODEL` | Model for step 5 consolidation |
| **Search** | |
| `TAVILY_API_KEY` | Tavily search API key (drug class pipeline) |
| **Observability** | |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (optional) |
| `LANGFUSE_HOST` | Langfuse host URL (optional) |
| **GCS** | |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to GCS service account JSON |
| `GCS_PROJECT_ID` | Google Cloud project ID |
| `GCS_BUCKET_NAME` | Default GCS bucket name |
| **Temporal** | |
| `TEMPORAL_HOST` | Temporal server address (default: `localhost:7233`) |
| `TEMPORAL_NAMESPACE` | Temporal namespace (default: `default`) |
| `IDLE_SHUTDOWN_MINUTES` | Auto-shutdown workers after N minutes idle (optional) |

## Common Options

Most scripts and the Temporal client support:

- `--limit N` -- Process only first N abstracts
- `--parallel_workers N` -- Number of parallel workers (scripts only, default varies by pipeline)
- `--max_concurrent N` -- Maximum concurrent workflows (Temporal client only, default: 50)

## License

MIT
