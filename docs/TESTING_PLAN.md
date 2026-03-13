# Unit Testing Implementation Plan for Entity Extraction Agent

## Executive Summary

This plan outlines a comprehensive testing strategy to achieve 80%+ test coverage for the entity-extraction-agent project, prioritizing agent logic (drug, indication, drug_class extraction/validation) with both fast unit tests and integration tests.

**Target:** ~19,000 lines of Python code with 0% current coverage
**Approach:** Build from agent logic outward, with dual test suite (fast unit + integration)

---

## Testing Strategy Overview

### Coverage Goals
- **Target: 80%+ overall coverage**
- Critical paths: 95%+ coverage (extraction/validation logic, Temporal workflows)
- Utility functions: 70%+ coverage
- Integration tests: End-to-end pipeline validation

### Testing Philosophy
1. **Mock by default, real APIs optional**: All tests use mocks, but support `--use-real-apis` flag for validation
2. **Dev environment integration**: MySQL and GCS tests connect to dev environment from local
3. **Fast feedback loop**: Unit tests < 1 minute, integration tests separate suite (5-10 minutes)
4. **Temporal testing**: Both workflow integration tests and activity unit tests

---

## Phase 1: Testing Infrastructure Setup

### 1.1 Dependencies and Configuration

**Add to pyproject.toml:**

```toml
[dependency-groups]
dev = [
    "pytest~=8.3.4",
    "pytest-asyncio~=0.24.0",
    "pytest-mock~=3.14.0",
    "pytest-cov~=6.0.0",
    "pytest-env~=1.1.5",
    "faker~=33.1.0",
    "freezegun~=1.5.1",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
markers = [
    "unit: Fast unit tests with mocked dependencies",
    "integration: Slower integration tests with real services",
    "temporal: Temporal workflow/activity tests",
    "slow: Tests that take >5 seconds",
    "requires_api: Tests that can optionally use real API calls",
]
addopts = [
    "-v",
    "--strict-markers",
    "--tb=short",
    "--cov=src",
    "--cov-report=term-missing",
    "--cov-report=html",
]
env = [
    "ENVIRONMENT=dev",
]

[tool.coverage.run]
source = ["src"]
omit = [
    "*/tests/*",
    "*/test_*.py",
    "*/__pycache__/*",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
    "if typing.TYPE_CHECKING:",
]
```

### 1.2 Test Directory Structure

```
tests/
├── __init__.py
├── conftest.py                          # Global fixtures
├── fixtures/                            # Test data fixtures
│   ├── __init__.py
│   ├── abstracts.py                     # Sample abstracts
│   ├── llm_responses.py                 # Mock LLM responses
│   ├── search_results.py                # Mock Tavily results
│   └── database_fixtures.py             # DB test data
│
├── unit/                                # Fast unit tests (<1 min total)
│   ├── __init__.py
│   ├── conftest.py                      # Unit test fixtures
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── core/
│   │   │   ├── test_llm_handler.py
│   │   │   ├── test_storage.py
│   │   │   ├── test_config.py
│   │   │   ├── test_token_tracking.py
│   │   │   └── test_ems_logger.py
│   │   │
│   │   ├── drug/
│   │   │   ├── test_extraction_agent.py
│   │   │   ├── test_validation_agent.py
│   │   │   └── test_schemas.py
│   │   │
│   │   ├── drug_class/
│   │   │   ├── test_pipeline.py
│   │   │   ├── test_step1_regimen.py
│   │   │   ├── test_step2_search.py
│   │   │   ├── test_step2_extraction.py
│   │   │   ├── test_step3_selection.py
│   │   │   ├── test_step4_explicit.py
│   │   │   ├── test_step5_consolidation.py
│   │   │   ├── test_validation.py
│   │   │   └── test_schemas.py
│   │   │
│   │   └── indication/
│   │       ├── test_extraction_agent.py
│   │       ├── test_validation_agent.py
│   │       ├── test_tools.py
│   │       └── test_schemas.py
│   │
│   ├── temporal/
│   │   ├── __init__.py
│   │   ├── test_activities/
│   │   │   ├── test_drug_activities.py
│   │   │   ├── test_drug_class_activities.py
│   │   │   ├── test_indication_activities.py
│   │   │   ├── test_result_storage.py
│   │   │   └── test_extraction_progress.py
│   │   │
│   │   └── test_client.py
│   │
│   └── db/
│       ├── test_engine.py
│       └── test_models.py
│
└── integration/                         # Integration tests (5-10 min)
    ├── __init__.py
    ├── conftest.py                      # Integration fixtures
    │
    ├── test_drug_pipeline_e2e.py
    ├── test_drug_class_pipeline_e2e.py
    ├── test_indication_pipeline_e2e.py
    │
    ├── temporal/
    │   ├── test_workflow_execution.py
    │   ├── test_workflow_pause_resume.py
    │   ├── test_workflow_signals.py
    │   └── test_state_persistence.py
    │
    ├── storage/
    │   ├── test_gcs_operations.py
    │   └── test_checkpoint_persistence.py
    │
    └── database/
        ├── test_db_transactions.py
        └── test_progress_tracking.py
```

---

## Implementation Order

### Week 1-2: Infrastructure + Drug Agent
1. Set up test infrastructure (conftest.py, fixtures)
2. Implement drug extraction tests
3. Implement drug validation tests
4. Target: 95%+ coverage for drug agent

### Week 3-4: Drug Class Agent
1. Implement pipeline orchestration tests
2. Implement step 1-5 tests
3. Implement search caching tests
4. Implement validation tests
5. Target: 95%+ coverage for drug_class agent

### Week 5: Indication Agent
1. Implement extraction agent tests (LangGraph)
2. Implement validation tests
3. Implement tools tests (rules loading)
4. Target: 95%+ coverage for indication agent

### Week 6: Temporal Activities
1. Implement activity unit tests (all 15+ activities)
2. Implement result storage tests
3. Implement progress tracking tests
4. Target: 90%+ coverage for activities

### Week 7: Temporal Workflows
1. Implement workflow execution tests
2. Implement pause/resume tests
3. Implement state persistence tests
4. Target: 85%+ coverage for workflows

### Week 8: Core Utilities + Integration
1. Implement storage tests
2. Implement database tests
3. Implement LLM handler tests
4. Implement end-to-end integration tests
5. Target: 80%+ overall coverage

---

## Success Metrics

### Coverage Targets
- **Overall: 80%+**
- Drug agent: 95%+
- Drug class agent: 95%+
- Indication agent: 95%+
- Temporal activities: 90%+
- Temporal workflows: 85%+
- Core utilities: 80%+

### Test Execution Performance
- Unit tests: < 1 minute
- Integration tests: 5-10 minutes
- Total test suite: < 15 minutes

### Quality Gates
- All tests pass before merge
- No decrease in coverage
- Critical paths: 100% coverage
- Integration tests: Pass with dev environment

---

## Next Steps

After plan approval:
1. Create test directory structure
2. Set up pytest configuration
3. Implement global fixtures
4. Start with drug agent tests (highest priority)
5. Iterate through remaining components

**Estimated Total Effort:** 8 weeks (1 developer full-time)
**Deliverables:**
- 200+ test files
- 80%+ code coverage
- CI/CD integration
- Documentation for running tests

For detailed test examples and implementation details, see the full plan at `/Users/sunny/.claude/plans/abstract-noodling-dolphin.md`.
