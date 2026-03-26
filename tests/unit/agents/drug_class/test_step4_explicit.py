"""Unit tests for drug class step 4: explicit extraction from title."""
import pytest
from unittest.mock import MagicMock, patch

from src.agents.drug_class.step4_explicit import extract_explicit_classes
from src.agents.drug_class.schemas import (
    ExplicitExtractionInput,
    ExplicitLLMResponse,
    ExplicitExtractionDetail,
    Step4Output,
    DrugClassExtractionError,
)


@pytest.fixture
def sample_explicit_input():
    """Sample explicit extraction input."""
    return ExplicitExtractionInput(
        abstract_id=123,
        abstract_title="Study of PD-1 Inhibitors in Advanced NSCLC",
        congress_id=1,
        batch_id=2,
    )


@pytest.fixture
def empty_title_input():
    """Input with empty title."""
    return ExplicitExtractionInput(
        abstract_id=456,
        abstract_title="",
        congress_id=1,
        batch_id=2,
    )


@pytest.fixture
def mock_explicit_response():
    """Mock LLM response with explicit drug classes."""
    detail = ExplicitExtractionDetail(
        drug_class="PD-1 Inhibitor",
        evidence="Title mentions 'PD-1 Inhibitors'",
        confidence_score=0.95,
    )
    return ExplicitLLMResponse(
        reasoning="The title explicitly mentions 'PD-1 Inhibitors'",
        drug_classes=["PD-1 Inhibitor"],
        extraction_details=[detail],
    )


@pytest.fixture
def mock_no_classes_response():
    """Mock LLM response with no drug classes found."""
    return ExplicitLLMResponse(
        reasoning="No explicit drug class mentions in the title",
        drug_classes=[],
        extraction_details=[],
    )


@pytest.mark.unit
class TestExplicitExtraction:
    """Test explicit drug class extraction from title."""

    def test_extract_explicit_classes_success(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test successful explicit extraction."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = (
                "System prompt for explicit extraction",
                "Input template: {abstract_title}",
                "Rules message",
                "v1.0"
            )

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = extract_explicit_classes(sample_explicit_input)

            assert isinstance(result, Step4Output)
            assert result.explicit_drug_classes == ["PD-1 Inhibitor"]
            assert len(result.extraction_details) == 1
            assert result.reasoning == "The title explicitly mentions 'PD-1 Inhibitors'"
            mock_llm.with_structured_output.assert_called_once_with(ExplicitLLMResponse)

    def test_extract_explicit_classes_empty_title(
        self,
        empty_title_input,
        mock_settings,
    ):
        """Test extraction with empty abstract title."""
        result = extract_explicit_classes(empty_title_input)

        # Should return NA without making LLM call
        assert result.explicit_drug_classes == ["NA"]
        assert result.reasoning == "Empty abstract title provided"

    def test_extract_explicit_classes_no_classes_found(
        self,
        sample_explicit_input,
        mock_no_classes_response,
        mock_settings,
    ):
        """Test extraction when no explicit classes are found."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = ("System", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_no_classes_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = extract_explicit_classes(sample_explicit_input)

            # Should return NA when no classes found
            assert result.explicit_drug_classes == ["NA"]
            assert result.reasoning == "No explicit drug class mentions in the title"

    def test_extract_explicit_classes_multiple_classes(
        self,
        mock_settings,
    ):
        """Test extraction with multiple explicit drug classes."""
        input_data = ExplicitExtractionInput(
            abstract_id=789,
            abstract_title="Comparison of PD-1 and CTLA-4 Inhibitors in Melanoma",
            congress_id=1,
            batch_id=2,
        )

        mock_llm = MagicMock()
        multi_response = ExplicitLLMResponse(
            reasoning="Title mentions two types of inhibitors",
            drug_classes=["PD-1 Inhibitor", "CTLA-4 Inhibitor"],
            extraction_details=[],
        )

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = ("System", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = multi_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = extract_explicit_classes(input_data)

            assert result.explicit_drug_classes == ["PD-1 Inhibitor", "CTLA-4 Inhibitor"]
            assert len(result.explicit_drug_classes) == 2

    def test_extract_explicit_classes_llm_error(
        self,
        sample_explicit_input,
        mock_settings,
    ):
        """Test extraction when LLM raises an error."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = ("System", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.side_effect = Exception("LLM API error")
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugClassExtractionError, match="Explicit extraction failed"):
                extract_explicit_classes(sample_explicit_input)

    def test_extract_explicit_classes_timeout_configuration(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test that LLM is configured with correct timeout."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = ("System", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            extract_explicit_classes(sample_explicit_input)

            # Verify LLM was created with 120s timeout
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.timeout == 120

    def test_extract_explicit_classes_with_callbacks(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test extraction with custom callbacks."""
        mock_llm = MagicMock()
        mock_callback = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = ("System", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = extract_explicit_classes(sample_explicit_input, callbacks=[mock_callback])

            # Verify callback was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert invoke_call_args[1]['config']['callbacks'] == [mock_callback]
            assert result.explicit_drug_classes == ["PD-1 Inhibitor"]

    def test_extract_explicit_classes_with_langfuse(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test extraction with Langfuse enabled."""
        mock_llm = MagicMock()
        mock_langfuse_client = MagicMock()
        mock_langfuse_handler = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug_class.step4_explicit.get_client', return_value=mock_langfuse_client), \
             patch('src.agents.drug_class.step4_explicit.CallbackHandler', return_value=mock_langfuse_handler), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt:

            mock_prompt.return_value = ("System", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = extract_explicit_classes(sample_explicit_input)

            # Verify Langfuse trace was updated
            mock_langfuse_client.update_current_trace.assert_called_once()
            trace_call_args = mock_langfuse_client.update_current_trace.call_args
            tags = trace_call_args[1]['tags']
            assert f"abstract_id:{sample_explicit_input.abstract_id}" in tags

            # Verify Langfuse handler was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert mock_langfuse_handler in invoke_call_args[1]['config']['callbacks']

            assert result.explicit_drug_classes == ["PD-1 Inhibitor"]

    def test_extract_explicit_classes_prompt_caching_enabled(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test that prompt caching is applied when enabled."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt, \
             patch('src.agents.drug_class.step4_explicit.config') as mock_config:

            mock_config.ENABLE_PROMPT_CACHING = True
            mock_config.EXPLICIT_MODEL = "gpt-4"
            mock_config.EXPLICIT_TEMPERATURE = 0.3
            mock_config.EXPLICIT_MAX_TOKENS = 500

            mock_prompt.return_value = ("System prompt", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            extract_explicit_classes(sample_explicit_input)

            # Verify messages were created with cache_control
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # System message should have cache_control
            assert isinstance(messages[0].content, list)
            assert messages[0].content[0]['cache_control'] == {"type": "ephemeral"}

            # Rules message should have cache_control
            assert isinstance(messages[1].content, list)
            assert messages[1].content[0]['cache_control'] == {"type": "ephemeral"}

    def test_extract_explicit_classes_prompt_caching_disabled(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test that messages are plain text when caching is disabled."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt, \
             patch('src.agents.drug_class.step4_explicit.config') as mock_config:

            mock_config.ENABLE_PROMPT_CACHING = False
            mock_config.EXPLICIT_MODEL = "gpt-4"
            mock_config.EXPLICIT_TEMPERATURE = 0.3
            mock_config.EXPLICIT_MAX_TOKENS = 500

            mock_prompt.return_value = ("System prompt", "Input: {abstract_title}", "Rules", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            extract_explicit_classes(sample_explicit_input)

            # Verify messages were created without cache_control
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]

            # Messages should have plain string content
            assert isinstance(messages[0].content, str)
            assert isinstance(messages[1].content, str)

    def test_extract_explicit_classes_input_template_substitution(
        self,
        sample_explicit_input,
        mock_explicit_response,
        mock_settings,
    ):
        """Test that input template is correctly substituted."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step4_explicit.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step4_explicit.settings', mock_settings), \
             patch('src.agents.drug_class.step4_explicit.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step4_explicit.get_explicit_extraction_prompt_parts') as mock_prompt, \
             patch('src.agents.drug_class.step4_explicit.config') as mock_config:

            mock_config.ENABLE_PROMPT_CACHING = False
            mock_config.EXPLICIT_MODEL = "gpt-4"
            mock_config.EXPLICIT_TEMPERATURE = 0.3
            mock_config.EXPLICIT_MAX_TOKENS = 500

            mock_prompt.return_value = (
                "System prompt",
                "Extract from: {abstract_title}",
                "Rules",
                "v1.0"
            )

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_explicit_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            extract_explicit_classes(sample_explicit_input)

            # Verify input message contains substituted title
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]
            input_message = messages[2]  # Third message is the input
            assert sample_explicit_input.abstract_title in input_message.content

    def test_extract_explicit_classes_whitespace_title(
        self,
        mock_settings,
    ):
        """Test extraction with whitespace-only title."""
        whitespace_input = ExplicitExtractionInput(
            abstract_id=999,
            abstract_title="   \t\n   ",
            congress_id=1,
            batch_id=2,
        )

        result = extract_explicit_classes(whitespace_input)

        # Should treat as empty
        assert result.explicit_drug_classes == ["NA"]
        assert result.reasoning == "Empty abstract title provided"
