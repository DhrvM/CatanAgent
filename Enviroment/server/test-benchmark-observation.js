import assert from 'assert';
import path from 'path';
import { promises as fsp } from 'fs';
import { BenchmarkStore } from './benchmarkStore.js';

const tempDir = path.join(process.cwd(), 'benchmark-test-data');

async function cleanup() {
  await fsp.rm(tempDir, { recursive: true, force: true });
}

async function main() {
  await cleanup();
  const store = new BenchmarkStore({ dataDir: tempDir });
  await store.init();

  await store.upsertRun({
    runId: 'run-alpha',
    benchmarkId: 'catan-benchmark-v1',
    baselineAgentId: 'baseline-bot',
    mapLayoutId: 'map-a',
    opponentPolicySet: 'mixed-opponents',
    scenarioTags: ['alternate-map', 'resource-scarcity'],
    players: [
      {
        playerKey: 'run-alpha:baseline',
        agentId: 'baseline-bot',
        agentVersion: '1.0.0',
        playerName: 'Baseline',
        baseline: true,
        startingSeat: 0,
      },
      {
        playerKey: 'run-alpha:challenger',
        agentId: 'challenger-bot',
        agentVersion: '2.0.0',
        playerName: 'Challenger',
        baseline: false,
        startingSeat: 1,
      },
    ],
  });

  await store.recordTaskResult({
    runId: 'run-alpha',
    benchmarkId: 'catan-benchmark-v1',
    baselineAgentId: 'baseline-bot',
    taskId: 'settlement-location-selection',
    playerKey: 'run-alpha:baseline',
    playerName: 'Baseline',
    agentId: 'baseline-bot',
    agentVersion: '1.0.0',
    startingSeat: 0,
    mapLayoutId: 'map-a',
    opponentPolicySet: 'mixed-opponents',
    scenarioTags: ['alternate-map'],
    success: true,
    latencyMs: 1100,
    retries: 1,
    illegalAttempts: 1,
  });

  await store.recordTaskResult({
    runId: 'run-alpha',
    benchmarkId: 'catan-benchmark-v1',
    baselineAgentId: 'baseline-bot',
    taskId: 'settlement-location-selection',
    playerKey: 'run-alpha:challenger',
    playerName: 'Challenger',
    agentId: 'challenger-bot',
    agentVersion: '2.0.0',
    startingSeat: 1,
    mapLayoutId: 'map-a',
    opponentPolicySet: 'mixed-opponents',
    scenarioTags: ['alternate-map'],
    success: true,
    latencyMs: 900,
    retries: 0,
    illegalAttempts: 0,
  });

  const baseGame = {
    id: 'game-1',
    benchmark: {
      enabled: true,
      runId: 'run-alpha',
      benchmarkId: 'catan-benchmark-v1',
      baselineAgentId: 'baseline-bot',
      mapLayoutId: 'map-a',
      opponentPolicySet: 'mixed-opponents',
      scenarioTags: ['resource-scarcity'],
      players: [
        {
          playerId: 'baseline-player',
          playerKey: 'run-alpha:baseline',
          agentId: 'baseline-bot',
          agentVersion: '1.0.0',
          playerName: 'Baseline',
          baseline: true,
          startingSeat: 0,
        },
        {
          playerId: 'challenger-player',
          playerKey: 'run-alpha:challenger',
          agentId: 'challenger-bot',
          agentVersion: '2.0.0',
          playerName: 'Challenger',
          baseline: false,
          startingSeat: 1,
        },
      ],
    },
    players: [
      { id: 'baseline-player', name: 'Baseline', victoryPoints: 8 },
      { id: 'challenger-player', name: 'Challenger', victoryPoints: 10 },
    ],
    roundNumber: 12,
    currentPlayerIndex: 1,
    winner: 'challenger-player',
  };

  await store.registerBenchmarkGame(baseGame);
  store.startTurn(baseGame, 'challenger-player');
  await store.recordAction(baseGame, 'challenger-player', 'rollDice', {}, { success: true }, {
    requestStartedAt: Date.now() - 500,
    acknowledgedAt: Date.now(),
  });
  await store.completeGame(baseGame);

  const leaderboard = store.getLeaderboard('agent');
  const challenger = leaderboard.entries.find(entry => entry.agentId === 'challenger-bot');
  const baseline = leaderboard.entries.find(entry => entry.agentId === 'baseline-bot');

  assert(challenger, 'Challenger entry exists');
  assert(baseline, 'Baseline entry exists');
  assert.strictEqual(challenger.rawMetrics.winRate, 1, 'Challenger win rate computed');
  assert.strictEqual(baseline.rawMetrics.winRate, 0, 'Baseline win rate computed');
  assert.strictEqual(challenger.rawMetrics.averageLatencyPerTurn, 500, 'Latency telemetry aggregated');
  assert.strictEqual(challenger.rawMetrics.illegalMoveRate, 0, 'Illegal move rate aggregated');

  const detail = store.getEntityDetail('player', 'run-alpha:challenger');
  assert(detail.games.length === 1, 'Player detail includes games');
  assert(detail.taskResults.length === 1, 'Player detail includes task results');

  const slices = store.getSliceComparisons();
  assert(slices.length > 0, 'Slice comparison data built');

  await cleanup();
  console.log('Benchmark observation tests passed');
}

main().catch(async error => {
  console.error(error);
  await cleanup();
  process.exit(1);
});
