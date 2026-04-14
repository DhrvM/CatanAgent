# Multi-Agent Catan System -- Technical Plan

Build a 4-agent cooperative system (Strategy, Development, Trading, Risk) with a shared Scratchpad. No orchestrator -- Strategy is the brain and coordinator. Agents communicate directly with each other per defined channels. Coexists alongside ReactCatanAgent via `--mode` flag.

---

## Architecture Overview

No orchestrator. Strategy is the brain and drives each turn. Agents hold direct references to their allowed peers and invoke each other via method calls. The Scratchpad is the shared memory all agents read/write.

### Communication Rules

- **Strategy** can talk to: Development, Trading, Risk (all agents)
- **Development** can talk to: Strategy, Risk
- **Risk** can talk to: Strategy, Development
- **Trading** can talk to: Strategy only

```
                    ┌──────────────────────┐
                    │      Scratchpad       │
                    │   (Shared Memory)     │
                    └──┬───┬───┬───┬───────┘
                       │   │   │   │
          ┌────────────┘   │   │   └──────────────┐
          │                │   │                   │
  ┌───────▼───────┐  ┌────▼───▼────┐  ┌───────────▼──────────┐
  │  Risk Agent   │  │  Strategy   │  │  Trading Agent       │
  │ (math+Ollama) │◄─┤  Agent      ├─►│  (GPT-4o negotiator) │
  │               │  │ (GPT-4o     │  │  peers: [strategy]   │
  │ peers:        │  │  the brain) │  └──────────────────────┘
  │ [strategy,    │  │ peers: ALL  │
  │  development] │  └──────┬──────┘
  └───────▲───────┘         │
          │           ┌─────▼──────────────┐
          └───────────┤ Development Agent   │
                      │ (GPT-4o builder)    │
                      │ peers: [strategy,   │
                      │         risk]       │
                      └────────────────────┘
```

### Turn Flow (Strategy-driven, no orchestrator)

```
1. Strategy detects it is our turn
2. Strategy writes latest game_state to Scratchpad
3. Strategy calls Risk.analyze()
   └─ Risk runs deterministic math + Ollama threat narrative
   └─ Risk writes RiskAnalysis to Scratchpad
   └─ Risk returns summary to Strategy
4. Strategy calls GPT-4o to produce a StrategyPlan
   └─ Strategy writes StrategyPlan to Scratchpad
5. Phase-specific execution:
   ├─ "roll": Strategy calls roll_dice directly
   ├─ "discard": Strategy delegates to Development.handle_discard()
   ├─ "robber": Strategy delegates to Development.handle_robber()
   │    └─ Development queries Risk.get_robber_targets()
   └─ "main":
        a. Strategy delegates to Trading.proactive_trade()
           └─ Trading calls Strategy.get_trade_policy() for guidance
        b. Strategy delegates to Development.execute_build_queue()
           └─ Development may query Risk.get_best_building_spots_ev()
        c. Strategy calls end_turn directly
6. Off-turn: Strategy detects incoming trade offers
   └─ Strategy delegates to Trading.respond_to_offer()
```

---

## File Structure

New and modified files (all under `Agent/`):

```
Agent/
├── main.py                          ← MODIFY: add --mode flag, wire agents
├── shared/
│   ├── __init__.py                  ← NEW
│   ├── scratchpad.py                ← NEW: Scratchpad class (shared memory)
│   └── base_agent.py                ← NEW: BaseAgent ABC (peer refs, call_agent)
├── strategy_agent/
│   ├── __init__.py                  ← NEW
│   ├── agent.py                     ← NEW: StrategyAgent (brain + game loop)
│   └── prompts.py                   ← NEW (currently stub)
├── development_agent/
│   ├── __init__.py                  ← NEW
│   ├── agent.py                     ← NEW
│   └── prompts.py                   ← NEW
├── trading_agent/
│   ├── __init__.py                  ← NEW
│   ├── agent.py                     ← NEW
│   └── prompts.py                   ← NEW
├── risk_agent/
│   ├── __init__.py                  ← NEW
│   ├── agent.py                     ← NEW (hybrid: math + LLM)
│   └── probabilities.py            ← NEW (deterministic math)
├── tools/
│   ├── risk_tools.py                ← NEW: probability/analysis tools
│   └── registry.py                  ← MODIFY: per-agent tool subsets
└── react_agent/                     ← KEEP: untouched, coexists
```

---

## 1. Shared Infrastructure

### 1a. Scratchpad (`Agent/shared/scratchpad.py`)

Thread-safe shared state. Each agent reads the sections it needs and writes its outputs. The Scratchpad is a Python class wrapping a dict with typed accessors and a `threading.Lock`.

```python
@dataclass
class Scratchpad:
    # --- Written by Strategy Agent at start of each turn ---
    game_state: Dict[str, Any]       # raw latest_state() from socket
    processed_state: Dict[str, Any]  # GameStateProcessor.process() output
    state_json: Dict[str, Any]       # structured JSON for LLM prompts
    turn_number: int
    phase: str                       # "setup" | "playing"
    turn_phase: str                  # "roll" | "main" | "discard" | "robber" | "specialBuild"
    recent_delta: List[str]          # what changed since last turn

    # --- Written by Risk Agent ---
    risk_analysis: RiskAnalysis

    # --- Written by Strategy Agent ---
    strategy_plan: StrategyPlan

    # --- Written by Trading Agent ---
    trade_state: TradeState

    # --- Written by Development Agent ---
    build_log: List[ActionRecord]    # actions executed this turn

    # --- Append-only across all agents ---
    action_log: List[ActionRecord]   # full history of all actions
    inter_agent_messages: List[AgentMessage]  # agent-to-agent messages
```

#### Key methods on Scratchpad

- `update_game_state(raw_state, processor)` -- called by Strategy each tick
- `read_for_agent(agent_name) -> Dict` -- returns agent-relevant slice of scratchpad
- `write_risk_analysis(analysis)` / `write_strategy_plan(plan)` / etc.
- `send_message(from_agent, to_agent, msg_type, content)`
- `get_messages_for(agent_name) -> List[AgentMessage]`
- `append_action(record: ActionRecord)`
- `to_state_json() -> Dict` -- structured JSON format for LLM prompts (replaces `format_for_llm`)

### 1b. BaseAgent (`Agent/shared/base_agent.py`)

Abstract base class that all 4 agents inherit from. Encodes the communication topology.

```python
from abc import ABC, abstractmethod

ALLOWED_CHANNELS = {
    "strategy":    {"development", "trading", "risk"},
    "development": {"strategy", "risk"},
    "risk":        {"strategy", "development"},
    "trading":     {"strategy"},
}

class BaseAgent(ABC):
    def __init__(self, name: str, scratchpad: Scratchpad):
        self.name = name
        self.scratchpad = scratchpad
        self._peers: Dict[str, "BaseAgent"] = {}

    def register_peer(self, agent: "BaseAgent") -> None:
        """Register a peer agent. Enforced at call time."""
        if agent.name in ALLOWED_CHANNELS.get(self.name, set()):
            self._peers[agent.name] = agent

    def call_agent(self, peer_name: str, method: str, **kwargs) -> Any:
        """Call a method on a peer agent. Raises if not an allowed channel."""
        if peer_name not in ALLOWED_CHANNELS.get(self.name, set()):
            raise PermissionError(
                f"{self.name} is not allowed to talk to {peer_name}"
            )
        peer = self._peers.get(peer_name)
        if not peer:
            raise ValueError(f"Peer {peer_name} not registered")
        fn = getattr(peer, method, None)
        if fn is None or not callable(fn):
            raise AttributeError(f"{peer_name} has no method {method}")
        return fn(**kwargs)

    def send_message(self, to: str, msg_type: str, content: Dict) -> None:
        """Write a message to the scratchpad (enforces allowed channels)."""
        if to not in ALLOWED_CHANNELS.get(self.name, set()):
            raise PermissionError(f"{self.name} cannot message {to}")
        self.scratchpad.append_message(AgentMessage(
            from_agent=self.name, to_agent=to,
            message_type=msg_type, content=content,
            timestamp=time.time(),
        ))

    def get_messages(self) -> List[AgentMessage]:
        """Read messages addressed to this agent."""
        return self.scratchpad.get_messages_for(self.name)
```

Usage:
- Strategy calls `self.call_agent("risk", "analyze")` or `self.call_agent("development", "execute_build_queue")`
- Development calls `self.call_agent("risk", "get_robber_targets")` when handling the robber
- Trading calls `self.call_agent("strategy", "get_trade_policy")` for guidance
- If Trading tries `self.call_agent("development", ...)` it raises `PermissionError`

### 1c. Sub-schemas (all defined in `Agent/shared/scratchpad.py`)

```python
@dataclass
class RiskAnalysis:
    resource_expected_income: Dict[str, float]  # {"brick": 1.2, "lumber": 0.8, ...} per turn
    per_building_income: List[Dict]             # [{vertex, resource, pips, expected}]
    opponent_threats: List[Dict]                # [{name, vp, turns_to_win_estimate, threat_level}]
    win_probabilities: Dict[str, float]         # {"me": 0.3, "Alice": 0.4, ...}
    robber_impact: Dict[str, float]             # {hex_key: production_loss}
    best_settlement_vertices: List[Dict]        # [{vertex, expected_value, resources}]
    best_city_vertices: List[Dict]              # [{vertex, expected_value_gain}]
    threat_narrative: str                        # LLM-generated 2-3 sentence threat summary
    updated_at: float

@dataclass
class StrategyPlan:
    long_term_goal: str              # "cities" | "longest_road" | "largest_army" | "balanced"
    short_term_goals: List[str]      # ordered, e.g. ["get ore for city", "block Alice's road"]
    priority_resources: List[str]    # ordered, e.g. ["ore", "grain"]
    build_queue: List[Dict]          # [{action: "upgrade_to_city", target: "v_1_-1_4", priority: 1}]
    trade_policy: TradePolicy
    risk_tolerance: str              # "aggressive" | "moderate" | "conservative"
    reasoning: str                   # Strategy's current thinking (persists across turns)
    updated_at: float

@dataclass
class TradePolicy:
    willing_to_give: List[str]       # resources we can trade away
    desperately_need: List[str]      # resources we'd accept bad deals for
    max_bank_ratio_acceptable: int   # won't bank-trade above this ratio
    should_propose_trades: bool      # whether Trading agent should proactively propose
    min_accept_score: float          # threshold for accepting incoming trades (0-1)

@dataclass
class TradeState:
    recent_trades: List[Dict]              # last N trades (ours and observed)
    pending_offer: Optional[Dict]          # currently active outgoing offer
    player_trade_history: Dict[str, List]  # per-opponent trade log
    player_reputation: Dict[str, float]    # trust scores per opponent (-1 to 1)
    updated_at: float

@dataclass
class ActionRecord:
    agent: str           # "strategy" | "development" | "trading" | "risk"
    action: str          # tool name
    args: Dict
    result: Dict
    success: bool
    timestamp: float

@dataclass
class AgentMessage:
    from_agent: str
    to_agent: str
    message_type: str    # "request" | "inform" | "query"
    content: Dict
    timestamp: float
```

---

## 2. Risk Agent (`Agent/risk_agent/`)

**Type:** Hybrid (deterministic math + Ollama LLM for threat narrative)
**Peers:** Strategy, Development
**LLM:** Ollama qwen3:8b (for threat narrative only; all math is deterministic)

### `probabilities.py` -- Pure Math

Functions (no LLM, no network calls):

- `expected_resource_income(state) -> Dict[str, float]`: For each of our buildings, sum `pips/36 * multiplier` (city=2x) per resource. Returns `{"brick": 1.2, "lumber": 0.8, ...}`.
- `per_building_income(state) -> List[Dict]`: Breakdown per building: `[{vertex, resource, number_token, pips, expected_per_turn}]`.
- `opponent_threat_assessment(state) -> List[Dict]`: For each opponent, estimate threat level from VP, road_length delta to longest road, knights to largest army, total cards, dev cards.
- `estimate_turns_to_win(vp, income, buildings_left) -> int`: Rough estimate of how many turns until a player reaches 10 VP given current income rate.
- `robber_impact_analysis(state) -> Dict[str, float]`: For each hex, calculate how much production the leading *opponent* loses if robber is placed there. Higher = better target.
- `rank_vertices_by_expected_value(state) -> List[Dict]`: Rank all legal settlement spots by `sum(pips * resource_weight)` where resource_weight is informed by what we lack.
- `rank_cities_by_value_gain(state) -> List[Dict]`: Rank our settlements by how much upgrading to city increases expected income.
- `win_probability_estimate(state) -> Dict[str, float]`: Simple heuristic: weighted combination of VP, income rate, dev cards, road/army progress.

**Important:** These functions consume the raw game state dict (same shape as `CatanSocketClient.latest_state()`). Key fields used:
- `state["players"]` -- list of player dicts
- `state["myIndex"]` -- our index
- `state["hexes"]` -- dict of hex_key -> {q, r, resource, number/token}
- `state["vertices"]` -- dict of vertex_key -> {owner, building}
- `state["edges"]` -- dict of edge_key -> {owner, road}
- `state["robber"]` -- current robber hex key ("q,r")
- `state["tradeRatios"]` -- dict of resource -> ratio

Vertex keys follow the pattern `v_{q}_{r}_{dir}` (dir 0-5). Edge keys: `e_{q}_{r}_{dir}`.
The `_adjacent_hex_coords(q, r, d)` helper in `Agent/utils/game_state_processor.py` maps a vertex to its adjacent hexes.

### `agent.py` -- RiskAgent class

```python
class RiskAgent(BaseAgent):
    def __init__(self, scratchpad, ollama):
        super().__init__("risk", scratchpad)
        self.ollama = ollama  # OllamaChat instance (qwen3:8b)

    def analyze(self) -> RiskAnalysis:
        """Run all deterministic calculations, then generate threat narrative.
        Called by Strategy at the start of each turn via call_agent("risk", "analyze")."""
        state = self.scratchpad.game_state
        analysis = RiskAnalysis(
            resource_expected_income=expected_resource_income(state),
            per_building_income=per_building_income(state),
            opponent_threats=opponent_threat_assessment(state),
            win_probabilities=win_probability_estimate(state),
            robber_impact=robber_impact_analysis(state),
            best_settlement_vertices=rank_vertices_by_expected_value(state),
            best_city_vertices=rank_cities_by_value_gain(state),
            threat_narrative=self._generate_narrative(...)  # Ollama call
        )
        self.scratchpad.write_risk_analysis(analysis)
        return analysis

    # Methods callable by peers (Strategy and Development):

    def get_robber_targets(self) -> List[Dict]:
        """Called by Development when handling robber phase.
        Returns hexes ranked by damage to opponents."""
        state = self.scratchpad.game_state
        return robber_impact_analysis(state)

    def get_best_building_spots_ev(self, building_type="settlement") -> List[Dict]:
        """Called by Development when choosing where to build."""
        state = self.scratchpad.game_state
        if building_type == "city":
            return rank_cities_by_value_gain(state)
        return rank_vertices_by_expected_value(state)

    def get_opponent_threat_summary(self) -> List[Dict]:
        """Called by Strategy for quick threat check."""
        return self.scratchpad.risk_analysis.opponent_threats

    def _generate_narrative(self, analysis_data: Dict) -> str:
        """Send math results to Ollama qwen3:8b for a 2-3 sentence threat summary."""
        # Uses self.ollama.chat() with json_only=False
        # System prompt: "Summarize this risk analysis in 2-3 sentences for a Catan AI."
        ...
```

### Risk Agent Tools (new `Agent/tools/risk_tools.py`)

These are registered in the ToolRegistry so agents with tool access can also query risk data:

| Tool | Description | Phases |
|---|---|---|
| `get_expected_income` | Expected resources per turn from current buildings | all |
| `get_opponent_threats` | Ranked opponent threat list with VP and estimates | all |
| `get_robber_targets` | Hexes ranked by damage to opponents | robber, main |
| `get_best_building_spots_ev` | Vertices ranked by expected value (not just pips) | main, setup |
| `get_win_probabilities` | Estimated win chance per player | all |

---

## 3. Strategy Agent (`Agent/strategy_agent/`)

**Type:** GPT-4o with function-calling
**Peers:** Development, Trading, Risk (all agents)
**Role:** The brain AND the game loop owner. Strategy owns `run()`, the main polling loop. Each turn it updates the scratchpad, calls Risk for analysis, produces a plan via GPT-4o, and delegates execution to Development/Trading. It directly handles `roll_dice` and `end_turn` since those are trivial control flow.

### `prompts.py`

```python
STRATEGY_SYSTEM_PROMPT = """
You are the Strategy Agent for a Settlers of Catan AI.

You are the brain of a multi-agent system. You receive:
1. The current game state (structured JSON)
2. Risk analysis (probabilities, threats, expected income)
3. Your previous strategy plan (for continuity)
4. Messages from other agents (trade results, build results)

Your job is to produce a STRATEGY PLAN as a JSON object with these fields:
- long_term_goal: one of "cities", "longest_road", "largest_army", "balanced"
- short_term_goals: ordered list of 1-3 immediate objectives
- priority_resources: ordered list of resources you need most
- build_queue: ordered list of {action, target, priority} for Development agent
- trade_policy: {willing_to_give, desperately_need, max_bank_ratio_acceptable,
                  should_propose_trades, min_accept_score}
- risk_tolerance: "aggressive" | "moderate" | "conservative"
- reasoning: 2-3 sentences explaining your current strategic thinking

RULES:
- Reassess long_term_goal every turn -- don't stubbornly stick to a losing strategy.
- If an opponent is at 8+ VP, shift to aggressive (block them, steal from them).
- Build queue should be specific: exact vertex keys for settlements/cities, exact edge keys for roads.
- Trade policy must balance urgency with not giving opponents what THEY need.
- Always explain your reasoning so future turns have context.
"""
```

### `agent.py` -- StrategyAgent class

```python
class StrategyAgent(BaseAgent):
    def __init__(self, scratchpad, openai, client, processor, registry, stats):
        super().__init__("strategy", scratchpad)
        self.openai = openai         # OpenAIClient instance
        self.client = client         # CatanSocketClient (for run loop + roll/end)
        self.processor = processor   # GameStateProcessor
        self.registry = registry     # ToolRegistry
        self.stats = stats           # AgentStatsTracker
        self._turn_counter = 0

    # ── The main game loop (lives here, not in an orchestrator) ──

    def run(self) -> None:
        """Connect, join, poll forever. This IS the entry point."""
        self.client.connect()
        # ... join/create game (same as ReactCatanAgent.run) ...
        while True:
            state = self.client.latest_state()
            if not state:
                time.sleep(0.25); continue

            if not is_my_turn(state):
                self._check_reactive_trades(state)
                time.sleep(0.25); continue

            if state.get("phase") == "setup":
                self._handle_setup(state)  # reuse existing heuristics from game_tools.py
                continue

            self._run_turn(state)

    def _run_turn(self, state) -> None:
        """One full turn: observe -> risk -> plan -> delegate -> end."""
        self._turn_counter += 1
        self.scratchpad.update_game_state(state, self.processor)

        # 1. Ask Risk for analysis
        self.call_agent("risk", "analyze")

        # 2. Plan (GPT-4o)
        self._plan()

        # 3. Execute by phase
        turn_phase = state.get("turnPhase")

        if turn_phase == "roll":
            self.registry.execute("roll_dice", {})
            state = self.client.wait_for_state()
            self.scratchpad.update_game_state(state, self.processor)
            turn_phase = state.get("turnPhase")

        if turn_phase == "discard":
            self.call_agent("development", "handle_discard")
            return

        if turn_phase == "robber":
            self.call_agent("development", "handle_robber")
            return

        if turn_phase == "main":
            self.call_agent("trading", "proactive_trade")
            self.call_agent("development", "execute_build_queue")
            self.registry.execute("end_turn", {})

    def _plan(self) -> None:
        """Call GPT-4o to produce a StrategyPlan."""
        messages = [
            {"role": "system", "content": STRATEGY_SYSTEM_PROMPT},
            {"role": "user", "content": self._build_context()},
        ]
        response = self.openai.chat_with_tools(messages, tools=self._query_tools())
        plan = self._parse_plan(response)
        self.scratchpad.write_strategy_plan(plan)

    def _check_reactive_trades(self, state) -> None:
        """Even off-turn, respond to incoming trade offers."""
        if state and self._has_trade_offer(state):
            self.scratchpad.update_game_state(state, self.processor)
            self.call_agent("trading", "respond_to_offer")

    # ── Methods that other agents call on Strategy ──

    def get_trade_policy(self) -> TradePolicy:
        """Called by Trading agent to get current trade guidance."""
        return self.scratchpad.strategy_plan.trade_policy

    def report_build_results(self, results: List[ActionRecord]) -> None:
        """Called by Development agent after executing build queue."""
        # Strategy can adjust plan mid-turn if builds failed
        ...

    def report_trade_results(self, results: Dict) -> None:
        """Called by Trading agent after completing trades."""
        # Strategy may re-plan if key resources were acquired
        ...
```

**Tools available to Strategy agent** (read-only query tools + `roll_dice` + `end_turn`):
- `roll_dice` (#1), `end_turn` (#12) -- trivial control flow
- `get_game_summary` (#15), `get_building_spots` (#13), `get_trade_options` (#14)
- `get_expected_income`, `get_opponent_threats`, `get_win_probabilities` (new, from risk_tools)

Strategy does NOT have building/trading action tools. It delegates those to Development/Trading.

---

## 4. Development Agent (`Agent/development_agent/`)

**Type:** GPT-4o with function-calling
**Peers:** Strategy (receives build queue, reports results), Risk (queries robber targets, building EV)

**Role:** Executor for building, dev cards, discard, and robber. Reads `strategy_plan.build_queue` from scratchpad and executes actions in order. Uses LLM for tactical decisions (e.g., which dev card to play, monopoly resource choice).

### `prompts.py`

```python
DEVELOPMENT_SYSTEM_PROMPT = """
You are the Development Agent for a Settlers of Catan AI.

You are an executor in a multi-agent system. You receive:
1. A build queue from the Strategy Agent (ordered list of actions to take)
2. Risk analysis data (you can query Risk Agent for robber targets, building EV)
3. The current game state
4. Your available tools

Your job is to execute the build queue in order:
- Try each action. If it fails (not enough resources, illegal placement), skip and try next.
- After building, check if you can buy development cards (if Strategy recommends it).
- For discard phase: keep resources that align with Strategy's priority_resources.
- For robber phase: ask Risk Agent for robber targets, place on the hex that
  maximizes damage to the leading opponent.
- Report all results back to Strategy via send_message().

RULES:
- Follow Strategy's build queue order unless an action is clearly impossible.
- When playing dev cards, consider: knight before rolling, monopoly when opponents have many of target resource.
- Do NOT end the turn -- Strategy handles that.
- Do NOT initiate trades -- Trading agent handles that.
"""
```

### `agent.py` -- DevelopmentAgent class

```python
class DevelopmentAgent(BaseAgent):
    def __init__(self, scratchpad, openai, registry):
        super().__init__("development", scratchpad)
        self.openai = openai       # OpenAIClient instance
        self.registry = registry   # ToolRegistry

    def execute_build_queue(self) -> List[ActionRecord]:
        """Execute Strategy's build queue using tools.
        Called by Strategy via call_agent("development", "execute_build_queue")."""
        build_queue = self.scratchpad.strategy_plan.build_queue
        results = []
        for item in build_queue:
            result = self.registry.execute(item["action"], item.get("args", {}))
            record = ActionRecord(agent="development", action=item["action"], ...)
            results.append(record)
            self.scratchpad.append_action(record)
        self.send_message("strategy", "inform", {"build_results": results})
        return results

    def handle_discard(self) -> None:
        """Discard cards guided by strategy_plan.priority_resources.
        Called by Strategy via call_agent("development", "handle_discard")."""
        # Keeps resources in priority_resources, discards excess of others
        # Uses GPT-4o for nuanced discard choices
        ...

    def handle_robber(self) -> None:
        """Place robber using Risk Agent data + strategy guidance.
        Called by Strategy via call_agent("development", "handle_robber")."""
        targets = self.call_agent("risk", "get_robber_targets")
        # Use GPT-4o to pick final hex + steal target
        ...
        self.send_message("strategy", "inform", {"robber_result": result})
```

**Tools available to Development agent** (action + query):
- `place_settlement` (#2), `place_road` (#3), `upgrade_to_city` (#4)
- `buy_dev_card` (#5), `play_dev_card` (#6)
- `discard_cards` (#10), `move_robber` (#11)
- `get_building_spots` (#13), `get_game_summary` (#15)
- `year_of_plenty_pick` (#17)
- `get_robber_targets`, `get_best_building_spots_ev` (new, via Risk agent or risk_tools)

---

## 5. Trading Agent (`Agent/trading_agent/`)

**Type:** GPT-4o with function-calling
**Peers:** Strategy only (receives trade_policy, reports trade results)

**Role:** Master negotiator. Two modes: **proactive** (proposes trades when Strategy says we need resources) and **reactive** (responds to incoming offers). Cannot talk to Development or Risk directly -- all strategic guidance comes from Strategy.

### `prompts.py`

```python
TRADING_SYSTEM_PROMPT = """
You are the Trading Agent for a Settlers of Catan AI.

You are a master negotiator in a multi-agent system. You receive:
1. The Strategy Agent's trade policy (what to give, what we need, thresholds)
2. Your own trade history with each opponent (reputation scores you maintain)
3. Current game state (who has how many cards, VPs)

PROACTIVE MODE (your turn, main phase):
- Check if Strategy says should_propose_trades=true
- Propose trades that get us priority_resources
- Try bank trades first if ratio is 3:1 or better
- Only propose player trades if bank trades are insufficient
- Never give an opponent a resource that would let them win
- Consider opponent card counts -- they can't trade what they don't have

REACTIVE MODE (incoming offer, may be off-turn):
- Evaluate against trade_policy.min_accept_score
- Score the trade: +points for getting priority_resources, -points for giving away priority_resources
- Consider opponent's VP: reject trades that help a player at 8+ VP
- Update your reputation scores based on trade outcomes

OUTPUT: After each action, report results back to Strategy via send_message().
"""
```

### `agent.py` -- TradingAgent class

```python
class TradingAgent(BaseAgent):
    def __init__(self, scratchpad, openai, registry):
        super().__init__("trading", scratchpad)
        self.openai = openai       # OpenAIClient instance
        self.registry = registry   # ToolRegistry
        self.trade_state = TradeState(...)  # maintains own reputation data

    def proactive_trade(self) -> None:
        """Propose trades if Strategy recommends it.
        Called by Strategy via call_agent("trading", "proactive_trade")."""
        policy = self.call_agent("strategy", "get_trade_policy")
        if not policy.should_propose_trades:
            return
        # Tries bank trades first (using get_trade_options)
        # Then proposes player trades if needed via GPT-4o
        ...
        self.send_message("strategy", "inform", {"trade_results": results})

    def respond_to_offer(self) -> None:
        """Evaluate and respond to an incoming trade offer.
        Called by Strategy via call_agent("trading", "respond_to_offer")."""
        policy = self.call_agent("strategy", "get_trade_policy")
        # Reads trade_policy, opponent VPs, reputation scores
        # Calls GPT-4o for decision
        ...
        self.send_message("strategy", "inform", {"trade_response": result})

    def _score_trade(self, offer, request, trade_policy) -> float:
        """Deterministic pre-filter before LLM decision."""
        # Quick math: is this trade aligned with our needs?
```

**Tools available to Trading agent:**
- `bank_trade` (#7), `propose_trade` (#8), `respond_to_trade` (#9)
- `get_trade_options` (#14), `get_game_summary` (#15)
- `get_trading_context` (from `Agent/tools/trading_tools.py`)
- `get_bank_trade_options` (from `Agent/tools/trading_tools.py`)

---

## 6. Entry Point & Agent Wiring (`Agent/main.py`)

No orchestrator class. `main.py` creates all 4 agents, wires their peer references, and calls `strategy.run()`.

Add `--mode` flag:

```python
parser.add_argument("--mode", choices=["react", "multi"], default="react",
                    help="'react' for single ReAct agent, 'multi' for multi-agent system")
```

When `--mode multi`:

```python
from Agent.shared.scratchpad import Scratchpad
from Agent.strategy_agent.agent import StrategyAgent
from Agent.development_agent.agent import DevelopmentAgent
from Agent.trading_agent.agent import TradingAgent
from Agent.risk_agent.agent import RiskAgent

scratchpad = Scratchpad()
client = CatanSocketClient(args.server)
processor = GameStateProcessor()
registry = build_tool_registry(client, processor)
stats = AgentStatsTracker(agent_name=args.name)

openai = OpenAIClient(model=args.model)
ollama = OllamaChat(OllamaConfig(model=args.ollama_model))

# Create agents
risk        = RiskAgent(scratchpad, ollama)
strategy    = StrategyAgent(scratchpad, openai, client, processor, registry, stats)
development = DevelopmentAgent(scratchpad, openai, registry)
trading     = TradingAgent(scratchpad, openai, registry)

# Wire peer references (enforced by ALLOWED_CHANNELS)
strategy.register_peer(risk)
strategy.register_peer(development)
strategy.register_peer(trading)

development.register_peer(strategy)
development.register_peer(risk)

risk.register_peer(strategy)
risk.register_peer(development)

trading.register_peer(strategy)

# Strategy owns the game loop
strategy.run()
```

---

## 7. Game State Format for LLM (`Scratchpad.to_state_json`)

Replace the current `format_for_llm` plain-text with structured JSON that all agents share. The `to_state_json()` method on Scratchpad produces:

```json
{
    "meta": {"phase": "playing", "turn_phase": "main", "is_my_turn": true, "turn_number": 14, "dice_roll": 8},
    "scoreboard": {"my_vp": 5, "opponents": [{"name": "Alice", "vp": 7, "cards": 8, "knights": 2, "road_len": 7}]},
    "my_state": {
        "resources": {"brick": 2, "lumber": 1, "wool": 3, "grain": 4, "ore": 1},
        "dev_cards": ["knight", "monopoly"],
        "pieces_left": {"settlements": 2, "cities": 3, "roads": 9},
        "trade_ratios": {"brick": 4, "lumber": 3, "wool": 4, "grain": 4, "ore": 2},
        "buildings": [{"type": "settlement", "vertex": "v_0_-1_2", "production": ["lumber:11", "grain:10"]}]
    },
    "board_signals": {
        "robber_hex": "1,-2",
        "longest_road": {"holder": "Alice", "length": 8},
        "largest_army": {"holder": null, "size": 0},
        "dev_cards_remaining": 12
    },
    "risk_summary": {
        "expected_income": {"brick": 1.2, "lumber": 0.8, "wool": 0.6, "grain": 1.5, "ore": 0.9},
        "top_threat": {"name": "Alice", "vp": 7, "threat_level": "high"},
        "threat_narrative": "Alice is 3 VP from winning with strong ore/grain income..."
    },
    "active_trade": null,
    "recent_delta": ["Rolled 8, gained +1 grain +1 ore", "Opponent Alice built road"]
}
```

Each agent receives this JSON plus its agent-specific context (e.g., Strategy gets full risk_analysis, Trading gets trade_policy + reputation scores).

---

## 8. New Tools: `risk_tools.py`

Register 5 new tools in the registry, backed by `risk_agent/probabilities.py`:

| Tool | Description | Phases |
|---|---|---|
| `get_expected_income` | Expected resources per turn from current buildings | all |
| `get_opponent_threats` | Ranked opponent threat list with VP and estimates | all |
| `get_robber_targets` | Hexes ranked by damage to opponents | robber, main |
| `get_best_building_spots_ev` | Vertices ranked by expected value (not just pips) | main, setup |
| `get_win_probabilities` | Estimated win chance per player | all |

---

## 9. Registry Update (`Agent/tools/registry.py`)

Modify `get_openai_schemas` to accept an `agent_filter` parameter:

```python
def get_openai_schemas(self, phase_filter=None, agent_filter=None) -> List[Dict]:
```

Each `ToolDefinition` gets a new `agents` field (list of agent names that can use it). When `agent_filter` is set, only tools where `agent_filter in tool.agents` are returned.

Tool-to-agent mapping:

| Agent | Tools |
|---|---|
| strategy | roll_dice, end_turn, get_game_summary, get_building_spots, get_trade_options, get_expected_income, get_opponent_threats, get_win_probabilities |
| development | place_settlement, place_road, upgrade_to_city, buy_dev_card, play_dev_card, discard_cards, move_robber, get_building_spots, get_game_summary, year_of_plenty_pick, get_robber_targets, get_best_building_spots_ev |
| trading | bank_trade, propose_trade, respond_to_trade, get_trade_options, get_game_summary |
| risk | (no tool-calling -- Risk uses direct function calls from probabilities.py) |

---

## 10. Implementation Order

Build order (dependencies flow downward):

1. `shared/scratchpad.py` + `shared/base_agent.py` (foundation, no deps)
2. `risk_agent/probabilities.py` (pure math, no deps)
3. `risk_agent/agent.py` (depends on scratchpad + probabilities)
4. `tools/risk_tools.py` + `registry.py` update (depends on probabilities)
5. `strategy_agent/agent.py` + `prompts.py` (depends on scratchpad, risk, registry)
6. `development_agent/agent.py` + `prompts.py` (depends on scratchpad, registry, risk)
7. `trading_agent/agent.py` + `prompts.py` (depends on scratchpad, registry)
8. `main.py` update (wires everything together)

Steps 6 and 7 (Development and Trading) can be built in parallel since they don't depend on each other.

---

## Integration Checklist for Team Members

### If you are building the **Trading Agent**:

1. **Inherit from `BaseAgent`** (`from Agent.shared.base_agent import BaseAgent`)
2. **Your `__init__` signature:** `TradingAgent(scratchpad, openai, registry)` -- call `super().__init__("trading", scratchpad)`
3. **You can only talk to Strategy.** Use `self.call_agent("strategy", "get_trade_policy")` to get trade guidance. Any call to `"development"` or `"risk"` will raise `PermissionError`.
4. **You must implement these methods** (called by Strategy):
   - `proactive_trade() -> None` -- Strategy calls this during main phase
   - `respond_to_offer() -> None` -- Strategy calls this when an incoming trade is detected (may be off-turn)
5. **Report results back** via `self.send_message("strategy", "inform", {"trade_results": ...})` or by calling `self.call_agent("strategy", "report_trade_results", results=...)`
6. **Read game state** from `self.scratchpad.state_json` or `self.scratchpad.game_state`
7. **Read trade policy** from Strategy: `policy = self.call_agent("strategy", "get_trade_policy")` returns a `TradePolicy` dataclass
8. **Your tools** (from registry): `bank_trade`, `propose_trade`, `respond_to_trade`, `get_trade_options`, `get_game_summary`. Get schemas via `self.registry.get_openai_schemas(agent_filter="trading")`
9. **Maintain your own `TradeState`** (reputation scores, trade history) -- write it to `self.scratchpad.write_trade_state(...)`
10. **Existing code you can reuse**: `Agent/tools/trading_tools.py` has `get_trading_context()`, `get_bank_trade_options()`, `execute_bank_trade()` etc. that already work with the socket client

### If you are building the **Risk Agent**:

1. **Inherit from `BaseAgent`** (`from Agent.shared.base_agent import BaseAgent`)
2. **Your `__init__` signature:** `RiskAgent(scratchpad, ollama)` -- call `super().__init__("risk", scratchpad)`
3. **You can talk to Strategy and Development.** Use `self.call_agent("strategy", ...)` or `self.call_agent("development", ...)`. Any call to `"trading"` will raise `PermissionError`.
4. **You must implement these methods** (called by peers):
   - `analyze() -> RiskAnalysis` -- Strategy calls this at start of every turn. Run all math, generate Ollama narrative, write to scratchpad, return result.
   - `get_robber_targets() -> List[Dict]` -- Development calls this during robber phase
   - `get_best_building_spots_ev(building_type="settlement") -> List[Dict]` -- Development calls this when choosing build locations
   - `get_opponent_threat_summary() -> List[Dict]` -- Strategy calls this for quick checks
5. **Write results to scratchpad** via `self.scratchpad.write_risk_analysis(analysis)`
6. **Read game state** from `self.scratchpad.game_state` (raw dict from socket)
7. **probabilities.py** is your pure-math module -- no LLM, no network. Only `_generate_narrative()` in `agent.py` calls Ollama.
8. **Game state format reference**: see `Agent/utils/game_state_processor.py` for how the server state is structured. Key geometry helpers: `_parse_vertex_key(vk)` returns `(q, r, dir)`, `_adjacent_hex_coords(q, r, d)` returns neighboring hex coords.
9. **Existing heuristic code you can reference**: `Agent/tools/game_tools.py` has `_score_vertex_for_setup()`, `_score_vertex_for_city()`, `_pip_value()`, `_adjacent_hexes_for_vertex()` -- similar logic but yours should use expected-value math (pips/36) rather than raw pip scores.
10. **You do NOT use tools from ToolRegistry.** Your calculations are direct Python function calls. However, 5 tools in `risk_tools.py` will wrap your functions so other agents can access them through the registry if needed.
