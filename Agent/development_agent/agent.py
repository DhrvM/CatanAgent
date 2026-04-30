"""
Development Agent — build queue execution, discard, and robber handling.

Called by Strategy via ``call_agent`` per TODO.md. Uses tools from the registry
(``agent_filter="development"``) and queries Risk for robber / building EV when useful.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import ActionRecord, Scratchpad, StrategyPlan
from Agent.development_agent.prompts import (
    DEVELOPMENT_SYSTEM_PROMPT,
    DEV_CONSULTANT_SYSTEM_PROMPT,
    build_discard_user_message,
    build_robber_user_message,
    build_dev_consult_context,
)
from Agent.Tools.game_tools import (
    RESOURCES,
    _build_discard_action,
)
from Agent.utils.game_state_processor import (
    _adjacent_hex_coords,
    _parse_vertex_key,
)

_LLM_ROBBER_ROUNDS = 2
_LLM_DISCARD_ROUNDS = 2
_THOUGHT_LOG_CHARS = 200


def _thought_excerpt_for_log(raw: str, max_chars: int = _THOUGHT_LOG_CHARS) -> str:
    """
    One-line friendly excerpt for [thought] logs: drop markdown/JSON blocks so we do not
    truncate mid-fence when the model ignores instructions.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    fence = s.find("```")
    if fence != -1:
        s = s[:fence].strip()
    if "\n\n" in s:
        s = s.split("\n\n", 1)[0].strip()
    s = " ".join(s.split())
    if len(s) <= max_chars:
        return s
    cut = s[: max_chars - 3].rstrip()
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "..."


def _thought_line_from_llm_response(
    assistant_text: str,
    tcs: List[Dict[str, Any]],
) -> str:
    """
    Prefer natural-language ``content``; if the API omits it (tool-only turn), summarize
    the first tool call so [thought] is never silent in the CLI.
    """
    excerpt = _thought_excerpt_for_log(assistant_text)
    if excerpt:
        return excerpt
    if not tcs:
        return ""
    tc0 = tcs[0]
    name = str(tc0.get("name") or "")
    args = tc0.get("arguments") if isinstance(tc0.get("arguments"), dict) else {}
    if name == "discard_cards":
        res = args.get("resources")
        if isinstance(res, dict):
            slim = {k: v for k, v in res.items() if int(v or 0) > 0}
            if slim:
                return f"Discarding resources: {slim}"
    if name == "move_robber":
        hk = args.get("hex_key")
        sid = args.get("steal_from_player_id")
        steal = "" if sid in (None, "") else f", steal from {sid}"
        return f"Moving robber to {hk}{steal}"
    return f"Calling {name}"


def _vertex_touches_hex(vertex_key: str, hex_key: str) -> bool:
    parsed = _parse_vertex_key(vertex_key)
    if not parsed:
        return False
    q, r, d = parsed
    for hq, hr in _adjacent_hex_coords(q, r, d):
        if f"{hq},{hr}" == hex_key:
            return True
    return False


class DevelopmentAgent(BaseAgent):
    """
    Builder and building consultant for the multi-agent system.

    Two modes:
      - **Consultant**: Strategy asks building questions via ``consult(question)``.
        Development advises on building spots, costs, and ROI using GPT-4o.
      - **Executor**: Strategy delegates builds via ``execute_build_queue()``.
        Development executes registry tools in order.

    Also handles discard (7-roll) and robber phases.

    Peers: Strategy (required), Risk (optional — register for robber EV / targets).
    """

    def __init__(
        self, scratchpad: Scratchpad, openai: Any = None, registry: Any = None,
    ) -> None:
        super().__init__("development", scratchpad)
        self.openai = openai
        self.registry = registry

    # ── Consultation (Strategy asks building questions) ────────────

    def consult(self, question: str) -> str:
        """
        Answer a building question from Strategy using GPT-4o.

        Builds context from the scratchpad: game state, our resources,
        available building spots, and Risk's building EV data.
        Returns a plain-English recommendation (3-5 sentences).
        """
        from dataclasses import asdict

        state_json = self.scratchpad.to_state_json()
        risk_data = asdict(self.scratchpad.risk_analysis)

        # Extract our current resources from scratchpad
        me = self.scratchpad.processed_state.get("me", {})
        resources = me.get("resources", {})

        # Get available building spots via registry
        building_spots = {}
        if self.registry:
            try:
                settle = self.registry.execute("get_building_spots", {"building_type": "settlement"})
                city = self.registry.execute("get_building_spots", {"building_type": "city"})
                building_spots = {
                    "settlement_spots": settle.get("spots", [])[:5],
                    "city_spots": city.get("spots", [])[:5],
                }
            except Exception:
                pass

        context = build_dev_consult_context(
            question=question,
            state_json=state_json,
            resources=resources,
            building_spots=building_spots,
            risk_analysis=risk_data,
        )

        answer = self._call_gpt4o(
            system=DEV_CONSULTANT_SYSTEM_PROMPT,
            user=context,
            fallback=self._fallback_consult(question),
        )

        print(f"  [dev] consult Q: {question[:80]}")
        print(f"  [dev] consult A: {answer[:120]}...")
        return answer

    def _call_gpt4o(self, system: str, user: str, fallback: str) -> str:
        """Send a system + user message to GPT-4o, return text or fallback."""
        if self.openai is None:
            return fallback
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = self.openai.chat_with_tools(messages, tools=None)
            text = self.openai.extract_text(response)
            if text and len(text.strip()) > 10:
                return text.strip()
        except Exception as e:
            print(f"  [dev] GPT-4o consult failed: {e}")
        return fallback

    def _fallback_consult(self, question: str) -> str:
        """Deterministic consultation fallback using cached risk data."""
        risk = self.scratchpad.risk_analysis
        me = self.scratchpad.processed_state.get("me", {})
        resources = me.get("resources", {})

        parts = [f"[Deterministic fallback for: {question}]"]

        # Check what we can afford
        can_settle = (resources.get("brick", 0) >= 1 and resources.get("lumber", 0) >= 1
                      and resources.get("wool", 0) >= 1 and resources.get("grain", 0) >= 1)
        can_city = resources.get("ore", 0) >= 3 and resources.get("grain", 0) >= 2
        can_road = resources.get("brick", 0) >= 1 and resources.get("lumber", 0) >= 1

        if can_city and risk.best_city_vertices:
            top = risk.best_city_vertices[0]
            parts.append(f"Can afford city upgrade. Best target: {top.get('vertex', '?')}.")
        elif can_settle and risk.best_settlement_vertices:
            top = risk.best_settlement_vertices[0]
            parts.append(f"Can afford settlement. Best target: {top.get('vertex', '?')}.")
        elif can_road:
            parts.append("Can afford road. Build toward best settlement spot.")
        else:
            parts.append("Cannot afford any buildings this turn. Trade or save.")

        return " ".join(parts)

    # ── Build queue ───────────────────────────────────────────────

    def execute_build_queue(self) -> List[ActionRecord]:
        """
        Execute Strategy's build queue using registry tools in order.
        Skips failed actions and continues (per TODO).
        """
        plan = self.scratchpad.strategy_plan
        if not isinstance(plan, StrategyPlan):
            plan = StrategyPlan()

        queue = list(plan.build_queue or [])
        queue.sort(key=lambda x: int(x.get("priority", 0)) if isinstance(x, dict) else 0)

        results: List[ActionRecord] = []
        if not queue:
            print("  [development] build_queue empty — nothing to execute")
            self._inform_strategy_build(results)
            return results

        if self.registry is None:
            print("  [development] registry missing — cannot execute build queue")
            self._inform_strategy_build(results)
            return results

        for item in queue:
            if not isinstance(item, dict):
                continue
            action, args = self._queue_item_to_tool(item)
            if not action:
                rec = ActionRecord(
                    agent="development",
                    action="skip",
                    args={"item": item},
                    result={"error": "unknown queue item"},
                    success=False,
                )
                self.scratchpad.append_build_action(rec)
                results.append(rec)
                continue

            print(f"  [action] {action}({json.dumps(args, default=str)})")
            result = self.registry.execute(action, args)
            ok = bool(isinstance(result, dict) and result.get("success", True))
            rec = ActionRecord(
                agent="development",
                action=action,
                args=args,
                result=dict(result) if isinstance(result, dict) else {"raw": result},
                success=ok,
            )
            self.scratchpad.append_build_action(rec)
            results.append(rec)

        self._inform_strategy_build(results)
        return results

    def _queue_item_to_tool(self, item: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """Map strategy plan build_queue entry to registry tool name + args."""
        raw = item.get("action") or item.get("tool") or item.get("type")
        if raw is None:
            return None, {}
        name = str(raw).strip()
        key = name.lower().replace("-", "_")

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
        key = aliases.get(key, key)

        target = item.get("target")
        extra = item.get("args") if isinstance(item.get("args"), dict) else {}

        if key == "place_settlement":
            vk = target or extra.get("vertex_key")
            if not vk:
                return None, {}
            return "place_settlement", {
                "vertex_key": str(vk),
                "is_setup": bool(extra.get("is_setup", False)),
            }

        if key == "place_road":
            ek = target or extra.get("edge_key")
            if not ek:
                return None, {}
            out: Dict[str, Any] = {
                "edge_key": str(ek),
                "is_setup": bool(extra.get("is_setup", False)),
            }
            if extra.get("last_settlement"):
                out["last_settlement"] = str(extra["last_settlement"])
            return "place_road", out

        if key == "upgrade_to_city":
            vk = target or extra.get("vertex_key")
            if not vk:
                return None, {}
            return "upgrade_to_city", {"vertex_key": str(vk)}

        if key == "buy_dev_card":
            return "buy_dev_card", {}

        if key == "play_dev_card":
            ct = item.get("card_type") or extra.get("card_type") or target
            if not ct:
                return None, {}
            params = extra.get("params")
            if params is None and isinstance(item.get("params"), dict):
                params = item.get("params")
            out = {"card_type": str(ct)}
            if isinstance(params, dict):
                out["params"] = params
            return "play_dev_card", out

        return None, {}

    def _inform_strategy_build(self, results: List[ActionRecord]) -> None:
        payload = {
            "build_results": [
                {
                    "action": r.action,
                    "success": r.success,
                    "args": r.args,
                    "result": r.result,
                }
                for r in results
            ],
        }
        self.send_message("strategy", "inform", payload)
        try:
            self.call_agent("strategy", "report_build_results", results=results)
        except Exception:
            pass

    # ── Discard ───────────────────────────────────────────────────

    def handle_discard(self) -> None:
        """
        Discard down to the legal count, preferring to keep Strategy's priority_resources.
        Tries LLM + discard_cards tool first when OpenAI is available; else heuristic.
        """
        state = self.scratchpad.game_state
        plan = self.scratchpad.strategy_plan
        priorities = list(plan.priority_resources or []) if isinstance(plan, StrategyPlan) else []

        if self.registry and self.openai:
            tools = self.registry.get_openai_schemas(
                phase_filter="discard", agent_filter="development",
            )
            if tools:
                done = self._handle_discard_with_llm(state, priorities, tools)
                if done:
                    return

        # Deterministic: priority-aware, else game_tools default
        payload = self._discard_resources_dict(state, priorities)
        if not payload:
            actions = _build_discard_action(state)
            if actions:
                payload = actions[0].payload.get("resources") or {}

        if not payload:
            print("  [development] handle_discard: could not compute discard set")
            self.send_message("strategy", "inform", {"discard": "failed", "reason": "no valid discard"})
            return

        result = self.registry.execute("discard_cards", {"resources": payload}) if self.registry else {"success": False}
        ok = bool(isinstance(result, dict) and result.get("success", True))
        print(f"  [action] discard_cards({json.dumps({'resources': payload}, default=str)})  # heuristic fallback")
        self.send_message("strategy", "inform", {"discard": payload, "result": result, "success": ok})
        self.scratchpad.append_action(ActionRecord(
            agent="development",
            action="discard_cards",
            args={"resources": payload},
            result=dict(result) if isinstance(result, dict) else {},
            success=ok,
        ))

    def _discard_resources_dict(
        self, state: Dict[str, Any], keep_first: List[str],
    ) -> Dict[str, int]:
        """Build resource -> count to discard, preferring to lose non-priority cards."""
        discarding = state.get("discardingPlayers")
        myi = state.get("myIndex")
        if not isinstance(discarding, list) or not isinstance(myi, int):
            return {}
        entry = next((d for d in discarding if isinstance(d, dict) and d.get("playerIndex") == myi), None)
        if not entry:
            return {}
        k = entry.get("cardsToDiscard")
        if not isinstance(k, int) or k <= 0:
            return {}

        players = state.get("players")
        if not isinstance(players, list) or myi < 0 or myi >= len(players):
            return {}
        me = players[myi] if isinstance(players[myi], dict) else {}
        res = me.get("resources")
        if not isinstance(res, dict):
            return {}

        hand = {r: int(res.get(r, 0) or 0) for r in RESOURCES}
        total = sum(hand.values())
        if total <= 0:
            return {}

        keep_set = {str(x).lower() for x in keep_first if x}
        order: List[str] = []
        for r in RESOURCES:
            if r not in keep_set:
                order.append(r)
        for r in RESOURCES:
            if r in keep_set:
                order.append(r)

        to_discard: Dict[str, int] = {r: 0 for r in RESOURCES}
        remaining = k
        for r in order:
            if remaining <= 0:
                break
            take = min(hand.get(r, 0), remaining)
            if take > 0:
                to_discard[r] = take
                remaining -= take
        if remaining != 0:
            return {}
        return {r: v for r, v in to_discard.items() if v > 0}

    def _handle_discard_with_llm(
        self,
        state: Dict[str, Any],
        priorities: List[str],
        tools: List[Dict[str, Any]],
    ) -> bool:
        """Return True if discard was executed via LLM."""
        hint = self._discard_hint_text(state)
        user = build_discard_user_message(
            self.scratchpad.state_json or {},
            priorities,
            hint,
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": DEVELOPMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        for attempt in range(_LLM_DISCARD_ROUNDS):
            try:
                response = self.openai.chat_with_tools(messages, tools=tools, temperature=0.2)
            except Exception as e:
                print(f"  [development] discard LLM error: {e}")
                return False

            assistant_text = self.openai.extract_text(response)
            tcs = self.openai.extract_tool_calls(response)
            thought_line = _thought_line_from_llm_response(assistant_text, tcs)
            # Avoid duplicate [thought] when the first reply has prose but no tool call and we nudge again.
            skip_thought = (not tcs) and bool(assistant_text) and (attempt + 1 < _LLM_DISCARD_ROUNDS)
            if thought_line and not skip_thought:
                print(f"  [thought] {thought_line}")

            if not tcs:
                if assistant_text and attempt + 1 < _LLM_DISCARD_ROUNDS:
                    messages.append({"role": "assistant", "content": assistant_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "You must invoke the discard_cards tool with "
                            '{"resources": { "brick": <n>, ... }} in the tool arguments only — '
                            "no JSON or code blocks in the message. One sentence of reasoning, then the tool call."
                        ),
                    })
                    continue
                return False
            tc = tcs[0]
            if tc["name"] != "discard_cards":
                return False
            args = tc["arguments"]
            print(f"  [action] discard_cards({json.dumps(args, default=str)})")
            result = self.registry.execute("discard_cards", args)
            ok = bool(isinstance(result, dict) and result.get("success", True))
            if ok:
                print(f"  [development] discard_cards (LLM) => ok")
                self.send_message("strategy", "inform", {"discard": args, "result": result, "llm": True})
                self.scratchpad.append_action(ActionRecord(
                    agent="development",
                    action="discard_cards",
                    args=args,
                    result=dict(result),
                    success=True,
                ))
                return True

            messages.append({
                "role": "assistant",
                "content": self.openai.extract_text(response) or "",
                "tool_calls": [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(args, default=str)},
                }],
            })
            messages.append(self.openai.build_tool_result_message(tc["id"], result))
        return False

    @staticmethod
    def _discard_hint_text(state: Dict[str, Any]) -> str:
        discarding = state.get("discardingPlayers")
        myi = state.get("myIndex")
        if not isinstance(discarding, list) or not isinstance(myi, int):
            return "Unknown discard requirement."
        entry = next((d for d in discarding if isinstance(d, dict) and d.get("playerIndex") == myi), None)
        if not entry:
            return "Unknown discard requirement."
        k = entry.get("cardsToDiscard")
        return f"Must discard exactly {k} card(s) (see server discardingPlayers)."

    # ── Robber ────────────────────────────────────────────────────

    def handle_robber(self) -> None:
        """
        Move robber using Risk targets when available; else heuristic hex + steal victim.
        Optional LLM pass to refine move_robber when OpenAI is configured.
        """
        state = self.scratchpad.game_state
        risk_targets: List[Dict[str, Any]] = []
        try:
            risk_targets = self.call_agent("risk", "get_robber_targets")
            if not isinstance(risk_targets, list):
                risk_targets = []
        except Exception as e:
            print(f"  [development] risk.get_robber_targets failed: {e}")

        hex_key, steal_id = self._robber_from_risk_targets(state, risk_targets)
        if not hex_key:
            hex_key, steal_id = self._heuristic_robber_move(state)

        heuristic_pick = {"hex_key": hex_key, "steal_from_player_id": steal_id}

        if self.registry and self.openai:
            tools = self.registry.get_openai_schemas(
                phase_filter="robber", agent_filter="development",
            )
            if tools:
                done = self._handle_robber_with_llm(
                    state, risk_targets, heuristic_pick, tools,
                )
                if done:
                    return

        self._execute_move_robber(hex_key, steal_id, source="heuristic")

    def _robber_from_risk_targets(
        self,
        state: Dict[str, Any],
        targets: List[Dict[str, Any]],
    ) -> Tuple[Optional[str], Optional[str]]:
        """Interpret Risk output: expect list of dicts with hex key + optional weight."""
        if not targets:
            return None, None
        best: Optional[Tuple[float, str]] = None
        for t in targets:
            if not isinstance(t, dict):
                continue
            hk = t.get("hex") or t.get("hex_key") or t.get("hexKey")
            if not hk:
                continue
            w = float(t.get("score", t.get("damage", t.get("weight", 1.0))) or 1.0)
            if best is None or w > best[0]:
                best = (w, str(hk))
        if best is None:
            return None, None
        hk = best[1]
        steal = self._pick_steal_target(state, hk)
        return hk, steal

    def _heuristic_robber_move(self, state: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        myi = state.get("myIndex")
        cur = state.get("robber")
        hexes = state.get("hexes") or {}
        best_hk: Optional[str] = None
        best_score = -1.0
        for hk, h in hexes.items():
            if not isinstance(h, dict):
                continue
            if hk == cur:
                continue
            res = str(h.get("resource", "") or "").lower()
            if res in ("desert", ""):
                continue
            score = 0.0
            for vk, v in (state.get("vertices") or {}).items():
                if not isinstance(v, dict):
                    continue
                own = v.get("owner")
                if own is None or own == myi:
                    continue
                if not _vertex_touches_hex(str(vk), str(hk)):
                    continue
                score += 1.0
            if score > best_score:
                best_score = score
                best_hk = str(hk)

        if best_hk is None:
            for hk in hexes:
                if hk != cur:
                    best_hk = str(hk)
                    break
        if best_hk is None:
            best_hk = ""

        steal = self._pick_steal_target(state, best_hk) if best_hk else None
        return best_hk, steal

    def _pick_steal_target(self, state: Dict[str, Any], hex_key: str) -> Optional[str]:
        """Pick a player id to steal from among opponents on this hex."""
        myi = state.get("myIndex")
        players = state.get("players")
        if not isinstance(players, list) or not isinstance(myi, int):
            return None
        seen: List[str] = []
        for vk, v in (state.get("vertices") or {}).items():
            if not isinstance(v, dict):
                continue
            own = v.get("owner")
            if own is None or own == myi:
                continue
            if not _vertex_touches_hex(str(vk), hex_key):
                continue
            if isinstance(own, int) and 0 <= own < len(players):
                pid = players[own].get("id")
                if pid is not None:
                    sid = str(pid)
                    if sid not in seen:
                        seen.append(sid)
        return seen[0] if seen else None

    def _handle_robber_with_llm(
        self,
        state: Dict[str, Any],
        risk_targets: List[Dict[str, Any]],
        heuristic_pick: Dict[str, Any],
        tools: List[Dict[str, Any]],
    ) -> bool:
        user = build_robber_user_message(
            self.scratchpad.state_json or {},
            risk_targets,
            heuristic_pick,
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": DEVELOPMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]
        for attempt in range(_LLM_ROBBER_ROUNDS):
            try:
                response = self.openai.chat_with_tools(messages, tools=tools, temperature=0.2)
            except Exception as e:
                print(f"  [development] robber LLM error: {e}")
                return False

            assistant_text = self.openai.extract_text(response)
            tcs = self.openai.extract_tool_calls(response)
            thought_line = _thought_line_from_llm_response(assistant_text, tcs)
            skip_thought = (not tcs) and bool(assistant_text) and (attempt + 1 < _LLM_ROBBER_ROUNDS)
            if thought_line and not skip_thought:
                print(f"  [thought] {thought_line}")

            if not tcs:
                if assistant_text and attempt + 1 < _LLM_ROBBER_ROUNDS:
                    messages.append({"role": "assistant", "content": assistant_text})
                    messages.append({
                        "role": "user",
                        "content": (
                            "Invoke move_robber via the tool with hex_key and optional steal_from_player_id "
                            "(use player id from state, e.g. p1). One sentence of reasoning, then the tool call."
                        ),
                    })
                    continue
                return False
            tc = tcs[0]
            if tc["name"] != "move_robber":
                return False
            args = tc["arguments"]
            print(f"  [action] move_robber({json.dumps(args, default=str)})")
            result = self.registry.execute("move_robber", args)
            ok = bool(isinstance(result, dict) and result.get("success", True))
            if ok:
                print(f"  [development] move_robber (LLM) => ok")
                self._record_robber_outcome(args, result, source="llm")
                return True

            messages.append({
                "role": "assistant",
                "content": self.openai.extract_text(response) or "",
                "tool_calls": [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(args, default=str)},
                }],
            })
            messages.append(self.openai.build_tool_result_message(tc["id"], result))
        return False

    def _execute_move_robber(
        self, hex_key: str, steal_from_player_id: Optional[str], source: str,
    ) -> None:
        if not self.registry or not hex_key:
            print("  [development] move_robber skipped (no registry or hex)")
            self.send_message("strategy", "inform", {"robber_result": {"success": False, "reason": "no hex"}})
            return

        args: Dict[str, Any] = {"hex_key": hex_key}
        if steal_from_player_id:
            args["steal_from_player_id"] = steal_from_player_id
        result = self.registry.execute("move_robber", args)
        ok = bool(isinstance(result, dict) and result.get("success", True))
        print(f"  [development] move_robber ({source}) => {ok} hex={hex_key}")
        self._record_robber_outcome(args, result, source=source)

    def _record_robber_outcome(
        self, args: Dict[str, Any], result: Dict[str, Any], source: str,
    ) -> None:
        ok = bool(isinstance(result, dict) and result.get("success", True))
        self.send_message("strategy", "inform", {
            "robber_result": {"args": args, "result": result, "source": source},
        })
        self.scratchpad.append_action(ActionRecord(
            agent="development",
            action="move_robber",
            args=args,
            result=dict(result),
            success=ok,
        ))
