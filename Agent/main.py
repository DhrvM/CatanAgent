#!/usr/bin/env python3
"""
CLI entry point for the ReAct Catan Agent.

Usage:
    python -m Agent.main --game-code ABCDEF --name ReactBot
    python Agent/main.py --game-code ABCDEF --name ReactBot
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
    parser = argparse.ArgumentParser(description="ReAct Catan Agent")
    parser.add_argument("--server", default="http://localhost:3001", help="Game server URL")
    parser.add_argument("--game-code", default=None, help="Game code to join (omit to create)")
    parser.add_argument("--name", default="ReactBot", help="Player name")
    parser.add_argument("--model", default="gpt-4o", help="OpenAI model")
    parser.add_argument("--ollama-model", default="qwen3:8b", help="Ollama model for summarization")
    args = parser.parse_args()

    from Agent.react_agent.agent import ReactCatanAgent

    agent = ReactCatanAgent(
        server_url=args.server,
        game_code=args.game_code,
        player_name=args.name,
        openai_model=args.model,
        ollama_model=args.ollama_model,
    )
    agent.run()


if __name__ == "__main__":
    main()
