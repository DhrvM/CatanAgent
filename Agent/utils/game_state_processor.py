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

        return {
            "phase": state.get("phase"),
            "setup_phase": state.get("setupPhase"),
            "turn_phase": state.get("turnPhase"),
            "is_my_turn": self._is_my_turn(state),
            "current_player_index": state.get("currentPlayerIndex"),
            "dice_roll": self._extract_dice(state),
            "me": self._extract_me(state, me, myi),
            "opponents": self._extract_opponents(state, myi),
            "robber_hex": state.get("robber"),
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

    def format_for_llm(
        self, processed: Dict[str, Any], detail: str = "full"
    ) -> str:
        """Produce a concise natural-language summary of the board state."""
        _ = detail  # Backward-compatible arg used by existing callers.
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
            lines.append(
                f"Opponent '{opp['name']}': VP={opp.get('vp', '?')}, "
                f"cards={opp.get('total_cards', '?')}, "
                f"knights={opp.get('knights_played', 0)}, "
                f"road_len={opp.get('road_length', 0)}"
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
            "index": myi,
            "name": me.get("name"),
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
            "trade_ratios": state.get("tradeRatios") or {},
            "my_buildings": self._extract_my_buildings(state, myi),
        }

    def _extract_opponents(
        self, state: Dict[str, Any], myi: Optional[int]
    ) -> List[Dict[str, Any]]:
        players = state.get("players")
        if not isinstance(players, list):
            return []

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

            opps.append({
                "index": idx,
                "name": p.get("name", f"Player{idx}"),
                "vp": int(p.get("victoryPoints", 0) or 0),
                "total_cards": total_cards,
                "dev_card_count": dev_count,
                "knights_played": int(p.get("knightsPlayed", 0) or 0),
                "road_length": int(p.get("roadLength", 0) or 0),
                "has_longest_road": bool(p.get("hasLongestRoad")),
                "has_largest_army": bool(p.get("hasLargestArmy")),
                "settlements_remaining": int(p.get("settlements", 0) or 0),
                "cities_remaining": int(p.get("cities", 0) or 0),
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
            buildings.append({
                "type": v["building"],
                "vertex": vk,
                "production": production,
            })

        return buildings

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
                production.append(f"{res}:{num}")
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
        if isinstance(holder_idx, int):
            players = state.get("players") or []
            if 0 <= holder_idx < len(players):
                p = players[holder_idx]
                holder_name = p.get("name") if isinstance(p, dict) else None
        return {"holder": holder_name, "length": int(length or 0)}

    @staticmethod
    def _extract_trade_offer(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for key in ("tradeOffer", "activeTradeOffer", "currentTradeOffer"):
            v = state.get(key)
            if isinstance(v, dict):
                return {
                    "from_index": v.get("from"),
                    "to_index": v.get("to"),
                    "offer": v.get("offer") or {},
                    "request": v.get("request") or {},
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
