"""
Risk-analysis tools for the ToolRegistry.

These 5 tools wrap the deterministic functions in
``Agent.risk_agent.probabilities`` so that agents with tool access
(Strategy, Development) can query risk data through the standard
registry/function-calling interface.

Each tool reads the **latest** game state from the socket client,
ensuring fresh data every call.
"""
from __future__ import annotations

from typing import Any, Dict, List

from Agent.utils.socket_client import CatanSocketClient
from Agent.risk_agent.probabilities import (
    expected_resource_income,
    opponent_threat_assessment,
    robber_impact_analysis,
    rank_vertices_by_expected_value,
    win_probability_estimate,
)


def register_risk_tools(
    registry: Any,
    client: CatanSocketClient,
) -> None:
    """
    Register the 5 risk-analysis tools on *registry*.

    Imports ToolDefinition and ToolParameter locally to avoid circular
    imports (registry.py imports game_tools which is unrelated to risk).
    """
    from Agent.Tools.registry import ToolDefinition, ToolParameter

    # ── 1. get_expected_income ────────────────────────────────────
    def _get_expected_income() -> Dict[str, Any]:
        state = client.latest_state() or {}
        income = expected_resource_income(state)
        total = sum(income.values())
        return {
            "success": True,
            "income_per_turn": income,
            "total_income": round(total, 4),
        }

    registry.register(ToolDefinition(
        name="get_expected_income",
        description=(
            "Get the expected resource income per turn from your current "
            "buildings.  Uses standard Catan probability (pips/36).  "
            "Accounts for robber blocking."
        ),
        parameters=[],
        handler=_get_expected_income,
        phases=["roll", "main", "robber", "discard", "setup", "specialBuild"],
        agents=["strategy", "development"],
    ))

    # ── 2. get_opponent_threats ───────────────────────────────────
    def _get_opponent_threats() -> Dict[str, Any]:
        state = client.latest_state() or {}
        threats = opponent_threat_assessment(state)
        return {
            "success": True,
            "threats": threats,
            "count": len(threats),
        }

    registry.register(ToolDefinition(
        name="get_opponent_threats",
        description=(
            "Get a ranked list of opponent threats.  Each entry includes "
            "VP, income rate, road/army progress, estimated turns to win, "
            "and a threat level (critical/high/medium/low)."
        ),
        parameters=[],
        handler=_get_opponent_threats,
        phases=["roll", "main", "robber", "discard", "setup", "specialBuild"],
        agents=["strategy", "development"],
    ))

    # ── 3. get_robber_targets ─────────────────────────────────────
    def _get_robber_targets() -> Dict[str, Any]:
        state = client.latest_state() or {}
        targets = robber_impact_analysis(state)
        return {
            "success": True,
            "targets": targets[:10],  # top 10
            "count": len(targets),
        }

    registry.register(ToolDefinition(
        name="get_robber_targets",
        description=(
            "Get hexes ranked by how much opponent production they'd block "
            "if the robber were placed there, minus our own production loss.  "
            "Use during robber phase or main phase to plan knight plays."
        ),
        parameters=[],
        handler=_get_robber_targets,
        phases=["robber", "main"],
        agents=["strategy", "development"],
    ))

    # ── 4. get_best_building_spots_ev ─────────────────────────────
    def _get_best_building_spots_ev(
        building_type: str = "settlement",
    ) -> Dict[str, Any]:
        state = client.latest_state() or {}
        building_type = str(building_type or "settlement").lower()
        if building_type == "city":
            from Agent.risk_agent.probabilities import rank_cities_by_value_gain
            spots = rank_cities_by_value_gain(state, top_k=5)
        else:
            spots = rank_vertices_by_expected_value(state, top_k=10)
        return {
            "success": True,
            "building_type": building_type,
            "spots": spots,
        }

    registry.register(ToolDefinition(
        name="get_best_building_spots_ev",
        description=(
            "Get vertices ranked by expected value for settlement or city.  "
            "Uses probability-weighted scoring adjusted by resource scarcity.  "
            "More precise than get_building_spots (which uses raw pip scores)."
        ),
        parameters=[
            ToolParameter(
                "building_type", "string",
                "Type: 'settlement' or 'city'",
                required=False,
                enum=["settlement", "city"],
            ),
        ],
        handler=_get_best_building_spots_ev,
        phases=["main", "setup"],
        agents=["strategy", "development"],
    ))

    # ── 5. get_win_probabilities ──────────────────────────────────
    def _get_win_probabilities() -> Dict[str, Any]:
        state = client.latest_state() or {}
        probs = win_probability_estimate(state)
        return {
            "success": True,
            "probabilities": probs,
        }

    registry.register(ToolDefinition(
        name="get_win_probabilities",
        description=(
            "Get estimated win probability for each player.  "
            "Heuristic based on VP, income rate, dev cards, and "
            "road/army progress.  Probabilities sum to 1.0."
        ),
        parameters=[],
        handler=_get_win_probabilities,
        phases=["roll", "main", "robber", "discard", "setup", "specialBuild"],
        agents=["strategy"],
    ))
