"""
Run repeatable benchmark matchups:
  - React agent vs Python benchmark heuristic agent
  - Strategy (multi) agent vs Python benchmark heuristic agent

Each game is created with benchmark metadata so Environment benchmark dashboards
aggregate results per run.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List
from urllib.request import urlopen

from Agent.utils.socket_client import CatanSocketClient


def _pseudo_name(kind: str, run_id: str, game_i: int, seat: str) -> str:
    """
    Build a stable pseudo-random name per run/game/seat, while keeping
    the agent type readable (react/strategy/heuristic).
    """
    rng = random.Random(f"{run_id}:{game_i}:{seat}:{kind}")
    suffix = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    return f"{kind}-{suffix}-g{game_i}"


def _http_get_json(url: str) -> Dict:
    with urlopen(url, timeout=10) as resp:  # nosec B310 - controlled local/server URL
        return json.loads(resp.read().decode("utf-8"))


def _wait_for_players(client: CatanSocketClient, expected_min_players: int, timeout_s: float = 45.0) -> Dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = client.latest_state() or {}
        players = state.get("players") if isinstance(state.get("players"), list) else []
        if len(players) >= expected_min_players:
            return state
        time.sleep(0.4)
    raise TimeoutError(f"Timed out waiting for {expected_min_players} players")


def _wait_for_game_finish(client: CatanSocketClient, timeout_s: float = 3600.0) -> Dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = client.latest_state() or {}
        if state.get("phase") == "finished":
            return state
        time.sleep(1.0)
    raise TimeoutError("Timed out waiting for game to finish")


def _spawn_agent_process(
    python_exe: str,
    mode: str,
    server_url: str,
    game_code: str,
    name: str,
    model: str,
    strategy_model: str,
    reconnect_player_id: str | None = None,
) -> subprocess.Popen:
    cmd: List[str] = [
        python_exe,
        "-m",
        "Agent.main",
        "--mode",
        mode,
        "--server",
        server_url,
        "--game-code",
        game_code,
        "--name",
        name,
    ]
    if reconnect_player_id:
        cmd.extend(["--reconnect-player-id", reconnect_player_id])
    if mode == "react":
        cmd.extend(["--model", model])
    if mode == "multi":
        cmd.extend(["--model", model, "--strategy-model", strategy_model])
    return subprocess.Popen(cmd)


def _run_series(args: argparse.Namespace, mode: str, run_id: str) -> None:
    tested_kind = "react" if mode == "react" else "strategy"
    baseline_kind = "heuristic"
    print(f"\n=== Running {mode} series (runId={run_id}) ===")

    for game_i in range(1, args.games + 1):
        host = CatanSocketClient(args.server)
        procs: List[subprocess.Popen] = []
        try:
            host.connect()
            host_name = _pseudo_name(
                kind=baseline_kind,
                run_id=run_id,
                game_i=game_i,
                seat="host",
            )
            created = host.create_game(
                player_name=host_name,
                is_extended=args.extended,
                enable_special_build=not args.disable_special_build,
                benchmark_config={
                    "runId": run_id,
                    "benchmarkId": args.benchmark_id,
                    "baselineAgentId": "benchmark-agent-py",
                    "opponentPolicySet": "python-benchmark-agent",
                    "scenarioTags": ["agent-matchup", mode],
                    "agentId": "benchmark-agent-py",
                    "agentVersion": "python",
                    "baseline": True,
                },
            )
            game_code = str(host.game_code or "")
            host_player_id = str(created.get("playerId") or host.player_id or "")
            if not host_player_id:
                raise RuntimeError("create_game did not return host playerId")
            print(f"[{mode} game {game_i}] created {game_code}")

            tested_proc = _spawn_agent_process(
                python_exe=args.python_exe,
                mode=mode,
                server_url=args.server,
                game_code=game_code,
                name=_pseudo_name(
                    kind=tested_kind,
                    run_id=run_id,
                    game_i=game_i,
                    seat="tested",
                ),
                model=args.model,
                strategy_model=args.strategy_model,
            )
            procs.append(tested_proc)

            # Wait for tested agent to join, then start as host.
            _wait_for_players(host, expected_min_players=args.total_players)
            started = host.call("startGame", {})
            if not isinstance(started, dict) or not started.get("success"):
                raise RuntimeError(f"startGame failed: {started}")

            # Hand host seat to Python benchmark agent.
            baseline_proc = _spawn_agent_process(
                python_exe=args.python_exe,
                mode="benchmark",
                server_url=args.server,
                game_code=game_code,
                name=_pseudo_name(
                    kind=baseline_kind,
                    run_id=run_id,
                    game_i=game_i,
                    seat="baseline",
                ),
                model=args.model,
                strategy_model=args.strategy_model,
                reconnect_player_id=host_player_id,
            )
            procs.append(baseline_proc)

            final_state = _wait_for_game_finish(host, timeout_s=args.game_timeout_s)
            winner_id = final_state.get("winner")
            winner_name = next(
                (p.get("name") for p in (final_state.get("players") or []) if isinstance(p, dict) and p.get("id") == winner_id),
                winner_id,
            )
            print(f"[{mode} game {game_i}] finished winner={winner_name}")

        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            try:
                host.close()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run React/Strategy vs heuristic benchmark matchups")
    parser.add_argument("--server", default="http://localhost:3001")
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument(
        "--total-players",
        type=int,
        default=2,
        help="Total players in each match (including tested agent). Python baseline mode currently supports only 2.",
    )
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--disable-special-build", action="store_true")
    parser.add_argument("--agents", choices=["react", "multi", "both"], default="both")
    parser.add_argument("--benchmark-id", default="catan-benchmark-v1")
    parser.add_argument("--run-prefix", default=f"agent-matchup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}")
    parser.add_argument("--python-exe", default=sys.executable)
    parser.add_argument("--model", default="gpt-4o")
    parser.add_argument("--strategy-model", default="gpt-5")
    parser.add_argument("--game-timeout-s", type=float, default=3600.0)
    args = parser.parse_args()
    if args.total_players != 2:
        raise SystemExit("--total-players must be exactly 2 when using Python benchmark baseline")

    modes = ["react", "multi"] if args.agents == "both" else [args.agents]
    run_ids = {mode: f"{args.run_prefix}-{mode}" for mode in modes}

    for mode in modes:
        _run_series(args, mode=mode, run_id=run_ids[mode])

    print("\n=== Benchmark run summaries ===")
    for mode in modes:
        run_id = run_ids[mode]
        try:
            data = _http_get_json(f"{args.server}/api/benchmark/runs/{run_id}")
            games = data.get("games", []) if isinstance(data, dict) else []
            print(f"- {mode}: runId={run_id} games={len(games)}")
        except Exception as exc:
            print(f"- {mode}: runId={run_id} (could not fetch run summary: {exc})")

    print("\nOpen Observation Deck and filter by runId to compare:")
    print(f"  {args.server.replace(':3001', ':5173')}/#observation-deck")


if __name__ == "__main__":
    main()
