# Evaluation Resources

No external dataset is required to run the benchmark. The benchmark tasks, scoring rules, and metric weights are defined in code:

- `Environment/server/benchmarkDefinitions.js`
- `Environment/server/benchmarkEvaluation.js`
- `Environment/server/benchmarkStore.js`

The validation images used by the paper are stored in:

- `benchmark_paper/Result Images/`

Those images document the reported human baseline, agent baselines, and task-review evidence. They are not required at runtime, but they should be kept with the paper as audit artifacts.

Runtime benchmark records are generated locally by the server in:

- `Environment/server/benchmark-data/`

Self-play baseline summaries are generated in:

- `Agent/training/results/`

Exported benchmark API snapshots are generated in:

- `benchmark_paper/Submission/evaluation/results/`
