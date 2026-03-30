"""Token usage tracking for LLM calls.

Provides a LangChain callback handler that accumulates token usage
across all LLM calls in a session. Works with any provider
(OpenAI, Anthropic, Google) via LangChain's standardized usage_metadata.

Usage:
    tracker = TokenUsageCallbackHandler()
    result = llm.invoke(messages, config={"callbacks": [tracker]})
    print(tracker.usage.total_tokens)  # accumulated across all calls
"""

import threading
from dataclasses import dataclass

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult


@dataclass
class TokenUsage:
    """Accumulated token usage from LLM calls."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }


class TokenUsageCallbackHandler(BaseCallbackHandler):
    """Accumulates token usage across all LLM calls in a session.

    Works with any LangChain chat model (OpenAI, Anthropic, Google)
    via the standardized usage_metadata on AIMessage. Thread-safe.

    Attributes:
        usage: Accumulated TokenUsage across all calls
        llm_calls: Number of LLM invocations tracked
    """

    def __init__(self) -> None:
        super().__init__()
        self.usage = TokenUsage()
        self.llm_calls = 0
        self._lock = threading.Lock()

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        """Called after each LLM call completes. Accumulates token usage."""
        with self._lock:
            self.llm_calls += 1
            for generation_list in response.generations:
                for generation in generation_list:
                    usage = self._extract_usage(generation)
                    self.usage.input_tokens += usage.get("input_tokens", 0)
                    self.usage.output_tokens += usage.get("output_tokens", 0)
                    self.usage.total_tokens += usage.get("total_tokens", 0)
                    self.usage.cache_creation_input_tokens += usage.get(
                        "cache_creation_input_tokens", 0
                    )
                    self.usage.cache_read_input_tokens += usage.get(
                        "cache_read_input_tokens", 0
                    )

    @staticmethod
    def _extract_usage(generation) -> dict:
        """Extract token usage from a generation object.

        Tries standardized usage_metadata first (works for all providers),
        then falls back to generation_info for older integrations.

        Cache token fields:
        - LangChain usage_metadata: input_token_details.cache_creation / cache_read
        - Anthropic generation_info: cache_creation_input_tokens / cache_read_input_tokens
        - OpenAI generation_info: token_usage.prompt_tokens_details.cached_tokens
        """
        # Primary: LangChain's standardized usage_metadata on the message
        if hasattr(generation, "message") and hasattr(generation.message, "usage_metadata"):
            usage = generation.message.usage_metadata
            if usage:
                details = (usage.get("input_token_details") or {})
                return {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "total_tokens": usage.get("total_tokens", 0),
                    "cache_creation_input_tokens": details.get("cache_creation", 0) or 0,
                    "cache_read_input_tokens": details.get("cache_read", 0) or 0,
                }

        # Fallback: generation_info (older integrations / proxy servers)
        if hasattr(generation, "generation_info") and generation.generation_info:
            info = generation.generation_info

            # Anthropic-style: cache fields at top level of usage
            cache_creation = info.get("cache_creation_input_tokens", 0) or 0
            cache_read = info.get("cache_read_input_tokens", 0) or 0

            token_usage = info.get("token_usage", {})
            if token_usage:
                # OpenAI-style: cached_tokens nested under prompt_tokens_details
                prompt_details = token_usage.get("prompt_tokens_details", {}) or {}
                if not cache_read and prompt_details.get("cached_tokens"):
                    cache_read = prompt_details["cached_tokens"]

                return {
                    "input_tokens": token_usage.get("prompt_tokens", 0),
                    "output_tokens": token_usage.get("completion_tokens", 0),
                    "total_tokens": token_usage.get("total_tokens", 0),
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                }

            # Anthropic-style without token_usage wrapper
            if cache_creation or cache_read or info.get("input_tokens"):
                return {
                    "input_tokens": info.get("input_tokens", 0),
                    "output_tokens": info.get("output_tokens", 0),
                    "total_tokens": info.get("input_tokens", 0) + info.get("output_tokens", 0),
                    "cache_creation_input_tokens": cache_creation,
                    "cache_read_input_tokens": cache_read,
                }

        return {}
