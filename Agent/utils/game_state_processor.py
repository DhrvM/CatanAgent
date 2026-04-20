"""
Structured extraction of the raw getPlayerView game state into an
LLM-friendly format with rich board awareness.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RESOURCES = ["brick", "lumber", "wool", "grain", "ore"]

PIPS: Dict[int, int] = {
    2: 1, 12: 1, 3: 2, 11: 2, 4: 3, 10: 3, 5: 4, 9: 4, 6: 5, 8: 5,
}


# ── tiny geometry helpers (match server conventions) ──────────────

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


def _adjacent_hex_coords(q: int, r: int, d: int) -> List[Tuple[int, int]]:
    """Return the ≤3 hex (q,r) coords that share vertex (q, r, d)."""
    coords = [(q, r)]
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
    return coords


def _edge_vertices(q: int, r: int, d: int) -> List[str]:
    # Matches server orientation conventions.
    if d == 0:
        return [f"v_{q}_{r}_0", f"v_{q}_{r}_1"]
    if d == 1:
        return [f"v_{q}_{r}_1", f"v_{q}_{r}_2"]
    if d == 2:
        return [f"v_{q}_{r}_2", f"v_{q}_{r}_3"]
    if d == 3:
        return [f"v_{q}_{r}_3", f"v_{q}_{r}_4"]
    if d == 4:
        return [f"v_{q}_{r}_4", f"v_{q}_{r}_5"]
    if d == 5:
        return [f"v_{q}_{r}_5", f"v_{q}_{r}_0"]
    return []


def _to_int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _normalize_hex_key(value: Any) -> str:
    if isinstance(value, str):
        parts = value.split(",")
        if len(parts) == 2:
            try:
                return f"{int(parts[0])},{int(parts[1])}"
            except Exception:
                pass
        return value
    if isinstance(value, (tuple, list)) and len(value) == 2:
        try:
            return f"{int(value[0])},{int(value[1])}"
        except Exception:
            return str(value)
    if isinstance(value, dict):
        q = value.get("q")
        r = value.get("r")
        if q is not None and r is not None:
            try:
                return f"{int(q)},{int(r)}"
            except Exception:
                return f"{q},{r}"
    return str(value)


def _normalize_vertex_key(value: Any) -> str:
    if isinstance(value, str):
        parsed = _parse_vertex_key(value)
        if parsed:
            q, r, d = parsed
            return f"v_{q}_{r}_{d}"
        return value
    return str(value)


def _building_multiplier(building_type: Any) -> int:
    bt = str(building_type or "").lower()
    return 2 if bt == "city" else 1


# ── main processor ────────────────────────────────────────────────

class GameStateProcessor:
    """
    Converts the raw ``getPlayerView`` dict from the Catan server into a
    compact, structured representation that GPT-4o can reason about.
    """

    # ----------------------------------------------------------------
    # public API
    # ----------------------------------------------------------------
    def process(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Full structured extraction."""
        if not isinstance(state, dict):
            return {"error": "invalid state"}

        me = self._me(state)
        myi = state.get("myIndex")
        opponent_board = self._extract_opponent_board(state, myi)

        return {
            "phase": state.get("phase"),
            "setup_phase": state.get("setupPhase"),
            "turn_phase": state.get("turnPhase"),
            "is_my_turn": self._is_my_turn(state),
            "current_player_index": _to_int_or_none(state.get("currentPlayerIndex")),
            "dice_roll": self._extract_dice(state),
            "me": self._extract_me(state, me, myi),
            "opponents": self._extract_opponents(state, myi, opponent_board),
            "opponent_board": opponent_board,
            "board_graph": self._extract_board_graph(state),
            "robber_hex": _normalize_hex_key(state.get("robber")),
            "robber_block_impact": self._estimate_robber_block_impact(state, myi),
            "longest_road": self._extract_achievement(
                state, "longestRoadPlayer", "longestRoadLength", "longest_road"
            ),
            "largest_army": self._extract_achievement(
                state, "largestArmyPlayer", "largestArmySize", "largest_army"
            ),
            "trade_offer": self._extract_trade_offer(state),
            "dev_cards_remaining": state.get("devCardDeck", 0),
            "discarding_players": state.get("discardingPlayers") or [],
            "free_roads": state.get("freeRoads", 0),
            "year_of_plenty_picks": state.get("yearOfPlentyPicks", 0),
        }

    def format_for_llm(self, processed: Dict[str, Any]) -> str:
        """Produce a concise natural-language summary of the board state."""
        lines: List[str] = []
        p = processed

        # Phase / turn
        lines.append(
            f"Phase: {p.get('phase')} | Turn phase: {p.get('turn_phase')} | "
            f"My turn: {p.get('is_my_turn')}"
        )

        dice = p.get("dice_roll")
        if dice:
            lines.append(f"Dice roll: {dice}")

        # Me
        me = p.get("me") or {}
        res = me.get("resources", {})
        res_str = ", ".join(f"{r}: {res.get(r, 0)}" for r in RESOURCES)
        lines.append(f"My resources: {res_str}")
        lines.append(f"My VP: {me.get('victory_points', '?')}")

        dev = me.get("dev_cards")
        if dev:
            lines.append(f"My dev cards: {dev}")

        ratios = me.get("trade_ratios", {})
        ratio_str = ", ".join(f"{r}: {ratios.get(r, 4)}:1" for r in RESOURCES)
        lines.append(f"Trade ratios: {ratio_str}")

        buildings = me.get("my_buildings", [])
        if buildings:
            bld_strs = []
            for b in buildings:
                prod = ", ".join(b.get("production", []))
                bld_strs.append(f"{b['type']} @ {b['vertex']} ({prod})")
            lines.append(f"My buildings: {'; '.join(bld_strs)}")

        lines.append(
            f"Pieces left — settlements: {me.get('settlements_remaining', '?')}, "
            f"cities: {me.get('cities_remaining', '?')}, "
            f"roads: {me.get('roads_remaining', '?')}"
        )

        # Opponents
        for opp in p.get("opponents", []):
            prod = opp.get("likely_production", {})
            prod_str = ", ".join(f"{r}:{prod.get(r, 0)}" for r in RESOURCES)
            lines.append(
                f"Opponent '{opp['name']}': VP={opp.get('vp', '?')}, "
                f"cards={opp.get('total_cards', '?')}, "
                f"knights={opp.get('knights_played', 0)}, "
                f"road_len={opp.get('road_length', 0)}, "
                f"buildings={opp.get('building_count', 0)}, "
                f"likely_prod[{prod_str}]"
            )

        # Board conditions
        lines.append(f"Robber hex: {p.get('robber_hex')}")

        lr = p.get("longest_road") or {}
        lines.append(
            f"Longest road: {lr.get('holder', 'none')} (length {lr.get('length', 0)})"
        )
        la = p.get("largest_army") or {}
        lines.append(
            f"Largest army: {la.get('holder', 'none')} (size {la.get('size', 0)})"
        )

        trade = p.get("trade_offer")
        if trade:
            lines.append(f"Active trade offer: {trade}")

        block_impacts = p.get("robber_block_impact", [])
        if isinstance(block_impacts, list) and block_impacts:
            top = block_impacts[:3]
            top_s = "; ".join(
                f"{b.get('hex')} (block {b.get('total_blocked_pips', 0)})"
                for b in top
            )
            lines.append(f"Top robber block hexes vs opponents: {top_s}")

        graph = p.get("board_graph")
        if isinstance(graph, dict):
            vertices = graph.get("vertices", [])
            edges = graph.get("edges", [])
            occupied_vertices = sum(
                1
                for v in vertices
                if isinstance(v, dict)
                and isinstance(v.get("occupancy"), dict)
                and v["occupancy"].get("piece_type") in ("settlement", "city")
            )
            occupied_edges = sum(
                1
                for e in edges
                if isinstance(e, dict)
                and isinstance(e.get("occupancy"), dict)
                and e["occupancy"].get("piece_type") == "road"
            )
            lines.append(
                f"Board graph: {len(vertices)} vertices ({occupied_vertices} occupied), "
                f"{len(edges)} edges ({occupied_edges} with roads)"
            )

        lines.append(f"Dev cards remaining in deck: {p.get('dev_cards_remaining', '?')}")

        return "\n".join(lines)

    # ----------------------------------------------------------------
    # internal helpers
    # ----------------------------------------------------------------
    @staticmethod
    def _is_my_turn(state: Dict[str, Any]) -> bool:
        myi = state.get("myIndex")
        cpi = state.get("currentPlayerIndex")
        if myi is not None and cpi is not None:
            try:
                return int(myi) == int(cpi)
            except Exception:
                pass
        return bool(state.get("isMyTurn", False))

    @staticmethod
    def _me(state: Dict[str, Any]) -> Dict[str, Any]:
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

    @staticmethod
    def _extract_dice(state: Dict[str, Any]) -> Optional[int]:
        dr = state.get("diceRoll")
        if isinstance(dr, dict):
            return dr.get("total")
        if isinstance(dr, int):
            return dr
        return None

    def _extract_me(
        self, state: Dict[str, Any], me: Dict[str, Any], myi: Optional[int]
    ) -> Dict[str, Any]:
        res = me.get("resources", {})
        if not isinstance(res, dict):
            res = {}

        dev_cards = me.get("developmentCards", [])
        if isinstance(dev_cards, list):
            dev_list = self._normalize_dev_cards(dev_cards)
        else:
            dev_list = []

        new_dev = me.get("newDevCards", [])
        if isinstance(new_dev, list):
            new_dev_list = self._normalize_dev_cards(new_dev)
        else:
            new_dev_list = []

        return {
            "index": _to_int_or_none(myi),
            "player_id": str(me.get("id")) if me.get("id") is not None else None,
            "name": str(me.get("name")) if me.get("name") is not None else "",
            "resources": {r: int(res.get(r, 0) or 0) for r in RESOURCES},
            "dev_cards": dev_list,
            "new_dev_cards": new_dev_list,
            "dev_card_played_this_turn": state.get("devCardPlayedThisTurn", False),
            "has_rolled_this_turn": state.get("hasRolledThisTurn", False),
            "victory_points": int(me.get("victoryPoints", 0) or 0),
            "hidden_vp": int(me.get("hiddenVictoryPoints", 0) or 0),
            "knights_played": int(me.get("knightsPlayed", 0) or 0),
            "road_length": int(me.get("roadLength", 0) or 0),
            "settlements_remaining": int(me.get("settlements", 0) or 0),
            "cities_remaining": int(me.get("cities", 0) or 0),
            "roads_remaining": int(me.get("roads", 0) or 0),
            "trade_ratios": {
                r: int((state.get("tradeRatios") or {}).get(r, 4) or 4)
                for r in RESOURCES
            },
            "my_buildings": self._extract_my_buildings(state, myi),
        }

    def _extract_opponents(
        self,
        state: Dict[str, Any],
        myi: Optional[int],
        opponent_board: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        players = state.get("players")
        if not isinstance(players, list):
            return []

        board_by_index: Dict[int, Dict[str, Any]] = {}
        for ob in opponent_board:
            idx = _to_int_or_none(ob.get("index"))
            if idx is not None:
                board_by_index[idx] = ob

        opps: List[Dict[str, Any]] = []
        for idx, p in enumerate(players):
            if not isinstance(p, dict):
                continue
            if isinstance(myi, int) and idx == myi:
                continue

            res = p.get("resources")
            total_cards = int(res) if isinstance(res, (int, float)) else 0

            dev = p.get("developmentCards")
            dev_count = int(dev) if isinstance(dev, (int, float)) else 0

            board_info = board_by_index.get(idx, {})
            opps.append({
                "index": idx,
                "player_id": str(p.get("id")) if p.get("id") is not None else None,
                "name": str(p.get("name", f"Player{idx}")),
                "vp": int(p.get("victoryPoints", 0) or 0),
                "total_cards": total_cards,
                "dev_card_count": dev_count,
                "knights_played": int(p.get("knightsPlayed", 0) or 0),
                "road_length": int(p.get("roadLength", 0) or 0),
                "has_longest_road": bool(p.get("hasLongestRoad")),
                "has_largest_army": bool(p.get("hasLargestArmy")),
                "settlements_remaining": int(p.get("settlements", 0) or 0),
                "cities_remaining": int(p.get("cities", 0) or 0),
                "building_count": int(board_info.get("building_count", 0) or 0),
                "likely_production": board_info.get("likely_production", {}),
            })
        return opps

    def _extract_my_buildings(
        self, state: Dict[str, Any], myi: Optional[int]
    ) -> List[Dict[str, Any]]:
        if myi is None:
            return []

        vertices = state.get("vertices") or {}
        hexes = state.get("hexes") or {}
        buildings: List[Dict[str, Any]] = []

        for vk, v in vertices.items():
            if not isinstance(v, dict):
                continue
            if v.get("owner") != myi or not v.get("building"):
                continue

            production = self._vertex_production(vk, hexes)
            parsed = _parse_vertex_key(str(vk))
            adjacent_hexes: List[str] = []
            if parsed:
                q, r, d = parsed
                adjacent_hexes = [
                    _normalize_hex_key((hq, hr))
                    for hq, hr in _adjacent_hex_coords(q, r, d)
                ]
            buildings.append({
                "type": str(v["building"]),
                "vertex": _normalize_vertex_key(vk),
                "adjacent_hexes": adjacent_hexes,
                "production": production,
            })

        return buildings

    def _extract_opponent_board(
        self, state: Dict[str, Any], myi: Optional[int]
    ) -> List[Dict[str, Any]]:
        players = state.get("players")
        vertices = state.get("vertices") or {}
        hexes = state.get("hexes") or {}
        if not isinstance(players, list) or not isinstance(vertices, dict):
            return []

        per_player: Dict[int, Dict[str, Any]] = {}

        for idx, p in enumerate(players):
            if not isinstance(p, dict):
                continue
            if isinstance(myi, int) and idx == myi:
                continue
            per_player[idx] = {
                "index": idx,
                "player_id": str(p.get("id")) if p.get("id") is not None else None,
                "name": str(p.get("name", f"Player{idx}")),
                "buildings": [],
                "likely_production": {r: 0.0 for r in RESOURCES},
            }

        for vk, v in vertices.items():
            if not isinstance(v, dict):
                continue
            owner = _to_int_or_none(v.get("owner"))
            if owner is None or owner not in per_player:
                continue
            btype = str(v.get("building") or "")
            if not btype:
                continue

            parsed = _parse_vertex_key(str(vk))
            if not parsed:
                continue
            q, r, d = parsed
            multiplier = _building_multiplier(btype)
            bprod: List[Dict[str, Any]] = []
            adjacent_hexes: List[str] = []

            for hq, hr in _adjacent_hex_coords(q, r, d):
                hk = _normalize_hex_key((hq, hr))
                adjacent_hexes.append(hk)
                h = hexes.get(hk)
                if not isinstance(h, dict):
                    continue
                res = str(h.get("resource") or "")
                num = _to_int_or_none(h.get("number"))
                if res and res != "desert" and num is not None:
                    pips = int(PIPS.get(num, 0))
                    per_player[owner]["likely_production"][res] += float(pips * multiplier)
                    bprod.append({
                        "hex": hk,
                        "resource": res,
                        "number": num,
                        "pips": pips,
                        "multiplier": multiplier,
                        "expected_weight": float(pips * multiplier),
                    })

            per_player[owner]["buildings"].append({
                "type": btype,
                "vertex": _normalize_vertex_key(vk),
                "adjacent_hexes": adjacent_hexes,
                "production": bprod,
            })

        out: List[Dict[str, Any]] = []
        for idx in sorted(per_player.keys()):
            entry = per_player[idx]
            likely = {
                r: round(float(entry["likely_production"].get(r, 0.0)), 2)
                for r in RESOURCES
            }
            total = round(sum(likely.values()), 2)
            out.append({
                "index": entry["index"],
                "player_id": entry["player_id"],
                "name": entry["name"],
                "building_count": len(entry["buildings"]),
                "buildings": entry["buildings"],
                "likely_production": likely,
                "total_expected_production": total,
            })
        return out

    def _estimate_robber_block_impact(
        self, state: Dict[str, Any], myi: Optional[int]
    ) -> List[Dict[str, Any]]:
        vertices = state.get("vertices") or {}
        hexes = state.get("hexes") or {}
        if not isinstance(vertices, dict) or not isinstance(hexes, dict):
            return []

        # Aggregate blocked expected production if robber were moved to each hex.
        impact_by_hex: Dict[str, Dict[int, float]] = {}
        for vk, v in vertices.items():
            if not isinstance(v, dict):
                continue
            owner = _to_int_or_none(v.get("owner"))
            if owner is None or (isinstance(myi, int) and owner == myi):
                continue
            btype = v.get("building")
            if not btype:
                continue
            parsed = _parse_vertex_key(str(vk))
            if not parsed:
                continue
            q, r, d = parsed
            multiplier = _building_multiplier(btype)
            for hq, hr in _adjacent_hex_coords(q, r, d):
                hk = _normalize_hex_key((hq, hr))
                h = hexes.get(hk)
                if not isinstance(h, dict):
                    continue
                res = str(h.get("resource") or "")
                num = _to_int_or_none(h.get("number"))
                if not res or res == "desert" or num is None:
                    continue
                pips = float(PIPS.get(num, 0) * multiplier)
                if hk not in impact_by_hex:
                    impact_by_hex[hk] = {}
                impact_by_hex[hk][owner] = impact_by_hex[hk].get(owner, 0.0) + pips

        out: List[Dict[str, Any]] = []
        for hk, by_player in impact_by_hex.items():
            player_impacts = [
                {
                    "player_index": int(pi),
                    "blocked_pips": round(float(val), 2),
                }
                for pi, val in sorted(by_player.items(), key=lambda kv: kv[1], reverse=True)
            ]
            total = round(sum(item["blocked_pips"] for item in player_impacts), 2)
            out.append({
                "hex": _normalize_hex_key(hk),
                "total_blocked_pips": total,
                "by_player": player_impacts,
            })

        out.sort(key=lambda item: item.get("total_blocked_pips", 0), reverse=True)
        return out

    def _extract_board_graph(self, state: Dict[str, Any]) -> Dict[str, Any]:
        players = state.get("players")
        vertices = state.get("vertices") or {}
        edges = state.get("edges") or {}
        hexes = state.get("hexes") or {}
        robber_hex = _normalize_hex_key(state.get("robber"))

        players_list = players if isinstance(players, list) else []
        players_by_index: Dict[int, Dict[str, Any]] = {}
        graph_players: List[Dict[str, Any]] = []
        for idx, p in enumerate(players_list):
            if not isinstance(p, dict):
                continue
            players_by_index[idx] = p
            graph_players.append({
                "index": idx,
                "player_id": str(p.get("id")) if p.get("id") is not None else None,
                "name": str(p.get("name", f"Player{idx}")),
            })

        graph_hexes: List[Dict[str, Any]] = []
        if isinstance(hexes, dict):
            for hk, h in sorted(hexes.items(), key=lambda kv: _normalize_hex_key(kv[0])):
                if not isinstance(h, dict):
                    continue
                norm_hk = _normalize_hex_key(hk)
                graph_hexes.append({
                    "hex": norm_hk,
                    "resource": str(h.get("resource", "")),
                    "number": _to_int_or_none(h.get("number")),
                    "robber": norm_hk == robber_hex,
                })

        graph_vertices: List[Dict[str, Any]] = []
        if isinstance(vertices, dict):
            for vk, v in sorted(vertices.items(), key=lambda kv: _normalize_vertex_key(kv[0])):
                if not isinstance(v, dict):
                    continue
                norm_vk = _normalize_vertex_key(vk)
                parsed = _parse_vertex_key(norm_vk)
                adjacent_hexes: List[str] = []
                if parsed:
                    q, r, d = parsed
                    adjacent_hexes = [
                        _normalize_hex_key((hq, hr))
                        for hq, hr in _adjacent_hex_coords(q, r, d)
                    ]
                owner_index = _to_int_or_none(v.get("owner"))
                owner_info = players_by_index.get(owner_index) if owner_index is not None else None
                piece_type = str(v.get("building") or "none")
                graph_vertices.append({
                    "vertex": norm_vk,
                    "adjacent_hexes": adjacent_hexes,
                    "occupancy": {
                        "piece_type": piece_type,
                        "owner_index": owner_index,
                        "owner_id": (
                            str(owner_info.get("id"))
                            if isinstance(owner_info, dict) and owner_info.get("id") is not None
                            else None
                        ),
                        "owner_name": (
                            str(owner_info.get("name"))
                            if isinstance(owner_info, dict) and owner_info.get("name") is not None
                            else None
                        ),
                    },
                })

        graph_edges: List[Dict[str, Any]] = []
        if isinstance(edges, dict):
            for ek, e in sorted(edges.items(), key=lambda kv: str(kv[0])):
                if not isinstance(e, dict):
                    continue
                parsed = _parse_edge_key(str(ek))
                if parsed:
                    q, r, d = parsed
                    edge_vertices = [_normalize_vertex_key(vk) for vk in _edge_vertices(q, r, d)]
                else:
                    edge_vertices = []
                owner_index = _to_int_or_none(e.get("owner"))
                owner_info = players_by_index.get(owner_index) if owner_index is not None else None
                has_road = bool(e.get("road"))
                graph_edges.append({
                    "edge": str(ek),
                    "vertices": edge_vertices,
                    "occupancy": {
                        "piece_type": "road" if has_road else "none",
                        "owner_index": owner_index if has_road else None,
                        "owner_id": (
                            str(owner_info.get("id"))
                            if has_road and isinstance(owner_info, dict) and owner_info.get("id") is not None
                            else None
                        ),
                        "owner_name": (
                            str(owner_info.get("name"))
                            if has_road and isinstance(owner_info, dict) and owner_info.get("name") is not None
                            else None
                        ),
                    },
                })

        return {
            "players": graph_players,
            "hexes": graph_hexes,
            "vertices": graph_vertices,
            "edges": graph_edges,
        }

    @staticmethod
    def _vertex_production(
        vk: str, hexes: Dict[str, Any]
    ) -> List[str]:
        parsed = _parse_vertex_key(vk)
        if not parsed:
            return []
        q, r, d = parsed

        production: List[str] = []
        for hq, hr in _adjacent_hex_coords(q, r, d):
            h = hexes.get(f"{hq},{hr}")
            if not isinstance(h, dict):
                continue
            res = h.get("resource")
            num = h.get("number")
            if res and res != "desert" and num is not None:
                production.append(f"{res}:{int(num)}")
        return production

    @staticmethod
    def _extract_achievement(
        state: Dict[str, Any],
        player_key: str,
        length_key: str,
        label: str,
    ) -> Dict[str, Any]:
        holder_idx = state.get(player_key)
        length = state.get(length_key, 0)
        holder_name = None
        holder_index = _to_int_or_none(holder_idx)
        if isinstance(holder_idx, int):
            players = state.get("players") or []
            if 0 <= holder_idx < len(players):
                p = players[holder_idx]
                holder_name = p.get("name") if isinstance(p, dict) else None
        metric = int(length or 0)
        return {
            "holder": str(holder_name) if holder_name is not None else None,
            "holder_index": holder_index,
            "length": metric,
            "size": metric,
        }

    @staticmethod
    def _extract_trade_offer(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for key in ("tradeOffer", "activeTradeOffer", "currentTradeOffer"):
            v = state.get(key)
            if isinstance(v, dict):
                return {
                    "from_index": _to_int_or_none(v.get("from")),
                    "to_index": _to_int_or_none(v.get("to")),
                    "offer": GameStateProcessor._normalize_resource_dict(v.get("offer") or {}),
                    "request": GameStateProcessor._normalize_resource_dict(v.get("request") or {}),
                }
        return None

    @staticmethod
    def _normalize_dev_cards(cards: list) -> List[str]:
        out: List[str] = []
        for item in cards:
            if isinstance(item, str):
                out.append(item)
            elif isinstance(item, dict):
                out.append(
                    item.get("type") or item.get("card") or item.get("name") or "unknown"
                )
            else:
                out.append("unknown")
        return out

    @staticmethod
    def _normalize_resource_dict(payload: Any) -> Dict[str, int]:
        if not isinstance(payload, dict):
            return {}
        out: Dict[str, int] = {}
        for r in RESOURCES:
            try:
                amt = int(payload.get(r, 0) or 0)
            except Exception:
                amt = 0
            if amt > 0:
                out[r] = amt
        return out
