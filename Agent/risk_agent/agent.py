"""
Risk Agent — STUB implementation.

Provides no-op methods so StrategyAgent.call_agent("risk", "analyze") works
without crashing.  Replace with real implementation from probabilities.py
and Ollama threat narrative.
"""
from __future__ import annotations

from typing import Any, Dict, List

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import Scratchpad, RiskAnalysis


class RiskAgent(BaseAgent):
    """
    Stub Risk Agent.  All methods return empty/default data.
    Real implementation should use probabilities.py + Ollama.
    """

    def __init__(self, scratchpad: Scratchpad, ollama: Any = None) -> None:
        super().__init__("risk", scratchpad)
        self.ollama = ollama  # OllamaChat instance (unused in stub)

    def analyze(self) -> RiskAnalysis:
        """
        Run all deterministic calculations, then generate threat narrative.
        Called by Strategy at the start of each turn.

        STUB: returns empty RiskAnalysis.
        """
        analysis = RiskAnalysis(
            threat_narrative="[stub] No risk analysis available.",
        )
        self.scratchpad.write_risk_analysis(analysis)
        return analysis

    def get_robber_targets(self) -> List[Dict]:
        """
        Called by Development when handling robber phase.
        STUB: returns empty list.
        """
        return []

    def get_best_building_spots_ev(self, building_type: str = "settlement") -> List[Dict]:
        """
        Called by Development when choosing where to build.
        STUB: returns empty list.
        """
        return []

    def get_opponent_threat_summary(self) -> List[Dict]:
        """
        Called by Strategy for quick threat check.
        STUB: returns empty list.
        """
        return []
