"""
ReAct (Reasoning + Acting) Catan Agent.

Uses GPT-4o for strategic reasoning and tool-calling,
and Ollama qwen3:8b for lightweight move summarisation.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from Agent.utils.socket_client import CatanSocketClient
from Agent.utils.game_state_processor import GameStateProcessor
from Agent.utils.openai_client import OpenAIClient
from Agent.utils.ollama_client import OllamaChat, OllamaConfig
from Agent.tools.registry import ToolRegistry, build_tool_registry
from Agent.react_agent.summarizer import MoveSummarizer
from Agent.react_agent.prompts import SYSTEM_PROMPT, MAX_STEPS_PER_TURN, build_turn_message

# Heuristic helpers reused for setup & fallback
from Agent.tools.game_tools import (
    _ranked_setup_settlements,
    _ranked_setup_roads,
    _build_discard_action,
    is_my_turn,
    Action,
)


class ReactCatanAgent:
    """
    Full ReAct agent:
      1. OBSERVE  → GameStateProcessor
      2. SUMMARIZE → MoveSummarizer (qwen3:8b)
      3. THINK+ACT → GPT-4o with tools (may return multiple tool_calls)
      4. OBSERVE  → tool results fed back to GPT-4o
      5. RECORD   → events + strategy saved
      6. REPEAT until end_turn or turn changes
    """

    def __init__(
        self,
        server_url: str = "http://localhost:3001",
        game_code: Optional[str] = None,
        player_name: str = "ReactBot",
        openai_model: str = "gpt-4o",
        ollama_model: str = "qwen3:8b",
    ) -> None:
        # socket
        self.client = CatanSocketClient(server_url)
        self.game_code = game_code
        self.player_name = player_name

        # processors
        self.processor = GameStateProcessor()
        self.summarizer = MoveSummarizer(
            OllamaChat(OllamaConfig(model=ollama_model, timeout_s=15, num_ctx=3072))
        )
        self.openai = OpenAIClient(model=openai_model)

        # tool registry (built after connect)
        self._registry: Optional[ToolRegistry] = None

        # setup tracking
        self._last_setup_settlement: Optional[str] = None
        self._setup_placements_done = 0

    # ──────────────────────────────────────────────────────────────
    # public entry
    # ──────────────────────────────────────────────────────────────
    def run(self) -> None:
        """Connect, join, and loop forever."""
        self.client.connect()

        if self.game_code:
            self.client.join_game(self.game_code, self.player_name)
        else:
            ack = self.client.create_game(self.player_name)
            self.game_code = ack["gameCode"]
            print(f"Created game: {self.game_code}")

        self._registry = build_tool_registry(self.client, self.processor)
        print(f"✅ {self.player_name} agent running (game {self.game_code})")

        while True:
            state = self.client.latest_state()
            if not state:
                time.sleep(0.25)
                continue

            if not is_my_turn(state):
                # Even when not our turn, feed server events to summarizer
                self._sync_events()
                time.sleep(0.25)
                continue

            phase = state.get("phase")

            # ── SETUP (heuristic, no LLM) ─────────────────────
            if phase == "setup":
                self._handle_setup(state)
                time.sleep(0.15)
                continue

            # ── PLAYING: ReAct loop ───────────────────────────
            self._react_turn(state)
            time.sleep(0.15)

    # ──────────────────────────────────────────────────────────────
    # ReAct loop
    # ──────────────────────────────────────────────────────────────
    def _react_turn(self, initial_state: Dict[str, Any]) -> None:
        """Run the multi-step ReAct loop for one turn."""
        assert self._registry is not None

        # 1. Observe
        processed = self.processor.process(initial_state)
        state_text = self.processor.format_for_llm(processed)

        # 2. Summarize (qwen3:8b)
        self._sync_events()
        summary = self.summarizer.summarize()

        # 3. Build initial messages
        turn_phase = processed.get("turn_phase", "main")
        tools = self._registry.get_openai_schemas(phase_filter=turn_phase)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_turn_message(state_text, summary, turn_phase)},
        ]

        turn_ended = False
        steps = 0

        while not turn_ended and steps < MAX_STEPS_PER_TURN:
            steps += 1

            # Refresh tools for current phase (may change after roll, robber, etc.)
            state = self.client.latest_state() or initial_state
            current_phase = state.get("turnPhase") or turn_phase
            tools = self._registry.get_openai_schemas(phase_filter=current_phase)

            if not tools:
                print(f"  [react] no tools for phase {current_phase}, waiting…")
                time.sleep(0.3)
                continue

            # 3. Call GPT-4o
            try:
                response = self.openai.chat_with_tools(messages, tools)
            except Exception as e:
                print(f"  [react] GPT-4o error: {e}")
                self._fallback_action(state)
                break

            tool_calls = self.openai.extract_tool_calls(response)
            assistant_text = self.openai.extract_text(response)

            # Record strategy from GPT-4o's reasoning
            if assistant_text:
                self.summarizer.record_strategy(assistant_text)
                print(f"  [thought] {assistant_text[:200]}")

            if not tool_calls:
                # GPT-4o responded with text only (no tool call) — done
                print("  [react] no tool calls, ending turn loop")
                break

            # Build the assistant message for the conversation
            assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_text or None}
            # Attach raw tool_calls from the response
            raw_msg = response.choices[0].message
            if raw_msg.tool_calls:
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in raw_msg.tool_calls
                ]
            messages.append(assistant_msg)

            # 4. Execute ALL tool calls and feed results back
            for tc in tool_calls:
                name = tc["name"]
                args = tc["arguments"]
                tc_id = tc["id"]

                print(f"  [action] {name}({json.dumps(args, default=str)})")

                result = self._registry.execute(name, args)
                print(f"  [result] {json.dumps(result, default=str)[:200]}")

                # Record event
                self.summarizer.record_event(name, {
                    "args": args,
                    "success": result.get("success", True),
                })

                # Feed result back to GPT-4o
                messages.append(
                    self.openai.build_tool_result_message(tc_id, result)
                )

                if name == "end_turn":
                    turn_ended = True

            # Brief pause for state to propagate
            time.sleep(0.1)

        if steps >= MAX_STEPS_PER_TURN and not turn_ended:
            print("  [react] max steps reached, forcing end_turn")
            self._registry.execute("end_turn", {})
            self.summarizer.record_event("end_turn", {"forced": True})

    # ──────────────────────────────────────────────────────────────
    # Setup phase (heuristic, no LLM)
    # ──────────────────────────────────────────────────────────────
    def _handle_setup(self, state: Dict[str, Any]) -> None:
        """Brute-force settlement + road placement during setup."""
        if self._last_setup_settlement is None:
            ranked = _ranked_setup_settlements(state, top_k=120)
            for vk in ranked:
                resp = self._safe_call("placeSettlement", {
                    "vertexKey": vk, "isSetup": True,
                })
                if isinstance(resp, dict) and resp.get("success"):
                    self._last_setup_settlement = vk
                    print(f"  [setup] settlement at {vk}")
                    self.summarizer.record_event("placeSettlement", {"vertex": vk, "setup": True})
                    return
            print("  [setup] no legal settlement found")
            return

        # Place road
        ranked_edges = _ranked_setup_roads(state, self._last_setup_settlement, top_k=200)
        for ek in ranked_edges:
            resp = self._safe_call("placeRoad", {
                "edgeKey": ek,
                "isSetup": True,
                "lastSettlement": self._last_setup_settlement,
            })
            if isinstance(resp, dict) and resp.get("success"):
                print(f"  [setup] road at {ek}")
                self.summarizer.record_event("placeRoad", {"edge": ek, "setup": True})
                adv = self._safe_call("advanceSetup")
                print(f"  [setup] advanceSetup => {adv}")
                self._last_setup_settlement = None
                self._setup_placements_done += 1
                return
        print("  [setup] no legal road found")

    # ──────────────────────────────────────────────────────────────
    # Fallback
    # ──────────────────────────────────────────────────────────────
    def _fallback_action(self, state: Dict[str, Any]) -> None:
        """Deterministic fallback when GPT-4o is unavailable."""
        turn_phase = state.get("turnPhase")
        print(f"  [fallback] phase={turn_phase}")

        if turn_phase == "roll":
            self._safe_call("rollDice")
            self.summarizer.record_event("rollDice", {"fallback": True})
        elif turn_phase == "discard":
            actions = _build_discard_action(state)
            if actions:
                a = actions[0]
                self._safe_call("discardCards", a.payload)
                self.summarizer.record_event("discardCards", {"fallback": True})
        elif turn_phase == "robber":
            hexes = list((state.get("hexes") or {}).keys())
            cur = state.get("robber")
            for hk in hexes:
                if hk != cur:
                    self._safe_call("moveRobber", {
                        "hexKey": hk, "stealFromPlayerId": None,
                    })
                    self.summarizer.record_event("moveRobber", {"fallback": True})
                    break
        else:
            self._safe_call("endTurn")
            self.summarizer.record_event("end_turn", {"fallback": True})

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────
    def _safe_call(
        self, event: str, payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        try:
            if payload is None or payload == {}:
                resp = self.client.sio.call(event, timeout=10)
            else:
                resp = self.client.sio.call(event, payload, timeout=10)
            if isinstance(resp, dict):
                return resp
            return {"success": True, "raw": resp}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _sync_events(self) -> None:
        """Push recent socket events into the summarizer."""
        for evt in self.client.get_all_events():
            self.summarizer.record_event(
                evt["type"],
                evt.get("data", {}),
            )
