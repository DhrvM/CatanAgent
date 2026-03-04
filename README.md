# CatanAgent


### Agent Directory structure

```
Agent/
├── __init__.py
├── main.py                    ← CLI entry point
├── tools/
│   ├── __init__.py
│   ├── game_tools.py          (was GeneralTools.py)
│   ├── trading_tools.py       (was TradingTools.py)
│   ├── state_tools.py         (was Tools.py)
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
