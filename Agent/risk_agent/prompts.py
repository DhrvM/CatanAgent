"""
Prompts and context builders for the Risk Agent (GPT-4o consultant).

The Risk Agent uses GPT-4o for two purposes:
  1. **Narrative generation**: Compact 2-3 sentence threat summary after
     deterministic math analysis (runs every turn via ``analyze()``).
  2. **Consultation**: Strategy asks a specific risk question; Risk builds
     a rich context from the scratchpad and has GPT-4o answer with a
     concrete, actionable recommendation.
  3. **Plan assessment**: Strategy passes a draft plan for Risk to critique
     against the current risk data before committing.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional


# ── System Prompts ────────────────────────────────────────────────

RISK_CONSULTANT_SYSTEM_PROMPT = """\
You are the Risk Consultant for a Settlers of Catan AI multi-agent system.

Your role is to answer risk questions from the Strategy Agent using the
mathematical analysis, board state, and action history provided.  You have
access to:
  - Deterministic probability data (expected income, opponent threats,
    robber impact, building EV, win probabilities)
  - The current game state (resources, VP, board layout)
  - Recent action history (what happened in previous turns)

RULES:
- Be specific: reference player names, VP counts, resource names, and hex
  coordinates.
- Be concise: 3-5 sentences max.  Strategy needs actionable intelligence,
  not essays.
- Always recommend a concrete action or priority (block X, build at Y,
  trade for Z).
- If the question involves comparing options, state the trade-offs clearly
  and pick a winner.
- Never suggest actions outside Catan rules.

Respond in plain English only — no JSON, no markdown code blocks."""


RISK_NARRATIVE_SYSTEM_PROMPT = """\
You are a Catan risk analyst.  Given numerical analysis of the board,
write a concise 2-3 sentence threat summary for the strategy AI.
Focus on: who is closest to winning, which resources are critical,
and one suggested tactical priority (e.g. block a player, upgrade
cities, grab longest road).  Be specific with player names and
numbers.  No JSON — plain English only."""


RISK_PLAN_ASSESSMENT_PROMPT = """\
You are the Risk Consultant for a Settlers of Catan AI.

The Strategy Agent has drafted a plan for this turn.  Your job is to
review it against the current risk data and flag any concerns:
- Does the build queue make sense given resource income and threats?
- Is the trade policy safe (not feeding a leading opponent)?
- Are we ignoring a critical threat (opponent at 8+ VP)?
- Is the long-term goal still viable or should it change?

Respond with:
1. A 1-2 sentence overall assessment (good / risky / needs changes).
2. Up to 3 specific concerns or suggestions, each 1 sentence.

Plain English only — no JSON."""


# ── Context Builders ──────────────────────────────────────────────

def build_narrative_context(analysis_data: Dict[str, Any]) -> str:
    """Build the user message for compact narrative generation."""
    return (
        "Here is the current risk analysis data for a Catan game:\n\n"
        + json.dumps(analysis_data, indent=2, default=str)
    )


def build_consult_context(
    question: str,
    risk_analysis: Dict[str, Any],
    state_json: Dict[str, Any],
    action_log: List[Dict[str, Any]],
) -> str:
    """
    Build the user message for a risk consultation.

    Combines the Strategy Agent's question with all available context
    from the scratchpad.
    """
    sections: List[str] = []

    sections.append(f"## Strategy Agent's Question\n{question}")

    sections.append("\n## Risk Analysis (Deterministic)")
    # Trim bulky fields for token efficiency
    trimmed = dict(risk_analysis)
    trimmed.pop("per_building_income", None)  # too granular
    if "robber_impact" in trimmed:
        trimmed["robber_impact"] = trimmed["robber_impact"][:5]  # top 5
    if "best_settlement_vertices" in trimmed:
        trimmed["best_settlement_vertices"] = trimmed["best_settlement_vertices"][:5]
    sections.append(json.dumps(trimmed, indent=2, default=str))

    sections.append("\n## Current Game State")
    sections.append(json.dumps(state_json, indent=2, default=str))

    if action_log:
        sections.append("\n## Recent Action History (last 10 actions)")
        for entry in action_log[-10:]:
            sections.append(
                f"- [{entry.get('agent', '?')}] {entry.get('action', '?')} "
                f"→ {'OK' if entry.get('success') else 'FAIL'}"
            )

    sections.append(
        "\n## Instructions\n"
        "Answer the Strategy Agent's question using the data above. "
        "Be specific and actionable. 3-5 sentences max."
    )

    return "\n".join(sections)


def build_plan_assessment_context(
    plan: Dict[str, Any],
    risk_analysis: Dict[str, Any],
    state_json: Dict[str, Any],
) -> str:
    """Build the user message for plan critique."""
    sections: List[str] = []

    sections.append("## Draft Strategy Plan")
    sections.append(json.dumps(plan, indent=2, default=str))

    sections.append("\n## Risk Analysis (Deterministic)")
    trimmed = dict(risk_analysis)
    trimmed.pop("per_building_income", None)
    sections.append(json.dumps(trimmed, indent=2, default=str))

    sections.append("\n## Current Game State")
    sections.append(json.dumps(state_json, indent=2, default=str))

    sections.append(
        "\n## Instructions\n"
        "Assess this plan against the risk data. "
        "Flag concerns and suggest improvements. Keep it brief."
    )

    return "\n".join(sections)
