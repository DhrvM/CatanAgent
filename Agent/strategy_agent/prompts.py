"""
Prompts and helpers for the Strategy Agent.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


STRATEGY_SYSTEM_PROMPT = """\
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
  - action is one of: "place_settlement", "place_road", "upgrade_to_city", "buy_dev_card", "play_dev_card"
  - target is a vertex/edge key (e.g. "v_0_-1_2") or null for cards
  - priority is an integer (1 = highest)
- trade_policy: {willing_to_give, desperately_need, max_bank_ratio_acceptable, \
should_propose_trades, min_accept_score}
- risk_tolerance: "aggressive" | "moderate" | "conservative"
- reasoning: 2-3 sentences explaining your current strategic thinking

RULES:
- Reassess long_term_goal every turn -- don't stubbornly stick to a losing strategy.
- If an opponent is at 8+ VP, shift to aggressive (block them, steal from them).
- Build queue should be specific: exact vertex keys for settlements/cities, exact edge keys for roads.
- Trade policy must balance urgency with not giving opponents what THEY need.
- Always explain your reasoning so future turns have context.
- Respond with ONLY the JSON object, no other text.
"""

MAX_STRATEGY_STEPS = 8


def build_strategy_context(
    state_json: Dict[str, Any],
    risk_analysis: Dict[str, Any],
    previous_plan: Dict[str, Any],
    agent_messages: List[Dict[str, Any]],
    building_spots: Dict[str, Any],
) -> str:
    """
    Assemble the user message sent to GPT-4o for strategy planning.
    Keeps it structured and token-efficient.
    """
    sections: List[str] = []

    # 1. Game state
    sections.append("## Current Game State")
    sections.append(json.dumps(state_json, indent=2, default=str))

    # 2. Risk analysis
    if risk_analysis and risk_analysis.get("updated_at", 0) > 0:
        sections.append("\n## Risk Analysis")
        sections.append(json.dumps(risk_analysis, indent=2, default=str))
    else:
        sections.append("\n## Risk Analysis\nNo risk data available yet.")

    # 3. Previous plan
    if previous_plan and previous_plan.get("updated_at", 0) > 0:
        sections.append("\n## Previous Strategy Plan")
        sections.append(json.dumps(previous_plan, indent=2, default=str))
    else:
        sections.append("\n## Previous Strategy Plan\nFirst turn — no previous plan.")

    # 4. Agent messages
    if agent_messages:
        sections.append("\n## Messages from Other Agents")
        for msg in agent_messages[-10:]:  # last 10 messages
            sections.append(
                f"- [{msg.get('from_agent', '?')}→{msg.get('to_agent', '?')}] "
                f"({msg.get('message_type', '?')}): "
                f"{json.dumps(msg.get('content', {}), default=str)}"
            )
    
    # 5. Available building spots
    if building_spots:
        sections.append("\n## Available Building Spots")
        sections.append(json.dumps(building_spots, indent=2, default=str))

    sections.append(
        "\n## Instructions\n"
        "Produce your strategy plan as a single JSON object. "
        "Include all fields: long_term_goal, short_term_goals, priority_resources, "
        "build_queue, trade_policy, risk_tolerance, reasoning."
    )

    return "\n".join(sections)