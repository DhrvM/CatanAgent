# Agent Architecture Refactor: Dynamic Consultation Model

Transform the rigid fixed-order turn flow into a dynamic model where Strategy orchestrates by consulting agents before making decisions.

## Current vs. Proposed Flow

```
CURRENT (rigid):                       PROPOSED (dynamic):
─────────────────                      ──────────────────
1. risk.analyze()                      1. risk.analyze()         (math baseline)
2. risk.consult(auto-question)         2. risk.consult(question) (competitive Q&A)
3. strategy._plan()                    3. dev.consult(question)  (building Q&A)
4. Always: trade → build → end         4. strategy._plan()       (informed by consultations)
                                       5. Execute plan actions    (order from plan)
                                       6. end_turn
```

### Key Differences

| Aspect | Current | Proposed |
|---|---|---|
| Development consultation | None — only executes | **New `consult(question)`** — advises on building options |
| Strategy planning input | Risk data only | Risk consultation **+ Development consultation** |
| Turn action order | Fixed: trade → build | **Dynamic**: determined by the plan |
| Long-term plans | Regenerated from scratch each turn | **Persisted**: first turns generate long-term strategy, later turns reference it |
| Strategy prompt | Generates plan blind | Generates plan **informed by two consultations** |

---

## Proposed Changes

### Development Agent

#### [MODIFY] [agent.py](file:///c:/Users/dhruv/Documents/Computer%20Science/498%20AI%20Agents/Experiment_Branch/CatanAgent/Agent/development_agent/agent.py)

Add a `consult(question)` method (mirroring Risk's):
- Strategy can ask: "What's the best settlement spot for ore income?", "Should I upgrade to city or build new settlement?", "Can I afford road + settlement this turn?"
- Builds context from scratchpad: game state, our resources, building costs, available spots, Risk's building EV data
- GPT-4o answers with concrete building recommendations
- Deterministic fallback if GPT-4o unavailable

#### [MODIFY] [prompts.py](file:///c:/Users/dhruv/Documents/Computer%20Science/498%20AI%20Agents/Experiment_Branch/CatanAgent/Agent/development_agent/prompts.py)

Add `DEV_CONSULTANT_SYSTEM_PROMPT` and `build_dev_consult_context()` for the new consultation mode.

---

### Strategy Agent

#### [MODIFY] [agent.py](file:///c:/Users/dhruv/Documents/Computer%20Science/498%20AI%20Agents/Experiment_Branch/CatanAgent/Agent/strategy_agent/agent.py)

**Turn flow rewrite** (`_run_turn`):
1. Update scratchpad with game state
2. `risk.analyze()` — populates math baseline
3. `risk.consult(question)` — competitive questions (who's winning, longest road/army threats)
4. **NEW**: `development.consult(question)` — building questions (best spots, resource optimization)
5. `_plan()` — GPT-4o planning now has **both** consultations in context
6. Execute plan actions — **order from plan's `action_order` field**, not fixed
7. End turn

**`_derive_dev_question()`** — new method to generate building questions:
- If we have enough resources for city upgrade → "Should I upgrade or save for settlement?"
- If income is unbalanced → "Where should I build to diversify income?"
- General → "What's my best building move this turn?"

**`_build_context()`** — add consultation results (risk + dev) to the planning context.

#### [MODIFY] [prompts.py](file:///c:/Users/dhruv/Documents/Computer%20Science/498%20AI%20Agents/Experiment_Branch/CatanAgent/Agent/strategy_agent/prompts.py)

**Updated system prompt:**
- Strategy now receives consultations from Risk AND Development
- Plan includes `action_order` field: e.g., `["trade", "build"]` or `["build", "trade"]`
- Strategy can specify `should_trade_first: true/false` based on consultations

**Updated context builder:**
- Add `risk_consultation` and `dev_consultation` sections

---

### Agents.md

Update the agent communication graph, Strategy turn sequence, and Development agent section to reflect the new consultation capability.

---

## Verification Plan

### Automated
- Import checks: `from Agent.development_agent.agent import DevelopmentAgent`
- Entrypoint: `python -m Agent.main --help`

### Manual
- Run against live game and verify:
  - `[dev] consult Q:` and `[dev] consult A:` log lines appear
  - Strategy plan references building advice in its reasoning
  - Turn actions execute in the order specified by the plan
