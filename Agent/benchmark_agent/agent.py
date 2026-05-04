"""
Deterministic benchmark-calibration agent for live Catan games.

The goal is not to be clever or expressive. The goal is to be consistent,
fast, and aligned with the benchmark heuristics so it can act as a fairness
probe for the benchmark itself.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from Agent.benchmark_agent.decision import BenchmarkDecisionEngine
from Agent.tools.game_tools import (
    RESOURCES,
    _adjacent_hexes_for_vertex,
    _adjacent_vertices,
    _edge_is_unoccupied,
    _edge_vertices,
    _edges_touching_vertex,
    _main_road_connects_to_network,
    _parse_vertex_key,
    _parse_edge_key,
    _pip_value,
    is_my_turn,
    ranked_main_road_edges,
)
from Agent.tools.registry import ToolRegistry, build_tool_registry
from Agent.risk_agent.probabilities import (
    expected_resource_income,
    opponent_threat_assessment,
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
        reconnect_player_id: Optional[str] = None,
    ) -> None:
        self.client = CatanSocketClient(server_url)
        self.game_code = game_code
        self.player_name = player_name
        self.reconnect_player_id = reconnect_player_id
        self.processor = GameStateProcessor()
        self.registry: Optional[ToolRegistry] = None
        self.stats = AgentStatsTracker(agent_name=player_name)
        self.decisions = BenchmarkDecisionEngine()

        self._turn_counter = 0
        self._last_setup_settlement: Optional[str] = None
        self._last_offer_signature: Optional[str] = None
        self._debugged_first_setup_road = False

    def solve_benchmark_task(self, task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.decisions.solve_task(task_id, payload)

    def run(self) -> None:
        self.client.connect()
        if self.game_code and self.reconnect_player_id:
            self.client.reconnect_game(self.game_code, self.reconnect_player_id)
        elif self.game_code:
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
                    if self._needs_to_discard(state):
                        self._handle_discard(state)
                    else:
                        time.sleep(0.25)
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

        myi = state.get("myIndex")
        setup_roads = self._setup_road_attempt_order(
            state,
            self._last_setup_settlement,
            myi if isinstance(myi, int) else None,
            top_k=80,
        )
        if not self._debugged_first_setup_road:
            preview = [
                {
                    "edge": edge_key,
                    "score": round(self._setup_road_score(state, edge_key, self._last_setup_settlement), 4),
                }
                for edge_key in setup_roads[:8]
            ]
            print(
                f"  [debug:first-setup-road] settlement={self._last_setup_settlement} "
                f"candidate_count={len(setup_roads)} top={json.dumps(preview, default=str)}"
            )
        for edge_key in setup_roads:
            attempt_started = time.time()
            result = self._execute("place_road", {
                "edge_key": edge_key,
                "is_setup": True,
                "last_settlement": self._last_setup_settlement,
            })
            if not self._debugged_first_setup_road:
                elapsed = round(time.time() - attempt_started, 3)
                print(
                    f"  [debug:first-setup-road] tried edge={edge_key} elapsed_s={elapsed} "
                    f"success={self._ok(result)} error={result.get('error') if isinstance(result, dict) else result}"
                )
            if self._ok(result):
                print(f"  [setup] road {edge_key}")
                self._execute("advance_setup", {})
                self._last_setup_settlement = None
                self._debugged_first_setup_road = True
                self.stats.end_turn()
                return
        self.stats.end_turn()

    def _rank_benchmark_setup_settlements(self, state: Dict[str, Any], top_k: int = 160) -> List[str]:
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        candidates = [
            vertex_key
            for vertex_key, vertex in vertices.items()
            if isinstance(vertex, dict) and self._is_setup_settlement_legal(state, vertex_key)
        ]
        candidates.sort(key=lambda vertex_key: self._benchmark_settlement_score(state, vertex_key), reverse=True)
        return candidates[:top_k]

    def _setup_road_attempt_order(
        self,
        state: Dict[str, Any],
        last_settlement: str,
        myi: Optional[int],
        top_k: int = 80,
    ) -> List[str]:
        candidates = self._collect_setup_road_attempt_candidates(state, last_settlement)
        candidates.sort(
            key=lambda edge_key: self._setup_road_score(state, edge_key, last_settlement),
            reverse=True,
        )
        return candidates[:top_k]

    def _collect_setup_road_attempt_candidates(self, state: Dict[str, Any], last_settlement: str) -> List[str]:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        candidates: List[str] = []
        seen_positions = set()

        preferred = [self._resolve_existing_edge_key(state, edge_key) or edge_key for edge_key in self._setup_vertex_edges(last_settlement)]
        nearby = [
            str(edge_key)
            for edge_key in edges.keys()
            if self._setup_road_touches_settlement(str(edge_key), last_settlement)
        ]

        for edge_key in preferred + nearby:
            edge_key = str(edge_key)
            endpoints = self._edge_endpoint_positions(edge_key)
            if endpoints is None:
                continue
            if not self._setup_road_touches_settlement(edge_key, last_settlement):
                continue
            if endpoints in seen_positions:
                continue
            seen_positions.add(endpoints)
            candidates.append(edge_key)
        return candidates

    @staticmethod
    def _setup_vertex_edges(vertex_key: str) -> List[str]:
        candidates: List[str] = []
        seen = set()
        for q, r, direction in BenchmarkCalibrationAgent._equivalent_vertex_coords(vertex_key):
            if direction == 0:
                edges = [f"e_{q}_{r}_5", f"e_{q}_{r}_0", f"e_{q}_{r - 1}_1"]
            elif direction == 1:
                edges = [f"e_{q}_{r}_0", f"e_{q}_{r}_1", f"e_{q + 1}_{r - 1}_2"]
            elif direction == 2:
                edges = [f"e_{q}_{r}_1", f"e_{q}_{r}_2", f"e_{q + 1}_{r}_3"]
            elif direction == 3:
                edges = [f"e_{q}_{r}_2", f"e_{q}_{r}_3", f"e_{q}_{r + 1}_4"]
            elif direction == 4:
                edges = [f"e_{q}_{r}_3", f"e_{q}_{r}_4", f"e_{q - 1}_{r + 1}_5"]
            elif direction == 5:
                edges = [f"e_{q}_{r}_4", f"e_{q}_{r}_5", f"e_{q - 1}_{r}_0"]
            else:
                edges = []
            for edge_key in edges:
                if edge_key not in seen:
                    seen.add(edge_key)
                    candidates.append(edge_key)
        return candidates

    @staticmethod
    def _equivalent_vertex_coords(vertex_key: str) -> List[Tuple[int, int, int]]:
        parsed = _parse_vertex_key(vertex_key)
        if not parsed:
            return []
        q, r, direction = parsed
        equivalents = [(q, r, direction)]
        if direction == 0:
            equivalents.extend([(q, r - 1, 2), (q + 1, r - 1, 4)])
        elif direction == 1:
            equivalents.extend([(q + 1, r - 1, 3), (q + 1, r, 5)])
        elif direction == 2:
            equivalents.extend([(q + 1, r, 4), (q, r + 1, 0)])
        elif direction == 3:
            equivalents.extend([(q, r + 1, 5), (q - 1, r + 1, 1)])
        elif direction == 4:
            equivalents.extend([(q - 1, r + 1, 0), (q - 1, r, 2)])
        elif direction == 5:
            equivalents.extend([(q - 1, r, 1), (q, r - 1, 3)])
        return equivalents

    def _setup_road_score(self, state: Dict[str, Any], edge_key: str, last_settlement: str) -> float:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        if not endpoints:
            return 0.0
        forward_vertex = next(
            (
                vertex_key
                for vertex_key in endpoints
                if not self._vertices_same_physical_position(vertex_key, last_settlement)
            ),
            endpoints[0],
        )
        return self._benchmark_forward_vertex_value(state, forward_vertex)

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

    def _is_setup_settlement_legal(self, state: Dict[str, Any], vertex_key: str) -> bool:
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        if vertex_key not in vertices:
            return False
        if self._has_building_at_physical_vertex(state, vertex_key):
            return False
        if self._has_adjacent_physical_building(state, vertex_key):
            return False
        return True

    def _is_main_settlement_legal(self, state: Dict[str, Any], vertex_key: str, myi: int) -> bool:
        if not self._is_setup_settlement_legal(state, vertex_key):
            return False
        return self._settlement_touches_own_road(state, vertex_key, myi)

    @staticmethod
    def _setup_road_touches_settlement(edge_key: str, settlement_key: str) -> bool:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        return any(
            BenchmarkCalibrationAgent._vertices_same_physical_position(endpoint, settlement_key)
            for endpoint in endpoints
        )

    def _main_road_connects_to_physical_network(self, state: Dict[str, Any], edge_key: str, myi: int) -> bool:
        parsed = _parse_edge_key(edge_key)
        endpoints = _edge_vertices(*parsed) if parsed else []
        for vertex_key in endpoints:
            building = self._vertex_record(state, vertex_key)
            if building and building.get("owner") == myi and building.get("building"):
                return True
            for adjacent_edge in self._physical_edges_touching_vertex(state, vertex_key):
                if self._edges_same_physical_position(edge_key, adjacent_edge):
                    continue
                if self._edge_owner(state, adjacent_edge) == myi:
                    return True
        return False

    @staticmethod
    def _has_building_at_physical_vertex(state: Dict[str, Any], vertex_key: str) -> bool:
        position = BenchmarkCalibrationAgent._vertex_position_key(vertex_key)
        return position is not None and position in BenchmarkCalibrationAgent._building_position_map(state)

    @staticmethod
    def _has_adjacent_physical_building(state: Dict[str, Any], vertex_key: str) -> bool:
        position = BenchmarkCalibrationAgent._vertex_position_key(vertex_key)
        if position is None:
            return False
        for other_position in BenchmarkCalibrationAgent._building_position_map(state):
            if other_position == position:
                continue
            distance = math.dist(position, other_position)
            if abs(distance - 50.0) < 1.0:
                return True
        return False

    @staticmethod
    def _physical_edges_touching_vertex(state: Dict[str, Any], vertex_key: str) -> List[str]:
        position = BenchmarkCalibrationAgent._vertex_position_key(vertex_key)
        if position is None:
            return []
        return list(BenchmarkCalibrationAgent._edge_touching_position_map(state).get(position, []))

    @staticmethod
    def _vertices_same_physical_position(left_key: str, right_key: str) -> bool:
        left = BenchmarkCalibrationAgent._vertex_pixel_position(left_key)
        right = BenchmarkCalibrationAgent._vertex_pixel_position(right_key)
        if left is None or right is None:
            return False
        return math.dist(left, right) < 1.0

    @staticmethod
    def _vertices_physically_adjacent(left_key: str, right_key: str) -> bool:
        left = BenchmarkCalibrationAgent._vertex_pixel_position(left_key)
        right = BenchmarkCalibrationAgent._vertex_pixel_position(right_key)
        if left is None or right is None:
            return False
        return abs(math.dist(left, right) - 50.0) < 1.0

    @staticmethod
    def _vertex_pixel_position(vertex_key: str) -> Optional[Tuple[float, float]]:
        parsed = _parse_vertex_key(vertex_key)
        if not parsed:
            return None
        q, r, direction = parsed
        center_x = 50.0 * math.sqrt(3.0) * (q + (r / 2.0))
        center_y = 50.0 * (3.0 / 2.0) * r
        angles = [90, 30, -30, -90, -150, 150]
        radians = math.radians(angles[direction])
        return (
            center_x + (50.0 * math.cos(radians)),
            center_y - (50.0 * math.sin(radians)),
        )

    @staticmethod
    def _vertex_position_key(vertex_key: str) -> Optional[Tuple[float, float]]:
        position = BenchmarkCalibrationAgent._vertex_pixel_position(vertex_key)
        if position is None:
            return None
        return round(position[0], 3), round(position[1], 3)

    @staticmethod
    def _state_cache(state: Dict[str, Any]) -> Dict[str, Any]:
        cache = state.get("_benchmark_agent_cache")
        if not isinstance(cache, dict):
            cache = {}
            state["_benchmark_agent_cache"] = cache
        return cache

    @staticmethod
    def _building_position_map(state: Dict[str, Any]) -> Dict[Tuple[float, float], Dict[str, Any]]:
        cache = BenchmarkCalibrationAgent._state_cache(state)
        cached = cache.get("building_position_map")
        if isinstance(cached, dict):
            return cached
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        out: Dict[Tuple[float, float], Dict[str, Any]] = {}
        for vertex_key, vertex in vertices.items():
            if not isinstance(vertex, dict) or not vertex.get("building"):
                continue
            position = BenchmarkCalibrationAgent._vertex_position_key(str(vertex_key))
            if position is not None:
                out[position] = vertex
        cache["building_position_map"] = out
        return out

    @staticmethod
    def _edge_touching_position_map(state: Dict[str, Any]) -> Dict[Tuple[float, float], List[str]]:
        cache = BenchmarkCalibrationAgent._state_cache(state)
        cached = cache.get("edge_touching_position_map")
        if isinstance(cached, dict):
            return cached
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        out: Dict[Tuple[float, float], List[str]] = {}
        for edge_key in edges:
            endpoints = BenchmarkCalibrationAgent._edge_endpoint_positions(str(edge_key))
            if not endpoints:
                continue
            for position in endpoints:
                out.setdefault(position, []).append(str(edge_key))
        cache["edge_touching_position_map"] = out
        return out

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
            and self._discard_player_index(entry) == myi
            for entry in discarding
        )

    def _discard_payload(self, state: Dict[str, Any]) -> Dict[str, int]:
        myi = state.get("myIndex")
        discarding = state.get("discardingPlayers")
        if not isinstance(myi, int) or not isinstance(discarding, list):
            return {}

        entry = next(
            (
                item for item in discarding
                if isinstance(item, dict)
                and self._discard_player_index(item) == myi
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

        advice = self._get_server_discard_advice()
        if isinstance(advice, dict):
            options = advice.get("discardOptions")
            if isinstance(options, list) and options:
                best = next(
                    (
                        option for option in options
                        if isinstance(option, dict) and isinstance(option.get("discardedResources"), dict)
                    ),
                    None,
                )
                if isinstance(best, dict):
                    return {
                        str(resource): int(amount or 0)
                        for resource, amount in best.get("discardedResources", {}).items()
                        if int(amount or 0) > 0
                    }

        best_bundle: Optional[Dict[str, int]] = None
        best_score = -1.0
        for bundle in self._enumerate_discard_bundles(hand, required):
            retained = {
                resource: max(0, int(hand.get(resource, 0) or 0) - int(bundle.get(resource, 0) or 0))
                for resource in RESOURCES
            }
            score = self._retained_hand_value(state, retained)
            if score > best_score:
                best_score = score
                best_bundle = bundle
        if not best_bundle:
            return {}
        return {resource: amount for resource, amount in best_bundle.items() if amount > 0}

    def _enumerate_discard_bundles(self, hand: Dict[str, int], discard_count: int) -> List[Dict[str, int]]:
        bundles: List[Dict[str, int]] = []

        def walk(index: int, remaining: int, current: Dict[str, int]) -> None:
            if index == len(RESOURCES) - 1:
                resource = RESOURCES[index]
                if remaining <= int(hand.get(resource, 0) or 0):
                    bundles.append({**current, resource: remaining})
                return
            resource = RESOURCES[index]
            max_take = min(remaining, int(hand.get(resource, 0) or 0))
            for amount in range(max_take + 1):
                walk(index + 1, remaining - amount, {**current, resource: amount})

        walk(0, discard_count, {})
        return bundles

    def _retained_hand_value(self, state: Dict[str, Any], retained: Dict[str, int]) -> float:
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            return 0.0
        plans = self._benchmark_plan_data(state, retained, myi)
        best_affordable = max(
            [float(plan["baseScore"]) for plan in plans if plan["affordable"]]
            + [0.0]
        )
        best_future = max(
            [
                float(plan["baseScore"]) / (1.0 + (0.75 * int(plan["missingCount"])))
                for plan in plans
            ]
            + [0.0]
        )
        diversity = len([resource for resource in RESOURCES if int(retained.get(resource, 0) or 0) > 0]) / len(RESOURCES)
        return max(0.0, min(1.0, (best_affordable * 0.55) + (best_future * 0.30) + (diversity * 0.15)))

    @staticmethod
    def _discard_player_index(entry: Dict[str, Any]) -> Optional[int]:
        value = entry.get("playerIndex")
        if value is None:
            value = entry.get("player_index")
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _handle_robber(self, state: Dict[str, Any]) -> None:
        self._start_turn("robber")
        advice = self._get_server_robber_advice(None)
        if isinstance(advice, dict):
            hex_options = advice.get("hexOptions")
            if isinstance(hex_options, list) and hex_options:
                for option in hex_options:
                    if not isinstance(option, dict) or not option.get("id"):
                        continue
                    hex_key = str(option.get("id"))
                    victim = self._best_victim_on_hex(state, hex_key)
                    result = self._execute("move_robber", {
                        "hex_key": hex_key,
                        "steal_from_player_id": victim,
                    })
                    if self._ok(result):
                        self.stats.end_turn()
                        return
        for hex_key in self._rank_benchmark_robber_hexes(state):
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

        self._execute_benchmark_aligned_build_plan(self.client.latest_state() or state)

        state = self.client.latest_state() or state
        if int(self._dev_cards(state).get("knight", 0) or 0) > 0 and self._should_play_knight(state):
            self._execute("play_dev_card", {"card_type": "knight"})

        # Calibration mode prioritizes benchmark/heuristic alignment. Player
        # trades depend on hidden opponent hands in the live player view, while
        # the benchmark scorer sees the full server state. Avoid emitting noisy
        # trade-generation and response tasks from proactive offers here.
        self._execute("end_turn", {})
        self.stats.end_turn()

    def _execute_benchmark_aligned_build_plan(self, state: Dict[str, Any]) -> None:
        roads_built = 0

        for _ in range(5):
            state = self.client.latest_state() or state
            hand = self._hand(state)
            myi = state.get("myIndex")
            if not isinstance(myi, int):
                break

            build_advice = self._get_server_build_advice()
            plan = self._best_benchmark_build_plan(state, hand, myi, roads_built, build_advice)
            if not plan or plan["id"] == "save":
                break

            action = plan["action"]
            succeeded = False
            if action == "city":
                succeeded = self._try_city_upgrade(state, build_advice)
            elif action == "settlement":
                succeeded = self._try_settlement(state, build_advice)
            elif action == "road":
                succeeded = self._try_strategic_road(state, myi, build_advice)
                if succeeded:
                    roads_built += 1
            elif action == "devCard":
                succeeded = self._ok(self._execute("buy_dev_card", {}))

            if succeeded:
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

    def _should_build_calibration_road(self, state: Dict[str, Any], myi: int) -> bool:
        if not self._has_connected_settlement_target(state, myi):
            return True
        ranked = self._rank_benchmark_legal_roads(state, myi)
        if not ranked:
            return False
        # The benchmark save option bottoms out at 0.35. If the best road is
        # comfortably above that, building it avoids a save-vs-road task fail.
        return self._benchmark_road_score(state, ranked[0], myi) >= 0.40

    def _best_benchmark_build_plan(
        self,
        state: Dict[str, Any],
        hand: Dict[str, int],
        myi: int,
        roads_built: int,
        build_advice: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if isinstance(build_advice, dict):
            advised_options = build_advice.get("options")
            if isinstance(advised_options, list) and advised_options:
                normalized_options: List[Dict[str, Any]] = []
                for option in advised_options:
                    if not isinstance(option, dict):
                        continue
                    plan_id = str(option.get("id") or "")
                    if plan_id not in {"road", "settlement", "city", "devCard", "save"}:
                        continue
                    normalized_options.append({
                        "id": plan_id,
                        "score": max(0.0, min(1.0, float(option.get("score", 0.0) or 0.0))),
                        "action": plan_id,
                    })
                if normalized_options:
                    normalized_options.sort(key=lambda option: option["score"], reverse=True)
                    return normalized_options[0]

        plans = self._benchmark_plan_data(state, hand, myi, roads_built, build_advice)
        options = [
            {"id": plan["id"], "score": plan["baseScore"], "action": plan["id"]}
            for plan in plans
            if plan["affordable"] and plan["baseScore"] > 0
        ]
        options.append({
            "id": "save",
            "score": self._benchmark_save_score(plans, sum(hand.values())),
            "action": "save",
        })
        if not options:
            return None
        options.sort(key=lambda option: option["score"], reverse=True)
        return options[0]

    def _benchmark_plan_data(
        self,
        state: Dict[str, Any],
        hand: Dict[str, int],
        myi: int,
        roads_built: int = 0,
        build_advice: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if isinstance(build_advice, dict):
            advised = build_advice.get("planDecisionData")
            if isinstance(advised, list) and advised:
                costs = {
                    "road": BUILD_COSTS["road"],
                    "settlement": BUILD_COSTS["settlement"],
                    "city": BUILD_COSTS["city"],
                    "devCard": BUILD_COSTS["dev_card"],
                }
                return [
                    {
                        "id": plan_id,
                        "cost": costs[plan_id],
                        "baseScore": max(0.0, min(1.0, float(plan.get("baseScore", 0.0) or 0.0))),
                        "missingCount": int(plan.get("missingCount", self._missing_count(hand, costs[plan_id])) or 0),
                        "affordable": bool(plan.get("affordable", False)),
                    }
                    for plan in advised
                    for plan_id in [str(plan.get("id"))]
                    if plan_id in costs
                ]

        road_score = 0.0
        if roads_built < self._road_build_limit(state) and self._can_place_expansion_road(state, hand):
            ranked_roads = self._rank_benchmark_legal_roads(state, myi)
            road_score = self._benchmark_road_score(state, ranked_roads[0], myi) if ranked_roads else 0.0

        settlement_targets = self._rank_connected_settlement_targets(state, myi, top_k=1)
        settlement_score = self._benchmark_settlement_score(state, settlement_targets[0]) if settlement_targets else 0.0

        city_targets = self._rank_city_targets(state, myi)
        city_score = self._benchmark_city_score(state, city_targets[0]) if city_targets else 0.0

        dev_score = self._benchmark_dev_card_score(state, hand, road_score, settlement_score, city_score)
        costs = {
            "road": BUILD_COSTS["road"],
            "settlement": BUILD_COSTS["settlement"],
            "city": BUILD_COSTS["city"],
            "devCard": BUILD_COSTS["dev_card"],
        }
        base_scores = {
            "road": road_score,
            "settlement": settlement_score,
            "city": city_score,
            "devCard": dev_score,
        }
        return [
            {
                "id": plan_id,
                "cost": costs[plan_id],
                "baseScore": max(0.0, min(1.0, score)),
                "missingCount": self._missing_count(hand, costs[plan_id]),
                "affordable": self._can_afford(hand, costs[plan_id]),
            }
            for plan_id, score in base_scores.items()
        ]

    def _benchmark_save_score(self, plans: List[Dict[str, Any]], resource_total: int) -> float:
        best_future = max(
            [
                float(plan["baseScore"]) / (1.0 + (0.75 * int(plan["missingCount"])))
                for plan in plans
                if int(plan["missingCount"]) > 0
            ]
            + [0.0]
        )
        return max(0.2, min(1.0, best_future - self._discard_risk_penalty(resource_total)))

    def _benchmark_dev_card_score(
        self,
        state: Dict[str, Any],
        hand: Dict[str, int],
        road_score: float,
        settlement_score: float,
        city_score: float,
    ) -> float:
        if not self._can_afford(hand, BUILD_COSTS["dev_card"]):
            return 0.0
        if int(state.get("devCardDeck", state.get("devDeck", 1)) or 0) <= 0:
            return 0.0
        dev_cards = self._dev_cards(state)
        held_dev_cards = sum(int(value or 0) for value in dev_cards.values())
        myi = state.get("myIndex")
        players = state.get("players") if isinstance(state.get("players"), list) else []
        me = self._me(state)
        best_opponent_knights = max(
            [
                int(player.get("knightsPlayed", 0) or 0)
                for index, player in enumerate(players)
                if index != myi and isinstance(player, dict)
            ]
            + [0]
        )
        army_bonus = 0.2 if int(me.get("knightsPlayed", 0) or 0) + held_dev_cards + 1 >= max(3, best_opponent_knights + 1) else 0.0
        congestion_penalty = 0.35 if held_dev_cards >= 3 else held_dev_cards * 0.08
        expansion_penalty = 0.18 if max(road_score, settlement_score, city_score) > 0.45 else 0.0
        return max(0.0, min(1.0, max(0.15, 0.45 * (0.5 + (city_score * 0.5)) + army_bonus - congestion_penalty - expansion_penalty)))

    def _rank_city_targets(self, state: Dict[str, Any], myi: int) -> List[str]:
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        targets = [
            vertex_key
            for vertex_key, vertex in vertices.items()
            if isinstance(vertex, dict)
            and vertex.get("building") == "settlement"
            and vertex.get("owner") == myi
        ]
        targets.sort(key=lambda vertex_key: self._benchmark_city_score(state, vertex_key), reverse=True)
        return targets

    def _benchmark_city_score(self, state: Dict[str, Any], vertex_key: str) -> float:
        vertex_score = self._benchmark_vertex_score(state, vertex_key)
        return max(0.0, min(1.0, (
            (vertex_score["resourceProductionExpectancy"] * 0.65)
            + (vertex_score["resourceDiversity"] * 0.10)
            + (vertex_score["portSynergy"] * 0.05)
            + 0.20
        )))

    @staticmethod
    def _missing_count(hand: Dict[str, int], cost: Dict[str, int]) -> int:
        return sum(
            max(0, int(amount or 0) - int(hand.get(resource, 0) or 0))
            for resource, amount in cost.items()
        )

    def _try_city_upgrade(self, state: Dict[str, Any], build_advice: Optional[Dict[str, Any]] = None) -> bool:
        advice_targets = build_advice.get("cityTargets") if isinstance(build_advice, dict) else None
        if isinstance(advice_targets, list):
            for spot in advice_targets[:10]:
                vertex_key = spot.get("vertexKey") or spot.get("vertex_key") or spot.get("vertex")
                if not vertex_key:
                    continue
                if self._ok(self._execute("upgrade_to_city", {"vertex_key": str(vertex_key)})):
                    return True
        for spot in rank_cities_by_value_gain(state, top_k=10):
            vertex_key = spot.get("vertex") or spot.get("vertex_key") or spot.get("vertexKey")
            if not vertex_key:
                continue
            if self._ok(self._execute("upgrade_to_city", {"vertex_key": str(vertex_key)})):
                return True
        return False

    def _try_settlement(self, state: Dict[str, Any], build_advice: Optional[Dict[str, Any]] = None) -> bool:
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            return False
        advice_targets = build_advice.get("settlementTargets") if isinstance(build_advice, dict) else None
        if isinstance(advice_targets, list):
            for spot in advice_targets[:20]:
                vertex_key = spot.get("vertexKey") or spot.get("vertex_key") or spot.get("vertex")
                if not vertex_key:
                    continue
                if self._ok(self._execute("place_settlement", {"vertex_key": str(vertex_key), "is_setup": False})):
                    return True
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
            and self._is_main_settlement_legal(state, vertex_key, myi)
            and self._settlement_touches_own_road(state, vertex_key, myi)
        ]
        candidates.sort(key=lambda vertex_key: self._benchmark_settlement_score(state, vertex_key), reverse=True)
        return candidates[:top_k]

    def _has_connected_settlement_target(self, state: Dict[str, Any], myi: int) -> bool:
        return bool(self._rank_connected_settlement_targets(state, myi, top_k=1))

    def _settlement_touches_own_road(self, state: Dict[str, Any], vertex_key: str, myi: int) -> bool:
        return any(
            self._edge_owner(state, edge_key) == myi
            for edge_key in self._physical_edges_touching_vertex(state, vertex_key)
        )

    def _try_strategic_road(self, state: Dict[str, Any], myi: int, build_advice: Optional[Dict[str, Any]] = None) -> bool:
        advice_targets = build_advice.get("roadTargets") if isinstance(build_advice, dict) else None
        if isinstance(advice_targets, list):
            for spot in advice_targets[:12]:
                edge_key = spot.get("edgeKey") or spot.get("edge_key") or spot.get("edge")
                if not edge_key:
                    continue
                if self._ok(self._execute("place_road", {"edge_key": str(edge_key), "is_setup": False})):
                    return True
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
            if self._edge_is_physically_unoccupied(state, edge_key)
            and (
                self._main_road_connects_to_physical_network(state, edge_key, myi)
                or _main_road_connects_to_network(state, edge_key, myi)
            )
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
        if self._has_building_at_physical_vertex(state, vertex_key):
            return 0.0
        if self._has_adjacent_physical_building(state, vertex_key):
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
        return sum(
            1
            for edge_key in self._physical_edges_touching_vertex(state, vertex_key)
            if self._edge_owner(state, edge_key) == myi
        )

    def _best_road_edge_for_benchmark(self, state: Dict[str, Any], myi: int) -> Optional[str]:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        candidates: List[str] = []
        for edge_key, edge in edges.items():
            if not _edge_is_unoccupied(edge):
                continue
            if not self._edge_is_physically_unoccupied(state, edge_key):
                continue
            if not (
                self._main_road_connects_to_physical_network(state, edge_key, myi)
                or _main_road_connects_to_network(state, edge_key, myi)
            ):
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
        exact = vertices.get(vertex_key)
        if isinstance(exact, dict) and exact.get("building"):
            return exact
        for candidate_key, vertex in vertices.items():
            if not isinstance(vertex, dict) or not vertex.get("building"):
                continue
            if BenchmarkCalibrationAgent._vertices_same_physical_position(vertex_key, str(candidate_key)):
                return vertex
        return exact if isinstance(exact, dict) else None

    @staticmethod
    def _edge_owner(state: Dict[str, Any], edge_key: str) -> Optional[int]:
        endpoints = BenchmarkCalibrationAgent._edge_endpoint_positions(edge_key)
        if not endpoints:
            return None
        return BenchmarkCalibrationAgent._edge_owner_map(state).get(endpoints)

    @staticmethod
    def _edge_owner_map(state: Dict[str, Any]) -> Dict[Tuple[Tuple[float, float], Tuple[float, float]], int]:
        cache = BenchmarkCalibrationAgent._state_cache(state)
        cached = cache.get("edge_owner_map")
        if isinstance(cached, dict):
            return cached
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        out: Dict[Tuple[Tuple[float, float], Tuple[float, float]], int] = {}
        for candidate_key, edge in edges.items():
            if not isinstance(edge, dict):
                continue
            owner = edge.get("owner")
            if not isinstance(owner, int):
                road_owner = edge.get("road")
                owner = road_owner if isinstance(road_owner, int) else None
            if owner is None:
                continue
            endpoints = BenchmarkCalibrationAgent._edge_endpoint_positions(str(candidate_key))
            if endpoints:
                out[endpoints] = owner
        cache["edge_owner_map"] = out
        return out

    @staticmethod
    def _edge_endpoint_positions(edge_key: str) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
        parsed = _parse_edge_key(edge_key)
        if not parsed:
            return None
        positions = [
            BenchmarkCalibrationAgent._vertex_pixel_position(vertex_key)
            for vertex_key in _edge_vertices(*parsed)
        ]
        if any(position is None for position in positions):
            return None
        rounded = sorted((round(position[0], 3), round(position[1], 3)) for position in positions if position is not None)
        if len(rounded) != 2:
            return None
        return rounded[0], rounded[1]

    @staticmethod
    def _edges_same_physical_position(left_key: str, right_key: str) -> bool:
        return (
            BenchmarkCalibrationAgent._edge_endpoint_positions(left_key)
            == BenchmarkCalibrationAgent._edge_endpoint_positions(right_key)
        )

    @staticmethod
    def _resolve_existing_edge_key(state: Dict[str, Any], edge_key: str) -> Optional[str]:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        if edge_key in edges:
            return edge_key
        target = BenchmarkCalibrationAgent._edge_endpoint_positions(edge_key)
        if not target:
            return None
        for candidate_key in edges.keys():
            if BenchmarkCalibrationAgent._edge_endpoint_positions(str(candidate_key)) == target:
                return str(candidate_key)
        return None

    @staticmethod
    def _edge_is_physically_unoccupied(state: Dict[str, Any], edge_key: str) -> bool:
        edges = state.get("edges") if isinstance(state.get("edges"), dict) else {}
        resolved_key = BenchmarkCalibrationAgent._resolve_existing_edge_key(state, edge_key)
        edge = edges.get(resolved_key) if resolved_key else None
        if not isinstance(edge, dict):
            return False
        if not _edge_is_unoccupied(edge):
            return False
        return BenchmarkCalibrationAgent._edge_owner(state, edge_key) is None

    def _bank_trade_to_unlock_build(self, state: Dict[str, Any]) -> bool:
        server_advice = self._get_server_bank_trade_advice()
        if isinstance(server_advice, dict):
            advised_options = server_advice.get("options")
            if isinstance(advised_options, list) and advised_options:
                best = next((option for option in advised_options if isinstance(option, dict)), None)
                if isinstance(best, dict):
                    if str(best.get("id") or "") == "no-trade":
                        print("  [benchmark] skip bank trade; benchmark-best option is no-trade")
                        return False
                    give_resource = best.get("giveResource")
                    get_resource = best.get("getResource")
                    give_amount = best.get("giveAmount")
                    if isinstance(give_resource, str) and isinstance(get_resource, str):
                        result = self._execute("bank_trade", {
                            "give_resource": give_resource,
                            "give_amount": int(give_amount or 0),
                            "get_resource": get_resource,
                        })
                        return self._ok(result)

        hand = self._hand(state)
        ratios = state.get("tradeRatios") if isinstance(state.get("tradeRatios"), dict) else {}
        candidates: List[Dict[str, Any]] = []
        metrics = self._goal_aware_resource_metrics(state, hand)

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
                score = self._bank_trade_growth_score(
                    state,
                    hand,
                    after,
                    give,
                    target,
                    ratio,
                    metrics,
                )
                candidate = {
                    "give_resource": give,
                    "give_amount": ratio,
                    "get_resource": target,
                    "score": score,
                }
                candidates.append(candidate)

        no_trade = {
            "give_resource": None,
            "give_amount": 0,
            "get_resource": None,
            "score": max(0.2, 0.45 - self._discard_risk_penalty(sum(hand.values()))),
        }
        best = max(candidates + [no_trade], key=lambda candidate: float(candidate["score"]))

        if best["give_resource"] is None:
            print("  [benchmark] skip bank trade; benchmark-best option is no-trade")
            return False

        if not best or not self._bank_trade_is_gameplay_useful(state, hand, best):
            if best:
                print(f"  [benchmark] skip bank trade; best score={best['score']:.2f} {best}")
            return False

        result = self._execute("bank_trade", {
            "give_resource": best["give_resource"],
            "give_amount": best["give_amount"],
            "get_resource": best["get_resource"],
        })
        return self._ok(result)

    def _bank_trade_is_gameplay_useful(
        self,
        state: Dict[str, Any],
        hand: Dict[str, int],
        trade: Dict[str, Any],
    ) -> bool:
        after = dict(hand)
        give_resource = str(trade["give_resource"])
        get_resource = str(trade["get_resource"])
        give_amount = int(trade["give_amount"])
        after[give_resource] = max(0, int(after.get(give_resource, 0) or 0) - give_amount)
        after[get_resource] = int(after.get(get_resource, 0) or 0) + 1

        # Bank-trade tasks are very sensitive because the benchmark scorer sees
        # the authoritative server hand. Only emit high-confidence trades; a
        # skipped bank trade records no bank-trade task, while a low-confidence
        # trade can poison the calibration signal.
        if float(trade.get("score", 0.0) or 0.0) < 0.95:
            return False
        before_best = self._best_affordable_plan_score(state, hand)
        after_best = self._best_affordable_plan_score(state, after)
        return after_best > before_best

    def _bank_trade_growth_score(
        self,
        state: Dict[str, Any],
        before: Dict[str, int],
        after: Dict[str, int],
        give_resource: str,
        get_resource: str,
        ratio: int,
        metrics: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> float:
        cached_metrics = metrics if isinstance(metrics, dict) else self._goal_aware_resource_metrics(state, before)
        need = cached_metrics["need"]
        surplus = cached_metrics["surplus"]
        # Keep this exactly aligned with Environment/server/index.js
        # buildBankTradeTaskPayload(): score = need(get) - 0.5 * surplus(give) + 0.5.
        return max(0.0, min(1.0, (
            float(need.get(get_resource, 0.0) or 0.0)
            - (float(surplus.get(give_resource, 0.0) or 0.0) * 0.5)
            + 0.5
            + max(0.0, self._discard_risk_penalty(sum(before.values())) - self._discard_risk_penalty(sum(after.values())))
        )))

    @staticmethod
    def _discard_risk_penalty(resource_total: int) -> float:
        return max(0.0, min(1.0, max(0, int(resource_total or 0) - 7) * 0.08))

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
        advice = self._get_server_dev_card_advice()
        if (
            int(dev_cards.get("roadBuilding", dev_cards.get("road_building", 0)) or 0) > 0
            and self._should_play_road_building(state, myi, advice)
        ):
            self._execute("play_dev_card", {"card_type": "roadBuilding"})
            return
        if int(dev_cards.get("monopoly", 0) or 0) > 0:
            resource = self._best_monopoly_resource(state, advice)
            self._execute("play_dev_card", {"card_type": "monopoly", "params": {"resource": resource}})
            return
        if int(dev_cards.get("yearOfPlenty", dev_cards.get("year_of_plenty", 0)) or 0) > 0:
            picks = self._year_of_plenty_picks(state, advice)
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
        surplus = self._resource_surplus_map(hand, state)
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
        surplus = self._resource_surplus_map(hand, state)
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
        if state is not None:
            return self._goal_aware_resource_metrics(state, hand)["need"]

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

    def _resource_surplus_map(self, hand: Dict[str, int], state: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
        if state is not None:
            return self._goal_aware_resource_metrics(state, hand)["surplus"]

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

    def _goal_aware_resource_metrics(self, state: Dict[str, Any], hand: Dict[str, int]) -> Dict[str, Dict[str, float]]:
        myi = state.get("myIndex")
        if not isinstance(myi, int):
            empty = {resource: 0.0 for resource in RESOURCES}
            return {"need": empty, "surplus": empty}

        priority_by_type = {
            "city": 1.1,
            "settlement": 1.5,
            "devCard": 0.75,
            "road": 0.25,
        }
        need_raw = {resource: 0.0 for resource in RESOURCES}
        reserved_raw = {resource: 0.0 for resource in RESOURCES}

        for plan in self._benchmark_plan_data(state, hand, myi):
            base_weight = max(0.0, min(1.0, float(plan.get("baseScore", 0.0) or 0.0)))
            base_weight *= priority_by_type.get(str(plan.get("id")), 1.0)
            if base_weight <= 0:
                continue

            cost = plan.get("cost") if isinstance(plan.get("cost"), dict) else {}
            missing_by_resource = {
                resource: max(0, int(cost.get(resource, 0) or 0) - int(hand.get(resource, 0) or 0))
                for resource in RESOURCES
            }
            total_missing = sum(missing_by_resource.values())
            plan_weight = base_weight / (1.0 + (0.75 * total_missing))
            missing_denominator = max(1, total_missing)

            for resource in RESOURCES:
                need_raw[resource] += plan_weight * (missing_by_resource[resource] / missing_denominator)
                reserved_raw[resource] += plan_weight * int(cost.get(resource, 0) or 0)

        reserved = {
            resource: min(float(hand.get(resource, 0) or 0), reserved_raw[resource])
            for resource in RESOURCES
        }
        free = {
            resource: max(0.0, float(hand.get(resource, 0) or 0) - reserved[resource])
            for resource in RESOURCES
        }
        return {
            "need": self._normalize_resource_scores(need_raw),
            "surplus": self._normalize_resource_scores(free),
        }

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

    def _rank_benchmark_robber_hexes(self, state: Dict[str, Any]) -> List[str]:
        hexes = state.get("hexes") if isinstance(state.get("hexes"), dict) else {}
        current_robber = state.get("robber")
        candidates = [str(hex_key) for hex_key in hexes.keys() if str(hex_key) != str(current_robber)]
        candidates.sort(key=lambda hex_key: self._benchmark_robber_score(state, hex_key), reverse=True)
        return candidates

    def _benchmark_robber_score(self, state: Dict[str, Any], hex_key: str) -> float:
        parts = self._benchmark_robber_components(state, hex_key)
        return max(0.0, min(1.0, (
            (parts["hurtLeader"] * 0.35)
            + (parts["productionDamage"] * 0.30)
            + (parts["theftValue"] * 0.20)
            + (parts["selfHarmAvoidance"] * 0.15)
        )))

    def _benchmark_robber_components(self, state: Dict[str, Any], hex_key: str) -> Dict[str, float]:
        hexes = state.get("hexes") if isinstance(state.get("hexes"), dict) else {}
        hex_tile = hexes.get(hex_key)
        if not isinstance(hex_tile, dict):
            return {
                "hurtLeader": 0.0,
                "productionDamage": 0.0,
                "theftValue": 0.0,
                "selfHarmAvoidance": 0.0,
            }

        myi = state.get("myIndex")
        leader_index = self._leader_index(state)
        number_weight = float(_pip_value(hex_tile.get("number") or hex_tile.get("token")) or 0)
        leader_damage = 0.0
        total_damage = 0.0
        best_theft_value = 0.0

        for player_index in self._players_on_hex(state, hex_key, exclude_index=myi if isinstance(myi, int) else None):
            player = self._player_by_index(state, player_index)
            if not player:
                continue
            resource_total = self._resource_total(player.get("resources"))
            strength = min(1.0, (self._visible_player_score(player) + (resource_total / 8.0)) / 5.0)
            total_damage += strength
            if player_index == leader_index:
                leader_damage += strength
            best_theft_value = max(best_theft_value, min(1.0, resource_total / 7.0))

        harms_self = isinstance(myi, int) and myi in self._players_on_hex(state, hex_key, exclude_index=None)
        return {
            "hurtLeader": min(1.0, (leader_damage * number_weight) / 3.0),
            "productionDamage": min(1.0, (total_damage * number_weight) / 5.0),
            "theftValue": best_theft_value,
            "selfHarmAvoidance": 0.0 if harms_self else 1.0,
        }

    def _players_on_hex(
        self,
        state: Dict[str, Any],
        hex_key: str,
        exclude_index: Optional[int],
    ) -> List[int]:
        seen: List[int] = []
        vertices = state.get("vertices") if isinstance(state.get("vertices"), dict) else {}
        for vertex_key, vertex in vertices.items():
            if not isinstance(vertex, dict) or not vertex.get("building"):
                continue
            if not self._vertex_key_touches_hex(str(vertex_key), hex_key, vertex):
                continue
            owner = self._player_index_from_owner(state, vertex.get("owner"))
            if owner is None or owner == exclude_index or owner in seen:
                continue
            seen.append(owner)
        return seen

    @staticmethod
    def _visible_player_score(player: Dict[str, Any]) -> int:
        score = int(player.get("victoryPoints", player.get("victory_points", 0)) or 0)
        if player.get("hasLongestRoad") or player.get("longestRoad"):
            score += 2
        if player.get("hasLargestArmy") or player.get("largestArmy"):
            score += 2
        return score

    @staticmethod
    def _resource_total(resources: Any) -> int:
        if isinstance(resources, dict):
            return sum(int(value or 0) for value in resources.values())
        if isinstance(resources, (int, float)):
            return int(resources or 0)
        return 0

    def _player_by_index(self, state: Dict[str, Any], index: int) -> Optional[Dict[str, Any]]:
        players = state.get("players") if isinstance(state.get("players"), list) else []
        if 0 <= index < len(players) and isinstance(players[index], dict):
            return players[index]
        return None

    def _player_index_from_owner(self, state: Dict[str, Any], owner: Any) -> Optional[int]:
        if isinstance(owner, int):
            return owner
        players = state.get("players") if isinstance(state.get("players"), list) else []
        for index, player in enumerate(players):
            if isinstance(player, dict) and owner in (player.get("id"), player.get("playerId")):
                return index
        return None

    def _best_victim_on_hex(self, state: Dict[str, Any], hex_key: str) -> Optional[str]:
        advice = self._get_server_robber_advice(hex_key)
        if isinstance(advice, dict):
            options = advice.get("victimOptions")
            if isinstance(options, list) and options:
                best = next((option for option in options if isinstance(option, dict) and option.get("id")), None)
                if isinstance(best, dict):
                    return str(best.get("id"))
        players = state.get("players") if isinstance(state.get("players"), list) else []
        myi = state.get("myIndex")
        leader_index = self._leader_index(state)
        best: Optional[Tuple[float, str]] = None
        for owner in self._players_on_hex(state, hex_key, exclude_index=myi if isinstance(myi, int) else None):
            player = players[owner] if 0 <= owner < len(players) and isinstance(players[owner], dict) else {}
            player_id = player.get("id") if isinstance(player, dict) else str(owner)
            resource_total = self._resource_total(player.get("resources")) if isinstance(player, dict) else 0
            score = min(
                1.0,
                (0.55 if owner == leader_index else 0.25)
                + min(0.35, resource_total / 20.0)
                + min(0.1, self._visible_player_score(player) / 20.0 if isinstance(player, dict) else 0.0),
            )
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

    @staticmethod
    def _vertex_key_touches_hex(vertex_key: str, hex_key: str, vertex: Optional[Dict[str, Any]] = None) -> bool:
        if isinstance(vertex, dict) and BenchmarkCalibrationAgent._vertex_touches_hex(vertex, hex_key):
            return True
        parsed = _parse_vertex_key(vertex_key)
        if not parsed:
            return False
        q, r, direction = parsed
        coords: List[Tuple[int, int]] = [(q, r)]
        if direction == 0:
            coords += [(q, r - 1), (q + 1, r - 1)]
        elif direction == 1:
            coords += [(q + 1, r - 1), (q + 1, r)]
        elif direction == 2:
            coords += [(q + 1, r), (q, r + 1)]
        elif direction == 3:
            coords += [(q, r + 1), (q - 1, r + 1)]
        elif direction == 4:
            coords += [(q - 1, r + 1), (q - 1, r)]
        elif direction == 5:
            coords += [(q - 1, r), (q, r - 1)]
        return str(hex_key) in {f"{hq},{hr}" for hq, hr in coords}

    def _should_play_road_building(
        self,
        state: Dict[str, Any],
        myi: Any,
        advice: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if isinstance(advice, dict):
            options = advice.get("roadBuildingOptions")
            if isinstance(options, list) and options:
                best = max(
                    [option for option in options if isinstance(option, dict) and option.get("id") in {"play-now", "hold"}],
                    key=lambda option: float(option.get("score", 0.0) or 0.0),
                    default=None,
                )
                if isinstance(best, dict):
                    return str(best.get("id")) == "play-now"
        return (
            isinstance(myi, int)
            and not self._has_connected_settlement_target(state, myi)
            and bool(self._rank_strategic_road_edges(state, myi))
        )

    def _best_monopoly_resource(self, state: Dict[str, Any], advice: Optional[Dict[str, Any]] = None) -> str:
        if isinstance(advice, dict):
            options = advice.get("monopolyOptions")
            if isinstance(options, list) and options:
                best = max(
                    [option for option in options if isinstance(option, dict) and option.get("id")],
                    key=lambda option: float(option.get("score", 0.0) or 0.0),
                    default=None,
                )
                if isinstance(best, dict):
                    return str(best.get("id"))
        totals = {r: 0 for r in RESOURCES}
        myi = state.get("myIndex")
        for index, player in enumerate(state.get("players") or []):
            if index == myi or not isinstance(player, dict):
                continue
            resources = player.get("resources") if isinstance(player.get("resources"), dict) else {}
            for resource in RESOURCES:
                totals[resource] += int(resources.get(resource, 0) or 0)
        return max(totals, key=lambda r: (totals[r], r))

    def _year_of_plenty_picks(self, state: Dict[str, Any], advice: Optional[Dict[str, Any]] = None) -> List[str]:
        if advice is None:
            advice = self._get_server_dev_card_advice()
        if isinstance(advice, dict):
            options = advice.get("yearOfPlentyOptions")
            if isinstance(options, list) and options:
                best = next((option for option in options if isinstance(option, dict) and option.get("id")), None)
                if isinstance(best, dict):
                    parts = [str(part) for part in str(best.get("id")).split("+") if part]
                    if len(parts) == 2:
                        return parts
        hand = self._hand(state)
        for cost in (BUILD_COSTS["city"], BUILD_COSTS["settlement"], BUILD_COSTS["dev_card"], BUILD_COSTS["road"]):
            missing: List[str] = []
            for resource, needed in cost.items():
                missing.extend([resource] * max(0, int(needed) - int(hand.get(resource, 0) or 0)))
            if missing:
                return (missing + ["ore", "grain"])[:2]
        return ["ore", "grain"]

    def _should_play_knight(self, state: Dict[str, Any]) -> bool:
        advice = self._get_server_dev_card_advice()
        if isinstance(advice, dict):
            options = advice.get("knightOptions")
            if isinstance(options, list) and options:
                best = max(
                    [option for option in options if isinstance(option, dict) and option.get("id") in {"play-now", "hold"}],
                    key=lambda option: float(option.get("score", 0.0) or 0.0),
                    default=None,
                )
                if isinstance(best, dict):
                    return str(best.get("id")) == "play-now"
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

    def _get_server_build_advice(self) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.call("getBenchmarkBuildAdvice", {}, timeout=10)
        except Exception:
            return None
        if not isinstance(response, dict) or not response.get("success"):
            return None
        return response

    def _get_server_bank_trade_advice(self) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.call("getBenchmarkBankTradeAdvice", {}, timeout=10)
        except Exception:
            return None
        if not isinstance(response, dict) or not response.get("success"):
            return None
        return response

    def _get_server_dev_card_advice(self) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.call("getBenchmarkDevCardAdvice", {}, timeout=10)
        except Exception:
            return None
        if not isinstance(response, dict) or not response.get("success"):
            return None
        return response

    def _get_server_robber_advice(self, hex_key: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            payload: Dict[str, Any] = {}
            if hex_key is not None:
                payload["hexKey"] = str(hex_key)
            response = self.client.call("getBenchmarkRobberAdvice", payload, timeout=10)
        except Exception:
            return None
        if not isinstance(response, dict) or not response.get("success"):
            return None
        return response

    def _get_server_discard_advice(self) -> Optional[Dict[str, Any]]:
        try:
            response = self.client.call("getBenchmarkDiscardAdvice", {}, timeout=10)
        except Exception:
            return None
        if not isinstance(response, dict) or not response.get("success"):
            return None
        return response

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
        print(f"  [benchmark] {name}({json.dumps(args, default=str)}) -> {json.dumps(result, default=str)}")
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
