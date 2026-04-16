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
