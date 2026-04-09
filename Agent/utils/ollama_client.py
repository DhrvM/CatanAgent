from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class OllamaConfig:
    host: str = "http://localhost:11434"
    model: str = "llama3.1:8b"
    temperature: float = 0.2
    num_ctx: int = 4096
    timeout_s: int = 60


class OllamaChat:
    """
    Minimal Ollama /api/chat wrapper without external deps.
    Uses `format: "json"` to strongly encourage valid JSON output.
    """
    def __init__(self, cfg: OllamaConfig):
        self.cfg = cfg

    def chat(self, messages: List[Dict[str, str]], json_only: bool = True) -> str:
        url = f"{self.cfg.host}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.cfg.temperature,
                "num_ctx": self.cfg.num_ctx,
            },
        }
        if json_only:
            payload["format"] = "json"

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
            raw = resp.read().decode("utf-8")

        obj = json.loads(raw)
        return obj.get("message", {}).get("content", "")

    @staticmethod
    def safe_json_loads(s: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(s)
        except Exception:
            # attempt to extract first {...}
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(s[start : end + 1])
                except Exception:
                    return None
            return None

    def chat_with_usage(
        self, messages: List[Dict[str, str]], json_only: bool = True
    ) -> tuple:
        """
        Like ``chat()`` but also returns token counts.
        Returns ``(text, {"prompt_tokens": int, "completion_tokens": int})``.
        """
        url = f"{self.cfg.host}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": self.cfg.temperature,
                "num_ctx": self.cfg.num_ctx,
            },
        }
        if json_only:
            payload["format"] = "json"

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.cfg.timeout_s) as resp:
            raw = resp.read().decode("utf-8")

        obj = json.loads(raw)
        text = obj.get("message", {}).get("content", "")
        usage = {
            "prompt_tokens": obj.get("prompt_eval_count", 0) or 0,
            "completion_tokens": obj.get("eval_count", 0) or 0,
        }
        return text, usage
