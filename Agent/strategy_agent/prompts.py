"""
Prompts and context builders for the Strategy Agent.

The Strategy Agent runs two kinds of LLM calls per turn:

1. ``plan`` — a single non-ReAct call that produces a ``StrategyPlan`` with
   build queue, trade policy, goals, etc.  Uses the full game state, last
   round summary, previous plan, and compact Catan rules reference as
   context.

2. ``react`` — a bounded ReAct loop during the main phase where the LLM
   chooses which tools to invoke (consultations, delegations, scratchpad
   reads, building spot queries, and finally ``end_turn``).  The initial
   prompt is minimal (turn metadata + the freshly-generated plan); any
   extra data must be pulled via tool calls.

There is also a ``setup`` prompt used once per setup turn to pick a
settlement + road together.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────────────────────────
# Compact Catan rules reference (shared across all Strategy prompts)
# ──────────────────────────────────────────────────────────────────

CATAN_RULES_CHEATSHEET = """\
CATAN RULES REFERENCE (compact):
- WIN: first player to reach 10 VP on their own turn.
- COSTS: Settlement = 1 brick + 1 lumber + 1 wool + 1 grain.
         City      = 3 ore + 2 grain (upgrades an existing settlement, doubles its production).
         Road      = 1 brick + 1 lumber.
         Dev card  = 1 ore + 1 grain + 1 wool.
- VP SOURCES: settlement = 1, city = 2, victory-point dev card = 1,
              longest road = 2 VP (need >=5 contiguous roads),
              largest army = 2 VP (need >=3 knights played).
- DICE: 2d6 each turn; every hex with that number produces for adjacent
        settlements (x1) and cities (x2).  Pips/probabilities:
        6,8 = 5/36 (best); 5,9 = 4/36; 4,10 = 3/36; 3,11 = 2/36; 2,12 = 1/36.
- 7 RULE: any player with >7 cards discards half (rounded down);
          the current player MUST move the robber to a different hex and
          may steal 1 random card from an opponent with a building there.
- ROBBER: blocks production on its hex until moved (by a 7 or a Knight).
- DEV CARDS: max 1 played per turn; CANNOT play one bought this turn;
             Knight may be played BEFORE rolling.  Types:
             * Knight = move robber + (maybe) steal;
             * Road Building = place 2 free roads;
             * Year of Plenty = take any 2 resources from the bank;
             * Monopoly = take ALL of one resource from every opponent;
             * VP cards = +1 VP each (revealed on final turn only).
- TRADING: Strategy only states resource intent; Trading chooses whether to
           use the bank, propose to players, respond/counter, or do nothing.
           Bank trades give N of one resource for 1 of another.  N is dynamic:
           default 4:1, generic port 3:1, specific-resource port 2:1 when our
           buildings grant that port.  Use current tradeRatios/get_trade_options
           as the authority.  Player-to-player trades must be agreed; on our
           turn we may propose, while off-turn we may only respond/counter.
- DISTANCE RULE: settlements cannot be placed adjacent to any other
                 building (minimum 2 edges apart).
- SETUP: snake draft — each player places 2 settlements + 2 roads;
         the second settlement earns 1 card of each adjacent hex's resource.
- LONGEST ROAD: unbroken chain of >=5 of your roads; can be broken by
                an opponent placing a settlement on an intermediate vertex.
"""


# ──────────────────────────────────────────────────────────────────
# Plan-step system prompt (non-ReAct, produces StrategyPlan JSON)
# ──────────────────────────────────────────────────────────────────

STRATEGY_PLAN_SYSTEM_PROMPT = f"""\
You are the Strategy Agent for a Settlers of Catan AI.  You are the brain
of a multi-agent system: you set the plan each turn and other agents
(Risk, Development, Trading) execute pieces of it on your behalf.

{CATAN_RULES_CHEATSHEET}

YOUR PEERS:
- Risk Agent: deterministic probability math + LLM consultations about
  threats, opponents, expected income, best building spots by EV,
  robber targets, win probabilities.
- Development Agent: executes build queues, buys/plays dev cards,
  handles discards and robber movement.
- Trading Agent: proposes and responds to bank / player trades under
  the policy you provide.

YOUR JOB RIGHT NOW (plan step):
Produce a STRATEGY PLAN for THIS turn as a single JSON object with these
fields:
- long_term_goal: one of "cities", "longest_road", "largest_army", "balanced"
- short_term_goals: ordered list of 1-3 immediate objectives for this turn
- priority_resources: ordered list of resources you want most (from
  {{brick, lumber, wool, grain, ore}})
- build_queue: ordered list of {{action, target, priority}} items that
  Development will execute if you tell it to.  Valid actions:
  "place_settlement", "place_road", "upgrade_to_city", "buy_dev_card".
  target is a vertex/edge key (e.g. "v_0_-1_2" or "e_1_0_3") or null
  for buy_dev_card.  priority is an integer (1 = highest).
- trade_policy: object with fields willing_to_give (list of resources),
  desperately_need (list of resources), max_bank_ratio_acceptable (int,
  default 4), should_propose_trades (bool), min_accept_score (float 0-1,
  default 0.5).
- should_trade_first: true if trading should happen before building
  this turn, false otherwise.  (This is a HINT; you can override at
  execution time.)
- risk_tolerance: "aggressive" | "moderate" | "conservative"
- reasoning: 2-3 sentences explaining the plan.

RULES:
- Reassess long_term_goal every turn — don't cling to a losing strategy.
- If any opponent is at 8+ VP, shift aggressive (block them).
- build_queue targets MUST be exact vertex/edge keys from the provided
  Available Building Spots — do NOT invent keys.
- trade_policy should only express resource intent: resources Trading may give,
  resources we need, acceptable bank ratio, whether Trading may act, and accept
  threshold.  Do not prescribe bank-vs-player mechanics; Trading owns that.
- If unsure, prefer empty/conservative fields over hallucinating spots.
- Respond with ONLY the JSON object, no surrounding prose, no markdown.
"""


# ──────────────────────────────────────────────────────────────────
# ReAct-step system prompt (tool-driven execution, must end with end_turn)
# ──────────────────────────────────────────────────────────────────

MAX_REACT_STEPS = 12

STRATEGY_REACT_SYSTEM_PROMPT = f"""\
You are the Strategy Agent for a Settlers of Catan AI, now in EXECUTION mode.

{CATAN_RULES_CHEATSHEET}

YOU HAVE JUST PRODUCED A STRATEGY PLAN for this turn.  The plan is
guidance, not a contract — you may override it if new information
surfaces mid-turn (a failed build, a failed trade, an opponent jumping
to 8+ VP, an incoming counter-offer, etc.).

You execute the turn by calling TOOLS.  There are three families:

1. SCRATCHPAD READERS (cheap, cacheable):
   - get_state              : fresh token-efficient game state JSON
   - get_plan               : the strategy plan you just produced
   - get_risk_analysis      : last computed RiskAnalysis (may be stale)
   - get_round_summary      : diff of what happened since your last turn
   - get_action_log         : recent action records (default last 15)
   - get_inter_agent_messages: messages addressed to Strategy

2. CONSULTANTS & DELEGATIONS (invoke peer agents):
   - analyze_risk           : force a fresh deterministic risk analysis
   - ask_risk(question)     : text consultation from the Risk Agent
   - ask_development(question): text consultation from the Development Agent
   - delegate_build(queue?, action?): hand building work to Development.
       Either pass the full queue (list of {{action,target,priority}}),
       pass a single action ({{action,target,priority?}}) and it will be
       executed alone, or pass nothing to run the current plan's build
       queue as-is.  Returns per-action success/failure.
   - delegate_trade()       : Trading Agent executes one proactive trade
       decision under your current trade_policy, choosing bank trade, player
       proposal, or no trade.  Returns immediately after a bank trade completes,
       a player proposal is posted as pending, or no viable trade is found.

3. DIRECT GAME TOOLS (registry):
   - get_building_spots / get_best_building_spots_ev / get_expected_income
     / get_opponent_threats / get_robber_targets / get_win_probabilities
     / get_trade_options / get_game_summary
   - play_dev_card           : play Monopoly, Year of Plenty, or Road
     Building yourself.  DO NOT play Knight here — let Development handle
     it via the build queue, since Knight triggers the robber flow.
   - year_of_plenty_pick     : pick a resource after playing Year of Plenty
   - end_turn                : CLOSE THE TURN.  You are the ONLY agent that
     can end the turn; every turn must end with this call.

INVARIANTS:
- Every turn starts and ends with Strategy.  YOU must call end_turn
  before yielding to the next player.
- Do NOT call the same read-only tool twice with the same arguments.
- Do NOT re-run analyze_risk unless the board changed materially.
- If delegate_build reports a failure, you may retry with a different
  target, trade for the missing resource, or move on.
- Do NOT call roll_dice — the dice are rolled for you before this loop.

STYLE:
- Prefer decisive tool calls over long explanations.
- Your reasoning is visible; tool calls are what actually happens.
- If a surprise is injected into the conversation as a [SURPRISE] system
  message, read it and adapt before your next tool call.
"""


# ──────────────────────────────────────────────────────────────────
# Setup-phase system prompt (single LLM call picks settlement + road)
# ──────────────────────────────────────────────────────────────────

STRATEGY_SETUP_SYSTEM_PROMPT = f"""\
You are the Strategy Agent for a Settlers of Catan AI, placing pieces
during the SETUP phase.

{CATAN_RULES_CHEATSHEET}

You are placing ONE settlement and ONE adjacent road this setup turn.
Consider:
- The ranked candidate vertices (by expected value, adjusted for resource
  scarcity and diversity).
- The probability pips on adjacent hexes (6/8 best, then 5/9, then 4/10).
- Port access at coastal vertices.
- Blocking or denying key spots from opponents.
- The road must be adjacent to your settlement (edge incident to the
  settlement vertex).

Respond with ONLY a single JSON object:
{{
  "settlement_vertex": "<vertex_key>",
  "road_edge": "<edge_key>",
  "reasoning": "<1-2 sentences>"
}}

Both keys MUST be from the provided candidate lists.  Do NOT invent keys.
"""


# ──────────────────────────────────────────────────────────────────
# Context builders
# ──────────────────────────────────────────────────────────────────

def build_plan_context(
    state_json: Dict[str, Any],
    previous_plan: Dict[str, Any],
    round_summary: Dict[str, Any],
    building_spots: Dict[str, Any],
    agent_messages: Optional[List[Dict[str, Any]]] = None,
    risk_analysis: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Assemble the user message for the plan-step LLM call.

    All sections are optional — only non-empty ones are rendered.
    """
    sections: List[str] = []

    sections.append("## Current Game State")
    sections.append(json.dumps(state_json, indent=2, default=str))

    if round_summary and round_summary.get("updated_at", 0) > 0:
        sections.append("\n## Last Round Summary (diff since my previous turn)")
        sections.append(json.dumps(round_summary, indent=2, default=str))
    else:
        sections.append("\n## Last Round Summary\n(no diff available — first turn or initial round)")

    if previous_plan and previous_plan.get("updated_at", 0) > 0:
        sections.append("\n## Previous Strategy Plan")
        sections.append(json.dumps(previous_plan, indent=2, default=str))
    else:
        sections.append("\n## Previous Strategy Plan\n(first turn — no previous plan)")

    if risk_analysis and risk_analysis.get("updated_at", 0) > 0:
        sections.append("\n## Cached Risk Analysis (may be stale; call analyze_risk in execution if needed)")
        sections.append(json.dumps(risk_analysis, indent=2, default=str))

    if building_spots:
        sections.append("\n## Available Building Spots (legal & ranked)")
        sections.append(json.dumps(building_spots, indent=2, default=str))

    if agent_messages:
        sections.append("\n## Recent Messages from Other Agents")
        for msg in agent_messages[-10:]:
            sections.append(
                f"- [{msg.get('from_agent', '?')} -> {msg.get('to_agent', '?')}] "
                f"({msg.get('message_type', '?')}): "
                f"{json.dumps(msg.get('content', {}), default=str)}"
            )

    sections.append(
        "\n## Instructions\n"
        "Produce your strategy plan as a single JSON object with all "
        "required fields: long_term_goal, short_term_goals, "
        "priority_resources, build_queue, trade_policy, should_trade_first, "
        "risk_tolerance, reasoning."
    )
    return "\n".join(sections)


def build_react_kickoff_context(
    turn_meta: Dict[str, Any],
    plan: Dict[str, Any],
) -> str:
    """
    Minimal bootstrapping message for the ReAct loop.

    Per the agent design, heavy data (state, risk, logs) is pulled via
    tool calls — this context only gives the LLM turn metadata + the
    freshly-generated plan so it can start executing.
    """
    sections: List[str] = []
    sections.append("## Turn Metadata")
    sections.append(json.dumps(turn_meta, indent=2, default=str))
    sections.append("\n## Your Strategy Plan (just produced)")
    sections.append(json.dumps(plan, indent=2, default=str))
    sections.append(
        "\n## Instructions\n"
        "Execute the plan by calling tools.  Call scratchpad readers for "
        "more detail if you need it, call delegate_build / delegate_trade "
        "to hand work to peer agents, then call end_turn when the turn is "
        "complete.  Every turn MUST end with end_turn."
    )
    return "\n".join(sections)


def build_setup_context(
    state_json: Dict[str, Any],
    candidate_vertices: List[Dict[str, Any]],
    candidate_edges_by_vertex: Dict[str, List[str]],
    risk_analysis: Optional[Dict[str, Any]] = None,
) -> str:
    """
    User message for the one-shot setup placement call.

    candidate_vertices is a ranked list of {vertex, score, production}.
    candidate_edges_by_vertex maps each candidate vertex to an ordered
    list of legal incident edges (top first).
    """
    sections: List[str] = []

    sections.append("## Current Game State")
    sections.append(json.dumps(state_json, indent=2, default=str))

    sections.append("\n## Candidate Settlement Vertices (ranked by expected value)")
    sections.append(json.dumps(candidate_vertices, indent=2, default=str))

    sections.append("\n## Candidate Roads per Settlement Vertex")
    sections.append(json.dumps(candidate_edges_by_vertex, indent=2, default=str))

    if risk_analysis and risk_analysis.get("updated_at", 0) > 0:
        sections.append("\n## Cached Risk Analysis")
        sections.append(json.dumps(risk_analysis, indent=2, default=str))

    sections.append(
        "\n## Instructions\n"
        "Pick ONE settlement vertex and ONE road edge (adjacent to that "
        "vertex) from the candidates above.  Respond with only the JSON "
        "object described in the system prompt."
    )
    return "\n".join(sections)


# ──────────────────────────────────────────────────────────────────
# Backwards-compatibility shims (legacy imports)
# ──────────────────────────────────────────────────────────────────
#
# The previous prompts module exposed STRATEGY_SYSTEM_PROMPT,
# MAX_STRATEGY_STEPS, and build_strategy_context().  Keep aliases so
# any external callers (tests, harness) continue to import cleanly.

STRATEGY_SYSTEM_PROMPT = STRATEGY_PLAN_SYSTEM_PROMPT
MAX_STRATEGY_STEPS = MAX_REACT_STEPS


def build_strategy_context(  # pragma: no cover - legacy shim
    state_json: Dict[str, Any],
    risk_analysis: Dict[str, Any],
    previous_plan: Dict[str, Any],
    agent_messages: List[Dict[str, Any]],
    building_spots: Dict[str, Any],
    risk_consultation: Optional[str] = None,
    dev_consultation: Optional[str] = None,
) -> str:
    """Legacy entry point — delegates to build_plan_context."""
    return build_plan_context(
        state_json=state_json,
        previous_plan=previous_plan,
        round_summary={},
        building_spots=building_spots,
        agent_messages=agent_messages,
        risk_analysis=risk_analysis,
    )
