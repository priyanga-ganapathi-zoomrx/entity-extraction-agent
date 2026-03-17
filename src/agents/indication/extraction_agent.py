"""Core indication extraction agent using LangGraph.

Minimal agent with tool calling for rules retrieval.
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
from src.agents.indication.prompts import get_extraction_prompt
from src.agents.indication.tools import get_tools


class State(TypedDict):
    """Agent state."""
    messages: Annotated[list[BaseMessage], operator.add]


class IndicationAgent:
    """LangGraph agent for indication extraction."""
    
    def __init__(self, rules_path: str = None, rules_data: list[dict] = None):
        self.tools = get_tools(rules_path=rules_path, rules_data=rules_data)
        self.llm = create_llm(LLMConfig(
            api_key=settings.llm.LLM_API_KEY,
            base_url=settings.llm.LLM_BASE_URL,
            model=config.LLM_MODEL,
            temperature=config.LLM_TEMPERATURE,
            max_tokens=config.LLM_MAX_TOKENS,
            timeout=120,  # 2 minute timeout
        ))
        self.llm_with_tools = self.llm.bind_tools(self.tools)
        self.system_prompt, self.prompt_file = get_extraction_prompt()
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
        
        # Set env vars for CallbackHandler (it reads from env)
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse.LANGFUSE_PUBLIC_KEY
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse.LANGFUSE_SECRET_KEY
        os.environ["LANGFUSE_HOST"] = settings.langfuse.LANGFUSE_HOST
        
        tags = [
            f"abstract_id:{abstract_id or 'unknown'}",
            f"prompt_name:MEDICAL_INDICATION_EXTRACTION_SYSTEM_PROMPT",
            f"prompt_file:{self.prompt_file}",
            f"model:{config.LLM_MODEL}",
        ]
        
        return RunnableConfig(
            recursion_limit=config.RECURSION_LIMIT,
            callbacks=[CallbackHandler()],
            metadata={"langfuse_tags": tags},
        )
    
    def invoke(self, abstract_title: str, session_title: str = "", abstract_id: str = None, callbacks: list = None) -> dict:
        """Invoke agent and return raw result.
        
        Args:
            abstract_title: The abstract title to extract indication from
            session_title: Optional session title (fallback source)
            abstract_id: Optional ID for Langfuse tracing
            callbacks: Optional list of LangChain callback handlers (e.g., TokenUsageCallbackHandler)
            
        Returns:
            Dict with 'messages' list containing conversation
        """
        prompt = f"Extract indication from:\n\nsession_title: {session_title}\nabstract_title: {abstract_title}"
        
        config = self._get_langfuse_config(abstract_id)
        if callbacks:
            existing = config.get("callbacks", []) if isinstance(config, dict) else (config.callbacks or [])
            all_callbacks = list(existing) + list(callbacks)
            if hasattr(config, "callbacks"):
                config.callbacks = all_callbacks
            else:
                config["callbacks"] = all_callbacks
        
        return self.graph.invoke(
            {"messages": [HumanMessage(content=prompt)]},
            config,
        )

