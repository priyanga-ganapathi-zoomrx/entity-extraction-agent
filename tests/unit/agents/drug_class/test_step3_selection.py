"""Unit tests for drug class step 3: selection."""
import pytest
from unittest.mock import MagicMock, patch
from src.agents.drug_class.step3_selection import select_drug_class
from src.agents.drug_class.schemas import SelectionInput, DrugSelectionResult, DrugClassExtractionError


@pytest.fixture
def selection_input_single_class():
    """Selection input with only one unique class."""
    from tests.fixtures.drug_class_responses import SELECTION_INPUT_SINGLE_CLASS
    return SelectionInput(**SELECTION_INPUT_SINGLE_CLASS)


@pytest.fixture
def selection_input_multiple_classes():
    """Selection input with multiple class types."""
    from tests.fixtures.drug_class_responses import SELECTION_INPUT_MULTIPLE_CLASSES
    return SelectionInput(**SELECTION_INPUT_MULTIPLE_CLASSES)


@pytest.fixture
def selection_input_no_classes():
    """Selection input with no classes."""
    from tests.fixtures.drug_class_responses import SELECTION_INPUT_NO_CLASSES
    return SelectionInput(**SELECTION_INPUT_NO_CLASSES)


@pytest.fixture
def selection_input_combination():
    """Selection input for combination therapy."""
    from tests.fixtures.drug_class_responses import SELECTION_INPUT_COMBINATION
    return SelectionInput(**SELECTION_INPUT_COMBINATION)


@pytest.mark.unit
class TestDrugClassSelection:
    """Test drug class selection (step 3)."""

    def test_select_drug_class_edge_case_zero_classes(self, selection_input_no_classes):
        """Test selection with 0 unique classes (no LLM call)."""
        result = select_drug_class(selection_input_no_classes)

        assert result.drug_name == "Unknown Drug"
        assert result.selected_drug_classes == ["NA"]
        assert "No extracted classes" in result.reasoning
        # Verify no LLM call was made (edge case optimization)

    def test_select_drug_class_edge_case_one_class(self, selection_input_single_class):
        """Test selection with 1 unique class (no LLM call)."""
        result = select_drug_class(selection_input_single_class)

        assert result.drug_name == "Pembrolizumab"
        assert result.selected_drug_classes == ["PD-1 Inhibitor"]
        assert "one unique class" in result.reasoning.lower()
        # Edge case: no LLM call needed

    def test_select_drug_class_multiple_types_llm_call(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test selection with multiple class types (requires LLM)."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System prompt", "Rules", "v1.0")):

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            result = select_drug_class(selection_input_multiple_classes)

            assert result.drug_name == "Pembrolizumab"
            assert result.selected_drug_classes == ["PD-1 Inhibitor"]
            assert "MoA" in result.reasoning
            mock_llm.invoke.assert_called_once()

    def test_select_drug_class_prioritization_moa_wins(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test that MoA takes priority over other class types."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            result = select_drug_class(selection_input_multiple_classes)

            # Verify MoA class was selected
            assert "PD-1 Inhibitor" in result.selected_drug_classes
            # Verify reasoning mentions prioritization
            assert ("priority" in result.reasoning.lower() or
                    "moa" in result.reasoning.lower())

    def test_select_drug_class_combination_therapy(
        self,
        selection_input_combination,
        mock_llm,
    ):
        """Test selection for combination therapy with multiple targets."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTI_TARGET

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTI_TARGET
            mock_create_llm.return_value = mock_llm

            result = select_drug_class(selection_input_combination)

            # Multiple targets should return multiple classes
            assert len(result.selected_drug_classes) >= 2
            assert "PD-1 Inhibitor" in result.selected_drug_classes
            assert "CTLA-4 Inhibitor" in result.selected_drug_classes

    def test_select_drug_class_llm_error(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test selection when LLM raises an error."""
        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            mock_llm.invoke.side_effect = Exception("LLM API error")
            mock_create_llm.return_value = mock_llm

            with pytest.raises(DrugClassExtractionError, match="Selection failed"):
                select_drug_class(selection_input_multiple_classes)

    def test_select_drug_class_with_langfuse_enabled(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test selection with Langfuse enabled."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=True), \
             patch('src.agents.drug_class.step3_selection.get_client') as mock_get_client, \
             patch('src.agents.drug_class.step3_selection.CallbackHandler') as mock_callback, \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            mock_lf = MagicMock()
            mock_get_client.return_value = mock_lf
            mock_handler = MagicMock()
            mock_callback.return_value = mock_handler

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            result = select_drug_class(selection_input_multiple_classes)

            mock_lf.update_current_trace.assert_called_once()
            mock_lf.update_current_generation.assert_called_once()
            assert result.selected_drug_classes == ["PD-1 Inhibitor"]

    def test_select_drug_class_with_callbacks(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test selection with custom callbacks."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            custom_callback = MagicMock()
            result = select_drug_class(selection_input_multiple_classes, callbacks=[custom_callback])

            assert result.selected_drug_classes == ["PD-1 Inhibitor"]
            invoke_call_args = mock_llm.invoke.call_args
            assert "config" in invoke_call_args.kwargs
            assert custom_callback in invoke_call_args.kwargs["config"]["callbacks"]

    def test_select_drug_class_creates_llm_with_correct_config(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test that LLM is created with correct configuration."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")), \
             patch('src.agents.drug_class.step3_selection.config') as mock_config:

            mock_config.SELECTION_MODEL = "gpt-4"
            mock_config.SELECTION_TEMPERATURE = 0.0
            mock_config.SELECTION_MAX_TOKENS = 2000
            mock_config.ENABLE_PROMPT_CACHING = False

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            select_drug_class(selection_input_multiple_classes)

            mock_create_llm.assert_called_once()
            call_args = mock_create_llm.call_args[0][0]
            assert call_args.model == "gpt-4"
            assert call_args.temperature == 0.0
            assert call_args.max_tokens == 2000
            assert call_args.timeout == 120

    def test_select_drug_class_three_message_pattern(
        self,
        selection_input_multiple_classes,
        mock_llm,
    ):
        """Test that selection uses 3-message pattern (system, rules, input)."""
        from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            select_drug_class(selection_input_multiple_classes)

            messages = mock_llm.invoke.call_args[0][0]
            assert len(messages) == 3

    def test_select_drug_class_handles_dict_input(
        self,
        mock_llm,
    ):
        """Test selection handles both Pydantic and dict inputs (for Temporal)."""
        # Use dict format directly (as would come from Temporal pipeline)
        from tests.fixtures.drug_class_responses import SELECTION_INPUT_MULTIPLE_CLASSES

        input_dict = SelectionInput(**SELECTION_INPUT_MULTIPLE_CLASSES)

        with patch('src.agents.drug_class.step3_selection.create_llm') as mock_create_llm, \
             patch('src.agents.drug_class.step3_selection.is_langfuse_enabled', return_value=False), \
             patch('src.agents.drug_class.step3_selection.get_selection_prompt_parts',
                   return_value=("System", "Rules", "v1.0")):

            from tests.fixtures.drug_class_responses import MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_llm.invoke.return_value = MOCK_SELECTION_MULTIPLE_MoA_WINS
            mock_create_llm.return_value = mock_llm

            result = select_drug_class(input_dict)
            assert result.selected_drug_classes == ["PD-1 Inhibitor"]
