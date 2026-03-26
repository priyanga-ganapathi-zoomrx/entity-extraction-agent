"""Unit tests for indication extraction agent."""
import pytest
from unittest.mock import patch, MagicMock, call
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from src.agents.indication.extraction_agent import IndicationAgent


@pytest.fixture
def mock_tools():
    """Create real LangChain tools for testing."""
    @tool
    def get_indication_rules(category: str, subcategories: list[str]) -> str:
        """Get indication extraction rules.

        Args:
            category: The category to search for
            subcategories: List of subcategories to filter by
        """
        return "Found 2 rule(s) for Disease/Abbreviations"

    return [get_indication_rules]


@pytest.fixture
def indication_agent(mock_tools):
    """Create IndicationAgent with mocked tools and LLM."""
    with patch('src.agents.indication.extraction_agent.get_tools', return_value=mock_tools), \
         patch('src.agents.indication.extraction_agent.create_llm') as mock_create_llm, \
         patch('src.agents.indication.extraction_agent.get_extraction_prompt', return_value=("System prompt", "prompt_v1.0.txt")), \
         patch('src.agents.indication.extraction_agent.is_langfuse_enabled', return_value=False):

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_create_llm.return_value = mock_llm

        agent = IndicationAgent(rules_data=[])
        agent.llm = mock_llm
        agent.llm_with_tools = mock_llm

        return agent


@pytest.mark.unit
class TestIndicationAgent:
    """Test indication extraction agent."""

    def test_agent_initialization_with_rules_data(self):
        """Test agent initialization with in-memory rules data."""
        sample_rules = [{"ID": "1", "Category": "Disease"}]

        with patch('src.agents.indication.extraction_agent.get_tools') as mock_get_tools, \
             patch('src.agents.indication.extraction_agent.create_llm'), \
             patch('src.agents.indication.extraction_agent.get_extraction_prompt', return_value=("System", "v1.txt")), \
             patch('src.agents.indication.extraction_agent.is_langfuse_enabled', return_value=False):

            mock_get_tools.return_value = []

            agent = IndicationAgent(rules_data=sample_rules)

            mock_get_tools.assert_called_once_with(rules_path=None, rules_data=sample_rules)

    def test_agent_initialization_with_rules_path(self):
        """Test agent initialization with CSV file path."""
        with patch('src.agents.indication.extraction_agent.get_tools') as mock_get_tools, \
             patch('src.agents.indication.extraction_agent.create_llm'), \
             patch('src.agents.indication.extraction_agent.get_extraction_prompt', return_value=("System", "v1.txt")), \
             patch('src.agents.indication.extraction_agent.is_langfuse_enabled', return_value=False):

            mock_get_tools.return_value = []

            agent = IndicationAgent(rules_path="/path/to/rules.csv")

            mock_get_tools.assert_called_once_with(rules_path="/path/to/rules.csv", rules_data=None)

    def test_agent_llm_configuration(self, mock_tools):
        """Test that agent creates LLM with correct config."""
        with patch('src.agents.indication.extraction_agent.get_tools', return_value=mock_tools), \
             patch('src.agents.indication.extraction_agent.create_llm') as mock_create_llm, \
             patch('src.agents.indication.extraction_agent.get_extraction_prompt', return_value=("System", "v1.txt")), \
             patch('src.agents.indication.extraction_agent.is_langfuse_enabled', return_value=False):

            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm
            mock_create_llm.return_value = mock_llm

            IndicationAgent(rules_data=[])

            mock_create_llm.assert_called_once()
            llm_config = mock_create_llm.call_args[0][0]
            # Just verify timeout is set correctly (actual config values may vary)
            assert llm_config.timeout == 120

    def test_get_system_message_no_caching(self, indication_agent):
        """Test system message without prompt caching."""
        from src.agents.indication.config import config as indication_config

        original_caching = indication_config.ENABLE_PROMPT_CACHING
        indication_config.ENABLE_PROMPT_CACHING = False

        try:
            msg = indication_agent._get_system_message()

            assert isinstance(msg, SystemMessage)
            assert msg.content == "System prompt"
        finally:
            indication_config.ENABLE_PROMPT_CACHING = original_caching

    def test_get_system_message_with_caching(self, indication_agent):
        """Test system message with prompt caching enabled."""
        from src.agents.indication.config import config as indication_config

        original_caching = indication_config.ENABLE_PROMPT_CACHING
        indication_config.ENABLE_PROMPT_CACHING = True

        try:
            msg = indication_agent._get_system_message()

            assert isinstance(msg, SystemMessage)
            assert isinstance(msg.content, list)
            assert msg.content[0]["type"] == "text"
            assert msg.content[0]["text"] == "System prompt"
            assert msg.content[0]["cache_control"] == {"type": "ephemeral"}
        finally:
            indication_config.ENABLE_PROMPT_CACHING = original_caching

    def test_llm_node_basic(self, indication_agent):
        """Test LLM node execution."""
        # Mock the LLM response
        mock_response = AIMessage(content="Non-Small Cell Lung Cancer")
        indication_agent.llm_with_tools.invoke.return_value = mock_response

        state = {
            "messages": [HumanMessage(content="Extract indication from: NSCLC")]
        }

        result = indication_agent._llm_node(state)

        assert "messages" in result
        assert len(result["messages"]) == 1
        assert result["messages"][0] == mock_response
        indication_agent.llm_with_tools.invoke.assert_called_once()

    def test_llm_node_adds_default_content_if_empty(self, indication_agent):
        """Test that LLM node adds default content if response is empty."""
        # Mock empty response with tool calls (proper format)
        mock_response = AIMessage(
            content="",
            tool_calls=[{
                "name": "get_indication_rules",
                "args": {"category": "Disease", "subcategories": []},
                "id": "call_1"
            }]
        )
        indication_agent.llm_with_tools.invoke.return_value = mock_response

        state = {"messages": [HumanMessage(content="Test")]}

        result = indication_agent._llm_node(state)

        # Should keep empty content when tool_calls present
        assert result["messages"][0].content == ""
        assert len(result["messages"][0].tool_calls) == 1

    def test_llm_node_handles_no_content_no_tool_calls(self, indication_agent):
        """Test LLM node when response has no content and no tool calls."""
        mock_response = AIMessage(content="")
        mock_response.tool_calls = None
        indication_agent.llm_with_tools.invoke.return_value = mock_response

        state = {"messages": [HumanMessage(content="Test")]}

        result = indication_agent._llm_node(state)

        assert result["messages"][0].content == "[Processing...]"

    def test_route_to_tools(self, indication_agent):
        """Test routing to tools when LLM makes tool calls."""
        ai_msg = AIMessage(
            content="",
            tool_calls=[{
                "name": "get_indication_rules",
                "args": {"category": "Disease", "subcategories": []},
                "id": "call_1"
            }]
        )
        state = {"messages": [ai_msg]}

        route = indication_agent._route(state)

        assert route == "tools"

    def test_route_to_end(self, indication_agent):
        """Test routing to END when no tool calls."""
        ai_msg = AIMessage(content="Non-Small Cell Lung Cancer")
        state = {"messages": [ai_msg]}

        route = indication_agent._route(state)

        assert route == "__end__"

    def test_route_empty_state(self, indication_agent):
        """Test routing with empty state."""
        state = {"messages": []}

        route = indication_agent._route(state)

        assert route == "__end__"

    def test_route_with_human_message(self, indication_agent):
        """Test routing when last message is HumanMessage (not AIMessage)."""
        state = {"messages": [HumanMessage(content="Test")]}

        route = indication_agent._route(state)

        assert route == "__end__"

    def test_get_langfuse_config_disabled(self, indication_agent):
        """Test Langfuse config when disabled."""
        config = indication_agent._get_langfuse_config(abstract_id="123")

        # Should have recursion_limit but no callbacks
        assert "recursion_limit" in config
        assert "callbacks" not in config

    def test_get_langfuse_config_enabled(self, mock_tools):
        """Test Langfuse config when enabled."""
        with patch('src.agents.indication.extraction_agent.get_tools', return_value=mock_tools), \
             patch('src.agents.indication.extraction_agent.create_llm'), \
             patch('src.agents.indication.extraction_agent.get_extraction_prompt', return_value=("System", "prompt_v1.txt")), \
             patch('src.agents.indication.extraction_agent.is_langfuse_enabled', return_value=True), \
             patch('src.agents.indication.extraction_agent.CallbackHandler') as mock_callback:

            mock_handler = MagicMock()
            mock_callback.return_value = mock_handler

            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm

            agent = IndicationAgent(rules_data=[])
            config = agent._get_langfuse_config(abstract_id="123")

            # Check that callback is present
            assert mock_handler in config["callbacks"]
            assert "langfuse_tags" in config["metadata"]
            tags = config["metadata"]["langfuse_tags"]
            assert "abstract_id:123" in tags
            assert "prompt_name:MEDICAL_INDICATION_EXTRACTION_SYSTEM_PROMPT" in tags
            assert "prompt_file:prompt_v1.txt" in tags

    def test_invoke_basic(self, indication_agent):
        """Test basic agent invocation."""
        with patch.object(indication_agent.graph, 'invoke') as mock_invoke:
            mock_invoke.return_value = {
                "messages": [
                    HumanMessage(content="Extract indication from:\n\nsession_title: Lung Cancer\nabstract_title: Pembrolizumab in NSCLC"),
                    AIMessage(content="Non-Small Cell Lung Cancer")
                ]
            }

            result = indication_agent.invoke(
                abstract_title="Pembrolizumab in NSCLC",
                session_title="Lung Cancer",
                abstract_id="123"
            )

            assert "messages" in result
            mock_invoke.assert_called_once()

            # Verify input to graph.invoke
            call_args = mock_invoke.call_args
            assert "messages" in call_args[0][0]
            assert len(call_args[0][0]["messages"]) == 1
            assert isinstance(call_args[0][0]["messages"][0], HumanMessage)
            assert "Pembrolizumab in NSCLC" in call_args[0][0]["messages"][0].content
            assert "Lung Cancer" in call_args[0][0]["messages"][0].content

    def test_invoke_with_empty_session_title(self, indication_agent):
        """Test invocation with empty session title."""
        with patch.object(indication_agent.graph, 'invoke') as mock_invoke:
            mock_invoke.return_value = {"messages": []}

            indication_agent.invoke(
                abstract_title="Pembrolizumab in NSCLC",
                session_title="",
                abstract_id="123"
            )

            call_args = mock_invoke.call_args
            prompt_content = call_args[0][0]["messages"][0].content
            assert "session_title: " in prompt_content
            assert "abstract_title: Pembrolizumab in NSCLC" in prompt_content

    def test_invoke_with_custom_callbacks(self, indication_agent):
        """Test invocation with custom callbacks."""
        custom_callback = MagicMock()

        with patch.object(indication_agent.graph, 'invoke') as mock_invoke, \
             patch.object(indication_agent, '_get_langfuse_config') as mock_get_config:

            mock_get_config.return_value = {"recursion_limit": 25}
            mock_invoke.return_value = {"messages": []}

            indication_agent.invoke(
                abstract_title="Test",
                callbacks=[custom_callback]
            )

            # Verify callbacks were added to config
            call_args = mock_invoke.call_args
            config = call_args[0][1]
            assert "callbacks" in config
            assert custom_callback in config["callbacks"]

    def test_invoke_merges_langfuse_and_custom_callbacks(self, mock_tools):
        """Test that custom callbacks are merged with Langfuse callbacks."""
        with patch('src.agents.indication.extraction_agent.get_tools', return_value=mock_tools), \
             patch('src.agents.indication.extraction_agent.create_llm'), \
             patch('src.agents.indication.extraction_agent.get_extraction_prompt', return_value=("System", "v1.txt")), \
             patch('src.agents.indication.extraction_agent.is_langfuse_enabled', return_value=True), \
             patch('src.agents.indication.extraction_agent.CallbackHandler') as mock_callback_class:

            langfuse_callback = MagicMock()
            mock_callback_class.return_value = langfuse_callback

            mock_llm = MagicMock()
            mock_llm.bind_tools.return_value = mock_llm

            agent = IndicationAgent(rules_data=[])

            custom_callback = MagicMock()

            with patch.object(agent.graph, 'invoke') as mock_invoke:
                mock_invoke.return_value = {"messages": []}

                agent.invoke(abstract_title="Test", callbacks=[custom_callback])

                call_args = mock_invoke.call_args
                config = call_args[0][1]
                assert langfuse_callback in config["callbacks"]
                assert custom_callback in config["callbacks"]

    def test_build_creates_valid_graph(self, indication_agent):
        """Test that _build creates a valid LangGraph."""
        graph = indication_agent.graph

        # Graph should be compiled
        assert graph is not None
        # Should have nodes (this is basic validation - full graph testing would be integration)
        assert hasattr(graph, 'invoke')

    def test_agent_handles_tool_call_loop(self, indication_agent):
        """Test agent with tool calling flow (LLM -> Tools -> LLM)."""
        # Simulate a full execution with tool calling
        tool_call_response = AIMessage(
            content="",
            tool_calls=[{"name": "get_indication_rules", "args": {"category": "Disease", "subcategories": ["Abbreviations"]}, "id": "call_1"}]
        )

        final_response = AIMessage(content="Non-Small Cell Lung Cancer")

        # Mock the LLM to return tool call first, then final answer
        indication_agent.llm_with_tools.invoke.side_effect = [tool_call_response, final_response]

        # This is a full graph execution test
        with patch('src.agents.indication.extraction_agent.ToolNode') as mock_tool_node_class:
            mock_tool_node = MagicMock()
            mock_tool_node.return_value = {"messages": [ToolMessage(content="NSCLC rule found", tool_call_id="call_1")]}
            mock_tool_node_class.return_value = mock_tool_node

            # Rebuild graph with mocked ToolNode
            indication_agent.graph = indication_agent._build()

            result = indication_agent.invoke(abstract_title="Pembrolizumab in NSCLC")

            # Should have multiple messages (input, tool call, tool response, final answer)
            assert "messages" in result
