"""Prompts for the Development Agent (multi-agent Catan)."""

from __future__ import annotations

import json
from typing import Any, Dict, List

DEVELOPMENT_SYSTEM_PROMPT = """\
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

When you call a tool (discard_cards, move_robber, etc.):
- You MUST set the assistant message `content` to at least one clear sentence of reasoning
  before the tool call (why this discard split, or why this hex / steal target).
- Do not leave `content` empty or use only tool_calls without text; our client logs `content` as [thought].
- Do not put JSON, markdown code fences, or resource tables only in `content`. Pass discard/move data
  exclusively through the tool call arguments, not as ```json blocks in the chat message.
"""


def build_discard_user_message(
    state_json: Dict[str, Any],
    priority_resources: List[str],
    discarding_hint: str,
) -> str:
    return (
        "You must choose which resource cards to discard. The server requires a specific count "
        "(usually half your hand, rounded down).\n\n"
        f"Priority resources to KEEP when possible: {priority_resources}\n\n"
        f"Discard requirement / context:\n{discarding_hint}\n\n"
        "## State snapshot\n"
        f"{json.dumps(state_json, indent=2, default=str)}\n\n"
        "Use the discard_cards tool with argument {\"resources\": {\"brick\": n, ...}} — do not paste JSON in the message.\n"
        "Reply with one short sentence in the assistant message explaining your choice, then invoke discard_cards."
    )


def build_robber_user_message(
    state_json: Dict[str, Any],
    risk_targets: List[Dict[str, Any]],
    heuristic_pick: Dict[str, Any],
) -> str:
    return (
        "Move the robber to a valid hex (not the desert, not the robber's current hex). "
        "Optionally set steal_from_player_id to steal from an opponent with a building on that hex.\n\n"
        "## Risk-ranked targets (may be empty)\n"
        f"{json.dumps(risk_targets, indent=2, default=str)}\n\n"
        "## Heuristic suggestion (fallback)\n"
        f"{json.dumps(heuristic_pick, indent=2, default=str)}\n\n"
        "## State snapshot\n"
        f"{json.dumps(state_json, indent=2, default=str)}\n\n"
        "Use steal_from_player_id from player id strings in state (e.g. p1), not display names, when calling move_robber.\n"
        "Reply with one short sentence in the assistant message explaining the move, then invoke move_robber."
    )


# ── Development Consultant Prompts ────────────────────────────────

DEV_CONSULTANT_SYSTEM_PROMPT = """\
You are the Development Consultant for a Settlers of Catan AI multi-agent system.

Your role is to answer building and development questions from the Strategy Agent.
You have access to:
  - The current game state (our resources, pieces remaining, buildings)
  - Building costs (settlement: brick+lumber+wool+grain, city: 3ore+2grain,
    road: brick+lumber, dev card: ore+wool+grain)
  - Available building spots with expected value rankings
  - Risk analysis data (income projections, robber impact)

RULES:
- Be specific: reference vertex keys (e.g. v_0_-1_2), resource names, and costs.
- Be concise: 3-5 sentences max.
- Always recommend a concrete action: "build settlement at X", "upgrade Y to city",
  "save for city upgrade", "buy dev card".
- Consider affordability: check if we have enough resources RIGHT NOW.
- Consider ROI: which building gives the best resource income per cost.
- Never suggest trades — the Trading Agent handles that.

Respond in plain English only — no JSON, no markdown code blocks."""


def build_dev_consult_context(
    question: str,
    state_json: Dict[str, Any],
    resources: Dict[str, int],
    building_spots: Dict[str, Any],
    risk_analysis: Dict[str, Any],
) -> str:
    """Build the user message for a Development consultation."""
    sections: List[str] = []

    sections.append(f"## Strategy Agent's Question\n{question}")

    sections.append("\n## Our Current Resources")
    sections.append(json.dumps(resources, indent=2, default=str))

    sections.append("\n## Building Costs Reference")
    sections.append(json.dumps({
        "settlement": {"brick": 1, "lumber": 1, "wool": 1, "grain": 1},
        "city": {"ore": 3, "grain": 2},
        "road": {"brick": 1, "lumber": 1},
        "dev_card": {"ore": 1, "wool": 1, "grain": 1},
    }, indent=2))

    if building_spots:
        sections.append("\n## Available Building Spots (ranked by EV)")
        sections.append(json.dumps(building_spots, indent=2, default=str))

    # Include relevant risk data (income + best vertices)
    if risk_analysis:
        trimmed = {}
        if "resource_expected_income" in risk_analysis:
            trimmed["current_income"] = risk_analysis["resource_expected_income"]
        if "best_settlement_vertices" in risk_analysis:
            trimmed["best_settlement_spots"] = risk_analysis["best_settlement_vertices"][:5]
        if "best_city_vertices" in risk_analysis:
            trimmed["best_city_upgrades"] = risk_analysis["best_city_vertices"][:5]
        if trimmed:
            sections.append("\n## Income & Building EV Data")
            sections.append(json.dumps(trimmed, indent=2, default=str))

    sections.append("\n## Game State")
    sections.append(json.dumps(state_json, indent=2, default=str))

    sections.append(
        "\n## Instructions\n"
        "Answer the Strategy Agent's building question using the data above. "
        "Be specific about vertex keys, costs, and expected income gains. "
        "3-5 sentences max."
    )

    return "\n".join(sections)
