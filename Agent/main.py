#!/usr/bin/env python3
"""
CLI entry point for the Catan Agent.

Supports two modes:
  --mode react  →  Single ReAct agent (default, existing behavior)
  --mode multi  →  Multi-agent system (Strategy + Development + Trading + Risk)

Usage:
    python -m Agent.main --game-code ABCDEF --name ReactBot
    python -m Agent.main --mode multi --game-code ABCDEF --name StrategyBot
"""
from __future__ import annotations

import argparse
import sys
import os

try:
    from dotenv import load_dotenv
    # Explicitly load the .env from the Agent/ folder
    current_dir = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(current_dir, ".env"))
except ImportError:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Catan AI Agent")
    parser.add_argument("--server", default="http://localhost:3001", help="Game server URL")
    parser.add_argument("--game-code", default=None, help="Game code to join (omit to create)")
    parser.add_argument("--name", default="ReactBot", help="Player name")
    parser.add_argument("--model", default="gpt-4o", help="OpenAI model")
    parser.add_argument("--ollama-model", default="qwen3:8b", help="Ollama model for summarization")
    parser.add_argument(
        "--mode", choices=["react", "multi"], default="react",
        help="'react' for single ReAct agent, 'multi' for multi-agent system",
    )
    args = parser.parse_args()

    if args.mode == "react":
        _run_react(args)
    else:
        _run_multi(args)


def _run_react(args) -> None:
    """Launch the single ReAct agent (existing behavior)."""
    from Agent.react_agent.agent import ReactCatanAgent

    agent = ReactCatanAgent(
        server_url=args.server,
        game_code=args.game_code,
        player_name=args.name,
        openai_model=args.model,
        ollama_model=args.ollama_model,
    )
    agent.run()


def _run_multi(args) -> None:
    """
    Launch the multi-agent system.

    Creates all 4 agents, wires peer references per ALLOWED_CHANNELS,
    and starts Strategy's game loop.
    """
    from Agent.shared.scratchpad import Scratchpad
    from Agent.strategy_agent.agent import StrategyAgent
    from Agent.development_agent.agent import DevelopmentAgent
    from Agent.trading_agent.agent import TradingAgent
    from Agent.risk_agent.agent import RiskAgent

    from Agent.utils.socket_client import CatanSocketClient
    from Agent.utils.game_state_processor import GameStateProcessor
    from Agent.utils.openai_client import OpenAIClient
    from Agent.utils.ollama_client import OllamaChat, OllamaConfig
    from Agent.utils.stats_tracker import AgentStatsTracker
    from Agent.tools.registry import build_tool_registry

    # ── Core infrastructure ───────────────────────────────────────
    scratchpad = Scratchpad()
    client = CatanSocketClient(args.server)
    processor = GameStateProcessor()
    registry = build_tool_registry(client, processor)
    stats = AgentStatsTracker(agent_name=args.name)

    # ── LLM clients ───────────────────────────────────────────────
    openai = OpenAIClient(model=args.model)
    ollama = OllamaChat(OllamaConfig(model=args.ollama_model))

    # ── Create agents ─────────────────────────────────────────────
    risk = RiskAgent(scratchpad, ollama)
    strategy = StrategyAgent(
        scratchpad, openai, client, processor, registry, stats,
        game_code=args.game_code,
        player_name=args.name,
    )
    development = DevelopmentAgent(scratchpad, openai, registry)
    trading = TradingAgent(scratchpad, openai, registry)

    # ── Wire peer references (enforced by ALLOWED_CHANNELS) ──────
    strategy.register_peer(risk)
    strategy.register_peer(development)
    strategy.register_peer(trading)

    development.register_peer(strategy)
    development.register_peer(risk)

    risk.register_peer(strategy)
    risk.register_peer(development)

    trading.register_peer(strategy)

    print(f"🔗 Multi-agent system wired:")
    print(f"   Strategy  → peers: {list(strategy._peers.keys())}")
    print(f"   Development → peers: {list(development._peers.keys())}")
    print(f"   Risk      → peers: {list(risk._peers.keys())}")
    print(f"   Trading   → peers: {list(trading._peers.keys())}")

    # ── Strategy owns the game loop ──────────────────────────────
    strategy.run()


if __name__ == "__main__":
    main()
