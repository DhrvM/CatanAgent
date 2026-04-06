import fs from 'fs';
import { promises as fsp } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { v4 as uuidv4 } from 'uuid';
import {
  BENCHMARK_TASKS,
  BENCHMARK_WEIGHTS,
  METRIC_DIRECTIONS,
  TASK_CATEGORIES,
  getTaskDefinition,
} from './benchmarkDefinitions.js';
import {
  computePrimaryComposite,
  computeSecondaryMetricsFromSlices,
  computeWeightedMetricScore,
  evaluateBenchmarkTask,
  metricStatus,
  normalizeMetricScore,
} from './benchmarkEvaluation.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DEFAULT_DATA_DIR = process.env.BENCHMARK_DATA_DIR || path.join(__dirname, 'benchmark-data');

const STORE_FILES = {
  runs: 'runs',
  games: 'games',
  tasks: 'tasks',
  pendingLongTermTasks: 'pending-long-term-tasks',
  telemetry: 'telemetry',
  snapshots: 'snapshots',
};

function jsonClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function safeNumber(value, fallback = 0) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}

function average(values) {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function variance(values) {
  if (values.length < 2) return 0;
  const mean = average(values);
  return average(values.map(value => (value - mean) ** 2)) || 0;
}

function safeDivide(numerator, denominator) {
  if (!denominator) return null;
  return numerator / denominator;
}

function sortScenarioTags(tags = []) {
  return [...new Set((tags || []).filter(Boolean))].sort();
}

function makeEntityAgentKey(agentId, agentVersion) {
  return `${agentId || 'unknown'}@${agentVersion || 'unversioned'}`;
}

function buildSliceKey(source) {
  return [
    source.benchmarkId || 'default-benchmark',
    source.taskId || source.gameType || 'gameplay',
    source.mapLayoutId || 'default-map',
    source.startingSeat ?? 'unknown-seat',
    source.opponentPolicySet || 'default-opponent',
    sortScenarioTags(source.scenarioTags).join(',') || 'no-scenarios',
  ].join('|');
}

function defaultFilters(rawFilters = {}) {
  return {
    benchmarkId: rawFilters.benchmarkId || null,
    taskCategory: rawFilters.taskCategory || null,
    baselineAgentId: rawFilters.baselineAgentId || null,
    mapLayoutId: rawFilters.mapLayoutId || null,
    startingSeat: rawFilters.startingSeat || null,
    opponentPolicySet: rawFilters.opponentPolicySet || null,
    runId: rawFilters.runId || null,
    search: rawFilters.search || null,
  };
}

function matchesFilters(record, filters) {
  if (filters.benchmarkId && record.benchmarkId !== filters.benchmarkId) return false;
  if (filters.baselineAgentId && record.baselineAgentId !== filters.baselineAgentId) return false;
  if (filters.mapLayoutId && record.mapLayoutId !== filters.mapLayoutId) return false;
  if (filters.startingSeat && String(record.startingSeat) !== String(filters.startingSeat)) return false;
  if (filters.opponentPolicySet && record.opponentPolicySet !== filters.opponentPolicySet) return false;
  if (filters.runId && record.runId !== filters.runId) return false;
  if (filters.taskCategory && record.taskCategory !== filters.taskCategory) return false;
  return true;
}

function deriveMapLayoutId(game) {
  if (!game?.hexes) return 'unknown-layout';
  return Object.entries(game.hexes)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([hexKey, hex]) => `${hexKey}:${hex.terrain}:${hex.number ?? 'x'}`)
    .join('|');
}

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

export class BenchmarkStore {
  constructor({ dataDir = DEFAULT_DATA_DIR } = {}) {
    this.dataDir = dataDir;
    this.dirMap = Object.fromEntries(
      Object.entries(STORE_FILES).map(([key, dirName]) => [key, path.join(this.dataDir, dirName)])
    );
    this.runs = new Map();
    this.games = new Map();
    this.tasks = new Map();
    this.pendingLongTermTasks = new Map();
    this.telemetry = new Map();
    this.snapshots = {
      updatedAt: null,
      overview: null,
      leaderboards: {
        agent: [],
        player: [],
      },
    };
    this.initialized = false;
  }

  async init() {
    if (this.initialized) return;
    await Promise.all(Object.values(this.dirMap).map(dir => fsp.mkdir(dir, { recursive: true })));
    await Promise.all([
      this.#loadCollection(this.dirMap.runs, this.runs),
      this.#loadCollection(this.dirMap.games, this.games),
      this.#loadCollection(this.dirMap.tasks, this.tasks),
      this.#loadCollection(this.dirMap.pendingLongTermTasks, this.pendingLongTermTasks),
      this.#loadCollection(this.dirMap.telemetry, this.telemetry),
    ]);
    await this.#loadSnapshot();
    this.initialized = true;
    await this.rebuildSnapshots();
  }

  async #loadCollection(dir, targetMap) {
    const entries = await fsp.readdir(dir).catch(() => []);
    await Promise.all(entries.filter(name => name.endsWith('.json')).map(async fileName => {
      const fullPath = path.join(dir, fileName);
      const raw = await fsp.readFile(fullPath, 'utf8');
      const parsed = JSON.parse(raw);
      const id = parsed.id || parsed.gameId || parsed.runId;
      if (id) {
        targetMap.set(id, parsed);
      }
    }));
  }

  async #loadSnapshot() {
    const snapshotPath = path.join(this.dirMap.snapshots, 'overview.json');
    if (!fs.existsSync(snapshotPath)) return;
    const raw = await fsp.readFile(snapshotPath, 'utf8');
    this.snapshots = JSON.parse(raw);
  }

  async #writeJson(filePath, payload) {
    await fsp.writeFile(filePath, JSON.stringify(payload, null, 2), 'utf8');
  }

  async #persistRun(run) {
    await this.#writeJson(path.join(this.dirMap.runs, `${run.runId}.json`), run);
  }

  async #persistGame(game) {
    await this.#writeJson(path.join(this.dirMap.games, `${game.gameId}.json`), game);
  }

  async #persistTask(task) {
    await this.#writeJson(path.join(this.dirMap.tasks, `${task.id}.json`), task);
  }

  async #persistPendingLongTermTask(task) {
    await this.#writeJson(path.join(this.dirMap.pendingLongTermTasks, `${task.id}.json`), task);
  }

  async #deletePendingLongTermTask(id) {
    await fsp.unlink(path.join(this.dirMap.pendingLongTermTasks, `${id}.json`)).catch(() => {});
  }

  async #persistTelemetry(gameId) {
    const payload = {
      gameId,
      records: this.telemetry.get(gameId) || [],
    };
    await this.#writeJson(path.join(this.dirMap.telemetry, `${gameId}.json`), payload);
  }

  async #persistSnapshot() {
    const payload = {
      ...this.snapshots,
      tasks: BENCHMARK_TASKS,
    };
    await this.#writeJson(path.join(this.dirMap.snapshots, 'overview.json'), payload);
  }

  async #clearJsonFiles(dir) {
    const entries = await fsp.readdir(dir).catch(() => []);
    await Promise.all(
      entries
        .filter(name => name.endsWith('.json'))
        .map(fileName => fsp.unlink(path.join(dir, fileName)).catch(() => {}))
    );
  }

  createOrUpdateRun(payload = {}) {
    const runId = payload.runId || uuidv4();
    const existing = this.runs.get(runId);
    const players = ensureArray(payload.players).map((player, index) => ({
      playerKey: player.playerKey || `${runId}:${player.agentId || player.name || `player-${index + 1}`}:${index}`,
      agentId: player.agentId || player.name || `agent-${index + 1}`,
      agentVersion: player.agentVersion || 'unknown',
      playerName: player.playerName || player.name || player.agentId || `Player ${index + 1}`,
      baseline: Boolean(player.baseline) || payload.baselineAgentId === player.agentId,
      startingSeat: player.startingSeat ?? index,
    }));

    const run = {
      runId,
      benchmarkId: payload.benchmarkId || existing?.benchmarkId || 'default-benchmark',
      baselineAgentId: payload.baselineAgentId || existing?.baselineAgentId || null,
      mapLayoutId: payload.mapLayoutId || existing?.mapLayoutId || 'default-map',
      opponentPolicySet: payload.opponentPolicySet || existing?.opponentPolicySet || 'default-opponent',
      scenarioTags: sortScenarioTags(payload.scenarioTags || existing?.scenarioTags || []),
      gameType: payload.gameType || existing?.gameType || 'full-game',
      taskId: payload.taskId || existing?.taskId || null,
      runLabel: payload.runLabel || existing?.runLabel || payload.benchmarkId || 'Benchmark Run',
      createdAt: existing?.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      players: players.length ? players : (existing?.players || []),
      notes: payload.notes || existing?.notes || '',
      metadata: {
        ...existing?.metadata,
        ...payload.metadata,
      },
    };

    this.runs.set(runId, run);
    return run;
  }

  async upsertRun(payload = {}) {
    const run = this.createOrUpdateRun(payload);
    await this.#persistRun(run);
    await this.rebuildSnapshots();
    return run;
  }

  async clearAllData() {
    this.runs.clear();
    this.games.clear();
    this.tasks.clear();
    this.pendingLongTermTasks.clear();
    this.telemetry.clear();
    this.snapshots = {
      updatedAt: null,
      overview: null,
      leaderboards: {
        agent: [],
        player: [],
      },
    };

    await Promise.all([
      this.#clearJsonFiles(this.dirMap.runs),
      this.#clearJsonFiles(this.dirMap.games),
      this.#clearJsonFiles(this.dirMap.tasks),
      this.#clearJsonFiles(this.dirMap.pendingLongTermTasks),
      this.#clearJsonFiles(this.dirMap.telemetry),
      this.#clearJsonFiles(this.dirMap.snapshots),
    ]);

    await this.rebuildSnapshots();
  }

  getOrCreateBenchmarkGame(game, options = {}) {
    const benchmark = game.benchmark || options.benchmark || {};
    if (!benchmark.enabled) return null;

    const run = this.createOrUpdateRun({
      runId: benchmark.runId,
      benchmarkId: benchmark.benchmarkId,
      baselineAgentId: benchmark.baselineAgentId,
      mapLayoutId: benchmark.mapLayoutId || deriveMapLayoutId(game),
      opponentPolicySet: benchmark.opponentPolicySet,
      scenarioTags: benchmark.scenarioTags,
      gameType: benchmark.gameType || 'full-game',
      taskId: benchmark.taskId || null,
      players: (benchmark.players || game.players || []).map((player, index) => ({
        playerKey: player.playerKey || player.benchmarkPlayerKey,
        agentId: player.agentId || player.benchmarkAgentId || player.name,
        agentVersion: player.agentVersion || player.benchmarkAgentVersion || 'unknown',
        playerName: player.playerName || player.name,
        baseline: Boolean(player.baseline) || benchmark.baselineAgentId === (player.agentId || player.benchmarkAgentId || player.name),
        startingSeat: player.startingSeat ?? index,
      })),
    });

    const existing = this.games.get(game.id);
    const gameRecord = {
      gameId: game.id,
      runId: run.runId,
      benchmarkId: run.benchmarkId,
      baselineAgentId: run.baselineAgentId,
      gameType: benchmark.gameType || run.gameType || 'full-game',
      taskId: benchmark.taskId || run.taskId || null,
      taskCategory: getTaskDefinition(benchmark.taskId || run.taskId)?.category || null,
      mapLayoutId: benchmark.mapLayoutId || run.mapLayoutId || deriveMapLayoutId(game),
      opponentPolicySet: benchmark.opponentPolicySet || run.opponentPolicySet || 'default-opponent',
      scenarioTags: sortScenarioTags(benchmark.scenarioTags || run.scenarioTags || []),
      createdAt: existing?.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      startedAt: existing?.startedAt || null,
      finishedAt: existing?.finishedAt || null,
      winnerId: existing?.winnerId || null,
      roundsPlayed: existing?.roundsPlayed || null,
      players: (game.players || []).map((player, index) => {
        const playerConfig = (benchmark.players || []).find(candidate => candidate.playerId === player.id)
          || (run.players || []).find(candidate => candidate.startingSeat === index)
          || {};
        return {
          playerId: player.id,
          playerKey: playerConfig.playerKey || player.benchmarkPlayerKey || `${run.runId}:${player.id}`,
          playerName: player.name,
          agentId: playerConfig.agentId || player.benchmarkAgentId || player.name,
          agentVersion: playerConfig.agentVersion || player.benchmarkAgentVersion || 'unknown',
          startingSeat: playerConfig.startingSeat ?? index,
          baseline: Boolean(playerConfig.baseline) || run.baselineAgentId === (playerConfig.agentId || player.benchmarkAgentId || player.name),
          finalVictoryPoints: null,
          won: false,
        };
      }),
      turnStates: existing?.turnStates || {},
      actionAttemptsByTurn: existing?.actionAttemptsByTurn || {},
      metadata: {
        ...existing?.metadata,
        ...(benchmark.metadata || {}),
      },
    };

    this.runs.set(run.runId, run);
    this.games.set(game.id, gameRecord);
    if (!this.telemetry.has(game.id)) {
      this.telemetry.set(game.id, []);
    }
    return gameRecord;
  }

  async registerBenchmarkGame(game, options = {}) {
    const gameRecord = this.getOrCreateBenchmarkGame(game, options);
    if (!gameRecord) return null;
    gameRecord.startedAt = gameRecord.startedAt || new Date().toISOString();
    gameRecord.updatedAt = new Date().toISOString();
    await Promise.all([
      this.#persistRun(this.runs.get(gameRecord.runId)),
      this.#persistGame(gameRecord),
      this.#persistTelemetry(gameRecord.gameId),
    ]);
    await this.rebuildSnapshots();
    return gameRecord;
  }

  startTurn(game, playerId) {
    const gameRecord = this.games.get(game.id);
    if (!gameRecord) return;

    const currentPlayer = gameRecord.players.find(candidate => candidate.playerId === playerId);
    if (!currentPlayer) return;

    const turnId = `${game.id}:${safeNumber(game.roundNumber, 0)}:${safeNumber(game.currentPlayerIndex, 0)}`;
    gameRecord.turnStates[playerId] = {
      turnId,
      roundNumber: safeNumber(game.roundNumber, 0),
      startedAt: Date.now(),
      startingSeat: currentPlayer.startingSeat,
      playerKey: currentPlayer.playerKey,
      agentId: currentPlayer.agentId,
      agentVersion: currentPlayer.agentVersion,
    };
    if (!gameRecord.actionAttemptsByTurn[turnId]) {
      gameRecord.actionAttemptsByTurn[turnId] = 0;
    }
  }

  async recordAction(game, playerId, actionName, payload, result, timing = {}) {
    const gameRecord = this.games.get(game.id);
    if (!gameRecord) return null;

    const playerRecord = gameRecord.players.find(player => player.playerId === playerId);
    if (!playerRecord) return null;

    const turnState = gameRecord.turnStates[playerId] || {
      turnId: `${game.id}:${safeNumber(game.roundNumber, 0)}:${safeNumber(game.currentPlayerIndex, 0)}`,
      roundNumber: safeNumber(game.roundNumber, 0),
      startedAt: Date.now(),
      startingSeat: playerRecord.startingSeat,
      playerKey: playerRecord.playerKey,
      agentId: playerRecord.agentId,
      agentVersion: playerRecord.agentVersion,
    };

    const requestStartedAt = timing.requestStartedAt || Date.now();
    const acknowledgedAt = timing.acknowledgedAt || Date.now();
    const clientTimestamp = timing.clientTimestamp || null;
    const processingLatencyMs = clientTimestamp
      ? Math.max(0, acknowledgedAt - clientTimestamp)
      : Math.max(0, acknowledgedAt - requestStartedAt);
    const decisionTimeMs = Math.max(0, acknowledgedAt - safeNumber(turnState.startedAt, acknowledgedAt));

    const retryIndex = gameRecord.actionAttemptsByTurn[turnState.turnId] || 0;
    gameRecord.actionAttemptsByTurn[turnState.turnId] = retryIndex + 1;

    const telemetryRecord = {
      id: uuidv4(),
      gameId: game.id,
      runId: gameRecord.runId,
      benchmarkId: gameRecord.benchmarkId,
      baselineAgentId: gameRecord.baselineAgentId,
      playerId,
      playerKey: playerRecord.playerKey,
      playerName: playerRecord.playerName,
      agentId: playerRecord.agentId,
      agentVersion: playerRecord.agentVersion,
      actionName,
      accepted: Boolean(result?.success),
      legal: Boolean(result?.success),
      retryIndex,
      turnId: turnState.turnId,
      roundNumber: turnState.roundNumber,
      startingSeat: playerRecord.startingSeat,
      taskId: gameRecord.taskId,
      taskCategory: gameRecord.taskCategory,
      gameType: gameRecord.gameType,
      mapLayoutId: gameRecord.mapLayoutId,
      opponentPolicySet: gameRecord.opponentPolicySet,
      scenarioTags: gameRecord.scenarioTags,
      latencyMs: decisionTimeMs,
      processingLatencyMs,
      decisionTimeMs,
      requestStartedAt: new Date(requestStartedAt).toISOString(),
      acknowledgedAt: new Date(acknowledgedAt).toISOString(),
      payload: payload || {},
      result: result || {},
    };

    const records = this.telemetry.get(game.id) || [];
    records.push(telemetryRecord);
    this.telemetry.set(game.id, records);
    if (result?.success) {
      gameRecord.turnStates[playerId] = {
        ...turnState,
        startedAt: acknowledgedAt,
      };
    }
    gameRecord.updatedAt = new Date().toISOString();

    await Promise.all([
      this.#persistGame(gameRecord),
      this.#persistTelemetry(game.id),
    ]);
    await this.rebuildSnapshots();
    return telemetryRecord;
  }

  async completeGame(game) {
    const gameRecord = this.games.get(game.id);
    if (!gameRecord) return null;

    gameRecord.updatedAt = new Date().toISOString();
    gameRecord.finishedAt = new Date().toISOString();
    gameRecord.winnerId = game.winner || null;
    gameRecord.roundsPlayed = safeNumber(game.roundNumber, null);
    gameRecord.players = gameRecord.players.map(playerRecord => {
      const statePlayer = game.players.find(player => player.id === playerRecord.playerId);
      return {
        ...playerRecord,
        finalVictoryPoints: statePlayer ? safeNumber(statePlayer.victoryPoints, 0) : 0,
        won: Boolean(statePlayer && game.winner === statePlayer.id),
      };
    });

    await this.#persistGame(gameRecord);
    await this.rebuildSnapshots();
    return gameRecord;
  }

  async recordTaskResult(payload = {}) {
    const run = this.createOrUpdateRun({
      runId: payload.runId,
      benchmarkId: payload.benchmarkId,
      baselineAgentId: payload.baselineAgentId,
      mapLayoutId: payload.mapLayoutId,
      opponentPolicySet: payload.opponentPolicySet,
      scenarioTags: payload.scenarioTags,
      taskId: payload.taskId,
      gameType: payload.gameType || 'reasoning-task',
      players: payload.players || [
        {
          playerKey: payload.playerKey,
          agentId: payload.agentId,
          agentVersion: payload.agentVersion,
          playerName: payload.playerName || payload.agentId,
          baseline: payload.baselineAgentId === payload.agentId,
          startingSeat: payload.startingSeat ?? 0,
        },
      ],
    });

    const definition = getTaskDefinition(payload.taskId);
    const id = payload.id || uuidv4();
    const evaluation = evaluateBenchmarkTask(payload.taskId, payload);
    const taskResult = {
      id,
      runId: run.runId,
      benchmarkId: run.benchmarkId,
      baselineAgentId: payload.baselineAgentId || run.baselineAgentId,
      taskId: payload.taskId,
      taskName: definition?.name || payload.taskName || payload.taskId,
      taskCategory: definition?.category || payload.taskCategory || null,
      difficulty: definition?.difficulty || payload.difficulty || 'unknown',
      passed: evaluation.passed,
      success: evaluation.passed,
      score: evaluation.score,
      latencyMs: payload.latencyMs ?? null,
      retries: payload.retries ?? 0,
      illegalAttempts: payload.illegalAttempts ?? 0,
      explanation: evaluation.explanation || payload.explanation || null,
      evaluationDetails: evaluation.evaluationDetails || null,
      playerKey: payload.playerKey || `${run.runId}:${payload.agentId || 'unknown-agent'}`,
      playerName: payload.playerName || payload.agentId || 'Unknown Player',
      agentId: payload.agentId || 'unknown-agent',
      agentVersion: payload.agentVersion || 'unknown',
      startingSeat: payload.startingSeat ?? 0,
      mapLayoutId: payload.mapLayoutId || run.mapLayoutId,
      opponentPolicySet: payload.opponentPolicySet || run.opponentPolicySet,
      scenarioTags: sortScenarioTags(payload.scenarioTags || run.scenarioTags || []),
      gameType: payload.gameType || 'reasoning-task',
      createdAt: payload.createdAt || new Date().toISOString(),
      response: payload.response || null,
      evaluationContext: payload.evaluationContext || null,
    };

    this.runs.set(run.runId, run);
    this.tasks.set(id, taskResult);
    await Promise.all([
      this.#persistRun(run),
      this.#persistTask(taskResult),
    ]);
    await this.rebuildSnapshots();
    return taskResult;
  }

  async createPendingLongTermTask(payload = {}) {
    const definition = getTaskDefinition(payload.taskId);
    const id = payload.id || uuidv4();
    const pendingTask = {
      id,
      runId: payload.runId || null,
      benchmarkId: payload.benchmarkId || null,
      baselineAgentId: payload.baselineAgentId || null,
      gameId: payload.gameId || null,
      taskId: payload.taskId,
      taskName: definition?.name || payload.taskName || payload.taskId,
      taskCategory: definition?.category || payload.taskCategory || null,
      difficulty: definition?.difficulty || payload.difficulty || 'unknown',
      playerKey: payload.playerKey || null,
      playerId: payload.playerId || null,
      playerName: payload.playerName || null,
      agentId: payload.agentId || null,
      agentVersion: payload.agentVersion || null,
      startingSeat: payload.startingSeat ?? 0,
      mapLayoutId: payload.mapLayoutId || null,
      opponentPolicySet: payload.opponentPolicySet || null,
      scenarioTags: sortScenarioTags(payload.scenarioTags || []),
      selectedOptionId: payload.selectedOptionId || null,
      decisionTurn: payload.decisionTurn ?? null,
      decisionTurnCount: payload.decisionTurnCount ?? null,
      horizonTurns: payload.horizonTurns ?? null,
      snapshot: payload.snapshot || null,
      evaluationContext: payload.evaluationContext || null,
      metadata: payload.metadata || {},
      createdAt: payload.createdAt || new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };

    this.pendingLongTermTasks.set(id, pendingTask);
    await this.#persistPendingLongTermTask(pendingTask);
    return pendingTask;
  }

  listPendingLongTermTasks(filters = {}) {
    return [...this.pendingLongTermTasks.values()].filter(task => {
      if (filters.gameId && task.gameId !== filters.gameId) return false;
      if (filters.taskId && task.taskId !== filters.taskId) return false;
      if (filters.playerId && task.playerId !== filters.playerId) return false;
      if (filters.playerKey && task.playerKey !== filters.playerKey) return false;
      return true;
    });
  }

  async removePendingLongTermTask(id) {
    this.pendingLongTermTasks.delete(id);
    await this.#deletePendingLongTermTask(id);
  }

  collectFilters() {
    const runs = [...this.runs.values()];
    const games = [...this.games.values()];
    const tasks = [...this.tasks.values()];
    const all = [...runs, ...games, ...tasks];

    return {
      benchmarkIds: [...new Set(all.map(item => item.benchmarkId).filter(Boolean))].sort(),
      baselineAgentIds: [...new Set(all.map(item => item.baselineAgentId).filter(Boolean))].sort(),
      mapLayoutIds: [...new Set(all.map(item => item.mapLayoutId).filter(Boolean))].sort(),
      opponentPolicySets: [...new Set(all.map(item => item.opponentPolicySet).filter(Boolean))].sort(),
      runIds: runs.map(run => ({
        runId: run.runId,
        label: run.runLabel || run.runId,
      })),
      taskCategories: Object.entries(TASK_CATEGORIES).map(([value, label]) => ({ value, label })),
    };
  }

  #buildEntityAccumulator(entityType, entityKey, displayName, meta = {}) {
    return {
      entityType,
      entityKey,
      displayName,
      agentId: meta.agentId || null,
      agentVersion: meta.agentVersion || null,
      playerKey: meta.playerKey || null,
      playerName: meta.playerName || null,
      benchmarkIds: new Set(),
      runIds: new Set(),
      mapLayoutIds: new Set(),
      opponentPolicySets: new Set(),
      scenarioTags: new Set(),
      slices: new Map(),
      gameplay: {
        gamesPlayed: 0,
        wins: 0,
        finalVictoryPoints: [],
        roundsToWin: [],
      },
      reasoning: {
        tasks: 0,
        successes: 0,
        scores: [],
      },
      operations: {
        latencies: [],
        illegalAttempts: 0,
        totalAttempts: 0,
      },
      taskBreakdown: new Map(),
      games: [],
      taskResults: [],
      telemetryRecords: [],
    };
  }

  #getSliceAccumulator(entity, source) {
    const sliceKey = buildSliceKey(source);
    if (!entity.slices.has(sliceKey)) {
      entity.slices.set(sliceKey, {
        sliceKey,
        benchmarkId: source.benchmarkId || null,
        baselineAgentId: source.baselineAgentId || null,
        taskId: source.taskId || null,
        taskCategory: source.taskCategory || null,
        gameType: source.gameType || null,
        mapLayoutId: source.mapLayoutId || null,
        startingSeat: source.startingSeat ?? null,
        opponentPolicySet: source.opponentPolicySet || null,
        scenarioTags: sortScenarioTags(source.scenarioTags || []),
        wins: 0,
        gamesPlayed: 0,
        finalVictoryPoints: [],
        roundsToWin: [],
        tasks: 0,
        successes: 0,
        taskScores: [],
        latencies: [],
        illegalAttempts: 0,
        totalAttempts: 0,
      });
    }
    return entity.slices.get(sliceKey);
  }

  #accumulateGame(entity, gameRecord, playerRecord) {
    entity.benchmarkIds.add(gameRecord.benchmarkId);
    entity.runIds.add(gameRecord.runId);
    entity.mapLayoutIds.add(gameRecord.mapLayoutId);
    entity.opponentPolicySets.add(gameRecord.opponentPolicySet);
    gameRecord.scenarioTags.forEach(tag => entity.scenarioTags.add(tag));

    entity.gameplay.gamesPlayed += 1;
    entity.gameplay.finalVictoryPoints.push(safeNumber(playerRecord.finalVictoryPoints, 0));
    if (playerRecord.won) {
      entity.gameplay.wins += 1;
      if (gameRecord.roundsPlayed !== null) {
        entity.gameplay.roundsToWin.push(gameRecord.roundsPlayed);
      }
    }
    entity.games.push({
      gameId: gameRecord.gameId,
      runId: gameRecord.runId,
      benchmarkId: gameRecord.benchmarkId,
      playerKey: playerRecord.playerKey,
      playerName: playerRecord.playerName,
      agentId: playerRecord.agentId,
      agentVersion: playerRecord.agentVersion,
      won: playerRecord.won,
      finalVictoryPoints: playerRecord.finalVictoryPoints,
      roundsPlayed: gameRecord.roundsPlayed,
      taskId: gameRecord.taskId,
      taskCategory: gameRecord.taskCategory,
      mapLayoutId: gameRecord.mapLayoutId,
      startingSeat: playerRecord.startingSeat,
      opponentPolicySet: gameRecord.opponentPolicySet,
      scenarioTags: gameRecord.scenarioTags,
      finishedAt: gameRecord.finishedAt,
    });

    const slice = this.#getSliceAccumulator(entity, {
      ...gameRecord,
      startingSeat: playerRecord.startingSeat,
    });
    slice.gamesPlayed += 1;
    slice.finalVictoryPoints.push(safeNumber(playerRecord.finalVictoryPoints, 0));
    if (playerRecord.won) {
      slice.wins += 1;
      if (gameRecord.roundsPlayed !== null) {
        slice.roundsToWin.push(gameRecord.roundsPlayed);
      }
    }
  }

  #accumulateTask(entity, taskResult) {
    entity.benchmarkIds.add(taskResult.benchmarkId);
    entity.runIds.add(taskResult.runId);
    entity.mapLayoutIds.add(taskResult.mapLayoutId);
    entity.opponentPolicySets.add(taskResult.opponentPolicySet);
    taskResult.scenarioTags.forEach(tag => entity.scenarioTags.add(tag));

    entity.reasoning.tasks += 1;
    entity.reasoning.successes += taskResult.success ? 1 : 0;
    entity.reasoning.scores.push(safeNumber(taskResult.score, 0));
    entity.taskResults.push(taskResult);

    const taskBreakdown = entity.taskBreakdown.get(taskResult.taskId) || {
      taskId: taskResult.taskId,
      taskName: taskResult.taskName,
      taskCategory: taskResult.taskCategory,
      difficulty: taskResult.difficulty,
      attempts: 0,
      successes: 0,
      scores: [],
      latencies: [],
      failureReasons: new Map(),
    };
    taskBreakdown.attempts += 1;
    taskBreakdown.successes += taskResult.success ? 1 : 0;
    taskBreakdown.scores.push(safeNumber(taskResult.score, 0));
    if (taskResult.latencyMs !== null) {
      taskBreakdown.latencies.push(taskResult.latencyMs);
    }
    if (!taskResult.success) {
      const failureReason = taskResult.explanation || 'Task rubric not satisfied';
      taskBreakdown.failureReasons.set(failureReason, (taskBreakdown.failureReasons.get(failureReason) || 0) + 1);
    }
    entity.taskBreakdown.set(taskResult.taskId, taskBreakdown);

    const slice = this.#getSliceAccumulator(entity, taskResult);
    slice.tasks += 1;
    slice.successes += taskResult.success ? 1 : 0;
    slice.taskScores.push(safeNumber(taskResult.score, 0));
    if (taskResult.latencyMs !== null) {
      slice.latencies.push(taskResult.latencyMs);
    }
    slice.illegalAttempts += safeNumber(taskResult.illegalAttempts, 0);
    slice.totalAttempts += 1 + safeNumber(taskResult.illegalAttempts, 0);
  }

  #accumulateTelemetry(entity, telemetryRecord) {
    entity.benchmarkIds.add(telemetryRecord.benchmarkId);
    entity.runIds.add(telemetryRecord.runId);
    entity.mapLayoutIds.add(telemetryRecord.mapLayoutId);
    entity.opponentPolicySets.add(telemetryRecord.opponentPolicySet);
    telemetryRecord.scenarioTags.forEach(tag => entity.scenarioTags.add(tag));

    entity.operations.totalAttempts += 1;
    if (!telemetryRecord.legal) {
      entity.operations.illegalAttempts += 1;
    }
    if (telemetryRecord.latencyMs !== null) {
      entity.operations.latencies.push(telemetryRecord.latencyMs);
    }
    entity.telemetryRecords.push(telemetryRecord);

    const slice = this.#getSliceAccumulator(entity, telemetryRecord);
    slice.totalAttempts += 1;
    if (!telemetryRecord.legal) {
      slice.illegalAttempts += 1;
    }
    if (telemetryRecord.latencyMs !== null) {
      slice.latencies.push(telemetryRecord.latencyMs);
    }
  }

  #buildEntityMaps(filters) {
    const entityMaps = {
      agent: new Map(),
      player: new Map(),
    };

    const games = [...this.games.values()].filter(game => matchesFilters(game, filters));
    const tasks = [...this.tasks.values()].filter(task => matchesFilters(task, filters));
    const telemetry = [...this.telemetry.values()]
      .flatMap(payload => ensureArray(payload.records || payload))
      .filter(record => matchesFilters(record, filters));

    games.forEach(gameRecord => {
      gameRecord.players.forEach(playerRecord => {
        const searchTarget = `${playerRecord.playerName} ${playerRecord.agentId} ${playerRecord.agentVersion}`;
        if (filters.search && !searchTarget.toLowerCase().includes(filters.search.toLowerCase())) {
          return;
        }

        const agentKey = makeEntityAgentKey(playerRecord.agentId, playerRecord.agentVersion);
        if (!entityMaps.agent.has(agentKey)) {
          entityMaps.agent.set(
            agentKey,
            this.#buildEntityAccumulator('agent', agentKey, playerRecord.agentId, {
              agentId: playerRecord.agentId,
              agentVersion: playerRecord.agentVersion,
            })
          );
        }
        if (!entityMaps.player.has(playerRecord.playerKey)) {
          entityMaps.player.set(
            playerRecord.playerKey,
            this.#buildEntityAccumulator('player', playerRecord.playerKey, playerRecord.playerName, {
              agentId: playerRecord.agentId,
              agentVersion: playerRecord.agentVersion,
              playerKey: playerRecord.playerKey,
              playerName: playerRecord.playerName,
            })
          );
        }

        this.#accumulateGame(entityMaps.agent.get(agentKey), gameRecord, playerRecord);
        this.#accumulateGame(entityMaps.player.get(playerRecord.playerKey), gameRecord, playerRecord);
      });
    });

    tasks.forEach(taskResult => {
      const searchTarget = `${taskResult.playerName} ${taskResult.agentId} ${taskResult.agentVersion} ${taskResult.taskName}`;
      if (filters.search && !searchTarget.toLowerCase().includes(filters.search.toLowerCase())) {
        return;
      }

      const agentKey = makeEntityAgentKey(taskResult.agentId, taskResult.agentVersion);
      if (!entityMaps.agent.has(agentKey)) {
        entityMaps.agent.set(
          agentKey,
          this.#buildEntityAccumulator('agent', agentKey, taskResult.agentId, {
            agentId: taskResult.agentId,
            agentVersion: taskResult.agentVersion,
          })
        );
      }
      if (!entityMaps.player.has(taskResult.playerKey)) {
        entityMaps.player.set(
          taskResult.playerKey,
          this.#buildEntityAccumulator('player', taskResult.playerKey, taskResult.playerName, {
            agentId: taskResult.agentId,
            agentVersion: taskResult.agentVersion,
            playerKey: taskResult.playerKey,
            playerName: taskResult.playerName,
          })
        );
      }

      this.#accumulateTask(entityMaps.agent.get(agentKey), taskResult);
      this.#accumulateTask(entityMaps.player.get(taskResult.playerKey), taskResult);
    });

    telemetry.forEach(record => {
      const agentKey = makeEntityAgentKey(record.agentId, record.agentVersion);
      if (!entityMaps.agent.has(agentKey)) {
        entityMaps.agent.set(
          agentKey,
          this.#buildEntityAccumulator('agent', agentKey, record.agentId, {
            agentId: record.agentId,
            agentVersion: record.agentVersion,
          })
        );
      }
      if (!entityMaps.player.has(record.playerKey)) {
        entityMaps.player.set(
          record.playerKey,
          this.#buildEntityAccumulator('player', record.playerKey, record.playerName, {
            agentId: record.agentId,
            agentVersion: record.agentVersion,
            playerKey: record.playerKey,
            playerName: record.playerName,
          })
        );
      }

      this.#accumulateTelemetry(entityMaps.agent.get(agentKey), record);
      this.#accumulateTelemetry(entityMaps.player.get(record.playerKey), record);
    });

    return entityMaps;
  }

  #finalizeSliceMetrics(slice) {
    return {
      winRate: safeDivide(slice.wins, slice.gamesPlayed),
      averageFinalVictoryPoints: average(slice.finalVictoryPoints),
      averageRoundsToWin: average(slice.roundsToWin),
      taskSuccessRate: safeDivide(slice.successes, slice.tasks),
      averageTaskScore: average(slice.taskScores),
      averageLatencyPerTurn: average(slice.latencies),
      illegalMoveRate: safeDivide(slice.illegalAttempts, slice.totalAttempts),
    };
  }

  #createRunSliceAccumulator(source) {
    return {
      runId: source.runId || null,
      benchmarkId: source.benchmarkId || null,
      taskId: source.taskId || null,
      taskCategory: source.taskCategory || null,
      gameType: source.gameType || null,
      mapLayoutId: source.mapLayoutId || null,
      startingSeat: source.startingSeat ?? null,
      opponentPolicySet: source.opponentPolicySet || null,
      scenarioTags: sortScenarioTags(source.scenarioTags || []),
      wins: 0,
      gamesPlayed: 0,
      finalVictoryPoints: [],
      roundsToWin: [],
      tasks: 0,
      successes: 0,
      taskScores: [],
      latencies: [],
      illegalAttempts: 0,
      totalAttempts: 0,
    };
  }

  #buildRunSlices(entity) {
    const runSlices = new Map();
    const getRunSlice = source => {
      const key = [
        source.runId || 'unknown-run',
        source.benchmarkId || 'default-benchmark',
        source.taskId || source.gameType || 'gameplay',
        source.mapLayoutId || 'default-map',
        source.startingSeat ?? 'unknown-seat',
        source.opponentPolicySet || 'default-opponent',
        sortScenarioTags(source.scenarioTags).join(',') || 'no-scenarios',
      ].join('|');
      if (!runSlices.has(key)) {
        runSlices.set(key, this.#createRunSliceAccumulator(source));
      }
      return runSlices.get(key);
    };

    entity.games.forEach(gameRecord => {
      const slice = getRunSlice({
        ...gameRecord,
        gameType: gameRecord.taskId ? 'reasoning-task' : 'full-game',
      });
      slice.gamesPlayed += 1;
      slice.finalVictoryPoints.push(safeNumber(gameRecord.finalVictoryPoints, 0));
      if (gameRecord.won) {
        slice.wins += 1;
        if (gameRecord.roundsPlayed !== null) {
          slice.roundsToWin.push(gameRecord.roundsPlayed);
        }
      }
    });

    entity.taskResults.forEach(taskResult => {
      const slice = getRunSlice(taskResult);
      slice.tasks += 1;
      slice.successes += taskResult.success ? 1 : 0;
      slice.taskScores.push(safeNumber(taskResult.score, 0));
      if (taskResult.latencyMs !== null) {
        slice.latencies.push(taskResult.latencyMs);
      }
      slice.illegalAttempts += safeNumber(taskResult.illegalAttempts, 0);
      slice.totalAttempts += 1 + safeNumber(taskResult.illegalAttempts, 0);
    });

    entity.telemetryRecords.forEach(record => {
      const slice = getRunSlice(record);
      slice.totalAttempts += 1;
      if (!record.legal) {
        slice.illegalAttempts += 1;
      }
      if (record.latencyMs !== null) {
        slice.latencies.push(record.latencyMs);
      }
    });

    return [...runSlices.values()].map(slice => {
      const metrics = this.#finalizeSliceMetrics(slice);
      const normalizedMetrics = Object.fromEntries(
        Object.keys(BENCHMARK_WEIGHTS).map(metric => [
          metric,
          Object.prototype.hasOwnProperty.call(metrics, metric) ? normalizeMetricScore(metric, metrics[metric]) : 0,
        ])
      );
      return {
        ...slice,
        metrics,
        normalizedMetrics,
        primaryCompositeScore: computePrimaryComposite(normalizedMetrics),
        configKey: [
          slice.benchmarkId || 'default-benchmark',
          slice.taskId || slice.gameType || 'gameplay',
          slice.mapLayoutId || 'default-map',
          slice.startingSeat ?? 'unknown-seat',
          slice.opponentPolicySet || 'default-opponent',
          sortScenarioTags(slice.scenarioTags).join(',') || 'no-scenarios',
        ].join('|'),
      };
    });
  }

  #finalizeEntity(entity) {
    const winRate = safeDivide(entity.gameplay.wins, entity.gameplay.gamesPlayed);
    const averageFinalVictoryPoints = average(entity.gameplay.finalVictoryPoints);
    const averageRoundsToWin = average(entity.gameplay.roundsToWin);
    const taskSuccessRate = safeDivide(entity.reasoning.successes, entity.reasoning.tasks);
    const averageTaskScore = average(entity.reasoning.scores);
    const averageLatencyPerTurn = average(entity.operations.latencies);
    const illegalMoveRate = safeDivide(entity.operations.illegalAttempts, entity.operations.totalAttempts);

    const finalizedSlices = [...entity.slices.values()].map(slice => {
      const sliceMetrics = this.#finalizeSliceMetrics(slice);
      const normalizedMetrics = Object.fromEntries(
        Object.keys(BENCHMARK_WEIGHTS).map(metric => [
          metric,
          Object.prototype.hasOwnProperty.call(sliceMetrics, metric)
            ? normalizeMetricScore(metric, sliceMetrics[metric])
            : 0,
        ])
      );
      const primaryCompositeScore = computePrimaryComposite(normalizedMetrics);

      return {
        ...slice,
        metrics: sliceMetrics,
        normalizedMetrics,
        primaryCompositeScore,
        sliceScore: primaryCompositeScore,
      };
    });

    const secondaryMetrics = computeSecondaryMetricsFromSlices(finalizedSlices, this.#buildRunSlices(entity));
    const rawMetrics = {
      winRate,
      averageFinalVictoryPoints,
      averageRoundsToWin,
      taskSuccessRate,
      averageTaskScore,
      averageLatencyPerTurn,
      illegalMoveRate,
      robustness: secondaryMetrics.robustness,
      consistency: secondaryMetrics.consistency,
      generalization: secondaryMetrics.generalization,
    };

    const normalizedMetrics = Object.fromEntries(
      Object.keys(BENCHMARK_WEIGHTS).map(metric => [metric, normalizeMetricScore(metric, rawMetrics[metric])])
    );
    const overallScore = computeWeightedMetricScore(normalizedMetrics);

    const fullTaskBreakdown = BENCHMARK_TASKS.map(definition => {
      const task = entity.taskBreakdown.get(definition.id) || {
        taskId: definition.id,
        taskName: definition.name,
        taskCategory: definition.category,
        difficulty: definition.difficulty,
        attempts: 0,
        successes: 0,
        scores: [],
        latencies: [],
        failureReasons: new Map(),
      };

      return {
        taskId: task.taskId,
        taskName: task.taskName,
        taskCategory: task.taskCategory,
        difficulty: task.difficulty,
        description: definition.description,
        scoring: definition.scoring,
        attempts: task.attempts,
        successes: task.successes,
        successRate: safeDivide(task.successes, task.attempts),
        averageLatencyMs: average(task.latencies),
        averageScore: average(task.scores),
        implemented: task.attempts > 0,
        commonFailureReasons: [...task.failureReasons.entries()]
          .sort((left, right) => right[1] - left[1])
          .slice(0, 3)
          .map(([reason, count]) => ({ reason, count })),
      };
    });

    return {
      entityType: entity.entityType,
      entityKey: entity.entityKey,
      displayName: entity.displayName,
      agentId: entity.agentId,
      agentVersion: entity.agentVersion,
      playerKey: entity.playerKey,
      playerName: entity.playerName,
      benchmarkIds: [...entity.benchmarkIds],
      runIds: [...entity.runIds],
      mapLayoutIds: [...entity.mapLayoutIds],
      opponentPolicySets: [...entity.opponentPolicySets],
      scenarioTags: [...entity.scenarioTags],
      rawMetrics,
      normalizedMetrics,
      categoryScores: {
        gameplay: average([
          normalizedMetrics.winRate,
          normalizedMetrics.averageFinalVictoryPoints,
          normalizedMetrics.averageRoundsToWin,
        ]),
        reasoning: normalizedMetrics.taskSuccessRate,
        operations: average([
          normalizedMetrics.averageLatencyPerTurn,
          normalizedMetrics.illegalMoveRate,
        ]),
        secondary: average([
          normalizedMetrics.robustness,
          normalizedMetrics.consistency,
          normalizedMetrics.generalization,
        ]),
      },
      metricDeltas: normalizedMetrics,
      metricStatuses: Object.fromEntries(
        Object.entries(normalizedMetrics).map(([metric, score]) => [metric, metricStatus(metric, score, rawMetrics[metric])])
      ),
      overallScore,
      consistencyVariance: secondaryMetrics.consistencyVariance,
      sliceCount: finalizedSlices.length,
      slices: finalizedSlices,
      taskBreakdown: fullTaskBreakdown,
      games: entity.games.sort((left, right) => String(right.finishedAt || '').localeCompare(String(left.finishedAt || ''))),
      taskResults: entity.taskResults.sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || ''))),
    };
  }

  buildLeaderboards(rawFilters = {}) {
    const filters = defaultFilters(rawFilters);
    const entityMaps = this.#buildEntityMaps(filters);

    const finalizeList = entityType => [...entityMaps[entityType].values()]
      .map(entity => this.#finalizeEntity(entity))
      .sort((left, right) => {
        const rightScore = right.overallScore ?? Number.NEGATIVE_INFINITY;
        const leftScore = left.overallScore ?? Number.NEGATIVE_INFINITY;
        if (rightScore !== leftScore) {
          return rightScore - leftScore;
        }
        const rightWinRate = right.rawMetrics.winRate ?? Number.NEGATIVE_INFINITY;
        const leftWinRate = left.rawMetrics.winRate ?? Number.NEGATIVE_INFINITY;
        if (rightWinRate !== leftWinRate) {
          return rightWinRate - leftWinRate;
        }
        return (right.rawMetrics.taskSuccessRate ?? Number.NEGATIVE_INFINITY) - (left.rawMetrics.taskSuccessRate ?? Number.NEGATIVE_INFINITY);
      })
      .map((entry, index) => ({
        ...entry,
        rank: index + 1,
      }));

    return {
      agent: finalizeList('agent'),
      player: finalizeList('player'),
    };
  }

  async rebuildSnapshots() {
    const leaderboards = this.buildLeaderboards();
    const runs = [...this.runs.values()].sort((left, right) => String(right.updatedAt).localeCompare(String(left.updatedAt)));
    const games = [...this.games.values()];
    const tasks = [...this.tasks.values()];

    this.snapshots = {
      updatedAt: new Date().toISOString(),
      overview: {
        updatedAt: new Date().toISOString(),
        totals: {
          runs: runs.length,
          games: games.length,
          tasks: tasks.length,
          telemetryRecords: [...this.telemetry.values()].reduce((sum, payload) => sum + ensureArray(payload.records || payload).length, 0),
          agents: leaderboards.agent.length,
          players: leaderboards.player.length,
        },
        topAgents: leaderboards.agent.slice(0, 5),
        topPlayers: leaderboards.player.slice(0, 5),
        tasksByCategory: Object.entries(TASK_CATEGORIES).map(([value, label]) => ({
          category: value,
          label,
          taskCount: BENCHMARK_TASKS.filter(task => task.category === value).length,
          completedCount: tasks.filter(task => task.taskCategory === value).length,
        })),
        recentRuns: runs.slice(0, 10),
        filters: this.collectFilters(),
      },
      leaderboards,
    };

    await this.#persistSnapshot();
  }

  getOverview() {
    return {
      ...jsonClone(this.snapshots.overview),
      tasks: BENCHMARK_TASKS,
      metricDirections: METRIC_DIRECTIONS,
      metricWeights: BENCHMARK_WEIGHTS,
    };
  }

  getLeaderboard(entityType = 'agent', filters = {}) {
    const leaderboards = this.buildLeaderboards(filters);
    return {
      entityType,
      filters: defaultFilters(filters),
      entries: leaderboards[entityType] || [],
      metricDirections: METRIC_DIRECTIONS,
      metricWeights: BENCHMARK_WEIGHTS,
    };
  }

  getEntityDetail(entityType, entityKey, filters = {}) {
    const leaderboards = this.buildLeaderboards(filters);
    const entry = (leaderboards[entityType] || []).find(candidate => candidate.entityKey === entityKey);
    if (!entry) return null;

    return {
      ...entry,
      filters: defaultFilters(filters),
      metricDirections: METRIC_DIRECTIONS,
      metricWeights: BENCHMARK_WEIGHTS,
      tasks: BENCHMARK_TASKS,
    };
  }

  getRunDetail(runId) {
    const run = this.runs.get(runId);
    if (!run) return null;

    return {
      run,
      games: [...this.games.values()].filter(game => game.runId === runId),
      tasks: [...this.tasks.values()].filter(task => task.runId === runId),
      telemetry: [...this.telemetry.values()]
        .flatMap(payload => ensureArray(payload.records || payload))
        .filter(record => record.runId === runId),
    };
  }

  getLiveLog({ limit = 100, filters = {} } = {}) {
    const normalizedLimit = Math.max(1, Math.min(500, safeNumber(limit, 100)));
    const taskResults = [...this.tasks.values()].filter(task => matchesFilters(task, filters));
    const unmatchedTaskIds = new Set(taskResults.map(task => task.id));

    const inferActionTaskId = record => {
      if (record.actionName === 'placeSettlement' && record.payload?.isSetup) return 'initial-settlement-location-selection';
      if (record.actionName === 'placeSettlement' && !record.payload?.isSetup) return 'city-vs-settlement-vs-road-prioritization';
      if (record.actionName === 'placeRoad' && record.payload?.isSetup) return 'road-placement-direction';
      if (record.actionName === 'placeRoad' && !record.payload?.isSetup) return 'road-placement-direction';
      if (record.actionName === 'upgradeToCity') return 'city-vs-settlement-vs-road-prioritization';
      if (record.actionName === 'bankTrade') return 'bank-trade-decision';
      if (record.actionName === 'proposeTrade') return 'generate-trade-offers';
      if (record.actionName === 'respondToTrade') return 'accept-or-reject-trade-offers';
      if (record.actionName === 'counterTrade') return 'generate-counter-trade-offer';
      return null;
    };

    const findLinkedTaskResult = record => {
      const taskId = inferActionTaskId(record);
      if (!taskId) return null;
      const actionTimestamp = Date.parse(record.acknowledgedAt || record.requestStartedAt || '') || 0;
      const candidates = taskResults
        .filter(task => unmatchedTaskIds.has(task.id))
        .filter(task => task.runId === record.runId && task.playerKey === record.playerKey && task.taskId === taskId)
        .map(task => ({
          task,
          delta: Math.abs((Date.parse(task.createdAt || '') || 0) - actionTimestamp),
        }))
        .filter(candidate => candidate.delta <= 15000)
        .sort((left, right) => left.delta - right.delta);

      const linked = candidates[0]?.task || null;
      if (linked) {
        unmatchedTaskIds.delete(linked.id);
      }
      return linked;
    };

    const telemetryEntries = [...this.telemetry.values()]
      .flatMap(payload => ensureArray(payload.records || payload))
      .filter(record => matchesFilters(record, filters))
      .map(record => {
        const linkedTask = findLinkedTaskResult(record);
        return {
        id: `action:${record.id}`,
        timestamp: record.acknowledgedAt || record.requestStartedAt || null,
        eventType: 'action',
        gameCode: record.gameId || null,
        runId: record.runId || null,
        benchmarkId: record.benchmarkId || null,
        playerName: record.playerName || null,
        agentId: record.agentId || null,
        turnNumber: record.roundNumber ?? null,
        task: record.taskId || record.taskCategory || record.actionName || null,
        actionName: record.actionName || null,
        benchmarkTaskId: linkedTask?.taskId || null,
        benchmarkTaskName: linkedTask?.taskName || null,
        success: linkedTask ? Boolean(linkedTask.success) : null,
        status: record.accepted ? 'accepted' : 'rejected',
        latencyMs: record.decisionTimeMs ?? null,
        moveTimeMs: record.decisionTimeMs ?? null,
        processingLatencyMs: record.processingLatencyMs ?? record.latencyMs ?? null,
        retryIndex: record.retryIndex ?? 0,
        illegal: record.legal === false,
        details: {
          benchmarkPassed: linkedTask ? Boolean(linkedTask.success) : null,
          benchmarkScore: linkedTask?.score ?? null,
          benchmarkExplanation: linkedTask?.explanation || null,
          benchmarkTaskCategory: linkedTask?.taskCategory || null,
          benchmarkEvaluationDetails: linkedTask?.evaluationDetails || null,
          startingSeat: record.startingSeat ?? null,
          opponentPolicySet: record.opponentPolicySet || null,
        },
      };
      });

    const taskEntries = taskResults
      .filter(task => unmatchedTaskIds.has(task.id))
      .map(task => ({
        id: `task:${task.id}`,
        timestamp: task.createdAt || null,
        eventType: 'task',
        gameCode: task.gameId || task.runId || null,
        runId: task.runId || null,
        benchmarkId: task.benchmarkId || null,
        playerName: task.playerName || null,
        agentId: task.agentId || null,
        turnNumber: task.turnNumber ?? task.roundNumber ?? null,
        task: task.taskName || task.taskId || null,
        actionName: null,
        success: Boolean(task.success),
        status: task.success ? 'passed' : 'failed',
        latencyMs: task.latencyMs ?? null,
        moveTimeMs: task.latencyMs ?? null,
        processingLatencyMs: null,
        retryIndex: task.retries ?? 0,
        illegal: safeNumber(task.illegalAttempts, 0) > 0,
        details: {
          score: task.score ?? null,
          explanation: task.explanation || null,
          taskCategory: task.taskCategory || null,
          evaluationDetails: task.evaluationDetails || null,
        },
      }));

    const completedGameEntries = [...this.games.values()]
      .filter(game => matchesFilters(game, filters))
      .filter(game => Boolean(game.finishedAt))
      .flatMap(game => game.players.map(player => ({
        id: `game:${game.gameId}:${player.playerKey}`,
        timestamp: game.finishedAt,
        eventType: 'game',
        gameCode: game.gameId || null,
        runId: game.runId || null,
        benchmarkId: game.benchmarkId || null,
        playerName: player.playerName || null,
        agentId: player.agentId || null,
        turnNumber: game.roundsPlayed ?? null,
        task: game.taskId || game.gameType || 'full-game',
        actionName: 'game-complete',
        success: Boolean(player.won),
        status: player.won ? 'won' : 'lost',
        latencyMs: null,
        moveTimeMs: null,
        processingLatencyMs: null,
        retryIndex: null,
        illegal: false,
        details: {
          finalVictoryPoints: player.finalVictoryPoints ?? null,
          roundsPlayed: game.roundsPlayed ?? null,
        },
      })));

    const entries = [...telemetryEntries, ...taskEntries, ...completedGameEntries]
      .sort((left, right) => String(right.timestamp || '').localeCompare(String(left.timestamp || '')))
      .slice(0, normalizedLimit);

    return {
      filters: defaultFilters(filters),
      limit: normalizedLimit,
      entries,
    };
  }

  getSliceComparisons(filters = {}) {
    const leaderboards = this.buildLeaderboards(filters);
    const entries = [...leaderboards.agent, ...leaderboards.player];
    const bySlice = new Map();

    entries.forEach(entry => {
      entry.slices.forEach(slice => {
        if (!bySlice.has(slice.sliceKey)) {
          bySlice.set(slice.sliceKey, {
            sliceKey: slice.sliceKey,
            benchmarkId: slice.benchmarkId,
            taskId: slice.taskId,
            taskCategory: slice.taskCategory,
            gameType: slice.gameType,
            mapLayoutId: slice.mapLayoutId,
            startingSeat: slice.startingSeat,
            opponentPolicySet: slice.opponentPolicySet,
            scenarioTags: slice.scenarioTags,
            entries: [],
          });
        }
        bySlice.get(slice.sliceKey).entries.push({
          entityType: entry.entityType,
          entityKey: entry.entityKey,
          displayName: entry.displayName,
          overallScore: entry.overallScore,
          sliceScore: slice.sliceScore,
          normalizedMetrics: slice.normalizedMetrics,
          metricDeltas: slice.normalizedMetrics,
          metrics: slice.metrics,
        });
      });
    });

    return [...bySlice.values()].sort((left, right) => left.sliceKey.localeCompare(right.sliceKey));
  }
}

export const benchmarkStore = new BenchmarkStore();
