# CatanAgent

CatanAgent is a Settlers of Catan agent system that can run either as a legacy
single ReAct agent or as a cooperative multi-agent system.

The hosted game server is:

- [https://catanagent.onrender.com](https://catanagent.onrender.com)

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
- `--agents react`, `--agents multi`, or `--agents react-vs-strategy`
- `--total-players <N>` to run more than the default 1v1 setup
- `--react-provider anthropic --anthropic-model claude-3-5-sonnet-latest` for Claude-based ReAct benchmarks
- `--strategy-model gpt-5.1` to set the Strategy model (default is `gpt-5.1`)

### React vs Strategy (Head-to-Head)

`run_matchups` also supports direct **React vs Strategy** runs:

```bash
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 1 --agents react-vs-strategy
```

Example with Claude for React and GPT-5.1 for Strategy:

```bash
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 3 --agents react-vs-strategy --react-provider anthropic --anthropic-model claude-3-5-sonnet-latest --strategy-model gpt-5.1
```

For manual qualitative comparisons, you can still run two separate terminals
(`Agent.main --mode react` and `Agent.main --mode multi`) in the same lobby.

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
