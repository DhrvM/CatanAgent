# Catan Benchmark Task Explanations

This document explains the benchmark task system currently defined in the codebase, why each rubric component exists, how each subcomponent is calculated, and which tasks are fully wired into runtime evaluation.

There are currently **20 benchmark reasoning tasks** in `Enviroment/server/benchmarkDefinitions.js`.

The separate objective **"Win the Game"** is not stored as a reasoning-task ID. It is evaluated through the gameplay metrics in the benchmark deck:

- `winRate`
- `averageFinalVictoryPoints`
- `averageRoundsToWin`

## 1. Design Principles

The benchmark uses three families of tasks:

- **Short-term tasks**: immediate tactical choices with direct board consequences.
- **Medium-term tasks**: action prioritization and resource conversion decisions.
- **Long-term tasks**: strategic race commitments whose value unfolds over several turns.

The main design goal is to score decisions against the **best legal alternative available at that moment**, not just whether the move was legal.

That is why most tasks are evaluated as:

- **Option ratio** = `selected_option_score / best_option_score`

This has two advantages:

- It rewards near-optimal play even when several choices are reasonable.
- It avoids brittle pass/fail labels for decisions that are inherently graded rather than binary.

## 2. Shared Heuristic Building Blocks

These shared components appear repeatedly in the code.

### 2.1 Production expectancy

Used for settlement quality and downstream road/city estimates.

The code maps dice numbers to production weights:

- `2,12 -> 1`
- `3,11 -> 2`
- `4,10 -> 3`
- `5,9 -> 4`
- `6,8 -> 5`

This is a compact approximation of the true two-dice probability distribution. The purpose is to reward vertices touching high-frequency numbers without requiring exact probability arithmetic during evaluation.

For a candidate settlement vertex:

- `productionWeight = sum(adjacent_hex_probability_weights)`
- `resourceProductionExpectancy = min(1, productionWeight / 15)`

Why divide by `15`:

- A vertex touches at most 3 hexes.
- The maximum per-hex weight is `5`.
- So the theoretical maximum is `3 * 5 = 15`.

### 2.2 Resource diversity

`resourceDiversity = min(1, distinct_adjacent_resource_types / 3)`

Why it matters:

- Diverse starts reduce dependency on trading.
- They make future build paths more robust.
- They reduce the chance of bottlenecking on one missing resource.

### 2.3 Expansion access

For a settlement candidate:

- compute incident edges
- keep only edges where a road could legally be placed
- `expansionAccess = min(1, legal_outgoing_edges / 3)`

Why it matters:

- A settlement is stronger if it leaves multiple directions open for future road growth.
- It acts as a proxy for flexibility and survivability in crowded boards.

### 2.4 Port synergy

Current implementation:

- specific 2:1 port -> `1.0`
- generic 3:1 port -> `0.7`
- no port -> `0.0`

Why it matters:

- Ports convert surplus into missing resources.
- A specific port is usually stronger because of better exchange efficiency.
- A generic port is still valuable, but less specialized.

### 2.5 Resource need and surplus

Used in trade and bank-trade scoring.

Current implementation is now **goal-aware**, not just hand-composition aware.

The system first builds a small set of candidate plans:

- `road`
- `settlement`
- `city`
- `devCard`

Each plan gets:

- a **base plan weight** from current board opportunities
- a **cost vector**
- a **distance-to-completion** equal to the total number of missing cards

In the current server implementation:

- road weight comes from the best legal road-extension score
- settlement weight comes from the best legal settlement score
- city weight comes from the best legal city-upgrade score
- dev-card weight uses a light heuristic derived from the city attractiveness signal, so ore is not automatically overvalued in pure expansion openings

Then:

- `planWeight = basePlanWeight / (1 + 0.75 * totalMissing)`

This discounts far-away plans so they do not dominate immediate tactical priorities.

For each resource:

- `need[r] += planWeight * (missing[r] / max(1, totalMissing))`

Interpretation:

- a resource is needed when it is a bottleneck for high-value near-term plans
- if a plan already has enough of that resource, it contributes `0`

For surplus:

- reserve part of the current hand for the weighted plans
- `reserved[r] = min(hand[r], sum(planWeight * cost[r]))`
- `free[r] = max(0, hand[r] - reserved[r])`
- `surplus[r] = normalize(free[r])`

This fixes the earlier failure mode where ore could be treated as important just because it was scarce in hand, even when the board strongly favored early road and settlement expansion.

## 3. Task Inventory and Implementation Status

Status labels used below:

- **Implemented**: task definition, evaluator, and action-time payload emission all exist.
- **Partial**: task definition and evaluator exist, but runtime triggering is incomplete.
- **Defined only**: task exists in definitions/evaluators but is not yet emitted from gameplay actions.

| Task ID | Category | Status |
| --- | --- | --- |
| `settlement-location-selection` | Short-term | Implemented |
| `road-placement-direction` | Short-term | Implemented |
| `knight-card-playing-decision` | Short-term | Implemented |
| `year-of-plenty-card-playing-decision` | Short-term | Implemented |
| `monopoly-card-playing-decision` | Short-term | Implemented |
| `road-building-card-playing-decision` | Short-term | Implemented |
| `robber-victim-selection` | Short-term | Implemented |
| `robber-placement` | Short-term | Implemented |
| `accept-or-reject-trade-offers` | Short-term | Implemented |
| `generate-trade-offers` | Short-term | Implemented |
| `select-targeted-trade-partner` | Short-term | Partial |
| `generate-counter-trade-offer` | Short-term | Implemented |
| `build-vs-save-decision` | Medium-term | Implemented |
| `city-vs-settlement-vs-road-prioritization` | Medium-term | Implemented |
| `development-card-purchase-decision` | Medium-term | Implemented |
| `discard-strategy-after-seven` | Medium-term | Implemented |
| `bank-trade-decision` | Medium-term | Implemented |
| `initial-settlement-location-selection` | Long-term | Implemented |
| `decide-pursue-longest-road` | Long-term | Implemented as pending long-horizon episode |
| `decide-pursue-largest-army` | Long-term | Implemented as pending long-horizon episode |

## 4. Task-by-Task Explanations

## 4.1 `initial-settlement-location-selection`

**Rubric**

- `0.40 production + 0.25 diversity + 0.20 expansionAccess + 0.15 portSynergy`

**Why these components**

- Production gets the highest weight because early income determines the entire opening trajectory.
- Diversity is second because opening bottlenecks are expensive and hard to repair.
- Expansion access matters because the second settlement and first few roads are path-dependent.
- Port synergy matters, but less than raw income, because ports are only useful once the economy starts producing surplus.

**Current calculation**

Same formula as settlement placement:

- `production = resourceProductionExpectancy`
- `diversity = resourceDiversity`
- `expansionAccess = legal_road_branches / 3`
- `portSynergy = 1.0, 0.7, or 0.0`

**Normalization**

- Final score is clamped to `[0,1]`.

## 4.2 `settlement-location-selection`

Same subcomponents and justification as the initial-settlement task, but applied to non-setup settlement placement.

The logic is reused because the same strategic tradeoffs still matter:

- stronger production
- better resource mix
- better expansion geometry
- port access

The main conceptual difference is that later settlements occur in a more constrained board, so the score compares the selected legal location against the best remaining legal location.

## 4.3 `road-placement-direction`

**Rubric**

- `0.50 futureReachability + 0.35 reachableIntersectionValue + 0.15 blockingValue`

**Why these components**

- Future reachability is weighted highest because roads are mostly investment moves.
- Reachable intersection value matters because roads are useful only insofar as they lead to strong future settlement points.
- Blocking value matters tactically, but not every road is a blocking move, so it carries the lowest weight.

**Current calculation**

- `futureReachability = min(1, next_edges_from_forward_vertex / 3)`
- `reachableIntersectionValue = 0.5 * production + 0.3 * diversity + 0.2 * portSynergy` of the forward vertex
- `blockingValue = 0`

Important note:

- `blockingValue` is currently a placeholder and is not yet modeled.
- This means the current implementation favors expansion and future settlement potential but does not truly recognize denial plays.

## 4.4 `knight-card-playing-decision`

**Target rubric**

- `0.40 robberThreatLevel + 0.30 resourceNeed + 0.20 armyProgressRatio + 0.10 safeHandSize`

**Intended justification**

- `robberThreatLevel`: a Knight is most valuable when the robber is actively harming the player or threatens a key production tile.
- `resourceNeed`: moving the robber and stealing a card is stronger when the player is missing key resources.
- `armyProgressRatio`: Knights have dual value because they advance Largest Army.
- `safeHandSize`: spending a Knight can reduce hand-risk and improve safety around a possible seven.

**Current status**

- The live system now emits this task for both `play-now` and `hold` outcomes.
- The runtime compares those two options with a heuristic built from robber pressure, resource need, army progress, and hand safety.

## 4.5 `year-of-plenty-card-playing-decision`

**Target rubric**

- `0.50 buildUnlock + 0.35 resourceScarcity + 0.15 longtermDiversityGain`

**Intended justification**

- `buildUnlock` is highest because Year of Plenty is strongest when it immediately unlocks a valuable build.
- `resourceScarcity` captures the value of fixing current bottlenecks.
- `longtermDiversityGain` rewards taking resources that also improve future flexibility.

**Current status**

- The task now finalizes after the second `Year of Plenty` pick.
- The chosen pair is compared against all legal unordered resource pairs.

## 4.6 `monopoly-card-playing-decision`

**Target rubric**

- `0.55 estimatedOpponentHoldings + 0.30 personalResourceNeed + 0.15 buildUnlock`

**Intended justification**

- The card is strongest when opponents likely hold a concentrated resource.
- Personal need matters because the best call depends on the agent's current build plans.
- Immediate build unlock matters, but it depends on the first two quantities.

**Current status**

- Monopoly plays now emit a benchmark payload over the five possible resource calls.
- The implementation uses actual hidden-opponent holdings available on the server as the holdings estimate.

## 4.7 `road-building-card-playing-decision`

**Target rubric**

- `Option-ratio`

**Intended justification**

- This is naturally a compare-the-alternatives decision.
- The key question is whether playing Road Building now produces more value than holding it for a better timing window.

**Current status**

- Runtime option generation is implemented as a `play-now` versus `hold` comparison.
- The immediate value is driven by the best available free-road follow-ups plus Longest Road leverage.

## 4.8 `robber-victim-selection`

**Rubric**

- `Option-ratio`

**Why option-ratio is appropriate**

- Victim selection is a discrete choice among legal targets.
- The best target depends on leader pressure, theft value, and visible board strength.

**Current calculation**

For each legal victim:

- start with `0.55` if the victim is the visible leader, else `0.25`
- add up to `0.35` from victim hand size via `resourceTotal / 20`
- add up to `0.10` from visible score via `visibleScore / 20`
- clamp to `[0,1]`

Interpretation:

- leader targeting is the dominant factor
- stealing from resource-rich players is next
- visible public strength slightly increases priority

## 4.9 `robber-placement`

**Rubric**

- `0.35 hurtLeader + 0.30 productionDamage + 0.20 theftValue + 0.15 selfHarmAvoidance`

**Why these components**

- Hurting the leader is the primary anti-runaway mechanism in Catan.
- Production damage matters because shutting down good tiles is long-lasting.
- Theft value matters because robber moves also create immediate hand swing.
- Self-harm avoidance matters because strong robber play should not block the agent's own production.

**Current calculation**

For a candidate hex:

- identify all opponents on that hex
- compute a per-player strength proxy:
  - `strengthScore = min(1, (visibleVP + resourceTotal / 8) / 5)`
- `hurtLeader` = leader-only portion of damage, weighted by the hex number probability
- `productionDamage` = total damage across all affected opponents, weighted by hex probability
- `theftValue` = best victim hand-size value on that hex, `min(1, resourceTotal / 7)`
- `selfHarmAvoidance` = `0` if the acting player is also on the hex, else `1`

Why number weighting is justified:

- Blocking a `6` or `8` should count more than blocking a `3` or `11`.

## 4.10 `accept-or-reject-trade-offers`

**Rubric**

- `0.50 selfGain + 0.25 leaderPenalty + 0.15 resourceUrgency + 0.10 fairnessBalance`

**Current implementation shape**

This task is currently approximated as a two-option ratio:

- option `accept`
- option `reject`

The current accept score is:

- `acceptScore = selfGain` from the responder's perspective

The current reject score is:

- `rejectScore = clamp(1 - acceptScore + 0.1)`

Trade self-gain comes from:

- `requestValue - 0.5 * giveCost`

where:

- `requestValue` uses the responder's need map
- `giveCost` uses the responder's surplus map

Current limitation:

- the code does not yet separately expose `leaderPenalty`, `resourceUrgency`, and `fairnessBalance` as independent logged subscores
- they are folded into the simplified trade heuristic

## 4.11 `generate-trade-offers`

**Rubric**

- `0.35 legality + 0.45 selfGain + 0.15 (1 - leaderPenalty) + 0.05 (1 - fairnessPenalty)`

**Why these components**

- A trade offer must first be legal.
- The primary purpose is still to improve the acting agent's position.
- Helping the leader is strategically dangerous and should be penalized.
- Extreme unfairness is undesirable because it reduces plausibility and acceptance chances.

**Current calculation**

- `legality = 1` unless the agent offers resources it does not own
- `selfGain = clamp(requestValue - 0.5 * giveCost)`
- `leaderPenalty = 1` if the target player is the visible leader, else `0`
- `fairnessPenalty = min(1, max(0, giveCost - requestValue))`

Then:

- `tradeOfferScore = 0.35 * legality + 0.45 * selfGain + 0.15 * (1 - leaderPenalty) + 0.05 * (1 - fairnessPenalty)`

The generated offer is then compared against a benchmark best score through a ratio.

## 4.12 `select-targeted-trade-partner`

**Rubric**

- `Option-ratio`

**Current calculation**

Each candidate trade partner is scored as:

- `selfGain + 0.25 - 0.4 * leaderHelpPenalty`

where `leaderHelpPenalty = 1` if the target is the leader.

Why this makes sense:

- a good partner is one who yields good value to the acting player
- but trading with the leader is strategically discounted

Current limitation:

- this task is only emitted when a trade is explicitly targeted to one player
- broad offers to all opponents do not produce this task result

## 4.13 `generate-counter-trade-offer`

**Rubric**

- `0.35 legality + 0.45 selfGain + 0.15 (1 - leaderPenalty) + 0.05 (1 - fairnessPenalty)`

The justification is the same as for generated trade offers.

The difference is contextual:

- a counteroffer should improve the responder's position relative to the incoming offer
- it is evaluated using the same structural trade-quality heuristic

## 4.14 `build-vs-save-decision`

**Rubric**

- `Option-ratio`

**Intended justification**

- This decision is best modeled as comparing the expected value of spending now versus preserving option value for later.

Typical factors should include:

- immediate VP or production gains
- likelihood that saved resources unlock a better build next turn
- risk of losing resources to a seven

**Current status**

- The task is emitted once per turn.
- Spending on settlement, road, city, or dev-card purchase records a build choice; ending the turn without spending records `save`.

## 4.15 `city-vs-settlement-vs-road-prioritization`

**Rubric**

- `Option-ratio`

**Why option-ratio is appropriate**

- This task compares build classes rather than board coordinates.
- It should answer: among the legal build families available right now, which one has the highest expected strategic value?

**Current calculation**

Current options are scored as follows:

- `settlement`: best legal settlement score using the settlement rubric
- `city`: best legal city-upgrade score
- `road`: best legal road score using the road-placement rubric

Current city heuristic:

- `0.65 * production + 0.10 * diversity + 0.05 * portSynergy + 0.20`

Why this heuristic is reasonable:

- cities primarily amplify existing production, so production gets the dominant weight
- diversity and port access still matter because they change the strategic value of doubling that vertex
- the constant `0.20` reflects the guaranteed VP and output increase from a city upgrade

Current status:

- The task is emitted for settlement, city, and normal road-building choices.
- Free roads from `Road Building` are intentionally excluded from this spend-prioritization task.

## 4.16 `development-card-purchase-decision`

**Rubric**

- `Option-ratio`

**Intended justification**

- Buying a development card is an investment choice against other possible uses of resources.
- It should be favored when hidden VP, Knight access, or future tactical options dominate immediate board builds.

**Current status**

- `buyDevCard` now emits this task directly.
- If dev-card purchase was affordable but skipped for the full turn, the negative case is recorded at turn end.

## 4.17 `discard-strategy-after-seven`

**Rubric**

- `RetainedHandValue / BestRetainedValue`

**Why this formulation is correct**

- Discarding is constrained by a fixed number of cards to lose.
- The strategic objective is to maximize the value of the remaining hand, not the discarded hand.

This is one of the cleanest task formulations in the benchmark because it directly matches the actual game objective.

**Current status**

- Candidate discard bundles are now generated directly from the player hand and required discard count.
- The selected discard is compared against the best retained-hand value over all legal discard bundles.

## 4.18 `bank-trade-decision`

**Rubric**

- `0.50 resourceValueDelta + 0.30 buildUnlock + 0.20 opportunityCost`

**Current implementation shape**

This task is currently approximated through a choice-ratio over candidate bank/port trades plus a `no-trade` option.

For each candidate trade:

- compute trade ratio using `GameLogic.getTradeRatio`
- score candidate as `need[get] - 0.5 * surplus[give] + 0.5`
- clamp to `[0,1]`

Also include:

- `no-trade = 0.45`

Why this is defensible:

- getting a needed resource is positive
- spending a surplus resource is less painful than spending a scarce one
- the `no-trade` baseline prevents weak trades from automatically looking good just because they are legal

Current limitation:

- the three named rubric components are not separately logged yet
- they are compressed into the single trade desirability heuristic

## 4.19 `decide-pursue-longest-road`

**Current benchmark definition**

- `0.40 roadProgressRatio + 0.30 opponentRoadGap + 0.20 resourceAffordability + 0.10 VPImpact`

**Requested long-horizon redesign**

The proposed redesign is stronger than the current immediate heuristic. The intended v2 behavior is:

- record a pending task episode when this strategic choice is made
- wait 4-6 turns for the evaluated player
- score using snapshot-plus-outcome evidence

Requested v2 rubric:

- `0.45 blockingEffectiveness + 0.30 opponentProgressThreat + 0.15 selfRoadSynergy + 0.10 resourceCost`

This redesign is justified because Longest Road decisions are not fully observable from a single action. Their quality depends on whether the investment actually slowed the opponent or improved the agent's race position over the following turns.

**Current status**

- The task now runs as a pending long-horizon episode.
- A pursue/defer decision creates a pending record, and finalization writes one normal benchmark task result after the horizon, decisive resolution, or game end.

## 4.20 `decide-pursue-largest-army`

**Current benchmark definition**

- `0.40 knightCardRatio + 0.30 opponentArmyGap + 0.20 devCardAffordability + 0.10 VPImpact`

**Requested long-horizon redesign**

Requested v2 rubric:

- `0.40 knightCardRatio + 0.30 opponentArmyGap + 0.20 devCardAffordability + 0.10 VPImpact`

but evaluated over a 4-6 turn future window rather than immediately.

Why this is the right structure:

- Largest Army is not about a single dev-card purchase or single Knight.
- It is about whether a multi-turn commitment improved the agent's race standing without harming the broader economy too much.

The proposed hybrid intent-plus-outcome model is particularly appropriate here because:

- pure outcome scoring would overreact to deck order and dice luck
- pure immediate scoring would ignore whether the strategic plan actually materialized

**Current status**

- The task now runs as a pending long-horizon episode.
- Pursue/defer decisions are finalized into ordinary benchmark task results after the evaluation window closes.

## 5. Why Some Current Components Feel "Random"

The parts that currently feel most heuristic are the ones where the code uses fast proxies instead of explicit tactical search.

The main examples are:

- `blockingValue` currently fixed at `0`
- bank-trade, trade-response, and partner-selection scores that compress several conceptual rubric terms into a single simple formula

These choices were likely made to keep the first benchmark version lightweight and deterministic, but they should be described as **heuristic proxies**, not as exact strategic models.

## 6. Recommended Paper Framing

For the research paper, the most accurate framing is:

- the benchmark currently mixes **fully operational tactical rubrics** with **partially instrumented strategic rubrics**
- the short-term implemented tasks are strongest empirically because they are grounded in concrete legal-option comparisons
- several tasks still use heuristic proxies rather than search-heavy strategic models, but they are now runtime-instrumented
- the long-horizon episode mechanism for Longest Road and Largest Army is now the active implementation path for delayed strategic scoring

## 7. Code References

Main files:

- `Enviroment/server/benchmarkDefinitions.js`
- `Enviroment/server/benchmarkEvaluation.js`
- `Enviroment/server/benchmarkStore.js`
- `Enviroment/server/index.js`

Most of the concrete heuristic formulas described above come from:

- settlement, road, robber, trade, and bank-trade helper functions in `Enviroment/server/index.js`
- task scoring and pass thresholds in `Enviroment/server/benchmarkEvaluation.js`
