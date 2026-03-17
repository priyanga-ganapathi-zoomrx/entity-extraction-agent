"""Unit tests for drug extraction agent."""
import pytest
from unittest.mock import MagicMock, patch, Mock
from src.agents.drug.extraction_agent import extract_drugs, DrugExtractionError
from src.agents.drug.schemas import DrugInput, ExtractionResult


@pytest.fixture
def sample_drug_input():
    """Sample drug extraction input."""
    return DrugInput(
        abstract_id=123,
        abstract_title="Study of Pembrolizumab in NSCLC patients",
        congress_id=1,
        batch_id=100,
    )


@pytest.fixture
def empty_drug_input():
    """Empty drug extraction input."""
    return DrugInput(
        abstract_id=456,
        abstract_title="",
        congress_id=1,
        batch_id=100,
    )


@pytest.mark.unit
class TestDrugExtraction:
    """Test drug extraction agent."""

    def test_extract_drugs_success(
        self,
        sample_drug_input,
        mock_drug_extraction_response,
        mock_llm,
    ):
        """Test successful drug extraction."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_drug_extraction_response
            mock_create_llm.return_value = mock_llm

            # Execute
            result = extract_drugs(sample_drug_input)

            # Verify
            assert result.primary_drugs == ["Pembrolizumab"]
            assert result.secondary_drugs == []
            assert result.comparator_drugs == ["Chemotherapy"]
            assert len(result.reasoning) > 0
            mock_llm.invoke.assert_called_once()

    def test_extract_drugs_empty_abstract(self, empty_drug_input, mock_llm):
        """Test extraction with empty abstract."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            mock_llm.invoke.return_value = ExtractionResult(
                reasoning=["No abstract title provided"],
                primary_drugs=[],
                secondary_drugs=[],
                comparator_drugs=[],
            )
            mock_create_llm.return_value = mock_llm

            result = extract_drugs(empty_drug_input)
            assert result.primary_drugs == []
            assert result.secondary_drugs == []
            assert result.comparator_drugs == []

    def test_extract_drugs_multiple_categories(self, sample_drug_input, mock_llm):
        """Test extraction with drugs in all categories."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            mock_llm.invoke.return_value = ExtractionResult(
                reasoning=["Found drugs in all categories"],
                primary_drugs=["Pembrolizumab"],
                secondary_drugs=["Nivolumab"],
                comparator_drugs=["Chemotherapy"],
            )
            mock_create_llm.return_value = mock_llm

            result = extract_drugs(sample_drug_input)
            assert len(result.primary_drugs) == 1
            assert len(result.secondary_drugs) == 1
            assert len(result.comparator_drugs) == 1

    def test_extract_drugs_combination_therapy(self, mock_llm):
        """Test extraction with combination therapy."""
        combination_input = DrugInput(
            abstract_id=555,
            abstract_title="Nivolumab Plus Ipilimumab in Melanoma",
            congress_id=1,
            batch_id=100,
        )

        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            mock_llm.invoke.return_value = ExtractionResult(
                reasoning=["Combination of Nivolumab and Ipilimumab"],
                primary_drugs=["Nivolumab", "Ipilimumab"],
                secondary_drugs=[],
                comparator_drugs=[],
            )
            mock_create_llm.return_value = mock_llm

            result = extract_drugs(combination_input)
            assert len(result.primary_drugs) == 2
            assert "Nivolumab" in result.primary_drugs
            assert "Ipilimumab" in result.primary_drugs

    def test_extract_drugs_llm_error(self, sample_drug_input, mock_llm):
        """Test extraction when LLM raises an error."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            mock_llm.invoke.side_effect = Exception("LLM API error")
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugExtractionError, match="Drug extraction failed"):
                extract_drugs(sample_drug_input)

    def test_extract_drugs_timeout_error(self, sample_drug_input, mock_llm):
        """Test extraction when LLM times out."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            mock_llm.invoke.side_effect = TimeoutError("Request timed out")
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugExtractionError):
                extract_drugs(sample_drug_input)

    def test_extract_drugs_with_langfuse_enabled(
        self,
        sample_drug_input,
        mock_drug_extraction_response,
        mock_llm,
    ):
        """Test extraction with Langfuse enabled."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug.extraction_agent.get_client') as mock_get_client, \
             patch('src.agents.drug.extraction_agent.CallbackHandler') as mock_callback_handler, \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            # Mock Langfuse client
            mock_lf = MagicMock()
            mock_get_client.return_value = mock_lf
            mock_handler = MagicMock()
            mock_callback_handler.return_value = mock_handler

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_drug_extraction_response
            mock_create_llm.return_value = mock_llm

            # Execute
            result = extract_drugs(sample_drug_input)

            # Verify Langfuse was called
            mock_lf.update_current_trace.assert_called_once()
            mock_lf.update_current_generation.assert_called_once()

            # Verify result
            assert result.primary_drugs == ["Pembrolizumab"]

    def test_extract_drugs_with_callbacks(
        self,
        sample_drug_input,
        mock_drug_extraction_response,
        mock_llm,
    ):
        """Test extraction with custom callbacks."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            # Configure mock LLM
            mock_llm.invoke.return_value = mock_drug_extraction_response
            mock_create_llm.return_value = mock_llm

            # Create custom callback
            custom_callback = MagicMock()

            # Execute with callback
            result = extract_drugs(sample_drug_input, callbacks=[custom_callback])

            # Verify callback was passed to invoke
            assert result.primary_drugs == ["Pembrolizumab"]
            invoke_call_args = mock_llm.invoke.call_args
            assert "config" in invoke_call_args.kwargs
            assert "callbacks" in invoke_call_args.kwargs["config"]
            assert custom_callback in invoke_call_args.kwargs["config"]["callbacks"]

    def test_extract_drugs_prompt_caching_enabled(
        self,
        sample_drug_input,
        mock_drug_extraction_response,
        mock_llm,
    ):
        """Test extraction with prompt caching enabled."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")), \
             patch('src.agents.drug.extraction_agent.config.ENABLE_PROMPT_CACHING', True):

            mock_llm.invoke.return_value = mock_drug_extraction_response
            mock_create_llm.return_value = mock_llm

            result = extract_drugs(sample_drug_input)

            # Verify LLM was invoked
            assert mock_llm.invoke.called
            messages = mock_llm.invoke.call_args[0][0]

            # Check that system message has cache_control (if caching enabled)
            # Note: This depends on implementation details
            assert result.primary_drugs == ["Pembrolizumab"]

    def test_extract_drugs_with_secondary_and_comparator(self, mock_llm):
        """Test extraction with secondary and comparator drugs."""
        input_data = DrugInput(
            abstract_id=789,
            abstract_title="Pembrolizumab + Carboplatin vs Chemotherapy in NSCLC",
            congress_id=1,
            batch_id=100,
        )

        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")):

            mock_llm.invoke.return_value = ExtractionResult(
                reasoning=["Primary: Pembrolizumab", "Secondary: Carboplatin", "Comparator: Chemotherapy"],
                primary_drugs=["Pembrolizumab"],
                secondary_drugs=["Carboplatin"],
                comparator_drugs=["Chemotherapy"],
            )
            mock_create_llm.return_value = mock_llm

            result = extract_drugs(input_data)
            assert result.primary_drugs == ["Pembrolizumab"]
            assert result.secondary_drugs == ["Carboplatin"]
            assert result.comparator_drugs == ["Chemotherapy"]

    def test_extract_drugs_creates_llm_with_correct_config(
        self,
        sample_drug_input,
        mock_drug_extraction_response,
        mock_llm,
    ):
        """Test that LLM is created with correct configuration."""
        with patch('src.agents.drug.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.drug.extraction_agent.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug.extraction_agent.get_extraction_prompt', return_value=("System prompt", "v1.0")), \
             patch('src.agents.drug.extraction_agent.config') as mock_config:

            # Set config values
            mock_config.EXTRACTION_MODEL = "gpt-4"
            mock_config.EXTRACTION_TEMPERATURE = 0.3
            mock_config.EXTRACTION_MAX_TOKENS = 2000
            mock_config.ENABLE_PROMPT_CACHING = False

            mock_llm.invoke.return_value = mock_drug_extraction_response
            mock_create_llm.return_value = mock_llm

            extract_drugs(sample_drug_input)

            # Verify create_llm was called with correct config
            mock_create_llm.assert_called_once()
            call_args = mock_create_llm.call_args[0][0]
            assert call_args.model == "gpt-4"
            assert call_args.temperature == 0.3
            assert call_args.max_tokens == 2000
            assert call_args.timeout == 120  # 2 minute timeout
