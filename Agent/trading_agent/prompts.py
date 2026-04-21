"""Prompts and helpers for the Trading Agent."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

TRADING_SYSTEM_PROMPT = """\
You are the Trading Agent for a Settlers of Catan AI.

You are a master negotiator in a multi-agent system. Your strategic guidance comes ONLY from
the Strategy Agent (trade policy + plan context below). You choose actions by calling the
provided tools — you do not talk to Development or Risk.

You receive:
1. Strategy trade policy (willing_to_give, desperately_need, bank ratio cap, thresholds)
2. Strategy plan hints (priority_resources, short_term_goals) when available
3. Your trade memory (reputation, recent trades)
4. Current structured game state

PROACTIVE MODE (your turn, main phase only — this invocation):
- Honor Strategy: if should_propose_trades is false, call no trade actions (you may still use
  get_trade_options / get_game_summary to inspect the board).
- Prefer bank trades via get_trade_options then bank_trade when the ratio is acceptable and
  the trade advances Strategy's goals.
- Use propose_trade for player trades when bank is insufficient or inappropriate.
- Use get_trade_offer_status / cancel_trade when you need to inspect or clear your own offer.
- Never knowingly help an opponent at 8+ VP win; avoid giving them finishing resources.

REACTIVE MODE (incoming offer, off-turn — separate invocation):
- Use respond_to_trade or counter_trade only; evaluate against trade_policy.min_accept_score
  and opponent VP.

OUTPUT: Reason briefly, then call tools. Results are reported to Strategy automatically.
"""

TRADING_AWAKE_OFFER_PROMPT = """\
Incoming trade offer while it is NOT your turn (off-turn / "awake" handling).

Semantics: in server state, "offer" is what the proposer gives YOU; "request" is what they want FROM you.

You MUST choose exactly one tool call:
- respond_to_trade with accept=true or accept=false to accept or decline.
- counter_trade only when the deal is close but unfavorable; pass offer and request dicts for YOUR counter
  (what you give / what you want). Keep counters modest (often 1-for-1 or small adjustments).

Do not call propose_trade, bank_trade, or end_turn here — only respond_to_trade or counter_trade.
"""


def build_awake_trade_user_message(
    state_json: Dict[str, Any],
    trade_policy: Dict[str, Any],
    trade_state: Dict[str, Any],
    offer: Dict[str, Any],
) -> str:
    """User message for off-turn LLM trade decisions (Trading Agent awake mode)."""
    sections = [
        TRADING_AWAKE_OFFER_PROMPT,
        "\n## Structured state (JSON)\n",
        json.dumps(state_json, indent=2, default=str),
        "\n## Strategy trade policy\n",
        json.dumps(trade_policy, indent=2, default=str),
        "\n## Trading memory (reputation, recent trades)\n",
        json.dumps(trade_state, indent=2, default=str),
        "\n## Incoming offer (normalized)\n",
        json.dumps(offer, indent=2, default=str),
    ]
    return "\n".join(sections)


def build_proactive_trade_user_message(
    state_json: Dict[str, Any],
    trade_policy: Dict[str, Any],
    trade_state: Dict[str, Any],
    strategy_extras: Optional[Dict[str, Any]] = None,
) -> str:
    """
    First user message for proactive (main-phase) trading — Strategy policy + plan + state.
    """
    extra = strategy_extras or {}
    sections = [
        "## Proactive trading (your turn, main phase)",
        "Use tools to execute Strategy's trade policy. Start by inspecting options if unsure.",
        "\n## Strategy trade policy (from Strategy Agent)\n",
        json.dumps(trade_policy, indent=2, default=str),
        "\n## Strategy plan context (priority goals — align trades with these)\n",
        json.dumps(
            {
                "priority_resources": extra.get("priority_resources", []),
                "short_term_goals": extra.get("short_term_goals", []),
                "long_term_goal": extra.get("long_term_goal", ""),
                "risk_tolerance": extra.get("risk_tolerance", ""),
            },
            indent=2,
            default=str,
        ),
        "\n## Trading memory (reputation, pending offer)\n",
        json.dumps(trade_state, indent=2, default=str),
        "\n## Structured game state\n",
        json.dumps(state_json, indent=2, default=str),
    ]
    return "\n".join(sections)


def build_trading_context(
    state_json: Dict[str, Any],
    trade_policy: Dict[str, Any],
    trade_state: Dict[str, Any],
) -> str:
    """Build a compact trading-focused context string for optional LLM use."""
    sections = [
        "## Current State",
        json.dumps(state_json, indent=2, default=str),
        "\n## Strategy Trade Policy",
        json.dumps(trade_policy, indent=2, default=str),
        "\n## Trading Memory",
        json.dumps(trade_state, indent=2, default=str),
    ]
    return "\n".join(sections)
