"""
Strategy Agent — the brain and game loop owner of the multi-agent system.

Architecture (hybrid plan-then-ReAct):

Every turn, Strategy:
  1. Observes the latest game state (writes to scratchpad).
  2. Computes a diff against the snapshot taken at the end of its previous
     turn (``RoundSummary``) so the planner sees what opponents did.
  3. Rolls dice directly (hardcoded, no LLM decision) if turnPhase == "roll".
  4. Generates a ``StrategyPlan`` via a single non-ReAct LLM call.
  5. In MAIN phase, runs a bounded ReAct loop (MAX_REACT_STEPS).  The loop
     may consult Risk/Development, delegate builds/trades, query board
     info, and must terminate by calling ``end_turn``.  If the budget is
     exhausted, Strategy forcibly ends the turn (invariant: every turn
     ends with Strategy).
  6. In DISCARD / ROBBER sub-phases, delegates to Development and lets
     the next tick handle whatever comes next (usually main phase).
  7. In SETUP, a single LLM call picks settlement + road for the turn.

All peer-agent communication goes through ``BaseAgent.call_agent`` so the
topology enforced in ``ALLOWED_CHANNELS`` is honored.
"""
from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import (
    Scratchpad,
    StrategyPlan,
    TradePolicy,
    ActionRecord,
    RiskAnalysis,
    RoundSummary,
)
from Agent.utils.socket_client import CatanSocketClient
from Agent.utils.game_state_processor import GameStateProcessor
from Agent.utils.openai_client import OpenAIClient
from Agent.utils.stats_tracker import AgentStatsTracker
from Agent.tools.registry import ToolRegistry
from Agent.trading_agent.agent import TradingAgent

from Agent.strategy_agent.prompts import (
    STRATEGY_PLAN_SYSTEM_PROMPT,
    STRATEGY_REACT_SYSTEM_PROMPT,
    STRATEGY_SETUP_SYSTEM_PROMPT,
    MAX_REACT_STEPS,
    build_plan_context,
    build_react_kickoff_context,
    build_setup_context,
)

# Heuristic helpers reused for setup fallback + discard/robber fallback
from Agent.tools.game_tools import (
    _ranked_setup_settlements,
    _ranked_setup_roads,
    _build_discard_action,
    is_my_turn,
)


# ──────────────────────────────────────────────────────────────────
# Meta-tool schemas (Strategy-only; not in the registry)
# ──────────────────────────────────────────────────────────────────

def _fn_schema(name: str, description: str, properties: Optional[Dict[str, Any]] = None,
               required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
        },
    }


STRATEGY_META_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    # ── scratchpad readers ─────────────────────────────────────────
    _fn_schema(
        "get_state",
        "Return the current token-efficient game state JSON from the scratchpad.",
    ),
    _fn_schema(
        "get_plan",
        "Return your current StrategyPlan (the plan just produced for this turn).",
    ),
    _fn_schema(
        "get_risk_analysis",
        "Return the most recently written RiskAnalysis from the scratchpad. "
        "May be stale — call analyze_risk to refresh.",
    ),
    _fn_schema(
        "get_round_summary",
        "Return the last-round summary diff (VP deltas, new buildings, robber "
        "moves, longest-road/largest-army changes, completed trades).",
    ),
    _fn_schema(
        "get_action_log",
        "Return the most recent action records.",
        properties={
            "limit": {
                "type": "integer",
                "description": "Max number of recent action records (default 15, max 50).",
            },
        },
    ),
    _fn_schema(
        "get_inter_agent_messages",
        "Return messages addressed to the Strategy Agent (last 10).",
    ),

    # ── consultants & delegations ─────────────────────────────────
    _fn_schema(
        "analyze_risk",
        "Run the Risk Agent's deterministic math + narrative analysis now. "
        "Updates scratchpad.risk_analysis.",
    ),
    _fn_schema(
        "ask_risk",
        "Send a natural-language question to the Risk Agent consultant and "
        "receive a 3-5 sentence recommendation.",
        properties={
            "question": {
                "type": "string",
                "description": "A specific risk question about threats, opponents, "
                               "resources, or the board.",
            },
        },
        required=["question"],
    ),
    _fn_schema(
        "ask_development",
        "Send a natural-language question to the Development Agent consultant "
        "about building moves, ROI, or placement.",
        properties={
            "question": {
                "type": "string",
                "description": "A specific building question.",
            },
        },
        required=["question"],
    ),
    _fn_schema(
        "delegate_build",
        "Hand building work to the Development Agent.  Supply EITHER a full "
        "queue (list of {action,target,priority} items), OR a single action "
        "({action,target,priority?}), OR nothing to execute the current plan's "
        "build_queue as-is.  Returns per-action success/failure.",
        properties={
            "queue": {
                "type": "array",
                "description": "Full build queue to execute.",
                "items": {"type": "object"},
            },
            "action": {
                "type": "object",
                "description": "Single build action to execute "
                               "(e.g. {\"action\":\"upgrade_to_city\",\"target\":\"v_0_-1_2\"}).",
            },
        },
    ),
    _fn_schema(
        "delegate_trade",
        "Have the Trading Agent make one proactive trade decision under the "
        "current trade_policy. Returns immediately after a bank trade completes, "
        "a player proposal is posted as pending, or no viable trade is found.",
    ),
]


# ──────────────────────────────────────────────────────────────────
# Strategy Agent
# ──────────────────────────────────────────────────────────────────

class StrategyAgent(BaseAgent):
    """
    Orchestrator for the multi-agent Catan system.

    Owns the main socket polling loop, produces a StrategyPlan each turn,
    executes the main phase via a bounded ReAct loop, and is the ONLY
    agent that ever calls ``end_turn`` — every turn starts and ends here.
    """

    def __init__(
        self,
        scratchpad: Scratchpad,
        openai: OpenAIClient,
        client: CatanSocketClient,
        processor: GameStateProcessor,
        registry: ToolRegistry,
        stats: AgentStatsTracker,
        game_code: Optional[str] = None,
        reconnect_player_id: Optional[str] = None,
        player_name: str = "StrategyBot",
    ) -> None:
        super().__init__("strategy", scratchpad)
        self.openai = openai
        self.client = client
        self.processor = processor
        self.registry = registry
        self.stats = stats
        self.game_code = game_code
        self.reconnect_player_id = reconnect_player_id
        self.player_name = player_name

        # Turn bookkeeping.
        #   _turn_phase_state values:
        #     "none"    — no turn in progress, awaiting ours
        #     "active"  — our turn is active; plan + ReAct still running
        #     "ended"   — we called end_turn; waiting for server transition
        self._turn_counter: int = 0
        self._turn_phase_state: str = "none"

        # Setup-phase bookkeeping (fallback heuristic)
        self._last_setup_settlement: Optional[str] = None
        self._setup_placements_done: int = 0

        # Round-diff snapshot (taken at the end of our previous turn)
        self._last_turn_snapshot: Optional[Dict[str, Any]] = None

        # Counter used to dedupe completed-trade detection for round_summary
        self._trade_highwater: float = 0.0
        self._last_trade_result: Optional[Dict[str, Any]] = None
        self._last_discard_sig: Optional[str] = None

    # ──────────────────────────────────────────────────────────────
    # Main game loop
    # ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Connect, join/create a game, and poll forever."""
        self.client.connect()

        if self.game_code:
            if self.reconnect_player_id:
                self.client.reconnect_game(self.game_code, self.reconnect_player_id)
            else:
                self.client.join_game(self.game_code, self.player_name)
        else:
            ack = self.client.create_game(self.player_name)
            self.game_code = ack["gameCode"]
            print(f"Created game: {self.game_code}")

        print(f"[OK] {self.player_name} multi-agent running (game {self.game_code})")

        try:
            while True:
                state = self.client.latest_state()
                if not state:
                    time.sleep(0.25)
                    continue

                if not is_my_turn(state):
                    # Reset turn state once the server has moved past us.
                    # This makes _begin_turn fire exactly once the next
                    # time it becomes our turn.
                    if self._turn_phase_state != "none":
                        self._turn_phase_state = "none"
                    self._handle_offturn_discard_if_needed(state)
                    self._check_reactive_trades(state)
                    time.sleep(0.25)
                    continue

                phase = state.get("phase")

                if phase == "setup":
                    self._handle_setup(state)
                    time.sleep(0.15)
                    continue

                self._run_turn(state)
                time.sleep(0.15)

        except KeyboardInterrupt:
            print("\n[!] Strategy Agent interrupted.")
        except Exception as e:
            print(f"\n[!] Strategy Agent error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            paths = self.stats.export_all("./logs/Agent")
            print("[stats] Saved:")
            print(f"   JSON: {paths['json']}")
            print(f"   CSV:  {paths['csv']}")

    # ──────────────────────────────────────────────────────────────
    # Turn dispatch (playing phase)
    # ──────────────────────────────────────────────────────────────

    def _run_turn(self, state: Dict[str, Any]) -> None:
        """
        Dispatch a tick of our turn based on the current ``turnPhase``.

        Turn lifecycle:
          - Roll phase    → auto-roll, refresh state, continue
          - Discard phase → delegate to Development, return
          - Robber phase  → delegate to Development, continue to main
          - Main phase    → plan (LLM) → ReAct loop → end_turn
          - Setup         → handled separately by _handle_setup
        """
        # If we've already called end_turn and are waiting for the server
        # to transition, do nothing — next tick should show someone else.
        if self._turn_phase_state == "ended":
            return

        self._check_pending_trade_offer()
        turn_phase = state.get("turnPhase", "main")

        # ── New-turn bookkeeping ──────────────────────────────────
        # Fire on the first tick of each of our turns regardless of the
        # phase we observe, so we always compute the round diff and mark
        # start_turn exactly once per turn.
        if self._turn_phase_state == "none":
            self._begin_turn(state)

        self.scratchpad.update_game_state(state, self.processor)

        print(f"\n{'=' * 60}")
        print(f"  TURN {self._turn_counter}  |  phase={turn_phase}")
        print(f"{'=' * 60}")

        # ── Pre-turn: auto-roll (no LLM) ──────────────────────────
        if turn_phase == "roll":
            self._auto_roll()
            # Refresh state and continue to whatever phase follows
            state = self.client.latest_state() or state
            self.scratchpad.update_game_state(state, self.processor)
            turn_phase = state.get("turnPhase", "main")
            print(f"  [strategy] After roll: turnPhase={turn_phase}")

        # ── Discard (on-turn path — off-turn handled elsewhere) ───
        if turn_phase == "discard":
            print("  [strategy] Discard phase -> Development")
            try:
                self.call_agent("development", "handle_discard")
            except Exception as e:
                print(f"  [strategy] Development discard failed: {e}")
                self._fallback_discard(state)
            self.stats.end_turn()
            return  # next tick will resume in robber/main

        # ── Robber ────────────────────────────────────────────────
        if turn_phase == "robber":
            print("  [strategy] Robber phase -> Development")
            try:
                self.call_agent("development", "handle_robber")
            except Exception as e:
                print(f"  [strategy] Development robber failed: {e}")
                self._fallback_robber(state)
            # Refresh; control may now be main
            state = self.client.latest_state() or state
            self.scratchpad.update_game_state(state, self.processor)
            turn_phase = state.get("turnPhase", "main")

        # ── Main phase: plan + ReAct loop ─────────────────────────
        if turn_phase == "main":
            self._plan(state)
            self._run_react_loop(state)
            self._close_turn(state)

        self.stats.end_turn()

    def _begin_turn(self, state: Dict[str, Any]) -> None:
        """Bookkeeping at the start of a new playing turn."""
        self._turn_counter += 1
        self._turn_phase_state = "active"
        self.stats.start_turn(self._turn_counter, phase=state.get("turnPhase", "main"))
        self.scratchpad.new_turn(self._turn_counter)

        # Compute round diff from previous turn's snapshot (if any)
        self.scratchpad.update_game_state(state, self.processor)
        summary = self._compute_round_summary(state)
        self.scratchpad.write_round_summary(summary)
        if summary.vp_deltas or summary.new_buildings or summary.robber_moved:
            print(f"  [strategy] Round diff: {self._short_diff(summary)}")

    def _close_turn(self, state: Dict[str, Any]) -> None:
        """
        Snapshot state after end_turn was called and mark the turn ended.

        We keep ``_turn_phase_state = "ended"``; run() will flip it back
        to "none" once the server visibly advances to another player.
        That prevents a double-begin if the server briefly echoes our
        stale "my turn" state on the next tick.
        """
        latest = self.client.latest_state() or state
        self._last_turn_snapshot = self._snapshot_for_diff(latest)
        self._turn_phase_state = "ended"

    # ──────────────────────────────────────────────────────────────
    # Auto-roll (hardcoded, no LLM decision)
    # ──────────────────────────────────────────────────────────────

    def _auto_roll(self) -> None:
        print("  [strategy] Rolling dice (auto)...")
        result = self.registry.execute("roll_dice", {})
        ok = bool(isinstance(result, dict) and result.get("success", True))
        self.stats.record_tool_call("roll_dice", success=ok)
        self.scratchpad.append_action(ActionRecord(
            agent="strategy", action="roll_dice",
            args={}, result=result if isinstance(result, dict) else {"raw": result},
            success=ok,
        ))
        time.sleep(0.3)

    # ──────────────────────────────────────────────────────────────
    # Plan step (non-ReAct LLM call producing StrategyPlan)
    # ──────────────────────────────────────────────────────────────

    def _plan(self, state: Dict[str, Any]) -> None:
        try:
            context = self._build_plan_context_payload()
            messages = [
                {"role": "system", "content": STRATEGY_PLAN_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ]
            response = self.openai.chat_with_tools(
                messages,
                tools=[self._plan_submission_tool_schema()],
            )

            usage = self.openai.extract_usage(response)
            self.stats.record_llm_call(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                model=self.openai.model,
            )

            text = self._extract_plan_text_from_response(response)
            plan = self._parse_plan(text)
            if plan.reasoning.startswith("Plan parsing failed"):
                print("  [strategy] Plan parse failed, requesting strict JSON retry...")
                retry_text = self._retry_plan_json(context, text)
                retry_plan = self._parse_plan(retry_text)
                if not retry_plan.reasoning.startswith("Plan parsing failed"):
                    plan = retry_plan
            self.scratchpad.write_strategy_plan(plan)
            print(
                f"  [strategy] Plan: goal={plan.long_term_goal}, "
                f"tolerance={plan.risk_tolerance}, "
                f"build_queue={len(plan.build_queue)} items, "
                f"trade_first={plan.should_trade_first}"
            )
            if plan.reasoning:
                print(f"  [strategy] Reasoning: {plan.reasoning}")

        except Exception as e:
            print(f"  [strategy] Planning failed: {e}")
            import traceback
            traceback.print_exc()

    def _build_plan_context_payload(self) -> str:
        state_json = self.scratchpad.to_state_json()
        prev_plan = asdict(self.scratchpad.strategy_plan)
        round_summary = asdict(self.scratchpad.round_summary)
        risk_data = asdict(self.scratchpad.risk_analysis)
        messages = [asdict(m) for m in self.get_messages()]

        building_spots: Dict[str, Any] = {}
        try:
            s_spots = self.registry.execute("get_building_spots", {"building_type": "settlement"})
            c_spots = self.registry.execute("get_building_spots", {"building_type": "city"})
            r_spots = self.registry.execute("get_building_spots", {"building_type": "road"})
            building_spots = {
                "settlement_spots": (s_spots or {}).get("spots", [])[:5],
                "city_spots": (c_spots or {}).get("spots", [])[:5],
                "road_spots": (r_spots or {}).get("spots", [])[:6],
            }
        except Exception:
            pass

        return build_plan_context(
            state_json=state_json,
            previous_plan=prev_plan,
            round_summary=round_summary,
            building_spots=building_spots,
            agent_messages=messages,
            risk_analysis=risk_data,
        )

    def _parse_plan(self, text: str) -> StrategyPlan:
        data = self._extract_first_json_dict(text)

        if not isinstance(data, dict):
            print("  [strategy] Could not parse plan, using defaults")
            snippet = (text or "").strip().replace("\n", "\\n")
            if snippet:
                print(f"  [strategy] Raw plan output: {snippet}")
            return StrategyPlan(reasoning="Plan parsing failed — using defaults")

        tp_data = data.get("trade_policy") or {}
        trade_policy = TradePolicy(
            willing_to_give=list(tp_data.get("willing_to_give") or []),
            desperately_need=list(tp_data.get("desperately_need") or []),
            max_bank_ratio_acceptable=int(tp_data.get("max_bank_ratio_acceptable", 4) or 4),
            should_propose_trades=bool(tp_data.get("should_propose_trades", True)),
            min_accept_score=float(tp_data.get("min_accept_score", 0.5) or 0.5),
        )
        return StrategyPlan(
            long_term_goal=str(data.get("long_term_goal", "balanced")),
            short_term_goals=list(data.get("short_term_goals") or []),
            priority_resources=list(data.get("priority_resources") or []),
            build_queue=list(data.get("build_queue") or []),
            trade_policy=trade_policy,
            should_trade_first=bool(data.get("should_trade_first", True)),
            risk_tolerance=str(data.get("risk_tolerance", "moderate")),
            reasoning=str(data.get("reasoning", "")),
        )

    @staticmethod
    def _plan_submission_tool_schema() -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "submit_plan",
                "description": "Return the finalized strategy plan in structured JSON.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "long_term_goal": {"type": "string"},
                        "short_term_goals": {"type": "array", "items": {"type": "string"}},
                        "priority_resources": {"type": "array", "items": {"type": "string"}},
                        "build_queue": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "target": {"type": ["string", "null"]},
                                    "priority": {"type": "integer"},
                                },
                                "required": ["action"],
                            },
                        },
                        "trade_policy": {
                            "type": "object",
                            "properties": {
                                "willing_to_give": {"type": "array", "items": {"type": "string"}},
                                "desperately_need": {"type": "array", "items": {"type": "string"}},
                                "max_bank_ratio_acceptable": {"type": "integer"},
                                "should_propose_trades": {"type": "boolean"},
                                "min_accept_score": {"type": "number"},
                            },
                        },
                        "should_trade_first": {"type": "boolean"},
                        "risk_tolerance": {"type": "string"},
                        "reasoning": {"type": "string"},
                    },
                    "required": [
                        "long_term_goal",
                        "short_term_goals",
                        "priority_resources",
                        "build_queue",
                        "trade_policy",
                        "should_trade_first",
                        "risk_tolerance",
                        "reasoning",
                    ],
                },
            },
        }

    def _extract_plan_text_from_response(self, response: Any) -> str:
        """Prefer structured submit_plan tool args; fallback to plain text."""
        try:
            calls = self.openai.extract_tool_calls(response)
            for call in calls:
                if call.get("name") != "submit_plan":
                    continue
                args = call.get("arguments")
                if isinstance(args, dict) and args:
                    return json.dumps(args, default=str)
        except Exception:
            pass
        return self.openai.extract_text(response)

    def _retry_plan_json(self, context: str, previous_output: Any) -> str:
        """
        Ask the model for a strict JSON-only restatement of the plan.
        This recovers from empty/prose/truncated first-pass outputs.
        """
        prev_text = previous_output if isinstance(previous_output, str) else json.dumps(previous_output, default=str)
        retry_messages = [
            {
                "role": "system",
                "content": (
                    "Return the plan by calling submit_plan exactly once. "
                    "Do not output prose."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Convert the previous output into a valid plan now.\n\n"
                    f"Original context:\n{context}\n\n"
                    f"Previous invalid output:\n{prev_text}"
                ),
            },
        ]
        try:
            response = self.openai.chat_with_tools(
                retry_messages,
                tools=[self._plan_submission_tool_schema()],
            )
            usage = self.openai.extract_usage(response)
            self.stats.record_llm_call(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                model=self.openai.model,
            )
            text = self._extract_plan_text_from_response(response)
            if text:
                return text
            print("  [strategy] Plan JSON retry produced empty output")
        except Exception as e:
            print(f"  [strategy] Plan JSON retry failed: {e}")
        return ""

    @staticmethod
    def _extract_first_json_dict(text: str) -> Optional[Dict[str, Any]]:
        """
        Best-effort JSON object extraction from LLM output.
        Handles:
        - pure JSON
        - fenced ```json ... ```
        - leading/trailing prose with embedded JSON
        """
        if not isinstance(text, str):
            return None
        s = text.strip()
        if not s:
            return None

        # Strip a top-level fenced code block if present.
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", s, flags=re.IGNORECASE | re.DOTALL)
        if fence_match:
            s = fence_match.group(1).strip()

        # Fast path: whole-string JSON.
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                return obj[0]
        except Exception:
            pass

        # Lenient path: Python-literal dicts/lists (single quotes, True/False/None).
        try:
            lit = ast.literal_eval(s)
            if isinstance(lit, dict):
                return lit
            if isinstance(lit, list) and lit and isinstance(lit[0], dict):
                return lit[0]
        except Exception:
            pass

        # Robust path: scan for first decodable JSON object in the text.
        decoder = json.JSONDecoder()
        for i, ch in enumerate(s):
            if ch != "{":
                continue
            try:
                obj, _end = decoder.raw_decode(s[i:])
            except Exception:
                continue
            if isinstance(obj, dict):
                return obj
            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                return obj[0]

        # Fallback: brace-balanced substring + lenient literal eval.
        depth = 0
        start = -1
        in_string = False
        quote_char = ""
        escape = False
        for i, ch in enumerate(s):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == quote_char:
                    in_string = False
                continue
            if ch in {"'", '"'}:
                in_string = True
                quote_char = ch
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        candidate = s[start:i + 1]
                        try:
                            lit = ast.literal_eval(candidate)
                            if isinstance(lit, dict):
                                return lit
                        except Exception:
                            continue
        return None

    # ──────────────────────────────────────────────────────────────
    # ReAct execution loop (main phase)
    # ──────────────────────────────────────────────────────────────

    def _run_react_loop(self, state: Dict[str, Any]) -> None:
        """
        Bounded tool-calling loop where Strategy executes the plan.

        The loop terminates when:
          - The LLM calls ``end_turn``; OR
          - The step budget is exhausted (Strategy forcibly ends the turn).

        Between steps we inject ``[SURPRISE]`` system messages when:
          - A delegated build had failures;
          - A delegated trade failed or returned with a pending counter;
          - An opponent crossed the 8 VP threshold during the turn;
          - A new incoming trade offer appeared.
        """
        react_tools = self._build_react_toolset()
        plan_dict = asdict(self.scratchpad.strategy_plan)

        turn_meta = {
            "turn_number": self._turn_counter,
            "turn_phase": state.get("turnPhase", "main"),
            "is_my_turn": True,
            "my_name": self.player_name,
        }

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": STRATEGY_REACT_SYSTEM_PROMPT},
            {"role": "user", "content": build_react_kickoff_context(turn_meta, plan_dict)},
        ]

        pre_state = self.client.latest_state() or state
        ended = False

        for step in range(MAX_REACT_STEPS):
            # Nudge towards termination as the budget closes
            if step == MAX_REACT_STEPS - 1:
                messages.append({
                    "role": "system",
                    "content": "You are on the LAST allowed step. Call end_turn now.",
                })

            try:
                response = self.openai.chat_with_tools(messages, tools=react_tools)
            except Exception as e:
                print(f"  [strategy] LLM call failed mid-ReAct: {e}")
                break

            usage = self.openai.extract_usage(response)
            self.stats.record_llm_call(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                model=self.openai.model,
            )

            # Echo any text reasoning
            text = self.openai.extract_text(response)
            if text:
                print(f"  [thought] {text}")

            assistant_msg = response.choices[0].message
            messages.append(self._assistant_msg_to_dict(assistant_msg))

            calls = self.openai.extract_tool_calls(response)
            if not calls:
                messages.append({
                    "role": "user",
                    "content": "Please continue by calling a tool. "
                               "If the turn is complete, call end_turn.",
                })
                continue

            for call in calls:
                name = call.get("name", "")
                args = call.get("arguments") or {}
                call_id = call.get("id", "")

                print(f"  [action] {name}({json.dumps(args, default=str)})")
                result = self._dispatch_tool_call(name, args)

                # Record registry-backed actions in the action log
                if not name.startswith(("get_", "ask_", "analyze_", "delegate_")):
                    ok = bool(isinstance(result, dict) and result.get("success", True))
                    self.stats.record_tool_call(name, success=ok)
                    self.scratchpad.append_action(ActionRecord(
                        agent="strategy", action=name,
                        args=args,
                        result=result if isinstance(result, dict) else {"raw": result},
                        success=ok,
                    ))

                messages.append(self.openai.build_tool_result_message(call_id, result))

                # Surprise detection based on state BEFORE/AFTER this call
                post_state = self.client.latest_state() or pre_state
                surprises = self._detect_surprises(name, result, pre_state, post_state)
                for s in surprises:
                    print(f"  [surprise] {s}")
                    messages.append({
                        "role": "system",
                        "content": f"[SURPRISE] {s}. Reconsider your plan before the next tool call.",
                    })
                pre_state = post_state

                if name == "end_turn":
                    ended = True
                    break

            if ended:
                break

            # Keep scratchpad in sync for readers
            self.scratchpad.update_game_state(pre_state, self.processor)

        if not ended:
            print("  [strategy] ReAct budget exhausted — forcing end_turn")
            forced = self.registry.execute("end_turn", {})
            ok = bool(isinstance(forced, dict) and forced.get("success", True))
            self.stats.record_tool_call("end_turn", success=ok)
            self.scratchpad.append_action(ActionRecord(
                agent="strategy", action="end_turn",
                args={}, result=forced if isinstance(forced, dict) else {"raw": forced},
                success=ok,
            ))

    def _build_react_toolset(self) -> List[Dict[str, Any]]:
        """Combine registry tools (filtered for strategy/main) with meta-tools."""
        registry_tools = self.registry.get_openai_schemas(
            agent_filter="strategy",
            phase_filter="main",
        )
        # roll_dice should never appear in the ReAct loop
        registry_tools = [
            t for t in registry_tools
            if t.get("function", {}).get("name") != "roll_dice"
        ]
        return registry_tools + STRATEGY_META_TOOL_SCHEMAS

    @staticmethod
    def _assistant_msg_to_dict(msg: Any) -> Dict[str, Any]:
        """Convert an SDK assistant message to the OpenAI chat-completions dict form."""
        tool_calls = []
        if getattr(msg, "tool_calls", None):
            for tc in msg.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })
        out: Dict[str, Any] = {
            "role": "assistant",
            "content": getattr(msg, "content", None) or "",
        }
        if tool_calls:
            out["tool_calls"] = tool_calls
        return out

    # ──────────────────────────────────────────────────────────────
    # Tool dispatch (meta-tools + registry tools)
    # ──────────────────────────────────────────────────────────────

    def _dispatch_tool_call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "get_state":                  self._meta_get_state,
            "get_plan":                   self._meta_get_plan,
            "get_risk_analysis":          self._meta_get_risk_analysis,
            "get_round_summary":          self._meta_get_round_summary,
            "get_action_log":             self._meta_get_action_log,
            "get_inter_agent_messages":   self._meta_get_messages,
            "analyze_risk":               self._meta_analyze_risk,
            "ask_risk":                   self._meta_ask_risk,
            "ask_development":            self._meta_ask_development,
            "delegate_build":             self._meta_delegate_build,
            "delegate_trade":             self._meta_delegate_trade,
        }
        if name in handlers:
            try:
                return handlers[name](args or {})
            except Exception as e:
                return {"success": False, "error": f"{name} failed: {e}"}

        # Fall through to the registry
        return self.registry.execute(name, args or {})

    # ── Meta: scratchpad readers ──────────────────────────────────

    def _meta_get_state(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "state": self.scratchpad.to_state_json()}

    def _meta_get_plan(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "plan": asdict(self.scratchpad.strategy_plan)}

    def _meta_get_risk_analysis(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        ra = self.scratchpad.risk_analysis
        return {
            "success": True,
            "fresh": ra.updated_at > 0,
            "risk_analysis": asdict(ra),
        }

    def _meta_get_round_summary(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "round_summary": asdict(self.scratchpad.round_summary)}

    def _meta_get_action_log(self, args: Dict[str, Any]) -> Dict[str, Any]:
        limit = int(args.get("limit", 15) or 15)
        limit = max(1, min(50, limit))
        log = [asdict(a) for a in self.scratchpad.action_log[-limit:]]
        return {"success": True, "count": len(log), "actions": log}

    def _meta_get_messages(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        msgs = [asdict(m) for m in self.get_messages()][-10:]
        return {"success": True, "count": len(msgs), "messages": msgs}

    # ── Meta: consultants & delegations ───────────────────────────

    def _meta_analyze_risk(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        ra = self.call_agent("risk", "analyze")
        return {
            "success": True,
            "narrative": getattr(ra, "threat_narrative", ""),
            "income": getattr(ra, "resource_expected_income", {}),
            "top_threat": (
                ra.opponent_threats[0] if getattr(ra, "opponent_threats", None) else {}
            ),
        }

    def _meta_ask_risk(self, args: Dict[str, Any]) -> Dict[str, Any]:
        question = str(args.get("question") or "").strip()
        if not question:
            return {"success": False, "error": "question is required"}
        answer = self.call_agent("risk", "consult", question=question)
        return {"success": True, "answer": answer}

    def _meta_ask_development(self, args: Dict[str, Any]) -> Dict[str, Any]:
        question = str(args.get("question") or "").strip()
        if not question:
            return {"success": False, "error": "question is required"}
        answer = self.call_agent("development", "consult", question=question)
        return {"success": True, "answer": answer}

    @staticmethod
    def _normalize_build_action_name(item: Dict[str, Any]) -> str:
        raw = item.get("action") or item.get("tool") or item.get("type")
        if raw is None:
            return ""
        key = str(raw).strip().lower().replace("-", "_")
        aliases = {
            "placesettlement": "place_settlement",
            "place_road": "place_road",
            "placeroad": "place_road",
            "upgrade_to_city": "upgrade_to_city",
            "upgradetocity": "upgrade_to_city",
            "buy_dev_card": "buy_dev_card",
            "buydevcard": "buy_dev_card",
            "play_dev_card": "play_dev_card",
            "playdevcard": "play_dev_card",
        }
        return aliases.get(key, key)

    @staticmethod
    def _build_target_from_item(item: Dict[str, Any], *keys: str) -> Optional[str]:
        target = item.get("target")
        if target:
            return str(target)
        extra = item.get("args") if isinstance(item.get("args"), dict) else {}
        for key in keys:
            if extra.get(key):
                return str(extra[key])
        return None

    def _validate_build_queue(
        self, queue: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Drop queue entries with targets that are certainly invalid.

        This intentionally checks only facts visible in the current board
        snapshot. The server remains the authority for distance-rule and
        connectivity legality.
        """
        state = self.client.latest_state() or self.scratchpad.game_state or {}
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        myi = state.get("myIndex")

        valid: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []

        def reject(item: Dict[str, Any], action: str, target: Optional[str], reason: str) -> None:
            errors.append({
                "action": action or str(item.get("action") or item.get("tool") or item.get("type") or ""),
                "target": target,
                "reason": reason,
                "item": item,
            })

        for item in queue:
            if not isinstance(item, dict):
                reject({"raw": item}, "", None, "queue item is not an object")
                continue

            action = self._normalize_build_action_name(item)

            if action in {"buy_dev_card", "play_dev_card"}:
                valid.append(item)
                continue

            if action == "place_settlement":
                target = self._build_target_from_item(item, "vertex_key")
                if not target:
                    reject(item, action, None, "missing settlement vertex target")
                    continue
                vertex = vertices.get(target)
                if not isinstance(vertex, dict):
                    reject(item, action, target, "vertex not on board")
                    continue
                if vertex.get("owner") is not None or vertex.get("building"):
                    reject(item, action, target, "vertex already occupied")
                    continue
                valid.append(item)
                continue

            if action == "upgrade_to_city":
                target = self._build_target_from_item(item, "vertex_key")
                if not target:
                    reject(item, action, None, "missing city vertex target")
                    continue
                vertex = vertices.get(target)
                if not isinstance(vertex, dict):
                    reject(item, action, target, "vertex not on board")
                    continue
                if vertex.get("owner") != myi or vertex.get("building") != "settlement":
                    reject(item, action, target, "target is not our settlement")
                    continue
                valid.append(item)
                continue

            if action == "place_road":
                target = self._build_target_from_item(item, "edge_key")
                if not target:
                    reject(item, action, None, "missing road edge target")
                    continue
                edge = edges.get(target)
                if not isinstance(edge, dict):
                    reject(item, action, target, "edge not on board")
                    continue
                if edge.get("owner") is not None:
                    reject(item, action, target, "edge already occupied")
                    continue
                valid.append(item)
                continue

            reject(item, action, self._build_target_from_item(item, "vertex_key", "edge_key"), "unknown build action")

        return valid, errors

    def _meta_delegate_build(self, args: Dict[str, Any]) -> Dict[str, Any]:
        queue = args.get("queue")
        action = args.get("action")

        plan = self.scratchpad.strategy_plan
        original_queue = list(plan.build_queue or [])

        if isinstance(queue, list):
            temp_queue: List[Dict[str, Any]] = [q for q in queue if isinstance(q, dict)]
        elif isinstance(action, dict):
            temp_queue = [action]
        else:
            temp_queue = list(original_queue)

        temp_queue, validation_errors = self._validate_build_queue(temp_queue)
        if validation_errors and not temp_queue:
            return {
                "success": False,
                "error": "all build queue targets invalid",
                "results": [],
                "validation_errors": validation_errors,
            }

        plan.build_queue = temp_queue
        try:
            results = self.call_agent("development", "execute_build_queue")
        except Exception as e:
            plan.build_queue = original_queue
            return {
                "success": False,
                "error": str(e),
                "results": [],
                "validation_errors": validation_errors,
            }
        plan.build_queue = original_queue

        serialized = []
        all_ok = True
        for r in results or []:
            ok = bool(getattr(r, "success", False))
            all_ok = all_ok and ok
            serialized.append({
                "action": getattr(r, "action", ""),
                "args": getattr(r, "args", {}),
                "success": ok,
                "error": (getattr(r, "result", {}) or {}).get("error"),
            })

        return {
            "success": all_ok and bool(serialized),
            "count": len(serialized),
            "results": serialized,
            "validation_errors": validation_errors,
        }

    def _meta_delegate_trade(self, _args: Dict[str, Any]) -> Dict[str, Any]:
        """Non-blocking call — Trading returns after one bank trade/proposal/no-op."""
        pre_trades = len(self.scratchpad.trade_state.recent_trades or [])
        self._last_trade_result = None
        try:
            self.call_agent("trading", "proactive_trade")
        except Exception as e:
            return {"success": False, "error": str(e)}
        post_trades = len(self.scratchpad.trade_state.recent_trades or [])
        completed = post_trades - pre_trades
        trade_result = dict(self._last_trade_result or {})
        pending = self.scratchpad.trade_state.pending_offer
        return {
            "success": completed > 0 or bool(pending) or self._trade_result_indicates_action(trade_result),
            "trades_completed_this_delegation": max(0, completed),
            "pending_offer": pending,
            "trade_result": trade_result,
        }

    @staticmethod
    def _trade_result_indicates_action(result: Dict[str, Any]) -> bool:
        executed = result.get("executed")
        if isinstance(executed, list):
            for step in executed:
                if not isinstance(step, dict):
                    continue
                if step.get("status") == "pending_player_response":
                    return True
                if step.get("type") in {"bank_trade", "player_trade"}:
                    tool_result = step.get("result") if isinstance(step.get("result"), dict) else {}
                    if bool(tool_result.get("success", False)):
                        return True
                    continue
                if step.get("name") not in {"bank_trade", "propose_trade"}:
                    continue
                tool_result = step.get("result") if isinstance(step.get("result"), dict) else {}
                if bool(tool_result.get("success", False)):
                    return True
        return result.get("status") in {"pending_player_response", "resolved_outcome_unknown"}

    # ──────────────────────────────────────────────────────────────
    # Surprise detection
    # ──────────────────────────────────────────────────────────────

    def _detect_surprises(
        self,
        call_name: str,
        call_result: Dict[str, Any],
        pre_state: Dict[str, Any],
        post_state: Dict[str, Any],
    ) -> List[str]:
        surprises: List[str] = []

        if call_name == "delegate_build":
            if isinstance(call_result, dict):
                validation_errors = [
                    e for e in (call_result.get("validation_errors") or [])
                    if isinstance(e, dict)
                ]
                if validation_errors:
                    examples = ", ".join(
                        f"{e.get('target')} ({e.get('action')}: {e.get('reason')})"
                        for e in validation_errors[:3]
                    )
                    surprises.append(
                        f"delegate_build rejected invalid targets: {examples}"
                    )
                failed = [
                    r for r in (call_result.get("results") or [])
                    if isinstance(r, dict) and not r.get("success")
                ]
                if failed:
                    names = ", ".join(str(r.get("action")) for r in failed[:3])
                    surprises.append(
                        f"delegate_build failed on: {names} (out of {len(call_result.get('results') or [])})"
                    )

        if call_name == "delegate_trade":
            if isinstance(call_result, dict) and not call_result.get("success"):
                surprises.append("delegate_trade completed no trades this round")

        # Opponent crosses 8 VP
        prev_max_name, prev_max_vp = self._max_opponent_vp(pre_state)
        curr_max_name, curr_max_vp = self._max_opponent_vp(post_state)
        if curr_max_vp >= 8 and prev_max_vp < 8:
            surprises.append(
                f"Opponent '{curr_max_name}' reached {curr_max_vp} VP — critical threat"
            )

        # New incoming trade offer appeared
        if (not self._has_trade_offer(pre_state)) and self._has_trade_offer(post_state):
            surprises.append(
                "A trade offer for us just appeared — consider delegate_trade or let it pass"
            )

        return surprises

    @staticmethod
    def _max_opponent_vp(state: Dict[str, Any]) -> Tuple[str, int]:
        players = state.get("players") if isinstance(state, dict) else None
        myi = state.get("myIndex") if isinstance(state, dict) else None
        if not isinstance(players, list) or not isinstance(myi, int):
            return ("", 0)
        best_name, best_vp = "", 0
        for i, p in enumerate(players):
            if i == myi or not isinstance(p, dict):
                continue
            vp = int(p.get("victoryPoints", 0) or 0)
            if vp > best_vp:
                best_vp = vp
                best_name = str(p.get("name", f"Player{i}"))
        return (best_name, best_vp)

    @staticmethod
    def _has_trade_offer(state: Dict[str, Any]) -> bool:
        return TradingAgent.has_incoming_offer_for_me(state)

    # ──────────────────────────────────────────────────────────────
    # Round summary (diff from end of previous turn)
    # ──────────────────────────────────────────────────────────────

    def _snapshot_for_diff(self, state: Dict[str, Any]) -> Dict[str, Any]:
        players = state.get("players") or []
        vp_by_player: Dict[str, int] = {}
        buildings_by_player: Dict[str, List[Dict[str, Any]]] = {}

        for i, p in enumerate(players):
            if not isinstance(p, dict):
                continue
            name = str(p.get("name", f"Player{i}"))
            vp_by_player[name] = int(p.get("victoryPoints", 0) or 0)
            buildings_by_player[name] = []

        vertices = state.get("vertices") or {}
        for vk, v in vertices.items():
            if not isinstance(v, dict):
                continue
            owner = v.get("owner")
            if owner is None:
                continue
            try:
                owner_idx = int(owner)
            except (TypeError, ValueError):
                continue
            if 0 <= owner_idx < len(players) and isinstance(players[owner_idx], dict):
                name = str(players[owner_idx].get("name", f"Player{owner_idx}"))
                buildings_by_player.setdefault(name, []).append({
                    "type": v.get("building"),
                    "vertex": vk,
                })

        return {
            "turn_number": self._turn_counter,
            "vp_by_player": vp_by_player,
            "buildings_by_player": buildings_by_player,
            "robber_hex": state.get("robber"),
            "longest_road": {
                "holder": state.get("longestRoadPlayer"),
                "length": state.get("longestRoadLength", 0) or 0,
            },
            "largest_army": {
                "holder": state.get("largestArmyPlayer"),
                "size": state.get("largestArmySize", 0) or 0,
            },
            "trade_highwater": self._trade_highwater,
        }

    def _compute_round_summary(self, state: Dict[str, Any]) -> RoundSummary:
        prev = self._last_turn_snapshot
        curr = self._snapshot_for_diff(state)

        if not prev:
            return RoundSummary(
                turn_number=self._turn_counter,
                notes=["No prior snapshot — first playing turn."],
            )

        vp_deltas: Dict[str, int] = {}
        for name, vp in curr["vp_by_player"].items():
            delta = vp - int(prev["vp_by_player"].get(name, 0))
            if delta != 0:
                vp_deltas[name] = delta

        new_buildings: Dict[str, List[Dict[str, Any]]] = {}
        for name, cur_bs in curr["buildings_by_player"].items():
            prev_bs = prev["buildings_by_player"].get(name, [])
            prev_keys = {(b.get("type"), b.get("vertex")) for b in prev_bs}
            fresh = [
                b for b in cur_bs
                if (b.get("type"), b.get("vertex")) not in prev_keys
            ]
            if fresh:
                new_buildings[name] = fresh

        robber_moved = None
        if prev["robber_hex"] != curr["robber_hex"]:
            robber_moved = {
                "from": str(prev["robber_hex"]) if prev["robber_hex"] else "",
                "to": str(curr["robber_hex"]) if curr["robber_hex"] else "",
            }

        longest_road_change = None
        if prev["longest_road"] != curr["longest_road"]:
            longest_road_change = {
                "from": prev["longest_road"].get("holder"),
                "to": curr["longest_road"].get("holder"),
                "length": curr["longest_road"].get("length", 0),
            }

        largest_army_change = None
        if prev["largest_army"] != curr["largest_army"]:
            largest_army_change = {
                "from": prev["largest_army"].get("holder"),
                "to": curr["largest_army"].get("holder"),
                "size": curr["largest_army"].get("size", 0),
            }

        # Trades completed for us since last snapshot (if Trading tracked any)
        completed_trades: List[Dict[str, Any]] = []
        prev_hw = float(prev.get("trade_highwater", 0.0) or 0.0)
        recent = self.scratchpad.trade_state.recent_trades or []
        for t in recent:
            ts = float((t or {}).get("timestamp", 0.0) or 0.0)
            if ts > prev_hw:
                completed_trades.append(t)
        if recent:
            self._trade_highwater = max(
                self._trade_highwater,
                max((float(t.get("timestamp", 0.0) or 0.0) for t in recent), default=0.0),
            )

        return RoundSummary(
            turn_number=self._turn_counter,
            vp_deltas=vp_deltas,
            new_buildings=new_buildings,
            robber_moved=robber_moved,
            longest_road_change=longest_road_change,
            largest_army_change=largest_army_change,
            completed_trades=completed_trades,
        )

    @staticmethod
    def _short_diff(summary: RoundSummary) -> str:
        parts: List[str] = []
        if summary.vp_deltas:
            parts.append("VP:" + ",".join(
                f"{k}{'+' if v > 0 else ''}{v}" for k, v in summary.vp_deltas.items()
            ))
        if summary.new_buildings:
            b = sum(len(v) for v in summary.new_buildings.values())
            parts.append(f"new_buildings={b}")
        if summary.robber_moved:
            parts.append(f"robber:{summary.robber_moved['from']}->{summary.robber_moved['to']}")
        if summary.longest_road_change:
            parts.append("longest_road_changed")
        if summary.largest_army_change:
            parts.append("largest_army_changed")
        return " | ".join(parts) or "(no visible changes)"

    # ──────────────────────────────────────────────────────────────
    # Off-turn reactive handling
    # ──────────────────────────────────────────────────────────────

    def _handle_offturn_discard_if_needed(self, state: Dict[str, Any]) -> None:
        """
        During a 7 discard window, non-current players may still be required to act.
        Wake up and discard immediately when our player appears in discardingPlayers.
        """
        requirement = self._get_discard_requirement(state)
        if requirement is None:
            self._last_discard_sig = None
            return

        players = state.get("players")
        myi = state.get("myIndex")
        me: Dict[str, Any] = {}
        if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players):
            maybe_me = players[myi]
            if isinstance(maybe_me, dict):
                me = maybe_me
        hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}
        req_sig = json.dumps(
            {"cards_to_discard": requirement, "hand": hand},
            sort_keys=True,
            default=str,
        )
        if req_sig == self._last_discard_sig:
            return

        actions = _build_discard_action(state)
        if not actions:
            print("  [strategy-awake] discard required but no valid discard payload computed")
            self._last_discard_sig = req_sig
            return
        payload = actions[0].payload if isinstance(actions[0].payload, dict) else {}
        resources = payload.get("resources") if isinstance(payload.get("resources"), dict) else {}
        if not resources:
            print("  [strategy-awake] discard required but discard payload is empty")
            self._last_discard_sig = req_sig
            return

        print(f"  [strategy-awake] discard required ({requirement}) -> {resources}")
        result = self._safe_call("discardCards", {"resources": resources})
        success = bool(isinstance(result, dict) and result.get("success"))
        self.stats.record_tool_call("discardCards", success=success)
        self.scratchpad.append_action(ActionRecord(
            agent="strategy",
            action="discardCards",
            args={"resources": resources},
            result=result if isinstance(result, dict) else {"raw": result},
            success=success,
        ))
        if success:
            print("  [strategy-awake] discard=done")
            self._last_discard_sig = None
        else:
            print(f"  [strategy-awake] discard=failed error={result}")
            self._last_discard_sig = req_sig

    @staticmethod
    def _get_discard_requirement(state: Dict[str, Any]) -> Optional[int]:
        if not isinstance(state, dict):
            return None
        if state.get("turnPhase") != "discard":
            return None
        discarding = state.get("discardingPlayers")
        myi = state.get("myIndex")
        if not isinstance(discarding, list) or not isinstance(myi, int):
            return None
        entry = next(
            (d for d in discarding if isinstance(d, dict) and d.get("playerIndex") == myi),
            None,
        )
        if not isinstance(entry, dict):
            return None
        try:
            cards_to_discard = int(entry.get("cardsToDiscard", 0) or 0)
        except Exception:
            return None
        return cards_to_discard if cards_to_discard > 0 else None

    def _check_reactive_trades(self, state: Dict[str, Any]) -> None:
        if not state:
            return
        self._check_pending_trade_offer()
        if self._has_trade_offer(state):
            self.scratchpad.update_game_state(state, self.processor)
            try:
                self.call_agent("trading", "respond_to_offer")
            except Exception as e:
                print(f"  [strategy] Reactive trade failed: {e}")

    def _check_pending_trade_offer(self) -> None:
        pending = self.scratchpad.trade_state.pending_offer
        if not pending:
            return
        try:
            result = self.call_agent("trading", "check_pending_offer")
        except Exception as e:
            print(f"  [strategy] Pending trade check failed: {e}")
            return
        if isinstance(result, dict) and result.get("status") != "pending_player_response":
            print(f"  [strategy] Pending trade status: {result}")

    # ──────────────────────────────────────────────────────────────
    # Methods callable by peer agents
    # ──────────────────────────────────────────────────────────────

    def get_trade_policy(self) -> TradePolicy:
        """Called by Trading Agent to read current trade guidance."""
        return self.scratchpad.strategy_plan.trade_policy

    def report_build_results(self, results: List[ActionRecord]) -> None:
        successes = sum(1 for r in results if r.success)
        failures = len(results) - successes
        print(f"  [strategy] Build results: {successes} succeeded, {failures} failed")

    def report_trade_results(self, results: Dict[str, Any]) -> None:
        self._last_trade_result = dict(results or {})
        print(f"  [strategy] Trade results: {results}")

    # ──────────────────────────────────────────────────────────────
    # Setup phase (one LLM call per setup turn + heuristic fallback)
    # ──────────────────────────────────────────────────────────────

    def _handle_setup(self, state: Dict[str, Any]) -> None:
        """
        Place 1 settlement + 1 adjacent road per setup turn.

        Preferred path: a single LLM call picks both.  If that fails or the
        server rejects the picks, we fall back to the EV-ranked heuristic
        used in the legacy code.
        """
        self._turn_counter += 1
        self.stats.start_turn(self._turn_counter, phase="setup")
        self.scratchpad.update_game_state(state, self.processor)

        # If a settlement was placed last tick but the road wasn't, finish it.
        if self._last_setup_settlement is not None:
            self._setup_place_road(state, self._last_setup_settlement)
            self.stats.end_turn()
            return

        # Try LLM placement first
        placement = None
        try:
            placement = self._llm_pick_setup_placement(state)
        except Exception as e:
            print(f"  [setup] LLM placement errored: {e}")

        placed_settlement: Optional[str] = None
        placed_road: bool = False

        if placement:
            vk = placement.get("settlement_vertex")
            ek = placement.get("road_edge")
            if isinstance(vk, str) and isinstance(ek, str):
                resp1 = self._safe_call("placeSettlement", {"vertexKey": vk, "isSetup": True})
                if isinstance(resp1, dict) and resp1.get("success"):
                    placed_settlement = vk
                    print(f"  [setup] settlement at {vk} (LLM)")
                    self.stats.record_tool_call("placeSettlement", success=True)
                    self.scratchpad.append_action(ActionRecord(
                        agent="strategy", action="placeSettlement",
                        args={"vertexKey": vk, "isSetup": True},
                        result=resp1, success=True,
                    ))
                    resp2 = self._safe_call("placeRoad", {
                        "edgeKey": ek, "isSetup": True, "lastSettlement": vk,
                    })
                    if isinstance(resp2, dict) and resp2.get("success"):
                        print(f"  [setup] road at {ek} (LLM)")
                        self.stats.record_tool_call("placeRoad", success=True)
                        self.scratchpad.append_action(ActionRecord(
                            agent="strategy", action="placeRoad",
                            args={"edgeKey": ek, "isSetup": True},
                            result=resp2, success=True,
                        ))
                        placed_road = True
                        self._advance_setup()
                        self._last_setup_settlement = None
                        self._setup_placements_done += 1
                        self.stats.end_turn()
                        return
                    else:
                        print(f"  [setup] LLM road rejected: {resp2}")
                        self.stats.record_tool_call("placeRoad", success=False)
                else:
                    print(f"  [setup] LLM settlement rejected: {resp1}")
                    self.stats.record_tool_call("placeSettlement", success=False)

        # Fallback: heuristic path
        if placed_settlement is None:
            self._setup_place_settlement_heuristic(state)
        elif not placed_road:
            self._setup_place_road(state, placed_settlement)

        self.stats.end_turn()

    def _llm_pick_setup_placement(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Single LLM call: pick a settlement vertex + adjacent road edge."""
        ranked_vs = _ranked_setup_settlements(state, top_k=10)
        candidates: List[Dict[str, Any]] = []
        edges_by_vertex: Dict[str, List[str]] = {}
        for vk in ranked_vs:
            prod = self.processor._vertex_production(vk, state.get("hexes") or {})
            candidates.append({"vertex": vk, "production": prod})
            edges_by_vertex[vk] = _ranked_setup_roads(state, vk, top_k=6)

        messages = [
            {"role": "system", "content": STRATEGY_SETUP_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": build_setup_context(
                    state_json=self.scratchpad.to_state_json(),
                    candidate_vertices=candidates,
                    candidate_edges_by_vertex=edges_by_vertex,
                    risk_analysis=asdict(self.scratchpad.risk_analysis),
                ),
            },
        ]

        response = self.openai.chat_with_tools(messages, tools=None)
        usage = self.openai.extract_usage(response)
        self.stats.record_llm_call(
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            model=self.openai.model,
        )

        text = self.openai.extract_text(response)
        data = self._extract_first_json_dict(text)

        if not isinstance(data, dict):
            snippet = (text or "").strip().replace("\n", "\\n")
            if snippet:
                print(f"  [setup] Could not parse LLM setup JSON: {snippet}")
            return None

        vk = data.get("settlement_vertex")
        ek = data.get("road_edge")
        if not (isinstance(vk, str) and isinstance(ek, str)):
            return None

        # Validate the LLM stayed within the provided candidate lists
        if vk not in edges_by_vertex:
            return None
        if ek not in edges_by_vertex[vk]:
            return None

        print(f"  [setup] LLM picked settlement={vk}, road={ek}")
        return {"settlement_vertex": vk, "road_edge": ek}

    def _setup_place_settlement_heuristic(self, state: Dict[str, Any]) -> None:
        ranked = _ranked_setup_settlements(state, top_k=120)
        for vk in ranked:
            resp = self._safe_call("placeSettlement", {
                "vertexKey": vk, "isSetup": True,
            })
            if isinstance(resp, dict) and resp.get("success"):
                self._last_setup_settlement = vk
                print(f"  [setup] settlement at {vk} (heuristic)")
                self.stats.record_tool_call("placeSettlement", success=True)
                self.scratchpad.append_action(ActionRecord(
                    agent="strategy", action="placeSettlement",
                    args={"vertexKey": vk, "isSetup": True},
                    result=resp, success=True,
                ))
                return
            self.stats.record_tool_call("placeSettlement", success=False)
        print("  [setup] no legal settlement found (heuristic)")

    def _setup_place_road(self, state: Dict[str, Any], settlement_vk: str) -> None:
        ranked_edges = _ranked_setup_roads(state, settlement_vk, top_k=200)
        for ek in ranked_edges:
            resp = self._safe_call("placeRoad", {
                "edgeKey": ek, "isSetup": True, "lastSettlement": settlement_vk,
            })
            if isinstance(resp, dict) and resp.get("success"):
                print(f"  [setup] road at {ek} (heuristic)")
                self.stats.record_tool_call("placeRoad", success=True)
                self.scratchpad.append_action(ActionRecord(
                    agent="strategy", action="placeRoad",
                    args={"edgeKey": ek, "isSetup": True},
                    result=resp, success=True,
                ))
                self._advance_setup()
                self._last_setup_settlement = None
                self._setup_placements_done += 1
                return
            self.stats.record_tool_call("placeRoad", success=False)
        print("  [setup] no legal road found (heuristic)")

    def _advance_setup(self) -> None:
        adv = self._safe_call("advanceSetup")
        ok = bool(isinstance(adv, dict) and adv.get("success"))
        self.stats.record_tool_call("advanceSetup", success=ok)
        print(f"  [setup] advanceSetup => {adv}")

    # ──────────────────────────────────────────────────────────────
    # Fallbacks (used only when Development delegation raises)
    # ──────────────────────────────────────────────────────────────

    def _fallback_discard(self, state: Dict[str, Any]) -> None:
        actions = _build_discard_action(state)
        if actions:
            a = actions[0]
            self._safe_call("discardCards", a.payload)
            print(f"  [fallback] discarded: {a.payload}")

    def _fallback_robber(self, state: Dict[str, Any]) -> None:
        hexes = list((state.get("hexes") or {}).keys())
        cur = state.get("robber")
        for hk in hexes:
            if hk != cur:
                self._safe_call("moveRobber", {
                    "hexKey": hk, "stealFromPlayerId": None,
                })
                print(f"  [fallback] moved robber to {hk}")
                break

    # ──────────────────────────────────────────────────────────────
    # Low-level helpers
    # ──────────────────────────────────────────────────────────────

    def _safe_call(
        self, event: str, payload: Optional[Dict[str, Any]] = None,
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
