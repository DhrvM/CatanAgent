"""
Formal tool definitions following OpenAI function-calling conventions.

Each tool has:
  - a typed schema (so GPT-4o can decide *what* to call and *with what args*)
  - a handler that actually executes the action via the Catan socket client
  - phase-aware availability (e.g. roll_dice only during 'roll' phase)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from Agent.utils.socket_client import CatanSocketClient
from Agent.utils.game_state_processor import GameStateProcessor
from Agent.tools.game_tools import (
    _all_vertex_keys_from_state,
    _all_edge_keys_from_state,
    _all_hex_keys_from_state,
    _score_vertex_for_setup,
    _score_vertex_for_city,
    _ranked_setup_settlements,
    _ranked_setup_roads,
    ranked_main_settlement_vertices,
    projected_settlement_vertices_after_one_road,
    _can_afford_settlement,
    _build_discard_action,
    _bank_trade_actions,
    _me,
    ranked_main_road_edges,
    RESOURCES,
)


# ── Schema types ──────────────────────────────────────────────────

@dataclass
class ToolParameter:
    name: str
    type: str  # "string", "integer", "number", "boolean", "object", "array"
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    properties: Optional[Dict[str, Any]] = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: List[ToolParameter] = field(default_factory=list)
    handler: Optional[Callable[..., Dict[str, Any]]] = None
    phases: Optional[List[str]] = None  # turn phases where this tool is available
    agents: Optional[List[str]] = None  # agent names that can use this tool


# ── Registry ──────────────────────────────────────────────────────

class ToolRegistry:
    """Central registry for all agent tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def get_openai_schemas(
        self,
        phase_filter: Optional[str] = None,
        agent_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return tool definitions in OpenAI function-calling format.
        Optionally filter by the current turn phase and/or agent name.
        """
        schemas: List[Dict[str, Any]] = []
        for t in self._tools.values():
            if phase_filter and t.phases and phase_filter not in t.phases:
                continue
            if agent_filter and t.agents and agent_filter not in t.agents:
                continue
            schema = self._tool_to_openai(t)
            schemas.append(schema)
        return schemas

    def execute(
        self, name: str, args: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run a tool by name and return the result dict."""
        tool = self._tools.get(name)
        if tool is None:
            return {"success": False, "error": f"Unknown tool: {name}"}
        if tool.handler is None:
            return {"success": False, "error": f"Tool {name} has no handler"}
        try:
            return tool.handler(**args)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_names(self) -> List[str]:
        return list(self._tools.keys())

    # ── internal ──────────────────────────────────────────────────

    @staticmethod
    def _tool_to_openai(t: ToolDefinition) -> Dict[str, Any]:
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for p in t.parameters:
            prop: Dict[str, Any] = {
                "type": p.type,
                "description": p.description,
            }
            if p.enum:
                prop["enum"] = p.enum
            if p.properties:
                prop["properties"] = p.properties
            properties[p.name] = prop
            if p.required:
                required.append(p.name)

        return {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }


# ── safe socket call helper ───────────────────────────────────────

def _safe_call(
    client: CatanSocketClient,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
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


# ── Factory: register all 17 tools ────────────────────────────────

def build_tool_registry(
    client: CatanSocketClient,
    processor: GameStateProcessor,
) -> ToolRegistry:
    """
    Create and return a fully-populated ToolRegistry bound to the
    given socket client and state processor.
    """
    reg = ToolRegistry()

    # ── 1. roll_dice ──────────────────────────────────────────────
    def _roll_dice() -> Dict[str, Any]:
        return _safe_call(client, "rollDice")

    reg.register(ToolDefinition(
        name="roll_dice",
        description="Roll the dice at the start of your turn. Must be called before any other main-phase action.",
        parameters=[],
        handler=_roll_dice,
        phases=["roll"],
        agents=["strategy"],
    ))

    # ── 2. place_settlement ───────────────────────────────────────
    def _place_settlement(vertex_key: str, is_setup: bool = False) -> Dict[str, Any]:
        return _safe_call(client, "placeSettlement", {
            "vertexKey": vertex_key,
            "isSetup": is_setup,
        })

    reg.register(ToolDefinition(
        name="place_settlement",
        description=(
            "Place a settlement at the given vertex. "
            "Costs 1 brick + 1 lumber + 1 wool + 1 grain (free during setup). "
            "Use get_building_spots first to find legal vertices."
        ),
        parameters=[
            ToolParameter("vertex_key", "string", "Vertex key like 'v_0_-1_2'"),
            ToolParameter("is_setup", "boolean", "True during setup phase", required=False),
        ],
        handler=_place_settlement,
        phases=["main", "setup"],
        agents=["development"],
    ))

    # ── 3. place_road ─────────────────────────────────────────────
    def _place_road(
        edge_key: str,
        is_setup: bool = False,
        last_settlement: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"edgeKey": edge_key, "isSetup": is_setup}
        if last_settlement:
            payload["lastSettlement"] = last_settlement
        return _safe_call(client, "placeRoad", payload)

    reg.register(ToolDefinition(
        name="place_road",
        description=(
            "Place a road at the given edge. Costs 1 brick + 1 lumber (free during setup). "
            "During setup, must be adjacent to your last settlement."
        ),
        parameters=[
            ToolParameter("edge_key", "string", "Edge key like 'e_0_-1_2'"),
            ToolParameter("is_setup", "boolean", "True during setup phase", required=False),
            ToolParameter("last_settlement", "string", "Vertex key of last placed settlement (setup only)", required=False),
        ],
        handler=_place_road,
        phases=["main", "setup"],
        agents=["development"],
    ))

    # ── 4. upgrade_to_city ────────────────────────────────────────
    def _upgrade_to_city(vertex_key: str) -> Dict[str, Any]:
        return _safe_call(client, "upgradeToCity", {"vertexKey": vertex_key})

    reg.register(ToolDefinition(
        name="upgrade_to_city",
        description="Upgrade an existing settlement to a city. Costs 3 ore + 2 grain. Doubles resource production.",
        parameters=[
            ToolParameter("vertex_key", "string", "Vertex key of YOUR settlement to upgrade"),
        ],
        handler=_upgrade_to_city,
        phases=["main"],
        agents=["development"],
    ))

    # ── 5. buy_dev_card ───────────────────────────────────────────
    def _buy_dev_card() -> Dict[str, Any]:
        return _safe_call(client, "buyDevCard")

    reg.register(ToolDefinition(
        name="buy_dev_card",
        description="Buy a development card from the deck. Costs 1 ore + 1 grain + 1 wool.",
        parameters=[],
        handler=_buy_dev_card,
        phases=["main"],
        agents=["development"],
    ))

    # ── 6. play_dev_card ──────────────────────────────────────────
    def _play_dev_card(card_type: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"cardType": card_type}
        if params:
            payload["params"] = params
        return _safe_call(client, "playDevCard", payload)

    reg.register(ToolDefinition(
        name="play_dev_card",
        description=(
            "Play a development card. Types: 'knight' (move robber), "
            "'roadBuilding' (2 free roads), 'yearOfPlenty' (pick 2 resources), "
            "'monopoly' (steal all of one resource). For monopoly, pass params={\"resource\": \"brick\"}. "
            "Can play knight before rolling; others require main phase. "
            "Only 1 dev card per turn. Cannot play cards bought this turn."
        ),
        parameters=[
            ToolParameter("card_type", "string", "Dev card type to play",
                          enum=["knight", "roadBuilding", "yearOfPlenty", "monopoly"]),
            ToolParameter("params", "object", "Extra params (e.g. {resource: 'brick'} for monopoly)",
                          required=False),
        ],
        handler=_play_dev_card,
        phases=["roll", "main"],
        agents=["development"],
    ))

    # ── 7. bank_trade ─────────────────────────────────────────────
    def _bank_trade(give_resource: str, give_amount: int, get_resource: str) -> Dict[str, Any]:
        return _safe_call(client, "bankTrade", {
            "giveResource": give_resource,
            "giveAmount": int(give_amount),
            "getResource": get_resource,
        })

    reg.register(ToolDefinition(
        name="bank_trade",
        description=(
            "Trade resources with the bank. Default ratio is 4:1, reduced by ports. "
            "Use get_trade_options to see available trades and ratios."
        ),
        parameters=[
            ToolParameter("give_resource", "string", "Resource to give", enum=RESOURCES),
            ToolParameter("give_amount", "integer", "Amount to give (usually 4, 3, or 2 depending on ports)"),
            ToolParameter("get_resource", "string", "Resource to receive (1 unit)", enum=RESOURCES),
        ],
        handler=_bank_trade,
        phases=["main"],
        agents=["trading"],
    ))

    # ── 8. propose_trade ──────────────────────────────────────────
    def _propose_trade(offer: Dict[str, int], request: Dict[str, int]) -> Dict[str, Any]:
        return _safe_call(client, "proposeTrade", {
            "offer": offer,
            "request": request,
        })

    reg.register(ToolDefinition(
        name="propose_trade",
        description="Propose a trade to other players. Specify what you offer and what you want.",
        parameters=[
            ToolParameter("offer", "object", "Resources you're offering, e.g. {\"brick\": 1, \"lumber\": 1}"),
            ToolParameter("request", "object", "Resources you want, e.g. {\"grain\": 1}"),
        ],
        handler=_propose_trade,
        phases=["main"],
        agents=["trading"],
    ))

    # ── 9. respond_to_trade ───────────────────────────────────────
    def _respond_to_trade(accept: bool) -> Dict[str, Any]:
        return _safe_call(client, "respondToTrade", {"accept": accept})

    reg.register(ToolDefinition(
        name="respond_to_trade",
        description="Accept or decline an incoming trade offer from another player.",
        parameters=[
            ToolParameter("accept", "boolean", "True to accept, False to decline"),
        ],
        handler=_respond_to_trade,
        phases=["main"],
        agents=["trading"],
    ))

    # ── 9b. counter_trade ─────────────────────────────────────────
    def _counter_trade(offer: Dict[str, int], request: Dict[str, int]) -> Dict[str, Any]:
        return _safe_call(client, "counterTrade", {
            "offer": offer,
            "request": request,
        })

    reg.register(ToolDefinition(
        name="counter_trade",
        description="Send a counter-offer in response to an incoming targeted trade.",
        parameters=[
            ToolParameter("offer", "object", "Resources you offer in the counter, e.g. {\"brick\": 1}"),
            ToolParameter("request", "object", "Resources you request in the counter, e.g. {\"grain\": 1}"),
        ],
        handler=_counter_trade,
        phases=["main"],
        agents=["trading"],
    ))

    # ── 9c. cancel_trade ──────────────────────────────────────────
    def _cancel_trade() -> Dict[str, Any]:
        return _safe_call(client, "cancelTrade")

    reg.register(ToolDefinition(
        name="cancel_trade",
        description="Cancel your currently active outgoing trade offer.",
        parameters=[],
        handler=_cancel_trade,
        phases=["main"],
        agents=["trading"],
    ))

    # ── 9d. get_trade_offer_status ────────────────────────────────
    def _get_trade_offer_status() -> Dict[str, Any]:
        state = client.latest_state() or {}
        offer = None
        for key in ("activeTradeOffer", "tradeOffer", "currentTradeOffer"):
            v = state.get(key)
            if isinstance(v, dict):
                offer = v
                break

        if not isinstance(offer, dict):
            return {"has_offer": False}

        players = state.get("players")
        myi = state.get("myIndex")
        my_id = None
        if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players):
            me = players[myi] if isinstance(players[myi], dict) else {}
            my_id = me.get("id")

        offer_from = offer.get("from") if offer.get("from") is not None else (
            offer.get("fromId") if offer.get("fromId") is not None else offer.get("from_id")
        )
        offer_to = offer.get("to") if offer.get("to") is not None else (
            offer.get("toId") if offer.get("toId") is not None else offer.get("to_id")
        )

        offer_from_str = str(offer_from) if offer_from is not None else ""
        me_candidates = {str(myi), str(my_id) if my_id is not None else ""}
        offer_from_me = offer_from_str in me_candidates

        return {
            "has_offer": True,
            "offer_from": offer_from,
            "offer_to": offer_to,
            "offer_from_me": offer_from_me,
            "can_i_respond": bool(
                offer.get("can_i_respond")
                or offer.get("canRespond")
                or offer.get("canRespondToTrade")
            ),
        }

    reg.register(ToolDefinition(
        name="get_trade_offer_status",
        description="Return whether a trade offer is active and whether it was proposed by us.",
        parameters=[],
        handler=_get_trade_offer_status,
        phases=["main"],
        agents=["trading"],
    ))

    # ── 10. discard_cards ─────────────────────────────────────────
    def _discard_cards(resources: Dict[str, int]) -> Dict[str, Any]:
        return _safe_call(client, "discardCards", {"resources": resources})

    reg.register(ToolDefinition(
        name="discard_cards",
        description="Discard cards when a 7 is rolled and you have more than 7 cards. Must discard half (rounded down).",
        parameters=[
            ToolParameter("resources", "object",
                          "Resources to discard, e.g. {\"brick\": 2, \"lumber\": 1}"),
        ],
        handler=_discard_cards,
        phases=["discard"],
        agents=["development"],
    ))

    # ── 11. move_robber ───────────────────────────────────────────
    def _resolve_player_id(player_ref: Optional[str], state: Dict[str, Any]) -> Optional[str]:
        """Accept player id or case-insensitive name match; server expects id."""
        if player_ref is None or player_ref == "":
            return None
        players = state.get("players")
        if not isinstance(players, list):
            return player_ref
        ref_l = str(player_ref).strip().lower()
        for p in players:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            name = p.get("name")
            if pid is not None and str(pid) == str(player_ref):
                return str(pid)
            if isinstance(name, str) and name.strip().lower() == ref_l:
                return str(pid) if pid is not None else None
        return player_ref

    def _move_robber(hex_key: str, steal_from_player_id: Optional[str] = None) -> Dict[str, Any]:
        state = client.latest_state() or {}
        resolved = _resolve_player_id(steal_from_player_id, state)
        return _safe_call(client, "moveRobber", {
            "hexKey": hex_key,
            "stealFromPlayerId": resolved,
        })

    reg.register(ToolDefinition(
        name="move_robber",
        description=(
            "Move the robber to a new hex and optionally steal a random resource from a player. "
            "Cannot place on current robber hex. Use get_players_on_hex to see who to steal from."
        ),
        parameters=[
            ToolParameter("hex_key", "string", "Hex coordinate key like '0,-1'"),
            ToolParameter("steal_from_player_id", "string",
                          "Player ID to steal from (or null)", required=False),
        ],
        handler=_move_robber,
        phases=["robber"],
        agents=["development"],
    ))

    # ── 12. end_turn ──────────────────────────────────────────────
    def _end_turn() -> Dict[str, Any]:
        return _safe_call(client, "endTurn")

    reg.register(ToolDefinition(
        name="end_turn",
        description="End your turn and pass to the next player. Call this when you're done with all actions.",
        parameters=[],
        handler=_end_turn,
        phases=["main"],
        agents=["strategy"],
    ))

    # ── 13. get_building_spots ────────────────────────────────────
    def _get_building_spots(
        building_type: str = "settlement",
    ) -> Dict[str, Any]:
        state = client.latest_state() or {}
        building_type = str(building_type or "settlement").lower()
        myi = state.get("myIndex")
        vertices = state.get("vertices") or {}

        if building_type == "city":
            if myi is None:
                return {"spots": []}
            spots = []
            for vk, v in vertices.items():
                if isinstance(v, dict) and v.get("owner") == myi and v.get("building") == "settlement":
                    score = _score_vertex_for_city(state, vk)
                    prod = processor._vertex_production(vk, state.get("hexes") or {})
                    spots.append({"vertex": vk, "score": round(score, 2), "production": prod})
            spots.sort(key=lambda s: s["score"], reverse=True)
            return {"building_type": "city", "spots": spots}

        if building_type == "road":
            if state.get("phase") == "setup":
                return {
                    "building_type": "road",
                    "spots": [],
                    "note": "During setup, roads must connect to your last settlement; use setup placement flow.",
                }
            if not isinstance(myi, int):
                return {"building_type": "road", "spots": []}
            spots = ranked_main_road_edges(state, myi, top_k=40)
            return {"building_type": "road", "spots": spots}

        spots: List[Dict[str, Any]] = []
        hexes = state.get("hexes") or {}
        if state.get("phase") == "setup":
            ranked = _ranked_setup_settlements(state, top_k=10)
            for vk in ranked:
                score = _score_vertex_for_setup(state, vk)
                prod = processor._vertex_production(vk, hexes)
                spots.append({"vertex": vk, "score": round(score, 2), "production": prod})
            return {"building_type": "settlement", "spots": spots}

        if not isinstance(myi, int):
            return {"building_type": "settlement", "spots": []}

        affordable_now = _can_afford_settlement(state, myi)
        ranked_legal = ranked_main_settlement_vertices(state, myi, top_k=20)
        for item in ranked_legal:
            vk = item.get("vertex")
            if not isinstance(vk, str):
                continue
            prod = processor._vertex_production(vk, hexes)
            spots.append({
                "vertex": vk,
                "score": item.get("score", 0.0),
                "base_score": item.get("base_score", 0.0),
                "horizon_score": item.get("horizon_score", 0.0),
                "affordable_now": affordable_now,
                "production": prod,
            })

        payload: Dict[str, Any] = {
            "building_type": "settlement",
            "spots": spots,
            "affordable_now": affordable_now,
        }
        if not affordable_now:
            payload["note"] = (
                "Settlement spots are legal by board position, but you cannot afford a "
                "settlement yet; gather resources first."
            )
        return payload

    reg.register(ToolDefinition(
        name="get_building_spots",
        description=(
            "List legal spots to build now. For settlement/city returns vertices ranked by score; "
            "Use building_type='settlement', 'city', or 'road' (for main phase edges to place_road). "
            "NOTE: The server still validates legality — some spots may be already taken."
        ),
        parameters=[
            ToolParameter("building_type", "string", "Type of building to check spots for",
                          required=False, enum=["settlement", "city", "road"]),
        ],
        handler=_get_building_spots,
        phases=["main", "setup"],
        agents=["strategy", "development"],
    ))

    # ── 13b. get_settlement_horizon_spots ────────────────────────
    def _get_settlement_horizon_spots() -> Dict[str, Any]:
        state = client.latest_state() or {}
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            return {"spots": []}

        hexes = state.get("hexes") or {}
        future_spots = projected_settlement_vertices_after_one_road(state, myi, top_k=12)
        spots: List[Dict[str, Any]] = []
        for item in future_spots:
            vk = item.get("vertex")
            if not isinstance(vk, str):
                continue
            prod = processor._vertex_production(vk, hexes)
            spots.append({
                "vertex": vk,
                "score": item.get("score", 0.0),
                "reachable_via_edges": item.get("reachable_via_edges", []),
                "production": prod,
            })
        return {
            "spots": spots,
            "note": (
                "Projected expansion targets: settlement vertices that can become reachable "
                "after placing one legal connecting road."
            ),
        }

    reg.register(ToolDefinition(
        name="get_settlement_horizon_spots",
        description=(
            "Plan expansion horizon: show promising settlement vertices that can be opened "
            "after one additional legal road."
        ),
        parameters=[],
        handler=_get_settlement_horizon_spots,
        phases=["main"],
        agents=["strategy", "development"],
    ))

    # ── 14. get_trade_options ─────────────────────────────────────
    def _get_trade_options() -> Dict[str, Any]:
        state = client.latest_state() or {}
        players = state.get("players")
        myi = state.get("myIndex")
        if not isinstance(players, list) or not isinstance(myi, int):
            return {"options": []}

        me = players[myi] if 0 <= myi < len(players) else {}
        hand = me.get("resources") if isinstance(me.get("resources"), dict) else {}
        ratios = state.get("tradeRatios") or {}

        options: List[Dict[str, Any]] = []
        for give_r in RESOURCES:
            have = int(hand.get(give_r, 0) or 0)
            ratio = int(ratios.get(give_r, 4) or 4)
            if ratio <= 0:
                ratio = 4
            if have < ratio:
                continue
            for get_r in RESOURCES:
                if get_r == give_r:
                    continue
                options.append({
                    "give": give_r,
                    "amount": ratio,
                    "get": get_r,
                })
        return {"options": options}

    reg.register(ToolDefinition(
        name="get_trade_options",
        description="List all available bank/port trades based on your current hand and trade ratios.",
        parameters=[],
        handler=_get_trade_options,
        phases=["main"],
        agents=["strategy", "trading"],
    ))

    # ── 15. get_game_summary ──────────────────────────────────────
    def _get_game_summary() -> Dict[str, Any]:
        state = client.latest_state() or {}
        processed = processor.process(state)
        return {"summary": processor.format_for_llm(processed)}

    reg.register(ToolDefinition(
        name="get_game_summary",
        description="Get a detailed text summary of the current game state, your resources, buildings, and opponents.",
        parameters=[],
        handler=_get_game_summary,
        phases=["roll", "main", "robber", "discard", "setup", "specialBuild"],
        agents=["strategy", "development", "trading"],
    ))

    # ── 16. advance_setup ─────────────────────────────────────────
    def _advance_setup() -> Dict[str, Any]:
        return _safe_call(client, "advanceSetup")

    reg.register(ToolDefinition(
        name="advance_setup",
        description="Advance to the next player during setup phase. Call after placing settlement + road.",
        parameters=[],
        handler=_advance_setup,
        phases=["setup"],
        agents=["strategy"],
    ))

    # ── 17. year_of_plenty_pick ───────────────────────────────────
    def _year_of_plenty_pick(resource: str) -> Dict[str, Any]:
        return _safe_call(client, "yearOfPlentyPick", {"resource": resource})

    reg.register(ToolDefinition(
        name="year_of_plenty_pick",
        description="Pick a free resource when using the Year of Plenty development card. Called twice (one resource each time).",
        parameters=[
            ToolParameter("resource", "string", "Resource to pick", enum=RESOURCES),
        ],
        handler=_year_of_plenty_pick,
        phases=["main"],
        agents=["development"],
    ))

    # ── Risk analysis tools (from probabilities.py) ─────────────
    from Agent.tools.risk_tools import register_risk_tools
    register_risk_tools(reg, client)

    return reg
