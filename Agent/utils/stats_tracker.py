"""
Modular, agent-agnostic statistics tracker for Catan AI agents.

Tracks per-turn and per-game metrics (token usage, tool calls, success rates)
and exports to JSON + CSV on demand.
"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class TurnRecord:
    """Snapshot of a single turn's metrics."""
    turn_number: int = 0
    phase: str = ""
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0
    successful_actions: int = 0
    failed_actions: int = 0
    tool_names: List[str] = field(default_factory=list)
    duration_s: float = 0.0


class AgentStatsTracker:
    """
    Reusable statistics tracker.  Instantiate once per game and call
    the recording methods from your agent loop.  On shutdown call
    ``export_all()`` to write JSON + CSV.
    """

    def __init__(self, agent_name: str = "Agent") -> None:
        self.agent_name = agent_name
        self._turn_history: List[TurnRecord] = []
        self._current_turn: Optional[TurnRecord] = None
        self._turn_start_time: float = 0.0
        self._game_start_time: float = time.time()

    # ── per-turn lifecycle ────────────────────────────────────────

    def start_turn(self, turn_number: int, phase: str = "") -> None:
        """Begin timing a new turn."""
        # auto-end previous turn if caller forgot
        if self._current_turn is not None:
            self.end_turn()
        self._current_turn = TurnRecord(turn_number=turn_number, phase=phase)
        self._turn_start_time = time.time()

    def record_llm_call(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = "",
    ) -> None:
        """Record one LLM invocation (OpenAI or Ollama)."""
        if self._current_turn is None:
            return
        self._current_turn.llm_calls += 1
        self._current_turn.prompt_tokens += prompt_tokens
        self._current_turn.completion_tokens += completion_tokens

    def record_tool_call(self, tool_name: str, success: bool) -> None:
        """Record one tool execution."""
        if self._current_turn is None:
            return
        self._current_turn.tool_calls += 1
        self._current_turn.tool_names.append(tool_name)
        if success:
            self._current_turn.successful_actions += 1
        else:
            self._current_turn.failed_actions += 1

    def end_turn(self) -> None:
        """Finalize timing and append the current turn to history."""
        if self._current_turn is None:
            return
        self._current_turn.duration_s = round(
            time.time() - self._turn_start_time, 3
        )
        self._turn_history.append(self._current_turn)
        self._current_turn = None

    # ── aggregated accessors ──────────────────────────────────────

    def get_turn_history(self) -> List[Dict[str, Any]]:
        """Return per-turn breakdown as list of dicts."""
        return [asdict(t) for t in self._turn_history]

    def get_game_summary(self) -> Dict[str, Any]:
        """Aggregate stats across all recorded turns."""
        total_turns = len(self._turn_history)
        total_prompt = sum(t.prompt_tokens for t in self._turn_history)
        total_completion = sum(t.completion_tokens for t in self._turn_history)
        total_tools = sum(t.tool_calls for t in self._turn_history)
        total_success = sum(t.successful_actions for t in self._turn_history)
        total_fail = sum(t.failed_actions for t in self._turn_history)
        total_llm = sum(t.llm_calls for t in self._turn_history)
        game_dur = round(time.time() - self._game_start_time, 2)

        return {
            "agent_name": self.agent_name,
            "total_turns": total_turns,
            "total_llm_calls": total_llm,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_prompt + total_completion,
            "total_tool_calls": total_tools,
            "total_successful_actions": total_success,
            "total_failed_actions": total_fail,
            "tool_success_rate": (
                round(total_success / total_tools * 100, 1)
                if total_tools > 0
                else 0.0
            ),
            "avg_prompt_tokens_per_turn": (
                round(total_prompt / total_turns, 1) if total_turns > 0 else 0
            ),
            "avg_completion_tokens_per_turn": (
                round(total_completion / total_turns, 1) if total_turns > 0 else 0
            ),
            "avg_tools_per_turn": (
                round(total_tools / total_turns, 2) if total_turns > 0 else 0
            ),
            "game_duration_s": game_dur,
        }

    # ── export ────────────────────────────────────────────────────

    def _build_filename(self) -> str:
        """<agent_name>_MM-DD-YYYY_HHMM"""
        now = datetime.now()
        return f"{self.agent_name}_{now.strftime('%m-%d-%Y_%H%M')}"

    def export_json(self, path: str) -> str:
        """Write full stats to a JSON file.  Returns the path written."""
        data = {
            "game_summary": self.get_game_summary(),
            "turn_history": self.get_turn_history(),
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        return path

    def export_csv(self, path: str) -> str:
        """Write per-turn data to a CSV file.  Returns the path written."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        rows = self.get_turn_history()
        if not rows:
            return path

        fieldnames = list(rows[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                # Flatten tool_names list to comma-separated string
                row_copy = dict(row)
                if isinstance(row_copy.get("tool_names"), list):
                    row_copy["tool_names"] = ",".join(row_copy["tool_names"])
                writer.writerow(row_copy)
        return path

    def export_all(self, directory: str = "./logs/Agent") -> Dict[str, str]:
        """
        Write both JSON + CSV to *directory*.
        File names: <agent_name>_MM-DD-YYYY_HHMM.{json,csv}
        Returns dict of {"json": path, "csv": path}.
        """
        # auto-end dangling turn
        if self._current_turn is not None:
            self.end_turn()

        os.makedirs(directory, exist_ok=True)
        base = self._build_filename()
        json_path = os.path.join(directory, f"{base}.json")
        csv_path = os.path.join(directory, f"{base}.csv")

        self.export_json(json_path)
        self.export_csv(csv_path)

        return {"json": json_path, "csv": csv_path}
