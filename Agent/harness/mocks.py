"""
Mock socket client and stub OpenAI for offline agent tests.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class MockSocketClient:
    """
    Minimal stand-in for CatanSocketClient: holds ``latest_state`` and logs ``sio.call``.
    """
    _state: Dict[str, Any]
    calls: List[Tuple[str, Optional[Dict[str, Any]]]] = field(default_factory=list)

    def connect(self) -> None:
        return None

    def join_game(self, game_code: str, player_name: str) -> None:
        return None

    def create_game(self, player_name: str) -> Dict[str, Any]:
        return {"gameCode": "MOCK", "success": True}

    def latest_state(self) -> Optional[Dict[str, Any]]:
        return self._state

    def set_state(self, state: Dict[str, Any]) -> None:
        self._state = state

    def get_all_events(self) -> List[Dict[str, Any]]:
        return []

    @property
    def sio(self) -> "MockSocketClient":
        return self

    def call(
        self,
        event: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        self.calls.append((event, payload))
        return {"success": True, "mock": True, "event": event}


def make_stub_chat_response(
    text: str = "",
    tool_calls: Optional[List[Tuple[str, Any]]] = None,
) -> Any:
    """
    Build a minimal object compatible with ``OpenAIClient.extract_tool_calls`` / ``extract_text``.

    *tool_calls*: list of (function_name, args_dict).
    """
    tool_calls = tool_calls or []
    tcs = []
    for i, (name, args) in enumerate(tool_calls):
        f = SimpleNamespace()
        f.name = name
        f.arguments = json.dumps(args) if isinstance(args, dict) else str(args)
        tc = SimpleNamespace()
        tc.id = f"stub_{i}"
        tc.function = f
        tcs.append(tc)

    msg = SimpleNamespace()
    msg.content = text
    msg.tool_calls = tcs if tcs else None

    ch = SimpleNamespace()
    ch.message = msg

    resp = SimpleNamespace()
    resp.choices = [ch]
    resp.usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return resp


class StubOpenAIClient:
    """
    No network. ``chat_with_tools`` uses *responder* or returns JSON text for Strategy-style calls.
    """

    model = "stub"

    def __init__(
        self,
        responder: Optional[Callable[[List[Dict], Optional[List]], Any]] = None,
        default_plan_json: str = '{"long_term_goal": "balanced", "build_queue": [], "trade_policy": {"willing_to_give": ["brick"], "desperately_need": ["ore"], "max_bank_ratio_acceptable": 4, "should_propose_trades": false, "min_accept_score": 0.5}, "priority_resources": ["ore", "grain"], "short_term_goals": [], "risk_tolerance": "moderate", "reasoning": "stub"}',
    ) -> None:
        self._responder = responder
        self._default_plan_json = default_plan_json
        self.last_messages: Optional[List[Dict[str, Any]]] = None
        self.last_tools: Optional[List[Dict[str, Any]]] = None
        self.temperature = 0.3
        self.max_tokens = 2048

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_retries: int = 5,
    ) -> Any:
        self.last_messages = messages
        self.last_tools = tools
        if self._responder:
            return self._responder(messages, tools)
        return make_stub_chat_response(text=self._default_plan_json, tool_calls=[])

    @staticmethod
    def extract_tool_calls(response: Any) -> List[Dict[str, Any]]:
        from Agent.utils.openai_client import OpenAIClient
        return OpenAIClient.extract_tool_calls(response)

    @staticmethod
    def extract_text(response: Any) -> str:
        from Agent.utils.openai_client import OpenAIClient
        return OpenAIClient.extract_text(response)

    @staticmethod
    def extract_usage(response: Any) -> Dict[str, int]:
        from Agent.utils.openai_client import OpenAIClient
        return OpenAIClient.extract_usage(response)

    @staticmethod
    def build_tool_result_message(tool_call_id: str, result: Any) -> Dict[str, Any]:
        from Agent.utils.openai_client import OpenAIClient
        return OpenAIClient.build_tool_result_message(tool_call_id, result)


class HarnessOpenAI(StubOpenAIClient):
    """
    Offline stub for the **agent harness**: Trading + Development tool scripts with
    ``[thought]`` / ``[action]``-style behavior (no API key / no network).

    Covers Development discard / robber prompts and Trading proactive / awake flows.
    """

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_retries: int = 5,
    ) -> Any:
        self.last_messages = messages
        self.last_tools = tools

        assistant_rounds = sum(1 for m in messages if m.get("role") == "assistant")

        blob = "".join(str(m.get("content", "")) for m in messages)

        # ── Development Agent — discard (build_discard_user_message) ──
        if "You must choose which resource cards to discard" in blob:
            return make_stub_chat_response(
                "Keeping ore and grain per Strategy priorities; discarding surplus brick "
                "and lumber first to meet the exact count.",
                [("discard_cards", {
                    "resources": {"brick": 3, "lumber": 3},
                })],
            )

        # ── Development Agent — robber (build_robber_user_message) ──
        if "Move the robber to a valid hex" in blob:
            return make_stub_chat_response(
                "Aligning with Risk/heuristic: block Alice on lumber at 2,-1 and steal.",
                [("move_robber", {
                    "hex_key": "2,-1",
                    "steal_from_player_id": "p1",
                })],
            )

        is_proactive = any(
            m.get("role") == "user" and "Proactive trading" in str(m.get("content", ""))
            for m in messages
        )
        is_awake = any(
            m.get("role") == "user"
            and (
                "Incoming trade offer" in str(m.get("content", ""))
                or "off-turn" in str(m.get("content", "")).lower()
            )
            for m in messages
        )

        # Harness scenario 5 — proactive: bank-first (marker in Strategy plan JSON)
        if is_proactive and "[HARNESS:proactive_bank]" in blob:
            if assistant_rounds == 0:
                return make_stub_chat_response(
                    "Eight brick but no ore; Strategy wants ore for a city. Checking bank "
                    "and port trade options before involving opponents.",
                    [("get_trade_options", {})],
                )
            if assistant_rounds == 1:
                return make_stub_chat_response(
                    "Standard bank ratio is 4:1 for brick→ore; executing one conversion now.",
                    [("bank_trade", {
                        "give_resource": "brick",
                        "give_amount": 4,
                        "get_resource": "ore",
                    })],
                )
            return make_stub_chat_response(
                "Bank conversion complete; stopping proactive trading for this step.",
                [],
            )

        if is_proactive:
            if assistant_rounds == 0:
                return make_stub_chat_response(
                    "Following Strategy: we need ore for a city. I will check bank trade "
                    "rates before offering to other players.",
                    [("get_trade_options", {})],
                )
            if assistant_rounds == 1:
                return make_stub_chat_response(
                    "Bank ratios are acceptable but slow; proposing 1 brick for 1 ore to "
                    "the table per our willing_to_give / desperately_need.",
                    [("propose_trade", {"offer": {"brick": 1}, "request": {"ore": 1}})],
                )
            return make_stub_chat_response(
                "Ending proactive trading loop for this turn.",
                [],
            )

        # Harness scenario 6 — reactive: counter (marker on proposer name in state JSON)
        if is_awake and "[HARNESS:reactive_counter]" in blob:
            return make_stub_chat_response(
                "Asking two grain for one lumber is steep; countering a fairer 1 grain "
                "for 1 lumber to stay in the game without overpaying.",
                [("counter_trade", {
                    "offer": {"grain": 1},
                    "request": {"lumber": 1},
                })],
            )

        if is_awake:
            return make_stub_chat_response(
                "Their offer undervalues our grain given Alice's VP; declining to keep "
                "flexibility for roads.",
                [("respond_to_trade", {"accept": False})],
            )

        return make_stub_chat_response(text=self._default_plan_json, tool_calls=[])


TradingHarnessOpenAI = HarnessOpenAI
