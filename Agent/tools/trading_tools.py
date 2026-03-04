# Agent/Tools/catan_trading_tools.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from SocketClient import CatanSocketClient


RESOURCES = ["brick", "lumber", "wool", "grain", "ore"]


def _safe_call(
    client: CatanSocketClient,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Mirror of your safe_call wrapper, but kept local so tools can be imported anywhere.
    Treat payload None OR {} as "no payload".
    """
    try:
        if payload is None or payload == {}:
            resp = client.sio.call(event, timeout=timeout)
        else:
            resp = client.sio.call(event, payload, timeout=timeout)

        if isinstance(resp, dict):
            return resp
        return {"success": True, "raw": resp}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _player_view_me(state: Dict[str, Any]) -> Dict[str, Any]:
    players = state.get("players")
    myi = state.get("myIndex")
    if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players) and isinstance(players[myi], dict):
        return players[myi]
    return {}


def _active_trade_offer_from_state(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Best-effort extraction, since I don't know your exact getPlayerView shape.
    If your GameLogic.getPlayerView already includes something like trade offer state,
    adapt this function to match it.
    """
    # common patterns:
    for k in ("activeTradeOffer", "tradeOffer", "currentTradeOffer"):
        v = state.get(k)
        if isinstance(v, dict):
            return v
    return None


def get_trading_context(client: CatanSocketClient) -> Dict[str, Any]:
    """
    Tool: trade-focused snapshot using the agent's latest_state() (player-view).
    Matches the docstring you gave as closely as possible without extra server endpoints.
    """
    state = client.latest_state() or {}
    me = _player_view_me(state)

    hand = me.get("resources") if isinstance(me.get("resources"), dict) else {r: 0 for r in RESOURCES}
    trade_ratios = me.get("tradeRatios") if isinstance(me.get("tradeRatios"), dict) else {}

    # Ports: if your view includes ports explicitly, pull them; otherwise keep empty.
    ports = me.get("ports")
    if not isinstance(ports, list):
        ports = []

    other_players: List[Dict[str, Any]] = []
    players = state.get("players")
    myi = state.get("myIndex")
    if isinstance(players, list):
        for idx, p in enumerate(players):
            if not isinstance(p, dict):
                continue
            if isinstance(myi, int) and idx == myi:
                continue
            total_cards = 0
            if isinstance(p.get("resources"), dict):
                total_cards += sum(int(x or 0) for x in p["resources"].values())
            # dev cards are likely hidden; only count if view exposes them
            if isinstance(p.get("developmentCards"), list):
                total_cards += len(p["developmentCards"])

            other_players.append(
                {
                    "player_id": str(p.get("id", "")),
                    "name": str(p.get("name", "")),
                    "total_cards": int(total_cards),
                    "visible_vp": int(p.get("victoryPoints") or 0),
                }
            )

    active = _active_trade_offer_from_state(state)

    # If the view doesn't carry offer state, we still return a consistent shape.
    active_trade_offer = None
    if isinstance(active, dict):
        active_trade_offer = {
            "from_id": active.get("from") or active.get("from_id") or active.get("fromId"),
            "to_id": active.get("to") or active.get("to_id") or active.get("toId"),
            "offer": active.get("offer") if isinstance(active.get("offer"), dict) else {},
            "request": active.get("request") if isinstance(active.get("request"), dict) else {},
            "can_i_respond": bool(active.get("can_i_respond") or active.get("canRespond") or active.get("canRespondToTrade")),
        }

    return {
        "hand": hand,
        "trade_ratios": trade_ratios,
        "ports": ports,
        "other_players": other_players,
        "active_trade_offer": active_trade_offer,
    }


def get_bank_trade_options(client: CatanSocketClient) -> Dict[str, Any]:
    """
    Enumerate all legal bank/port trades based on current hand and tradeRatios in player-view.
    This does NOT require a server endpoint.
    """
    state = client.latest_state() or {}
    me = _player_view_me(state)

    hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}
    ratios = me.get("tradeRatios") if isinstance(me.get("tradeRatios"), dict) else {}

    options: List[Dict[str, Any]] = []
    for give_r in RESOURCES:
        have = int(hand.get(give_r, 0) or 0)
        ratio = int(ratios.get(give_r, 4) or 4)  # default 4:1 if unknown
        if ratio <= 0:
            ratio = 4
        if have < ratio:
            continue
        for get_r in RESOURCES:
            if get_r == give_r:
                continue
            options.append({"give_resource": give_r, "give_amount": ratio, "get_resource": get_r})

    return {"options": options}


def execute_bank_trade(
    client: CatanSocketClient,
    give_resource: str,
    give_amount: int,
    get_resource: str,
) -> Dict[str, Any]:
    resp = _safe_call(
        client,
        "bankTrade",
        {"giveResource": give_resource, "giveAmount": int(give_amount), "getResource": get_resource},
        timeout=10,
    )
    # Best effort new hand from latest state after action
    if resp.get("success"):
        state = client.latest_state() or {}
        me = _player_view_me(state)
        new_hand = me.get("resources") if isinstance(me.get("resources"), dict) else None
        return {"success": True, "error": None, "new_hand": new_hand}
    return {"success": False, "error": resp.get("error", "bankTrade failed"), "new_hand": None}


def propose_player_trade(
    client: CatanSocketClient,
    offer: Dict[str, int],
    request: Dict[str, int],
    target_player_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"offer": offer, "request": request}
    if target_player_id is not None:
        payload["targetPlayerId"] = target_player_id

    resp = _safe_call(client, "proposeTrade", payload, timeout=10)
    if resp.get("success"):
        return {"success": True, "error": None}
    return {"success": False, "error": resp.get("error", "proposeTrade failed")}


def respond_to_trade_offer(
    client: CatanSocketClient,
    decision: str,
) -> Dict[str, Any]:
    decision = decision.strip().lower()
    if decision not in ("accept", "decline"):
        return {"success": False, "error": "decision must be 'accept' or 'decline'"}

    # Your server expects {accept: bool}
    resp = _safe_call(client, "respondToTrade", {"accept": decision == "accept"}, timeout=10)
    if resp.get("success"):
        return {"success": True, "error": None}
    return {"success": False, "error": resp.get("error", "respondToTrade failed")}


def counter_trade_offer(
    client: CatanSocketClient,
    offer: Dict[str, int],
    request: Dict[str, int],
) -> Dict[str, Any]:
    resp = _safe_call(client, "counterTrade", {"offer": offer, "request": request}, timeout=10)
    if resp.get("success"):
        return {"success": True, "error": None}
    return {"success": False, "error": resp.get("error", "counterTrade failed")}


def cancel_my_trade_offer(client: CatanSocketClient) -> Dict[str, Any]:
    resp = _safe_call(client, "cancelTrade", None, timeout=10)
    if resp.get("success"):
        return {"success": True, "error": None}
    return {"success": False, "error": resp.get("error", "cancelTrade failed")}


def query_strategy_alignment_for_trade(player_id: str, trade: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stub coordination tool — you’ll later wire this to your strategy sub-agent.
    For now return neutral.
    """
    return {
        "alignment_score": 0.0,
        "reasoning": "No strategy agent wired; default neutral.",
        "recommendation": "neutral",
    }