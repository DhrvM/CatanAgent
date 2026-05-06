# CatanAgent

## Benchmark Evaluation

Runnable benchmark evaluation scripts and setup instructions are in [`benchmark_paper/Submission/evaluation/README.md`](benchmark_paper/Submission/evaluation/README.md).

Quick commands:

```bash
./benchmark_paper/Submission/evaluation/run_benchmark_tests.sh
./benchmark_paper/Submission/evaluation/run_heuristic_baseline.sh --games 10 --players 3 --parallel 1
./benchmark_paper/Submission/evaluation/export_benchmark_results.py --server http://localhost:3001
```

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
