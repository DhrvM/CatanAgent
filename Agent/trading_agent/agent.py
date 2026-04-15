"""
Trading Agent — proactive and reactive trade execution.

Off-turn incoming offers are handled in ``respond_to_offer`` (invoked by Strategy),
using an optional LLM "awake" path (respond_to_trade / counter_trade) with heuristic
fallback — aligned with ``ReactCatanAgent`` reactive trade behavior and TODO.md.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import ActionRecord, Scratchpad, TradePolicy, TradeState
from Agent.trading_agent.prompts import (
    TRADING_SYSTEM_PROMPT,
    build_awake_trade_user_message,
)

RESOURCES = ["brick", "lumber", "wool", "grain", "ore"]
TRADE_RESPONSE_TIMEOUT_SEC = 60
TRADE_POLL_INTERVAL_SEC = 2

_AWAKE_LLM_MAX_ROUNDS = 2


def extract_incoming_offer_for_me(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Return a normalized incoming trade we can respond to, or None.

    Matches server fields used by ReactCatanAgent (broadcast, targeted, canRespond flags).
    """
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
            return None

        targeted_to_me = isinstance(to_idx, int) and to_idx == myi
        broadcast = to_idx is None
        can_respond_flag = bool(
            raw_offer.get("can_i_respond")
            or raw_offer.get("canRespond")
            or raw_offer.get("canRespondToTrade")
        )
        if not (targeted_to_me or broadcast or can_respond_flag):
            continue

        offer_part = raw_offer.get("offer") if isinstance(raw_offer.get("offer"), dict) else {}
        request_part = raw_offer.get("request") if isinstance(raw_offer.get("request"), dict) else {}
        return {
            "from": from_idx,
            "to": to_idx,
            "offer": offer_part,
            "request": request_part,
            "raw": raw_offer,
        }
    return None


class TradingAgent(BaseAgent):
    """Trading executor that only coordinates with Strategy."""

    def __init__(
        self, scratchpad: Scratchpad, openai: Any = None, registry: Any = None,
    ) -> None:
        super().__init__("trading", scratchpad)
        self.openai = openai
        self.registry = registry
        self.trade_state = TradeState()
        self.scratchpad.write_trade_state(self.trade_state)

        # Dedupe off-turn responses (same offer broadcast while polling)
        self._last_awake_offer_sig: Optional[str] = None

    @staticmethod
    def has_incoming_offer_for_me(state: Dict[str, Any]) -> bool:
        """Used by Strategy to decide whether to call respond_to_offer off-turn."""
        return extract_incoming_offer_for_me(state) is not None

    def proactive_trade(self) -> None:
        """
        Propose trades if Strategy recommends it.
        Called by Strategy via call_agent("trading", "proactive_trade").
        """
        policy = self.call_agent("strategy", "get_trade_policy")
        if not isinstance(policy, TradePolicy):
            policy = TradePolicy()

        results: Dict[str, Any] = {
            "mode": "proactive",
            "executed": [],
            "reason": "",
        }

        if not policy.should_propose_trades:
            results["reason"] = "strategy policy disabled proactive trades"
            self._report_results(results)
            return

        # First preference: bank trade if ratio is acceptable and it yields needed resources.
        bank_result = self._attempt_bank_trade(policy)
        if bank_result is not None:
            results["executed"].append(bank_result)
            results["reason"] = "executed bank trade"
            self._report_results(results)
            return

        # Second preference: player trade proposal if no bank trade was viable.
        player_result = self._attempt_player_trade(policy)
        if player_result is not None:
            results["executed"].append(player_result)
            results["reason"] = "proposed player trade"
        else:
            results["reason"] = "no viable trade found"

        self._report_results(results)

    def respond_to_offer(self) -> None:
        """
        Evaluate and respond to an incoming trade offer.
        Called by Strategy via call_agent("trading", "respond_to_offer") — typically off-turn.

        1) Prefer LLM "awake" tool use (respond_to_trade / counter_trade) when OpenAI + registry exist.
        2) Fall back to deterministic scoring (same as pre-awake TradingAgent).
        """
        policy = self.call_agent("strategy", "get_trade_policy")
        if not isinstance(policy, TradePolicy):
            policy = TradePolicy()

        state = self.scratchpad.game_state
        offer_ctx = extract_incoming_offer_for_me(state)
        if not offer_ctx:
            result = {"mode": "reactive", "action": "ignore", "reason": "no active offer for us"}
            self._report_trade_response(result)
            return

        sig = json.dumps(offer_ctx, sort_keys=True, default=str)
        if sig == self._last_awake_offer_sig:
            return

        raw_for_proposer = offer_ctx.get("raw") if isinstance(offer_ctx.get("raw"), dict) else offer_ctx
        offer = self._normalize_resource_dict(offer_ctx.get("offer"))
        request = self._normalize_resource_dict(offer_ctx.get("request"))
        proposer = self._extract_offer_proposer(raw_for_proposer)
        proposer_vp = self._lookup_player_vp(proposer)

        if self.openai and self.registry:
            llm_out = self._awake_trade_decision_with_llm(state, offer_ctx, policy)
            if llm_out is None:
                pass  # fall through to deterministic heuristic
            elif isinstance(llm_out, dict) and llm_out.get("success"):
                self._last_awake_offer_sig = sig
                self._apply_awake_llm_outcome(
                    llm_out=llm_out,
                    offer=offer,
                    request=request,
                    proposer=proposer,
                    proposer_vp=proposer_vp,
                    policy=policy,
                )
                return
            else:
                # Awake LLM ran but did not complete — do not also send respond_to_trade (duplicate).
                err = ""
                if isinstance(llm_out, dict):
                    err = str(llm_out.get("error") or "")
                thought = ""
                if isinstance(llm_out, dict):
                    thought = str(llm_out.get("thought") or "")
                self._report_trade_response({
                    "mode": "reactive_awake",
                    "action": "failed",
                    "reason": err or "awake_llm_or_tool_failed",
                    "thought": thought,
                    "proposer": proposer,
                })
                return

        # ── Deterministic fallback (heuristic scoring) ─────────────────
        score = self._score_trade(offer=offer, request=request, trade_policy=policy)
        if proposer_vp >= 8:
            score -= 0.35

        threshold = float(policy.min_accept_score)
        counter_window = max(0.0, threshold - 0.2)

        action = "decline"
        tool_result: Dict[str, Any]
        counter_payload: Optional[Dict[str, Dict[str, int]]] = None

        if score >= threshold:
            action = "accept"
            tool_result = self._exec_tool("respond_to_trade", {"accept": True})
        elif score >= counter_window:
            counter_payload = self._build_counter_offer(
                incoming_offer=offer,
                incoming_request=request,
                policy=policy,
            )
            if counter_payload:
                action = "counter"
                tool_result = self._exec_tool(
                    "counter_trade",
                    {
                        "offer": counter_payload["offer"],
                        "request": counter_payload["request"],
                    },
                )
                if bool(tool_result.get("success", False)):
                    self.trade_state.pending_offer = {
                        "offer": counter_payload["offer"],
                        "request": counter_payload["request"],
                        "target": proposer,
                        "kind": "counter",
                    }
                    self.scratchpad.write_trade_state(self.trade_state)
                    counter_followup = self._await_offer_response_or_timeout(policy)
                else:
                    action = "decline"
                    tool_result = self._exec_tool("respond_to_trade", {"accept": False})
            else:
                tool_result = self._exec_tool("respond_to_trade", {"accept": False})
        else:
            tool_result = self._exec_tool("respond_to_trade", {"accept": False})

        self._record_trade_outcome(
            counterpart=proposer,
            offer=offer,
            request=request,
            accepted=action == "accept" and bool(tool_result.get("success", True)),
        )

        result = {
            "mode": "reactive",
            "action": action,
            "score": round(score, 3),
            "threshold": threshold,
            "proposer": proposer,
            "proposer_vp": proposer_vp,
            "tool_result": tool_result,
        }
        if counter_payload:
            result["counter_offer"] = counter_payload
            if "counter_followup" in locals():
                result["counter_followup"] = counter_followup
        self._report_trade_response(result)
        if bool(tool_result.get("success", True)) and action in ("accept", "decline", "counter"):
            self._last_awake_offer_sig = sig

    def _awake_trade_decision_with_llm(
        self,
        state: Dict[str, Any],
        offer_ctx: Dict[str, Any],
        policy: TradePolicy,
    ) -> Optional[Dict[str, Any]]:
        """
        Lightweight ReAct-style off-turn trade reasoning (mirrors ReactCatanAgent).

        Allowed tools: respond_to_trade, counter_trade only.
        """
        if not self.registry or not self.openai:
            return None

        all_tools = self.registry.get_openai_schemas(phase_filter="main", agent_filter="trading")
        allowed = {"respond_to_trade", "counter_trade"}
        tools = [
            t for t in all_tools
            if isinstance(t, dict)
            and isinstance(t.get("function"), dict)
            and t["function"].get("name") in allowed
        ]
        if not tools:
            return None

        tp_dict = asdict(policy)
        ts_dict = asdict(self.trade_state)
        user_content = build_awake_trade_user_message(
            self.scratchpad.state_json or {},
            tp_dict,
            ts_dict,
            offer_ctx,
        )
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": TRADING_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        final_thought = ""
        for _ in range(_AWAKE_LLM_MAX_ROUNDS):
            try:
                response = self.openai.chat_with_tools(messages, tools=tools, temperature=0.2)
            except Exception:
                return None

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

            result = self._exec_tool(name, args)
            ok = bool(isinstance(result, dict) and result.get("success"))

            if ok:
                return {
                    "success": True,
                    "action": name,
                    "args": args,
                    "result": result,
                    "thought": final_thought,
                }

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
            "thought": final_thought,
            "error": "tool_failed_after_repair_attempt",
        }

    def _apply_awake_llm_outcome(
        self,
        llm_out: Dict[str, Any],
        offer: Dict[str, int],
        request: Dict[str, int],
        proposer: str,
        proposer_vp: int,
        policy: TradePolicy,
    ) -> None:
        """Record stats + report after a successful awake LLM tool execution."""
        action = str(llm_out.get("action", ""))
        tool_result = llm_out.get("result") if isinstance(llm_out.get("result"), dict) else {}
        thought = str(llm_out.get("thought") or "")
        args = llm_out.get("args") if isinstance(llm_out.get("args"), dict) else {}

        if thought:
            print(f"  [thought] {thought[:320]}")

        threshold = float(policy.min_accept_score)
        base_score = self._score_trade(offer=offer, request=request, trade_policy=policy)

        if action == "respond_to_trade":
            accepted = bool(args.get("accept"))
            self._record_trade_outcome(
                counterpart=proposer,
                offer=offer,
                request=request,
                accepted=accepted and bool(tool_result.get("success", True)),
            )
            decision = "accept" if accepted else "decline"
            print(f"  [awake] decision={decision}")
            self._report_trade_response({
                "mode": "reactive_awake",
                "action": decision,
                "thought": thought,
                "score": round(base_score, 3),
                "threshold": threshold,
                "proposer": proposer,
                "proposer_vp": proposer_vp,
                "tool_result": tool_result,
            })
            self.scratchpad.append_action(ActionRecord(
                agent="trading",
                action="respond_to_trade",
                args={"accept": accepted, "awake": True},
                result=dict(tool_result),
                success=bool(tool_result.get("success", True)),
            ))
            return

        if action == "counter_trade":
            counter_followup: Optional[Dict[str, Any]] = None
            if bool(tool_result.get("success", False)):
                self.trade_state.pending_offer = {
                    "offer": args.get("offer") or {},
                    "request": args.get("request") or {},
                    "target": proposer,
                    "kind": "counter_awake",
                }
                self.scratchpad.write_trade_state(self.trade_state)
                counter_followup = self._await_offer_response_or_timeout(policy)
            print("  [awake] decision=counter")
            out: Dict[str, Any] = {
                "mode": "reactive_awake",
                "action": "counter",
                "thought": thought,
                "score": round(base_score, 3),
                "threshold": threshold,
                "proposer": proposer,
                "proposer_vp": proposer_vp,
                "tool_result": tool_result,
                "counter_args": {"offer": args.get("offer"), "request": args.get("request")},
            }
            if counter_followup is not None:
                out["counter_followup"] = counter_followup
            self._report_trade_response(out)
            self.scratchpad.append_action(ActionRecord(
                agent="trading",
                action="counter_trade",
                args={"awake": True, **args},
                result=dict(tool_result),
                success=bool(tool_result.get("success", False)),
            ))
            return

        self._report_trade_response({
            "mode": "reactive_awake",
            "action": "unknown",
            "thought": thought,
            "tool_result": tool_result,
        })

    def _attempt_bank_trade(self, policy: TradePolicy) -> Optional[Dict[str, Any]]:
        options_result = self._exec_tool("get_trade_options", {})
        options = options_result.get("options", []) if isinstance(options_result, dict) else []
        if not isinstance(options, list):
            options = []

        desperately_need = set(policy.desperately_need)
        willing_to_give = set(policy.willing_to_give)
        max_ratio = int(policy.max_bank_ratio_acceptable or 4)

        for opt in options:
            if not isinstance(opt, dict):
                continue
            give = str(opt.get("give", ""))
            get = str(opt.get("get", ""))
            amount = int(opt.get("amount", 4) or 4)
            if get not in desperately_need:
                continue
            if willing_to_give and give not in willing_to_give:
                continue
            if amount > max_ratio:
                continue

            result = self._exec_tool(
                "bank_trade",
                {"give_resource": give, "give_amount": amount, "get_resource": get},
            )
            if bool(result.get("success", True)):
                self._record_trade_outcome(
                    counterpart="bank",
                    offer={give: amount},
                    request={get: 1},
                    accepted=True,
                )
                return {
                    "type": "bank_trade",
                    "offer": {give: amount},
                    "request": {get: 1},
                    "result": result,
                }
        return None

    def _attempt_player_trade(self, policy: TradePolicy) -> Optional[Dict[str, Any]]:
        desired = next((r for r in policy.desperately_need if r in RESOURCES), None)
        give = next((r for r in policy.willing_to_give if r in RESOURCES and r != desired), None)
        if not desired or not give:
            return None

        # Avoid offering resources to a near-winning opponent when possible.
        blocked = set(self._top_threat_player_names(vp_threshold=8))
        target = self._choose_trade_target(exclude_names=blocked)

        propose_args = {"offer": {give: 1}, "request": {desired: 1}}
        result = self._exec_tool("propose_trade", propose_args)
        success = bool(result.get("success", True))
        if success:
            self.trade_state.pending_offer = {
                "offer": {give: 1},
                "request": {desired: 1},
                "target": target,
            }
            self.scratchpad.write_trade_state(self.trade_state)
            timeout_result = self._await_offer_response_or_timeout(policy)
            return {
                "type": "player_trade",
                "target": target,
                "offer": {give: 1},
                "request": {desired: 1},
                "result": result,
                "post_offer": timeout_result,
            }
        return {
            "type": "player_trade",
            "target": target,
            "offer": {give: 1},
            "request": {desired: 1},
            "result": result,
        }

    def _score_trade(
        self, offer: Dict[str, int], request: Dict[str, int], trade_policy: TradePolicy,
    ) -> float:
        """
        Deterministic pre-filter before any optional LLM decision.
        Returns value in [0, 1] where higher is better for us.
        """
        desired = set(trade_policy.desperately_need)
        willing = set(trade_policy.willing_to_give)

        score = 0.5
        for res, amt in offer.items():
            if res in desired:
                score += 0.25 * amt
            elif res in willing:
                score += 0.05 * amt
            else:
                score -= 0.10 * amt

        for res, amt in request.items():
            if res in desired:
                score -= 0.20 * amt
            elif res in willing:
                score -= 0.05 * amt
            else:
                score -= 0.12 * amt

        return max(0.0, min(1.0, score))

    def _build_counter_offer(
        self,
        incoming_offer: Dict[str, int],
        incoming_request: Dict[str, int],
        policy: TradePolicy,
    ) -> Optional[Dict[str, Dict[str, int]]]:
        """
        Build a counter payload using server semantics:
          - counter offer = what we give
          - counter request = what we want
        """
        counter_offer = dict(incoming_request)   # what they asked from us
        counter_request = dict(incoming_offer)   # what they offered to us

        if not counter_offer or not counter_request:
            return None

        # Try to improve terms in our favor: ask +1 of a needed resource.
        desired = [r for r in policy.desperately_need if r in RESOURCES]
        if desired:
            target = desired[0]
            counter_request[target] = int(counter_request.get(target, 0) or 0) + 1
        else:
            largest_get = max(counter_request.items(), key=lambda kv: kv[1])[0]
            counter_request[largest_get] = int(counter_request.get(largest_get, 0) or 0) + 1

        # If we would give any desperately-needed resource, try reducing it by 1.
        for res in list(counter_offer.keys()):
            amt = int(counter_offer.get(res, 0) or 0)
            if res in desired and amt > 1:
                counter_offer[res] = amt - 1
                break

        counter_offer = self._normalize_resource_dict(counter_offer)
        counter_request = self._normalize_resource_dict(counter_request)
        if not counter_offer or not counter_request:
            return None
        return {"offer": counter_offer, "request": counter_request}

    def _report_results(self, results: Dict[str, Any]) -> None:
        self.send_message("strategy", "inform", {"trade_results": results})
        try:
            self.call_agent("strategy", "report_trade_results", results=results)
        except Exception:
            # Strategy callback is optional; message path is authoritative.
            pass

    def _report_trade_response(self, result: Dict[str, Any]) -> None:
        self.send_message("strategy", "inform", {"trade_response": result})
        try:
            self.call_agent("strategy", "report_trade_results", results=result)
        except Exception:
            pass

    def _record_trade_outcome(
        self,
        counterpart: str,
        offer: Dict[str, int],
        request: Dict[str, int],
        accepted: bool,
    ) -> None:
        rec = {
            "counterpart": counterpart,
            "offer": offer,
            "request": request,
            "accepted": accepted,
        }
        self.trade_state.recent_trades.append(rec)
        self.trade_state.recent_trades = self.trade_state.recent_trades[-20:]
        if counterpart not in self.trade_state.player_trade_history:
            self.trade_state.player_trade_history[counterpart] = []
        self.trade_state.player_trade_history[counterpart].append(rec)

        delta = 0.05 if accepted else -0.03
        current = float(self.trade_state.player_reputation.get(counterpart, 0.0))
        self.trade_state.player_reputation[counterpart] = max(-1.0, min(1.0, current + delta))
        self.scratchpad.write_trade_state(self.trade_state)

        self.scratchpad.append_action(ActionRecord(
            agent="trading",
            action="trade_outcome",
            args={"counterpart": counterpart, "offer": offer, "request": request},
            result={"accepted": accepted},
            success=True,
        ))

    def _exec_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if self.registry is None:
            return {"success": False, "error": f"tool registry unavailable for {name}"}
        return self.registry.execute(name, args)

    def _await_offer_response_or_timeout(self, policy: TradePolicy) -> Dict[str, Any]:
        """Wait for offer resolution; if stale at 60s, cancel and fallback."""
        deadline = time.monotonic() + TRADE_RESPONSE_TIMEOUT_SEC
        while time.monotonic() < deadline:
            status = self._exec_tool("get_trade_offer_status", {})
            has_offer = bool(status.get("has_offer", False))
            from_me = bool(status.get("offer_from_me", False))
            if not has_offer or not from_me:
                self.trade_state.pending_offer = None
                self.scratchpad.write_trade_state(self.trade_state)
                return {
                    "status": "resolved_before_timeout",
                    "timeout_sec": TRADE_RESPONSE_TIMEOUT_SEC,
                    "offer_status": status,
                }
            time.sleep(TRADE_POLL_INTERVAL_SEC)

        cancel_result = self._exec_tool("cancel_trade", {})
        fallback_result = self._attempt_bank_trade(policy)
        self.trade_state.pending_offer = None
        self.scratchpad.write_trade_state(self.trade_state)
        return {
            "status": "timeout",
            "timeout_sec": TRADE_RESPONSE_TIMEOUT_SEC,
            "cancel_result": cancel_result,
            "fallback": fallback_result or {"type": "none"},
        }

    def _extract_offer_proposer(self, offer_state: Dict[str, Any]) -> str:
        for key in ("from", "from_id", "fromId", "proposer", "player"):
            value = offer_state.get(key)
            if value is not None:
                return str(value)
        return "unknown"

    def _lookup_player_vp(self, player_identifier: str) -> int:
        state = self.scratchpad.game_state
        players = state.get("players") if isinstance(state, dict) else None
        if not isinstance(players, list):
            return 0
        for idx, p in enumerate(players):
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id", ""))
            name = str(p.get("name", ""))
            if player_identifier in (pid, name, str(idx)):
                return int(p.get("victoryPoints", 0) or p.get("vp", 0) or 0)
        return 0

    def _top_threat_player_names(self, vp_threshold: int) -> List[str]:
        state = self.scratchpad.game_state
        players = state.get("players") if isinstance(state, dict) else None
        names: List[str] = []
        if not isinstance(players, list):
            return names
        for p in players:
            if not isinstance(p, dict):
                continue
            vp = int(p.get("victoryPoints", 0) or p.get("vp", 0) or 0)
            if vp >= vp_threshold:
                names.append(str(p.get("name", "")))
        return names

    def _choose_trade_target(self, exclude_names: set) -> Optional[str]:
        state = self.scratchpad.game_state
        players = state.get("players") if isinstance(state, dict) else None
        my_idx = state.get("myIndex") if isinstance(state, dict) else None
        if not isinstance(players, list):
            return None

        best_name: Optional[str] = None
        best_score = -10.0
        for idx, p in enumerate(players):
            if not isinstance(p, dict):
                continue
            if isinstance(my_idx, int) and idx == my_idx:
                continue
            name = str(p.get("name", ""))
            if name in exclude_names:
                continue
            vp = int(p.get("victoryPoints", 0) or p.get("vp", 0) or 0)
            reputation = float(self.trade_state.player_reputation.get(name, 0.0))
            score = (1.0 - (vp / 10.0)) + reputation
            if score > best_score:
                best_score = score
                best_name = name
        return best_name

    @staticmethod
    def _normalize_resource_dict(payload: Any) -> Dict[str, int]:
        if not isinstance(payload, dict):
            return {}
        clean: Dict[str, int] = {}
        for key, value in payload.items():
            k = str(key)
            if k not in RESOURCES:
                continue
            try:
                amt = int(value)
            except Exception:
                continue
            if amt > 0:
                clean[k] = amt
        return clean
