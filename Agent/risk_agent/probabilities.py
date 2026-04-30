"""
Risk Agent — deterministic probability and analysis functions.

Pure math, no LLM, no network calls.  Every function consumes the raw
``getPlayerView`` game-state dict (same shape as
``CatanSocketClient.latest_state()``).

Standard Catan probability: each number token has ``pips / 36`` chance
of being rolled per turn.  A city doubles production.  The robber blocks
production on the hex it occupies.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from Agent.utils.game_state_processor import (
    _parse_vertex_key,
    _adjacent_hex_coords,
    PIPS,
    RESOURCES,
)


# ── helpers ───────────────────────────────────────────────────────

def _pip_probability(number_token: Any) -> float:
    """Return the probability (0–5/36) of a number token being rolled."""
    try:
        return PIPS.get(int(number_token), 0) / 36.0
    except (TypeError, ValueError):
        return 0.0


def _me_dict(state: Dict[str, Any]) -> Dict[str, Any]:
    players = state.get("players")
    myi = state.get("myIndex")
    if (
        isinstance(players, list)
        and isinstance(myi, int)
        and 0 <= myi < len(players)
        and isinstance(players[myi], dict)
    ):
        return players[myi]
    return {}


def _my_resources(state: Dict[str, Any]) -> Dict[str, int]:
    me = _me_dict(state)
    res = me.get("resources")
    if not isinstance(res, dict):
        return {r: 0 for r in RESOURCES}
    return {r: int(res.get(r, 0) or 0) for r in RESOURCES}


def _hexes(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    hx = state.get("hexes") or {}
    return hx if isinstance(hx, dict) else {}


def _vertices(state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    vx = state.get("vertices") or {}
    return vx if isinstance(vx, dict) else {}


def _vertex_adjacent_hexes(
    vk: str, hexes: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return hex dicts adjacent to vertex ``vk``."""
    parsed = _parse_vertex_key(vk)
    if not parsed:
        return []
    q, r, d = parsed
    out: List[Dict[str, Any]] = []
    for hq, hr in _adjacent_hex_coords(q, r, d):
        h = hexes.get(f"{hq},{hr}")
        if isinstance(h, dict):
            out.append(h)
    return out


def _vertex_hex_keys(vk: str) -> List[str]:
    """Return hex key strings (``\"q,r\"``) adjacent to vertex ``vk``."""
    parsed = _parse_vertex_key(vk)
    if not parsed:
        return []
    q, r, d = parsed
    return [f"{hq},{hr}" for hq, hr in _adjacent_hex_coords(q, r, d)]


def _hex_key_from_hex(h: Dict[str, Any]) -> str:
    return f"{h.get('q', '?')},{h.get('r', '?')}"


# ── 1. expected_resource_income ───────────────────────────────────

def expected_resource_income(state: Dict[str, Any]) -> Dict[str, float]:
    """
    Expected resources per turn from our current buildings.

    For each of our buildings (settlement/city), for each adjacent hex:
      income += (pips / 36) * multiplier
    where multiplier = 2 for city, 1 for settlement.
    The robber blocks production on its hex (income = 0).
    """
    myi = state.get("myIndex")
    robber = state.get("robber")
    hexes = _hexes(state)
    vertices = _vertices(state)

    income: Dict[str, float] = {r: 0.0 for r in RESOURCES}

    for vk, v in vertices.items():
        if not isinstance(v, dict):
            continue
        if v.get("owner") != myi:
            continue
        building = v.get("building")
        if not building:
            continue

        multiplier = 2.0 if building == "city" else 1.0

        for h in _vertex_adjacent_hexes(vk, hexes):
            res = h.get("resource")
            num = h.get("number")
            hk = _hex_key_from_hex(h)

            if not res or res == "desert" or num is None:
                continue
            # Robber blocks this hex
            if hk == robber:
                continue
            if res in income:
                income[res] += _pip_probability(num) * multiplier

    # Round for readability
    return {r: round(v, 4) for r, v in income.items()}


# ── 2. per_building_income ────────────────────────────────────────

def per_building_income(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Breakdown of expected income per building.

    Returns list of dicts:
      [{vertex, building_type, resource, number_token, pips,
        expected_per_turn, robber_blocked}]
    """
    myi = state.get("myIndex")
    robber = state.get("robber")
    hexes = _hexes(state)
    vertices = _vertices(state)
    rows: List[Dict[str, Any]] = []

    for vk, v in vertices.items():
        if not isinstance(v, dict):
            continue
        if v.get("owner") != myi:
            continue
        building = v.get("building")
        if not building:
            continue

        multiplier = 2.0 if building == "city" else 1.0

        for h in _vertex_adjacent_hexes(vk, hexes):
            res = h.get("resource")
            num = h.get("number")
            hk = _hex_key_from_hex(h)
            if not res or res == "desert" or num is None:
                continue

            blocked = (hk == robber)
            pips = PIPS.get(int(num), 0) if num is not None else 0
            ev = 0.0 if blocked else _pip_probability(num) * multiplier

            rows.append({
                "vertex": vk,
                "building_type": building,
                "resource": res,
                "number_token": num,
                "pips": pips,
                "expected_per_turn": round(ev, 4),
                "robber_blocked": blocked,
            })

    return rows


# ── 3. opponent_threat_assessment ─────────────────────────────────

def _estimate_turns_to_win(
    vp: int, total_income_rate: float,
) -> int:
    """
    Rough estimate: how many turns until this player reaches 10 VP.

    Assumes ~2 total resources per turn translate to roughly 1 VP every
    5-7 turns (settlement cost ~= 4 resources, city ~= 5, etc.).
    """
    remaining_vp = max(0, 10 - vp)
    if remaining_vp == 0:
        return 0
    # Very rough: each VP costs about 4 resources on average
    resources_per_vp = 4.0
    effective_income = max(total_income_rate, 0.1)
    turns = (remaining_vp * resources_per_vp) / effective_income
    return max(1, int(round(turns)))


def _opponent_income_rate(
    state: Dict[str, Any], player_index: int,
) -> float:
    """Estimate total expected resources/turn for an opponent."""
    robber = state.get("robber")
    hexes = _hexes(state)
    vertices = _vertices(state)
    total = 0.0

    for vk, v in vertices.items():
        if not isinstance(v, dict):
            continue
        if v.get("owner") != player_index:
            continue
        building = v.get("building")
        if not building:
            continue
        multiplier = 2.0 if building == "city" else 1.0

        for h in _vertex_adjacent_hexes(vk, hexes):
            res = h.get("resource")
            num = h.get("number")
            hk = _hex_key_from_hex(h)
            if not res or res == "desert" or num is None:
                continue
            if hk == robber:
                continue
            total += _pip_probability(num) * multiplier

    return total


def opponent_threat_assessment(
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    For each opponent, estimate threat level.

    Factors: VP, income rate, road length vs longest road, knights vs
    largest army, dev cards held, estimated turns to win.

    Returns list sorted by threat_level (descending).
    """
    players = state.get("players")
    myi = state.get("myIndex")
    if not isinstance(players, list) or not isinstance(myi, int):
        return []

    lr_holder = state.get("longestRoadPlayer")
    lr_length = int(state.get("longestRoadLength", 0) or 0)
    la_holder = state.get("largestArmyPlayer")
    la_size = int(state.get("largestArmySize", 0) or 0)

    threats: List[Dict[str, Any]] = []

    for idx, p in enumerate(players):
        if not isinstance(p, dict):
            continue
        if idx == myi:
            continue

        vp = int(p.get("victoryPoints", 0) or 0)
        knights = int(p.get("knightsPlayed", 0) or 0)
        road_len = int(p.get("roadLength", 0) or 0)

        # Dev card count (opponents show count, not contents)
        dev_raw = p.get("developmentCards")
        dev_count = int(dev_raw) if isinstance(dev_raw, (int, float)) else 0

        # Total resource cards
        res_raw = p.get("resources")
        total_cards = int(res_raw) if isinstance(res_raw, (int, float)) else 0

        income_rate = _opponent_income_rate(state, idx)
        turns_to_win = _estimate_turns_to_win(vp, income_rate)

        # Composite threat score
        threat_score = 0.0
        threat_score += vp * 10.0                  # VP is the primary driver
        threat_score += income_rate * 8.0           # higher income = faster growth
        threat_score += dev_count * 3.0             # hidden VP potential
        threat_score += total_cards * 0.5           # more cards = more options

        # Road proximity to longest road bonus
        road_threshold = max(lr_length, 5)
        if road_len >= road_threshold - 1:
            threat_score += 8.0
        elif road_len >= road_threshold - 2:
            threat_score += 4.0

        # Army proximity to largest army bonus
        army_threshold = max(la_size, 3)
        if knights >= army_threshold - 1:
            threat_score += 8.0
        elif knights >= army_threshold - 2:
            threat_score += 4.0

        # Already holds an achievement
        if idx == lr_holder:
            threat_score += 5.0
        if idx == la_holder:
            threat_score += 5.0

        # Classify
        if vp >= 8 or threat_score >= 75:
            level = "critical"
        elif vp >= 6 or threat_score >= 50:
            level = "high"
        elif vp >= 4 or threat_score >= 30:
            level = "medium"
        else:
            level = "low"

        threats.append({
            "index": idx,
            "name": p.get("name", f"Player{idx}"),
            "vp": vp,
            "income_rate": round(income_rate, 3),
            "turns_to_win_estimate": turns_to_win,
            "road_length": road_len,
            "knights_played": knights,
            "dev_cards": dev_count,
            "total_cards": total_cards,
            "threat_score": round(threat_score, 1),
            "threat_level": level,
        })

    threats.sort(key=lambda t: t["threat_score"], reverse=True)
    return threats


# ── 4. robber_impact_analysis ─────────────────────────────────────

def robber_impact_analysis(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    For each non-desert hex (excluding where the robber already is):
    calculate how much *opponent* production we'd block by placing the
    robber there — minus how much of *our own* production we'd lose.

    Returns hexes sorted by net impact (best targets first).
    Each entry: {hex_key, resource, number_token, opponent_damage,
                 our_damage, net_impact, affected_opponents}
    """
    myi = state.get("myIndex")
    cur_robber = state.get("robber")
    hexes = _hexes(state)
    vertices = _vertices(state)

    results: List[Dict[str, Any]] = []

    for hk, h in hexes.items():
        if not isinstance(h, dict):
            continue
        # Skip current robber position and desert
        if hk == cur_robber:
            continue
        res = h.get("resource")
        num = h.get("number")
        if not res or res == "desert" or num is None:
            continue

        prob = _pip_probability(num)
        opponent_damage = 0.0
        our_damage = 0.0
        affected_opponents: List[str] = []

        # Find all buildings touching this hex
        for vk, v in vertices.items():
            if not isinstance(v, dict):
                continue
            owner = v.get("owner")
            building = v.get("building")
            if owner is None or not building:
                continue

            # Check if this vertex touches this hex
            hex_keys = _vertex_hex_keys(vk)
            if hk not in hex_keys:
                continue

            multiplier = 2.0 if building == "city" else 1.0
            production_loss = prob * multiplier

            if owner == myi:
                our_damage += production_loss
            else:
                opponent_damage += production_loss
                # Track affected opponent name
                players = state.get("players")
                if isinstance(players, list) and isinstance(owner, int):
                    if 0 <= owner < len(players):
                        pname = players[owner].get("name", f"Player{owner}")
                        if pname not in affected_opponents:
                            affected_opponents.append(pname)

        net_impact = opponent_damage - our_damage

        results.append({
            "hex_key": hk,
            "resource": res,
            "number_token": num,
            "pips": PIPS.get(int(num), 0),
            "opponent_damage": round(opponent_damage, 4),
            "our_damage": round(our_damage, 4),
            "net_impact": round(net_impact, 4),
            "affected_opponents": affected_opponents,
        })

    results.sort(key=lambda x: x["net_impact"], reverse=True)
    return results


# ── 5. rank_vertices_by_expected_value ────────────────────────────

def _resource_weights(state: Dict[str, Any]) -> Dict[str, float]:
    """
    Dynamic resource weights based on scarcity in our hand.
    Resources we have 0 of are most valuable.
    """
    hand = _my_resources(state)
    weights: Dict[str, float] = {}
    for r in RESOURCES:
        count = hand.get(r, 0)
        if count == 0:
            weights[r] = 2.0
        elif count == 1:
            weights[r] = 1.5
        elif count <= 3:
            weights[r] = 1.0
        else:
            weights[r] = 0.6
    return weights


def _vertex_is_legal_for_settlement(
    state: Dict[str, Any], vk: str,
) -> bool:
    """
    Check distance rule + unoccupied.  Not a perfect legality check
    (server validates), but filters obvious bad spots.
    """
    vertices = _vertices(state)
    v = vertices.get(vk)
    if not isinstance(v, dict):
        return False
    # Already occupied
    if v.get("owner") is not None or v.get("building"):
        return False

    # Distance rule: no adjacent vertex may have a building
    parsed = _parse_vertex_key(vk)
    if not parsed:
        return False
    q, r, d = parsed

    # Adjacent vertices (same logic as game_tools._adjacent_vertices)
    adj_keys: List[str] = []
    next_dir = (d + 1) % 6
    prev_dir = (d + 5) % 6
    adj_keys.append(f"v_{q}_{r}_{next_dir}")
    adj_keys.append(f"v_{q}_{r}_{prev_dir}")

    cross_map = {
        0: f"v_{q}_{r-1}_{1}",
        1: f"v_{q+1}_{r-1}_{2}",
        2: f"v_{q+1}_{r}_{3}",
        3: f"v_{q}_{r+1}_{4}",
        4: f"v_{q-1}_{r+1}_{5}",
        5: f"v_{q-1}_{r}_{0}",
    }
    cross = cross_map.get(d)
    if cross:
        adj_keys.append(cross)

    for ak in adj_keys:
        adj = vertices.get(ak)
        if isinstance(adj, dict) and adj.get("building"):
            return False

    return True


def rank_vertices_by_expected_value(
    state: Dict[str, Any], top_k: int = 10,
) -> List[Dict[str, Any]]:
    """
    Rank all legal settlement vertices by expected value.

    EV = sum over adjacent hexes of:
        (pips / 36) * resource_weight
    where resource_weight is dynamic based on what we currently lack.
    """
    robber = state.get("robber")
    hexes = _hexes(state)
    weights = _resource_weights(state)

    # Collect all unique vertex keys from hexes
    seen_vk: set = set()
    for hk, h in hexes.items():
        if not isinstance(h, dict):
            continue
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            seen_vk.add(f"v_{int(q)}_{int(r)}_{d}")

    results: List[Dict[str, Any]] = []

    for vk in seen_vk:
        if not _vertex_is_legal_for_settlement(state, vk):
            continue

        ev = 0.0
        resources_produced: List[str] = []

        for h in _vertex_adjacent_hexes(vk, hexes):
            res = h.get("resource")
            num = h.get("number")
            hk = _hex_key_from_hex(h)
            if not res or res == "desert" or num is None:
                continue

            prob = _pip_probability(num)
            # Penalize robber-blocked hexes
            if hk == robber:
                prob *= 0.1  # not zero — robber moves

            w = weights.get(res, 1.0)
            ev += prob * w
            resources_produced.append(f"{res}:{num}")

        # Diversity bonus: more distinct resources is better
        unique_resources = len(set(
            rp.split(":")[0] for rp in resources_produced
        ))
        ev += unique_resources * 0.02

        if ev > 0:
            results.append({
                "vertex": vk,
                "expected_value": round(ev, 4),
                "resources": resources_produced,
                "diversity": unique_resources,
            })

    results.sort(key=lambda x: x["expected_value"], reverse=True)
    return results[:top_k]


# ── 6. rank_cities_by_value_gain ──────────────────────────────────

def rank_cities_by_value_gain(
    state: Dict[str, Any], top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Rank our existing settlements by how much upgrading to city
    increases expected income.

    A city doubles production, so the gain equals the settlement's
    current expected income.
    """
    myi = state.get("myIndex")
    robber = state.get("robber")
    hexes = _hexes(state)
    vertices = _vertices(state)

    results: List[Dict[str, Any]] = []

    for vk, v in vertices.items():
        if not isinstance(v, dict):
            continue
        if v.get("owner") != myi:
            continue
        if v.get("building") != "settlement":
            continue

        current_ev = 0.0
        production: List[str] = []

        for h in _vertex_adjacent_hexes(vk, hexes):
            res = h.get("resource")
            num = h.get("number")
            hk = _hex_key_from_hex(h)
            if not res or res == "desert" or num is None:
                continue
            prob = _pip_probability(num)
            if hk == robber:
                prob = 0.0
            current_ev += prob
            production.append(f"{res}:{num}")

        # Gain from upgrading = current_ev (doubles from 1x to 2x)
        results.append({
            "vertex": vk,
            "current_ev": round(current_ev, 4),
            "upgrade_gain": round(current_ev, 4),
            "production": production,
        })

    results.sort(key=lambda x: x["upgrade_gain"], reverse=True)
    return results[:top_k]


# ── 7. win_probability_estimate ───────────────────────────────────

def win_probability_estimate(state: Dict[str, Any]) -> Dict[str, float]:
    """
    Heuristic win probability for each player.

    Weights:
      - VP progress (50%): vp / 10
      - Income rate  (20%): normalized across players
      - Dev cards    (15%): hidden VP / largest army potential
      - Achievements (15%): road length + army progress

    Normalized so probabilities sum to 1.0.
    """
    players = state.get("players")
    myi = state.get("myIndex")
    if not isinstance(players, list):
        return {}

    lr_length = int(state.get("longestRoadLength", 0) or 0)
    la_size = int(state.get("largestArmySize", 0) or 0)

    raw_scores: Dict[str, float] = {}

    for idx, p in enumerate(players):
        if not isinstance(p, dict):
            continue

        name = p.get("name", f"Player{idx}")
        vp = int(p.get("victoryPoints", 0) or 0)
        knights = int(p.get("knightsPlayed", 0) or 0)
        road_len = int(p.get("roadLength", 0) or 0)

        dev_raw = p.get("developmentCards")
        if isinstance(dev_raw, (int, float)):
            dev_count = int(dev_raw)
        elif isinstance(dev_raw, list):
            dev_count = len(dev_raw)
        else:
            dev_count = 0

        income = (
            _opponent_income_rate(state, idx)
            if idx != myi
            else sum(expected_resource_income(state).values())
        )

        # VP component (0 to 1)
        vp_score = vp / 10.0

        # Income component (will be normalized later)
        income_score = income

        # Dev card component
        dev_score = min(dev_count * 0.15, 1.0)

        # Achievement proximity
        road_proximity = min(road_len / max(lr_length + 1, 5), 1.0)
        army_proximity = min(knights / max(la_size + 1, 3), 1.0)
        ach_score = (road_proximity + army_proximity) / 2.0

        raw = (
            vp_score * 0.50
            + income_score * 0.20
            + dev_score * 0.15
            + ach_score * 0.15
        )
        raw_scores[name] = max(raw, 0.01)

    # Normalize
    total = sum(raw_scores.values())
    if total <= 0:
        return {name: round(1.0 / len(raw_scores), 3) for name in raw_scores}

    return {
        name: round(score / total, 3)
        for name, score in raw_scores.items()
    }
