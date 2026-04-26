"""
Deterministic benchmark-calibration agent for live Catan games.

The goal is not to be clever or expressive. The goal is to be consistent,
fast, and aligned with the benchmark heuristics so it can act as a fairness
probe for the benchmark itself.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from Agent.benchmark_agent.decision import BenchmarkDecisionEngine
from Agent.Tools.game_tools import (
    RESOURCES,
    _adjacent_hexes_for_vertex,
    _adjacent_vertices,
    _build_discard_action,
    _edge_is_unoccupied,
    _edge_vertices,
    _edges_touching_vertex,
    _main_road_connects_to_network,
    _parse_edge_key,
    _ranked_setup_roads,
    _ranked_setup_settlements,
    _pip_value,
    is_my_turn,
    ranked_main_road_edges,
)
from Agent.Tools.registry import ToolRegistry, build_tool_registry
from Agent.risk_agent.probabilities import (
    expected_resource_income,
    opponent_threat_assessment,
    robber_impact_analysis,
    rank_cities_by_value_gain,
    rank_vertices_by_expected_value,
)
from Agent.utils.game_state_processor import GameStateProcessor
from Agent.utils.socket_client import CatanSocketClient
from Agent.utils.stats_tracker import AgentStatsTracker


BUILD_COSTS = {
    "settlement": {"brick": 1, "lumber": 1, "wool": 1, "grain": 1},
    "road": {"brick": 1, "lumber": 1},
    "city": {"ore": 3, "grain": 2},
    "dev_card": {"ore": 1, "grain": 1, "wool": 1},
}

TRADE_RESPONSE_TIMEOUT_S = 45.0


class BenchmarkCalibrationAgent:
    """
    A deterministic agent that can both solve benchmark task payloads and play
    full games through the socket server.
    """

    def __init__(
        self,
        server_url: str = "http://localhost:3001",
        game_code: Optional[str] = None,
        player_name: str = "BenchmarkBot",
    ) -> None:
        self.client = CatanSocketClient(server_url)
        self.game_code = game_code
        self.player_name = player_name
        self.processor = GameStateProcessor()
        self.registry: Optional[ToolRegistry] = None
        self.stats = AgentStatsTracker(agent_name=player_name)
        self.decisions = BenchmarkDecisionEngine()

        self._turn_counter = 0
        self._last_setup_settlement: Optional[str] = None
        self._last_offer_signature: Optional[str] = None

    def solve_benchmark_task(self, task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.decisions.solve_task(task_id, payload)

    def run(self) -> None:
        self.client.connect()
        if self.game_code:
            self.client.join_game(self.game_code, self.player_name)
        else:
            ack = self.client.create_game(self.player_name)
            self.game_code = ack["gameCode"]
            print(f"Created game: {self.game_code}")

        self.registry = build_tool_registry(self.client, self.processor)
        print(f"[OK] {self.player_name} benchmark agent running (game {self.game_code})")

        try:
            while True:
                state = self.client.latest_state()
                if not state:
                    time.sleep(0.25)
                    continue

                if state.get("turnPhase") == "discard" and self._needs_to_discard(state):
                    self._handle_discard(state)
                    time.sleep(0.15)
                    continue

                if not is_my_turn(state):
                    self._respond_to_incoming_trade(state)
                    time.sleep(0.25)
                    continue

                phase = state.get("phase")
                turn_phase = state.get("turnPhase")
                if phase == "setup":
                    self._handle_setup(state)
                elif turn_phase == "roll":
                    self._execute("roll_dice", {})
                elif turn_phase == "discard":
                    self._handle_discard(state)
                elif turn_phase == "robber":
                    self._handle_robber(state)
                elif turn_phase == "main":
                    self._handle_main(state)
                else:
                    time.sleep(0.25)
                time.sleep(0.15)
        except KeyboardInterrupt:
            print("\n[benchmark] interrupted")
        finally:
            paths = self.stats.export_all("./logs/Agent")
            print(f"[benchmark] stats saved: {paths['json']}")

    def _handle_setup(self, state: Dict[str, Any]) -> None:
        self._start_turn("setup")
        if self._last_setup_settlement is None:
            for vertex_key in self._rank_benchmark_setup_settlements(state, top_k=160):
                result = self._execute("place_settlement", {
                    "vertex_key": vertex_key,
                    "is_setup": True,
                })
                if self._ok(result):
                    self._last_setup_settlement = vertex_key
                    print(f"  [setup] settlement {vertex_key}")
                    self.stats.end_turn()
                    return
            self.stats.end_turn()
            return

        for edge_key in _ranked_setup_roads(state, self._last_setup_settlement, top_k=240):
            result = self._execute("place_road", {
                "edge_key": edge_key,
                "is_setup": True,
                "last_settlement": self._last_setup_settlement,
            })
            if self._ok(result):
                print(f"  [setup] road {edge_key}")
                self._execute("advance_setup", {})
                self._last_setup_settlement = None
                self.stats.end_turn()
                return
        self.stats.end_turn()

    def _rank_benchmark_setup_settlements(self, state: Dict[str, Any], top_k: int = 160) -> List[str]:
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        candidates = [
            vertex_key
            for vertex_key, vertex in vertices.items()
            if isinstance(vertex, dict) and not vertex.get("building") and self._is_distance_rule_open(state, vertex_key)
        ]
        if not candidates:
            return _ranked_setup_settlements(state, top_k=top_k)
        candidates.sort(key=lambda vertex_key: self._benchmark_settlement_score(state, vertex_key), reverse=True)
        return candidates[:top_k]

    def _benchmark_settlement_score(self, state: Dict[str, Any], vertex_key: str) -> float:
        vertex_score = self._benchmark_vertex_score(state, vertex_key)
        return (
            (vertex_score["resourceProductionExpectancy"] * 0.40)
            + (vertex_score["resourceDiversity"] * 0.25)
            + (self._settlement_expansion_access(state, vertex_key) * 0.20)
            + (vertex_score["portSynergy"] * 0.15)
        )

    def _settlement_expansion_access(self, state: Dict[str, Any], vertex_key: str) -> float:
        open_edges = sum(
            1
            for edge_key in _edges_touching_vertex(state, vertex_key)
            if self._edge_is_physically_unoccupied(state, edge_key)
        )
        return min(1.0, open_edges / 3.0)

    @staticmethod
    def _is_distance_rule_open(state: Dict[str, Any], vertex_key: str) -> bool:
        vertex = (state.get("vertices") or {}).get(vertex_key)
        if isinstance(vertex, dict) and vertex.get("building"):
            return False
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        for adjacent in _adjacent_vertices(vertex_key):
            adjacent_record = vertices.get(adjacent)
            if isinstance(adjacent_record, dict) and adjacent_record.get("building"):
                return False
        return True

    def _handle_discard(self, state: Dict[str, Any]) -> None:
        self._start_turn("discard")
        payload = self._discard_payload(state)
        if payload:
            result = self._execute("discard_cards", {"resources": payload})
            print(f"  [discard] {payload} => {result}")
        else:
            print("  [discard] no valid discard payload found")
        self.stats.end_turn()

    def _needs_to_discard(self, state: Dict[str, Any]) -> bool:
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            return False
        discarding = state.get("discardingPlayers")
        if not isinstance(discarding, list):
            return False
        return any(
            isinstance(entry, dict)
            and int(entry.get("playerIndex", entry.get("player_index", -1)) or -1) == myi
            for entry in discarding
        )

    def _discard_payload(self, state: Dict[str, Any]) -> Dict[str, int]:
        actions = _build_discard_action(state)
        if actions:
            resources = actions[0].payload.get("resources") if isinstance(actions[0].payload, dict) else None
            if isinstance(resources, dict):
                return {resource: int(resources.get(resource, 0) or 0) for resource in RESOURCES if int(resources.get(resource, 0) or 0) > 0}

        myi = state.get("myIndex")
        discarding = state.get("discardingPlayers")
        if not isinstance(myi, int) or not isinstance(discarding, list):
            return {}

        entry = next(
            (
                item for item in discarding
                if isinstance(item, dict)
                and int(item.get("playerIndex", item.get("player_index", -1)) or -1) == myi
            ),
            None,
        )
        if not entry:
            return {}

        hand = self._hand(state)
        required = int(entry.get("cardsToDiscard", entry.get("cards_to_discard", 0)) or 0)
        if required <= 0:
            total_cards = sum(hand.values())
            required = total_cards // 2 if total_cards > 7 else 0
        if required <= 0:
            return {}

        keep_priority = self._resource_need_map(hand, state)
        discard_order = sorted(
            RESOURCES,
            key=lambda resource: (
                float(keep_priority.get(resource, 0.0) or 0.0),
                -int(hand.get(resource, 0) or 0),
            ),
        )
        payload = {resource: 0 for resource in RESOURCES}
        remaining = required
        for resource in discard_order:
            if remaining <= 0:
                break
            amount = min(int(hand.get(resource, 0) or 0), remaining)
            if amount > 0:
                payload[resource] = amount
                remaining -= amount
        if remaining:
            return {}
        return {resource: amount for resource, amount in payload.items() if amount > 0}

    def _handle_robber(self, state: Dict[str, Any]) -> None:
        self._start_turn("robber")
        for target in robber_impact_analysis(state):
            hex_key = target.get("hex") or target.get("hex_key") or target.get("hexKey")
            if not hex_key or hex_key == state.get("robber"):
                continue
            victim = self._best_victim_on_hex(state, str(hex_key))
            result = self._execute("move_robber", {
                "hex_key": str(hex_key),
                "steal_from_player_id": victim,
            })
            if self._ok(result):
                self.stats.end_turn()
                return

        for hex_key in (state.get("hexes") or {}).keys():
            if hex_key == state.get("robber"):
                continue
            if self._ok(self._execute("move_robber", {"hex_key": str(hex_key)})):
                break
        self.stats.end_turn()

    def _handle_main(self, state: Dict[str, Any]) -> None:
        self._start_turn("main")

        # Opportunistic dev-card plays before spending. These are conservative
        # because the server enforces card availability and one-card-per-turn.
        self._play_high_value_dev_card(state)

        # Bank/port trades can unlock immediate builds.
        state = self.client.latest_state() or state
        self._bank_trade_to_unlock_build(state)

        self._execute_vp_first_build_plan(self.client.latest_state() or state)

        proposed_trade = self._propose_safe_trade_if_useful(self.client.latest_state() or state)
        if proposed_trade:
            self._wait_for_trade_resolution(timeout_s=TRADE_RESPONSE_TIMEOUT_S)
        self._execute("end_turn", {})
        self.stats.end_turn()

    def _execute_vp_first_build_plan(self, state: Dict[str, Any]) -> None:
        roads_built = 0

        for _ in range(5):
            state = self.client.latest_state() or state
            hand = self._hand(state)

            if self._can_afford(hand, BUILD_COSTS["city"]):
                if self._try_city_upgrade(state):
                    time.sleep(0.1)
                    continue

            state = self.client.latest_state() or state
            hand = self._hand(state)
            if self._can_afford(hand, BUILD_COSTS["settlement"]):
                if self._try_settlement(state):
                    time.sleep(0.1)
                    continue

            state = self.client.latest_state() or state
            hand = self._hand(state)
            myi = state.get("myIndex")
            if (
                roads_built < self._road_build_limit(state)
                and isinstance(myi, int)
                and self._can_place_expansion_road(state, hand)
                and not self._has_connected_settlement_target(state, myi)
            ):
                if self._try_strategic_road(state, myi):
                    roads_built += 1
                    time.sleep(0.1)
                    continue

            state = self.client.latest_state() or state
            hand = self._hand(state)
            if self._should_buy_dev_card(state, hand):
                if self._ok(self._execute("buy_dev_card", {})):
                    time.sleep(0.1)
                    continue

            break

    def _can_place_expansion_road(self, state: Dict[str, Any], hand: Dict[str, int]) -> bool:
        if int(state.get("freeRoads", 0) or 0) > 0:
            return True
        return self._can_afford(hand, BUILD_COSTS["road"])

    @staticmethod
    def _road_build_limit(state: Dict[str, Any]) -> int:
        return 2 if int(state.get("freeRoads", 0) or 0) > 0 else 1

    def _try_city_upgrade(self, state: Dict[str, Any]) -> bool:
        for spot in rank_cities_by_value_gain(state, top_k=10):
            vertex_key = spot.get("vertex") or spot.get("vertex_key") or spot.get("vertexKey")
            if not vertex_key:
                continue
            if self._ok(self._execute("upgrade_to_city", {"vertex_key": str(vertex_key)})):
                return True
        return False

    def _try_settlement(self, state: Dict[str, Any]) -> bool:
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            return False
        for vertex_key in self._rank_connected_settlement_targets(state, myi, top_k=80):
            if self._ok(self._execute("place_settlement", {"vertex_key": vertex_key, "is_setup": False})):
                return True
        return False

    def _rank_connected_settlement_targets(self, state: Dict[str, Any], myi: int, top_k: int = 80) -> List[str]:
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        candidates = [
            vertex_key
            for vertex_key, vertex in vertices.items()
            if isinstance(vertex, dict)
            and not vertex.get("building")
            and self._is_distance_rule_open(state, vertex_key)
            and self._settlement_touches_own_road(state, vertex_key, myi)
        ]
        candidates.sort(key=lambda vertex_key: self._benchmark_settlement_score(state, vertex_key), reverse=True)
        return candidates[:top_k]

    def _has_connected_settlement_target(self, state: Dict[str, Any], myi: int) -> bool:
        return bool(self._rank_connected_settlement_targets(state, myi, top_k=1))

    def _settlement_touches_own_road(self, state: Dict[str, Any], vertex_key: str, myi: int) -> bool:
        return any(self._edge_owner(state, edge_key) == myi for edge_key in _edges_touching_vertex(state, vertex_key))

    def _try_strategic_road(self, state: Dict[str, Any], myi: int) -> bool:
        for edge_key in self._rank_benchmark_legal_roads(state, myi)[:12]:
            if self._ok(self._execute("place_road", {"edge_key": edge_key, "is_setup": False})):
                return True
        return False

    def _best_build_action(self, state: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
        hand = self._hand(state)
        if self._can_afford(hand, BUILD_COSTS["city"]):
            for spot in rank_cities_by_value_gain(state, top_k=10):
                vertex_key = spot.get("vertex") or spot.get("vertex_key") or spot.get("vertexKey")
                if vertex_key:
                    return "upgrade_to_city", {"vertex_key": str(vertex_key)}

        if self._can_afford(hand, BUILD_COSTS["settlement"]):
            for spot in rank_vertices_by_expected_value(state, top_k=25):
                vertex_key = spot.get("vertex") or spot.get("vertex_key") or spot.get("vertexKey")
                if vertex_key:
                    return "place_settlement", {"vertex_key": str(vertex_key), "is_setup": False}

        if self._should_buy_dev_card(state, hand):
            return "buy_dev_card", {}

        if self._can_afford(hand, BUILD_COSTS["road"]):
            myi = state.get("myIndex")
            if isinstance(myi, int):
                edge_key = self._best_road_edge_for_benchmark(state, myi)
                if edge_key:
                    return "place_road", {"edge_key": edge_key, "is_setup": False}
        return None

    def _rank_benchmark_legal_roads(self, state: Dict[str, Any], myi: int) -> List[str]:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        candidates = [
            edge_key
            for edge_key, edge in edges.items()
            if self._edge_is_physically_unoccupied(state, edge_key) and _main_road_connects_to_network(state, edge_key, myi)
        ]
        candidates.sort(
            key=lambda edge_key: self._strategic_road_score(state, edge_key, myi),
            reverse=True,
        )
        return candidates

    def _rank_strategic_road_edges(self, state: Dict[str, Any], myi: int) -> List[str]:
        return [
            edge_key
            for edge_key in self._rank_benchmark_legal_roads(state, myi)
            if self._road_is_growth_oriented(state, edge_key, myi)
        ]

    def _strategic_road_score(self, state: Dict[str, Any], edge_key: str, myi: int) -> float:
        return self._benchmark_road_score(state, edge_key, myi)

    def _road_is_growth_oriented(self, state: Dict[str, Any], edge_key: str, myi: int) -> bool:
        components = self._benchmark_road_components(state, edge_key, myi)
        if components["cycleAvoidance"] <= 0:
            return False
        if not self._road_creates_new_settlement_target(state, edge_key, myi):
            return False
        if components["settlementTargetValue"] >= 0.20:
            return True
        return components["extensionValue"] >= 1.0 and components["futureReachability"] >= 0.66

    def _road_creates_new_settlement_target(self, state: Dict[str, Any], edge_key: str, myi: int) -> bool:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        if len(endpoints) != 2:
            return False
        for vertex_key in endpoints:
            if self._own_road_degree_at_vertex(state, vertex_key, myi) > 0:
                continue
            if self._open_settlement_vertex_value(state, vertex_key) > 0:
                return True
        return False

    def _road_settlement_target_bonus(self, state: Dict[str, Any], edge_key: str, myi: Optional[int] = None) -> float:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        if not endpoints:
            return 0.0
        return max(
            (
                0.0
                if isinstance(myi, int) and self._own_road_degree_at_vertex(state, vertex_key, myi) > 0
                else self._open_settlement_vertex_value(state, vertex_key)
            )
            for vertex_key in endpoints
        )

    def _open_settlement_vertex_value(self, state: Dict[str, Any], vertex_key: str) -> float:
        vertex = self._vertex_record(state, vertex_key)
        if vertex and vertex.get("building"):
            return 0.0
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        for adjacent in _adjacent_vertices(vertex_key):
            adjacent_record = vertices.get(adjacent)
            if isinstance(adjacent_record, dict) and adjacent_record.get("building"):
                return 0.0
        return self._benchmark_forward_vertex_value(state, vertex_key)

    def _road_extension_bonus(self, state: Dict[str, Any], edge_key: str, myi: int) -> float:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        if not endpoints:
            return 0.0
        endpoint_degrees = [self._own_road_degree_at_vertex(state, vertex_key, myi) for vertex_key in endpoints]
        touches_building = any((self._vertex_record(state, vertex_key) or {}).get("owner") == myi for vertex_key in endpoints)
        if 0 in endpoint_degrees and (touches_building or max(endpoint_degrees) > 0):
            return 1.0
        if min(endpoint_degrees) == 0:
            return 0.6
        return 0.0

    def _road_cycle_penalty(self, state: Dict[str, Any], edge_key: str, myi: int) -> float:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        if len(endpoints) != 2:
            return 0.0
        degrees = [self._own_road_degree_at_vertex(state, vertex_key, myi) for vertex_key in endpoints]
        own_buildings = [
            (self._vertex_record(state, vertex_key) or {}).get("owner") == myi
            for vertex_key in endpoints
        ]
        if all(degree > 0 or owns for degree, owns in zip(degrees, own_buildings)):
            return 1.0
        return 0.0

    def _own_road_degree_at_vertex(self, state: Dict[str, Any], vertex_key: str, myi: int) -> int:
        return sum(1 for edge_key in _edges_touching_vertex(state, vertex_key) if self._edge_owner(state, edge_key) == myi)

    def _best_road_edge_for_benchmark(self, state: Dict[str, Any], myi: int) -> Optional[str]:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        candidates: List[str] = []
        for edge_key, edge in edges.items():
            if not _edge_is_unoccupied(edge):
                continue
            if not self._edge_is_physically_unoccupied(state, edge_key):
                continue
            if not _main_road_connects_to_network(state, edge_key, myi):
                continue
            candidates.append(edge_key)

        if not candidates:
            fallback = ranked_main_road_edges(state, myi, top_k=1)
            if fallback:
                return str(fallback[0].get("edge") or "")
            return None

        return max(
            candidates,
            key=lambda edge_key: self._benchmark_road_score(state, edge_key, myi),
        )

    def _benchmark_road_score(self, state: Dict[str, Any], edge_key: str, myi: int) -> float:
        placement = self._benchmark_road_components(state, edge_key, myi)
        return (
            (placement["futureReachability"] * 0.25)
            + (placement["reachableIntersectionValue"] * 0.20)
            + (placement["blockingValue"] * 0.10)
            + (placement["settlementTargetValue"] * 0.25)
            + (placement["extensionValue"] * 0.15)
            + (placement["cycleAvoidance"] * 0.05)
        )

    def _benchmark_road_components(self, state: Dict[str, Any], edge_key: str, myi: int) -> Dict[str, float]:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        if not endpoints:
            return {
                "futureReachability": 0.0,
                "reachableIntersectionValue": 0.0,
                "blockingValue": 0.0,
                "settlementTargetValue": 0.0,
                "extensionValue": 0.0,
                "cycleAvoidance": 1.0,
            }

        forward_candidates = [
            vertex_key
            for vertex_key in endpoints
            if self._own_road_degree_at_vertex(state, vertex_key, myi) == 0
            and (self._vertex_record(state, vertex_key) or {}).get("owner") != myi
        ] or endpoints
        forward_vertex = max(forward_candidates, key=lambda vertex_key: self._benchmark_forward_vertex_value(state, vertex_key))
        next_edges = [candidate for candidate in _edges_touching_vertex(state, forward_vertex) if candidate != edge_key]
        already_has_settlement_target = self._has_connected_settlement_target(state, myi)
        return {
            "futureReachability": min(1.0, len(next_edges) / 3.0),
            "reachableIntersectionValue": self._benchmark_forward_vertex_value(state, forward_vertex),
            "blockingValue": self._road_blocking_value(state, edge_key, myi),
            "settlementTargetValue": 0.0 if already_has_settlement_target else self._road_settlement_target_bonus(state, edge_key, myi),
            "extensionValue": 0.0 if already_has_settlement_target else self._road_extension_bonus(state, edge_key, myi),
            "cycleAvoidance": 1.0 - self._road_cycle_penalty(state, edge_key, myi),
        }

    def _benchmark_forward_vertex_value(self, state: Dict[str, Any], vertex_key: str) -> float:
        vertex_score = self._benchmark_vertex_score(state, vertex_key)
        return (
            (vertex_score["resourceProductionExpectancy"] * 0.50)
            + (vertex_score["resourceDiversity"] * 0.30)
            + (vertex_score["portSynergy"] * 0.20)
        )

    def _benchmark_vertex_score(self, state: Dict[str, Any], vertex_key: str) -> Dict[str, float]:
        adjacent_hexes = _adjacent_hexes_for_vertex(state, vertex_key)
        production_weight = sum(_pip_value(hex_tile.get("number") or hex_tile.get("token")) for hex_tile in adjacent_hexes)
        resource_types = {
            str(hex_tile.get("resource"))
            for hex_tile in adjacent_hexes
            if hex_tile.get("resource")
        }
        return {
            "resourceProductionExpectancy": min(1.0, production_weight / 15.0),
            "resourceDiversity": min(1.0, len(resource_types) / 3.0),
            "portSynergy": self._port_synergy(state, vertex_key),
        }

    @staticmethod
    def _port_synergy(state: Dict[str, Any], vertex_key: str) -> float:
        ports = state.get("ports") if isinstance(state.get("ports"), list) else []
        for port in ports:
            if not isinstance(port, dict):
                continue
            vertices = port.get("vertices") if isinstance(port.get("vertices"), list) else []
            if vertex_key in [str(vertex) for vertex in vertices]:
                return 1.0 if port.get("resource") else 0.7
        return 0.0

    def _road_blocking_value(self, state: Dict[str, Any], edge_key: str, myi: int) -> float:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        best = 0.0
        for vertex_key in endpoints:
            vertex = self._vertex_record(state, vertex_key)
            if vertex and vertex.get("owner") == myi:
                continue
            adjacent_edges = _edges_touching_vertex(state, vertex_key)
            remaining_open = sum(
                1
                for candidate in adjacent_edges
                if candidate != edge_key and _edge_is_unoccupied((state.get("edges") or {}).get(candidate))
            )
            branch_scarcity = max(0.0, min(1.0, (2 - remaining_open) / 2.0))
            open_value = 1.0 if not vertex or not vertex.get("building") else 0.35
            for opponent_index, player in enumerate(state.get("players") or []):
                if opponent_index == myi or not isinstance(player, dict):
                    continue
                if vertex and vertex.get("owner") not in (None, opponent_index):
                    continue
                opponent_edges = sum(
                    1
                    for candidate in adjacent_edges
                    if self._edge_owner(state, candidate) == opponent_index
                )
                opponent_presence = opponent_edges + (1 if vertex and vertex.get("owner") == opponent_index else 0)
                if opponent_presence <= 0:
                    continue
                vertex_opportunity = 0.8 if vertex and vertex.get("owner") == opponent_index else open_value
                road_pressure = max(
                    0.0,
                    min(
                        1.0,
                        (float(player.get("roadLength", 0) or 0) + (2.0 if player.get("hasLongestRoad") else 0.0)) / 8.0,
                    ),
                )
                presence_strength = max(0.0, min(1.0, opponent_presence / 2.0))
                best = max(
                    best,
                    max(
                        0.0,
                        min(
                            1.0,
                            (branch_scarcity * 0.5)
                            + (vertex_opportunity * 0.3)
                            + (presence_strength * 0.1)
                            + (road_pressure * 0.1),
                        ),
                    ),
                )
        return best

    @staticmethod
    def _vertex_record(state: Dict[str, Any], vertex_key: str) -> Optional[Dict[str, Any]]:
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        vertex = vertices.get(vertex_key)
        return vertex if isinstance(vertex, dict) else None

    @staticmethod
    def _edge_owner(state: Dict[str, Any], edge_key: str) -> Optional[int]:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        edge = edges.get(edge_key)
        if isinstance(edge, dict):
            owner = edge.get("owner")
            if isinstance(owner, int):
                return owner
            if edge.get("road") and isinstance(edge.get("road"), int):
                return edge.get("road")

        target_vertices = BenchmarkCalibrationAgent._edge_endpoint_set(edge_key)
        if not target_vertices:
            return None
        for candidate_key, candidate_edge in edges.items():
            if candidate_key == edge_key:
                continue
            if BenchmarkCalibrationAgent._edge_endpoint_set(candidate_key) != target_vertices:
                continue
            if not isinstance(candidate_edge, dict):
                continue
            owner = candidate_edge.get("owner")
            if isinstance(owner, int):
                return owner
            if candidate_edge.get("road") and isinstance(candidate_edge.get("road"), int):
                return candidate_edge.get("road")
        return None

    @staticmethod
    def _edge_endpoint_set(edge_key: str) -> Optional[frozenset]:
        parsed = _parse_edge_key(edge_key)
        if not parsed:
            return None
        return frozenset(_edge_vertices(*parsed))

    @staticmethod
    def _edge_is_physically_unoccupied(state: Dict[str, Any], edge_key: str) -> bool:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        edge = edges.get(edge_key)
        if not isinstance(edge, dict):
            return False
        if not _edge_is_unoccupied(edge):
            return False
        return BenchmarkCalibrationAgent._edge_owner(state, edge_key) is None

    def _bank_trade_to_unlock_build(self, state: Dict[str, Any]) -> bool:
        hand = self._hand(state)
        ratios = state.get("tradeRatios") if isinstance(state.get("tradeRatios"), dict) else {}
        best: Optional[Dict[str, Any]] = None

        for give in RESOURCES:
            ratio = int(ratios.get(give, 4) or 4)
            if ratio <= 0:
                ratio = 4
            if int(hand.get(give, 0) or 0) < ratio:
                continue
            for target in RESOURCES:
                if target == give:
                    continue
                after = dict(hand)
                after[give] = int(after.get(give, 0) or 0) - ratio
                after[target] = int(after.get(target, 0) or 0) + 1
                score = self._bank_trade_growth_score(state, hand, after, give, target, ratio)
                candidate = {
                    "give_resource": give,
                    "give_amount": ratio,
                    "get_resource": target,
                    "score": score,
                }
                if best is None or candidate["score"] > best["score"]:
                    best = candidate

        if not best or best["score"] < 0.45:
            if best:
                print(f"  [benchmark] skip bank trade; best score={best['score']:.2f} {best}")
            return False

        result = self._execute("bank_trade", {
            "give_resource": best["give_resource"],
            "give_amount": best["give_amount"],
            "get_resource": best["get_resource"],
        })
        return self._ok(result)

    def _bank_trade_growth_score(
        self,
        state: Dict[str, Any],
        before: Dict[str, int],
        after: Dict[str, int],
        give_resource: str,
        get_resource: str,
        ratio: int,
    ) -> float:
        before_best = self._best_affordable_plan_score(state, before)
        after_best = self._best_affordable_plan_score(state, after)
        unlock_bonus = max(0.0, after_best - before_best)
        need = self._resource_need_map(before, state)
        surplus = self._resource_surplus_map(before)
        need_gain = float(need.get(get_resource, 0.0) or 0.0)
        give_cost = float(surplus.get(give_resource, 0.0) or 0.0) * min(1.0, ratio / 4.0)
        settlement_missing = self._missing_for_cost(before, BUILD_COSTS["settlement"])
        settlement_bonus = 0.35 if get_resource in settlement_missing and self._has_settlement_target(state) else 0.0
        return max(0.0, min(1.5, unlock_bonus + need_gain + settlement_bonus - (0.35 * give_cost)))

    def _best_affordable_plan_score(self, state: Dict[str, Any], hand: Dict[str, int]) -> float:
        scores = []
        if self._can_afford(hand, BUILD_COSTS["city"]):
            scores.append(1.0)
        if self._can_afford(hand, BUILD_COSTS["settlement"]) and self._has_settlement_target(state):
            scores.append(0.95)
        if self._can_afford(hand, BUILD_COSTS["dev_card"]):
            scores.append(0.65)
        if self._can_afford(hand, BUILD_COSTS["road"]):
            scores.append(0.35)
        return max(scores, default=0.0)

    def _play_high_value_dev_card(self, state: Dict[str, Any]) -> None:
        dev_cards = self._dev_cards(state)
        myi = state.get("myIndex")
        if (
            int(dev_cards.get("roadBuilding", dev_cards.get("road_building", 0)) or 0) > 0
            and isinstance(myi, int)
            and not self._has_connected_settlement_target(state, myi)
            and self._rank_strategic_road_edges(state, myi)
        ):
            self._execute("play_dev_card", {"card_type": "roadBuilding"})
            return
        if int(dev_cards.get("monopoly", 0) or 0) > 0:
            resource = self._best_monopoly_resource(state)
            self._execute("play_dev_card", {"card_type": "monopoly", "params": {"resource": resource}})
            return
        if int(dev_cards.get("yearOfPlenty", dev_cards.get("year_of_plenty", 0)) or 0) > 0:
            picks = self._year_of_plenty_picks(state)
            if self._ok(self._execute("play_dev_card", {"card_type": "yearOfPlenty"})):
                for resource in picks:
                    self._execute("year_of_plenty_pick", {"resource": resource})
            return
        if int(dev_cards.get("knight", 0) or 0) > 0 and self._should_play_knight(state):
            self._execute("play_dev_card", {"card_type": "knight"})

    def _propose_safe_trade_if_useful(self, state: Dict[str, Any]) -> bool:
        trade = self._best_player_trade(state)
        if not trade:
            return False
        result = self._execute("propose_trade", {
            "offer": trade["offer"],
            "request": trade["request"],
            "target_player_id": trade["target_player_id"],
        })
        return self._ok(result)

    def _best_player_trade(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        hand = self._hand(state)
        if not hand:
            return None

        players = state.get("players") if isinstance(state.get("players"), list) else []
        myi = state.get("myIndex")
        need = self._resource_need_map(hand, state)
        surplus = self._resource_surplus_map(hand)
        leader_index = self._leader_index(state)

        best: Optional[Dict[str, Any]] = None
        for partner_index, partner in enumerate(players):
            if partner_index == myi or not isinstance(partner, dict):
                continue
            raw_partner_resources = partner.get("resources")
            partner_resources = raw_partner_resources if isinstance(raw_partner_resources, dict) else {}
            partner_total_cards = int(raw_partner_resources or 0) if isinstance(raw_partner_resources, (int, float)) else sum(
                int(amount or 0) for amount in partner_resources.values()
            )
            partner_id = partner.get("id")
            if not partner_id:
                continue
            for give_resource in RESOURCES:
                if int(hand.get(give_resource, 0) or 0) < 1:
                    continue
                for request_resource in RESOURCES:
                    if request_resource == give_resource:
                        continue
                    if partner_resources and int(partner_resources.get(request_resource, 0) or 0) < 1:
                        continue
                    if not partner_resources and partner_total_cards < 1:
                        continue
                    score_parts = self._trade_score_parts(
                        state,
                        hand,
                        {give_resource: 1},
                        {request_resource: 1},
                        partner_index == leader_index,
                    )
                    score = self._trade_quality_score(score_parts)
                    candidate = {
                        "offer": {give_resource: 1},
                        "request": {request_resource: 1},
                        "target_player_id": str(partner_id),
                        "score": score,
                        **score_parts,
                    }
                    if best is None or candidate["score"] > best["score"]:
                        best = candidate

        if best is None or best["score"] < 0.7:
            return None
        return best

    def _trade_score_parts(
        self,
        state: Dict[str, Any],
        hand: Dict[str, int],
        outgoing: Dict[str, int],
        incoming: Dict[str, int],
        helps_leader: bool,
    ) -> Dict[str, float]:
        need = self._resource_need_map(hand, state)
        surplus = self._resource_surplus_map(hand)
        after = dict(hand)
        for resource, amount in incoming.items():
            after[resource] = int(after.get(resource, 0) or 0) + int(amount or 0)
        for resource, amount in outgoing.items():
            after[resource] = max(0, int(after.get(resource, 0) or 0) - int(amount or 0))

        give_cost = sum(float(surplus.get(r, 0.0) or 0.0) * int(a or 0) for r, a in outgoing.items())
        request_value = sum(float(need.get(r, 0.0) or 0.0) * int(a or 0) for r, a in incoming.items())
        plan_gain = max(0.0, self._best_affordable_plan_score(state, after) - self._best_affordable_plan_score(state, hand))
        settlement_gain = self._settlement_progress_gain(hand, after) if self._has_settlement_target(state) else 0.0
        return {
            "self_gain": max(0.0, min(1.0, (request_value * 0.45) + (plan_gain * 0.35) + (settlement_gain * 0.2) - (give_cost * 0.35))),
            "leader_penalty": 1.0 if helps_leader else 0.0,
            "fairness_penalty": max(0.0, min(1.0, give_cost - request_value)),
            "request_value": max(0.0, min(1.0, request_value)),
            "give_cost": max(0.0, min(1.0, give_cost)),
            "plan_gain": max(0.0, min(1.0, plan_gain)),
            "settlement_gain": max(0.0, min(1.0, settlement_gain)),
        }

    @staticmethod
    def _trade_quality_score(score_parts: Dict[str, float]) -> float:
        return max(0.0, min(1.0, (
            0.35
            + (0.45 * float(score_parts.get("self_gain", 0.0) or 0.0))
            + (0.15 * (1.0 - float(score_parts.get("leader_penalty", 0.0) or 0.0)))
            + (0.05 * (1.0 - float(score_parts.get("fairness_penalty", 0.0) or 0.0)))
        )))

    def _resource_need_map(self, hand: Dict[str, int], state: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        raw = {resource: 0.0 for resource in RESOURCES}
        weighted_costs = (
            (BUILD_COSTS["city"], 1.1),
            (BUILD_COSTS["settlement"], 1.5 if state is None or self._has_settlement_target(state) else 0.7),
            (BUILD_COSTS["dev_card"], 0.75),
            (BUILD_COSTS["road"], 0.25),
        )
        for cost, priority in weighted_costs:
            missing = {
                resource: max(0, int(cost.get(resource, 0) or 0) - int(hand.get(resource, 0) or 0))
                for resource in RESOURCES
            }
            total_missing = sum(missing.values())
            if total_missing <= 0:
                continue
            plan_weight = priority / (1.0 + (0.75 * total_missing))
            for resource, amount in missing.items():
                raw[resource] += plan_weight * (amount / max(1, total_missing))
        return self._normalize_resource_scores(raw)

    def _missing_for_cost(self, hand: Dict[str, int], cost: Dict[str, int]) -> List[str]:
        missing: List[str] = []
        for resource, amount in cost.items():
            missing.extend([resource] * max(0, int(amount or 0) - int(hand.get(resource, 0) or 0)))
        return missing

    def _settlement_progress_gain(self, before: Dict[str, int], after: Dict[str, int]) -> float:
        before_missing = len(self._missing_for_cost(before, BUILD_COSTS["settlement"]))
        after_missing = len(self._missing_for_cost(after, BUILD_COSTS["settlement"]))
        return max(0.0, min(1.0, (before_missing - after_missing) / 4.0))

    def _has_settlement_target(self, state: Dict[str, Any]) -> bool:
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            return False
        return self._has_connected_settlement_target(state, myi)

    def _resource_surplus_map(self, hand: Dict[str, int]) -> Dict[str, float]:
        reserved = {resource: 0 for resource in RESOURCES}
        for cost in (BUILD_COSTS["city"], BUILD_COSTS["settlement"], BUILD_COSTS["dev_card"], BUILD_COSTS["road"]):
            if self._can_afford(hand, cost):
                for resource, amount in cost.items():
                    reserved[resource] = max(reserved[resource], int(amount or 0))
        free = {
            resource: max(0, int(hand.get(resource, 0) or 0) - reserved.get(resource, 0))
            for resource in RESOURCES
        }
        return self._normalize_resource_scores(free)

    @staticmethod
    def _normalize_resource_scores(values: Dict[str, float]) -> Dict[str, float]:
        max_value = max([float(values.get(resource, 0.0) or 0.0) for resource in RESOURCES] + [0.0])
        if max_value <= 0:
            return {resource: 0.0 for resource in RESOURCES}
        return {
            resource: max(0.0, min(1.0, float(values.get(resource, 0.0) or 0.0) / max_value))
            for resource in RESOURCES
        }

    def _wait_for_trade_resolution(self, timeout_s: float = TRADE_RESPONSE_TIMEOUT_S) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            result = self._execute("get_trade_offer_status", {})
            if not self._ok(result) or not result.get("has_offer"):
                return
            if not result.get("offer_from_me"):
                state = self.client.latest_state() or {}
                self._respond_to_incoming_trade(state)
                return
            time.sleep(1.0)
        self._execute("cancel_trade", {})

    def _respond_to_incoming_trade(self, state: Dict[str, Any]) -> None:
        offer = self._incoming_offer(state)
        if not offer:
            self._last_offer_signature = None
            return
        signature = json.dumps(offer, sort_keys=True, default=str)
        if signature == self._last_offer_signature:
            return
        accept = self._should_accept_offer(state, offer)
        if self.registry is None:
            self.registry = build_tool_registry(self.client, self.processor)
        result = self._execute("respond_to_trade", {"accept": accept})
        if self._ok(result):
            self._last_offer_signature = signature

    def _should_accept_offer(self, state: Dict[str, Any], offer: Dict[str, Any]) -> bool:
        hand = self._hand(state)
        request = offer.get("request") if isinstance(offer.get("request"), dict) else {}
        their_offer = offer.get("offer") if isinstance(offer.get("offer"), dict) else {}
        for resource, amount in request.items():
            if int(hand.get(resource, 0) or 0) < int(amount or 0):
                return False

        after = dict(hand)
        for resource, amount in their_offer.items():
            after[resource] = int(after.get(resource, 0) or 0) + int(amount or 0)
        for resource, amount in request.items():
            after[resource] = int(after.get(resource, 0) or 0) - int(amount or 0)

        proposer = offer.get("from")
        score_parts = self._trade_score_parts(
            state,
            hand,
            {str(resource): int(amount or 0) for resource, amount in request.items()},
            {str(resource): int(amount or 0) for resource, amount in their_offer.items()},
            self._is_leader(state, proposer),
        )
        score = self._trade_quality_score(score_parts)
        print(f"  [benchmark] incoming trade score={score:.2f} parts={score_parts} accept={score >= 0.7}")
        return score >= 0.7

    def _incoming_offer(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        myi = state.get("myIndex")
        for key in ("activeTradeOffer", "tradeOffer", "currentTradeOffer"):
            raw = state.get(key)
            if not isinstance(raw, dict):
                continue
            if raw.get("from") == myi:
                return None
            if raw.get("to") in (None, myi) or raw.get("canRespond") or raw.get("can_i_respond"):
                return {
                    "from": raw.get("from"),
                    "to": raw.get("to"),
                    "offer": raw.get("offer") if isinstance(raw.get("offer"), dict) else {},
                    "request": raw.get("request") if isinstance(raw.get("request"), dict) else {},
                }
        return None

    def _best_victim_on_hex(self, state: Dict[str, Any], hex_key: str) -> Optional[str]:
        players = state.get("players") if isinstance(state.get("players"), list) else []
        myi = state.get("myIndex")
        threats = {str(t.get("player_index", t.get("playerIndex", t.get("index", "")))): t for t in opponent_threat_assessment(state)}
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        best: Optional[Tuple[float, str]] = None
        for vertex in vertices.values():
            if not isinstance(vertex, dict):
                continue
            if not self._vertex_touches_hex(vertex, hex_key):
                continue
            owner = vertex.get("owner")
            if owner is None or owner == myi:
                continue
            player = players[owner] if isinstance(owner, int) and 0 <= owner < len(players) else {}
            player_id = player.get("id") if isinstance(player, dict) else str(owner)
            threat = threats.get(str(owner), {})
            score = float(threat.get("threat_score", 0.0) or 0.0)
            if best is None or score > best[0]:
                best = (score, str(player_id))
        return best[1] if best else None

    @staticmethod
    def _vertex_touches_hex(vertex: Dict[str, Any], hex_key: str) -> bool:
        for key in ("adjacentHexes", "hexes"):
            values = vertex.get(key)
            if isinstance(values, list) and hex_key in [str(v) for v in values]:
                return True
        return False

    def _best_monopoly_resource(self, state: Dict[str, Any]) -> str:
        totals = {r: 0 for r in RESOURCES}
        myi = state.get("myIndex")
        for index, player in enumerate(state.get("players") or []):
            if index == myi or not isinstance(player, dict):
                continue
            resources = player.get("resources") if isinstance(player.get("resources"), dict) else {}
            for resource in RESOURCES:
                totals[resource] += int(resources.get(resource, 0) or 0)
        return max(totals, key=lambda r: (totals[r], r))

    def _year_of_plenty_picks(self, state: Dict[str, Any]) -> List[str]:
        hand = self._hand(state)
        for cost in (BUILD_COSTS["city"], BUILD_COSTS["settlement"], BUILD_COSTS["dev_card"], BUILD_COSTS["road"]):
            missing: List[str] = []
            for resource, needed in cost.items():
                missing.extend([resource] * max(0, int(needed) - int(hand.get(resource, 0) or 0)))
            if missing:
                return (missing + ["ore", "grain"])[:2]
        return ["ore", "grain"]

    def _should_play_knight(self, state: Dict[str, Any]) -> bool:
        me = self._me(state)
        my_knights = int(me.get("knightsPlayed", me.get("knights_played", 0)) or 0)
        opponents = [
            player for index, player in enumerate(state.get("players") or [])
            if index != state.get("myIndex") and isinstance(player, dict)
        ]
        best_opponent_knights = max(
            (int(player.get("knightsPlayed", player.get("knights_played", 0)) or 0) for player in opponents),
            default=0,
        )
        if my_knights + 1 >= 3 and my_knights + 1 > best_opponent_knights:
            return True
        if state.get("robber"):
            income = expected_resource_income(state)
            if sum(income.values()) < 0.25:
                return True
        threats = opponent_threat_assessment(state)
        return bool(threats and float(threats[0].get("victory_points", threats[0].get("vp", 0)) or 0) >= 7)

    def _should_buy_dev_card(self, state: Dict[str, Any], hand: Dict[str, int]) -> bool:
        if not self._can_afford(hand, BUILD_COSTS["dev_card"]):
            return False
        me = self._me(state)
        visible_vp = int(me.get("victoryPoints", me.get("victory_points", 0)) or 0)
        hidden_vp = int(me.get("hiddenVictoryPoints", me.get("hidden_victory_points", 0)) or 0)
        vp = visible_vp + hidden_vp
        held_dev_cards = sum(self._dev_cards(state).values())
        myi = state.get("myIndex")

        if held_dev_cards >= 3:
            return False
        if isinstance(myi, int) and self._rank_strategic_road_edges(state, myi):
            return False
        if self._can_afford(hand, BUILD_COSTS["settlement"]) or self._can_afford(hand, BUILD_COSTS["city"]):
            return False

        my_knights = int(me.get("knightsPlayed", me.get("knights_played", 0)) or 0)
        opponents = [
            player for index, player in enumerate(state.get("players") or [])
            if index != myi and isinstance(player, dict)
        ]
        best_opponent_knights = max(
            (int(player.get("knightsPlayed", player.get("knights_played", 0)) or 0) for player in opponents),
            default=0,
        )
        close_to_largest_army = my_knights < 3 and my_knights + held_dev_cards + 1 >= max(3, best_opponent_knights + 1)
        if close_to_largest_army:
            return True

        return vp >= 8 and held_dev_cards == 0

    def _is_leader(self, state: Dict[str, Any], player_index: Any) -> bool:
        if not isinstance(player_index, int):
            return False
        players = state.get("players") if isinstance(state.get("players"), list) else []
        if not (0 <= player_index < len(players)):
            return False
        max_vp = max((int(p.get("victoryPoints", p.get("victory_points", 0)) or 0) for p in players if isinstance(p, dict)), default=0)
        player = players[player_index]
        return int(player.get("victoryPoints", player.get("victory_points", 0)) or 0) >= max_vp and max_vp >= 6

    @staticmethod
    def _leader_index(state: Dict[str, Any]) -> Optional[int]:
        players = state.get("players") if isinstance(state.get("players"), list) else []
        best_index: Optional[int] = None
        best_score = -1
        for index, player in enumerate(players):
            if not isinstance(player, dict):
                continue
            score = int(player.get("victoryPoints", player.get("victory_points", 0)) or 0)
            if player.get("hasLongestRoad") or player.get("longestRoad"):
                score += 2
            if player.get("hasLargestArmy") or player.get("largestArmy"):
                score += 2
            if score > best_score:
                best_score = score
                best_index = index
        return best_index

    def _execute(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if self.registry is None:
            raise RuntimeError("Tool registry has not been initialized")
        result = self.registry.execute(name, args)
        success = self._ok(result)
        self.stats.record_tool_call(name, success=success)
        print(f"  [benchmark] {name}({json.dumps(args, default=str)}) -> {json.dumps(result, default=str)[:160]}")
        return result

    def _start_turn(self, phase: str) -> None:
        self._turn_counter += 1
        self.stats.start_turn(self._turn_counter, phase=phase)

    @staticmethod
    def _ok(result: Any) -> bool:
        return isinstance(result, dict) and bool(result.get("success", True))

    @staticmethod
    def _registry_args(payload: Dict[str, Any]) -> Dict[str, Any]:
        mapping = {
            "vertexKey": "vertex_key",
            "edgeKey": "edge_key",
            "isSetup": "is_setup",
            "lastSettlement": "last_settlement",
            "hexKey": "hex_key",
            "stealFromPlayerId": "steal_from_player_id",
        }
        return {mapping.get(key, key): value for key, value in payload.items()}

    @staticmethod
    def _can_afford(hand: Dict[str, int], cost: Dict[str, int]) -> bool:
        return all(int(hand.get(resource, 0) or 0) >= amount for resource, amount in cost.items())

    def _hand(self, state: Dict[str, Any]) -> Dict[str, int]:
        resources = self._me(state).get("resources")
        if not isinstance(resources, dict):
            return {}
        return {resource: int(resources.get(resource, 0) or 0) for resource in RESOURCES}

    def _dev_cards(self, state: Dict[str, Any]) -> Dict[str, int]:
        cards = self._me(state).get("devCards") or self._me(state).get("developmentCards")
        if isinstance(cards, dict):
            return {str(card): int(count or 0) for card, count in cards.items()}
        if isinstance(cards, list):
            counts: Dict[str, int] = {}
            for card in cards:
                key = str(card)
                counts[key] = counts.get(key, 0) + 1
            return counts
        return {}

    @staticmethod
    def _me(state: Dict[str, Any]) -> Dict[str, Any]:
        players = state.get("players")
        myi = state.get("myIndex")
        if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players):
            player = players[myi]
            return player if isinstance(player, dict) else {}
        return {}
