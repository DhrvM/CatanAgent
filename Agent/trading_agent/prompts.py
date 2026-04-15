"""Prompts and helpers for the Trading Agent."""

from __future__ import annotations

import json
from typing import Any, Dict

TRADING_SYSTEM_PROMPT = """\
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
