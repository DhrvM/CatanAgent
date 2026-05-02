# CatanAgent

CatanAgent is a Settlers of Catan agent system that can run either as a legacy
single ReAct agent or as a cooperative multi-agent system.

The hosted game server is:

- [https://catanagent.onrender.com](https://catanagent.onrender.com)

## Running the Agents

### 1. Configure Environment

Create or update `Agent/.env` with your OpenAI key:

```env
OPENAI_API_KEY=your_openai_api_key_here
CATAN_SERVER_URL=https://catanagent.onrender.com
```

`CATAN_SERVER_URL` is optional if you pass `--prod` or `--server` on the command
line.

### 2. Run the Multi-Agent System

The multi-agent system launches Strategy, Risk, Development, and Trading agents.
Strategy owns the game loop and coordinates the other agents.

Join an existing hosted game:

```powershell
python -m Agent.main --mode multi --prod --game-code ABCDEF --name StrategyBot
```

Or pass the hosted server URL explicitly:

```powershell
python -m Agent.main --mode multi --server https://catanagent.onrender.com --game-code ABCDEF --name StrategyBot
```

Create a new hosted game by omitting `--game-code`:

```powershell
python -m Agent.main --mode multi --prod --name StrategyBot
```

### 3. Model Configuration

By default, the Strategy Agent uses GPT-5 and the other agents use GPT-4o:

```powershell
python -m Agent.main --mode multi --prod --game-code ABCDEF --name StrategyBot --strategy-model gpt-5 --model gpt-4o
```

Flags:

- `--strategy-model`: model used only by the Strategy Agent. Default: `gpt-5`.
- `--model`: model used by Risk, Development, and Trading in multi-agent mode.
  It is also used by the legacy ReAct agent. Default: `gpt-4o`.
- `--ollama-model`: local Ollama model for legacy summarization support.
  Default: `qwen3:8b`.

### 4. Run the Legacy ReAct Agent

The legacy single-agent mode is still available:

```powershell
python -m Agent.main --mode react --prod --game-code ABCDEF --name ReactBot
```

### 5. Run Against a Local Server

If the game server is running locally on port `3001`:

```powershell
python -m Agent.main --mode multi --game-code ABCDEF --name StrategyBot
```

This defaults to `http://localhost:3001` unless `CATAN_SERVER_URL` is set.

### 6. Run the Harness

Run deterministic mock checks without calling OpenAI:

```powershell
python -m Agent.main --mode harness --harness-mock --harness-auto
```

Run live harness checks using OpenAI:

```powershell
python -m Agent.main --mode harness --harness-auto --model gpt-4o-mini
```

### 7. Hosting Note

The Python agents are long-running Socket.IO clients. They keep a persistent
connection open and poll the game state continuously, so they are not a good fit
for Vercel serverless functions. Host the web/game server on a platform like
Render or Vercel if desired, but run the agents themselves on a long-running
process host such as Render, Railway, Fly.io, a VPS, or your local machine.

### 8. Benchmark Matchups (React/Strategy vs Heuristic)

Run automated benchmark matchup series against heuristic bots:

```powershell
python -m Agent.benchmark_agent.run_matchups --server http://localhost:3001 --games 3 --agents both
```

This runner executes:

- React agent vs heuristic bots
- Strategy (multi-agent) vs heuristic bots

Each series gets a dedicated benchmark `runId`, and results are recorded by the
Environment benchmark store so you can compare win rate, VP, tasks, and latency
in the Observation Deck.

By default, matchup games run as **1v1** (tested agent vs one heuristic agent).
To run larger games, pass `--total-players <N>`.

### 9. Spectator View Mode (Environment UI)

In the Environment lobby, use **Spectate Game** to watch an active game by code
without occupying a player seat. This is useful for visually monitoring
agent-vs-agent benchmark matches while they run.

### Agent Directory structure

```
Agent/
├── __init__.py
├── main.py                    ← CLI entry point
├── tools/
│   ├── __init__.py
│   ├── game_tools.py          (was GeneralTools.py)
│   ├── trading_tools.py       (was TradingTools.py)
│   ├── state_tools.py         (was Tools.p$$y)
│   └── registry.py            (17 tool definitions)
├── utils/
│   ├── __init__.py
│   ├── socket_client.py       (was SocketClient.py)
│   ├── ollama_client.py
│   ├── openai_client.py
│   └── game_state_processor.py
└── react_agent/
    ├── __init__.py
    ├── agent.py               (ReAct loop)
    ├── summarizer.py           (move history + strategy)
    └── prompts.py             (system prompt)
```
