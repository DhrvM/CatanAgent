#!/usr/bin/env python3
"""
CLI entry point for the Catan Agent.

Supports modes:
  --mode react    →  Single ReAct agent (default)
  --mode multi    →  Multi-agent system (Strategy + Development + Trading + Risk)
  --mode benchmark → Deterministic benchmark-calibration agent
  --mode harness  →  Offline agent harness: Trading + Development scenarios

Usage:
    # Local server (default)
    python -m Agent.main --game-code ABCDEF --name ReactBot
    python -m Agent.main --mode multi --game-code ABCDEF --name StrategyBot
    python -m Agent.main --mode multi --strategy-model gpt-5 --game-code ABCDEF

    # Hosted server on Render (https://catanagent.onrender.com)
    python -m Agent.main --prod --game-code ABCDEF --name ReactBot
    python -m Agent.main --mode multi --prod --game-code ABCDEF --name StrategyBot

    # Explicit server URL
    python -m Agent.main --server https://my-catan.example.com --game-code ABCDEF

    # Or set CATAN_SERVER_URL in Agent/.env and omit --server / --prod.

    # Harness
    python -m Agent.harness.lab
    python -m Agent.main --mode harness --harness-auto
    python -m Agent.main --mode harness --harness-mock --harness-auto   # no API key
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


def _configure_console_output() -> None:
    """Avoid Windows cp1252 crashes when agent logs include arrows or symbols."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_configure_console_output()


LOCAL_SERVER_URL = "http://localhost:3001"
HOSTED_SERVER_URL = "https://catanagent.onrender.com"


def _resolve_server_url(cli_value: str | None, prod_flag: bool) -> str:
    """
    Resolve the game-server URL with the following precedence:
      1. Explicit --server argument.
      2. --prod flag → hosted Render server.
      3. CATAN_SERVER_URL environment variable.
      4. Default to local dev server.
    """
    if cli_value:
        return cli_value
    if prod_flag:
        return HOSTED_SERVER_URL
    env_url = os.environ.get("CATAN_SERVER_URL")
    if env_url:
        return env_url
    return LOCAL_SERVER_URL


def main() -> None:
    parser = argparse.ArgumentParser(description="Catan AI Agent")
    parser.add_argument(
        "--server",
        default=None,
        help=(
            "Game server URL. Overrides --prod and CATAN_SERVER_URL. "
            f"Defaults to CATAN_SERVER_URL env var or {LOCAL_SERVER_URL}."
        ),
    )
    parser.add_argument(
        "--prod",
        action="store_true",
        help=f"Shortcut for --server {HOSTED_SERVER_URL} (the hosted Render server).",
    )
    parser.add_argument("--game-code", default=None, help="Game code to join (omit to create)")
    parser.add_argument("--name", default="ReactBot", help="Player name")
    parser.add_argument("--model", default="gpt-4o", help="OpenAI model for non-Strategy agents and react mode")
    parser.add_argument("--strategy-model", default="gpt-5", help="OpenAI model for Strategy Agent in multi-agent mode")
    parser.add_argument("--ollama-model", default="qwen3:8b", help="Ollama model for summarization")
    parser.add_argument(
        "--mode", choices=["react", "multi", "benchmark", "harness"], default="react",
        help=(
            "'react' single agent | 'multi' multi-agent | "
            "'benchmark' deterministic calibration agent | "
            "'harness' offline agent harness (Trading + Development)"
        ),
    )
    parser.add_argument(
        "--harness-auto",
        action="store_true",
        help="With --mode harness: run all checks non-interactively and exit 1 if any fail",
    )
    parser.add_argument(
        "--harness-mock",
        action="store_true",
        help="With --mode harness: use HarnessOpenAI stub (no OPENAI_API_KEY)",
    )
    args = parser.parse_args()

    args.server = _resolve_server_url(args.server, args.prod)
    if args.mode != "harness":
        print(f"[main] Connecting to game server: {args.server}")

    if args.mode == "harness":
        from argparse import Namespace

        from Agent.harness import lab as harness_lab

        lab_args = Namespace(
            mock=args.harness_mock,
            model=os.environ.get("HARNESS_MODEL") or args.model,
        )
        harness_lab._load_env()
        if not lab_args.mock:
            try:
                harness_lab.configure_harness(use_mock=False, model=lab_args.model)
            except ValueError as e:
                print(f"\n{e}", file=sys.stderr)
                print(
                    "\n  Set OPENAI_API_KEY in Agent/.env, use --harness-mock, or export HARNESS_MODEL.\n",
                    file=sys.stderr,
                )
                sys.exit(1)
        else:
            harness_lab.configure_harness(use_mock=True, model=lab_args.model)

        if args.harness_auto:
            results = harness_lab.run_all_checks()
            harness_lab.print_results(results)
            sys.exit(1 if not all(r.passed for r in results) else 0)
        harness_lab.chatbot_loop(lab_args)
        return

    if args.mode == "react":
        _run_react(args)
    elif args.mode == "multi":
        _run_multi(args)
    else:
        _run_benchmark(args)


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
    from Agent.Tools.registry import build_tool_registry

    # ── Core infrastructure ───────────────────────────────────────
    scratchpad = Scratchpad()
    client = CatanSocketClient(args.server)
    processor = GameStateProcessor()
    registry = build_tool_registry(client, processor)
    stats = AgentStatsTracker(agent_name=args.name)

    # ── LLM clients ───────────────────────────────────────────────
    openai = OpenAIClient(model=args.model)
    strategy_openai = OpenAIClient(model=args.strategy_model)
    ollama = OllamaChat(OllamaConfig(model=args.ollama_model))

    # ── Create agents ─────────────────────────────────────────────
    risk = RiskAgent(scratchpad, openai=openai)
    strategy = StrategyAgent(
        scratchpad, strategy_openai, client, processor, registry, stats,
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
    print(f"   Models    → strategy={strategy_openai.model}, others={openai.model}")
    print(f"   Strategy  → peers: {list(strategy._peers.keys())}")
    print(f"   Development → peers: {list(development._peers.keys())}")
    print(f"   Risk      → peers: {list(risk._peers.keys())}")
    print(f"   Trading   → peers: {list(trading._peers.keys())}")

    # ── Strategy owns the game loop ──────────────────────────────
    strategy.run()


def _run_benchmark(args) -> None:
    """Launch the deterministic benchmark-calibration agent."""
    from Agent.benchmark_agent.agent import BenchmarkCalibrationAgent

    agent = BenchmarkCalibrationAgent(
        server_url=args.server,
        game_code=args.game_code,
        player_name=args.name,
    )
    agent.run()


if __name__ == "__main__":
    main()
