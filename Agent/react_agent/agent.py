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
from Agent.utils.stats_tracker import AgentStatsTracker
from Agent.tools.registry import ToolRegistry, build_tool_registry
from Agent.react_agent.summarizer import MoveSummarizer
from Agent.react_agent.prompts import SYSTEM_PROMPT, MAX_STEPS_PER_TURN, build_turn_message

# ReAct guardrails (observed failure modes in long runs)
_MAX_GET_GAME_SUMMARY_PER_TURN = 6
_OBSERVE_ONLY_TOOLS = frozenset({"get_game_summary", "get_building_spots", "get_trade_options"})


def _is_openai_rate_limit_error(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None)
    if code == 429:
        return True
    body = str(exc).lower()
    return "429" in body or "rate limit" in body or "too many requests" in body


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

        # statistics tracker
        self.stats = AgentStatsTracker(agent_name=player_name)
        self._turn_counter = 0

        # setup tracking
        self._last_setup_settlement: Optional[str] = None
        self._setup_placements_done = 0

        # off-turn trade reactivity (avoid duplicate responses to same offer)
        self._last_reactive_offer_sig: Optional[str] = None

        # avoid repeating failed placements in one ReAct turn
        self._failed_placements: set = set()

        # per ReAct turn: throttle summary spam; auto end_turn after lone propose_trade
        self._summary_calls_this_turn: int = 0
        self._propose_trade_awaiting_end: bool = False

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

        try:
            while True:
                try:
                    state = self.client.latest_state()
                    if not state:
                        time.sleep(0.25)
                        continue

                    if not is_my_turn(state):
                        self._handle_reactive_trade_offer(state)
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
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    if _is_openai_rate_limit_error(e):
                        print(f"\n⚠ OpenAI rate limited ({e}); backing off and continuing…")
                        time.sleep(25.0)
                        continue
                    print(f"\n❌ Agent error: {e}")
                    raise
        except KeyboardInterrupt:
            print("\n⏹  Agent interrupted.")
        finally:
            paths = self.stats.export_all("./logs/Agent")
            print(f"📊 Stats saved:")
            print(f"   JSON: {paths['json']}")
            print(f"   CSV:  {paths['csv']}")

    # ──────────────────────────────────────────────────────────────
    # ReAct loop
    # ──────────────────────────────────────────────────────────────
    def _react_turn(self, initial_state: Dict[str, Any]) -> None:
        """Run the multi-step ReAct loop for one turn."""
        assert self._registry is not None

        self._turn_counter += 1
        turn_phase = initial_state.get("turnPhase", "main")
        self.stats.start_turn(self._turn_counter, phase=turn_phase)
        self._failed_placements.clear()
        self._summary_calls_this_turn = 0
        self._propose_trade_awaiting_end = False

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
                # Record token usage
                usage = self.openai.extract_usage(response)
                self.stats.record_llm_call(
                    prompt_tokens=usage["prompt_tokens"],
                    completion_tokens=usage["completion_tokens"],
                    model=self.openai.model,
                )
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
                if self._propose_trade_awaiting_end:
                    print("  [react] no tool calls after propose_trade; auto end_turn")
                    self._propose_trade_awaiting_end = False
                    et = self._react_validate_and_execute("end_turn", {})
                    self.stats.record_tool_call(
                        "end_turn", success=bool(isinstance(et, dict) and et.get("success", True)),
                    )
                    self.summarizer.record_event("end_turn", {"after": "propose_trade", "auto": True})
                    turn_ended = True
                else:
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
            success_non_observe: set = set()
            propose_trade_succeeded = False

            for tc in tool_calls:
                name = tc["name"]
                args = tc["arguments"]
                tc_id = tc["id"]

                print(f"  [action] {name}({json.dumps(args, default=str)})")

                result = self._react_validate_and_execute(name, args)
                print(f"  [result] {json.dumps(result, default=str)[:200]}")

                # Record stats
                success = result.get("success", True)
                self.stats.record_tool_call(name, success=bool(success))

                # Record event
                self.summarizer.record_event(name, {
                    "args": args,
                    "success": success,
                })

                # Feed result back to GPT-4o
                messages.append(
                    self.openai.build_tool_result_message(tc_id, result)
                )

                if name == "end_turn":
                    turn_ended = True
                if success and name not in _OBSERVE_ONLY_TOOLS:
                    success_non_observe.add(name)
                if name == "propose_trade" and success:
                    propose_trade_succeeded = True

            other_substantive = {n for n in success_non_observe if n != "propose_trade"}
            if propose_trade_succeeded and not turn_ended and not other_substantive:
                self._propose_trade_awaiting_end = True
            elif turn_ended or other_substantive:
                self._propose_trade_awaiting_end = False

            # Brief pause for state to propagate
            time.sleep(0.1)

        if steps >= MAX_STEPS_PER_TURN and not turn_ended:
            st = self.client.latest_state() or initial_state
            tp = st.get("turnPhase")
            if tp == "main":
                print("  [react] max steps reached, forcing end_turn")
                self._registry.execute("end_turn", {})
                self.stats.record_tool_call("end_turn", success=True)
                self.summarizer.record_event("end_turn", {"forced": True})
            else:
                print(f"  [react] max steps reached in phase {tp!r}; heuristic fallback")
                self._fallback_action(st)
                self.summarizer.record_event("fallback", {"forced": True, "phase": tp})

        self.stats.end_turn()

    # ──────────────────────────────────────────────────────────────
    # Setup phase (heuristic, no LLM)
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
                    self.summarizer.record_event("placeSettlement", {"vertex": vk, "setup": True})
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
                self.summarizer.record_event("placeRoad", {"edge": ek, "setup": True})
                adv = self._safe_call("advanceSetup")
                self.stats.record_tool_call("advanceSetup", success=bool(isinstance(adv, dict) and adv.get("success")))
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

    def _react_validate_and_execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Guardrails so the model does not spam invalid tool calls."""
        assert self._registry is not None
        state = self.client.latest_state() or {}
        turn_phase = state.get("turnPhase")
        vertices = state.get("vertices") or {}
        edges = state.get("edges") or {}

        if name == "get_game_summary":
            if self._summary_calls_this_turn >= _MAX_GET_GAME_SUMMARY_PER_TURN:
                return {
                    "success": False,
                    "error": (
                        f"get_game_summary already called {_MAX_GET_GAME_SUMMARY_PER_TURN} times this turn; "
                        "reuse prior tool output and the turn message instead of polling again."
                    ),
                }
            self._summary_calls_this_turn += 1

        if name == "respond_to_trade":
            if self._extract_trade_offer_for_me(state) is None:
                return {
                    "success": False,
                    "error": (
                        "No active incoming trade offer for you — do not call respond_to_trade. "
                        "Use propose_trade to make an offer, or wait for an opponent offer."
                    ),
                }

        if name == "buy_dev_card":
            me = self._me(state)
            res = me.get("resources") if isinstance(me.get("resources"), dict) else {}
            try:
                if (
                    int(res.get("ore", 0) or 0) < 1
                    or int(res.get("grain", 0) or 0) < 1
                    or int(res.get("wool", 0) or 0) < 1
                ):
                    return {
                        "success": False,
                        "error": (
                            "buy_dev_card costs 1 ore + 1 grain + 1 wool — "
                            "you do not have enough resources."
                        ),
                    }
            except (TypeError, ValueError):
                return {
                    "success": False,
                    "error": "Could not read your resources for buy_dev_card; refresh state.",
                }

        if name == "roll_dice" and turn_phase == "robber":
            return {
                "success": False,
                "error": (
                    "turnPhase is robber — use move_robber (or resolve robber first). "
                    "Do not roll dice until you leave the robber phase."
                ),
            }

        if name == "propose_trade":
            offer = args.get("offer")
            request = args.get("request")
            if not isinstance(offer, dict) or not isinstance(request, dict):
                return {
                    "success": False,
                    "error": "propose_trade requires offer and request as resource dicts.",
                }

            def _positive_total(d: Dict[str, Any]) -> int:
                t = 0
                for v in d.values():
                    try:
                        t += max(0, int(v or 0))
                    except Exception:
                        return -1
                return t

            if _positive_total(offer) <= 0 or _positive_total(request) <= 0:
                return {
                    "success": False,
                    "error": "propose_trade needs non-empty offer and request (positive amounts).",
                }

        if name == "place_settlement":
            vk = args.get("vertex_key")
            if isinstance(vk, str):
                key = f"settlement:{vk}"
                if key in self._failed_placements:
                    return {
                        "success": False,
                        "error": "Already failed this settlement placement this turn; pick a different vertex from get_building_spots.",
                    }
                v = vertices.get(vk)
                if isinstance(v, dict) and v.get("owner") is not None:
                    return {
                        "success": False,
                        "error": f"Vertex {vk} is already occupied — choose another vertex.",
                    }

        if name == "place_road":
            ek = args.get("edge_key")
            if isinstance(ek, str):
                key = f"road:{ek}"
                if key in self._failed_placements:
                    return {
                        "success": False,
                        "error": "Already failed this road placement this turn; call get_building_spots with building_type \"road\" for new edges.",
                    }
                e = edges.get(ek)
                if isinstance(e, dict) and e.get("road"):
                    return {
                        "success": False,
                        "error": f"Edge {ek} already has a road — use get_building_spots with building_type \"road\".",
                    }

        result = self._registry.execute(name, args)
        success = bool(result.get("success", True)) if isinstance(result, dict) else False

        if not success and isinstance(result, dict):
            err = str(result.get("error", "")).lower()
            if name == "place_settlement" and isinstance(args.get("vertex_key"), str):
                if any(
                    s in err
                    for s in ("occupied", "connect", "illegal", "invalid", "must", "not legal")
                ):
                    self._failed_placements.add(f"settlement:{args['vertex_key']}")
            if name == "place_road" and isinstance(args.get("edge_key"), str):
                if any(
                    s in err
                    for s in ("already", "exists", "occupied", "connect", "illegal", "invalid")
                ):
                    self._failed_placements.add(f"road:{args['edge_key']}")

        return result

    # ──────────────────────────────────────────────────────────────
    # Reactive/off-turn trade handling ("awake" behavior)
    # ──────────────────────────────────────────────────────────────
    def _handle_reactive_trade_offer(self, state: Dict[str, Any]) -> None:
        """
        React to incoming trade offers while off-turn.
        Handles both:
          - targeted offers to this agent
          - broadcast offers to all players
        """
        offer = self._extract_trade_offer_for_me(state)
        if not offer:
            self._last_reactive_offer_sig = None
            return

        offer_sig = json.dumps(offer, sort_keys=True, default=str)
        if offer_sig == self._last_reactive_offer_sig:
            return

        llm_result = self._reactive_trade_decision_with_llm(state, offer)
        fallback_resp: Optional[Dict[str, Any]] = None
        if llm_result is None:
            accept = self._should_accept_offer(state, offer)
            thought = self._heuristic_trade_reason(state, offer, accept)
            print(f"  [thought] {thought}")
            resp = self._safe_call("respondToTrade", {"accept": accept})
            fallback_resp = resp
            success = bool(isinstance(resp, dict) and resp.get("success"))
            self.stats.record_tool_call("respondToTrade", success=success)
            self.summarizer.record_event("respondToTrade", {
                "accept": accept,
                "reactive": True,
                "fallback": True,
                "success": success,
            })
            decision = "accept" if accept else "decline"
        else:
            success = bool(llm_result.get("success", False))
            action = llm_result.get("action", "unknown")
            thought = str(
                llm_result.get("thought")
                or f"LLM selected reactive action: {action}."
            )
            print(f"  [thought] {thought}")
            self.summarizer.record_event(action, {
                "reactive": True,
                "fallback": False,
                "success": success,
            })
            if action == "respond_to_trade":
                tool_args = llm_result.get("args", {}) if isinstance(llm_result, dict) else {}
                decision = "accept" if bool(tool_args.get("accept")) else "decline"
            elif action == "counter_trade":
                decision = "counter"
            else:
                decision = "unknown"

        if success:
            print(f"  [awake] decision={decision}")
            self._last_reactive_offer_sig = offer_sig
        else:
            if isinstance(llm_result, dict) and llm_result.get("result") is not None:
                err = llm_result.get("result")
            elif fallback_resp is not None:
                err = fallback_resp
            else:
                err = "unknown"
            print(f"  [awake] decision=failed error={err}")

    def _reactive_trade_decision_with_llm(
        self, state: Dict[str, Any], offer: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Lightweight ReAct-style off-turn trade reasoning.
        Allows:
          - respond_to_trade (accept/decline)
          - counter_trade (sometimes)
        """
        if self._registry is None:
            return None

        try:
            processed = self.processor.process(state)
            state_text = self.processor.format_for_llm(processed)
        except Exception:
            state_text = json.dumps(state, default=str)

        # Restrict to trade-response actions while off-turn.
        all_tools = self._registry.get_openai_schemas(phase_filter="main")
        allowed = {"respond_to_trade", "counter_trade"}
        tools = [
            t for t in all_tools
            if isinstance(t, dict)
            and isinstance(t.get("function"), dict)
            and t["function"].get("name") in allowed
        ]
        if not tools:
            return None

        prompt = (
            "Incoming trade offer while it is NOT your turn.\n"
            "Semantics: in the server state, \"offer\" is what the proposer gives; "
            "\"request\" is what they want from you.\n"
            "Decide whether to accept, decline, or counter.\n"
            "You MUST choose exactly one tool call.\n"
            "- Use respond_to_trade with accept=true/false for accept/decline.\n"
            "- Use counter_trade only when the deal is close but unfavorable; "
            "pass offer/request dicts for YOUR counter (what you give / what you want).\n"
            "- Counter offers should be modest (usually 1-for-1 or slight improvement), "
            "not extreme.\n"
            "Current state:\n"
            f"{state_text}\n\n"
            "Incoming offer JSON:\n"
            f"{json.dumps(offer, default=str)}"
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        final_thought = ""
        # Keep off-turn latency low.
        for _ in range(2):
            response = self.openai.chat_with_tools(messages, tools=tools, temperature=0.2)
            usage = self.openai.extract_usage(response)
            self.stats.record_llm_call(
                prompt_tokens=usage["prompt_tokens"],
                completion_tokens=usage["completion_tokens"],
                model=self.openai.model,
            )
            assistant_text = self.openai.extract_text(response)
            if assistant_text:
                final_thought = assistant_text.strip()
            tool_calls = self.openai.extract_tool_calls(response)
            if not tool_calls:
                return None

            tc = tool_calls[0]
            name = tc["name"]
            args = tc["arguments"]
            if name not in allowed:
                return None

            result = self._registry.execute(name, args)
            ok = bool(isinstance(result, dict) and result.get("success"))
            self.stats.record_tool_call(name, success=ok)

            if ok:
                return {
                    "success": True,
                    "action": name,
                    "args": args,
                    "result": result,
                    "thought": final_thought,
                }

            # Give model one chance to repair arguments.
            messages.append({
                "role": "assistant",
                "content": self.openai.extract_text(response) or "",
                "tool_calls": [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, default=str)},
                }],
            })
            messages.append(self.openai.build_tool_result_message(tc["id"], result))

        return {
            "success": False,
            "action": "none",
            "result": {"error": "reactive decision failed"},
            "thought": final_thought,
        }

    def _extract_trade_offer_for_me(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(state, dict):
            return None
        players = state.get("players")
        myi = state.get("myIndex")
        if not isinstance(players, list) or not isinstance(myi, int):
            return None

        for key in ("activeTradeOffer", "tradeOffer", "currentTradeOffer"):
            raw_offer = state.get(key)
            if not isinstance(raw_offer, dict):
                continue

            from_idx = raw_offer.get("from")
            to_idx = raw_offer.get("to")
            if from_idx == myi:
                return None  # our own outgoing trade

            # targeted-to-me OR broadcast-to-all (to missing/null)
            targeted_to_me = isinstance(to_idx, int) and to_idx == myi
            broadcast = to_idx is None
            can_respond_flag = bool(
                raw_offer.get("can_i_respond")
                or raw_offer.get("canRespond")
                or raw_offer.get("canRespondToTrade")
            )
            if targeted_to_me or broadcast or can_respond_flag:
                return {
                    "from": from_idx,
                    "to": to_idx,
                    "offer": raw_offer.get("offer") if isinstance(raw_offer.get("offer"), dict) else {},
                    "request": raw_offer.get("request") if isinstance(raw_offer.get("request"), dict) else {},
                }
        return None

    def _should_accept_offer(self, state: Dict[str, Any], offer: Dict[str, Any]) -> bool:
        """Simple deterministic accept/decline heuristic for off-turn offers."""
        me = self._me(state)
        hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}
        request = offer.get("request") if isinstance(offer.get("request"), dict) else {}
        their_offer = offer.get("offer") if isinstance(offer.get("offer"), dict) else {}

        # Must be able to pay requested resources.
        for resource, amount in request.items():
            try:
                amt = int(amount or 0)
            except Exception:
                return False
            if amt < 0 or int(hand.get(resource, 0) or 0) < amt:
                return False

        # Accept if their offer gives at least one resource we currently lack.
        for resource, amount in their_offer.items():
            try:
                amt = int(amount or 0)
            except Exception:
                continue
            if amt > 0 and int(hand.get(resource, 0) or 0) == 0:
                return True

        # Otherwise, decline by default.
        return False

    def _heuristic_trade_reason(
        self, state: Dict[str, Any], offer: Dict[str, Any], accept: bool,
    ) -> str:
        me = self._me(state)
        hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}
        request = offer.get("request") if isinstance(offer.get("request"), dict) else {}
        their_offer = offer.get("offer") if isinstance(offer.get("offer"), dict) else {}

        if accept:
            missing = [
                r for r, amt in their_offer.items()
                if int(amt or 0) > 0 and int(hand.get(r, 0) or 0) == 0
            ]
            return (
                "Accepting trade because we can pay the request and it fills missing "
                f"resource(s): {missing}."
            )
        cannot_pay = []
        for r, amt in request.items():
            if int(hand.get(r, 0) or 0) < int(amt or 0):
                cannot_pay.append(r)
        if cannot_pay:
            return f"Declining trade because we cannot pay requested resource(s): {cannot_pay}."
        return (
            "Declining trade because we can pay but their offer does not add any resource "
            "we currently lack; keeping our hand for roads/settlements is better than a sideways swap."
        )

    @staticmethod
    def _me(state: Dict[str, Any]) -> Dict[str, Any]:
        players = state.get("players")
        myi = state.get("myIndex")
        if (
            isinstance(players, list)
            and isinstance(myi, int)
            and 0 <= myi < len(players)
            and isinstance(players[myi], dict)
        ):
            return players[myi]
        return {}
