import fs from 'fs';
import { promises as fsp } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { v4 as uuidv4 } from 'uuid';
import {
  BENCHMARK_TASKS,
  BENCHMARK_WEIGHTS,
  METRIC_DIRECTIONS,
  SECONDARY_SLICE_TAGS,
  TASK_CATEGORIES,
  getTaskDefinition,
} from './benchmarkDefinitions.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DEFAULT_DATA_DIR = process.env.BENCHMARK_DATA_DIR || path.join(__dirname, 'benchmark-data');

const STORE_FILES = {
  runs: 'runs',
  games: 'games',
  tasks: 'tasks',
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

function normalizeDelta(metric, value, baselineValue) {
  if (value === null || baselineValue === null || value === undefined || baselineValue === undefined) {
    return null;
  }
  const denominator = Math.max(Math.abs(baselineValue), 1);
  return METRIC_DIRECTIONS[metric] === 'lower'
    ? (baselineValue - value) / denominator
    : (value - baselineValue) / denominator;
}

function sortScenarioTags(tags = []) {
  return [...new Set((tags || []).filter(Boolean))].sort();
}

function makeEntityAgentKey(agentId, agentVersion) {
  return `${agentId || 'unknown'}@${agentVersion || 'unversioned'}`;
}

function metricStatus(metric, delta) {
  if (delta === null || delta === undefined) return 'no-baseline';
  if (delta > 0) {
    return METRIC_DIRECTIONS[metric] === 'lower'
      ? 'better-than-baseline-lower-is-better'
      : 'above-baseline';
  }
  if (delta < 0) {
    return METRIC_DIRECTIONS[metric] === 'lower'
      ? 'worse-than-baseline-lower-is-better'
      : 'below-baseline';
  }
  return 'at-baseline';
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
    const latencyMs = clientTimestamp
      ? Math.max(0, acknowledgedAt - clientTimestamp)
      : Math.max(0, acknowledgedAt - requestStartedAt);

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
      latencyMs,
      requestStartedAt: new Date(requestStartedAt).toISOString(),
      acknowledgedAt: new Date(acknowledgedAt).toISOString(),
      payload: payload || {},
      result: result || {},
    };

    const records = this.telemetry.get(game.id) || [];
    records.push(telemetryRecord);
    this.telemetry.set(game.id, records);
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
    const taskResult = {
      id,
      runId: run.runId,
      benchmarkId: run.benchmarkId,
      baselineAgentId: payload.baselineAgentId || run.baselineAgentId,
      taskId: payload.taskId,
      taskName: definition?.name || payload.taskName || payload.taskId,
      taskCategory: definition?.category || payload.taskCategory || null,
      difficulty: definition?.difficulty || payload.difficulty || 'unknown',
      success: Boolean(payload.success),
      score: payload.score ?? (payload.success ? 1 : 0),
      latencyMs: payload.latencyMs ?? null,
      retries: payload.retries ?? 0,
      illegalAttempts: payload.illegalAttempts ?? 0,
      explanation: payload.explanation || null,
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
        totalRetries: 0,
        turnsWithAttempts: new Set(),
      },
      taskBreakdown: new Map(),
      games: [],
      taskResults: [],
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
        latencies: [],
        illegalAttempts: 0,
        totalAttempts: 0,
        retryCount: 0,
        turnIds: new Set(),
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
      latencies: [],
    };
    taskBreakdown.attempts += 1;
    taskBreakdown.successes += taskResult.success ? 1 : 0;
    if (taskResult.latencyMs !== null) {
      taskBreakdown.latencies.push(taskResult.latencyMs);
    }
    entity.taskBreakdown.set(taskResult.taskId, taskBreakdown);

    const slice = this.#getSliceAccumulator(entity, taskResult);
    slice.tasks += 1;
    slice.successes += taskResult.success ? 1 : 0;
    if (taskResult.latencyMs !== null) {
      slice.latencies.push(taskResult.latencyMs);
    }
    slice.illegalAttempts += safeNumber(taskResult.illegalAttempts, 0);
    slice.totalAttempts += 1 + safeNumber(taskResult.illegalAttempts, 0) + safeNumber(taskResult.retries, 0);
    slice.retryCount += safeNumber(taskResult.retries, 0);
  }

  #accumulateTelemetry(entity, telemetryRecord) {
    entity.benchmarkIds.add(telemetryRecord.benchmarkId);
    entity.runIds.add(telemetryRecord.runId);
    entity.mapLayoutIds.add(telemetryRecord.mapLayoutId);
    entity.opponentPolicySets.add(telemetryRecord.opponentPolicySet);
    telemetryRecord.scenarioTags.forEach(tag => entity.scenarioTags.add(tag));

    entity.operations.totalAttempts += 1;
    entity.operations.turnsWithAttempts.add(telemetryRecord.turnId);
    if (!telemetryRecord.legal) {
      entity.operations.illegalAttempts += 1;
    }
    if (telemetryRecord.retryIndex > 0) {
      entity.operations.totalRetries += 1;
    }
    if (telemetryRecord.latencyMs !== null) {
      entity.operations.latencies.push(telemetryRecord.latencyMs);
    }

    const slice = this.#getSliceAccumulator(entity, telemetryRecord);
    slice.totalAttempts += 1;
    slice.turnIds.add(telemetryRecord.turnId);
    if (!telemetryRecord.legal) {
      slice.illegalAttempts += 1;
    }
    if (telemetryRecord.retryIndex > 0) {
      slice.retryCount += 1;
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
      averageLatencyPerTurn: average(slice.latencies),
      illegalMoveRate: safeDivide(slice.illegalAttempts, slice.totalAttempts),
      retryRate: safeDivide(slice.retryCount, slice.turnIds.size),
    };
  }

  #baselineBySlice(entityMaps) {
    const baselineMaps = {
      agent: new Map(),
      player: new Map(),
    };

    [...this.games.values()].forEach(gameRecord => {
      gameRecord.players.forEach(playerRecord => {
        if (!playerRecord.baseline) return;
        const agentKey = makeEntityAgentKey(playerRecord.agentId, playerRecord.agentVersion);
        const sliceKey = buildSliceKey({
          ...gameRecord,
          startingSeat: playerRecord.startingSeat,
        });
        const agentEntity = entityMaps.agent.get(agentKey);
        const playerEntity = entityMaps.player.get(playerRecord.playerKey);
        if (agentEntity?.slices.has(sliceKey)) {
          baselineMaps.agent.set(sliceKey, {
            entityKey: agentKey,
            metrics: this.#finalizeSliceMetrics(agentEntity.slices.get(sliceKey)),
          });
        }
        if (playerEntity?.slices.has(sliceKey)) {
          baselineMaps.player.set(sliceKey, {
            entityKey: playerRecord.playerKey,
            metrics: this.#finalizeSliceMetrics(playerEntity.slices.get(sliceKey)),
          });
        }
      });
    });

    [...this.tasks.values()].forEach(taskResult => {
      if (taskResult.baselineAgentId !== taskResult.agentId) return;
      const agentKey = makeEntityAgentKey(taskResult.agentId, taskResult.agentVersion);
      const sliceKey = buildSliceKey(taskResult);
      const agentEntity = entityMaps.agent.get(agentKey);
      const playerEntity = entityMaps.player.get(taskResult.playerKey);
      if (agentEntity?.slices.has(sliceKey)) {
        baselineMaps.agent.set(sliceKey, {
          entityKey: agentKey,
          metrics: this.#finalizeSliceMetrics(agentEntity.slices.get(sliceKey)),
        });
      }
      if (playerEntity?.slices.has(sliceKey)) {
        baselineMaps.player.set(sliceKey, {
          entityKey: taskResult.playerKey,
          metrics: this.#finalizeSliceMetrics(playerEntity.slices.get(sliceKey)),
        });
      }
    });

    return baselineMaps;
  }

  #finalizeEntity(entity, baselineBySlice = new Map()) {
    const winRate = safeDivide(entity.gameplay.wins, entity.gameplay.gamesPlayed);
    const averageFinalVictoryPoints = average(entity.gameplay.finalVictoryPoints);
    const averageRoundsToWin = average(entity.gameplay.roundsToWin);
    const taskSuccessRate = safeDivide(entity.reasoning.successes, entity.reasoning.tasks);
    const averageLatencyPerTurn = average(entity.operations.latencies);
    const illegalMoveRate = safeDivide(entity.operations.illegalAttempts, entity.operations.totalAttempts);
    const retryRate = safeDivide(entity.operations.totalRetries, entity.operations.turnsWithAttempts.size);

    const sliceScores = [];
    const metricDeltasByMetric = {};
    const finalizedSlices = [...entity.slices.values()].map(slice => {
      const sliceMetrics = this.#finalizeSliceMetrics(slice);
      const baseline = baselineBySlice.get(slice.sliceKey) || null;
      const metricDeltas = Object.fromEntries(
        Object.keys(BENCHMARK_WEIGHTS).map(metric => {
          if (!Object.prototype.hasOwnProperty.call(sliceMetrics, metric)) {
            return [metric, null];
          }
          const baselineMetric = baseline?.metrics?.[metric] ?? null;
          return [metric, normalizeDelta(metric, sliceMetrics[metric], baselineMetric)];
        })
      );

      Object.entries(metricDeltas).forEach(([metric, delta]) => {
        if (delta === null) return;
        if (!metricDeltasByMetric[metric]) {
          metricDeltasByMetric[metric] = [];
        }
        metricDeltasByMetric[metric].push(delta);
      });

      const validDeltas = Object.entries(metricDeltas)
        .filter(([metric, delta]) => delta !== null && BENCHMARK_WEIGHTS[metric])
        .map(([metric, delta]) => delta * BENCHMARK_WEIGHTS[metric]);
      const sliceScore = validDeltas.length ? validDeltas.reduce((sum, value) => sum + value, 0) : null;
      if (sliceScore !== null) {
        sliceScores.push(sliceScore);
      }

      return {
        ...slice,
        metrics: sliceMetrics,
        metricDeltas,
        sliceScore,
      };
    });

    const robustnessSliceScores = finalizedSlices
      .filter(slice => slice.scenarioTags.some(tag => SECONDARY_SLICE_TAGS.robustness.includes(tag)))
      .map(slice => slice.sliceScore)
      .filter(score => score !== null);
    const generalizationSliceScores = finalizedSlices
      .filter(slice => {
        if (slice.scenarioTags.some(tag => SECONDARY_SLICE_TAGS.generalization.includes(tag))) return true;
        return Boolean(slice.mapLayoutId || slice.startingSeat !== null || slice.opponentPolicySet);
      })
      .map(slice => slice.sliceScore)
      .filter(score => score !== null);

    const rawMetrics = {
      winRate,
      averageFinalVictoryPoints,
      averageRoundsToWin,
      taskSuccessRate,
      averageLatencyPerTurn,
      illegalMoveRate,
      retryRate,
      robustness: average(robustnessSliceScores.length ? robustnessSliceScores : sliceScores),
      consistency: sliceScores.length ? 1 / (1 + variance(sliceScores)) : null,
      generalization: average(generalizationSliceScores.length ? generalizationSliceScores : sliceScores),
    };

    const aggregatedDeltas = Object.fromEntries(
      Object.keys(BENCHMARK_WEIGHTS).map(metric => [metric, average(metricDeltasByMetric[metric] || [])])
    );
    aggregatedDeltas.robustness = rawMetrics.robustness;
    aggregatedDeltas.generalization = rawMetrics.generalization;
    aggregatedDeltas.consistency = rawMetrics.consistency === null ? null : rawMetrics.consistency - 0.5;

    const overallScore = Object.entries(BENCHMARK_WEIGHTS)
      .map(([metric, weight]) => {
        const delta = aggregatedDeltas[metric];
        return delta === null || delta === undefined ? null : delta * weight;
      })
      .filter(value => value !== null)
      .reduce((sum, value) => sum + value, 0);

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
      categoryScores: {
        gameplay: average([
          aggregatedDeltas.winRate,
          aggregatedDeltas.averageFinalVictoryPoints,
          aggregatedDeltas.averageRoundsToWin,
        ].filter(value => value !== null)),
        reasoning: aggregatedDeltas.taskSuccessRate,
        operations: average([
          aggregatedDeltas.averageLatencyPerTurn,
          aggregatedDeltas.illegalMoveRate,
          aggregatedDeltas.retryRate,
        ].filter(value => value !== null)),
        secondary: average([
          rawMetrics.robustness,
          rawMetrics.consistency,
          rawMetrics.generalization,
        ].filter(value => value !== null)),
      },
      metricDeltas: aggregatedDeltas,
      metricStatuses: Object.fromEntries(
        Object.entries(aggregatedDeltas).map(([metric, delta]) => [metric, metricStatus(metric, delta)])
      ),
      overallScore,
      consistencyVariance: variance(sliceScores),
      sliceCount: finalizedSlices.length,
      slices: finalizedSlices,
      taskBreakdown: [...entity.taskBreakdown.values()].map(task => ({
        ...task,
        successRate: safeDivide(task.successes, task.attempts),
        averageLatencyMs: average(task.latencies),
      })),
      games: entity.games.sort((left, right) => String(right.finishedAt || '').localeCompare(String(left.finishedAt || ''))),
      taskResults: entity.taskResults.sort((left, right) => String(right.createdAt || '').localeCompare(String(left.createdAt || ''))),
    };
  }

  buildLeaderboards(rawFilters = {}) {
    const filters = defaultFilters(rawFilters);
    const entityMaps = this.#buildEntityMaps(filters);
    const baselineMaps = this.#baselineBySlice(entityMaps);

    const finalizeList = entityType => [...entityMaps[entityType].values()]
      .map(entity => this.#finalizeEntity(entity, baselineMaps[entityType]))
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
          metricDeltas: slice.metricDeltas,
          metrics: slice.metrics,
        });
      });
    });

    return [...bySlice.values()].sort((left, right) => left.sliceKey.localeCompare(right.sliceKey));
  }
}

export const benchmarkStore = new BenchmarkStore();
