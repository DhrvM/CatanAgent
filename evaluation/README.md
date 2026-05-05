# Catan Benchmark Evaluation

This directory contains the runnable evaluation workflow for the Catan benchmark. It is intended to make the benchmark reproducible for another reader: install the server and agent dependencies, run a baseline, export benchmark records, and interpret the generated JSON.

## What Is Evaluated

The benchmark records both full-game outcomes and task-level decisions:

- Full-game metrics: win rate, final victory points, rounds to win, and average turn latency.
- Task metrics: pass/success rate and task scores for short-term, medium-term, and long-term Catan decisions.
- Task categories: settlement placement, road direction, robber placement/victim choice, trade decisions, build/save decisions, development card decisions, discard decisions, and long-term achievement pursuit.

The benchmark definitions live in `Environment/server/benchmarkDefinitions.js`. The server persists benchmark runs and task records through the benchmark store and exposes JSON endpoints under `/api/benchmark/*`.

## Repository Resources

Required local resources:

- `Environment/server/benchmarkDefinitions.js`: task descriptions, metric directions, and metric weights.
- `Environment/server/benchmarkEvaluation.js`: scoring logic for benchmark task payloads.
- `Environment/server/benchmarkStore.js`: run/task storage, leaderboards, slices, and live logs.
- `Agent/training/run_self_play.py`: automated heuristic baseline runner.

Generated resources:

- `Agent/training/results/*.json`: self-play baseline outputs.
- `Environment/server/benchmark-data/`: benchmark store data written by the local server.
- `evaluation/results/<timestamp>/`: exported benchmark API snapshots.

External resources:

- No external dataset is required.
- OpenAI API access is optional and only needed for `react` or `multi`/strategy agents. Set `OPENAI_API_KEY` in your shell or `Agent/.env`.
- A hosted game server may be used through `--server https://catanagent.onrender.com`, but local evaluation is recommended for reproducibility.

## Requirements

- Python 3.10+.
- Node.js 18+ and npm.
- Python packages from `requirements.txt`.
- Server packages from `Environment/server/package.json`.

Optional:

- Client packages from `Environment/client/package.json` if you want to inspect the browser UI and benchmark dashboard.

## Setup

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cd Environment/server
npm install
cd ../..
```

Optional UI setup:

```bash
cd Environment/client
npm install
cd ../..
```

## Run The Server

In one terminal:

```bash
cd Environment/server
npm start
```

The default server URL is `http://localhost:3001`.

To clear previous local benchmark records before a fresh run:

```bash
cd Environment/server
npm run benchmark:clear
```

## Validate Benchmark Code

Run the benchmark/unit evaluation checks:

```bash
./evaluation/run_benchmark_tests.sh
```

This runs the server-side benchmark observation, scoring, resource heuristic, and longest-road cycle tests.

## Run The Heuristic Agent Baseline

The paper uses 10 games of heuristic bot instances as the simple agent baseline. With the server running:

```bash
./evaluation/run_heuristic_baseline.sh --games 10 --players 3 --parallel 1
```

The script writes a JSON summary to:

```text
Agent/training/results/heuristic_baseline_10_games.json
```

You can shorten the run while debugging:

```bash
./evaluation/run_heuristic_baseline.sh --games 1 --players 3 --timeout-s 600
```

## Run LLM Agent Evaluations

For a ReAct or multi-agent strategy evaluation, start a game in the browser or use the local server flow, then launch agents against the same game code:

```bash
python -m Agent.main --mode react --server http://localhost:3001 --game-code ABCDEF --name ReactAgent
python -m Agent.main --mode multi --server http://localhost:3001 --game-code ABCDEF --name StrategyAgent
```

The benchmark-enabled server records task telemetry automatically when benchmark mode is enabled for the game. The heuristic self-play runner uses the benchmark calibration agent and is the recommended reproducible baseline path.

## Export Benchmark Results

With the server running, export the benchmark API state:

```bash
./evaluation/export_benchmark_results.py --server http://localhost:3001
```

This creates a timestamped directory under `evaluation/results/` containing:

- `definitions.json`: task descriptions, categories, metric directions, and metric weights.
- `overview.json`: aggregate benchmark overview and top entities.
- `leaderboard_agents.json`: agent-level leaderboard.
- `leaderboard_players.json`: player-level leaderboard.
- `slices.json`: per-slice comparisons by benchmark, map, opponent policy, scenario tags, and task category.
- `live_log.json`: recent task/action events with task score and pass/fail fields.
- `manifest.json`: export metadata and endpoint mapping.

You can change the output directory:

```bash
./evaluation/export_benchmark_results.py --out evaluation/results/my_run
```

## Interpret Results

Start with `overview.json`:

- `totals.runs`, `totals.games`, `totals.tasks`, and `totals.telemetryRecords` show how much data has been collected.
- `topAgents` and `topPlayers` show the highest aggregate benchmark scores.

Then inspect `leaderboard_agents.json`:

- `overallScore` is the weighted score used for ranking.
- `normalizedMetrics` shows each metric after direction-aware normalization.
- `rawMetrics` shows direct values such as win rate, average final VP, task success rate, and latency.
- `taskBreakdown` identifies which benchmark tasks were passed or failed.

Use `slices.json` to check robustness:

- Compare performance by task category, map layout, opponent policy set, and scenario tags.
- A well-designed benchmark should reveal strengths and weaknesses instead of producing only one undifferentiated score.

Use `live_log.json` for auditability:

- Each entry links an observed action to its benchmark task, score, pass/fail result, and explanation.
- This is the right file to inspect when validating task quality or debugging surprising leaderboard results.

Metric weights are defined in `Environment/server/benchmarkDefinitions.js`:

- Win rate: 0.30.
- Average final victory points: 0.15.
- Average rounds to win: 0.15, lower is better.
- Task success rate: 0.30.
- Average latency per turn: 0.10, lower is better.
