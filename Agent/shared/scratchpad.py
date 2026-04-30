"""
Shared Scratchpad — thread-safe in-memory state shared by all agents.

Each agent reads the sections it needs and writes its outputs.
The Scratchpad wraps a collection of typed fields with a threading.Lock.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from Agent.utils.game_state_processor import GameStateProcessor, RESOURCES


# ── Sub-schemas (agent-produced data) ─────────────────────────────

@dataclass
class TradePolicy:
    """Strategy Agent's guidance for the Trading Agent."""
    willing_to_give: List[str] = field(default_factory=list)
    desperately_need: List[str] = field(default_factory=list)
    max_bank_ratio_acceptable: int = 4
    should_propose_trades: bool = True
    min_accept_score: float = 0.5


@dataclass
class RiskAnalysis:
    """Output of the Risk Agent's deterministic + LLM analysis."""
    resource_expected_income: Dict[str, float] = field(default_factory=dict)
    per_building_income: List[Dict] = field(default_factory=list)
    opponent_threats: List[Dict] = field(default_factory=list)
    win_probabilities: Dict[str, float] = field(default_factory=dict)
    robber_impact: List[Dict] = field(default_factory=list)
    best_settlement_vertices: List[Dict] = field(default_factory=list)
    best_city_vertices: List[Dict] = field(default_factory=list)
    threat_narrative: str = ""
    updated_at: float = 0.0


@dataclass
class StrategyPlan:
    """Strategy Agent's turn plan, consumed by Development and Trading."""
    long_term_goal: str = "balanced"           # "cities"|"longest_road"|"largest_army"|"balanced"
    short_term_goals: List[str] = field(default_factory=list)
    priority_resources: List[str] = field(default_factory=list)
    build_queue: List[Dict] = field(default_factory=list)
    trade_policy: TradePolicy = field(default_factory=TradePolicy)
    should_trade_first: bool = True              # dynamic action ordering
    risk_tolerance: str = "moderate"           # "aggressive"|"moderate"|"conservative"
    reasoning: str = ""
    updated_at: float = 0.0


@dataclass
class TradeState:
    """Trading Agent's persistent state across turns."""
    recent_trades: List[Dict] = field(default_factory=list)
    pending_offer: Optional[Dict] = None
    player_trade_history: Dict[str, List] = field(default_factory=dict)
    player_reputation: Dict[str, float] = field(default_factory=dict)
    updated_at: float = 0.0


@dataclass
class RoundSummary:
    """
    Diff of what happened since Strategy was last invoked on our turn.

    Written by Strategy at the start of each turn (after comparing the
    new game state against the snapshot it took when it ended its last
    turn).  Consumed by the plan LLM call so the model has concrete
    context about what opponents did between our turns.
    """
    turn_number: int = 0
    vp_deltas: Dict[str, int] = field(default_factory=dict)             # player_name -> delta
    new_buildings: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)  # player_name -> [{type, vertex}]
    robber_moved: Optional[Dict[str, str]] = None                        # {"from": hex, "to": hex}
    longest_road_change: Optional[Dict[str, Any]] = None                 # {"from": name|null, "to": name|null, "length": int}
    largest_army_change: Optional[Dict[str, Any]] = None                 # {"from": name|null, "to": name|null, "size": int}
    completed_trades: List[Dict[str, Any]] = field(default_factory=list) # [{from, to, offer, request}]
    notes: List[str] = field(default_factory=list)
    updated_at: float = 0.0


@dataclass
class ActionRecord:
    """Single action taken by any agent."""
    agent: str              # "strategy"|"development"|"trading"|"risk"
    action: str             # tool name or method name
    args: Dict = field(default_factory=dict)
    result: Dict = field(default_factory=dict)
    success: bool = True
    timestamp: float = 0.0


@dataclass
class AgentMessage:
    """Inter-agent message stored on the scratchpad."""
    from_agent: str
    to_agent: str
    message_type: str       # "request"|"inform"|"query"
    content: Dict = field(default_factory=dict)
    timestamp: float = 0.0


# ── Scratchpad ────────────────────────────────────────────────────

class Scratchpad:
    """
    Thread-safe shared state for the multi-agent system.

    Written by Strategy at turn start (game_state, processed_state, etc.).
    Written by Risk (risk_analysis), Strategy (strategy_plan),
    Trading (trade_state), Development (build_log).
    Append-only logs for actions and inter-agent messages.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # ── Written by Strategy Agent at start of each turn ──
        self.game_state: Dict[str, Any] = {}
        self.processed_state: Dict[str, Any] = {}
        self.state_json: Dict[str, Any] = {}
        self.turn_number: int = 0
        self.phase: str = ""                     # "setup" | "playing"
        self.turn_phase: str = ""                # "roll"|"main"|"discard"|"robber"|"specialBuild"
        self.recent_delta: List[str] = []

        # ── Written by Risk Agent ──
        self.risk_analysis: RiskAnalysis = RiskAnalysis()

        # ── Written by Strategy Agent ──
        self.strategy_plan: StrategyPlan = StrategyPlan()

        # ── Written by Trading Agent ──
        self.trade_state: TradeState = TradeState()

        # ── Written by Strategy Agent (per-turn round diff) ──
        self.round_summary: RoundSummary = RoundSummary()

        # ── Written by Development Agent ──
        self.build_log: List[ActionRecord] = []

        # ── Append-only across all agents ──
        self.action_log: List[ActionRecord] = []
        self.inter_agent_messages: List[AgentMessage] = []

    # ── Game state updates (called by Strategy each tick) ─────────

    def update_game_state(
        self,
        raw_state: Dict[str, Any],
        processor: GameStateProcessor,
    ) -> None:
        """Ingest a new raw server state and recompute processed views."""
        with self._lock:
            self.game_state = raw_state
            self.processed_state = processor.process(raw_state)
            self.phase = raw_state.get("phase", "")
            self.turn_phase = raw_state.get("turnPhase", "")
            self.state_json = self._build_state_json(raw_state, processor)

    # ── Typed writers (each guarded by lock) ──────────────────────

    def write_risk_analysis(self, analysis: RiskAnalysis) -> None:
        with self._lock:
            analysis.updated_at = time.time()
            self.risk_analysis = analysis

    def write_strategy_plan(self, plan: StrategyPlan) -> None:
        with self._lock:
            plan.updated_at = time.time()
            self.strategy_plan = plan

    def write_trade_state(self, state: TradeState) -> None:
        with self._lock:
            state.updated_at = time.time()
            self.trade_state = state

    def write_round_summary(self, summary: RoundSummary) -> None:
        with self._lock:
            summary.updated_at = time.time()
            self.round_summary = summary

    def append_action(self, record: ActionRecord) -> None:
        with self._lock:
            record.timestamp = time.time()
            self.action_log.append(record)

    def append_build_action(self, record: ActionRecord) -> None:
        with self._lock:
            record.timestamp = time.time()
            self.build_log.append(record)
            self.action_log.append(record)

    # ── Inter-agent messaging ─────────────────────────────────────

    def append_message(self, msg: AgentMessage) -> None:
        with self._lock:
            msg.timestamp = time.time()
            self.inter_agent_messages.append(msg)

    def get_messages_for(self, agent_name: str) -> List[AgentMessage]:
        with self._lock:
            return [
                m for m in self.inter_agent_messages
                if m.to_agent == agent_name
            ]

    # ── Read slices for specific agents ───────────────────────────

    def read_for_agent(self, agent_name: str) -> Dict[str, Any]:
        """Return an agent-relevant slice of the scratchpad as a dict."""
        with self._lock:
            base = {
                "game_state": self.game_state,
                "processed_state": self.processed_state,
                "state_json": self.state_json,
                "turn_number": self.turn_number,
                "phase": self.phase,
                "turn_phase": self.turn_phase,
            }

            if agent_name == "strategy":
                base["risk_analysis"] = asdict(self.risk_analysis)
                base["strategy_plan"] = asdict(self.strategy_plan)
                base["trade_state"] = asdict(self.trade_state)
                base["build_log"] = [asdict(a) for a in self.build_log]
                base["round_summary"] = asdict(self.round_summary)
            elif agent_name == "development":
                base["strategy_plan"] = asdict(self.strategy_plan)
                base["risk_analysis"] = asdict(self.risk_analysis)
            elif agent_name == "trading":
                base["strategy_plan"] = asdict(self.strategy_plan)
                base["trade_state"] = asdict(self.trade_state)
            elif agent_name == "risk":
                base["risk_analysis"] = asdict(self.risk_analysis)

            base["messages"] = [
                asdict(m) for m in self.inter_agent_messages
                if m.to_agent == agent_name
            ]
            return base

    # ── Structured JSON for LLM prompts (§7 of TODO) ─────────────

    def to_state_json(self) -> Dict[str, Any]:
        """
        Produce the structured JSON format that all agents share.
        Designed for token-efficient LLM consumption.
        """
        with self._lock:
            return dict(self.state_json)

    def _build_state_json(
        self,
        raw: Dict[str, Any],
        processor: GameStateProcessor,
    ) -> Dict[str, Any]:
        """
        Build the structured state JSON per TODO §7.
        Called inside update_game_state (lock already held).
        """
        processed = self.processed_state
        me = processed.get("me", {})
        opponents = processed.get("opponents", [])

        # Extract dice info
        dice_roll = processed.get("dice_roll")

        # Risk summary (may be empty if Risk hasn't run yet)
        risk = self.risk_analysis
        risk_summary: Dict[str, Any] = {}
        if risk.updated_at > 0:
            risk_summary = {
                "expected_income": risk.resource_expected_income,
                "top_threat": (
                    risk.opponent_threats[0] if risk.opponent_threats else {}
                ),
                "threat_narrative": risk.threat_narrative,
            }

        # Active trade
        trade_offer = processed.get("trade_offer")

        return {
            "meta": {
                "phase": processed.get("phase", ""),
                "turn_phase": processed.get("turn_phase", ""),
                "is_my_turn": processed.get("is_my_turn", False),
                "turn_number": self.turn_number,
                "dice_roll": dice_roll,
            },
            "scoreboard": {
                "my_vp": me.get("victory_points", 0),
                "opponents": [
                    {
                        "name": o.get("name", ""),
                        "vp": o.get("vp", 0),
                        "cards": o.get("total_cards", 0),
                        "knights": o.get("knights_played", 0),
                        "road_len": o.get("road_length", 0),
                    }
                    for o in opponents
                ],
            },
            "my_state": {
                "resources": me.get("resources", {}),
                "dev_cards": me.get("dev_cards", []),
                "pieces_left": {
                    "settlements": me.get("settlements_remaining", 0),
                    "cities": me.get("cities_remaining", 0),
                    "roads": me.get("roads_remaining", 0),
                },
                "trade_ratios": me.get("trade_ratios", {}),
                "buildings": [
                    {
                        "type": b.get("type", ""),
                        "vertex": b.get("vertex", ""),
                        "production": b.get("production", []),
                    }
                    for b in me.get("my_buildings", [])
                ],
            },
            "board_signals": {
                "robber_hex": processed.get("robber_hex", ""),
                "longest_road": processed.get("longest_road", {}),
                "largest_army": processed.get("largest_army", {}),
                "dev_cards_remaining": processed.get("dev_cards_remaining", 0),
            },
            "risk_summary": risk_summary,
            "active_trade": trade_offer,
            "recent_delta": list(self.recent_delta),
        }

    # ── Turn management ───────────────────────────────────────────

    def new_turn(self, turn_number: int) -> None:
        """Reset per-turn data at the start of a new turn."""
        with self._lock:
            self.turn_number = turn_number
            self.build_log = []
            self.recent_delta = []
