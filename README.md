# CatanAgent

CatanAgent is a Settlers of Catan AI system with:
- a cooperative multi-agent mode (Strategy + Risk + Development + Trading), and
- a legacy single-agent ReAct mode.

This README focuses on running **benchmarks locally** with the local game server
and web client.

## Local Benchmark Quick Start

### 1) Start the game server (Terminal A)

```bash
cd Environment/server
npm install
npm start
```

The server runs at `http://localhost:3001`.

### 2) Start the web client (Terminal B)

```bash
cd Environment/client
npm install
npm run dev
```

Open the client at `http://localhost:5173`.

### 3) Create and activate Python venv (Terminal C)

You must activate the virtual environment **every time** before running any
agent command (`Agent.main`, `run_matchups`, harness, etc.).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If you open a new terminal later, reactivate it:

```bash
source .venv/bin/activate
```

### 4) Configure agent env vars

Create or update `Agent/.env`:

```env
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
CATAN_SERVER_URL=http://localhost:3001
```

`CATAN_SERVER_URL` is optional for local runs because the agent defaults to
`http://localhost:3001`.

## Run Local Benchmarks

With the venv active:

```bash
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 1 --agents both
```

This runs:
- ReAct agent vs heuristic bots
- Multi-agent Strategy system vs heuristic bots

### Benchmark Scenarios (React/Strategy vs Heuristic)

Run both scenarios in one command:

```bash
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 1 --agents both
```

Run only **React vs heuristic**:

```bash
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 1 --agents react
```

Run only **Strategy (multi-agent) vs heuristic**:

```bash
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 1 --agents multi
```

Useful options:
- `--agents react` or `--agents multi` to run one agent family only
- `--total-players <N>` to run more than the default 1v1 setup
- `--react-provider anthropic --anthropic-model claude-3-5-sonnet-latest` for Claude-based ReAct benchmarks

### React vs Strategy (Head-to-Head)

`run_matchups` is designed for **agent vs heuristic** series. For direct
**React vs Strategy**, run a manual 1v1 game:

1. In the local client (`http://localhost:5173`), create a 2-player game and copy
   the game code.
2. In one terminal (with venv active), run React:

```bash
python -m Agent.main --mode react --server http://localhost:3001 --game-code ABCDEF --name ReactBot
```

3. In a second terminal (with venv active), run Strategy:

```bash
python -m Agent.main --mode multi --server http://localhost:3001 --game-code ABCDEF --name StrategyBot
```

4. Start the game from the lobby, then use **Spectate Game** to watch.

Note: this head-to-head mode is great for qualitative comparison, but it is not
recorded as the heuristic benchmark `runId` series produced by
`run_matchups`.

## Run Agent Client Manually (Optional)

With the venv active, you can also join/create games directly:

```bash
# Multi-agent on local server (join game)
python -m Agent.main --mode multi --game-code ABCDEF --name StrategyBot

# Multi-agent on local server (create game by omitting --game-code)
python -m Agent.main --mode multi --name StrategyBot
```

Legacy ReAct mode:

```bash
python -m Agent.main --mode react --game-code ABCDEF --name ReactBot
```

## Spectate Benchmark Games

1. Open `http://localhost:5173`.
2. In the lobby, click **Spectate Game**.
3. Enter the game code for an active benchmark match.

Spectator mode lets you watch benchmark games without taking a player slot.

## Agent Directory Structure

```text
Agent/
├── __init__.py
├── main.py
├── benchmark_agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── decision.py
│   └── run_matchups.py
├── shared/
│   ├── __init__.py
│   ├── base_agent.py
│   └── scratchpad.py
├── strategy_agent/
│   ├── __init__.py
│   ├── agent.py
│   └── prompts.py
├── risk_agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── probabilities.py
│   └── prompts.py
├── development_agent/
│   ├── __init__.py
│   ├── agent.py
│   └── prompts.py
├── trading_agent/
│   ├── __init__.py
│   ├── agent.py
│   └── prompts.py
├── react_agent/
│   ├── __init__.py
│   ├── agent.py
│   ├── prompts.py
│   └── summarizer.py
├── tools/
│   ├── __init__.py
│   ├── registry.py
│   ├── game_tools.py
│   ├── risk_tools.py
│   ├── state_tools.py
│   ├── trading_tools.py
│   ├── SCRATCHPAD.md
│   └── tools.md
├── utils/
│   ├── __init__.py
│   ├── anthropic_client.py
│   ├── game_state_processor.py
│   ├── ollama_client.py
│   ├── openai_client.py
│   ├── socket_client.py
│   └── stats_tracker.py
├── harness/
│   ├── __init__.py
│   ├── lab.py
│   ├── mocks.py
│   └── scenarios.py
├── training/
│   ├── __init__.py
│   ├── run_self_play.py
│   ├── self_play_reward.py
│   └── results/
└── Tools/
    └── TradingTools.py
```
