from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# -------- action object --------

@dataclass(frozen=True)
class Action:
    type: str
    payload: Dict[str, Any]
    score: float = 0.0


# -------- key helpers --------

def vkey(q: int, r: int, d: int) -> str:
    return f"v_{q}_{r}_{d}"

def ekey(q: int, r: int, d: int) -> str:
    return f"e_{q}_{r}_{d}"

def parse_vkey(key: str) -> Optional[Tuple[int, int, int]]:
    try:
        _, qs, rs, ds = key.split("_")
        return int(qs), int(rs), int(ds)
    except Exception:
        return None

def parse_ekey(key: str) -> Optional[Tuple[int, int, int]]:
    try:
        _, qs, rs, ds = key.split("_")
        return int(qs), int(rs), int(ds)
    except Exception:
        return None

def hex_key(q: int, r: int) -> str:
    return f"{q},{r}"


# -------- board geometry (matches your server conventions) --------

def get_adjacent_hexes_to_vertex(q: int, r: int, d: int) -> List[Tuple[int, int]]:
    out = [(q, r)]
    if d == 0:
        out += [(q, r - 1), (q + 1, r - 1)]
    elif d == 1:
        out += [(q + 1, r - 1), (q + 1, r)]
    elif d == 2:
        out += [(q + 1, r), (q, r + 1)]
    elif d == 3:
        out += [(q, r + 1), (q - 1, r + 1)]
    elif d == 4:
        out += [(q - 1, r + 1), (q - 1, r)]
    elif d == 5:
        out += [(q - 1, r), (q, r - 1)]
    return out

def get_edge_vertices(q: int, r: int, d: int) -> List[str]:
    if d == 0: return [vkey(q, r, 0), vkey(q, r, 1)]
    if d == 1: return [vkey(q, r, 1), vkey(q, r, 2)]
    if d == 2: return [vkey(q, r, 2), vkey(q, r, 3)]
    if d == 3: return [vkey(q, r, 3), vkey(q, r, 4)]
    if d == 4: return [vkey(q, r, 4), vkey(q, r, 5)]
    if d == 5: return [vkey(q, r, 5), vkey(q, r, 0)]
    return []


# -------- state helpers --------

def is_my_turn(state: Dict[str, Any]) -> bool:
    return isinstance(state.get("myIndex"), int) and state.get("myIndex") == state.get("currentPlayerIndex")

def my_player(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    players = state.get("players")
    myi = state.get("myIndex")
    if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players):
        return players[myi]
    return None

def all_vertices(state: Dict[str, Any]) -> List[str]:
    hexes = state.get("hexes") or {}
    out: List[str] = []
    for hk, h in hexes.items():
        if not isinstance(h, dict):
            continue
        q = h.get("q")
        r = h.get("r")
        if q is None or r is None:
            # fallback: parse from key "q,r"
            try:
                qs, rs = hk.split(",")
                q, r = int(qs), int(rs)
            except Exception:
                continue
        for d in range(6):
            out.append(vkey(int(q), int(r), d))
    return list(dict.fromkeys(out))

def all_edges(state: Dict[str, Any]) -> List[str]:
    hexes = state.get("hexes") or {}
    out: List[str] = []
    for hk, h in hexes.items():
        if not isinstance(h, dict):
            continue
        q = h.get("q")
        r = h.get("r")
        if q is None or r is None:
            try:
                qs, rs = hk.split(",")
                q, r = int(qs), int(rs)
            except Exception:
                continue
        for d in range(6):
            out.append(ekey(int(q), int(r), d))
    return list(dict.fromkeys(out))


# -------- heuristics --------

PIPS = {2: 1, 12: 1, 3: 2, 11: 2, 4: 3, 10: 3, 5: 4, 9: 4, 6: 5, 8: 5}

def score_setup_vertex(state: Dict[str, Any], v: str) -> float:
    pv = parse_vkey(v)
    if not pv:
        return -1e9
    q, r, d = pv
    hexes = state.get("hexes") or {}
    resources: List[str] = []
    score = 0.0
    for (hq, hr) in get_adjacent_hexes_to_vertex(q, r, d):
        h = hexes.get(hex_key(hq, hr))
        if not isinstance(h, dict):
            continue
        res = h.get("resource")
        num = h.get("number")
        if isinstance(res, str) and res:
            resources.append(res)
            if isinstance(num, int) and num in PIPS:
                score += PIPS[num]
    score += 0.75 * len(set(resources))  # diversity
    # small port bonus if state includes ports
    ports = state.get("ports")
    if isinstance(ports, list):
        for p in ports:
            if isinstance(p, dict) and isinstance(p.get("vertices"), list) and v in p["vertices"]:
                score += 0.5
                break
    return score


# -------- discard builder --------

def compute_discard_payload(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    me = my_player(state)
    if not me or not isinstance(me.get("resources"), dict):
        return None
    r: Dict[str, Any] = me["resources"]
    total = sum(int(v) for v in r.values())
    if total <= 7:
        return None
    need = total // 2

    items = sorted([(k, int(v)) for k, v in r.items()], key=lambda kv: kv[1], reverse=True)
    disc = {k: 0 for k, _ in items}
    rem = need
    for k, amt in items:
        if rem <= 0:
            break
        take = min(amt, rem)
        disc[k] = take
        rem -= take

    return {
        "resources": {
            "brick": int(disc.get("brick", 0)),
            "lumber": int(disc.get("lumber", 0)),
            "wool": int(disc.get("wool", 0)),
            "grain": int(disc.get("grain", 0)),
            "ore": int(disc.get("ore", 0)),
        }
    }


# -------- main menu builder --------

def build_action_menu(
    state: Dict[str, Any],
    last_setup_settlement: Optional[str],
) -> List[Action]:
    """
    Returns candidate actions for THIS agent only.
    """
    if not state or not is_my_turn(state):
        return []

    phase = state.get("phase")
    turn_phase = state.get("turnPhase")

    actions: List[Action] = []

    # --- SETUP ---
    if phase == "setup":
        if last_setup_settlement:
            # placeRoad candidates touching that settlement
            edges = all_edges(state)
            road_actions: List[Action] = []
            for e in edges:
                pe = parse_ekey(e)
                if not pe:
                    continue
                q, r, d = pe
                ev = get_edge_vertices(q, r, d)
                touches = (last_setup_settlement in ev)
                road_actions.append(Action(
                    "placeRoad",
                    {"edgeKey": e, "isSetup": True, "lastSettlement": last_setup_settlement},
                    score=1.0 if touches else 0.0
                ))
            road_actions.sort(key=lambda a: a.score, reverse=True)
            actions.extend(road_actions[:30])
            # after road, advance
            actions.append(Action("advanceSetup", {}, score=-0.1))
            return actions

        # else pick settlement vertex candidates
        verts = all_vertices(state)
        settlement_actions = [
            Action("placeSettlement", {"vertexKey": v, "isSetup": True}, score=score_setup_vertex(state, v))
            for v in verts
        ]
        settlement_actions.sort(key=lambda a: a.score, reverse=True)
        return settlement_actions[:30]

    # --- PLAYING ---
    if phase == "playing":
        if turn_phase == "roll":
            return [Action("rollDice", {}, score=1.0)]

        if turn_phase == "discard":
            payload = compute_discard_payload(state)
            if payload:
                return [Action("discardCards", payload, score=1.0)]
            # if you're in discard phase but you don't need to discard, do nothing
            return []

        if turn_phase == "robber":
            # choose a few robber moves; victim chosen later by agent via getPlayersOnHex
            hexes = list((state.get("hexes") or {}).keys())
            cur = state.get("robber")
            candidates = [h for h in hexes if h != cur]
            random.shuffle(candidates)
            for hk in candidates[:10]:
                actions.append(Action("moveRobber", {"hexKey": hk, "stealFromPlayerId": None}, score=0.0))
            return actions

        if turn_phase == "main":
            # minimal: dev card buy if possible, else end turn
            me = my_player(state)
            if me and isinstance(me.get("resources"), dict):
                r = me["resources"]
                if int(r.get("ore", 0)) >= 1 and int(r.get("grain", 0)) >= 1 and int(r.get("wool", 0)) >= 1:
                    actions.append(Action("buyDevCard", {}, score=0.4))

            actions.append(Action("endTurn", {}, score=0.0))
            return actions

    return []