"""
Thin wrapper around the OpenAI Python SDK for GPT-4o with tool-calling.
"""
from __future__ import annotations

import json
import os
import random
import time
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
        max_retries: int = 5,
    ) -> Any:
        """
        Send a chat-completion request with optional tool definitions.

        Returns the raw ``ChatCompletion`` object from the SDK so callers
        can inspect ``choices[0].message.tool_calls``.

        Retries with exponential backoff on HTTP 429 / rate-limit errors.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if self._supports_temperature():
            kwargs["temperature"] = temperature if temperature is not None else self.temperature
        if self._uses_max_completion_tokens():
            kwargs["max_completion_tokens"] = self.max_tokens
        else:
            kwargs["max_tokens"] = self.max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        for attempt in range(max(1, max_retries)):
            try:
                return self._client.chat.completions.create(**kwargs)
            except Exception as e:
                if not self._is_rate_limit_error(e) or attempt >= max_retries - 1:
                    raise
                wait_s = min(90.0, (2**attempt) + random.uniform(0.0, 0.75))
                time.sleep(wait_s)

    @staticmethod
    def _is_rate_limit_error(exc: BaseException) -> bool:
        code = getattr(exc, "status_code", None)
        if code == 429:
            return True
        body = str(exc).lower()
        return "429" in body or "rate limit" in body or "too many requests" in body

    def _uses_max_completion_tokens(self) -> bool:
        """Newer reasoning models reject the legacy max_tokens parameter."""
        model = (self.model or "").lower()
        return model.startswith(("gpt-5", "o1", "o3", "o4"))

    def _supports_temperature(self) -> bool:
        """Reasoning models only accept their default temperature."""
        model = (self.model or "").lower()
        return not model.startswith(("gpt-5", "o1", "o3", "o4"))

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
    def extract_usage(response: Any) -> Dict[str, int]:
        """Return {"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }

    @staticmethod
    def build_tool_result_message(tool_call_id: str, result: Any) -> Dict[str, Any]:
        """Create a 'tool' role message to feed the result back to GPT-4o."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": json.dumps(result) if not isinstance(result, str) else result,
        }
