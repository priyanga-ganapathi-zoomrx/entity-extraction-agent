"""Indication Validation Agent using LangGraph.

Minimal agent that validates indication extractions against rules.
Includes Langfuse tracing, prompt caching, and per-request timeout (120s).
Retries are handled by Temporal.
"""

import operator
from typing import Annotated, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from langfuse.langchain import CallbackHandler
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from src.agents.core import settings, create_llm, LLMConfig
from src.agents.core.langfuse_config import is_langfuse_enabled
from src.agents.indication.config import config
from src.agents.indication.prompts import get_extraction_prompt, get_validation_prompt
from src.agents.indication.tools import get_tools


class State(TypedDict):
    """Agent state."""
    messages: Annotated[list[BaseMessage], operator.add]


class IndicationValidationAgent:
    """LangGraph agent for indication validation."""
    
    def __init__(self):
        """Initialize the validation agent using config settings."""
        self.tools = get_tools()
        
        self.llm = create_llm(LLMConfig(
            api_key=settings.llm.LLM_API_KEY,
            base_url=settings.llm.LLM_BASE_URL,
            model=config.VALIDATION_LLM_MODEL,
            temperature=config.VALIDATION_LLM_TEMPERATURE,
            max_tokens=config.VALIDATION_LLM_MAX_TOKENS,
            timeout=120,  # 2 minute timeout
        ))
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        
        # Load prompts
        self.system_prompt, self.prompt_version = get_validation_prompt()
        self.extraction_prompt, self.extraction_prompt_version = get_extraction_prompt()
        
        self.graph = self._build()
        self._langfuse_enabled = is_langfuse_enabled()
    
    def _build(self) -> StateGraph:
        """Build the LangGraph state graph."""
        g = StateGraph(State)
        g.add_node("llm", self._llm_node)
        g.add_node("tools", ToolNode(self.tools))
        g.add_edge(START, "llm")
        g.add_conditional_edges("llm", self._route, ["tools", END])
        g.add_edge("tools", "llm")
        return g.compile()
    
    def _get_system_message(self) -> SystemMessage:
        """Get system message with optional prompt caching."""
        if config.ENABLE_PROMPT_CACHING:
            return SystemMessage(content=[{
                "type": "text",
                "text": self.system_prompt,
                "cache_control": {"type": "ephemeral"}
            }])
        return SystemMessage(content=self.system_prompt)
    
    def _get_reference_rules_message(self) -> HumanMessage:
        """Get reference rules message with optional prompt caching."""
        content = f"""## REFERENCE RULES DOCUMENT

The following is the complete extraction rules document that the extractor was instructed to follow. Use this as your reference to verify compliance.

---

{self.extraction_prompt}

---

END OF REFERENCE RULES DOCUMENT"""

        if config.ENABLE_PROMPT_CACHING:
            return HumanMessage(content=[{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"}
            }])
        return HumanMessage(content=content)
    
    def _llm_node(self, state: State) -> dict:
        """LLM node - invokes LLM with tools."""
        messages = [self._get_system_message()] + state.get("messages", [])
        response: AIMessage = self.llm_with_tools.invoke(messages)
        
        if not response.content and not response.tool_calls:
            response.content = "[Processing...]"
        
        return {"messages": [response]}
    
    def _route(self, state: State) -> Literal["tools", "__end__"]:
        """Route to tools or end."""
        msgs = state.get("messages", [])
        if msgs and isinstance(msgs[-1], AIMessage) and getattr(msgs[-1], "tool_calls", None):
            return "tools"
        return END
    
    def _get_langfuse_config(self, abstract_id: str = None) -> RunnableConfig:
        """Get RunnableConfig with Langfuse tracing if enabled."""
        import os
        
        if not self._langfuse_enabled:
            return RunnableConfig(recursion_limit=config.RECURSION_LIMIT)
        
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse.LANGFUSE_PUBLIC_KEY
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse.LANGFUSE_SECRET_KEY
        os.environ["LANGFUSE_HOST"] = settings.langfuse.LANGFUSE_HOST
        
        tags = [
            f"abstract_id:{abstract_id or 'unknown'}",
            f"prompt_name:INDICATION_VALIDATION_SYSTEM_PROMPT",
            f"prompt_version:{self.prompt_version}",
            f"extraction_rules_version:{self.extraction_prompt_version}",
            f"model:{config.VALIDATION_LLM_MODEL}",
            "validation",
        ]
        
        return RunnableConfig(
            recursion_limit=config.RECURSION_LIMIT,
            callbacks=[CallbackHandler()],
            metadata={"langfuse_tags": tags},
        )
    
    def _format_validation_input(
        self,
        session_title: str,
        abstract_title: str,
        extraction_result: dict,
    ) -> str:
        """Format the validation input for the LLM."""
        import json
        
        generated_indication = extraction_result.get('generated_indication', '')
        indication_display = (
            generated_indication
            if str(generated_indication).strip()
            else "(EMPTY - extractor returned nothing; validate whether an indication should exist)"
        )

        empty_notice = ""
        if not str(generated_indication).strip():
            empty_notice = """

IMPORTANT:
- The extractor returned an empty indication.
- Your job is to determine if an indication SHOULD exist based on the titles.
- If the titles clearly contain a valid indication, treat this as a high-severity omission/FAIL.
- If no indication exists in the titles, you may mark PASS/REVIEW but must explain why no indication is expected."""

        return f"""## Validation Input

### Original Titles
- **session_title**: {session_title}
- **abstract_title**: {abstract_title}

### Extraction Result to Validate
- **generated_indication**: {indication_display}
- **selected_source**: {extraction_result.get('selected_source', '')}
- **reasoning**: {extraction_result.get('reasoning', '')}
- **rules_retrieved**: {json.dumps(extraction_result.get('rules_retrieved', []), indent=2)}
- **components_identified**: {json.dumps(extraction_result.get('components_identified', []), indent=2)}

Please perform all 6 validation checks and return your validation result in the specified JSON format.{empty_notice}"""

    def invoke(
        self,
        session_title: str,
        abstract_title: str,
        extraction_result: dict,
        abstract_id: str = None,
        callbacks: list = None,
    ) -> dict:
        """Invoke agent and return raw result.
        
        Args:
            session_title: The session/conference title
            abstract_title: The research abstract title
            extraction_result: The extraction result dict to validate
            abstract_id: Optional ID for Langfuse tracing
            callbacks: Optional list of LangChain callback handlers (e.g., TokenUsageCallbackHandler)
            
        Returns:
            Dict with 'messages' list containing conversation
        """
        validation_input = self._format_validation_input(
            session_title, abstract_title, extraction_result
        )
        
        initial_messages = [
            self._get_reference_rules_message(),
            HumanMessage(content=validation_input),
        ]
        
        config = self._get_langfuse_config(abstract_id)
        if callbacks:
            existing = config.get("callbacks", []) if isinstance(config, dict) else (config.callbacks or [])
            all_callbacks = list(existing) + list(callbacks)
            if hasattr(config, "callbacks"):
                config.callbacks = all_callbacks
            else:
                config["callbacks"] = all_callbacks
        
        return self.graph.invoke(
            {"messages": initial_messages},
            config,
        )
