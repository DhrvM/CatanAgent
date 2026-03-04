"""
Thin wrapper around the OpenAI Python SDK for GPT-4o with tool-calling.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


class OpenAIClient:
    """
    Wraps the OpenAI chat-completions endpoint with native tool-calling.

    Requires:
      - ``pip install openai``
      - ``OPENAI_API_KEY`` env-var (or pass *api_key* directly)
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        resolved_key = api_key or os.getenv("OPENAI_API_KEY")
        if not resolved_key:
            raise ValueError(
                "OPENAI_API_KEY must be set as an environment variable or passed directly."
            )
        self._client = OpenAI(api_key=resolved_key)

    # ----------------------------------------------------------------
    # primary API
    # ----------------------------------------------------------------
    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> Any:
        """
        Send a chat-completion request with optional tool definitions.

        Returns the raw ``ChatCompletion`` object from the SDK so callers
        can inspect ``choices[0].message.tool_calls``.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        return self._client.chat.completions.create(**kwargs)

    # ----------------------------------------------------------------
    # helpers
    # ----------------------------------------------------------------
    @staticmethod
    def extract_tool_calls(
        response: Any,
    ) -> List[Dict[str, Any]]:
        """
        Pull out structured tool-call dicts from a ChatCompletion.

        Returns a list of ``{"id": ..., "name": ..., "arguments": dict}``.
        """
        msg = response.choices[0].message
        if not msg.tool_calls:
            return []

        calls: List[Dict[str, Any]] = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            calls.append({
                "id": tc.id,
                "name": tc.function.name,
                "arguments": args,
            })
        return calls

    @staticmethod
    def extract_text(response: Any) -> str:
        """Return the plain-text content from a ChatCompletion (if any)."""
        msg = response.choices[0].message
        return msg.content or ""

    @staticmethod
    def build_tool_result_message(tool_call_id: str, result: Any) -> Dict[str, Any]:
        """Create a 'tool' role message to feed the result back to GPT-4o."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result) if not isinstance(result, str) else result,
        }
