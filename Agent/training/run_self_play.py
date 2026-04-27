#!/usr/bin/env python3
"""Run heuristic self-play games and score them with objective rewards.

The game server must already be running. For local development:

    npm --prefix Environment/server start
    python3 -m Agent.training.run_self_play --games 5 --players 3
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import multiprocessing as mp
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from queue import Empty
from threading import Thread
from typing import Any, Dict, List, Optional

from Agent.training.self_play_reward import RewardWeights, score_all_players


LOCAL_SERVER_URL = "http://localhost:3001"
DEFAULT_OUTPUT = "Agent/training/results/self_play_rewards.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run heuristic self-play and score final states.")
    parser.add_argument("--games", type=int, default=1, help="Number of self-play games to run.")
    parser.add_argument("--players", type=int, default=3, help="Number of heuristic players per game.")
    parser.add_argument("--server", default=LOCAL_SERVER_URL, help="Catan server URL.")
    parser.add_argument("--agent-type", choices=["benchmark"], default="benchmark", help="Agent implementation to run.")
    parser.add_argument("--timeout-s", type=float, default=1800.0, help="Maximum seconds per game.")
    parser.add_argument("--parallel", type=int, default=1, help="Number of games to run concurrently.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="JSON file for aggregate results.")
    args = parser.parse_args()

    if args.players < 3:
        raise SystemExit("Self-play needs at least 3 players for standard Catan.")
    if args.games < 1:
        raise SystemExit("--games must be at least 1.")
    if args.parallel < 1:
        raise SystemExit("--parallel must be at least 1.")
    if args.parallel > 10:
        raise SystemExit("--parallel should be 10 or lower unless you raise MAX_CONCURRENT_GAMES on the server.")

    results: List[Dict[str, Any]] = []
    started_at = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {}
        next_game_number = 1

        while next_game_number <= args.games and len(futures) < args.parallel:
            future = _submit_game(executor, next_game_number, args)
            futures[future] = next_game_number
            next_game_number += 1

        while futures:
            done, _ = concurrent.futures.wait(
                futures,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                game_number = futures.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "ok": False,
                        "gameNumber": game_number,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append(result)
                _print_game_result(game_number, args.games, result)
                _write_partial_results(args.output, args, started_at, results)

                if next_game_number <= args.games:
                    future = _submit_game(executor, next_game_number, args)
                    futures[future] = next_game_number
                    next_game_number += 1

    payload = {
        "startedAt": started_at,
        "finishedAt": time.time(),
        "config": {
            "games": args.games,
            "players": args.players,
            "server": args.server,
            "agentType": args.agent_type,
            "timeoutS": args.timeout_s,
            "parallel": args.parallel,
            "rewardWeights": asdict(RewardWeights()),
        },
        "summary": summarize_results(results),
        "games": sorted(results, key=lambda item: int(item.get("gameNumber", 0) or 0)),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"[self-play] wrote results to {output_path}", flush=True)


def _submit_game(
    executor: concurrent.futures.Executor,
    game_number: int,
    args: argparse.Namespace,
) -> concurrent.futures.Future:
    print(f"[self-play] starting game {game_number}/{args.games}", flush=True)
    cleanup_finished_games(args.server)
    return executor.submit(
        run_one_game,
        game_number=game_number,
        server=args.server,
        players=args.players,
        agent_type=args.agent_type,
        timeout_s=args.timeout_s,
    )


def _print_game_result(game_number: int, total_games: int, result: Dict[str, Any]) -> None:
    if result.get("ok"):
        winner = result.get("winnerName") or result.get("winner") or "unknown"
        print(f"[self-play] game {game_number}/{total_games} finished: winner={winner}", flush=True)
    else:
        print(f"[self-play] game {game_number}/{total_games} failed: {result.get('error')}", flush=True)


def _write_partial_results(
    output: str,
    args: argparse.Namespace,
    started_at: float,
    results: List[Dict[str, Any]],
) -> None:
    output_path = Path(output)
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    partial_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "startedAt": started_at,
        "updatedAt": time.time(),
        "config": {
            "games": args.games,
            "players": args.players,
            "server": args.server,
            "agentType": args.agent_type,
            "timeoutS": args.timeout_s,
            "parallel": args.parallel,
            "rewardWeights": asdict(RewardWeights()),
        },
        "summary": summarize_results(results),
        "games": sorted(results, key=lambda item: int(item.get("gameNumber", 0) or 0)),
    }
    partial_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def run_one_game(
    *,
    game_number: int,
    server: str,
    players: int,
    agent_type: str,
    timeout_s: float,
) -> Dict[str, Any]:
    """Run one game in a child process so agent threads are cleaned up fully."""

    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    process = ctx.Process(
        target=_run_one_game_child,
        args=(queue, game_number, server, players, agent_type, timeout_s),
        daemon=False,
    )
    process.start()
    try:
        return queue.get(timeout=timeout_s + 30.0)
    except Empty:
        process.terminate()
        process.join(timeout=5.0)
        return {
            "ok": False,
            "gameNumber": game_number,
            "error": f"Timed out after {timeout_s:.1f}s",
        }
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5.0)


def _run_one_game_child(
    queue: mp.Queue,
    game_number: int,
    server: str,
    players: int,
    agent_type: str,
    timeout_s: float,
) -> None:
    try:
        result = _run_one_game_in_process(
            game_number=game_number,
            server=server,
            players=players,
            agent_type=agent_type,
            timeout_s=timeout_s,
        )
        queue.put(result)
    except Exception as exc:
        queue.put({
            "ok": False,
            "gameNumber": game_number,
            "error": f"{type(exc).__name__}: {exc}",
        })


def _run_one_game_in_process(
    *,
    game_number: int,
    server: str,
    players: int,
    agent_type: str,
    timeout_s: float,
) -> Dict[str, Any]:
    if agent_type != "benchmark":
        raise ValueError(f"Unsupported agent_type: {agent_type}")

    from Agent.benchmark_agent.agent import BenchmarkCalibrationAgent

    agents: List[BenchmarkCalibrationAgent] = []
    threads: List[Thread] = []
    start_time = time.time()
    host: Optional[BenchmarkCalibrationAgent] = None
    game_code: Optional[str] = None

    def launch_agent(name: str, game_code: Optional[str] = None) -> BenchmarkCalibrationAgent:
        agent = BenchmarkCalibrationAgent(server_url=server, game_code=game_code, player_name=name)
        thread = Thread(target=agent.run, daemon=True)
        thread.start()
        agents.append(agent)
        threads.append(thread)
        return agent

    try:
        cleanup_finished_games(server)
        host = launch_agent(f"HeuristicBot-1-G{game_number}")
        game_code = _wait_for_game_code(host, timeout_s=20.0)

        for seat in range(2, players + 1):
            launch_agent(f"HeuristicBot-{seat}-G{game_number}", game_code=game_code)

        _wait_for_player_count(host, players, timeout_s=30.0)
        start_ack = host.client.sio.call("startGame", timeout=10)
        if not start_ack.get("success"):
            raise RuntimeError(f"startGame failed: {start_ack}")

        final_state = _wait_for_finished_state(agents, timeout_s=timeout_s)
        rewards = [breakdown.as_dict() for breakdown in score_all_players(final_state)]
        winner_id = final_state.get("winner")
        winner_name = _winner_name(final_state, winner_id)

        return {
            "ok": True,
            "gameNumber": game_number,
            "gameCode": game_code,
            "durationS": round(time.time() - start_time, 2),
            "phase": final_state.get("phase"),
            "winner": winner_id,
            "winnerName": winner_name,
            "players": _final_player_rows(final_state),
            "rewards": rewards,
        }
    finally:
        if host is not None and game_code:
            try:
                host.client.sio.call("deleteGame", timeout=10)
            except Exception as exc:
                print(f"[self-play] warning: could not delete game {game_code}: {exc}", flush=True)
        for agent in agents:
            try:
                agent.client.close()
            except Exception:
                pass
        cleanup_finished_games(server)


def cleanup_finished_games(server: str) -> None:
    """Ask the local server to clear completed games from its in-memory game map."""

    url = server.rstrip("/") + "/api/admin/cleanup-finished-games"
    request = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
            cleaned = int(payload.get("cleanedCount", 0) or 0)
            if cleaned:
                print(f"[self-play] cleaned {cleaned} finished server game(s)", flush=True)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        # Older servers will not have this endpoint. Per-game deleteGame still
        # handles cleanup after restart, so this should not kill the run.
        return


def _wait_for_game_code(agent: Any, timeout_s: float) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if agent.game_code:
            return str(agent.game_code)
        time.sleep(0.1)
    raise TimeoutError("Timed out waiting for host agent to create a game")


def _wait_for_player_count(agent: Any, players: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = agent.client.latest_state()
        if isinstance(state, dict) and len(state.get("players") or []) >= players:
            return
        time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {players} players to join")


def _wait_for_finished_state(agents: List[Any], timeout_s: float) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    last_state: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        for agent in agents:
            state = agent.client.latest_state()
            if isinstance(state, dict):
                last_state = state
                if state.get("phase") == "finished":
                    return state
        time.sleep(0.5)
    if last_state is None:
        raise TimeoutError("Timed out before receiving any game state")
    raise TimeoutError(f"Game did not finish before timeout; last phase={last_state.get('phase')}")


def _winner_name(state: Dict[str, Any], winner_id: Any) -> Optional[str]:
    for player in state.get("players") or []:
        if isinstance(player, dict) and str(player.get("id")) == str(winner_id):
            return str(player.get("name") or player.get("playerName") or winner_id)
    return None


def _final_player_rows(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, player in enumerate(state.get("players") or []):
        if not isinstance(player, dict):
            continue
        rows.append({
            "index": index,
            "id": player.get("id"),
            "name": player.get("name") or player.get("playerName"),
            "victoryPoints": player.get("victoryPoints", 0),
            "roads": _count_piece_field(player.get("roads")),
            "settlements": _count_piece_field(player.get("settlements")),
            "cities": _count_piece_field(player.get("cities")),
            "longestRoad": bool(player.get("longestRoad")),
            "largestArmy": bool(player.get("largestArmy")),
        })
    rows.sort(key=lambda row: row["victoryPoints"], reverse=True)
    return rows


def _count_piece_field(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [result for result in results if result.get("ok")]
    if not completed:
        return {"completedGames": 0, "failedGames": len(results)}

    reward_totals: Dict[str, List[float]] = {}
    wins: Dict[str, int] = {}
    for result in completed:
        winner = result.get("winnerName") or str(result.get("winner") or "unknown")
        wins[winner] = wins.get(winner, 0) + 1
        for reward in result.get("rewards") or []:
            name = str(reward.get("player_name"))
            reward_totals.setdefault(name, []).append(float(reward.get("total", 0.0)))

    return {
        "completedGames": len(completed),
        "failedGames": len(results) - len(completed),
        "wins": wins,
        "averageRewardByPlayer": {
            name: round(sum(values) / len(values), 4)
            for name, values in reward_totals.items()
            if values
        },
    }


if __name__ == "__main__":
    main()
