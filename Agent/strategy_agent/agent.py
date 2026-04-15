"""
Strategy Agent — the brain and game loop owner for the multi-agent system.

Owns the main polling loop (run()), produces strategy plans via GPT-4o,
and delegates execution to Development/Trading agents.  Handles roll_dice
and end_turn directly since those are trivial control flow.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import (
    Scratchpad,
    StrategyPlan,
    TradePolicy,
    ActionRecord,
    RiskAnalysis,
)
from Agent.utils.socket_client import CatanSocketClient
from Agent.utils.game_state_processor import GameStateProcessor
from Agent.utils.openai_client import OpenAIClient
from Agent.utils.stats_tracker import AgentStatsTracker
from Agent.tools.registry import ToolRegistry
from Agent.trading_agent.agent import TradingAgent

from Agent.strategy_agent.prompts import (
    STRATEGY_SYSTEM_PROMPT,
    MAX_STRATEGY_STEPS,
    build_strategy_context,
)

# Heuristic helpers reused for setup & fallback
from Agent.tools.game_tools import (
    _ranked_setup_settlements,
    _ranked_setup_roads,
    _build_discard_action,
    is_my_turn,
)


class StrategyAgent(BaseAgent):
    """
    The brain of the multi-agent Catan system.

    Responsibilities:
      - Owns the main game loop (connect, join, poll)
      - Updates scratchpad with latest game state each tick
      - Calls Risk Agent for analysis at start of each turn
      - Produces a StrategyPlan via GPT-4o
      - Delegates building to Development, trading to Trading
      - Directly handles roll_dice and end_turn
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
        player_name: str = "StrategyBot",
    ) -> None:
        super().__init__("strategy", scratchpad)
        self.openai = openai
        self.client = client
        self.processor = processor
        self.registry = registry
        self.stats = stats
        self.game_code = game_code
        self.player_name = player_name
        self._turn_counter = 0

        # Setup tracking (reused from ReactCatanAgent pattern)
        self._last_setup_settlement: Optional[str] = None
        self._setup_placements_done = 0

    # ──────────────────────────────────────────────────────────────
    # Main game loop
    # ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Connect, join/create game, poll forever.  This IS the entry point."""
        self.client.connect()

        if self.game_code:
            self.client.join_game(self.game_code, self.player_name)
        else:
            ack = self.client.create_game(self.player_name)
            self.game_code = ack["gameCode"]
            print(f"Created game: {self.game_code}")

        print(f"✅ {self.player_name} multi-agent running (game {self.game_code})")

        try:
            while True:
                state = self.client.latest_state()
                if not state:
                    time.sleep(0.25)
                    continue

                if not is_my_turn(state):
                    self._check_reactive_trades(state)
                    time.sleep(0.25)
                    continue

                phase = state.get("phase")

                # ── SETUP (heuristic, no LLM) ─────────────────────
                if phase == "setup":
                    self._handle_setup(state)
                    time.sleep(0.15)
                    continue

                # ── PLAYING: multi-agent turn ─────────────────────
                self._run_turn(state)
                time.sleep(0.15)

        except KeyboardInterrupt:
            print("\n⏹  Strategy Agent interrupted.")
        except Exception as e:
            print(f"\n❌ Strategy Agent error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            paths = self.stats.export_all("./logs/Agent")
            print(f"📊 Stats saved:")
            print(f"   JSON: {paths['json']}")
            print(f"   CSV:  {paths['csv']}")

    # ──────────────────────────────────────────────────────────────
    # Turn execution
    # ──────────────────────────────────────────────────────────────

    def _run_turn(self, state: Dict[str, Any]) -> None:
        """One full turn: observe → risk → plan → delegate → end."""
        self._turn_counter += 1
        turn_phase = state.get("turnPhase", "main")
        self.stats.start_turn(self._turn_counter, phase=turn_phase)
        self.scratchpad.new_turn(self._turn_counter)
        self.scratchpad.update_game_state(state, self.processor)

        print(f"\n{'='*60}")
        print(f"  TURN {self._turn_counter}  |  phase={turn_phase}")
        print(f"{'='*60}")

        # 1. Ask Risk for analysis
        try:
            risk_result = self.call_agent("risk", "analyze")
            print(f"  [risk] Analysis complete")
        except Exception as e:
            print(f"  [risk] Analysis failed: {e}")

        # 2. Plan (GPT-4o)
        self._plan()

        # 3. Execute by phase
        turn_phase = state.get("turnPhase", "main")

        if turn_phase == "roll":
            print(f"  [strategy] Rolling dice...")
            result = self.registry.execute("roll_dice", {})
            self.stats.record_tool_call("roll_dice", success=bool(result.get("success", True)))
            self.scratchpad.append_action(ActionRecord(
                agent="strategy", action="roll_dice",
                args={}, result=result,
                success=bool(result.get("success", True)),
            ))

            # Wait for state to update after dice roll
            time.sleep(0.3)
            state = self.client.latest_state() or state
            self.scratchpad.update_game_state(state, self.processor)
            turn_phase = state.get("turnPhase", "main")
            print(f"  [strategy] After roll: turnPhase={turn_phase}")

        if turn_phase == "discard":
            print(f"  [strategy] Discard phase — delegating to Development")
            try:
                self.call_agent("development", "handle_discard")
            except Exception as e:
                print(f"  [strategy] Development discard failed: {e}")
                self._fallback_discard(state)
            self.stats.end_turn()
            return

        if turn_phase == "robber":
            print(f"  [strategy] Robber phase — delegating to Development")
            try:
                self.call_agent("development", "handle_robber")
            except Exception as e:
                print(f"  [strategy] Development robber failed: {e}")
                self._fallback_robber(state)
            self.stats.end_turn()
            return

        if turn_phase == "main":
            # a. Trading
            print(f"  [strategy] Main phase — delegating trades")
            try:
                self.call_agent("trading", "proactive_trade")
            except Exception as e:
                print(f"  [strategy] Trading failed: {e}")

            # b. Building
            print(f"  [strategy] Main phase — delegating builds")
            try:
                self.call_agent("development", "execute_build_queue")
            except Exception as e:
                print(f"  [strategy] Development build failed: {e}")

            # c. End turn
            print(f"  [strategy] Ending turn")
            result = self.registry.execute("end_turn", {})
            self.stats.record_tool_call("end_turn", success=bool(result.get("success", True)))
            self.scratchpad.append_action(ActionRecord(
                agent="strategy", action="end_turn",
                args={}, result=result,
                success=bool(result.get("success", True)),
            ))

        self.stats.end_turn()

    # ──────────────────────────────────────────────────────────────
    # GPT-4o planning
    # ──────────────────────────────────────────────────────────────

    def _plan(self) -> None:
        """Call GPT-4o to produce a StrategyPlan."""
        try:
            context = self._build_context()
            messages = [
                {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ]

            # Get query-only tools for Strategy
            tools = self.registry.get_openai_schemas(
                phase_filter=self.scratchpad.turn_phase,
                agent_filter="strategy",
            )

            response = self.openai.chat_with_tools(messages, tools=tools)

            # Record token usage
            usage = self.openai.extract_usage(response)
            self.stats.record_llm_call(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                model=self.openai.model,
            )

            # Parse the plan from response
            text = self.openai.extract_text(response)
            plan = self._parse_plan(text)
            self.scratchpad.write_strategy_plan(plan)
            print(f"  [strategy] Plan: goal={plan.long_term_goal}, "
                  f"tolerance={plan.risk_tolerance}, "
                  f"build_queue={len(plan.build_queue)} items")
            if plan.reasoning:
                print(f"  [strategy] Reasoning: {plan.reasoning[:150]}...")

        except Exception as e:
            print(f"  [strategy] Planning failed: {e}")
            # Keep the previous plan if planning fails
            import traceback
            traceback.print_exc()

    def _build_context(self) -> str:
        """Assemble game state + risk + plan + messages for GPT-4o."""
        state_json = self.scratchpad.to_state_json()
        risk_data = asdict(self.scratchpad.risk_analysis)
        prev_plan = asdict(self.scratchpad.strategy_plan)
        messages = [asdict(m) for m in self.get_messages()]

        # Get available building spots for context
        building_spots = {}
        try:
            building_spots = self.registry.execute("get_building_spots", {"building_type": "settlement"})
            city_spots = self.registry.execute("get_building_spots", {"building_type": "city"})
            building_spots = {
                "settlement_spots": building_spots.get("spots", [])[:5],
                "city_spots": city_spots.get("spots", [])[:5],
            }
        except Exception:
            pass

        return build_strategy_context(
            state_json=state_json,
            risk_analysis=risk_data,
            previous_plan=prev_plan,
            agent_messages=messages,
            building_spots=building_spots,
        )

    def _parse_plan(self, text: str) -> StrategyPlan:
        """Parse GPT-4o's response into a StrategyPlan dataclass."""
        # Try to extract JSON from the response
        data = None

        # Attempt direct parse
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

        # Attempt to extract JSON block from markdown
        if data is None and text:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass

        if not isinstance(data, dict):
            print(f"  [strategy] Could not parse plan, using defaults")
            return StrategyPlan(reasoning="Plan parsing failed — using defaults")

        # Build TradePolicy
        tp_data = data.get("trade_policy", {})
        trade_policy = TradePolicy(
            willing_to_give=tp_data.get("willing_to_give", []),
            desperately_need=tp_data.get("desperately_need", []),
            max_bank_ratio_acceptable=int(tp_data.get("max_bank_ratio_acceptable", 4)),
            should_propose_trades=bool(tp_data.get("should_propose_trades", True)),
            min_accept_score=float(tp_data.get("min_accept_score", 0.5)),
        )

        return StrategyPlan(
            long_term_goal=data.get("long_term_goal", "balanced"),
            short_term_goals=data.get("short_term_goals", []),
            priority_resources=data.get("priority_resources", []),
            build_queue=data.get("build_queue", []),
            trade_policy=trade_policy,
            risk_tolerance=data.get("risk_tolerance", "moderate"),
            reasoning=data.get("reasoning", ""),
        )

    # ──────────────────────────────────────────────────────────────
    # Off-turn reactive trades
    # ──────────────────────────────────────────────────────────────

    def _check_reactive_trades(self, state: Dict[str, Any]) -> None:
        """Even off-turn, respond to incoming trade offers."""
        if not state:
            return
        if self._has_trade_offer(state):
            self.scratchpad.update_game_state(state, self.processor)
            try:
                self.call_agent("trading", "respond_to_offer")
            except Exception as e:
                print(f"  [strategy] Reactive trade failed: {e}")

    @staticmethod
    def _has_trade_offer(state: Dict[str, Any]) -> bool:
        """
        True if there is an incoming trade the Trading agent should handle off-turn.

        Delegates to the same rules as TradingAgent / React awake: targeted to us,
        broadcast (to is null), or canRespond flags — not our own outgoing offer.
        """
        return TradingAgent.has_incoming_offer_for_me(state)

    # ──────────────────────────────────────────────────────────────
    # Methods callable by peer agents
    # ──────────────────────────────────────────────────────────────

    def get_trade_policy(self) -> TradePolicy:
        """Called by Trading agent to get current trade guidance."""
        return self.scratchpad.strategy_plan.trade_policy

    def report_build_results(self, results: List[ActionRecord]) -> None:
        """Called by Development agent after executing build queue."""
        successes = sum(1 for r in results if r.success)
        failures = len(results) - successes
        print(f"  [strategy] Build results: {successes} succeeded, {failures} failed")

    def report_trade_results(self, results: Dict[str, Any]) -> None:
        """Called by Trading agent after completing trades."""
        print(f"  [strategy] Trade results: {results}")

    # ──────────────────────────────────────────────────────────────
    # Setup phase (heuristic, no LLM — same as ReactCatanAgent)
    # ──────────────────────────────────────────────────────────────

    def _handle_setup(self, state: Dict[str, Any]) -> None:
        """Brute-force settlement + road placement during setup."""
        self._turn_counter += 1
        self.stats.start_turn(self._turn_counter, phase="setup")

        if self._last_setup_settlement is None:
            ranked = _ranked_setup_settlements(state, top_k=120)
            for vk in ranked:
                resp = self._safe_call("placeSettlement", {
                    "vertexKey": vk, "isSetup": True,
                })
                if isinstance(resp, dict) and resp.get("success"):
                    self._last_setup_settlement = vk
                    print(f"  [setup] settlement at {vk}")
                    self.stats.record_tool_call("placeSettlement", success=True)
                    self.scratchpad.append_action(ActionRecord(
                        agent="strategy", action="placeSettlement",
                        args={"vertexKey": vk, "isSetup": True},
                        result=resp, success=True,
                    ))
                    self.stats.end_turn()
                    return
                else:
                    self.stats.record_tool_call("placeSettlement", success=False)
            print("  [setup] no legal settlement found")
            self.stats.end_turn()
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
                self.stats.record_tool_call("placeRoad", success=True)
                self.scratchpad.append_action(ActionRecord(
                    agent="strategy", action="placeRoad",
                    args={"edgeKey": ek, "isSetup": True},
                    result=resp, success=True,
                ))
                adv = self._safe_call("advanceSetup")
                self.stats.record_tool_call(
                    "advanceSetup",
                    success=bool(isinstance(adv, dict) and adv.get("success")),
                )
                print(f"  [setup] advanceSetup => {adv}")
                self._last_setup_settlement = None
                self._setup_placements_done += 1
                self.stats.end_turn()
                return
            else:
                self.stats.record_tool_call("placeRoad", success=False)
        print("  [setup] no legal road found")
        self.stats.end_turn()

    # ──────────────────────────────────────────────────────────────
    # Fallbacks
    # ──────────────────────────────────────────────────────────────

    def _fallback_discard(self, state: Dict[str, Any]) -> None:
        """Deterministic discard fallback."""
        actions = _build_discard_action(state)
        if actions:
            a = actions[0]
            self._safe_call("discardCards", a.payload)
            print(f"  [fallback] discarded: {a.payload}")

    def _fallback_robber(self, state: Dict[str, Any]) -> None:
        """Deterministic robber fallback."""
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
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _safe_call(
        self, event: str, payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Low-level socket call with error handling."""
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
