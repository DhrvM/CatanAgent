"""
Trading Agent — STUB implementation.

Provides no-op methods so StrategyAgent.call_agent("trading", ...)
works without crashing.  Replace with real GPT-4o negotiation,
proactive/reactive trade logic, and reputation scoring.
"""
from __future__ import annotations

from typing import Any, Dict

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import Scratchpad, TradeState


class TradingAgent(BaseAgent):
    """
    Stub Trading Agent.  All methods are no-ops.
    Real implementation should handle proactive + reactive trades via GPT-4o.
    """

    def __init__(
        self, scratchpad: Scratchpad, openai: Any = None, registry: Any = None,
    ) -> None:
        super().__init__("trading", scratchpad)
        self.openai = openai
        self.registry = registry
        self.trade_state = TradeState()

    def proactive_trade(self) -> None:
        """
        Propose trades if Strategy recommends it.
        Called by Strategy via call_agent("trading", "proactive_trade").

        STUB: no-op.
        """
        print("  [trading] (stub) proactive_trade — no trades proposed")
        self.send_message("strategy", "inform", {
            "trade_results": [],
            "note": "Trading agent is a stub — no trades executed.",
        })

    def respond_to_offer(self) -> None:
        """
        Evaluate and respond to an incoming trade offer.
        Called by Strategy via call_agent("trading", "respond_to_offer").

        STUB: no-op.
        """
        print("  [trading] (stub) respond_to_offer — no action taken")
        self.send_message("strategy", "inform", {
            "trade_response": {"action": "ignored", "reason": "stub agent"},
        })
