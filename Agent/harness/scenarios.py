"""
Game-state fixtures for the Trading harness.

``*_rich`` states are larger, more Catan-like snapshots so a real LLM has enough
context (resources, ratios, opponents, board) to reason about trades.
"""
from __future__ import annotations

from typing import Any, Dict, List

RES = ["brick", "lumber", "wool", "grain", "ore"]


def _trade_ratios_default() -> Dict[str, int]:
    return {"brick": 4, "lumber": 4, "wool": 4, "grain": 4, "ore": 3}


def players_four_player_rich() -> List[Dict[str, Any]]:
    """Four players: us + three opponents with varied VP and hands."""
    return [
        {
            "id": "me-1",
            "name": "You",
            "victoryPoints": 5,
            "resources": {"brick": 6, "lumber": 4, "wool": 4, "grain": 3, "ore": 1},
            "tradeRatios": _trade_ratios_default(),
        },
        {
            "id": "p-alice",
            "name": "Alice",
            "victoryPoints": 7,
            "resources": {"brick": 1, "lumber": 0, "wool": 2, "grain": 3, "ore": 1},
        },
        {
            "id": "p-bob",
            "name": "Bob",
            "victoryPoints": 4,
            "resources": {"brick": 0, "lumber": 2, "wool": 1, "grain": 0, "ore": 2},
        },
        {
            "id": "p-cara",
            "name": "Cara",
            "victoryPoints": 3,
            "resources": {"brick": 2, "lumber": 1, "wool": 0, "grain": 1, "ore": 0},
        },
    ]


def state_main_my_turn_rich() -> Dict[str, Any]:
    """
    Main phase, our turn — enough resources for 4:1 bank trades and a plausible board.
    """
    return {
        "phase": "playing",
        "turnPhase": "main",
        "myIndex": 0,
        "currentPlayerIndex": 0,
        "isMyTurn": True,
        "players": players_four_player_rich(),
        "tradeRatios": _trade_ratios_default(),
        "devCardDeck": 14,
        "hexes": {
            "0,-1": {"resource": "lumber", "number": 8},
            "1,-1": {"resource": "ore", "number": 6},
            "1,0": {"resource": "grain", "number": 5},
            "0,0": {"resource": "brick", "number": 9},
            "2,-1": {"resource": "wool", "number": 10},
            "-1,0": {"resource": "desert", "number": None},
        },
        "vertices": {
            "v_0_-1_2": {"owner": 0, "building": "settlement"},
            "v_1_-1_3": {"owner": 0, "building": "settlement"},
            "v_1_0_0": {"owner": 1, "building": "settlement"},
        },
        "edges": {
            "e_0_-1_1": {"owner": 0},
        },
        "robber": "-1,0",
        "longestRoadPlayer": "p-alice",
        "longestRoadLength": 8,
        "largestArmyPlayer": None,
        "largestArmySize": 0,
        "diceRoll": {"total": 9, "die1": 4, "die2": 5},
    }


def state_incoming_trade_off_turn_rich() -> Dict[str, Any]:
    """
    Not our turn — Alice (close to winning) proposes brick for our grain.
    """
    return {
        "phase": "playing",
        "turnPhase": "main",
        "myIndex": 0,
        "currentPlayerIndex": 2,
        "isMyTurn": False,
        "players": players_four_player_rich(),
        "tradeRatios": _trade_ratios_default(),
        "devCardDeck": 14,
        "hexes": {
            "0,-1": {"resource": "lumber", "number": 8},
            "1,-1": {"resource": "ore", "number": 6},
        },
        "vertices": {
            "v_0_-1_2": {"owner": 0, "building": "settlement"},
            "v_1_0_0": {"owner": 1, "building": "settlement"},
        },
        "edges": {},
        "robber": "0,-1",
        "activeTradeOffer": {
            "from": 1,
            "to": 0,
            "offer": {"brick": 1},
            "request": {"grain": 1},
            "canRespond": True,
            "can_i_respond": True,
        },
    }


def players_four_bank_focus() -> List[Dict[str, Any]]:
    """
    Same four-player table as ``players_four_player_rich``, but our hand is brick-heavy
    and ore-poor so 4:1 (or better) bank trades are the natural first move.
    """
    pl = list(players_four_player_rich())
    pl[0] = {
        **pl[0],
        "resources": {"brick": 8, "lumber": 1, "wool": 1, "grain": 2, "ore": 0},
    }
    return pl


def state_main_my_turn_bank_focus() -> Dict[str, Any]:
    """
    Main phase, our turn — surplus brick, need ore; good for bank-first reasoning.

    Pair with a Strategy plan whose ``short_term_goals`` include the tag
    ``[HARNESS:proactive_bank]`` so ``HarnessOpenAI`` (``--mock``) can script
    ``get_trade_options`` → ``bank_trade``. A real LLM does not need the tag.
    """
    st = dict(state_main_my_turn_rich())
    st["players"] = players_four_bank_focus()
    return st


def state_incoming_trade_counter_rich() -> Dict[str, Any]:
    """
    Off-turn — Bob asks 2 grain for 1 lumber (greedy).

    Proposer name includes ``[HARNESS:reactive_counter]`` so ``HarnessOpenAI``
    can script ``counter_trade`` under ``--mock``. Remove or rename for production-style
    prompts if you prefer cleaner scoreboard text.
    """
    st = dict(state_incoming_trade_off_turn_rich())
    pl = list(players_four_player_rich())
    pl[1] = {**pl[1], "name": "Bob [HARNESS:reactive_counter]"}
    st["players"] = pl
    st["activeTradeOffer"] = {
        "from": 1,
        "to": 0,
        "offer": {"lumber": 1},
        "request": {"grain": 2},
        "canRespond": True,
        "can_i_respond": True,
    }
    return st


# ── Legacy minimal states (still valid) ────────────────────────────

def _players_min() -> List[Dict[str, Any]]:
    return [
        {
            "id": "p0",
            "name": "Me",
            "victoryPoints": 4,
            "resources": {"brick": 1, "lumber": 1, "wool": 2, "grain": 1, "ore": 1},
        },
        {
            "id": "p1",
            "name": "Alice",
            "victoryPoints": 6,
            "resources": {"brick": 0, "lumber": 0, "wool": 0, "grain": 2, "ore": 2},
        },
    ]


def state_main_my_turn() -> Dict[str, Any]:
    return {
        "phase": "playing",
        "turnPhase": "main",
        "myIndex": 0,
        "currentPlayerIndex": 0,
        "isMyTurn": True,
        "players": _players_min(),
        "hexes": {
            "0,-1": {"resource": "lumber", "number": 8},
            "1,-1": {"resource": "ore", "number": 6},
        },
        "vertices": {},
        "edges": {},
        "robber": "2,-1",
    }


def state_incoming_trade_off_turn() -> Dict[str, Any]:
    return {
        "phase": "playing",
        "turnPhase": "main",
        "myIndex": 0,
        "currentPlayerIndex": 1,
        "isMyTurn": False,
        "players": _players_min(),
        "hexes": {"0,-1": {"resource": "wool", "number": 5}},
        "vertices": {},
        "edges": {},
        "robber": "0,-1",
        "activeTradeOffer": {
            "from": 1,
            "to": 0,
            "offer": {"brick": 1},
            "request": {"grain": 1},
            "canRespond": True,
        },
    }


def state_discard_my_turn() -> Dict[str, Any]:
    me = dict(_players_min()[0])
    me["resources"] = {"brick": 3, "lumber": 3, "wool": 2, "grain": 2, "ore": 2}
    pl = [me, _players_min()[1]]
    return {
        "phase": "playing",
        "turnPhase": "discard",
        "myIndex": 0,
        "currentPlayerIndex": 0,
        "isMyTurn": True,
        "players": pl,
        "hexes": {},
        "vertices": {},
        "edges": {},
        "robber": "0,-1",
        "discardingPlayers": [{"playerIndex": 0, "cardsToDiscard": 6}],
    }


def state_robber_my_turn() -> Dict[str, Any]:
    return {
        "phase": "playing",
        "turnPhase": "robber",
        "myIndex": 0,
        "currentPlayerIndex": 0,
        "isMyTurn": True,
        "players": _players_min(),
        "hexes": {
            "0,-1": {"resource": "grain", "number": 9},
            "1,-1": {"resource": "desert", "number": None},
            "2,-1": {"resource": "lumber", "number": 10},
        },
        "vertices": {"v_0_-1_0": {"owner": 1, "building": "settlement"}},
        "edges": {},
        "robber": "0,-1",
    }


def state_main_dev_build_queue() -> Dict[str, Any]:
    """
    Main phase — Strategy left one settlement in the build queue; Development runs tools
    deterministically (no LLM). Uses a free vertex key the mock server accepts.
    """
    return {
        "phase": "playing",
        "turnPhase": "main",
        "myIndex": 0,
        "currentPlayerIndex": 0,
        "isMyTurn": True,
        "players": [
            {
                "id": "p0",
                "name": "Me",
                "victoryPoints": 4,
                "resources": {"brick": 1, "lumber": 1, "wool": 1, "grain": 1, "ore": 2},
            },
            {
                "id": "p1",
                "name": "Alice",
                "victoryPoints": 5,
                "resources": {"brick": 0, "lumber": 0, "wool": 0, "grain": 0, "ore": 0},
            },
        ],
        "hexes": {
            "0,-1": {"resource": "lumber", "number": 8},
            "1,-1": {"resource": "ore", "number": 6},
        },
        "vertices": {
            "v_0_-1_2": {"owner": None, "building": None},
        },
        "edges": {},
        "robber": "2,-1",
    }
