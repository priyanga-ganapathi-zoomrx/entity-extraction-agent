"""Abstract Extraction Workflow - Temporal entity workflow for medical abstract processing.

Entity workflow pattern: one long-lived workflow per session+entity combination.

Two entity types:
  - entity="drug"       → Drug extraction + validation → Drug class pipeline + validation
  - entity="indication" → Indication extraction + validation

Pause/Resume:
  On pipeline failure, the workflow pauses via workflow.wait_condition and waits
  for a retry or abort signal from the admin portal.  On retry, the while loop
  restarts — each pipeline method checks self._output (which persists across
  loop iterations) to skip already-completed steps and only re-executes from
  the point of failure.  This is NOT Temporal replay; it is application-level
  skip logic using workflow instance state.

  Temporal replay (re-executing workflow code from the start using event
  history) only occurs when a worker restarts.  Within a single workflow
  execution, looping back and calling execute_activity again creates new
  activity invocations that will actually run.

GCS is used ONLY for storing downloadable result files, not for checkpointing.
SQL status is updated via the update_extraction_progress activity stub.
"""

import asyncio
from typing import Optional

from temporalio import workflow
from temporalio.exceptions import is_cancelled_exception

with workflow.unsafe.imports_passed_through():
    from src.temporal.config import (
        TaskQueues,
        Timeouts,
        RetryPolicies,
    )
    from src.temporal.schemas.workflow import (
        AbstractExtractionInput,
        AbstractExtractionOutput,
    )
    # Result storage activity
    from src.temporal.activities.result_storage import save_step_output
    # Extraction progress activity (SQL stub)
    from src.temporal.activities.extraction_progress import update_extraction_progress
    # Batch finalization
    from src.temporal.activities.check_and_finalize_batch import CheckAndFinalizeInput
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
        # Top-level pipeline completion (drug_pipeline, drug_class_pipeline, indication_pipeline)
        self._completed_steps: set[str] = set()
        # Drug class intermediate state — persists across retry loops.
        # Per-drug steps 1-3 results: drug_name → {components, extractions, selections}
        self._dc_per_drug_data: dict[str, dict] = {}
        self._dc_step4_result: Optional[dict] = None
        # None = pending, {} = evaluated but skipped (explicit was NA)
        self._dc_step5_output: Optional[dict] = None
        self._dc_validation_data: Optional[dict] = None

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

        try:
            await self._execute_pipeline(input)
            # Check if batch is done and finalize (update status, generate XLSX)
            await workflow.execute_activity(
                "check_and_finalize_batch",
                CheckAndFinalizeInput(
                    batch_id=input.batch_id, congress_id=input.congress_id
                ),
                task_queue=TaskQueues.RESULT_STORAGE,
                start_to_close_timeout=Timeouts.BATCH_FINALIZATION,
                retry_policy=RetryPolicies.BATCH_FINALIZATION,
            )
            return self._output
        except asyncio.CancelledError:
            self._current_status = "aborted"
            await self._shielded_abort_progress(input)
            raise

    async def _execute_pipeline(
        self, input: AbstractExtractionInput
    ) -> AbstractExtractionOutput:
        """Run the entity pipeline with retry loop and failure-pause logic."""
        while True:
            self._current_status = "running"

            try:
                if input.entity == "drug":
                    if "drug_pipeline" not in self._completed_steps:
                        self._current_entity = "drug"
                        await self._update_progress(input, "drug", "running")
                        await self._run_drug_pipeline(input)
                        await self._update_progress(input, "drug", "success")
                        self._completed_steps.add("drug_pipeline")
                    else:
                        workflow.logger.info(
                            f"Skipping already completed drug pipeline for {input.abstract_id}"
                        )

                    self._current_entity = "drug_class"
                    primary_drugs = self._output.drug.extraction.get("primary_drugs", [])
                    if primary_drugs:
                        if "drug_class_pipeline" not in self._completed_steps:
                            await self._update_progress(input, "drug_class", "running")
                            await self._run_drug_class_pipeline(input, primary_drugs)
                            await self._update_progress(input, "drug_class", "success")
                            self._completed_steps.add("drug_class_pipeline")
                        else:
                            workflow.logger.info(
                                f"Skipping already completed drug class pipeline for {input.abstract_id}"
                            )
                    else:
                        workflow.logger.info(
                            f"No primary drugs for {input.abstract_id}, "
                            "skipping drug class pipeline"
                        )
                        await self._update_progress(input, "drug_class", "success")

                elif input.entity == "indication":
                    if "indication_pipeline" not in self._completed_steps:
                        self._current_entity = "indication"
                        await self._update_progress(input, "indication", "running")
                        await self._run_indication_pipeline(input)
                        await self._update_progress(input, "indication", "success")
                        self._completed_steps.add("indication_pipeline")
                    else:
                        workflow.logger.info(
                            f"Skipping already completed indication pipeline for {input.abstract_id}"
                        )

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

            except Exception as e:
                # Activity cancellation arrives as ActivityError wrapping
                # temporalio.exceptions.CancelledError (an Exception subclass,
                # NOT asyncio.CancelledError).  Convert to asyncio.CancelledError
                # so run()'s handler catches it and runs shielded cleanup.
                if is_cancelled_exception(e):
                    raise asyncio.CancelledError() from e

                workflow.logger.error(
                    f"Pipeline failed for {input.abstract_id} "
                    f"(entity: {input.entity}): {e}"
                )
                self._output.errors.append(str(e))
                self._current_status = "failed"
                # Blocking await so DB is up-to-date before check_and_finalize
                entity = self._current_entity or input.entity
                await workflow.execute_activity(
                    update_extraction_progress,
                    args=[input.batch_id, input.congress_id, input.abstract_id, entity, "failed"],
                    task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
                    start_to_close_timeout=Timeouts.ENTITY_MAPPING_PROGRESS,
                    retry_policy=RetryPolicies.ENTITY_MAPPING_PROGRESS,
                )
                # Check if batch is done and finalize
                await workflow.execute_activity(
                    "check_and_finalize_batch",
                    CheckAndFinalizeInput(
                        batch_id=input.batch_id, congress_id=input.congress_id
                    ),
                    task_queue=TaskQueues.RESULT_STORAGE,
                    start_to_close_timeout=Timeouts.BATCH_FINALIZATION,
                    retry_policy=RetryPolicies.BATCH_FINALIZATION,
                )

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
                    entity = self._current_entity or input.entity
                    workflow.start_activity(
                        update_extraction_progress,
                        args=[
                            input.batch_id,
                            input.congress_id,
                            input.abstract_id,
                            entity,
                            "aborted",
                        ],
                        task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
                        start_to_close_timeout=Timeouts.ENTITY_MAPPING_PROGRESS,
                        retry_policy=RetryPolicies.ENTITY_MAPPING_PROGRESS,
                    )
                    # Defensive: check batch finalization (likely no-op since
                    # AP server already set batch status to 'aborted')
                    workflow.start_activity(
                        "check_and_finalize_batch",
                        CheckAndFinalizeInput(
                            batch_id=input.batch_id, congress_id=input.congress_id
                        ),
                        task_queue=TaskQueues.RESULT_STORAGE,
                        start_to_close_timeout=Timeouts.BATCH_FINALIZATION,
                        retry_policy=RetryPolicies.BATCH_FINALIZATION,
                    )
                    workflow.logger.info(
                        f"Abort signal received for {input.abstract_id}"
                    )
                    return self._output

                # Retry signal received — clear errors and loop back.
                workflow.logger.info(
                    f"Retry signal received for {input.abstract_id}, "
                    "resuming pipeline from failed step"
                )
                self._retry_requested = False
                self._output.errors.clear()

    async def _shielded_abort_progress(
        self, input: AbstractExtractionInput
    ) -> None:
        """Update DB to aborted status, shielded from cancellation.

        Used when workflow is cancelled; asyncio.shield ensures this activity
        runs even though the workflow is tearing down.  If it fails (DB down,
        timeout), we log and continue — workflow still terminates as CANCELLED.
        """
        try:
            await asyncio.shield(
                workflow.execute_activity(
                    update_extraction_progress,
                    args=[
                        input.batch_id,
                        input.congress_id,
                        input.abstract_id,
                        self._current_entity or input.entity,
                        "aborted",
                    ],
                    task_queue=TaskQueues.ENTITY_MAPPING_PROGRESS,
                    start_to_close_timeout=Timeouts.ENTITY_MAPPING_PROGRESS,
                    retry_policy=RetryPolicies.ENTITY_MAPPING_PROGRESS,
                )
            )
        except Exception:
            workflow.logger.warning(
                f"Failed to update abort status for {input.abstract_id}, "
                "workflow will still terminate as CANCELLED"
            )

    # =========================================================================
    # PROGRESS HELPERS
    # =========================================================================

    async def _update_progress(
        self,
        input: AbstractExtractionInput,
        entity: str,
        status: str,
    ) -> None:
        """Update extraction progress in SQL via the progress activity.

        No try/except — if this fails, the exception propagates and the
        workflow pauses, allowing the admin to fix the issue and retry.
        """
        await workflow.execute_activity(
            update_extraction_progress,
            args=[input.batch_id, input.congress_id, input.abstract_id, entity, status],
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
        entity: str,
        step_name: str,
        data: dict,
    ) -> None:
        """Save a step result to GCS for download from the admin portal.

        Path: congress/{cid}/batches/{bid}/{entity}/{sid}/{step_name}.json
        """
        await workflow.execute_activity(
            save_step_output,
            args=[input.congress_id, input.batch_id, entity, input.abstract_id, step_name, data],
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
        """Run drug extraction + validation, skipping already-completed steps."""
        workflow.logger.info(f"Running drug pipeline for abstract {input.abstract_id}")

        # Extraction
        if not self._output.drug.extraction:
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
            await self._save_result(input, "drug", "extraction", extraction)
            self._output.drug.extraction = extraction
        else:
            workflow.logger.info(f"Skipping completed drug extraction for {input.abstract_id}")

        # Validation
        if not self._output.drug.validation:
            validation = await workflow.execute_activity(
                validate_drugs,
                DrugValidationInput(
                    abstract_id=input.abstract_id,
                    abstract_title=input.abstract_title,
                    extraction_result=self._output.drug.extraction,
                ),
                task_queue=TaskQueues.DRUG,
                start_to_close_timeout=Timeouts.FAST_LLM,
                retry_policy=RetryPolicies.FAST_LLM,
            )
            self._extract_token_metadata(validation)
            await self._save_result(input, "drug", "validation", validation)
            self._output.drug.validation = validation
        else:
            workflow.logger.info(f"Skipping completed drug validation for {input.abstract_id}")

        workflow.logger.info(f"Drug pipeline completed for {input.abstract_id}")

    # =========================================================================
    # INDICATION PIPELINE
    # =========================================================================

    async def _run_indication_pipeline(self, input: AbstractExtractionInput) -> None:
        """Run indication extraction + validation, skipping already-completed steps."""
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
        if not self._output.indication.extraction:
            extraction = await workflow.execute_activity(
                extract_indication,
                indication_input,
                task_queue=TaskQueues.INDICATION_EXTRACTION,
                start_to_close_timeout=Timeouts.FAST_LLM,
                retry_policy=RetryPolicies.FAST_LLM,
            )
            self._extract_token_metadata(extraction)
            await self._save_result(input, "indication", "extraction", extraction)
            self._output.indication.extraction = extraction
        else:
            workflow.logger.info(
                f"Skipping completed indication extraction for {input.abstract_id}"
            )

        # Validation (slow LLM - multi-arg activity)
        if not self._output.indication.validation:
            validation = await workflow.execute_activity(
                validate_indication,
                args=[indication_input, self._output.indication.extraction],
                task_queue=TaskQueues.INDICATION_VALIDATION,
                start_to_close_timeout=Timeouts.SLOW_LLM,
                retry_policy=RetryPolicies.SLOW_LLM,
            )
            self._extract_token_metadata(validation)
            await self._save_result(input, "indication", "validation", validation)
            self._output.indication.validation = validation
        else:
            workflow.logger.info(
                f"Skipping completed indication validation for {input.abstract_id}"
            )

        workflow.logger.info(
            f"Indication pipeline completed for {input.abstract_id}"
        )

    # =========================================================================
    # DRUG CLASS PIPELINE
    # =========================================================================

    async def _run_drug_class_pipeline(
        self, input: AbstractExtractionInput, primary_drugs: list[str]
    ) -> None:
        """Run the full drug class pipeline (steps 1-5 + validation).

        Skip logic uses instance state presence checks:
          - Per-drug steps 1-3: self._dc_per_drug_data[drug] exists → skip
          - Steps 1-3 aggregate: self._output.drug_class.drug_results non-empty → skip save
          - Step 4: self._dc_step4_result is not None → skip
          - Step 5: self._dc_step5_output is not None → skip ({}=skipped, {data}=ran)
          - Validation: self._dc_validation_data is not None → skip
        """
        workflow.logger.info(
            f"Running drug class pipeline for abstract {input.abstract_id} "
            f"with {len(primary_drugs)} drugs"
        )

        # ---- Steps 1-3 (per-drug loops) ----
        await self._run_drug_class_steps1_3(input, primary_drugs)

        missing = [d for d in primary_drugs if d not in self._dc_per_drug_data]
        if missing:
            raise RuntimeError(f"Drug class steps 1-3 failed for drugs: {missing}")

        # Save aggregate output once all drugs complete
        if not self._output.drug_class.drug_results:
            steps1_3_data = self._build_steps1_3_output(primary_drugs)
            await self._save_result(input, "drug_class", "steps1_3", steps1_3_data)
            self._output.drug_class.drug_results = steps1_3_data["drug_results"]

        # ---- Step 4: Explicit extraction from title ----
        if self._dc_step4_result is None:
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
            await self._save_result(input, "drug_class", "step4", step4_result)
            self._output.drug_class.explicit_classes = step4_result.get(
                "explicit_drug_classes", []
            )
            self._dc_step4_result = step4_result
        else:
            workflow.logger.info(
                f"Skipping completed drug class step 4 for {input.abstract_id}"
            )

        # ---- Step 5: Consolidation ----
        if self._dc_step5_output is None:
            explicit = self._output.drug_class.explicit_classes
            if explicit and explicit != ["NA"]:
                drug_selections = self._get_drug_selections()
                step5_result = await workflow.execute_activity(
                    step5_consolidation,
                    ConsolidationInput(
                        abstract_id=input.abstract_id,
                        abstract_title=input.abstract_title,
                        explicit_drug_classes=explicit,
                        drug_selections=drug_selections,
                    ),
                    task_queue=TaskQueues.DRUG_CLASS,
                    start_to_close_timeout=Timeouts.FAST_LLM,
                    retry_policy=RetryPolicies.FAST_LLM,
                )
                self._extract_token_metadata(step5_result)
                await self._save_result(input, "drug_class", "step5", step5_result)
                self._dc_step5_output = step5_result
                self._output.drug_class.refined_explicit_classes = (
                    step5_result.get("refined_explicit_classes", explicit)
                )
            else:
                self._output.drug_class.refined_explicit_classes = explicit
                self._dc_step5_output = {}
        else:
            workflow.logger.info(
                f"Skipping completed drug class step 5 for {input.abstract_id}"
            )

        # ---- Step 6: Validation (per component) ----
        if self._dc_validation_data is None:
            self._dc_validation_data = await self._run_drug_class_validation(input)
            self._extract_token_metadata(self._dc_validation_data)
            await self._save_result(input, "drug_class", "validation", self._dc_validation_data)
            self._output.drug_class.validation_results = self._dc_validation_data.get("results", [])
        else:
            workflow.logger.info(
                f"Skipping completed drug class validation for {input.abstract_id}"
            )

        workflow.logger.info(
            f"Drug class pipeline completed for {input.abstract_id}"
        )

    async def _run_drug_class_steps1_3(
        self, input: AbstractExtractionInput, primary_drugs: list[str],
    ) -> None:
        """Run steps 1-3 for all primary drugs.

        Results are stored immediately in self._dc_per_drug_data[drug] as
        each drug completes.  On retry, drugs already in the dict are skipped.
        """
        for drug in primary_drugs:
            if drug in self._dc_per_drug_data:
                workflow.logger.info(f"Skipping completed steps 1-3 for drug '{drug}'")
                continue

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

            extractions: dict[str, dict] = {}
            selections: dict[str, dict] = {}

            for component in components:
                # Step 2a: Fetch search results
                search_result = await workflow.execute_activity(
                    step2_fetch_search_results,
                    args=[component, input.firms, input.congress_id, input.abstract_id],
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

                extractions[component] = extraction_result

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
                    selections[component] = selection_result

            # Store immediately — available even if the next drug fails
            self._dc_per_drug_data[drug] = {
                "components": components,
                "extractions": extractions,
                "selections": selections,
            }

    # =========================================================================
    # DRUG CLASS HELPERS
    # =========================================================================

    def _build_steps1_3_output(self, primary_drugs: list[str]) -> dict:
        """Build the aggregate steps 1-3 output dict from _dc_per_drug_data."""
        drug_results = []
        drug_selections = []
        extraction_results = {}

        for drug in primary_drugs:
            data = self._dc_per_drug_data[drug]
            drug_results.append({"drug": drug, **data})
            extraction_results.update(data.get("extractions", {}))
            for comp, sel in data.get("selections", {}).items():
                drug_selections.append({
                    "drug_name": comp,
                    "selected_classes": sel.get("selected_drug_classes", []),
                })

        return {
            "drug_results": drug_results,
            "drug_selections": drug_selections,
            "extraction_results": extraction_results,
        }

    def _get_drug_selections(self) -> list[dict]:
        """Derive drug_selections list from _dc_per_drug_data."""
        selections = []
        for data in self._dc_per_drug_data.values():
            for comp, sel in data.get("selections", {}).items():
                selections.append({
                    "drug_name": comp,
                    "selected_classes": sel.get("selected_drug_classes", []),
                })
        return selections

    def _get_extraction_results(self) -> dict[str, dict]:
        """Derive extraction_results dict from _dc_per_drug_data."""
        results = {}
        for data in self._dc_per_drug_data.values():
            results.update(data.get("extractions", {}))
        return results

    async def _run_drug_class_validation(
        self, input: AbstractExtractionInput,
    ) -> dict:
        """Run validation for each drug component.

        Derives all inputs from instance state (_dc_per_drug_data,
        _dc_step4_result, _dc_step5_output).
        """
        extraction_results = self._get_extraction_results()
        drug_selections = self._get_drug_selections()

        explicit_drug_classes = {}
        if self._dc_step4_result:
            explicit_drug_classes = {
                "drug_classes": self._dc_step4_result.get("explicit_drug_classes", []),
                "reasoning": self._dc_step4_result.get("reasoning", ""),
            }

        refined_explicit_drug_classes = {}
        if self._dc_step5_output:
            refined_explicit_drug_classes = {
                "drug_classes": self._dc_step5_output.get("refined_explicit_classes", []),
                "removed_classes": self._dc_step5_output.get("removed_classes", []),
                "reasoning": self._dc_step5_output.get("reasoning", ""),
            }

        results = []

        for component, extraction_result in extraction_results.items():
            drug_classes = extraction_result.get("drug_classes", [])
            if not drug_classes or drug_classes == ["NA"]:
                continue

            search_result = await workflow.execute_activity(
                step2_fetch_search_results,
                args=[component, input.firms, input.congress_id, input.abstract_id],
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
                    drug_selections=drug_selections,
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

        return {"results": results}
