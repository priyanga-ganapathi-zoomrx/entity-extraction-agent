"""Unit tests for drug class step 1: regimen identification."""
import pytest
from unittest.mock import MagicMock, patch

from src.agents.drug_class.step1_regimen import identify_regimen
from src.agents.drug_class.schemas import (
    RegimenInput,
    RegimenLLMResponse,
    DrugClassExtractionError,
)


@pytest.fixture
def sample_regimen_input():
    """Sample regimen identification input."""
    return RegimenInput(
        abstract_id=123,
        abstract_title="Study of FOLFOX in Colorectal Cancer",
        drug="FOLFOX",
        congress_id=1,
        batch_id=2,
    )


@pytest.fixture
def sample_single_drug_input():
    """Sample single drug input (not a regimen)."""
    return RegimenInput(
        abstract_id=456,
        abstract_title="Study of Pembrolizumab in NSCLC",
        drug="Pembrolizumab",
        congress_id=1,
        batch_id=2,
    )


@pytest.fixture
def mock_regimen_response():
    """Mock LLM response for a regimen."""
    return RegimenLLMResponse(
        components=["Fluorouracil", "Leucovorin", "Oxaliplatin"]
    )


@pytest.fixture
def mock_single_drug_response():
    """Mock LLM response for a single drug."""
    return RegimenLLMResponse(
        components=["Pembrolizumab"]
    )


@pytest.mark.unit
class TestRegimenIdentification:
    """Test regimen identification logic."""

    def test_identify_regimen_success(
        self,
        sample_regimen_input,
        mock_regimen_response,
        mock_settings,
    ):
        """Test successful regimen identification."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_regimen_response

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            # Configure mock LLM with structured output
            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_regimen_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            # Execute
            result = identify_regimen(sample_regimen_input)

            # Verify
            assert result == ["Fluorouracil", "Leucovorin", "Oxaliplatin"]
            mock_llm.with_structured_output.assert_called_once_with(RegimenLLMResponse)
            mock_llm_with_structured.invoke.assert_called_once()

    def test_identify_regimen_single_drug(
        self,
        sample_single_drug_input,
        mock_single_drug_response,
        mock_settings,
    ):
        """Test regimen identification for a single drug (not a regimen)."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_single_drug_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(sample_single_drug_input)

            assert result == ["Pembrolizumab"]

    def test_identify_regimen_empty_components_fallback(
        self,
        sample_regimen_input,
        mock_settings,
    ):
        """Test regimen identification when LLM returns empty components."""
        mock_llm = MagicMock()
        empty_response = RegimenLLMResponse(components=[])

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = empty_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(sample_regimen_input)

            # Should fallback to original drug
            assert result == ["FOLFOX"]

    def test_identify_regimen_model_configuration(
        self,
        sample_regimen_input,
        mock_regimen_response,
        mock_settings,
    ):
        """Test that regimen model configuration is used."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step1_regimen.config') as mock_config:

            mock_config.REGIMEN_MODEL = "gpt-4-turbo"
            mock_config.REGIMEN_TEMPERATURE = 0.3
            mock_config.REGIMEN_MAX_TOKENS = 500

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_regimen_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(sample_regimen_input)

            # Verify model configuration was used
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.model == "gpt-4-turbo"
            assert llm_config.temperature == 0.3
            assert llm_config.max_tokens == 500
            assert result == ["Fluorouracil", "Leucovorin", "Oxaliplatin"]

    def test_identify_regimen_no_title(self, mock_settings):
        """Test regimen identification with no abstract title."""
        input_no_title = RegimenInput(
            abstract_id=789,
            abstract_title=None,
            drug="FOLFIRI",
            congress_id=1,
            batch_id=2,
        )

        mock_llm = MagicMock()
        mock_response = RegimenLLMResponse(
            components=["Fluorouracil", "Leucovorin", "Irinotecan"]
        )

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(input_no_title)

            assert result == ["Fluorouracil", "Leucovorin", "Irinotecan"]

    def test_identify_regimen_llm_error(
        self,
        sample_regimen_input,
        mock_settings,
    ):
        """Test regimen identification when LLM raises an error."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.side_effect = Exception("LLM API error")
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugClassExtractionError, match="Regimen identification failed"):
                identify_regimen(sample_regimen_input)

    def test_identify_regimen_timeout_configuration(
        self,
        sample_regimen_input,
        mock_regimen_response,
        mock_settings,
    ):
        """Test that LLM is configured with correct timeout."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_regimen_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            identify_regimen(sample_regimen_input)

            # Verify LLM was created with 120s timeout
            call_args = mock_create_llm.call_args
            llm_config = call_args[0][0]
            assert llm_config.timeout == 120

    def test_identify_regimen_with_callbacks(
        self,
        sample_regimen_input,
        mock_regimen_response,
        mock_settings,
    ):
        """Test regimen identification with custom callbacks."""
        mock_llm = MagicMock()
        mock_callback = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_regimen_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(sample_regimen_input, callbacks=[mock_callback])

            # Verify callback was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert invoke_call_args[1]['config']['callbacks'] == [mock_callback]
            assert result == ["Fluorouracil", "Leucovorin", "Oxaliplatin"]

    def test_identify_regimen_with_langfuse(
        self,
        sample_regimen_input,
        mock_regimen_response,
        mock_settings,
    ):
        """Test regimen identification with Langfuse enabled."""
        mock_llm = MagicMock()
        mock_langfuse_client = MagicMock()
        mock_langfuse_handler = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug_class.step1_regimen.get_client', return_value=mock_langfuse_client), \
             patch('src.agents.drug_class.step1_regimen.CallbackHandler', return_value=mock_langfuse_handler):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_regimen_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(sample_regimen_input)

            # Verify Langfuse trace was updated
            mock_langfuse_client.update_current_trace.assert_called_once()
            trace_call_args = mock_langfuse_client.update_current_trace.call_args
            tags = trace_call_args[1]['tags']
            assert f"abstract_id:{sample_regimen_input.abstract_id}" in tags
            assert f"drug:{sample_regimen_input.drug}" in tags

            # Verify Langfuse generation was updated
            mock_langfuse_client.update_current_generation.assert_called_once()

            # Verify Langfuse handler was passed to invoke
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            assert mock_langfuse_handler in invoke_call_args[1]['config']['callbacks']

            assert result == ["Fluorouracil", "Leucovorin", "Oxaliplatin"]

    def test_identify_regimen_combination_therapy(
        self,
        mock_settings,
    ):
        """Test regimen identification for combination therapy."""
        combo_input = RegimenInput(
            abstract_id=999,
            abstract_title="Nivolumab + Ipilimumab in Melanoma",
            drug="Nivolumab + Ipilimumab",
            congress_id=1,
            batch_id=2,
        )

        mock_llm = MagicMock()
        combo_response = RegimenLLMResponse(
            components=["Nivolumab", "Ipilimumab"]
        )

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False):

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = combo_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(combo_input)

            assert result == ["Nivolumab", "Ipilimumab"]

    def test_identify_regimen_prompt_loading(
        self,
        sample_regimen_input,
        mock_regimen_response,
        mock_settings,
    ):
        """Test that regimen prompt is loaded correctly."""
        mock_llm = MagicMock()

        with patch('src.agents.drug_class.step1_regimen.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step1_regimen.settings', mock_settings), \
             patch('src.agents.drug_class.step1_regimen.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step1_regimen.get_regimen_identification_prompt') as mock_get_prompt:

            mock_get_prompt.return_value = ("System prompt for regimen identification", "v1.0")

            mock_llm_with_structured = MagicMock()
            mock_llm_with_structured.invoke.return_value = mock_regimen_response
            mock_llm.with_structured_output.return_value = mock_llm_with_structured
            mock_create_llm.return_value = mock_llm

            result = identify_regimen(sample_regimen_input)

            # Verify prompt was loaded
            mock_get_prompt.assert_called_once()

            # Verify system message was created with prompt
            invoke_call_args = mock_llm_with_structured.invoke.call_args
            messages = invoke_call_args[0][0]
            assert len(messages) == 2
            assert messages[0].content == "System prompt for regimen identification"
            assert "FOLFOX" in messages[1].content
            assert result == ["Fluorouracil", "Leucovorin", "Oxaliplatin"]
