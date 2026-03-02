# Agent/Tools/catan_action_menu.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Action:
    """
    A single suggested action for the agent to take.
    `type` must match the server socket event name.
    """
    type: str
    payload: Dict[str, Any]
    score: float = 0.0


def _get(d: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def is_my_turn(state: Dict[str, Any]) -> bool:
    """
    Supports multiple server shapes.
    - If state has isMyTurn/myTurn, use it.
    - Else, if it has myIndex/currentPlayerIndex, compare them (your server).
    - Else, fall back to id-based fields.
    """
    if not isinstance(state, dict):
        return False

    is_my = _get(state, "isMyTurn", "myTurn", default=None)
    if isinstance(is_my, bool):
        return is_my

    myi = state.get("myIndex")
    cpi = state.get("currentPlayerIndex")
    if myi is not None and cpi is not None:
        try:
            return int(myi) == int(cpi)
        except Exception:
            pass

    current_pid = _get(state, "currentPlayerId", "currentTurnPlayerId", default=None)
    view_pid = _get(state, "playerId", default=None)
    if current_pid is not None and view_pid is not None:
        return str(current_pid) == str(view_pid)

    return False


# ---------------------------------------------------------------------
# Key enumerators (match your server's key formats)
#   hexKey(q,r) -> f"{q},{r}"
#   vertexKey(q,r,d) -> f"v_{q}_{r}_{d}"
#   edgeKey(q,r,d) -> f"e_{q}_{r}_{d}"
# ---------------------------------------------------------------------

def _all_hex_keys_from_state(state: Dict[str, Any]) -> List[str]:
    hexes = state.get("hexes", {}) or {}
    if not isinstance(hexes, dict):
        return []
    # In your server, hexes dict is keyed by "q,r" already.
    return [str(k) for k in hexes.keys()]


def _all_vertex_keys_from_state(state: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    hexes = state.get("hexes", {}) or {}
    if not isinstance(hexes, dict):
        return keys

    for _, h in hexes.items():
        if not isinstance(h, dict):
            continue
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            keys.append(f"v_{q}_{r}_{d}")
    return keys


def _all_edge_keys_from_state(state: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    hexes = state.get("hexes", {}) or {}
    if not isinstance(hexes, dict):
        return keys

    for _, h in hexes.items():
        if not isinstance(h, dict):
            continue
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            keys.append(f"e_{q}_{r}_{d}")
    return keys


# ---------------------------------------------------------------------
# Action menu
# ---------------------------------------------------------------------

def build_action_menu(state: Dict[str, Any], last_setup_settlement: Optional[str] = None) -> List[Action]:
    """
    Build a robust action list for your server.

    Setup:
      - phase == 'setup' and it's your turn:
          - if last_setup_settlement is None -> try placeSettlement(vKey,isSetup=True)
          - else -> try placeRoad(eKey,isSetup=True,lastSettlement=...) + advanceSetup

    Playing turn phases (your server):
      - turnPhase == 'roll' -> rollDice
      - turnPhase == 'discard' -> discardCards (placeholder: discard 0 if forced? we avoid if possible)
      - turnPhase == 'robber' -> moveRobber(hexKey, stealFromPlayerId=None)
      - turnPhase == 'main' -> endTurn
      - turnPhase == 'specialBuild' -> endSpecialBuild (minimal; otherwise you'd add build actions here)
    """
    if not isinstance(state, dict) or not is_my_turn(state):
        return []

    phase = state.get("phase")
    turn_phase = _get(state, "turnPhase", default=None)

    # -----------------------
    # SETUP
    # -----------------------
    if phase == "setup":
        actions: List[Action] = []

        # Place settlement first
        if not last_setup_settlement:
            for vkey in _all_vertex_keys_from_state(state):
                actions.append(Action(
                    "placeSettlement",
                    {"vertexKey": vkey, "isSetup": True},
                    score=1.0
                ))
            # safety fallback (shouldn't happen)
            if not actions:
                actions.append(Action("advanceSetup", {}, score=0.0))
            return actions

        # Then place road connected to that settlement, then advance setup
        for ekey in _all_edge_keys_from_state(state):
            actions.append(Action(
                "placeRoad",
                {"edgeKey": ekey, "isSetup": True, "lastSettlement": last_setup_settlement},
                score=1.0
            ))
        actions.append(Action("advanceSetup", {}, score=0.1))
        return actions

    # -----------------------
    # PLAYING
    # -----------------------
    # Discard phase happens if someone has >7 cards after a 7 roll.
    # Your server requires exact discard counts; without computing amounts,
    # we cannot safely discard. So we return [] and let your agent wait
    # unless you want to implement discard logic.
    if turn_phase == "discard":
        # If YOU want a naive policy later: compute required discard count from state["discardingPlayers"].
        return []

    # Robber phase: must move robber to a different hex (and optionally steal).
    if turn_phase == "robber":
        current_robber = state.get("robber")  # should be "q,r"
        actions: List[Action] = []
        for hk in _all_hex_keys_from_state(state):
            if hk == current_robber:
                continue
            actions.append(Action(
                "moveRobber",
                {"hexKey": hk, "stealFromPlayerId": None},
                score=1.0
            ))
        # If we couldn't enumerate, at least don't endTurn incorrectly
        return actions

    # Special building phase (5-6 player rule). Minimal: just endSpecialBuild.
    if turn_phase == "specialBuild":
        return [Action("endSpecialBuild", {}, score=1.0)]

    # Normal phases
    if turn_phase == "roll":
        return [Action("rollDice", {}, score=1.0)]
    if turn_phase == "main":
        return [Action("endTurn", {}, score=1.0)]

    return []