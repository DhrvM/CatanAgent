import assert from 'assert';
import path from 'path';
import { promises as fsp } from 'fs';
import { BenchmarkStore } from './benchmarkStore.js';
import { BENCHMARK_TASKS } from './benchmarkDefinitions.js';

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
    latencyMs: 1100,
    retries: 1,
    illegalAttempts: 1,
    response: {
      selectedOptionId: 'best',
    },
    evaluationContext: {
      legalOptions: [
        {
          id: 'best',
          resourceProductionExpectancy: 1,
          resourceDiversity: 0.9,
          expansionAccess: 0.9,
          portSynergy: 0.8,
        },
        {
          id: 'okay',
          resourceProductionExpectancy: 0.5,
          resourceDiversity: 0.4,
          expansionAccess: 0.4,
          portSynergy: 0.2,
        },
      ],
    },
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
    latencyMs: 900,
    retries: 0,
    illegalAttempts: 0,
    response: {
      selectedOptionId: 'best',
    },
    evaluationContext: {
      legalOptions: [
        {
          id: 'best',
          resourceProductionExpectancy: 1,
          resourceDiversity: 0.85,
          expansionAccess: 0.9,
          portSynergy: 0.75,
        },
        {
          id: 'risky',
          resourceProductionExpectancy: 0.55,
          resourceDiversity: 0.5,
          expansionAccess: 0.45,
          portSynergy: 0.15,
        },
      ],
    },
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
  assert(challenger.rawMetrics.averageLatencyPerTurn >= 0, 'Latency telemetry aggregated');
  assert(challenger.normalizedMetrics.taskSuccessRate > 0, 'Task success normalized absolutely');
  assert(challenger.metricStatuses.taskSuccessRate === 'excellent', 'Absolute metric status assigned');

  const detail = store.getEntityDetail('player', 'run-alpha:challenger');
  assert(detail.games.length === 1, 'Player detail includes games');
  assert(detail.taskResults.length === 1, 'Player detail includes task results');
  assert.strictEqual(detail.taskBreakdown.length, BENCHMARK_TASKS.length, 'Task breakdown includes all defined benchmark tasks');
  assert(detail.taskBreakdown.find(task => task.taskId === 'settlement-location-selection')?.averageScore > 0.9, 'Task breakdown includes average rubric score');
  assert(detail.taskBreakdown.find(task => task.taskId === 'road-placement-direction')?.attempts === 0, 'Unattempted tasks are still displayed');
  assert(detail.slices[0].normalizedMetrics.winRate >= 0, 'Slices expose normalized metrics');

  const originalScore = challenger.overallScore;
  await store.upsertRun({
    runId: 'run-alpha',
    baselineAgentId: 'different-bot',
  });
  const rescoredLeaderboard = store.getLeaderboard('agent');
  const rescoredChallenger = rescoredLeaderboard.entries.find(entry => entry.agentId === 'challenger-bot');
  assert.strictEqual(rescoredChallenger.overallScore, originalScore, 'Overall score is baseline-independent');

  const slices = store.getSliceComparisons();
  assert(slices.length > 0, 'Slice comparison data built');

  const liveLog = store.getLiveLog({ limit: 10 });
  assert(liveLog.entries.length >= 3, 'Live log includes task, action, and game events');
  assert(liveLog.entries.some(entry => entry.eventType === 'task'), 'Live log includes task entries');
  assert(liveLog.entries.some(entry => entry.eventType === 'action'), 'Live log includes action entries');
  assert(liveLog.entries.some(entry => entry.eventType === 'game'), 'Live log includes game completion entries');
  const taskLogEntry = liveLog.entries.find(entry => entry.eventType === 'task');
  const actionLogEntry = liveLog.entries.find(entry => entry.eventType === 'action');
  assert(taskLogEntry.status === 'passed', 'Task live log reflects evaluator pass/fail');
  assert(taskLogEntry.details.score > 0.9, 'Task live log exposes evaluator score');
  assert(actionLogEntry.status === 'accepted', 'Action live log reflects transport acceptance');
  assert(actionLogEntry.moveTimeMs >= 0, 'Action live log exposes move time');
  assert(actionLogEntry.processingLatencyMs >= 0, 'Action live log exposes server processing time');

  await store.clearAllData();
  const emptyOverview = store.getOverview();
  assert.strictEqual(emptyOverview.totals.runs, 0, 'Clear removes persisted runs');
  assert.strictEqual(emptyOverview.totals.games, 0, 'Clear removes persisted games');
  assert.strictEqual(emptyOverview.totals.telemetryRecords, 0, 'Clear removes telemetry');

  await cleanup();
  console.log('Benchmark observation tests passed');
}

main().catch(async error => {
  console.error(error);
  await cleanup();
  process.exit(1);
});
