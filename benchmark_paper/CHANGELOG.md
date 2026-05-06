# Benchmark Paper Changelog

Comparison: `draft benchmark paper.pdf` to current `benchmark_paper.pdf`.

## Added

- Added concrete benchmark validation evidence:
  - Human baseline from the Vayun-Sharayu benchmark-enabled game.
  - Agent baseline from 10 heuristic-bot self-play games.
  - ReAct GPT-4o agent comparison against the heuristic bot.
  - Task-review evidence using intentionally good and bad initial settlement placements.
- Added validation figures using the result images folder:
  - Human baseline game state and leaderboard.
  - Heuristic-bot baseline and ReAct-agent comparison.
  - Constructed placement board and benchmark task-review log.
- Added official Catan rules reference as the first bibliography entry:
  - Klaus Teuber, *Catan base game rules*, originally published 1995.
- Added clearer evaluation/reporting requirements:
  - Required metrics.
  - Task scoring definition.
  - Aggregation formula.
  - Required reporting fields.

## Changed

- Rewrote the Evaluation Protocol section into a concise four-part structure:
  - Metrics.
  - Task scoring.
  - Aggregation.
  - Reporting.
- Updated the validation section from general pilot-testing language to specific evidence-backed claims with screenshots and measured results.
- Updated figure organization:
  - Combined related validation images into paired subfigures.
  - Kept paired figures aligned and vertically centered.
  - Reduced figure sprawl by grouping related evidence into three validation figures.
- Refined the conclusion to reflect the validated 20-task live benchmark and future work on larger baselines and expert calibration.

## Fixed

- Fixed appendix overfull layout issues by breaking long equations across aligned lines.
- Fixed long task/formula expressions in the main text and appendix so the PDF compiles without overfull box warnings.
- Improved readability of long task IDs in appendix tables using breakable formatting.

## Net Effect

- The draft described CatanBench and its scoring design.
- The current paper now also demonstrates that the benchmark has been validated with human play, baseline agents, and task-review evidence.
- The current paper is more submission-ready because it explicitly documents how agents should be evaluated and what results must be reported.

