"""
Development Agent — STUB implementation.

Provides no-op methods so StrategyAgent.call_agent("development", ...)
works without crashing.  Replace with real GPT-4o build-queue execution,
discard handling, and robber placement.
"""
from __future__ import annotations

from typing import Any, Dict, List

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import Scratchpad, ActionRecord


class DevelopmentAgent(BaseAgent):
    """
    Stub Development Agent.  All methods are no-ops.
    Real implementation should execute build_queue, handle_discard, handle_robber.
    """

    def __init__(
        self, scratchpad: Scratchpad, openai: Any = None, registry: Any = None,
    ) -> None:
        super().__init__("development", scratchpad)
        self.openai = openai
        self.registry = registry

    def execute_build_queue(self) -> List[ActionRecord]:
        """
        Execute Strategy's build queue using tools.
        Called by Strategy via call_agent("development", "execute_build_queue").

        STUB: no-op, returns empty list.
        """
        print("  [development] (stub) execute_build_queue — no actions taken")
        self.send_message("strategy", "inform", {
            "build_results": [],
            "note": "Development agent is a stub — no builds executed.",
        })
        return []

    def handle_discard(self) -> None:
        """
        Discard cards guided by strategy_plan.priority_resources.
        Called by Strategy via call_agent("development", "handle_discard").

        STUB: no-op.
        """
        print("  [development] (stub) handle_discard — no action taken")

    def handle_robber(self) -> None:
        """
        Place robber using Risk Agent data + strategy guidance.
        Called by Strategy via call_agent("development", "handle_robber").

        STUB: no-op.
        """
        print("  [development] (stub) handle_robber — no action taken")
