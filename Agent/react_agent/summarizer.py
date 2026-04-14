"""
Track game events and the agent's strategic thinking, then use
Ollama qwen3:8b to produce a concise rolling summary for the next
GPT-4o decision step.
"""
from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Dict, List, Optional

from Agent.utils.ollama_client import OllamaChat, OllamaConfig

# Maximum events kept in the rolling buffer
_DEFAULT_MAX_EVENTS = 60

SUMMARIZE_SYSTEM = """\
You are a concise game historian for a Settlers of Catan AI agent.

You will receive a JSON object with two arrays:
  - "events": recent game events (dice rolls, builds, trades, robber moves, etc.)
  - "strategies": the AI's own strategic thoughts from recent turns

Produce a SHORT summary (3-5 sentences max) that covers:
1. Key events from the last few turns (resources gained, buildings placed, trades).
2. Any long-term or short-term strategy the AI has been pursuing.
3. Notable opponent actions (if any).

Be factual and concise. Do not invent information.
Output ONLY the summary text, no JSON wrapping.
"""


class MoveSummarizer:
    """
    Maintains a rolling buffer of game events and the agent's strategy
    thoughts.  Before each GPT-4o decision, ``summarize()`` calls the
    local Ollama model to produce a narrative context string.
    """

    def __init__(
        self,
        ollama: Optional[OllamaChat] = None,
        max_events: int = _DEFAULT_MAX_EVENTS,
    ) -> None:
        if ollama is None:
            ollama = OllamaChat(
                OllamaConfig(model="qwen3:8b", timeout_s=15, num_ctx=3072)
            )
        self._ollama = ollama
        self._events: deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._strategies: deque[str] = deque(maxlen=20)
        self._last_summary: str = ""
        self._last_summary_ts: float = 0.0

    # ----------------------------------------------------------------
    # recording
    # ----------------------------------------------------------------
    def record_event(self, event_type: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Append a game event (dice roll, build, trade, etc.)."""
        self._events.append({
            "type": event_type,
            "details": details or {},
            "t": round(time.time(), 2),
        })

    def record_strategy(self, thought: str) -> None:
        """
        Capture the GPT-4o agent's strategic reasoning so the summarizer
        can maintain continuity of long/short-term plans.
        """
        if thought and thought.strip():
            self._strategies.append(thought.strip())

    # ----------------------------------------------------------------
    # summarization
    # ----------------------------------------------------------------
    def summarize(self, force: bool = False) -> str:
        """
        Call Ollama to produce a natural-language summary of recent
        events and strategies.

        Returns the cached summary if called again within 3 seconds
        (avoids hammering the model during a multi-action turn).
        """
        now = time.time()
        if not force and self._last_summary and (now - self._last_summary_ts) < 3.0:
            return self._last_summary

        if not self._events and not self._strategies:
            return "(No game events recorded yet.)"

        payload = {
            "events": list(self._events)[-30:],  # last 30 events
            "strategies": list(self._strategies)[-10:],  # last 10 thoughts
        }

        try:
            out = self._ollama.chat(
                messages=[
                    {"role": "system", "content": SUMMARIZE_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, default=str)},
                ],
                json_only=False,  # we want free-form text
            )
            self._last_summary = out.strip() if out else "(Summary unavailable.)"
        except Exception as e:
            self._last_summary = f"(Summarizer error: {e})"

        self._last_summary_ts = time.time()
        return self._last_summary

    # ----------------------------------------------------------------
    # accessors
    # ----------------------------------------------------------------
    def get_recent_events(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the last *n* raw events."""
        return list(self._events)[-n:]

    def get_recent_strategies(self, n: int = 5) -> List[str]:
        """Return the last *n* strategy thoughts."""
        return list(self._strategies)[-n:]

    def clear(self) -> None:
        """Reset all history (e.g. when starting a new game)."""
        self._events.clear()
        self._strategies.clear()
        self._last_summary = ""
