# Agent/Tools/GeneralTools.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Action:
    type: str
    payload: Dict[str, Any]
    score: float = 0.0


RESOURCES = ["brick", "lumber", "wool", "grain", "ore"]


def _get(d: Dict[str, Any], *keys: str, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def is_my_turn(state: Dict[str, Any]) -> bool:
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


# ------------------------------------------------------------
# Board parsing helpers (your server keys)
# ------------------------------------------------------------

def _hexes_dict(state: Dict[str, Any]) -> Dict[str, Any]:
    hx = state.get("hexes") or {}
    return hx if isinstance(hx, dict) else {}


def _parse_hex_key(hk: str) -> Optional[Tuple[int, int]]:
    # "q,r"
    try:
        q_s, r_s = hk.split(",")
        return int(q_s), int(r_s)
    except Exception:
        return None


def _pip_value(token: Any) -> int:
    # standard Catan pip counts
    pips = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}
    try:
        t = int(token)
        return pips.get(t, 0)
    except Exception:
        return 0


def _vertex_key(q: int, r: int, d: int) -> str:
    return f"v_{q}_{r}_{d}"


def _edge_key(q: int, r: int, d: int) -> str:
    return f"e_{q}_{r}_{d}"


def _all_vertex_keys_from_state(state: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    for _, h in _hexes_dict(state).items():
        if not isinstance(h, dict):
            continue
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            keys.append(_vertex_key(int(q), int(r), d))
    return keys


def _all_edge_keys_from_state(state: Dict[str, Any]) -> List[str]:
    keys: List[str] = []
    for _, h in _hexes_dict(state).items():
        if not isinstance(h, dict):
            continue
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            keys.append(_edge_key(int(q), int(r), d))
    return keys


def _all_hex_keys_from_state(state: Dict[str, Any]) -> List[str]:
    return [str(k) for k in _hexes_dict(state).keys()]


# ------------------------------------------------------------
# Simple geometry helpers (local, matches server conventions)
# ------------------------------------------------------------

def _parse_vertex_key(vk: str) -> Optional[Tuple[int, int, int]]:
    try:
        _, qs, rs, ds = vk.split("_")
        return int(qs), int(rs), int(ds)
    except Exception:
        return None


def _parse_edge_key(ek: str) -> Optional[Tuple[int, int, int]]:
    try:
        _, qs, rs, ds = ek.split("_")
        return int(qs), int(rs), int(ds)
    except Exception:
        return None


def _adjacent_vertices(vk: str) -> List[str]:
    """
    Local port of gameLogic.getAdjacentVertices.
    Used only for heuristics (server still enforces rules).
    """
    parsed = _parse_vertex_key(vk)
    if not parsed:
        return []
    q, r, d = parsed

    out: List[str] = []
    next_dir = (d + 1) % 6
    prev_dir = (d + 5) % 6
    out.append(_vertex_key(q, r, next_dir))
    out.append(_vertex_key(q, r, prev_dir))

    if d == 0:
        out.append(_vertex_key(q, r - 1, 1))
    elif d == 1:
        out.append(_vertex_key(q + 1, r - 1, 2))
    elif d == 2:
        out.append(_vertex_key(q + 1, r, 3))
    elif d == 3:
        out.append(_vertex_key(q, r + 1, 4))
    elif d == 4:
        out.append(_vertex_key(q - 1, r + 1, 5))
    elif d == 5:
        out.append(_vertex_key(q - 1, r, 0))

    return out


def _edge_vertices(q: int, r: int, d: int) -> List[str]:
    # Port of gameLogic.getEdgeVertices
    if d == 0:
        return [_vertex_key(q, r, 0), _vertex_key(q, r, 1)]
    if d == 1:
        return [_vertex_key(q, r, 1), _vertex_key(q, r, 2)]
    if d == 2:
        return [_vertex_key(q, r, 2), _vertex_key(q, r, 3)]
    if d == 3:
        return [_vertex_key(q, r, 3), _vertex_key(q, r, 4)]
    if d == 4:
        return [_vertex_key(q, r, 4), _vertex_key(q, r, 5)]
    if d == 5:
        return [_vertex_key(q, r, 5), _vertex_key(q, r, 0)]
    return []


def _edges_touching_vertex(state: Dict[str, Any], vk: str) -> List[str]:
    """
    Best-effort: scan all edges and keep those whose endpoints include vk.
    Board is small so this is cheap.
    """
    edges = state.get("edges") or {}
    out: List[str] = []
    for ek, e in edges.items():
        if not isinstance(e, dict):
            continue
        parsed = _parse_edge_key(ek)
        if not parsed:
            continue
        q, r, d = parsed
        if vk in _edge_vertices(q, r, d):
            out.append(ek)
    return out


# ------------------------------------------------------------
# Settlement / city scoring for main phase
# ------------------------------------------------------------

def _score_vertex_for_city(state: Dict[str, Any], vertex_key: str) -> float:
    """Reuse setup-style heuristic: good production + diversity are great city targets."""
    hx = _hexes_dict(state)
    parsed = _parse_vertex_key(vertex_key)
    if not parsed:
        return 0.0
    q, r, d = parsed

    seen_res = set()
    score = 0.0
    # Small stencil around the vertex; server will still do the real legality checks.
    neighbors: List[Tuple[int, int]] = [
        (q, r),
        (q, r - 1),
        (q + 1, r - 1),
        (q + 1, r),
        (q, r + 1),
        (q - 1, r + 1),
        (q - 1, r),
    ]
    for aq, ar in neighbors:
        h = hx.get(f"{aq},{ar}")
        if not isinstance(h, dict):
            continue
        res = h.get("resource")
        tok = h.get("token")
        if res in (None, "desert"):
            continue
        seen_res.add(res)
        score += float(_pip_value(tok))
    score += 0.5 * len(seen_res)
    return score



# ------------------------------------------------------------
# Settlement scoring (fix "stupid" setup)
# ------------------------------------------------------------

def _adjacent_hexes_for_vertex(state: Dict[str, Any], vertex_key: str) -> List[Dict[str, Any]]:
    """
    Approximate the 3 hexes that share this vertex, mirroring server logic.
    Port of gameLogic.getAdjacentHexesToVertex (q,r,dir).
    """
    parsed = _parse_vertex_key(vertex_key)
    if not parsed:
        return []
    q, r, d = parsed

    coords: List[Tuple[int, int]] = [(q, r)]
    if d == 0:
        coords += [(q, r - 1), (q + 1, r - 1)]
    elif d == 1:
        coords += [(q + 1, r - 1), (q + 1, r)]
    elif d == 2:
        coords += [(q + 1, r), (q, r + 1)]
    elif d == 3:
        coords += [(q, r + 1), (q - 1, r + 1)]
    elif d == 4:
        coords += [(q - 1, r + 1), (q - 1, r)]
    elif d == 5:
        coords += [(q - 1, r), (q, r - 1)]

    hx = _hexes_dict(state)
    out: List[Dict[str, Any]] = []
    for hq, hr in coords:
        h = hx.get(f"{hq},{hr}")
        if isinstance(h, dict):
            out.append(h)
    return out


def _score_vertex_for_setup(state: Dict[str, Any], vertex_key: str) -> float:
    """
    Heuristic:
    - prefer high pips (6/8 heavy)
    - prefer resource diversity (avoid double-desert/empty)
    - penalize desert / robber hex
    """
    hexes = _adjacent_hexes_for_vertex(state, vertex_key)
    if not hexes:
        return -1e9

    robber = state.get("robber")  # "q,r"
    score = 0.0
    seen_resources = set()

    for h in hexes:
        if not isinstance(h, dict):
            continue
        resource = h.get("resource")
        token = h.get("token")

        # Desert / none
        if resource in (None, "desert"):
            score -= 3.0
            continue

        seen_resources.add(resource)

        # pips
        score += float(_pip_value(token))

        # robber penalty
        q, r = h.get("q"), h.get("r")
        if q is not None and r is not None and robber == f"{q},{r}":
            score -= 4.0

        # tiny bump for “key” resources early
        if resource in ("brick", "lumber"):
            score += 0.5
        if resource in ("grain", "ore"):
            score += 0.25

    # diversity bonus
    score += 0.75 * len(seen_resources)

    return score


def _ranked_setup_settlements(state: Dict[str, Any], top_k: int = 80) -> List[str]:
    """
    Rank all vertex keys by setup score; return top_k to try.
    Still relies on server legality checks, but tries good spots first.
    """
    candidates = _all_vertex_keys_from_state(state)
    scored = [(vk, _score_vertex_for_setup(state, vk)) for vk in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [vk for vk, _ in scored[:top_k]]


def _ranked_setup_roads(state: Dict[str, Any], last_settlement: str, top_k: int = 120) -> List[str]:
    """
    We still don't have exact edge adjacency, but we can do better than random:
    - prefer roads whose encoded (q,r) matches the settlement's (q,r) hex
    - otherwise, just keep a subset.
    """
    parts = last_settlement.split("_")
    home_qr = None
    if len(parts) == 4:
        try:
            home_qr = (int(parts[1]), int(parts[2]))
        except Exception:
            home_qr = None

    edges = _all_edge_keys_from_state(state)
    if not home_qr:
        return edges[:top_k]

    def edge_score(ek: str) -> float:
        ps = ek.split("_")
        if len(ps) != 4:
            return 0.0
        try:
            q, r = int(ps[1]), int(ps[2])
        except Exception:
            return 0.0
        return 1.0 if (q, r) == home_qr else 0.0

    ranked = sorted(edges, key=edge_score, reverse=True)
    return ranked[:top_k]


# ------------------------------------------------------------
# Discard logic
# ------------------------------------------------------------

def _build_discard_action(state: Dict[str, Any]) -> List[Action]:
    discarding = state.get("discardingPlayers")
    myi = state.get("myIndex")

    if not isinstance(discarding, list) or not isinstance(myi, int):
        return []

    entry = next((d for d in discarding if isinstance(d, dict) and d.get("playerIndex") == myi), None)
    if not entry:
        return []

    k = entry.get("cardsToDiscard")
    if not isinstance(k, int) or k <= 0:
        return []

    players = state.get("players")
    if not isinstance(players, list) or myi < 0 or myi >= len(players):
        return []

    me = players[myi] if isinstance(players[myi], dict) else {}
    res = me.get("resources")
    if not isinstance(res, dict):
        return []

    piles = [(r, int(res.get(r, 0) or 0)) for r in RESOURCES]
    piles.sort(key=lambda x: x[1], reverse=True)

    to_discard = {r: 0 for r in RESOURCES}
    remaining = k
    for r, amt in piles:
        if remaining <= 0:
            break
        take = min(amt, remaining)
        if take > 0:
            to_discard[r] = take
            remaining -= take

    if remaining != 0:
        return []
    return [Action("discardCards", {"resources": to_discard}, score=1.0)]


# ------------------------------------------------------------
# Trading helpers (menu construction)
# ------------------------------------------------------------

def _me(state: Dict[str, Any]) -> Dict[str, Any]:
    players = state.get("players")
    myi = state.get("myIndex")
    if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players) and isinstance(players[myi], dict):
        return players[myi]
    return {}


def _extract_trade_offer(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Best-effort: adjust to your real getPlayerView field name.
    """
    for k in ("activeTradeOffer", "tradeOffer", "currentTradeOffer"):
        v = state.get(k)
        if isinstance(v, dict):
            return v
    return None


def _bank_trade_actions(state: Dict[str, Any], max_actions: int = 60) -> List[Action]:
    """
    Generate bankTrade actions using tradeRatios + hand.
    Preference:
      - if we have an excess resource (>=ratio), trade into a missing resource (0 count).
      - otherwise generate all legal trades (capped).
    """
    me = _me(state)
    hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}
    # tradeRatios is exposed at the top level of getPlayerView
    ratios = state.get("tradeRatios") if isinstance(state.get("tradeRatios"), dict) else {}

    # identify "missing" resources
    missing = [r for r in RESOURCES if int(hand.get(r, 0) or 0) == 0]

    actions: List[Action] = []

    for give_r in RESOURCES:
        have = int(hand.get(give_r, 0) or 0)
        ratio = int(ratios.get(give_r, 4) or 4)
        if ratio <= 0:
            ratio = 4
        if have < ratio:
            continue

        # preferential targets first
        targets = missing[:] if missing else [r for r in RESOURCES if r != give_r]
        for get_r in targets:
            if get_r == give_r:
                continue
            # server expects giveResource/giveAmount/getResource
            actions.append(
                Action(
                    "bankTrade",
                    {"giveResource": give_r, "giveAmount": ratio, "getResource": get_r},
                    score=1.0 if get_r in missing else 0.4,
                )
            )
            if len(actions) >= max_actions:
                return actions

    return actions


def _city_upgrade_actions(state: Dict[str, Any]) -> List[Action]:
    """
    Generate upgradeToCity actions for our own settlements when we can afford them.
    """
    players = state.get("players")
    myi = state.get("myIndex")
    vertices = state.get("vertices") or {}
    if not isinstance(players, list) or not isinstance(myi, int):
        return []

    me = players[myi] if 0 <= myi < len(players) and isinstance(players[myi], dict) else {}
    res = me.get("resources") if isinstance(me.get("resources"), dict) else {}

    # City cost: 3 ore + 2 grain
    if int(res.get("ore", 0) or 0) < 3 or int(res.get("grain", 0) or 0) < 2:
        return []

    actions: List[Action] = []
    for vk, v in (vertices or {}).items():
        if not isinstance(v, dict):
            continue
        if v.get("building") != "settlement":
            continue
        if v.get("owner") != myi:
            continue
        base_score = _score_vertex_for_city(state, vk)
        actions.append(
            Action(
                "upgradeToCity",
                {"vertexKey": vk},
                score=1.4 + 0.05 * base_score,
            )
        )
    return actions


def _buy_dev_card_actions(state: Dict[str, Any]) -> List[Action]:
    """
    Suggest buying a development card when we have the resources.
    Simple cost check; server enforces deck availability, etc.
    """
    me = _me(state)
    if not me or not isinstance(me.get("resources"), dict):
        return []
    r = me["resources"]
    if (
        int(r.get("ore", 0) or 0) >= 1
        and int(r.get("grain", 0) or 0) >= 1
        and int(r.get("wool", 0) or 0) >= 1
    ):
        # Single generic buy action; model can decide when to take it.
        return [Action("buyDevCard", {}, score=0.8)]
    return []


def _trade_offer_response_actions(state: Dict[str, Any]) -> List[Action]:
    """
    If there's an active offer and we can respond, provide accept/decline and maybe counter.
    Without knowing your offer schema, keep it conservative:
      - always include decline
      - accept only if we can pay request and it gives us something we lack
    """
    offer = _extract_trade_offer(state)
    if not isinstance(offer, dict):
        return []

    can_resp = offer.get("can_i_respond")
    if not isinstance(can_resp, bool):
        # some views might use different field
        can_resp = bool(offer.get("canRespond") or offer.get("canRespondToTrade"))

    if not can_resp:
        # If we are the proposer, we might be able to cancel
        # (server has cancelTrade event without payload)
        return [Action("cancelTrade", {}, score=0.2)]

    me = _me(state)
    hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}

    request = offer.get("request") if isinstance(offer.get("request"), dict) else {}
    their_offer = offer.get("offer") if isinstance(offer.get("offer"), dict) else {}

    def can_pay(req: Dict[str, Any]) -> bool:
        for r, amt in req.items():
            try:
                a = int(amt or 0)
            except Exception:
                return False
            if a < 0:
                return False
            if int(hand.get(r, 0) or 0) < a:
                return False
        return True

    def gives_missing(off: Dict[str, Any]) -> bool:
        for r, amt in off.items():
            try:
                a = int(amt or 0)
            except Exception:
                continue
            if a > 0 and int(hand.get(r, 0) or 0) == 0:
                return True
        return False

    actions: List[Action] = []
    actions.append(Action("respondToTrade", {"accept": False}, score=0.9))

    if can_pay(request) and gives_missing(their_offer):
        actions.append(Action("respondToTrade", {"accept": True}, score=1.2))
    else:
        # still allow accept at lower score in case model has a reason
        actions.append(Action("respondToTrade", {"accept": True}, score=0.2))

    # Very conservative counter: swap 1-for-1 of a resource we have 2+ of, for a missing resource
    # (Only meaningful if server allows counterTrade while responding)
    missing = [r for r in RESOURCES if int(hand.get(r, 0) or 0) == 0]
    if missing:
        excess = [r for r in RESOURCES if int(hand.get(r, 0) or 0) >= 2]
        if excess:
            actions.append(
                Action(
                    "counterTrade",
                    {"offer": {excess[0]: 1}, "request": {missing[0]: 1}},
                    score=0.4,
                )
            )

    return actions


# ------------------------------------------------------------
# Main menu builder
# ------------------------------------------------------------

def build_action_menu(state: Dict[str, Any], last_setup_settlement: Optional[str] = None) -> List[Action]:
    if not isinstance(state, dict) or not is_my_turn(state):
        return []

    phase = state.get("phase")
    turn_phase = _get(state, "turnPhase", default=None)

    # -----------------------
    # SETUP
    # -----------------------
    if phase == "setup":
        if not last_setup_settlement:
            ranked = _ranked_setup_settlements(state, top_k=120)
            return [
                Action("placeSettlement", {"vertexKey": vk, "isSetup": True}, score=1.0)
                for vk in ranked
            ] or [Action("advanceSetup", {}, score=0.0)]

        ranked_edges = _ranked_setup_roads(state, last_setup_settlement, top_k=200)
        actions = [
            Action(
                "placeRoad",
                {"edgeKey": ek, "isSetup": True, "lastSettlement": last_setup_settlement},
                score=1.0,
            )
            for ek in ranked_edges
        ]
        actions.append(Action("advanceSetup", {}, score=0.1))
        return actions

    # -----------------------
    # FORCED PHASES
    # -----------------------
    if turn_phase == "discard":
        return _build_discard_action(state)

    if turn_phase == "robber":
        current_robber = state.get("robber")
        actions: List[Action] = []
        for hk in _all_hex_keys_from_state(state):
            if hk == current_robber:
                continue
            actions.append(Action("moveRobber", {"hexKey": hk, "stealFromPlayerId": None}, score=1.0))
        return actions

    if turn_phase == "specialBuild":
        return [Action("endSpecialBuild", {}, score=1.0)]

    if turn_phase == "roll":
        return [Action("rollDice", {}, score=1.0)]

    # -----------------------
    # MAIN PHASE
    # -----------------------
    if turn_phase == "main":
        actions: List[Action] = []

        # If a trade offer exists, provide response options
        actions.extend(_trade_offer_response_actions(state))

        # Add bank trade options (capped)
        actions.extend(_bank_trade_actions(state, max_actions=50))

        # Strategic build / development-card options
        actions.extend(_city_upgrade_actions(state))
        actions.extend(_buy_dev_card_actions(state))

        # Always allow endTurn
        actions.append(Action("endTurn", {}, score=0.5))

        return actions

    return []