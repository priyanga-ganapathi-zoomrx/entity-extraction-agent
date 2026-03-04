"""Abstract Extraction Workflow - Temporal entity workflow for medical abstract processing.

Entity workflow pattern: one long-lived workflow per session+entity combination.
Uses Temporal's event history as the checkpoint mechanism (no GCS status.json).

Two entity types:
  - entity="drug"       → Drug extraction + validation → Drug class pipeline + validation
  - entity="indication" → Indication extraction + validation

Pause/Resume:
  On pipeline failure, the workflow pauses via workflow.wait_condition and waits
  for a retry or abort signal from the admin portal.  On retry, the while loop
  restarts — Temporal replays completed activities from event history (instant,
  no re-execution), then runs only the failed step.

GCS is used ONLY for storing downloadable result files, not for checkpointing.
SQL status is updated via the update_extraction_progress activity stub.
"""

import asyncio
from datetime import timedelta
from typing import Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from src.temporal.config import (
        TaskQueues,
        Timeouts,
        RetryPolicies,
    )
    from src.temporal.schemas.workflow import (
        AbstractExtractionInput,
        AbstractExtractionOutput,
        StepResult,
    )
    # Result storage activity
    from src.temporal.activities.result_storage import save_step_output
    # Extraction progress activity (SQL stub)
    from src.temporal.activities.extraction_progress import update_extraction_progress
    # Drug activities + schemas
    from src.agents.drug.schemas import (
        DrugInput,
        ValidationInput as DrugValidationInput,
    )
    from src.temporal.activities.drug import extract_drugs, validate_drugs
    # Drug class activities + schemas
    from src.agents.drug_class.schemas import (
        RegimenInput,
        DrugClassExtractionInput,
        SelectionInput,
        ExplicitExtractionInput,
        ConsolidationInput,
        ValidationInput as DrugClassValidationInput,
    )
    from src.temporal.activities.drug_class import (
        step1_regimen,
        step2_fetch_search_results,
        step2_extract_with_tavily,
        step2_extract_with_grounded,
        step3_selection,
        step4_explicit,
        step5_consolidation,
        validate_drug_class_activity,
    )
    # Indication activities + schemas
    from src.agents.indication.schemas import IndicationInput
    from src.temporal.activities.indication import (
        extract_indication,
        validate_indication,
    )


# =============================================================================
# WORKFLOW DEFINITION
# =============================================================================

@workflow.defn(name="AbstractExtractionWorkflow")
class AbstractExtractionWorkflow:
    """Entity workflow for a single session+entity extraction.

    Stays alive until the pipeline succeeds or the admin explicitly aborts.
    Uses Temporal signals for retry/abort and event history for state.
    """

    def __init__(self) -> None:
        self._output: Optional[AbstractExtractionOutput] = None
        self._current_status: str = "pending"
        self._retry_requested: bool = False
        self._abort_requested: bool = False
        self._current_entity: str = ""

    # =========================================================================
    # SIGNALS
    # =========================================================================

    @workflow.signal
    async def retry(self) -> None:
        """Signal from admin portal to retry the failed pipeline."""
        self._retry_requested = True

    @workflow.signal
    async def abort(self) -> None:
        """Signal from admin portal to abort this workflow."""
        self._abort_requested = True

    # =========================================================================
    # QUERIES
    # =========================================================================

    @workflow.query
    def status(self) -> str:
        """Return the current workflow status string."""
        return self._current_status

    @workflow.query
    def get_output(self) -> Optional[AbstractExtractionOutput]:
        return self._output

    # =========================================================================
    # MAIN RUN
    # =========================================================================

    @workflow.run
    async def run(self, input: AbstractExtractionInput) -> AbstractExtractionOutput:
        """Execute the entity extraction pipeline with pause/resume on failure."""
        self._output = AbstractExtractionOutput(abstract_id=input.abstract_id)

        workflow.logger.info(
            f"Starting entity workflow for abstract {input.abstract_id} "
            f"(entity: {input.entity}, batch: {input.batch_id})"
        )

        while True:
            self._current_status = "running"

            try:
                if input.entity == "drug":
                    self._current_entity = "drug"
                    await self._update_progress(input, "drug", "running")
                    await self._run_drug_pipeline(input)
                    await self._update_progress(input, "drug", "success")

                    self._current_entity = "drug_class"
                    primary_drugs = self._output.drug.extraction.get("primary_drugs", [])
                    if primary_drugs:
                        await self._update_progress(input, "drug_class", "running")
                        await self._run_drug_class_pipeline(input, primary_drugs)
                        await self._update_progress(input, "drug_class", "success")
                    else:
                        workflow.logger.info(
                            f"No primary drugs for {input.abstract_id}, "
                            "skipping drug class pipeline"
                        )
                        await self._update_progress(input, "drug_class", "success")

                elif input.entity == "indication":
                    self._current_entity = "indication"
                    await self._update_progress(input, "indication", "running")
                    await self._run_indication_pipeline(input)
                    await self._update_progress(input, "indication", "success")

                else:
                    raise ValueError(f"Unknown entity type: {input.entity}")

                # Pipeline completed successfully
                self._output.completed = True
                self._current_status = "success"
                workflow.logger.info(
                    f"Entity workflow completed for {input.abstract_id} "
                    f"(entity: {input.entity})"
                )
                return self._output

            except asyncio.CancelledError:
                # Temporal cancellation (abort while activity is running)
                self._current_status = "aborted"
                self._update_progress_on_failure(input)
                raise

            except Exception as e:
                workflow.logger.error(
                    f"Pipeline failed for {input.abstract_id} "
                    f"(entity: {input.entity}): {e}"
                )
                self._output.errors.append(str(e))
                self._current_status = "failed"
                self._update_progress_on_failure(input)

                # Pause — wait for admin to send retry or abort signal
                workflow.logger.info(
                    f"Workflow paused for {input.abstract_id}, "
                    "waiting for retry or abort signal"
                )
                self._retry_requested = False
                self._abort_requested = False
                await workflow.wait_condition(
                    lambda: self._retry_requested or self._abort_requested
                )

                if self._abort_requested:
                    self._current_status = "aborted"
                    workflow.logger.info(
                        f"Abort signal received for {input.abstract_id}"
                    )
                    return self._output

                # Retry signal received — clear errors and loop back.
                # Temporal replay will return cached results for completed
                # activities, so only the failed step re-executes.
                workflow.logger.info(
                    f"Retry signal received for {input.abstract_id}, "
                    "resuming pipeline"
                )
                self._retry_requested = False
                self._output.errors.clear()

    # =========================================================================
    # PROGRESS HELPERS
    # =========================================================================

    async def _update_progress(
        self,
        input: AbstractExtractionInput,
        entity: str,
        status: str,
    ) -> None:
        """Update extraction progress in SQL via the stub activity."""
        if not input.batch_id:
            return
        try:
            await workflow.execute_activity(
                update_extraction_progress,
                args=[
                    input.batch_id,
                    input.congress_id,
                    int(input.abstract_id),
                    entity,
                    status,
                ],
                task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
                start_to_close_timeout=Timeouts.ENTITY_MAPPING_PROGRESS,
                retry_policy=RetryPolicies.ENTITY_MAPPING_PROGRESS,
            )
        except Exception as e:
            workflow.logger.warning(
                f"Failed to update progress for {input.abstract_id}: {e}"
            )

    def _update_progress_on_failure(self, input: AbstractExtractionInput) -> None:
        """Best-effort progress update on failure (fire-and-forget).

        Uses _current_entity (set before each sub-pipeline) to mark only
        the entity that actually failed, avoiding overwriting an earlier
        success for a different sub-entity.
        """
        entity = self._current_entity or input.entity
        if not input.batch_id or not entity:
            return
        workflow.start_activity(
            update_extraction_progress,
            args=[input.batch_id, input.congress_id, int(input.abstract_id), entity, "failed"],
            task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
            start_to_close_timeout=Timeouts.ENTITY_MAPPING_PROGRESS,
            retry_policy=RetryPolicies.ENTITY_MAPPING_PROGRESS,
        )

    # =========================================================================
    # RESULT STORAGE HELPER
    # =========================================================================

    async def _save_result(
        self,
        input: AbstractExtractionInput,
        step_name: str,
        data: dict,
    ) -> None:
        """Save a step result to GCS for download from the admin portal."""
        if not input.storage_path:
            return
        await workflow.execute_activity(
            save_step_output,
            args=[input.storage_path, input.batch_id, input.abstract_id, step_name, data],
            task_queue=TaskQueues.RESULT_STORAGE,
            start_to_close_timeout=Timeouts.RESULT_STORAGE,
            retry_policy=RetryPolicies.RESULT_STORAGE,
        )

    # =========================================================================
    # TOKEN METADATA HELPER
    # =========================================================================

    @staticmethod
    def _extract_token_metadata(result: dict) -> tuple[dict | None, int]:
        """Extract and remove token metadata from an activity result dict.

        Activities embed _token_usage and _llm_calls in their output.
        This strips them before saving so only business data is persisted.
        """
        token_usage = result.pop("_token_usage", None)
        llm_calls = result.pop("_llm_calls", 1)
        return token_usage, llm_calls

    # =========================================================================
    # DRUG PIPELINE
    # =========================================================================

    async def _run_drug_pipeline(self, input: AbstractExtractionInput) -> None:
        """Run drug extraction + validation."""
        workflow.logger.info(f"Running drug pipeline for abstract {input.abstract_id}")

        # Extraction
        extraction = await workflow.execute_activity(
            extract_drugs,
            DrugInput(
                abstract_id=input.abstract_id,
                abstract_title=input.abstract_title,
            ),
            task_queue=TaskQueues.DRUG,
            start_to_close_timeout=Timeouts.FAST_LLM,
            retry_policy=RetryPolicies.FAST_LLM,
        )
        self._extract_token_metadata(extraction)
        await self._save_result(input, "drug_extraction", extraction)
        self._output.drug.extraction = extraction

        # Validation
        validation = await workflow.execute_activity(
            validate_drugs,
            DrugValidationInput(
                abstract_id=input.abstract_id,
                abstract_title=input.abstract_title,
                extraction_result=extraction,
            ),
            task_queue=TaskQueues.DRUG,
            start_to_close_timeout=Timeouts.FAST_LLM,
            retry_policy=RetryPolicies.FAST_LLM,
        )
        self._extract_token_metadata(validation)
        await self._save_result(input, "drug_validation", validation)
        self._output.drug.validation = validation

        workflow.logger.info(f"Drug pipeline completed for {input.abstract_id}")

    # =========================================================================
    # INDICATION PIPELINE
    # =========================================================================

    async def _run_indication_pipeline(self, input: AbstractExtractionInput) -> None:
        """Run indication extraction + validation."""
        workflow.logger.info(
            f"Running indication pipeline for abstract {input.abstract_id}"
        )

        indication_input = IndicationInput(
            abstract_id=input.abstract_id,
            abstract_title=input.abstract_title,
            session_title=input.session_title,
            rules_file_path=input.rules_file_path,
        )

        # Extraction
        extraction = await workflow.execute_activity(
            extract_indication,
            indication_input,
            task_queue=TaskQueues.INDICATION_EXTRACTION,
            start_to_close_timeout=Timeouts.FAST_LLM,
            retry_policy=RetryPolicies.FAST_LLM,
        )
        self._extract_token_metadata(extraction)
        await self._save_result(input, "indication_extraction", extraction)
        self._output.indication.extraction = extraction

        # Validation (slow LLM - multi-arg activity)
        validation = await workflow.execute_activity(
            validate_indication,
            args=[indication_input, extraction],
            task_queue=TaskQueues.INDICATION_VALIDATION,
            start_to_close_timeout=Timeouts.SLOW_LLM,
            retry_policy=RetryPolicies.SLOW_LLM,
        )
        self._extract_token_metadata(validation)
        await self._save_result(input, "indication_validation", validation)
        self._output.indication.validation = validation

        workflow.logger.info(
            f"Indication pipeline completed for {input.abstract_id}"
        )

    # =========================================================================
    # DRUG CLASS PIPELINE
    # =========================================================================

    async def _run_drug_class_pipeline(
        self, input: AbstractExtractionInput, primary_drugs: list[str]
    ) -> None:
        """Run the full drug class pipeline (steps 1-5 + validation)."""
        workflow.logger.info(
            f"Running drug class pipeline for abstract {input.abstract_id} "
            f"with {len(primary_drugs)} drugs"
        )

        # ---- Steps 1-3 (per-drug loops) ----
        steps1_3_data = await self._run_drug_class_steps1_3(input, primary_drugs)

        drug_errors = [
            d["error"] for d in steps1_3_data.get("drug_results", [])
            if d.get("error")
        ]
        if drug_errors:
            error_msg = f"Drug class steps 1-3 errors: {drug_errors}"
            self._output.errors.append(error_msg)
            raise RuntimeError(error_msg)

        # Strip aggregated token metadata before saving
        for key in list(steps1_3_data.keys()):
            if key.startswith("_step"):
                steps1_3_data.pop(key)

        await self._save_result(input, "drug_class_steps1_3", steps1_3_data)

        self._output.drug_class.drug_results = steps1_3_data.get("drug_results", [])
        all_drug_selections = steps1_3_data.get("drug_selections", [])
        all_extraction_results = steps1_3_data.get("extraction_results", {})

        # ---- Step 4: Explicit extraction from title ----
        step4_result = await workflow.execute_activity(
            step4_explicit,
            ExplicitExtractionInput(
                abstract_id=input.abstract_id,
                abstract_title=input.abstract_title,
            ),
            task_queue=TaskQueues.DRUG_CLASS,
            start_to_close_timeout=Timeouts.FAST_LLM,
            retry_policy=RetryPolicies.FAST_LLM,
        )
        self._extract_token_metadata(step4_result)
        await self._save_result(input, "drug_class_step4", step4_result)
        self._output.drug_class.explicit_classes = step4_result.get(
            "explicit_drug_classes", []
        )

        # ---- Step 5: Consolidation ----
        explicit = self._output.drug_class.explicit_classes
        step5_output = None
        if explicit and explicit != ["NA"]:
            step5_result = await workflow.execute_activity(
                step5_consolidation,
                ConsolidationInput(
                    abstract_id=input.abstract_id,
                    abstract_title=input.abstract_title,
                    explicit_drug_classes=explicit,
                    drug_selections=all_drug_selections,
                ),
                task_queue=TaskQueues.DRUG_CLASS,
                start_to_close_timeout=Timeouts.FAST_LLM,
                retry_policy=RetryPolicies.FAST_LLM,
            )
            self._extract_token_metadata(step5_result)
            await self._save_result(input, "drug_class_step5", step5_result)
            step5_output = step5_result
            self._output.drug_class.refined_explicit_classes = (
                step5_result.get("refined_explicit_classes", explicit)
            )
        else:
            self._output.drug_class.refined_explicit_classes = explicit

        # ---- Step 6: Validation (per component) ----
        validation_data = await self._run_drug_class_validation(
            input,
            all_extraction_results,
            drug_selections=all_drug_selections,
            step4_output=step4_result,
            step5_output=step5_output,
        )
        self._extract_token_metadata(validation_data)
        await self._save_result(input, "drug_class_validation", validation_data)
        self._output.drug_class.validation_results = validation_data.get("results", [])

        if validation_data.get("errors"):
            self._output.errors.extend(validation_data["errors"])

        workflow.logger.info(
            f"Drug class pipeline completed for {input.abstract_id}"
        )

    async def _run_drug_class_steps1_3(
        self, input: AbstractExtractionInput, primary_drugs: list[str],
    ) -> dict:
        """Run steps 1-3 for all primary drugs (per-drug loops).

        Returns dict with drug_results, drug_selections, extraction_results.
        """
        drug_results = []
        all_drug_selections = []
        all_extraction_results = {}

        for drug in primary_drugs:
            drug_data = {
                "drug": drug, "components": [],
                "extractions": {}, "selections": {},
            }
            try:
                # Step 1: Regimen identification
                workflow.logger.info(
                    f"Step 1 - Regimen for drug '{drug}' in {input.abstract_id}"
                )
                step1_result = await workflow.execute_activity(
                    step1_regimen,
                    RegimenInput(
                        abstract_id=input.abstract_id,
                        abstract_title=input.abstract_title,
                        drug=drug,
                    ),
                    task_queue=TaskQueues.DRUG_CLASS,
                    start_to_close_timeout=Timeouts.FAST_LLM,
                    retry_policy=RetryPolicies.FAST_LLM,
                )
                components = step1_result.get("_result", [drug])
                drug_data["components"] = components

                # Steps 2-3: For each component
                for component in components:
                    # Step 2a: Fetch search results
                    search_result = await workflow.execute_activity(
                        step2_fetch_search_results,
                        args=[component, input.firms, input.storage_path],
                        task_queue=TaskQueues.DRUG_CLASS,
                        start_to_close_timeout=Timeouts.SEARCH,
                        retry_policy=RetryPolicies.SEARCH,
                    )

                    # Step 2b: Extract with Tavily
                    ext_input = DrugClassExtractionInput(
                        abstract_id=input.abstract_id,
                        abstract_title=input.abstract_title,
                        drug=component,
                        full_abstract=input.full_abstract,
                        firms=input.firms,
                        drug_class_results=search_result.get("drug_class_results", []),
                        firm_search_results=search_result.get("firm_search_results", []),
                    )
                    extraction_result = await workflow.execute_activity(
                        step2_extract_with_tavily,
                        ext_input,
                        task_queue=TaskQueues.DRUG_CLASS,
                        start_to_close_timeout=Timeouts.FAST_LLM,
                        retry_policy=RetryPolicies.FAST_LLM,
                    )
                    self._extract_token_metadata(extraction_result)

                    # Fallback to grounded search if Tavily returns NA
                    drug_classes = extraction_result.get("drug_classes", [])
                    if not drug_classes or drug_classes == ["NA"]:
                        workflow.logger.info(
                            f"Tavily returned NA for {component}, trying grounded search"
                        )
                        extraction_result = await workflow.execute_activity(
                            step2_extract_with_grounded,
                            ext_input,
                            task_queue=TaskQueues.DRUG_CLASS,
                            start_to_close_timeout=Timeouts.FAST_LLM,
                            retry_policy=RetryPolicies.FAST_LLM,
                        )
                        self._extract_token_metadata(extraction_result)

                    drug_data["extractions"][component] = extraction_result
                    all_extraction_results[component] = extraction_result

                    # Step 3: Selection (if extraction has details)
                    extraction_details = extraction_result.get("extraction_details", [])
                    if extraction_details:
                        selection_result = await workflow.execute_activity(
                            step3_selection,
                            SelectionInput(
                                abstract_id=input.abstract_id,
                                drug_name=component,
                                extraction_details=extraction_details,
                            ),
                            task_queue=TaskQueues.DRUG_CLASS,
                            start_to_close_timeout=Timeouts.FAST_LLM,
                            retry_policy=RetryPolicies.FAST_LLM,
                        )
                        self._extract_token_metadata(selection_result)

                        drug_data["selections"][component] = selection_result
                        all_drug_selections.append({
                            "drug_name": component,
                            "selected_classes": selection_result.get(
                                "selected_drug_classes", []
                            ),
                        })

            except Exception as e:
                workflow.logger.error(
                    f"Drug class steps 1-3 error for drug '{drug}': {e}"
                )
                drug_data["error"] = str(e)

            drug_results.append(drug_data)

        return {
            "drug_results": drug_results,
            "drug_selections": all_drug_selections,
            "extraction_results": all_extraction_results,
        }

    async def _run_drug_class_validation(
        self,
        input: AbstractExtractionInput,
        extraction_results: dict[str, dict],
        drug_selections: list[dict] | None = None,
        step4_output: dict | None = None,
        step5_output: dict | None = None,
    ) -> dict:
        """Run validation for each drug component."""
        explicit_drug_classes = {}
        if step4_output:
            explicit_drug_classes = {
                "drug_classes": step4_output.get("explicit_drug_classes", []),
                "reasoning": step4_output.get("reasoning", ""),
            }

        refined_explicit_drug_classes = {}
        if step5_output:
            refined_explicit_drug_classes = {
                "drug_classes": step5_output.get("refined_explicit_classes", []),
                "removed_classes": step5_output.get("removed_classes", []),
                "reasoning": step5_output.get("reasoning", ""),
            }

        results = []
        errors = []

        for component, extraction_result in extraction_results.items():
            drug_classes = extraction_result.get("drug_classes", [])
            if not drug_classes or drug_classes == ["NA"]:
                continue
            try:
                search_result = await workflow.execute_activity(
                    step2_fetch_search_results,
                    args=[component, input.firms, input.storage_path],
                    task_queue=TaskQueues.DRUG_CLASS,
                    start_to_close_timeout=Timeouts.SEARCH,
                    retry_policy=RetryPolicies.SEARCH,
                )

                validation_result = await workflow.execute_activity(
                    validate_drug_class_activity,
                    DrugClassValidationInput(
                        abstract_id=input.abstract_id,
                        drug_name=component,
                        abstract_title=input.abstract_title,
                        full_abstract=input.full_abstract,
                        search_results=search_result.get("drug_class_results", []),
                        extraction_result=extraction_result,
                        drug_selections=drug_selections or [],
                        explicit_drug_classes=explicit_drug_classes,
                        refined_explicit_drug_classes=refined_explicit_drug_classes,
                    ),
                    task_queue=TaskQueues.DRUG_CLASS,
                    start_to_close_timeout=Timeouts.FAST_LLM,
                    retry_policy=RetryPolicies.FAST_LLM,
                )
                self._extract_token_metadata(validation_result)
                results.append({
                    "drug_name": component, "validation": validation_result,
                })
            except Exception as e:
                workflow.logger.error(f"Validation failed for drug '{component}': {e}")
                errors.append(f"Validation error for {component}: {e}")

        return {"results": results, "errors": errors}
