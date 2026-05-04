"""
Thin wrapper around the Anthropic Messages API with tool-calling.
Implements the same helper surface used by ReactCatanAgent/OpenAIClient.
"""
from __future__ import annotations

import json
import os
import random
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None


class AnthropicClient:
    def __init__(
        self,
        model: str = "claude-3-5-sonnet-latest",
        api_key: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 2048,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        resolved_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set as an environment variable or passed directly."
            )
        if Anthropic is None:
            raise ImportError(
                "anthropic package is not installed. Install with: pip install anthropic"
            )
        self._client = Anthropic(api_key=resolved_key)

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_retries: int = 5,
    ) -> Any:
        system_text, normalized_messages = self._normalize_messages(messages)
        anthropic_tools = self._convert_tools(tools or [])

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": normalized_messages,
            "max_tokens": self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if system_text:
            kwargs["system"] = system_text
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        for attempt in range(max(1, max_retries)):
            try:
                return self._client.messages.create(**kwargs)
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

    @staticmethod
    def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for t in tools:
            fn = t.get("function") if isinstance(t, dict) else None
            if not isinstance(fn, dict):
                continue
            input_schema = fn.get("parameters")
            if not isinstance(input_schema, dict):
                input_schema = {"type": "object", "properties": {}}
            converted.append(
                {
                    "name": str(fn.get("name") or ""),
                    "description": str(fn.get("description") or ""),
                    "input_schema": input_schema,
                }
            )
        return converted

    @staticmethod
    def _normalize_messages(messages: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        normalized: List[Dict[str, Any]] = []

        for m in messages:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or "user")
            content = m.get("content")

            if role == "system":
                if content:
                    system_parts.append(str(content))
                continue

            if role == "assistant":
                blocks: List[Dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": str(content)})
                raw_calls = m.get("tool_calls")
                if isinstance(raw_calls, list):
                    for tc in raw_calls:
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function")
                        if not isinstance(fn, dict):
                            continue
                        name = str(fn.get("name") or "")
                        if not name:
                            continue
                        args = fn.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                parsed_args = json.loads(args)
                            except Exception:
                                parsed_args = {}
                        elif isinstance(args, dict):
                            parsed_args = args
                        else:
                            parsed_args = {}
                        call_id = str(tc.get("id") or f"anthropic-call-{uuid.uuid4().hex[:10]}")
                        blocks.append(
                            {
                                "type": "tool_use",
                                "id": call_id,
                                "name": name,
                                "input": parsed_args,
                            }
                        )
                normalized.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
                continue

            if role == "user":
                if isinstance(content, list):
                    normalized.append({"role": "user", "content": content})
                else:
                    normalized.append(
                        {"role": "user", "content": [{"type": "text", "text": str(content or "")}]}
                    )
                continue

            # Fallback: preserve as user text if unknown role appears.
            normalized.append({"role": "user", "content": [{"type": "text", "text": str(content or "")}]})

        return ("\n\n".join(system_parts), normalized)

    @staticmethod
    def extract_tool_calls(response: Any) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) != "tool_use":
                continue
            calls.append(
                {
                    "id": getattr(block, "id", ""),
                    "name": getattr(block, "name", ""),
                    "arguments": getattr(block, "input", {}) or {},
                }
            )
        return calls

    @staticmethod
    def extract_text(response: Any) -> str:
        chunks: List[str] = []
        for block in getattr(response, "content", []) or []:
            if getattr(block, "type", None) == "text":
                txt = getattr(block, "text", "")
                if txt:
                    chunks.append(str(txt))
        return "\n".join(chunks).strip()

    @staticmethod
    def extract_usage(response: Any) -> Dict[str, int]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        prompt_tokens = getattr(usage, "input_tokens", 0) or 0
        completion_tokens = getattr(usage, "output_tokens", 0) or 0
        return {
            "prompt_tokens": int(prompt_tokens),
            "completion_tokens": int(completion_tokens),
            "total_tokens": int(prompt_tokens + completion_tokens),
        }

    @staticmethod
    def build_tool_result_message(tool_call_id: str, result: Any) -> Dict[str, Any]:
        rendered = json.dumps(result, default=str) if not isinstance(result, str) else result
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": rendered,
                }
            ],
        }

