"""Objective self-play reward for calibrating heuristic weights.

This intentionally avoids benchmark task success so the optimizer does not
learn to overfit the grader it is supposed to improve.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional

from Agent.Tools.game_tools import (
    RESOURCES,
    _adjacent_hexes_for_vertex,
    _adjacent_vertices,
    _edges_touching_vertex,
    _pip_value,
)

TARGET_EXPECTED_INCOME = 3.0
DIVERSITY_INCOME_THRESHOLD = 0.15
TARGET_EXPANSION_SPOTS = 3.0


@dataclass(frozen=True)
class RewardWeights:
    """Weights for the objective self-play reward."""

    win: float = 1.00
    final_vp: float = 0.40
    expected_income: float = 0.20
    resource_diversity: float = 0.20
    expansion_capacity: float = 0.20


@dataclass(frozen=True)
class RewardBreakdown:
    """Reward components in normalized [0, 1] space plus weighted total."""

    player_index: int
    player_name: str
    win: float
    final_vp: float
    expected_income: float
    resource_diversity: float
    expansion_capacity: float
    total: float

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def score_player_state(
    state: Mapping[str, Any],
    *,
    player_index: Optional[int] = None,
    player_id: Optional[Any] = None,
    player_name: Optional[str] = None,
    weights: RewardWeights = RewardWeights(),
) -> RewardBreakdown:
    """Score one player from a terminal or near-terminal game state."""

    idx = _resolve_player_index(
        state,
        player_index=player_index,
        player_id=player_id,
        player_name=player_name,
    )
    players = _players(state)
    player = players[idx]
    income = expected_income_by_resource(state, idx)

    win = 1.0 if _player_won(state, idx, player) else 0.0
    final_vp = _clamp01(_victory_points(player) / 10.0)
    expected_income = _clamp01(sum(income.values()) / TARGET_EXPECTED_INCOME)
    resource_diversity = _clamp01(
        sum(1 for amount in income.values() if amount >= DIVERSITY_INCOME_THRESHOLD) / float(len(RESOURCES))
    )
    expansion_capacity = _clamp01(
        connected_legal_settlement_spots(state, idx) / TARGET_EXPANSION_SPOTS
    )

    total = (
        weights.win * win
        + weights.final_vp * final_vp
        + weights.expected_income * expected_income
        + weights.resource_diversity * resource_diversity
        + weights.expansion_capacity * expansion_capacity
    )

    return RewardBreakdown(
        player_index=idx,
        player_name=str(_player_name(player, idx)),
        win=round(win, 4),
        final_vp=round(final_vp, 4),
        expected_income=round(expected_income, 4),
        resource_diversity=round(resource_diversity, 4),
        expansion_capacity=round(expansion_capacity, 4),
        total=round(total, 4),
    )


def score_all_players(
    state: Mapping[str, Any],
    *,
    weights: RewardWeights = RewardWeights(),
) -> List[RewardBreakdown]:
    """Return reward breakdowns for every player in a state."""

    return [
        score_player_state(state, player_index=i, weights=weights)
        for i, _ in enumerate(_players(state))
    ]


def expected_income_by_resource(state: Mapping[str, Any], player_index: int) -> Dict[str, float]:
    """Expected resource cards per roll, by resource, for one player."""

    income = {resource: 0.0 for resource in RESOURCES}
    vertices = _mapping(state.get("vertices"))
    robber = state.get("robber")

    for vertex_key, vertex in vertices.items():
        if not isinstance(vertex, Mapping):
            continue
        if not _is_player_building(vertex, player_index):
            continue

        multiplier = 2.0 if _building_type(vertex) == "city" else 1.0
        for hex_info in _adjacent_hexes_for_vertex(dict(state), str(vertex_key)):
            if not isinstance(hex_info, Mapping):
                continue
            resource = hex_info.get("resource")
            if resource not in income:
                continue
            if _hex_key(hex_info) == robber:
                continue
            token = hex_info.get("token", hex_info.get("number"))
            income[str(resource)] += (_pip_value(token) / 36.0) * multiplier

    return {resource: round(value, 4) for resource, value in income.items()}


def connected_legal_settlement_spots(state: Mapping[str, Any], player_index: int) -> int:
    """Count empty distance-rule-safe vertices connected to the player's roads."""

    vertices = _mapping(state.get("vertices"))
    count = 0

    for vertex_key, vertex in vertices.items():
        if not isinstance(vertex, Mapping):
            continue
        if _building_type(vertex):
            continue
        if not _distance_rule_open(vertices, str(vertex_key)):
            continue
        if _touches_player_road(state, str(vertex_key), player_index):
            count += 1

    return count


def _resolve_player_index(
    state: Mapping[str, Any],
    *,
    player_index: Optional[int],
    player_id: Optional[Any],
    player_name: Optional[str],
) -> int:
    players = _players(state)

    if player_index is not None:
        if 0 <= player_index < len(players):
            return player_index
        raise ValueError(f"player_index {player_index} is out of range for {len(players)} players")

    for i, player in enumerate(players):
        if player_id is not None and str(_player_id(player, i)) == str(player_id):
            return i
        if player_name is not None and _player_name(player, i) == player_name:
            return i

    if player_id is not None or player_name is not None:
        raise ValueError("Could not resolve player from player_id/player_name")
    raise ValueError("Provide player_index, player_id, or player_name")


def _players(state: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    players = state.get("players")
    if not isinstance(players, list):
        raise ValueError("State must include a players list")
    return [p if isinstance(p, Mapping) else {} for p in players]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _player_id(player: Mapping[str, Any], fallback: int) -> Any:
    return player.get("id", player.get("playerId", player.get("userId", fallback)))


def _player_name(player: Mapping[str, Any], fallback: int) -> str:
    return str(player.get("name", player.get("playerName", player.get("username", f"Player {fallback + 1}"))))


def _victory_points(player: Mapping[str, Any]) -> float:
    for key in ("victoryPoints", "victory_points", "vp", "finalVictoryPoints", "final_victory_points"):
        value = player.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _player_won(state: Mapping[str, Any], player_index: int, player: Mapping[str, Any]) -> bool:
    if player.get("won") is True or player.get("isWinner") is True:
        return True

    winner_index = state.get("winnerIndex", state.get("winner_index"))
    if winner_index is not None:
        try:
            return int(winner_index) == player_index
        except Exception:
            pass

    winner = state.get("winner", state.get("winnerId", state.get("winnerName")))
    if winner is not None:
        return str(winner) in {str(_player_id(player, player_index)), _player_name(player, player_index)}

    return _victory_points(player) >= 10.0


def _is_player_building(vertex: Mapping[str, Any], player_index: int) -> bool:
    return vertex.get("owner") == player_index and _building_type(vertex) in {"settlement", "city"}


def _building_type(vertex: Mapping[str, Any]) -> Optional[str]:
    building = vertex.get("building")
    if isinstance(building, str):
        return building
    if building:
        return str(building)
    return None


def _distance_rule_open(vertices: Mapping[str, Any], vertex_key: str) -> bool:
    for neighbor_key in _adjacent_vertices(vertex_key):
        neighbor = vertices.get(neighbor_key)
        if isinstance(neighbor, Mapping) and _building_type(neighbor):
            return False
    return True


def _touches_player_road(state: Mapping[str, Any], vertex_key: str, player_index: int) -> bool:
    edges = _mapping(state.get("edges"))
    for edge_key in _edges_touching_vertex(dict(state), vertex_key):
        edge = edges.get(edge_key)
        if isinstance(edge, Mapping) and edge.get("owner") == player_index and edge.get("road"):
            return True
    return False


def _hex_key(hex_info: Mapping[str, Any]) -> Optional[str]:
    if "key" in hex_info:
        return str(hex_info["key"])
    q = hex_info.get("q")
    r = hex_info.get("r")
    if q is None or r is None:
        return None
    return f"{q},{r}"


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
