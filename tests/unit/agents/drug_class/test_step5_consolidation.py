"""Unit tests for drug class step 5: consolidation."""
import pytest
from unittest.mock import MagicMock, patch

from src.agents.drug_class.step5_consolidation import consolidate_drug_classes
from src.agents.drug_class.schemas import (
    ConsolidationInput,
    ConsolidationLLMResponse,
    RefinedExplicitClasses,
    RemovedClassInfo,
    DrugClassExtractionError,
)


@pytest.fixture
def sample_consolidation_input():
    """Sample consolidation input with both explicit classes and drug selections."""
    return ConsolidationInput(
        abstract_id=123,
        abstract_title="Study of PD-1 Inhibitors in NSCLC",
        congress_id=1,
        batch_id=2,
        explicit_drug_classes=["PD-1 Inhibitor", "Immunotherapy", "Monoclonal Antibody"],
        drug_selections=[
            {
                "drug_name": "Pembrolizumab",
                "selected_drug_classes": ["PD-1 Inhibitor"],
            }
        ],
    )


@pytest.fixture
def sample_empty_explicit_input():
    """Sample input with no explicit classes."""
    return ConsolidationInput(
        abstract_id=456,
        abstract_title="Study of Pembrolizumab in NSCLC",
        congress_id=1,
        batch_id=2,
        explicit_drug_classes=["NA"],
        drug_selections=[
            {
                "drug_name": "Pembrolizumab",
                "selected_drug_classes": ["PD-1 Inhibitor"],
            }
        ],
    )


@pytest.fixture
def sample_no_selections_input():
    """Sample input with explicit classes but no drug selections."""
    return ConsolidationInput(
        abstract_id=789,
        abstract_title="Study of PD-1 Inhibitors",
        congress_id=1,
        batch_id=2,
        explicit_drug_classes=["PD-1 Inhibitor", "Immunotherapy"],
        drug_selections=[],
    )


@pytest.fixture
def mock_consolidation_response():
    """Mock LLM response for consolidation."""
    return ConsolidationLLMResponse(
        reasoning="Removed duplicates and parent classes. PD-1 Inhibitor is already captured in drug selections.",
        refined_explicit_drug_classes=RefinedExplicitClasses(
            drug_classes=["Immunotherapy"],
            removed_classes=[
                RemovedClassInfo(class_name="PD-1 Inhibitor", reason="Duplicate - already in drug selections"),
                RemovedClassInfo(class_name="Monoclonal Antibody", reason="Parent class - more specific class available"),
            ],
        ),
    )


@pytest.fixture
def mock_no_removal_response():
    """Mock LLM response with no classes removed."""
    return ConsolidationLLMResponse(
        reasoning="All explicit classes are unique and specific.",
        refined_explicit_drug_classes=RefinedExplicitClasses(
            drug_classes=["PD-1 Inhibitor", "Immunotherapy"],
            removed_classes=[],
        ),
    )


@pytest.fixture
def mock_all_removed_response():
    """Mock LLM response where all classes were removed."""
    return ConsolidationLLMResponse(
        reasoning="All explicit classes were duplicates of drug-specific selections.",
        refined_explicit_drug_classes=RefinedExplicitClasses(
            drug_classes=[],
            removed_classes=[
                RemovedClassInfo(class_name="PD-1 Inhibitor", reason="Duplicate"),
            ],
        ),
    )


@pytest.mark.unit
class TestConsolidation:
    """Test consolidation logic."""

    def test_consolidate_success(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test successful consolidation."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(sample_consolidation_input)

            # Verify
            assert result.refined_explicit_classes == ["Immunotherapy"]
            assert result.removed_classes == ["PD-1 Inhibitor", "Monoclonal Antibody"]
            assert "Removed duplicates and parent classes" in result.reasoning
            mock_llm.with_structured_output.assert_called_once_with(ConsolidationLLMResponse)

    def test_consolidate_empty_explicit_classes(
        self,
        sample_empty_explicit_input,
        mock_settings,
    ):
        """Test consolidation with empty explicit classes (no LLM call)."""
        # No mocks needed - should not call LLM
        result = consolidate_drug_classes(sample_empty_explicit_input)

        # Verify early return without LLM call
        assert result.refined_explicit_classes == ["NA"]
        assert result.removed_classes == []
        assert result.reasoning == "No explicit drug classes to consolidate."

    def test_consolidate_no_drug_selections(
        self,
        sample_no_selections_input,
        mock_settings,
    ):
        """Test consolidation with no drug selections to compare against."""
        # No mocks needed - should not call LLM
        result = consolidate_drug_classes(sample_no_selections_input)

        # Verify early return without LLM call
        assert result.refined_explicit_classes == ["PD-1 Inhibitor", "Immunotherapy"]
        assert result.removed_classes == []
        assert result.reasoning == "No drug selections to compare against."

    def test_consolidate_no_removal(
        self,
        sample_consolidation_input,
        mock_no_removal_response,
        mock_settings,
    ):
        """Test consolidation when no classes are removed."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_no_removal_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(sample_consolidation_input)

            assert result.refined_explicit_classes == ["PD-1 Inhibitor", "Immunotherapy"]
            assert result.removed_classes == []

    def test_consolidate_all_removed(
        self,
        sample_consolidation_input,
        mock_all_removed_response,
        mock_settings,
    ):
        """Test consolidation when all classes are removed."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_all_removed_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(sample_consolidation_input)

            # Should return NA when all classes removed
            assert result.refined_explicit_classes == ["NA"]
            assert result.removed_classes == ["PD-1 Inhibitor"]

    def test_consolidate_json_formatting(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test that input data is formatted as JSON for LLM."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            consolidate_drug_classes(sample_consolidation_input)

            # Verify invoke was called
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # Check that input message contains JSON-formatted data
            input_message = messages[2].content  # Third message is input
            assert "PD-1 Inhibitor" in input_message
            assert "Pembrolizumab" in input_message
            # Should contain JSON structure
            assert "{" in input_message and "}" in input_message

    def test_consolidate_model_configuration(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test that consolidation model configuration is used."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step5_consolidation.config') as mock_config:

            mock_config.CONSOLIDATION_MODEL = "gpt-4-turbo"
            mock_config.CONSOLIDATION_TEMPERATURE = 0.2
            mock_config.CONSOLIDATION_MAX_TOKENS = 800

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(sample_consolidation_input)

            # Verify model configuration was used
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.model == "gpt-4-turbo"
            assert llm_config.temperature == 0.2
            assert llm_config.max_tokens == 800
            assert result.refined_explicit_classes == ["Immunotherapy"]

    def test_consolidate_timeout_configuration(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test that LLM is configured with correct timeout."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            consolidate_drug_classes(sample_consolidation_input)

            # Verify LLM was created with 120s timeout
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.timeout == 120

    def test_consolidate_llm_error(
        self,
        sample_consolidation_input,
        mock_settings,
    ):
        """Test consolidation when LLM raises an error."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.side_effect = Exception("LLM API error")
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugClassExtractionError, match="Consolidation failed"):
                consolidate_drug_classes(sample_consolidation_input)

    def test_consolidate_with_callbacks(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test consolidation with custom callbacks."""
        mock_llm = MagicMock()
        mock_callback = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(sample_consolidation_input, callbacks=[mock_callback])

            # Verify callback was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert invoke_call_args[1]['config']['callbacks'] == [mock_callback]
            assert result.refined_explicit_classes == ["Immunotherapy"]

    def test_consolidate_with_langfuse(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test consolidation with Langfuse enabled."""
        mock_llm = MagicMock()
        mock_langfuse_client = MagicMock()
        mock_langfuse_handler = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug_class.step5_consolidation.get_client', return_value=mock_langfuse_client), \
             patch('src.agents.drug_class.step5_consolidation.CallbackHandler', return_value=mock_langfuse_handler):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(sample_consolidation_input)

            # Verify Langfuse trace was updated
            mock_langfuse_client.update_current_trace.assert_called_once()
            trace_call_args = mock_langfuse_client.update_current_trace.call_args
            tags = trace_call_args[1]['tags']
            assert f"abstract_id:{sample_consolidation_input.abstract_id}" in tags
            assert "explicit_classes_count:3" in tags
            assert "drug_selections_count:1" in tags

            # Verify Langfuse generation was updated
            mock_langfuse_client.update_current_generation.assert_called_once()
            gen_call_args = mock_langfuse_client.update_current_generation.call_args
            metadata = gen_call_args[1]['metadata']
            assert metadata['explicit_classes_count'] == 3
            assert metadata['drug_selections_count'] == 1

            # Verify Langfuse handler was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert mock_langfuse_handler in invoke_call_args[1]['config']['callbacks']

            assert result.refined_explicit_classes == ["Immunotherapy"]

    def test_consolidate_prompt_caching_enabled(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test consolidation with prompt caching enabled."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step5_consolidation.config') as mock_config:

            mock_config.ENABLE_PROMPT_CACHING = True
            mock_config.CONSOLIDATION_MODEL = "gpt-4"
            mock_config.CONSOLIDATION_TEMPERATURE = 0.0
            mock_config.CONSOLIDATION_MAX_TOKENS = 1000

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            consolidate_drug_classes(sample_consolidation_input)

            # Verify messages were created with cache_control
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # System message (first) should have cache_control
            assert isinstance(messages[0].content, list)
            assert messages[0].content[0]['cache_control'] == {"type": "ephemeral"}

            # Rules message (second) should have cache_control
            assert isinstance(messages[1].content, list)
            assert messages[1].content[0]['cache_control'] == {"type": "ephemeral"}

    def test_consolidate_prompt_caching_disabled(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test consolidation with prompt caching disabled."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step5_consolidation.config') as mock_config:

            mock_config.ENABLE_PROMPT_CACHING = False
            mock_config.CONSOLIDATION_MODEL = "gpt-4"
            mock_config.CONSOLIDATION_TEMPERATURE = 0.0
            mock_config.CONSOLIDATION_MAX_TOKENS = 1000

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            consolidate_drug_classes(sample_consolidation_input)

            # Verify messages were created as plain text
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # System and rules messages should be plain strings
            assert isinstance(messages[0].content, str)
            assert isinstance(messages[1].content, str)

    def test_consolidate_input_template_substitution(
        self,
        sample_consolidation_input,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test that input template substitution works correctly."""
        mock_llm = MagicMock()
        custom_template = """# CONSOLIDATION
## Title: {abstract_title}
## Explicit: {explicit_drug_classes_json}
## Selections: {drug_selections_json}"""

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step5_consolidation.get_consolidation_prompt_parts') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt", custom_template, "Rules message", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            consolidate_drug_classes(sample_consolidation_input)

            # Verify template substitution occurred
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]
            input_message = messages[2].content

            assert "Title: Study of PD-1 Inhibitors in NSCLC" in input_message
            assert "PD-1 Inhibitor" in input_message
            assert "Pembrolizumab" in input_message

    def test_consolidate_empty_explicit_list(
        self,
        mock_settings,
    ):
        """Test consolidation with explicitly empty list."""
        input_empty = ConsolidationInput(
            abstract_id=999,
            abstract_title="Test Study",
            congress_id=1,
            batch_id=2,
            explicit_drug_classes=[],
            drug_selections=[{"drug_name": "Drug1", "selected_drug_classes": ["Class1"]}],
        )

        result = consolidate_drug_classes(input_empty)

        # Should return NA without LLM call
        assert result.refined_explicit_classes == ["NA"]
        assert result.removed_classes == []
        assert result.reasoning == "No explicit drug classes to consolidate."

    def test_consolidate_multiple_drug_selections(
        self,
        mock_consolidation_response,
        mock_settings,
    ):
        """Test consolidation with multiple drug selections."""
        input_multi = ConsolidationInput(
            abstract_id=888,
            abstract_title="Study of Combination Therapy",
            congress_id=1,
            batch_id=2,
            explicit_drug_classes=["PD-1 Inhibitor", "CTLA-4 Inhibitor", "Immunotherapy"],
            drug_selections=[
                {"drug_name": "Nivolumab", "selected_drug_classes": ["PD-1 Inhibitor"]},
                {"drug_name": "Ipilimumab", "selected_drug_classes": ["CTLA-4 Inhibitor"]},
            ],
        )

        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step5_consolidation.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step5_consolidation.settings', mock_settings), \
             patch('src.agents.drug_class.step5_consolidation.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_consolidation_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = consolidate_drug_classes(input_multi)

            # Verify both drug selections are in the input message
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]
            input_message = messages[2].content

            assert "Nivolumab" in input_message
            assert "Ipilimumab" in input_message
            assert result.refined_explicit_classes == ["Immunotherapy"]
