"""
Risk Agent — GPT-4o-powered consultant with deterministic math backend.

The Risk Agent is a **consultant**: Strategy asks it questions, and it
reasons over the board state, probability data, and action history to
produce actionable answers via GPT-4o.

It also runs deterministic math every turn (``analyze()``) to populate
the scratchpad with probability data that all agents can read.

Peers: Strategy, Development.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import Scratchpad, RiskAnalysis

from Agent.utils.openai_client import OpenAIClient

from Agent.risk_agent.probabilities import (
    expected_resource_income,
    per_building_income,
    opponent_threat_assessment,
    robber_impact_analysis,
    rank_vertices_by_expected_value,
    rank_cities_by_value_gain,
    win_probability_estimate,
)
from Agent.risk_agent.prompts import (
    RISK_CONSULTANT_SYSTEM_PROMPT,
    RISK_NARRATIVE_SYSTEM_PROMPT,
    RISK_PLAN_ASSESSMENT_PROMPT,
    build_narrative_context,
    build_consult_context,
    build_plan_assessment_context,
)


class RiskAgent(BaseAgent):
    """
    GPT-4o-powered Risk Consultant.

    - ``analyze()``  — deterministic math + GPT-4o narrative (every turn)
    - ``consult(q)`` — Strategy asks a risk question → GPT-4o answers
    - ``assess_plan(plan)`` — Strategy passes a draft plan → GPT-4o critiques

    Peers: Strategy, Development.
    """

    def __init__(
        self,
        scratchpad: Scratchpad,
        openai: Optional[OpenAIClient] = None,
    ) -> None:
        super().__init__("risk", scratchpad)
        self.openai = openai

    # ──────────────────────────────────────────────────────────────
    # Primary entry point (called by Strategy every turn)
    # ──────────────────────────────────────────────────────────────

    def analyze(self) -> RiskAnalysis:
        """
        Run all deterministic calculations, then generate a threat
        narrative via GPT-4o.

        Called by Strategy at the start of each turn.
        """
        state = self.scratchpad.game_state

        income = expected_resource_income(state)
        buildings = per_building_income(state)
        threats = opponent_threat_assessment(state)
        win_probs = win_probability_estimate(state)
        robber = robber_impact_analysis(state)
        best_settlements = rank_vertices_by_expected_value(state, top_k=5)
        best_cities = rank_cities_by_value_gain(state, top_k=5)

        # Generate threat narrative via GPT-4o (graceful fallback)
        narrative = self._generate_narrative({
            "income": income,
            "threats": threats,
            "win_probs": win_probs,
            "best_robber_target": robber[0] if robber else None,
        })

        analysis = RiskAnalysis(
            resource_expected_income=income,
            per_building_income=buildings,
            opponent_threats=threats,
            win_probabilities=win_probs,
            robber_impact=robber,
            best_settlement_vertices=best_settlements,
            best_city_vertices=best_cities,
            threat_narrative=narrative,
        )

        self.scratchpad.write_risk_analysis(analysis)
        print(f"  [risk] income={income}")
        print(f"  [risk] threats={len(threats)} opponents assessed")
        print(f"  [risk] narrative: {narrative[:120]}...")
        return analysis

    # ──────────────────────────────────────────────────────────────
    # Consultation (Strategy asks a risk question)
    # ──────────────────────────────────────────────────────────────

    def consult(self, question: str) -> str:
        """
        Answer a risk question from Strategy using GPT-4o.

        Builds context from the scratchpad: latest risk analysis,
        game state, and recent action log.  Returns a plain-English
        recommendation (3-5 sentences).

        Falls back to a deterministic answer if GPT-4o is unavailable.
        """
        risk_data = asdict(self.scratchpad.risk_analysis)
        state_json = self.scratchpad.to_state_json()
        action_log = [asdict(a) for a in self.scratchpad.action_log[-10:]]

        context = build_consult_context(
            question=question,
            risk_analysis=risk_data,
            state_json=state_json,
            action_log=action_log,
        )

        answer = self._call_gpt4o(
            system=RISK_CONSULTANT_SYSTEM_PROMPT,
            user=context,
            fallback=self._fallback_consult(question),
        )

        print(f"  [risk] consult Q: {question[:80]}")
        print(f"  [risk] consult A: {answer[:120]}...")
        return answer

    # ──────────────────────────────────────────────────────────────
    # Plan Assessment (Strategy asks Risk to critique a draft plan)
    # ──────────────────────────────────────────────────────────────

    def assess_plan(self, plan: Dict[str, Any]) -> str:
        """
        Critique a draft strategy plan against current risk data.

        Returns a plain-English assessment with up to 3 concerns.
        """
        risk_data = asdict(self.scratchpad.risk_analysis)
        state_json = self.scratchpad.to_state_json()

        context = build_plan_assessment_context(
            plan=plan,
            risk_analysis=risk_data,
            state_json=state_json,
        )

        assessment = self._call_gpt4o(
            system=RISK_PLAN_ASSESSMENT_PROMPT,
            user=context,
            fallback="No assessment available — GPT-4o unreachable.",
        )

        print(f"  [risk] plan assessment: {assessment[:120]}...")
        return assessment

    # ──────────────────────────────────────────────────────────────
    # Methods callable by peer agents
    # ──────────────────────────────────────────────────────────────

    def get_robber_targets(self) -> List[Dict]:
        """
        Called by Development when handling robber phase.
        Returns hexes ranked by net damage to opponents.
        """
        state = self.scratchpad.game_state
        return robber_impact_analysis(state)

    def get_best_building_spots_ev(
        self, building_type: str = "settlement",
    ) -> List[Dict]:
        """
        Called by Development when choosing where to build.
        Returns vertices ranked by expected value or city upgrade gain.
        """
        state = self.scratchpad.game_state
        if building_type == "city":
            return rank_cities_by_value_gain(state)
        return rank_vertices_by_expected_value(state)

    def get_opponent_threat_summary(self) -> List[Dict]:
        """
        Called by Strategy for a quick threat check.
        Returns cached threats or recomputes from scratchpad.
        """
        cached = self.scratchpad.risk_analysis.opponent_threats
        if cached:
            return cached
        state = self.scratchpad.game_state
        return opponent_threat_assessment(state)

    # ──────────────────────────────────────────────────────────────
    # GPT-4o wrapper
    # ──────────────────────────────────────────────────────────────

    def _call_gpt4o(
        self, system: str, user: str, fallback: str,
    ) -> str:
        """
        Send a system + user message to GPT-4o and return the text.
        Falls back to ``fallback`` if OpenAI is unavailable.
        """
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
            print(f"  [risk] GPT-4o call failed: {e}")

        return fallback

    # ──────────────────────────────────────────────────────────────
    # Narrative generation (part of analyze())
    # ──────────────────────────────────────────────────────────────

    def _generate_narrative(
        self, analysis_data: Dict[str, Any],
    ) -> str:
        """Generate a 2-3 sentence threat narrative via GPT-4o."""
        context = build_narrative_context(analysis_data)
        return self._call_gpt4o(
            system=RISK_NARRATIVE_SYSTEM_PROMPT,
            user=context,
            fallback=self._fallback_narrative(analysis_data),
        )

    # ──────────────────────────────────────────────────────────────
    # Deterministic fallbacks (when GPT-4o is unavailable)
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _fallback_narrative(data: Dict[str, Any]) -> str:
        """Deterministic template when GPT-4o is unavailable."""
        threats = data.get("threats") or []
        income = data.get("income") or {}
        win_probs = data.get("win_probs") or {}

        parts: List[str] = []

        if threats:
            top = threats[0]
            parts.append(
                f"{top.get('name', '?')} is the biggest threat at "
                f"{top.get('vp', '?')} VP "
                f"(~{top.get('turns_to_win_estimate', '?')} turns to win, "
                f"threat level: {top.get('threat_level', '?')})."
            )

        if win_probs:
            our_prob = max(win_probs.values())
            parts.append(f"Our estimated win probability: {our_prob:.0%}.")

        if income:
            weakest = min(income.items(), key=lambda kv: kv[1])
            parts.append(
                f"Weakest income: {weakest[0]} ({weakest[1]:.2f}/turn)."
            )

        return " ".join(parts) if parts else "No threat data available."

    def _fallback_consult(self, question: str) -> str:
        """Deterministic consultation fallback using cached risk data."""
        risk = self.scratchpad.risk_analysis
        threats = risk.opponent_threats
        income = risk.resource_expected_income

        parts = [f"[Deterministic fallback for: {question}]"]

        if threats:
            top = threats[0]
            parts.append(
                f"Top threat: {top.get('name', '?')} at {top.get('vp', '?')} VP "
                f"(level: {top.get('threat_level', '?')})."
            )

        if income:
            weakest = min(income.items(), key=lambda kv: kv[1])
            strongest = max(income.items(), key=lambda kv: kv[1])
            parts.append(
                f"Strongest income: {strongest[0]} ({strongest[1]:.2f}/turn). "
                f"Weakest: {weakest[0]} ({weakest[1]:.2f}/turn)."
            )

        return " ".join(parts)
