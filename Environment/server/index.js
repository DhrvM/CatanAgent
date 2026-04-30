/**
 * ============================================================================
 * CATAN GAME SERVER
 * ============================================================================
 * 
 * Express + Socket.io server for multiplayer Catan game.
 * Handles real-time game communication, session management, and game state.
 * 
 * Features:
 * - WebSocket-based real-time multiplayer
 * - Game room management with 6-character codes
 * - Automatic cleanup of stale games
 * - Connection limits for free tier hosting
 * - Keep-alive ping endpoint for Render free tier
 * 
 * @author Viral Doshi
 */

import express from 'express';
import { createServer } from 'http';
import { Server } from 'socket.io';
import cors from 'cors';
import { v4 as uuidv4 } from 'uuid';
import * as GameLogic from './gameLogic.js';
import { benchmarkStore } from './benchmarkStore.js';
import { computeGoalAwareResourceMetrics, PLAN_COSTS, RESOURCE_TYPES } from './resourceHeuristics.js';
import {
  BENCHMARK_TASKS,
  BENCHMARK_WEIGHTS,
  METRIC_DIRECTIONS,
  TASK_CATEGORIES,
} from './benchmarkDefinitions.js';

await benchmarkStore.init();

// ============================================================================
// EXPRESS APP SETUP
// ============================================================================

const app = express();
app.use(cors());
app.use(express.json());

// ============================================================================
// HTTP ENDPOINTS
// ============================================================================

/** Server info and health check */
app.get('/', (req, res) => {
  res.json({
    name: 'Catan Game Server',
    status: 'running',
    version: '1.0.0',
    endpoints: {
      health: '/health',
      socket: 'ws://[this-url]'
    },
    author: 'Viral Doshi'
  });
});

/** Detailed health check with server stats */
app.get('/health', (req, res) => {
  res.json({ 
    status: 'ok', 
    uptime: process.uptime(),
    activeGames: games.size,
    maxGames: MAX_CONCURRENT_GAMES,
    connectedPlayers: totalConnectedPlayers,
    maxPlayers: MAX_TOTAL_PLAYERS,
    timestamp: new Date().toISOString()
  });
});

/** Status endpoint for client to check if server has capacity */
app.get('/status', (req, res) => {
  const isAtCapacity = totalConnectedPlayers >= MAX_TOTAL_PLAYERS || games.size >= MAX_CONCURRENT_GAMES;
  res.json({
    available: !isAtCapacity,
    players: totalConnectedPlayers,
    maxPlayers: MAX_TOTAL_PLAYERS,
    games: games.size,
    maxGames: MAX_CONCURRENT_GAMES
  });
});

/** Lightweight keep-alive ping (prevents Render free tier from sleeping) */
app.get('/ping', (req, res) => {
  res.send('pong');
});

function parseBenchmarkFilters(query = {}) {
  return {
    benchmarkId: query.benchmarkId || null,
    taskCategory: query.taskCategory || null,
    baselineAgentId: query.baselineAgentId || null,
    mapLayoutId: query.mapLayoutId || null,
    startingSeat: query.startingSeat || null,
    opponentPolicySet: query.opponentPolicySet || null,
    runId: query.runId || null,
    search: query.search || null,
  };
}

function parseLiveLogLimit(query = {}) {
  const value = Number(query.limit);
  if (!Number.isFinite(value)) return 100;
  return Math.max(1, Math.min(500, value));
}

app.get('/api/benchmark/definitions', (req, res) => {
  res.json({
    tasks: BENCHMARK_TASKS,
    taskCategories: TASK_CATEGORIES,
    metricDirections: METRIC_DIRECTIONS,
    metricWeights: BENCHMARK_WEIGHTS,
  });
});

app.get('/api/benchmark/overview', (req, res) => {
  res.json(benchmarkStore.getOverview());
});

app.get('/api/benchmark/leaderboard', (req, res) => {
  const entityType = req.query.entityType === 'player' ? 'player' : 'agent';
  res.json(benchmarkStore.getLeaderboard(entityType, parseBenchmarkFilters(req.query)));
});

app.get('/api/benchmark/agents/:entityKey', (req, res) => {
  const detail = benchmarkStore.getEntityDetail('agent', req.params.entityKey, parseBenchmarkFilters(req.query));
  if (!detail) {
    return res.status(404).json({ error: 'Agent not found' });
  }
  res.json(detail);
});

app.get('/api/benchmark/players/:entityKey', (req, res) => {
  const detail = benchmarkStore.getEntityDetail('player', req.params.entityKey, parseBenchmarkFilters(req.query));
  if (!detail) {
    return res.status(404).json({ error: 'Player not found' });
  }
  res.json(detail);
});

app.get('/api/benchmark/runs/:runId', (req, res) => {
  const detail = benchmarkStore.getRunDetail(req.params.runId);
  if (!detail) {
    return res.status(404).json({ error: 'Run not found' });
  }
  res.json(detail);
});

app.get('/api/benchmark/slices', (req, res) => {
  res.json({
    filters: parseBenchmarkFilters(req.query),
    slices: benchmarkStore.getSliceComparisons(parseBenchmarkFilters(req.query)),
  });
});

app.get('/api/benchmark/live-log', (req, res) => {
  res.json(benchmarkStore.getLiveLog({
    limit: parseLiveLogLimit(req.query),
    filters: parseBenchmarkFilters(req.query),
  }));
});

app.post('/api/benchmark/runs', async (req, res) => {
  try {
    const run = await benchmarkStore.upsertRun(req.body || {});
    res.status(201).json(run);
  } catch (error) {
    console.error('Failed to store benchmark run', error);
    res.status(500).json({ error: error.message || 'Failed to store benchmark run' });
  }
});

app.post('/api/benchmark/tasks', async (req, res) => {
  try {
    const taskResult = await benchmarkStore.recordTaskResult(req.body || {});
    res.status(201).json(taskResult);
  } catch (error) {
    console.error('Failed to store task result', error);
    res.status(500).json({ error: error.message || 'Failed to store task result' });
  }
});

// ============================================================================
// SOCKET.IO SETUP
// ============================================================================

const httpServer = createServer(app);
const io = new Server(httpServer, {
  cors: {
    origin: "*",
    methods: ["GET", "POST"]
  }
});

// ============================================================================
// IN-MEMORY STATE
// ============================================================================

/** Map of gameCode -> game state object */
const games = new Map();

/** Map of socketId -> { gameId, playerId } for tracking player connections */
const playerSockets = new Map();

// Connection limits for free tier hosting (Render, Heroku, etc.)
const MAX_CONCURRENT_GAMES = 10;
const MAX_TOTAL_PLAYERS = 200;
let totalConnectedPlayers = 0;

// ============================================================================
// AUTOMATIC CLEANUP
// ============================================================================

/** Clean up games that have been idle for 3+ hours */
setInterval(() => {
  const threeHoursAgo = Date.now() - (3 * 60 * 60 * 1000);
  let cleanedCount = 0;
  games.forEach((game, id) => {
    if (game.createdAt && game.createdAt < threeHoursAgo) {
      games.delete(id);
      cleanedCount++;
    }
  });
  if (cleanedCount > 0) {
    console.log(`Cleaned up ${cleanedCount} stale games. Active games: ${games.size}`);
  }
}, 30 * 60 * 1000); // Check every 30 minutes

// ============================================================================
// HELPER FUNCTIONS
// ============================================================================

/** Generate a 6-character alphanumeric game code (excludes ambiguous chars like 0/O, 1/I) */
function generateGameCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 6; i++) {
    code += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return code;
}

/** Send player-specific game state to each player in a game */
function broadcastGameState(gameId) {
  const game = games.get(gameId);
  if (!game) return;
  
  game.players.forEach(player => {
    const socketId = [...playerSockets.entries()]
      .find(([_, v]) => v.gameId === gameId && v.playerId === player.id)?.[0];
    
    if (socketId) {
      const playerView = GameLogic.getPlayerView(game, player.id);
      io.to(socketId).emit('gameState', playerView);
    }
  });
}

/** Broadcast an event to all players in a game (same data to everyone) */
function broadcastToGame(gameId, event, data) {
  const game = games.get(gameId);
  if (!game) return;
  
  game.players.forEach(player => {
    const socketId = [...playerSockets.entries()]
      .find(([_, v]) => v.gameId === gameId && v.playerId === player.id)?.[0];
    
    if (socketId) {
      io.to(socketId).emit(event, data);
    }
  });
}

function mergeBenchmarkPlayer(basePlayer, benchmarkPlayer = {}, fallbackSeat = 0) {
  const agentId = benchmarkPlayer.agentId || basePlayer.name;
  return {
    ...basePlayer,
    benchmarkPlayerKey: benchmarkPlayer.playerKey || `${benchmarkPlayer.runId || 'run'}:${basePlayer.id}`,
    benchmarkAgentId: agentId,
    benchmarkAgentVersion: benchmarkPlayer.agentVersion || 'unknown',
    benchmarkBaseline: Boolean(benchmarkPlayer.baseline),
    benchmarkStartingSeat: benchmarkPlayer.startingSeat ?? fallbackSeat,
  };
}

function extractClientTimestamp(payload) {
  if (!payload || typeof payload !== 'object') return null;
  const candidate = payload.clientTimestamp || payload.benchmarkTimestamp || payload._clientTimestamp;
  return Number.isFinite(Number(candidate)) ? Number(candidate) : null;
}

const DICE_PROBABILITY_WEIGHTS = {
  2: 1,
  3: 2,
  4: 3,
  5: 4,
  6: 5,
  8: 5,
  9: 4,
  10: 3,
  11: 2,
  12: 1,
};

function clamp01(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(1, numeric));
}

function normalizeVertexKey(vKey) {
  const match = String(vKey || '').match(/^v_(-?\d+)_(-?\d+)_(\d+)$/);
  if (!match) return null;
  return {
    q: Number(match[1]),
    r: Number(match[2]),
    dir: Number(match[3]),
  };
}

function getEdgeVerticesFromKey(edgeKeyValue) {
  const match = String(edgeKeyValue || '').match(/^e_(-?\d+)_(-?\d+)_(\d+)$/);
  if (!match) return [];
  const q = Number(match[1]);
  const r = Number(match[2]);
  const dir = Number(match[3]);
  if (dir === 0) return [GameLogic.vertexKey(q, r, 0), GameLogic.vertexKey(q, r, 1)];
  if (dir === 1) return [GameLogic.vertexKey(q, r, 1), GameLogic.vertexKey(q, r, 2)];
  if (dir === 2) return [GameLogic.vertexKey(q, r, 2), GameLogic.vertexKey(q, r, 3)];
  if (dir === 3) return [GameLogic.vertexKey(q, r, 3), GameLogic.vertexKey(q, r, 4)];
  if (dir === 4) return [GameLogic.vertexKey(q, r, 4), GameLogic.vertexKey(q, r, 5)];
  if (dir === 5) return [GameLogic.vertexKey(q, r, 5), GameLogic.vertexKey(q, r, 0)];
  return [];
}

function scoreVertexPlacement(game, vKey) {
  const adjacentHexes = GameLogic.getVertexAdjacentHexes(game, vKey);
  const productionWeight = adjacentHexes.reduce((sum, hex) => sum + (DICE_PROBABILITY_WEIGHTS[hex.number] || 0), 0);
  const resourceTypes = [...new Set(adjacentHexes.map(hex => hex.resource).filter(Boolean))];
  const port = (game.ports || []).find(candidate => candidate.vertices.some(portVertex => GameLogic.areVerticesEqual(portVertex, vKey))) || null;
  const edgeOptions = GameLogic.getVertexEdges(vKey)
    .filter(edgeKeyValue => GameLogic.canPlaceRoad(game, game.currentPlayer, edgeKeyValue, true, vKey).valid);

  return {
    resourceProductionExpectancy: Math.min(1, productionWeight / 15),
    resourceDiversity: Math.min(1, resourceTypes.length / 3),
    expansionAccess: Math.min(1, edgeOptions.length / 3),
    portSynergy: port ? (port.resource ? 1 : 0.7) : 0,
  };
}

function settlementPlacementScore(score = {}) {
  return clamp01(
    (Number(score.resourceProductionExpectancy) || 0) * 0.4
    + (Number(score.resourceDiversity) || 0) * 0.25
    + (Number(score.expansionAccess) || 0) * 0.2
    + (Number(score.portSynergy) || 0) * 0.15
  );
}

function roadPlacementPriorityScore(score = {}) {
  return clamp01(
    (Number(score.futureReachability) || 0) * 0.5
    + (Number(score.reachableIntersectionValue) || 0) * 0.35
    + (Number(score.blockingValue) || 0) * 0.15
  );
}

function getRoadOwnerAtEdge(game, edgeKeyValue) {
  const parsed = parseEdgeKey(edgeKeyValue);
  if (!parsed) return null;
  const equivalents = GameLogic.getEquivalentEdges(parsed.q, parsed.r, parsed.dir);
  for (const equivalent of equivalents) {
    const equivalentKey = GameLogic.edgeKey(equivalent.q, equivalent.r, equivalent.dir);
    const edge = game.edges?.[equivalentKey];
    if (edge?.road) {
      return edge.owner;
    }
  }
  return null;
}

function areEdgeKeysEquivalent(leftEdgeKey, rightEdgeKey) {
  const left = parseEdgeKey(leftEdgeKey);
  const right = parseEdgeKey(rightEdgeKey);
  if (!left || !right) return false;
  return GameLogic.getEquivalentEdges(left.q, left.r, left.dir)
    .some(candidate => candidate.q === right.q && candidate.r === right.r && candidate.dir === right.dir);
}

function getBuildingAtVertex(game, vKey) {
  for (const [vertexKey, vertex] of Object.entries(game.vertices || {})) {
    if (!vertex?.building) continue;
    if (GameLogic.areVerticesEqual(vertexKey, vKey)) {
      return {
        key: vertexKey,
        ...vertex,
      };
    }
  }
  return null;
}

function isOpenSettlementVertex(game, vKey) {
  if (getBuildingAtVertex(game, vKey)) return false;
  const adjacentVertices = GameLogic.getAdjacentVertices(vKey, game.hexes);
  return !adjacentVertices.some(adjacentVertex => getBuildingAtVertex(game, adjacentVertex));
}

function scoreRoadBlockingValue(game, playerId, edgeKeyValue) {
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  if (playerIndex === -1) return 0;

  const endpoints = getEdgeVerticesFromKey(edgeKeyValue);
  const opponents = game.players
    .map((player, index) => ({ player, index }))
    .filter(({ index }) => index !== playerIndex);

  let bestBlockingScore = 0;

  endpoints.forEach(vKey => {
    const building = getBuildingAtVertex(game, vKey);
    if (building?.owner === playerIndex) return;

    const vertexEdges = GameLogic.getVertexEdges(vKey);
    const remainingOpenBranches = vertexEdges.filter(candidateEdge => (
      !areEdgeKeysEquivalent(candidateEdge, edgeKeyValue) && getRoadOwnerAtEdge(game, candidateEdge) === null
    )).length;
    const branchScarcity = clamp01((2 - remainingOpenBranches) / 2);
    const openSettlementValue = isOpenSettlementVertex(game, vKey) ? 1 : 0.35;

    opponents.forEach(({ player, index }) => {
      if (building && building.owner !== index) return;

      const opponentAdjacentRoads = vertexEdges.filter(candidateEdge => getRoadOwnerAtEdge(game, candidateEdge) === index).length;
      const opponentPresence = opponentAdjacentRoads + (building?.owner === index ? 1 : 0);
      if (opponentPresence <= 0) return;

      const vertexOpportunity = building?.owner === index ? 0.8 : openSettlementValue;
      const roadPressure = clamp01(((player.roadLength || 0) + (player.hasLongestRoad ? 2 : 0)) / 8);
      const presenceStrength = clamp01(opponentPresence / 2);

      const blockingScore = clamp01(
        (branchScarcity * 0.5)
        + (vertexOpportunity * 0.3)
        + (presenceStrength * 0.1)
        + (roadPressure * 0.1)
      );

      bestBlockingScore = Math.max(bestBlockingScore, blockingScore);
    });
  });

  return bestBlockingScore;
}

function bestSettlementPlanScore(game, playerId) {
  const settlementOptions = Object.keys(game.vertices || {})
    .filter(vKey => GameLogic.canPlaceSettlement(game, playerId, vKey, false).valid)
    .map(vKey => settlementPlacementScore(scoreVertexPlacement(game, vKey)));
  return settlementOptions.length ? Math.max(...settlementOptions) : 0;
}

function bestRoadPlanScore(game, playerId) {
  const roadOptions = Object.keys(game.edges || {})
    .filter(eKey => GameLogic.canPlaceRoad(game, playerId, eKey, false, null).valid)
    .map(eKey => roadPlacementPriorityScore(scoreRoadPlacement(game, eKey, null)));
  return roadOptions.length ? Math.max(...roadOptions) : 0;
}

function cityUpgradeScore(game, vKey) {
  const baseScore = scoreVertexPlacement(game, vKey);
  return clamp01(
    (baseScore.resourceProductionExpectancy * 0.65)
    + (baseScore.resourceDiversity * 0.1)
    + (baseScore.portSynergy * 0.05)
    + 0.2
  );
}

function buildSettlementEvaluationContext(game, playerId) {
  const legalOptions = Object.keys(game.vertices || {})
    .filter(vKey => GameLogic.canPlaceSettlement(game, playerId, vKey, true).valid)
    .map(vKey => ({
      id: vKey,
      ...scoreVertexPlacement(game, vKey),
    }));

  return {
    legalOptions,
  };
}

function bestCityPlanScore(game, playerId) {
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  if (playerIndex === -1) return 0;

  const player = game.players[playerIndex];
  if (!player?.cities) return 0;

  const cityOptions = Object.entries(game.vertices || {})
    .filter(([, vertex]) => vertex?.building === 'settlement' && vertex.owner === playerIndex)
    .map(([vKey]) => cityUpgradeScore(game, vKey));

  return cityOptions.length ? Math.max(...cityOptions) : 0;
}

function countMissingResources(resources = {}, cost = {}) {
  return RESOURCE_TYPES.reduce((sum, resource) => (
    sum + Math.max(0, (Number(cost[resource]) || 0) - (Number(resources[resource]) || 0))
  ), 0);
}

function normalizeResourceSnapshot(resources = {}) {
  return Object.fromEntries(RESOURCE_TYPES.map(resource => [resource, Math.max(0, Number(resources[resource]) || 0)]));
}

function buildPlanDecisionData(game, playerId, resourcesOverride = null) {
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return [];

  const resources = normalizeResourceSnapshot(resourcesOverride || player.resources || {});
  const devDeckAvailable = Array.isArray(game.devDeck) ? game.devDeck.length > 0 : true;
  const roadPlanScore = bestRoadPlanScore(game, playerId);
  const settlementPlanScore = bestSettlementPlanScore(game, playerId);
  const cityPlanScore = bestCityPlanScore(game, playerId);
  const devCardPlanScore = devDeckAvailable
    ? Math.max(0.2, 0.55 * (0.5 + (cityPlanScore * 0.5)))
    : 0;

  return [
    { id: 'road', type: 'road', cost: PLAN_COSTS.road, baseScore: roadPlanScore },
    { id: 'settlement', type: 'settlement', cost: PLAN_COSTS.settlement, baseScore: settlementPlanScore },
    { id: 'city', type: 'city', cost: PLAN_COSTS.city, baseScore: cityPlanScore },
    { id: 'devCard', type: 'devCard', cost: PLAN_COSTS.devCard, baseScore: devCardPlanScore },
  ].map(plan => {
    const missingCount = countMissingResources(resources, plan.cost);
    return {
      ...plan,
      resources,
      missingCount,
      affordable: missingCount === 0,
      effectiveScore: plan.baseScore / (1 + (0.75 * missingCount)),
    };
  });
}

function scoreSaveOption(planDecisionData = [], resourceTotal = 0) {
  const bestFutureScore = Math.max(0, ...planDecisionData
    .filter(plan => plan.missingCount > 0)
    .map(plan => plan.effectiveScore));
  return clamp01(Math.max(0.35, bestFutureScore + (resourceTotal > 7 ? 0.1 : 0)));
}

function buildBuildVsSaveTaskPayload(game, playerId, selectedOptionId) {
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return null;

  const planDecisionData = buildPlanDecisionData(game, playerId);
  const options = planDecisionData
    .filter(plan => plan.affordable && plan.baseScore > 0)
    .map(plan => ({
      id: plan.id,
      score: clamp01(plan.baseScore),
    }));

  options.push({
    id: 'save',
    score: scoreSaveOption(planDecisionData, sumResources(player.resources)),
  });

  if (!options.some(option => option.id === selectedOptionId)) return null;

  return {
    taskId: 'build-vs-save-decision',
    selectedOptionId,
    evaluationContext: {
      options,
    },
  };
}

function buildDevelopmentCardPurchaseTaskPayload(game, playerId, selectedOptionId) {
  const planDecisionData = buildPlanDecisionData(game, playerId);
  const devCardPlan = planDecisionData.find(plan => plan.id === 'devCard');
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player || !devCardPlan) return null;

  if (!devCardPlan.affordable && selectedOptionId === 'buy-dev-card') return null;
  if (!devCardPlan.affordable && selectedOptionId === 'skip-dev-card') return null;

  const alternativeScore = Math.max(
    scoreSaveOption(planDecisionData, sumResources(player.resources)),
    ...planDecisionData
      .filter(plan => plan.id !== 'devCard' && plan.affordable)
      .map(plan => plan.baseScore)
  );

  return {
    taskId: 'development-card-purchase-decision',
    selectedOptionId,
    evaluationContext: {
      options: [
        { id: 'buy-dev-card', score: clamp01(devCardPlan.baseScore) },
        { id: 'skip-dev-card', score: clamp01(alternativeScore) },
      ],
    },
  };
}

function buildGoalAwarePlanCandidates(game, playerId) {
  return buildPlanDecisionData(game, playerId).map(plan => ({
    type: plan.type,
    cost: plan.cost,
    basePlanWeight: plan.baseScore,
  })).filter(plan => plan.basePlanWeight > 0);
}

function buildBuildPrioritizationTaskPayload(game, playerId, selectedOptionId) {
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  if (playerIndex === -1) return null;
  const player = game.players[playerIndex];

  const settlementOptions = Object.keys(game.vertices || {})
    .filter(vKey => GameLogic.canPlaceSettlement(game, playerId, vKey, false).valid)
    .map(vKey => settlementPlacementScore(scoreVertexPlacement(game, vKey)));

  const cityOptions = player?.cities > 0 && hasRequiredResources(player, { ore: 3, grain: 2 })
    ? Object.entries(game.vertices || {})
      .filter(([, vertex]) => vertex?.building === 'settlement' && vertex.owner === playerIndex)
      .map(([vKey]) => cityUpgradeScore(game, vKey))
    : [];

  const roadOptions = Object.keys(game.edges || {})
    .filter(eKey => GameLogic.canPlaceRoad(game, playerId, eKey, false, null).valid)
    .map(eKey => roadPlacementPriorityScore(scoreRoadPlacement(game, eKey, null)));

  const options = [
    settlementOptions.length ? { id: 'settlement', score: Math.max(...settlementOptions) } : null,
    cityOptions.length ? { id: 'city', score: Math.max(...cityOptions) } : null,
    roadOptions.length ? { id: 'road', score: Math.max(...roadOptions) } : null,
  ].filter(Boolean);

  if (!options.length || !options.some(option => option.id === selectedOptionId)) {
    return null;
  }

  return {
    taskId: 'city-vs-settlement-vs-road-prioritization',
    selectedOptionId,
    evaluationContext: {
      options,
    },
  };
}

function scoreRoadPlacement(game, edgeKeyValue, lastSettlement) {
  const actingPlayerId = game.currentPlayer;
  const endpoints = getEdgeVerticesFromKey(edgeKeyValue);
  const forwardVertex = lastSettlement
    ? (endpoints.find(vKey => !GameLogic.areVerticesEqual(vKey, lastSettlement)) || endpoints[0] || null)
    : (endpoints
      .map(vKey => ({
        vKey,
        score: scoreVertexPlacement(game, vKey),
      }))
      .sort((left, right) => (
        ((right.score.resourceProductionExpectancy * 0.5) + (right.score.resourceDiversity * 0.3) + (right.score.portSynergy * 0.2))
        - ((left.score.resourceProductionExpectancy * 0.5) + (left.score.resourceDiversity * 0.3) + (left.score.portSynergy * 0.2))
      ))[0]?.vKey || endpoints[0] || null);
  const forwardVertexScore = forwardVertex ? scoreVertexPlacement(game, forwardVertex) : {
    resourceProductionExpectancy: 0,
    resourceDiversity: 0,
    expansionAccess: 0,
    portSynergy: 0,
  };
  const nextEdges = forwardVertex
    ? GameLogic.getVertexEdges(forwardVertex).filter(candidate => candidate !== edgeKeyValue)
    : [];

  return {
    futureReachability: Math.min(1, nextEdges.length / 3),
    reachableIntersectionValue: (
      (forwardVertexScore.resourceProductionExpectancy * 0.5)
      + (forwardVertexScore.resourceDiversity * 0.3)
      + (forwardVertexScore.portSynergy * 0.2)
    ),
    blockingValue: actingPlayerId ? scoreRoadBlockingValue(game, actingPlayerId, edgeKeyValue) : 0,
  };
}

function hasRequiredResources(player = {}, cost = {}) {
  return Object.entries(cost).every(([resource, amount]) => (Number(player.resources?.[resource]) || 0) >= amount);
}

function scoreResourceNeed(game, playerId) {
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return {};
  return computeGoalAwareResourceMetrics(player.resources || {}, buildGoalAwarePlanCandidates(game, playerId)).need;
}

function scoreResourceSurplus(game, playerId) {
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return {};
  return computeGoalAwareResourceMetrics(player.resources || {}, buildGoalAwarePlanCandidates(game, playerId)).surplus;
}

function identifyLeaderIndex(game) {
  const players = game.players || [];
  let bestIndex = 0;
  let bestScore = Number.NEGATIVE_INFINITY;
  players.forEach((player, index) => {
    const score = (player.victoryPoints || 0) + ((player.longestRoad ? 2 : 0)) + ((player.largestArmy ? 2 : 0));
    if (score > bestScore) {
      bestScore = score;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function sumResources(resources = {}) {
  return Object.values(resources || {}).reduce((sum, value) => sum + (Number(value) || 0), 0);
}

function getPlayerVisibleScore(player = {}) {
  return (player.victoryPoints || 0)
    + (player.hasLongestRoad ? 2 : 0)
    + (player.hasLargestArmy ? 2 : 0);
}

function scoreRobberHexPlacement(game, actingPlayerIndex, hexKeyValue) {
  const leaderIndex = identifyLeaderIndex(game);
  const hex = game.hexes?.[hexKeyValue];
  if (!hex) {
    return {
      hurtLeader: 0,
      productionDamage: 0,
      theftValue: 0,
      selfHarmAvoidance: 0,
    };
  }

  const playersOnHex = GameLogic.getPlayersOnHex(game, hexKeyValue, actingPlayerIndex) || [];
  const numberWeight = DICE_PROBABILITY_WEIGHTS[hex.number] || 0;
  let leaderDamage = 0;
  let totalDamage = 0;
  let bestTheftValue = 0;

  playersOnHex.forEach(playerIndex => {
    const player = game.players[playerIndex];
    if (!player) return;
    const resourceTotal = sumResources(player.resources);
    const strengthScore = Math.min(1, (getPlayerVisibleScore(player) + (resourceTotal / 8)) / 5);
    totalDamage += strengthScore;
    if (playerIndex === leaderIndex) {
      leaderDamage += strengthScore;
    }
    bestTheftValue = Math.max(bestTheftValue, Math.min(1, resourceTotal / 7));
  });

  const myBuildingsOnHex = (GameLogic.getPlayersOnHex(game, hexKeyValue, null) || []).includes(actingPlayerIndex);
  const normalizedProductionDamage = Math.min(1, (totalDamage * numberWeight) / 5);
  const normalizedLeaderDamage = Math.min(1, (leaderDamage * numberWeight) / 3);

  return {
    hurtLeader: normalizedLeaderDamage,
    productionDamage: normalizedProductionDamage,
    theftValue: bestTheftValue,
    selfHarmAvoidance: myBuildingsOnHex ? 0 : 1,
  };
}

function buildRobberTaskPayload(game, playerId, hexKeyValue, stealFromPlayerId) {
  const actingPlayerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  if (actingPlayerIndex === -1) return null;

  const hexOptions = Object.keys(game.hexes || {})
    .filter(candidateHexKey => candidateHexKey !== game.robber)
    .map(candidateHexKey => ({
      id: candidateHexKey,
      ...scoreRobberHexPlacement(game, actingPlayerIndex, candidateHexKey),
    }));

  const payloads = [{
    taskId: 'robber-placement',
    selectedHexId: hexKeyValue,
    evaluationContext: {
      options: hexOptions,
    },
  }];

  const victimOptions = (GameLogic.getPlayersOnHex(game, hexKeyValue, actingPlayerIndex) || [])
    .map(playerIndex => {
      const player = game.players[playerIndex];
      const resourceTotal = sumResources(player?.resources);
      const isLeader = playerIndex === identifyLeaderIndex(game);
      return {
        id: player?.id || `player-${playerIndex}`,
        score: Math.min(
          1,
          (isLeader ? 0.55 : 0.25)
          + Math.min(0.35, resourceTotal / 20)
          + Math.min(0.1, getPlayerVisibleScore(player) / 20)
        ),
      };
    });

  if (victimOptions.length && stealFromPlayerId) {
    payloads.push({
      taskId: 'robber-victim-selection',
      selectedOptionId: stealFromPlayerId,
      evaluationContext: {
        options: victimOptions,
      },
    });
  }

  return payloads;
}

function tradeBundleValue(bundle = {}, scoreMap = {}) {
  return Object.entries(bundle || {}).reduce((sum, [resource, amount]) => (
    sum + ((scoreMap[resource] || 0) * (Number(amount) || 0))
  ), 0);
}

function scoreTradeOfferForPlayer(game, fromPlayer, toPlayer, offer = {}, request = {}) {
  const needMap = scoreResourceNeed(game, fromPlayer?.id);
  const surplusMap = scoreResourceSurplus(game, fromPlayer?.id);
  const giveCost = tradeBundleValue(offer, surplusMap);
  const requestValue = tradeBundleValue(request, needMap);
  const leaderIndex = identifyLeaderIndex(game);
  const toIndex = game.players.findIndex(candidate => candidate.id === toPlayer?.id);
  const leaderHelpPenalty = toIndex === leaderIndex ? 1 : 0;
  const fairnessPenalty = Math.max(0, giveCost - requestValue);

  return {
    legal: Object.entries(offer).every(([resource, amount]) => (fromPlayer.resources?.[resource] || 0) >= amount),
    selfGain: Math.max(0, Math.min(1, requestValue - (giveCost * 0.5))),
    leaderHelpPenalty,
    fairnessPenalty: Math.min(1, fairnessPenalty),
  };
}

function canPlayerFulfillTradeBundle(player, bundle = {}) {
  return Object.entries(bundle || {}).every(([resource, amount]) => (
    (player?.resources?.[resource] || 0) >= (Number(amount) || 0)
  ));
}

function scoreTargetedTradePartner(game, fromPlayer, toPlayer, offer = {}, request = {}) {
  const offerScore = scoreTradeOfferForPlayer(game, fromPlayer, toPlayer, offer, request);
  const availabilityBonus = canPlayerFulfillTradeBundle(toPlayer, request) ? 0.25 : 0;
  return {
    id: toPlayer.id,
    score: Math.max(
      0,
      Math.min(
        1,
        offerScore.selfGain + availabilityBonus - (offerScore.leaderHelpPenalty * 0.4)
      )
    ),
    canFulfillRequest: canPlayerFulfillTradeBundle(toPlayer, request),
    selfGain: offerScore.selfGain,
    leaderHelpPenalty: offerScore.leaderHelpPenalty,
  };
}

function buildRoadPlacementTaskPayload(game, playerId, edgeKeyValue, isSetup, lastSettlement) {
  const evaluationContext = isSetup
    ? buildRoadEvaluationContext(game, playerId, lastSettlement)
    : {
      legalOptions: Object.keys(game.edges || {})
        .filter(eKey => GameLogic.canPlaceRoad(game, playerId, eKey, false, null).valid)
        .map(eKey => ({
          id: eKey,
          ...scoreRoadPlacement(game, eKey, lastSettlement),
        })),
    };

  return {
    taskId: 'road-placement-direction',
    selectedOptionId: edgeKeyValue,
    evaluationContext,
  };
}

function buildBankTradeTaskPayload(game, playerId, giveResource, giveAmount, getResource) {
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  const player = game.players[playerIndex];
  const needMap = scoreResourceNeed(game, playerId);
  const surplusMap = scoreResourceSurplus(game, playerId);
  const candidateOptions = [];

  Object.keys(player.resources || {}).forEach(resourceToGive => {
    const requiredRatio = GameLogic.getTradeRatio(game, playerIndex, resourceToGive);
    if ((player.resources?.[resourceToGive] || 0) < requiredRatio) return;
    Object.keys(player.resources || {}).forEach(resourceToGet => {
      if (resourceToGet === resourceToGive) return;
      const score = Math.max(0, Math.min(1, (needMap[resourceToGet] || 0) - ((surplusMap[resourceToGive] || 0) * 0.5) + 0.5));
      candidateOptions.push({
        id: `${resourceToGive}:${requiredRatio}:${resourceToGet}`,
        score,
      });
    });
  });

  candidateOptions.push({
    id: 'no-trade',
    score: 0.45,
  });

  return {
    taskId: 'bank-trade-decision',
    selectedOptionId: `${giveResource}:${giveAmount}:${getResource}`,
    evaluationContext: {
      options: candidateOptions,
    },
  };
}

function buildTradeProposalTaskPayload(game, playerId, offer, request, targetPlayerId) {
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  const fromPlayer = game.players[playerIndex];
  const possiblePartners = game.players.filter(candidate => candidate.id !== playerId);
  const recipients = targetPlayerId
    ? game.players.filter(candidate => candidate.id === targetPlayerId)
    : possiblePartners;
  const scoredTargets = possiblePartners.map(candidate => (
    scoreTargetedTradePartner(game, fromPlayer, candidate, offer, request)
  ));
  const selectedTargetId = targetPlayerId || recipients[0]?.id || 'all-opponents';
  const selectedTarget = recipients.find(candidate => candidate.id === selectedTargetId) || recipients[0] || null;

  return {
    taskId: 'generate-trade-offers',
    selectedOptionId: selectedTargetId,
    evaluationContext: {
      bestOfferScore: 1,
    },
    offerContext: scoreTradeOfferForPlayer(game, fromPlayer, selectedTarget, offer, request),
    partnerContext: targetPlayerId ? {
      taskId: 'select-targeted-trade-partner',
      selectedOptionId: selectedTargetId,
      evaluationContext: {
        options: scoredTargets,
      },
    } : null,
  };
}

function buildTradeResponseTaskPayload(game, playerId, accept) {
  const tradeOffer = game.tradeOffer;
  if (!tradeOffer) return null;
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  const responder = game.players[playerIndex];
  const proposer = game.players[tradeOffer.from];
  const acceptScore = scoreTradeOfferForPlayer(game, responder, proposer, tradeOffer.request, tradeOffer.offer).selfGain;
  const rejectScore = Math.max(0, Math.min(1, 1 - acceptScore + 0.1));
  return {
    taskId: 'accept-or-reject-trade-offers',
    selectedOptionId: accept ? 'accept' : 'reject',
    evaluationContext: {
      options: [
        { id: 'accept', score: acceptScore },
        { id: 'reject', score: rejectScore },
      ],
    },
  };
}

function buildCounterTradeTaskPayload(game, playerId, offer, request) {
  const playerIndex = game.players.findIndex(candidate => candidate.id === playerId);
  const fromPlayer = game.players[playerIndex];
  const originalProposer = game.tradeOffer ? game.players[game.tradeOffer.to] : null;
  return {
    taskId: 'generate-counter-trade-offer',
    selectedOptionId: originalProposer?.id || 'counter-target',
    evaluationContext: {
      bestOfferScore: 1,
    },
    offerContext: scoreTradeOfferForPlayer(game, fromPlayer, originalProposer, offer, request),
  };
}

function buildRoadEvaluationContext(game, playerId, lastSettlement) {
  const legalOptions = Object.keys(game.edges || {})
    .filter(eKey => GameLogic.canPlaceRoad(game, playerId, eKey, true, lastSettlement).valid)
    .map(eKey => ({
      id: eKey,
      ...scoreRoadPlacement(game, eKey, lastSettlement),
    }));

  return {
    legalOptions,
  };
}

function ensureBenchmarkTaskState(game) {
  if (!game.benchmarkTaskState) {
    game.benchmarkTaskState = {
      turnDecisions: {},
      turnCounts: {},
      longTermStats: {},
      yearOfPlenty: {},
    };
  }
  return game.benchmarkTaskState;
}

function getCurrentBenchmarkTurnKey(game) {
  return `${game.id}:${Number(game.roundNumber) || 0}:${Number(game.currentPlayerIndex) || 0}`;
}

function getPlayerTurnDecisionState(game, playerId) {
  const state = ensureBenchmarkTaskState(game);
  const turnKey = getCurrentBenchmarkTurnKey(game);
  if (!state.turnDecisions[playerId] || state.turnDecisions[playerId].turnKey !== turnKey) {
    state.turnDecisions[playerId] = {
      turnKey,
      buildVsSaveRecorded: false,
      devPurchaseRecorded: false,
      knightRecorded: false,
      roadBuildingRecorded: false,
      longestRoadRecorded: false,
      largestArmyRecorded: false,
    };
  }
  return state.turnDecisions[playerId];
}

function markTurnDecisionRecorded(game, playerId, key) {
  getPlayerTurnDecisionState(game, playerId)[key] = true;
}

function hasTurnDecisionRecorded(game, playerId, key) {
  return Boolean(getPlayerTurnDecisionState(game, playerId)[key]);
}

function incrementBenchmarkTurnCount(game, playerId) {
  const state = ensureBenchmarkTaskState(game);
  state.turnCounts[playerId] = (state.turnCounts[playerId] || 0) + 1;
  getPlayerTurnDecisionState(game, playerId);
  return state.turnCounts[playerId];
}

function getBenchmarkTurnCount(game, playerId) {
  return ensureBenchmarkTaskState(game).turnCounts[playerId] || 0;
}

function incrementLongTermStat(game, playerId, statKey, amount = 1) {
  const state = ensureBenchmarkTaskState(game);
  if (!state.longTermStats[playerId]) {
    state.longTermStats[playerId] = {
      roadsBuilt: 0,
      devCardsBought: 0,
    };
  }
  state.longTermStats[playerId][statKey] = (state.longTermStats[playerId][statKey] || 0) + amount;
}

function getLongTermStatSnapshot(game, playerId) {
  const stats = ensureBenchmarkTaskState(game).longTermStats[playerId] || {};
  return {
    roadsBuilt: stats.roadsBuilt || 0,
    devCardsBought: stats.devCardsBought || 0,
  };
}

function getPlayerIndex(game, playerId) {
  return game.players.findIndex(candidate => candidate.id === playerId);
}

function scoreKnightPlayOption(game, playerId) {
  const playerIndex = getPlayerIndex(game, playerId);
  if (playerIndex === -1) return null;
  const player = game.players[playerIndex];
  const robberHex = game.robber ? game.hexes?.[game.robber] : null;
  const selfOnRobber = game.robber ? (GameLogic.getPlayersOnHex(game, game.robber, null) || []).includes(playerIndex) : false;
  const robberThreatLevel = selfOnRobber ? clamp01((DICE_PROBABILITY_WEIGHTS[robberHex?.number] || 3) / 5) : 0.15;
  const needMap = scoreResourceNeed(game, playerId);
  const resourceNeed = Math.max(0, ...Object.values(needMap));
  const bestOpponentKnights = Math.max(0, ...game.players
    .filter(candidate => candidate.id !== playerId)
    .map(candidate => candidate.knightsPlayed || 0));
  const armyProgressRatio = clamp01((player.knightsPlayed + 1) / Math.max(3, bestOpponentKnights + 1));
  return clamp01(
    (robberThreatLevel * 0.45)
    + (resourceNeed * 0.35)
    + (armyProgressRatio * 0.2)
  );
}

function buildKnightDecisionTaskPayload(game, playerId, selectedOptionId) {
  const playNowScore = scoreKnightPlayOption(game, playerId);
  if (playNowScore === null) return null;
  const holdScore = clamp01(0.25 + ((1 - playNowScore) * 0.75));
  return {
    taskId: 'knight-card-playing-decision',
    selectedOptionId,
    evaluationContext: {
      options: [
        { id: 'play-now', score: playNowScore },
        { id: 'hold', score: holdScore },
      ],
    },
  };
}

function scoreRoadBuildingPlayOption(game, playerId) {
  const playerIndex = getPlayerIndex(game, playerId);
  if (playerIndex === -1) return null;
  const player = game.players[playerIndex];
  const roadScores = Object.keys(game.edges || {})
    .filter(eKey => GameLogic.canPlaceRoad(game, playerId, eKey, false, null).valid)
    .map(eKey => roadPlacementPriorityScore(scoreRoadPlacement(game, eKey, null)))
    .sort((left, right) => right - left);
  const topTwoAverage = roadScores.length
    ? roadScores.slice(0, 2).reduce((sum, value) => sum + value, 0) / Math.min(2, roadScores.length)
    : 0;
  const bestOpponentRoad = Math.max(0, ...game.players
    .filter(candidate => candidate.id !== playerId)
    .map(candidate => candidate.roadLength || 0));
  const longestRoadLeverage = clamp01(((player.roadLength || 0) + 2 - bestOpponentRoad + 3) / 6);
  return clamp01((topTwoAverage * 0.7) + (longestRoadLeverage * 0.3));
}

function buildRoadBuildingDecisionTaskPayload(game, playerId, selectedOptionId) {
  const playNowScore = scoreRoadBuildingPlayOption(game, playerId);
  if (playNowScore === null) return null;
  return {
    taskId: 'road-building-card-playing-decision',
    selectedOptionId,
    evaluationContext: {
      options: [
        { id: 'play-now', score: playNowScore },
        { id: 'hold', score: clamp01(0.3 + ((1 - playNowScore) * 0.7)) },
      ],
    },
  };
}

function countDistinctPositiveResources(resources = {}) {
  return RESOURCE_TYPES.filter(resource => (Number(resources[resource]) || 0) > 0).length;
}

function getBestAffordablePlanScoreFromData(planDecisionData = []) {
  return Math.max(0, ...planDecisionData.filter(plan => plan.affordable).map(plan => plan.baseScore));
}

function buildPlanDecisionDataFromResources(game, playerId, resources) {
  return buildPlanDecisionData(game, playerId, resources);
}

function buildYearOfPlentyChoiceTaskPayload(game, playerId, chosenResources = []) {
  const state = ensureBenchmarkTaskState(game).yearOfPlenty[playerId];
  if (!state || chosenResources.length !== 2) return null;
  const snapshotResources = normalizeResourceSnapshot(state.resources);
  const needMap = state.needMap || {};
  const beforePlanData = buildPlanDecisionDataFromResources(game, playerId, snapshotResources);
  const beforeBest = getBestAffordablePlanScoreFromData(beforePlanData);

  const options = [];
  RESOURCE_TYPES.forEach((left, leftIndex) => {
    RESOURCE_TYPES.slice(leftIndex).forEach(right => {
      const afterResources = normalizeResourceSnapshot(snapshotResources);
      afterResources[left] += 1;
      afterResources[right] += 1;
      const afterPlanData = buildPlanDecisionDataFromResources(game, playerId, afterResources);
      const afterBest = getBestAffordablePlanScoreFromData(afterPlanData);
      const buildUnlock = clamp01(Math.max(0, afterBest - beforeBest) + (afterBest > beforeBest ? afterBest : 0));
      const scarcity = clamp01(((needMap[left] || 0) + (needMap[right] || 0)) / 2);
      const diversityGain = clamp01((countDistinctPositiveResources(afterResources) - countDistinctPositiveResources(snapshotResources)) / 2);
      options.push({
        id: [left, right].sort().join('+'),
        score: clamp01((buildUnlock * 0.5) + (scarcity * 0.35) + (diversityGain * 0.15)),
      });
    });
  });

  return {
    taskId: 'year-of-plenty-card-playing-decision',
    selectedOptionId: [...chosenResources].sort().join('+'),
    evaluationContext: {
      options,
    },
  };
}

function buildMonopolyTaskPayload(game, playerId, selectedResource) {
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return null;
  const snapshotResources = normalizeResourceSnapshot(player.resources);
  const needMap = scoreResourceNeed(game, playerId);
  const beforeBest = getBestAffordablePlanScoreFromData(buildPlanDecisionDataFromResources(game, playerId, snapshotResources));
  const totalsByResource = Object.fromEntries(RESOURCE_TYPES.map(resource => [resource, 0]));
  game.players
    .filter(candidate => candidate.id !== playerId)
    .forEach(candidate => {
      RESOURCE_TYPES.forEach(resource => {
        totalsByResource[resource] += Number(candidate.resources?.[resource]) || 0;
      });
    });
  const maxHolding = Math.max(1, ...Object.values(totalsByResource));
  const options = RESOURCE_TYPES.map(resource => {
    const afterResources = normalizeResourceSnapshot(snapshotResources);
    afterResources[resource] += totalsByResource[resource];
    const afterBest = getBestAffordablePlanScoreFromData(buildPlanDecisionDataFromResources(game, playerId, afterResources));
    const estimatedOpponentHoldings = clamp01(totalsByResource[resource] / maxHolding);
    const personalResourceNeed = clamp01(needMap[resource] || 0);
    const buildUnlock = clamp01(Math.max(0, afterBest - beforeBest) + (afterBest > beforeBest ? afterBest : 0));
    return {
      id: resource,
      score: clamp01((estimatedOpponentHoldings * 0.55) + (personalResourceNeed * 0.3) + (buildUnlock * 0.15)),
    };
  });

  return {
    taskId: 'monopoly-card-playing-decision',
    selectedOptionId: selectedResource,
    evaluationContext: {
      options,
    },
  };
}

function enumerateDiscardChoices(resources = {}, discardCount) {
  const normalized = normalizeResourceSnapshot(resources);
  const options = [];

  function walk(index, remaining, current) {
    if (index === RESOURCE_TYPES.length - 1) {
      const resource = RESOURCE_TYPES[index];
      if (remaining <= normalized[resource]) {
        const bundle = {
          ...current,
          [resource]: remaining,
        };
        options.push(bundle);
      }
      return;
    }

    const resource = RESOURCE_TYPES[index];
    const maxDiscard = Math.min(remaining, normalized[resource]);
    for (let amount = 0; amount <= maxDiscard; amount += 1) {
      walk(index + 1, remaining - amount, {
        ...current,
        [resource]: amount,
      });
    }
  }

  walk(0, discardCount, {});
  return options;
}

function scoreRetainedHandValue(game, playerId, resources) {
  const planData = buildPlanDecisionDataFromResources(game, playerId, resources);
  const bestAffordable = getBestAffordablePlanScoreFromData(planData);
  const bestFuture = Math.max(0, ...planData.map(plan => plan.effectiveScore));
  const diversity = clamp01(countDistinctPositiveResources(resources) / RESOURCE_TYPES.length);
  return clamp01((bestAffordable * 0.55) + (bestFuture * 0.3) + (diversity * 0.15));
}

function buildDiscardTaskPayload(game, playerId, discardedResources = {}) {
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return null;
  const discardCount = Math.floor(sumResources(player.resources) / 2);
  const discardOptions = enumerateDiscardChoices(player.resources, discardCount).map(bundle => {
    const retainedResources = normalizeResourceSnapshot(player.resources);
    RESOURCE_TYPES.forEach(resource => {
      retainedResources[resource] = Math.max(0, retainedResources[resource] - (bundle[resource] || 0));
    });
    const id = RESOURCE_TYPES.map(resource => `${resource}:${bundle[resource] || 0}`).join('|');
    return {
      id,
      discardedResources: bundle,
      retainedHandValue: scoreRetainedHandValue(game, playerId, retainedResources),
    };
  });

  return {
    taskId: 'discard-strategy-after-seven',
    selectedDiscardId: RESOURCE_TYPES.map(resource => `${resource}:${discardedResources[resource] || 0}`).join('|'),
    evaluationContext: {
      discardOptions,
    },
  };
}

function getLongestRoadRaceSnapshot(game, playerId) {
  const playerIndex = getPlayerIndex(game, playerId);
  if (playerIndex === -1) return null;
  const opponents = game.players
    .map((player, index) => ({ player, index }))
    .filter(entry => entry.player.id !== playerId);
  const target = opponents.sort((left, right) => (right.player.roadLength || 0) - (left.player.roadLength || 0))[0] || null;
  return {
    playerRoadLength: game.players[playerIndex].roadLength || 0,
    targetPlayerId: target?.player.id || null,
    targetRoadLength: target?.player.roadLength || 0,
    playerHasLongestRoad: Boolean(game.players[playerIndex].hasLongestRoad),
    targetHasLongestRoad: Boolean(target?.player?.hasLongestRoad),
    playerVisibleScore: getPlayerVisibleScore(game.players[playerIndex]),
    stats: getLongTermStatSnapshot(game, playerId),
  };
}

function shouldConsiderLongestRoadRace(game, playerId) {
  const snapshot = getLongestRoadRaceSnapshot(game, playerId);
  if (!snapshot) return false;
  return snapshot.playerRoadLength >= 3 || snapshot.targetRoadLength >= 4 || bestRoadPlanScore(game, playerId) > 0;
}

function getLargestArmyRaceSnapshot(game, playerId) {
  const playerIndex = getPlayerIndex(game, playerId);
  if (playerIndex === -1) return null;
  const opponents = game.players
    .map((player, index) => ({ player, index }))
    .filter(entry => entry.player.id !== playerId);
  const target = opponents.sort((left, right) => (right.player.knightsPlayed || 0) - (left.player.knightsPlayed || 0))[0] || null;
  return {
    playerKnights: game.players[playerIndex].knightsPlayed || 0,
    targetPlayerId: target?.player.id || null,
    targetKnights: target?.player.knightsPlayed || 0,
    playerHasLargestArmy: Boolean(game.players[playerIndex].hasLargestArmy),
    targetHasLargestArmy: Boolean(target?.player?.hasLargestArmy),
    playerVisibleScore: getPlayerVisibleScore(game.players[playerIndex]),
    resourceTotal: sumResources(game.players[playerIndex].resources),
    stats: getLongTermStatSnapshot(game, playerId),
  };
}

function shouldConsiderLargestArmyRace(game, playerId) {
  const snapshot = getLargestArmyRaceSnapshot(game, playerId);
  if (!snapshot) return false;
  const player = game.players.find(candidate => candidate.id === playerId);
  return snapshot.playerKnights >= 1 || snapshot.targetKnights >= 2 || hasRequiredResources(player, PLAN_COSTS.devCard);
}

async function maybeCreatePendingLongTermEpisode(game, playerId, taskId, selectedOptionId) {
  if (!game?.benchmark?.enabled) return null;
  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return null;
  const existing = benchmarkStore.listPendingLongTermTasks({ gameId: game.id, playerId, taskId });
  if (existing.length) return existing[0];

  const stateSnapshot = taskId === 'decide-pursue-longest-road'
    ? getLongestRoadRaceSnapshot(game, playerId)
    : getLargestArmyRaceSnapshot(game, playerId);
  if (!stateSnapshot) return null;

  return benchmarkStore.createPendingLongTermTask({
    runId: game.benchmark.runId,
    benchmarkId: game.benchmark.benchmarkId,
    baselineAgentId: game.benchmark.baselineAgentId,
    gameId: game.id,
    taskId,
    playerKey: player.benchmarkPlayerKey,
    playerId,
    playerName: player.name,
    agentId: player.benchmarkAgentId || player.name,
    agentVersion: player.benchmarkAgentVersion || 'unknown',
    startingSeat: player.benchmarkStartingSeat ?? 0,
    mapLayoutId: game.benchmark.mapLayoutId,
    opponentPolicySet: game.benchmark.opponentPolicySet,
    scenarioTags: game.benchmark.scenarioTags || [],
    selectedOptionId,
    decisionTurn: game.roundNumber ?? 0,
    decisionTurnCount: getBenchmarkTurnCount(game, playerId),
    horizonTurns: 4,
    snapshot: stateSnapshot,
  });
}

function buildLongestRoadFinalizationPayload(game, pendingTask) {
  const player = game.players.find(candidate => candidate.id === pendingTask.playerId);
  if (!player) return null;
  const target = game.players.find(candidate => candidate.id === pendingTask.snapshot?.targetPlayerId) || null;
  const selfGrowth = Math.max(0, (player.roadLength || 0) - (pendingTask.snapshot?.playerRoadLength || 0));
  const targetGrowth = Math.max(0, (target?.roadLength || 0) - (pendingTask.snapshot?.targetRoadLength || 0));
  const roadsBuiltDelta = Math.max(0, (getLongTermStatSnapshot(game, pendingTask.playerId).roadsBuilt || 0) - (pendingTask.snapshot?.stats?.roadsBuilt || 0));
  const opponentProgressThreat = clamp01((((pendingTask.snapshot?.targetRoadLength || 0) - (pendingTask.snapshot?.playerRoadLength || 0)) + ((pendingTask.snapshot?.targetHasLongestRoad ? 1 : 0) * 2) + 2) / 8);
  const blockingEffectiveness = clamp01(0.45 + (selfGrowth * 0.18) - (targetGrowth * 0.12) + (player.hasLongestRoad ? 0.25 : 0));
  const selfRoadSynergy = clamp01((selfGrowth / 4) + (player.hasLongestRoad ? 0.35 : 0));
  const resourceCost = roadsBuiltDelta <= 0
    ? (pendingTask.selectedOptionId === 'defer' ? 1 : 0.5)
    : clamp01((selfGrowth + (player.hasLongestRoad ? 1 : 0.5)) / roadsBuiltDelta);
  const pursueScore = clamp01(
    (blockingEffectiveness * 0.45)
    + (opponentProgressThreat * 0.3)
    + (selfRoadSynergy * 0.15)
    + (resourceCost * 0.1)
  );
  const deferScore = clamp01(
    ((1 - opponentProgressThreat) * 0.45)
    + ((1 - Math.min(1, targetGrowth / 3)) * 0.25)
    + ((1 - Math.min(1, selfGrowth / 3)) * 0.15)
    + ((roadsBuiltDelta === 0 ? 1 : 0.2) * 0.15)
  );

  return {
    taskId: pendingTask.taskId,
    selectedOptionId: pendingTask.selectedOptionId,
    evaluationContext: {
      options: [
        { id: 'pursue', score: pursueScore },
        { id: 'defer', score: deferScore },
      ],
    },
    explanation: `Longest Road episode finalized after ${pendingTask.horizonTurns} turns using threat, growth, and efficiency deltas.`,
  };
}

function buildLargestArmyFinalizationPayload(game, pendingTask) {
  const player = game.players.find(candidate => candidate.id === pendingTask.playerId);
  if (!player) return null;
  const target = game.players.find(candidate => candidate.id === pendingTask.snapshot?.targetPlayerId) || null;
  const devCardsBoughtDelta = Math.max(0, (getLongTermStatSnapshot(game, pendingTask.playerId).devCardsBought || 0) - (pendingTask.snapshot?.stats?.devCardsBought || 0));
  const knightsGained = Math.max(0, (player.knightsPlayed || 0) - (pendingTask.snapshot?.playerKnights || 0));
  const gapStart = (pendingTask.snapshot?.playerKnights || 0) - (pendingTask.snapshot?.targetKnights || 0);
  const gapEnd = (player.knightsPlayed || 0) - (target?.knightsPlayed || 0);
  const knightCardRatio = devCardsBoughtDelta <= 0 ? (knightsGained > 0 ? 1 : 0) : clamp01(knightsGained / devCardsBoughtDelta);
  const opponentArmyGap = clamp01(((gapEnd - gapStart) + 3) / 6);
  const resourceDelta = sumResources(player.resources) - (pendingTask.snapshot?.resourceTotal || 0);
  const devCardAffordability = clamp01(0.6 + (resourceDelta / 10) - (devCardsBoughtDelta * 0.08));
  const vpDelta = getPlayerVisibleScore(player) - (pendingTask.snapshot?.playerVisibleScore || 0);
  const vpImpact = clamp01((vpDelta + (player.hasLargestArmy ? 1 : 0)) / 3);
  const pursueScore = clamp01(
    (knightCardRatio * 0.4)
    + (opponentArmyGap * 0.3)
    + (devCardAffordability * 0.2)
    + (vpImpact * 0.1)
  );
  const deferScore = clamp01(
    ((1 - opponentArmyGap) * 0.35)
    + ((1 - knightCardRatio) * 0.25)
    + ((resourceDelta >= 0 ? 1 : 0.4) * 0.2)
    + ((player.hasLargestArmy ? 0 : 1) * 0.2)
  );

  return {
    taskId: pendingTask.taskId,
    selectedOptionId: pendingTask.selectedOptionId,
    evaluationContext: {
      options: [
        { id: 'pursue', score: pursueScore },
        { id: 'defer', score: deferScore },
      ],
    },
    explanation: `Largest Army episode finalized after ${pendingTask.horizonTurns} turns using knight conversion, race gap, and VP impact.`,
  };
}

async function maybeFinalizeLongTermTasks(game) {
  if (!game?.benchmark?.enabled) return;
  const pendingTasks = benchmarkStore.listPendingLongTermTasks({ gameId: game.id });
  for (const pendingTask of pendingTasks) {
    const player = game.players.find(candidate => candidate.id === pendingTask.playerId);
    if (!player) continue;
    const turnsElapsed = getBenchmarkTurnCount(game, pendingTask.playerId) - (pendingTask.decisionTurnCount || 0);
    const horizonMet = turnsElapsed >= (pendingTask.horizonTurns || 4);
    const decisiveResolution = pendingTask.taskId === 'decide-pursue-longest-road'
      ? Boolean(player.hasLongestRoad)
      : Boolean(player.hasLargestArmy);
    const shouldFinalize = horizonMet || decisiveResolution || game.phase === 'finished';
    if (!shouldFinalize) continue;

    const payload = pendingTask.taskId === 'decide-pursue-longest-road'
      ? buildLongestRoadFinalizationPayload(game, pendingTask)
      : buildLargestArmyFinalizationPayload(game, pendingTask);
    if (!payload) continue;

    await benchmarkStore.recordTaskResult({
      runId: pendingTask.runId,
      benchmarkId: pendingTask.benchmarkId,
      baselineAgentId: pendingTask.baselineAgentId,
      taskId: pendingTask.taskId,
      playerKey: pendingTask.playerKey,
      playerName: pendingTask.playerName,
      agentId: pendingTask.agentId,
      agentVersion: pendingTask.agentVersion,
      startingSeat: pendingTask.startingSeat,
      mapLayoutId: pendingTask.mapLayoutId,
      opponentPolicySet: pendingTask.opponentPolicySet,
      scenarioTags: pendingTask.scenarioTags,
      gameId: pendingTask.gameId,
      turnNumber: game.roundNumber ?? null,
      response: {
        selectedOptionId: payload.selectedOptionId,
      },
      evaluationContext: payload.evaluationContext,
      explanation: payload.explanation,
    });
    await benchmarkStore.removePendingLongTermTask(pendingTask.id);
  }
}

async function persistBenchmarkTaskPayload(game, playerId, benchmarkTaskPayload, timing = {}) {
  if (!game?.benchmark?.enabled) return null;
  if (!benchmarkTaskPayload) return null;

  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return null;
  const response = {
    ...(benchmarkTaskPayload.response || {}),
  };

  if (benchmarkTaskPayload.selectedOptionId !== undefined && response.selectedOptionId === undefined) {
    response.selectedOptionId = benchmarkTaskPayload.selectedOptionId;
  }
  if (benchmarkTaskPayload.selectedHexId !== undefined && response.selectedHexId === undefined) {
    response.selectedHexId = benchmarkTaskPayload.selectedHexId;
  }
  if (benchmarkTaskPayload.selectedDiscardId !== undefined && response.selectedDiscardId === undefined) {
    response.selectedDiscardId = benchmarkTaskPayload.selectedDiscardId;
  }
  if (benchmarkTaskPayload.choice !== undefined && response.choice === undefined) {
    response.choice = benchmarkTaskPayload.choice;
  }
  if (benchmarkTaskPayload.offer !== undefined && response.offer === undefined) {
    response.offer = benchmarkTaskPayload.offer;
  }
  if (benchmarkTaskPayload.request !== undefined && response.request === undefined) {
    response.request = benchmarkTaskPayload.request;
  }

  return benchmarkStore.recordTaskResult({
    runId: game.benchmark.runId,
    benchmarkId: game.benchmark.benchmarkId,
    taskId: benchmarkTaskPayload.taskId,
    playerKey: player.benchmarkPlayerKey,
    playerName: player.name,
    agentId: player.benchmarkAgentId || player.name,
    agentVersion: player.benchmarkAgentVersion || 'unknown',
    startingSeat: player.benchmarkStartingSeat ?? 0,
    mapLayoutId: game.benchmark.mapLayoutId,
    opponentPolicySet: game.benchmark.opponentPolicySet,
    scenarioTags: game.benchmark.scenarioTags || [],
    gameId: game.id,
    turnNumber: game.roundNumber ?? null,
    latencyMs: timing.decisionTimeMs ?? null,
    retries: timing.retryIndex ?? 0,
    response: Object.keys(response).length ? response : null,
    evaluationContext: benchmarkTaskPayload.evaluationContext || null,
    offerContext: benchmarkTaskPayload.offerContext || null,
    explanation: benchmarkTaskPayload.explanation || null,
    evaluationDetails: benchmarkTaskPayload.evaluationDetails || null,
  });
}

async function recordBenchmarkTaskFromAction(game, playerId, benchmarkTaskPayload, timing = {}) {
  if (Array.isArray(benchmarkTaskPayload)) {
    const results = [];
    for (const payload of benchmarkTaskPayload) {
      const result = await persistBenchmarkTaskPayload(game, playerId, payload, timing);
      if (result) {
        results.push(result);
      }
    }
    return results;
  }
  return persistBenchmarkTaskPayload(game, playerId, benchmarkTaskPayload, timing);
}

async function recordBenchmarkAction(game, playerId, actionName, payload, result, requestStartedAt, benchmarkTaskPayload = null) {
  if (!game?.benchmark?.enabled) return;
  const telemetryRecord = await benchmarkStore.recordAction(game, playerId, actionName, payload, result, {
    requestStartedAt,
    acknowledgedAt: Date.now(),
    clientTimestamp: extractClientTimestamp(payload),
  });
  if (result?.success) {
    await recordBenchmarkTaskFromAction(game, playerId, benchmarkTaskPayload, telemetryRecord || {});
  }
  if (game.phase === 'finished') {
    await maybeFinalizeLongTermTasks(game);
    await benchmarkStore.completeGame(game);
  }
}

async function registerBenchmarkGameIfNeeded(game) {
  if (!game?.benchmark?.enabled) return;
  await benchmarkStore.registerBenchmarkGame(game);
}

function maybeStartBenchmarkTurn(game) {
  if (!game?.benchmark?.enabled) return;
  const currentPlayer = game.players?.[game.currentPlayerIndex];
  if (!currentPlayer) return;
  incrementBenchmarkTurnCount(game, currentPlayer.id);
  void maybeFinalizeLongTermTasks(game);
  benchmarkStore.startTurn(game, currentPlayer.id);
}

// ============================================================================
// SOCKET.IO EVENT HANDLERS
// ============================================================================

io.on('connection', (socket) => {
  // --------------------------------------------------------------------
  // CONNECTION MANAGEMENT
  // --------------------------------------------------------------------
  
  // Check if server is at capacity
  if (totalConnectedPlayers >= MAX_TOTAL_PLAYERS) {
    console.log('Server at capacity, rejecting connection:', socket.id);
    socket.emit('serverFull', { 
      message: 'Server is at maximum capacity. Please try again in a few minutes.',
      players: totalConnectedPlayers,
      maxPlayers: MAX_TOTAL_PLAYERS
    });
    socket.disconnect(true);
    return;
  }
  
  totalConnectedPlayers++;
  console.log(`Player connected: ${socket.id} (Total: ${totalConnectedPlayers}/${MAX_TOTAL_PLAYERS})`);
  
  // --------------------------------------------------------------------
  // GAME CREATION AND JOINING
  // --------------------------------------------------------------------
  
  /** Create a new game room as the host */
  socket.on('createGame', async ({ playerName, isExtended = false, enableSpecialBuild = true, benchmark = null }, callback) => {
    // Check game limit
    if (games.size >= MAX_CONCURRENT_GAMES) {
      callback({ 
        success: false, 
        error: 'Server has reached maximum number of games. Please try again later.' 
      });
      return;
    }
    
    const gameCode = generateGameCode();
    const playerId = uuidv4();
    
    const game = GameLogic.createGame(gameCode, {
      id: playerId,
      name: playerName
    }, isExtended, enableSpecialBuild);

    if (benchmark?.enabled) {
      const benchmarkPlayer = {
        playerId,
        playerKey: benchmark.player?.playerKey || `${benchmark.runId || gameCode}:${playerId}`,
        agentId: benchmark.player?.agentId || playerName,
        agentVersion: benchmark.player?.agentVersion || 'unknown',
        playerName,
        baseline: Boolean(benchmark.player?.baseline) || benchmark.baselineAgentId === (benchmark.player?.agentId || playerName),
        startingSeat: benchmark.player?.startingSeat ?? 0,
        runId: benchmark.runId || null,
      };
      game.benchmark = {
        enabled: true,
        runId: benchmark.runId || uuidv4(),
        benchmarkId: benchmark.benchmarkId || 'default-benchmark',
        taskId: benchmark.taskId || null,
        gameType: benchmark.gameType || 'full-game',
        mapLayoutId: benchmark.mapLayoutId || null,
        opponentPolicySet: benchmark.opponentPolicySet || 'default-opponent',
        scenarioTags: Array.isArray(benchmark.scenarioTags) ? benchmark.scenarioTags : [],
        baselineAgentId: benchmark.baselineAgentId || null,
        metadata: benchmark.metadata || {},
        players: [benchmarkPlayer],
      };
      game.players[0] = mergeBenchmarkPlayer(game.players[0], benchmarkPlayer, 0);
      await registerBenchmarkGameIfNeeded(game);
    }
    
    // Add timestamp for cleanup
    game.createdAt = Date.now();
    
    games.set(gameCode, game);
    playerSockets.set(socket.id, { gameId: gameCode, playerId });
    socket.join(gameCode);
    
    console.log(`Game ${gameCode} created by ${playerName}`);
    
    callback({
      success: true,
      gameCode,
      playerId,
      gameState: GameLogic.getPlayerView(game, playerId)
    });
  });
  
  /** Join an existing game room using a game code */
  socket.on('joinGame', async ({ gameCode, playerName, benchmark = null }, callback) => {
    const game = games.get(gameCode.toUpperCase());
    
    if (!game) {
      callback({ success: false, error: 'Game not found' });
      return;
    }
    
    const playerId = uuidv4();
    const result = GameLogic.addPlayer(game, { id: playerId, name: playerName });
    
    if (!result.success) {
      callback({ success: false, error: result.error });
      return;
    }
    
    playerSockets.set(socket.id, { gameId: gameCode.toUpperCase(), playerId });
    socket.join(gameCode.toUpperCase());

    if (game.benchmark?.enabled) {
      const benchmarkPlayer = {
        playerId,
        playerKey: benchmark?.player?.playerKey || `${game.benchmark.runId || gameCode.toUpperCase()}:${playerId}`,
        agentId: benchmark?.player?.agentId || playerName,
        agentVersion: benchmark?.player?.agentVersion || 'unknown',
        playerName,
        baseline: Boolean(benchmark?.player?.baseline) || game.benchmark.baselineAgentId === (benchmark?.player?.agentId || playerName),
        startingSeat: benchmark?.player?.startingSeat ?? (game.players.length - 1),
        runId: game.benchmark.runId,
      };
      game.benchmark.players.push(benchmarkPlayer);
      game.players[game.players.length - 1] = mergeBenchmarkPlayer(
        game.players[game.players.length - 1],
        benchmarkPlayer,
        game.players.length - 1
      );
      await registerBenchmarkGameIfNeeded(game);
    }
    
    console.log(`${playerName} joined game ${gameCode}`);
    
    callback({
      success: true,
      gameCode: gameCode.toUpperCase(),
      playerId,
      gameState: GameLogic.getPlayerView(game, playerId)
    });
    
    // Notify all players
    broadcastToGame(gameCode.toUpperCase(), 'playerJoined', { playerName });
    broadcastGameState(gameCode.toUpperCase());
  });
  
  // --------------------------------------------------------------------
  // GAME FLOW CONTROLS
  // --------------------------------------------------------------------
  
  /** Start the game (host only) - randomizes player order and begins setup */
  socket.on('startGame', async (callback) => {
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    if (!game) {
      callback({ success: false, error: 'Game not found' });
      return;
    }
    
    // Only host can start
    if (game.players[0].id !== playerInfo.playerId) {
      callback({ success: false, error: 'Only host can start the game' });
      return;
    }
    
    const result = GameLogic.startGame(game);
    
    if (result.success) {
      if (game.benchmark?.enabled) {
        game.players = game.players.map((player, index) => {
          const benchmarkPlayer = game.benchmark.players.find(candidate => candidate.playerId === player.id) || {};
          const merged = mergeBenchmarkPlayer(player, {
            ...benchmarkPlayer,
            startingSeat: index,
          }, index);
          if (benchmarkPlayer.playerId) {
            benchmarkPlayer.startingSeat = index;
          }
          return merged;
        });
        await registerBenchmarkGameIfNeeded(game);
        maybeStartBenchmarkTurn(game);
      }
      broadcastToGame(playerInfo.gameId, 'gameStarted', { 
        turnOrder: result.turnOrder 
      });
      broadcastGameState(playerInfo.gameId);
    }
    
    callback(result);
  });
  
  /** Shuffle the board layout (host only, before game starts) */
  socket.on('shuffleBoard', (callback) => {
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    if (!game) {
      callback({ success: false, error: 'Game not found' });
      return;
    }
    
    // Only host can shuffle
    if (game.players[0].id !== playerInfo.playerId) {
      callback({ success: false, error: 'Only host can shuffle the board' });
      return;
    }
    
    const result = GameLogic.shuffleBoard(game);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'boardShuffled', {});
      broadcastGameState(playerInfo.gameId);
    }
    
    callback(result);
  });
  
  // --------------------------------------------------------------------
  // TURN ACTIONS
  // --------------------------------------------------------------------
  
  /** Roll dice at start of turn - distributes resources or triggers robber */
  socket.on('rollDice', async (callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const result = GameLogic.rollDice(game, playerInfo.playerId);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'diceRolled', { 
        roll: result.roll,
        playerId: playerInfo.playerId 
      });
      
      // Broadcast resource gains to all players (resource gains are public in Catan)
      if (result.resourceGains) {
        // Collect all gains for broadcast
        const allGains = [];
        game.players.forEach((player, idx) => {
          const gains = result.resourceGains[idx];
          const hasGains = Object.values(gains).some(v => v > 0);
          if (hasGains) {
            allGains.push({
              playerId: player.id,
              playerName: player.name,
              playerIndex: idx,
              gains
            });
          }
        });
        
        // Broadcast all resource gains to everyone in the game
        if (allGains.length > 0) {
          broadcastToGame(playerInfo.gameId, 'resourcesDistributed', {
            fromRoll: result.roll.total,
            allGains
          });
        }
        
        // Also send personal notification to each player who received resources
        game.players.forEach((player, idx) => {
          const gains = result.resourceGains[idx];
          const hasGains = Object.values(gains).some(v => v > 0);
          if (hasGains) {
            const socketEntry = [...playerSockets.entries()]
              .find(([_, v]) => v.gameId === playerInfo.gameId && v.playerId === player.id);
            if (socketEntry) {
              io.to(socketEntry[0]).emit('resourcesReceived', { 
                gains,
                fromRoll: result.roll.total
              });
            }
          }
        });
      }
      
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'rollDice', {}, result, requestStartedAt);
    callback(result);
  });
  
  /** Discard cards when a 7 is rolled (for players with >7 cards) */
  socket.on('discardCards', async ({ resources }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? buildDiscardTaskPayload(game, playerInfo.playerId, resources)
      : null;
    const result = GameLogic.discardCards(game, playerInfo.playerId, resources);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'discardCards', { resources }, result, requestStartedAt, benchmarkTaskPayload);
    callback(result);
  });
  
  /** Move robber and optionally steal from a player */
  socket.on('moveRobber', async ({ hexKey, stealFromPlayerId }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? buildRobberTaskPayload(game, playerInfo.playerId, hexKey, stealFromPlayerId)
      : null;
    const result = GameLogic.moveRobber(game, playerInfo.playerId, hexKey, stealFromPlayerId);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'robberMoved', { hexKey });
      
      // Send steal notifications to both players
      if (result.stolenInfo) {
        const { resource, thief, thiefName, victim, victimName } = result.stolenInfo;
        
        // Notify the thief what they stole
        const thiefSocket = [...playerSockets.entries()]
          .find(([_, v]) => v.gameId === playerInfo.gameId && v.playerId === thief)?.[0];
        if (thiefSocket) {
          io.to(thiefSocket).emit('stealResult', { 
            type: 'stole',
            resource,
            otherPlayer: victimName
          });
        }
        
        // Notify the victim what was stolen from them
        const victimSocket = [...playerSockets.entries()]
          .find(([_, v]) => v.gameId === playerInfo.gameId && v.playerId === victim)?.[0];
        if (victimSocket) {
          io.to(victimSocket).emit('stealResult', { 
            type: 'stolen',
            resource,
            otherPlayer: thiefName
          });
        }
      }
      
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'moveRobber',
      { hexKey, stealFromPlayerId },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    callback(result);
  });
  
  // --------------------------------------------------------------------
  // BUILDING ACTIONS
  // --------------------------------------------------------------------
  
  /** Place a settlement on a vertex */
  socket.on('placeSettlement', async ({ vertexKey, isSetup }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? (
        isSetup
          ? {
            taskId: 'initial-settlement-location-selection',
            selectedOptionId: vertexKey,
            evaluationContext: buildSettlementEvaluationContext(game, playerInfo.playerId),
          }
          : [
            buildBuildVsSaveTaskPayload(game, playerInfo.playerId, 'settlement'),
            buildBuildPrioritizationTaskPayload(game, playerInfo.playerId, 'settlement'),
            {
              taskId: 'settlement-location-selection',
              selectedOptionId: vertexKey,
              evaluationContext: buildSettlementEvaluationContext(game, playerInfo.playerId),
            },
          ].filter(Boolean)
      )
      : null;
    const result = GameLogic.placeSettlement(game, playerInfo.playerId, vertexKey);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'settlementPlaced', { 
        vertexKey, 
        playerId: playerInfo.playerId 
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'placeSettlement',
      { vertexKey, isSetup },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    if (result.success && game?.benchmark?.enabled && !isSetup) {
      markTurnDecisionRecorded(game, playerInfo.playerId, 'buildVsSaveRecorded');
    }
    callback(result);
  });
  
  /** Place a road on an edge */
  socket.on('placeRoad', async ({ edgeKey, isSetup, lastSettlement }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const countsAsSpendDecision = !isSetup && (game?.freeRoads || 0) === 0;
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? [
        countsAsSpendDecision ? buildBuildVsSaveTaskPayload(game, playerInfo.playerId, 'road') : null,
        countsAsSpendDecision ? buildBuildPrioritizationTaskPayload(game, playerInfo.playerId, 'road') : null,
        buildRoadPlacementTaskPayload(game, playerInfo.playerId, edgeKey, Boolean(isSetup), lastSettlement),
      ].filter(Boolean)
      : null;
    const result = GameLogic.placeRoad(game, playerInfo.playerId, edgeKey, isSetup, lastSettlement);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'roadPlaced', { 
        edgeKey, 
        playerId: playerInfo.playerId 
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'placeRoad',
      { edgeKey, isSetup, lastSettlement },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    if (result.success && game?.benchmark?.enabled) {
      incrementLongTermStat(game, playerInfo.playerId, 'roadsBuilt', 1);
      if (countsAsSpendDecision) {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'buildVsSaveRecorded');
        if (!hasTurnDecisionRecorded(game, playerInfo.playerId, 'longestRoadRecorded') && shouldConsiderLongestRoadRace(game, playerInfo.playerId)) {
          await maybeCreatePendingLongTermEpisode(game, playerInfo.playerId, 'decide-pursue-longest-road', 'pursue');
          markTurnDecisionRecorded(game, playerInfo.playerId, 'longestRoadRecorded');
        }
      }
    }
    callback(result);
  });
  
  /** Upgrade an existing settlement to a city */
  socket.on('upgradeToCity', async ({ vertexKey }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? [
        buildBuildVsSaveTaskPayload(game, playerInfo.playerId, 'city'),
        buildBuildPrioritizationTaskPayload(game, playerInfo.playerId, 'city'),
      ].filter(Boolean)
      : null;
    const result = GameLogic.upgradeToCity(game, playerInfo.playerId, vertexKey);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'cityBuilt', { 
        vertexKey, 
        playerId: playerInfo.playerId 
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'upgradeToCity',
      { vertexKey },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    if (result.success && game?.benchmark?.enabled) {
      markTurnDecisionRecorded(game, playerInfo.playerId, 'buildVsSaveRecorded');
    }
    callback(result);
  });
  
  // --------------------------------------------------------------------
  // DEVELOPMENT CARDS
  // --------------------------------------------------------------------
  
  /** Buy a development card from the deck */
  socket.on('buyDevCard', async (callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? [
        buildBuildVsSaveTaskPayload(game, playerInfo.playerId, 'devCard'),
        buildDevelopmentCardPurchaseTaskPayload(game, playerInfo.playerId, 'buy-dev-card'),
      ].filter(Boolean)
      : null;
    const result = GameLogic.buyDevCard(game, playerInfo.playerId);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'buyDevCard', {}, result, requestStartedAt, benchmarkTaskPayload);
    if (result.success && game?.benchmark?.enabled) {
      incrementLongTermStat(game, playerInfo.playerId, 'devCardsBought', 1);
      markTurnDecisionRecorded(game, playerInfo.playerId, 'buildVsSaveRecorded');
      markTurnDecisionRecorded(game, playerInfo.playerId, 'devPurchaseRecorded');
      if (!hasTurnDecisionRecorded(game, playerInfo.playerId, 'largestArmyRecorded') && shouldConsiderLargestArmyRace(game, playerInfo.playerId)) {
        await maybeCreatePendingLongTermEpisode(game, playerInfo.playerId, 'decide-pursue-largest-army', 'pursue');
        markTurnDecisionRecorded(game, playerInfo.playerId, 'largestArmyRecorded');
      }
    }
    callback(result);
  });
  
  /** Play a development card */
  socket.on('playDevCard', async ({ cardType, params }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    let benchmarkTaskPayload = null;
    if (game?.benchmark?.enabled) {
      if (cardType === 'knight') {
        benchmarkTaskPayload = buildKnightDecisionTaskPayload(game, playerInfo.playerId, 'play-now');
      } else if (cardType === 'roadBuilding') {
        benchmarkTaskPayload = buildRoadBuildingDecisionTaskPayload(game, playerInfo.playerId, 'play-now');
      } else if (cardType === 'monopoly') {
        benchmarkTaskPayload = buildMonopolyTaskPayload(game, playerInfo.playerId, params?.resource);
      } else if (cardType === 'yearOfPlenty') {
        ensureBenchmarkTaskState(game).yearOfPlenty[playerInfo.playerId] = {
          resources: normalizeResourceSnapshot(game.players.find(candidate => candidate.id === playerInfo.playerId)?.resources || {}),
          needMap: scoreResourceNeed(game, playerInfo.playerId),
        };
      }
    }
    const result = GameLogic.playDevCard(game, playerInfo.playerId, cardType, params);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'devCardPlayed', { 
        cardType, 
        playerId: playerInfo.playerId 
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'playDevCard', { cardType, params }, result, requestStartedAt, benchmarkTaskPayload);
    if (result.success && game?.benchmark?.enabled) {
      if (cardType === 'knight') {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'knightRecorded');
        if (!hasTurnDecisionRecorded(game, playerInfo.playerId, 'largestArmyRecorded') && shouldConsiderLargestArmyRace(game, playerInfo.playerId)) {
          await maybeCreatePendingLongTermEpisode(game, playerInfo.playerId, 'decide-pursue-largest-army', 'pursue');
          markTurnDecisionRecorded(game, playerInfo.playerId, 'largestArmyRecorded');
        }
      }
      if (cardType === 'roadBuilding') {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'roadBuildingRecorded');
        if (!hasTurnDecisionRecorded(game, playerInfo.playerId, 'longestRoadRecorded') && shouldConsiderLongestRoadRace(game, playerInfo.playerId)) {
          await maybeCreatePendingLongTermEpisode(game, playerInfo.playerId, 'decide-pursue-longest-road', 'pursue');
          markTurnDecisionRecorded(game, playerInfo.playerId, 'longestRoadRecorded');
        }
      }
    } else if (!result.success && game?.benchmark?.enabled && cardType === 'yearOfPlenty') {
      delete ensureBenchmarkTaskState(game).yearOfPlenty[playerInfo.playerId];
    }
    callback(result);
  });
  
  /** Pick a resource for Year of Plenty card */
  socket.on('yearOfPlentyPick', async ({ resource }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const previousPicksRemaining = game.yearOfPlentyPicks;
    const result = GameLogic.yearOfPlentyPick(game, playerInfo.playerId, resource);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    let benchmarkTaskPayload = null;
    if (result.success && game?.benchmark?.enabled) {
      const taskState = ensureBenchmarkTaskState(game);
      const context = taskState.yearOfPlenty[playerInfo.playerId] || {
        resources: normalizeResourceSnapshot(game.players.find(candidate => candidate.id === playerInfo.playerId)?.resources || {}),
        needMap: scoreResourceNeed(game, playerInfo.playerId),
      };
      context.selectedResources = [...(context.selectedResources || []), resource];
      taskState.yearOfPlenty[playerInfo.playerId] = context;
      if (previousPicksRemaining === 1 || context.selectedResources.length >= 2) {
        benchmarkTaskPayload = buildYearOfPlentyChoiceTaskPayload(game, playerInfo.playerId, context.selectedResources.slice(0, 2));
        delete taskState.yearOfPlenty[playerInfo.playerId];
      }
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'yearOfPlentyPick', { resource }, result, requestStartedAt, benchmarkTaskPayload);
    callback(result);
  });
  
  // --------------------------------------------------------------------
  // TRADING
  // --------------------------------------------------------------------
  
  /** Trade with the bank (uses port ratios if available) */
  socket.on('bankTrade', async ({ giveResource, giveAmount, getResource }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? buildBankTradeTaskPayload(game, playerInfo.playerId, giveResource, giveAmount, getResource)
      : null;
    const result = GameLogic.bankTrade(game, playerInfo.playerId, giveResource, giveAmount, getResource);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'bankTrade',
      { giveResource, giveAmount, getResource },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    callback(result);
  });
  
  /** Propose a trade to other players (optional targetPlayerId = propose to one player) */
  socket.on('proposeTrade', async ({ offer, request, targetPlayerId }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskContext = game?.benchmark?.enabled
      ? buildTradeProposalTaskPayload(game, playerInfo.playerId, offer, request, targetPlayerId || null)
      : null;
    const benchmarkTaskPayload = benchmarkTaskContext
      ? [
        {
          taskId: benchmarkTaskContext.taskId,
          selectedOptionId: benchmarkTaskContext.selectedOptionId,
          evaluationContext: benchmarkTaskContext.evaluationContext,
          offerContext: benchmarkTaskContext.offerContext,
        },
        benchmarkTaskContext.partnerContext,
      ].filter(Boolean)
      : null;
    const result = GameLogic.proposeTrade(game, playerInfo.playerId, offer, request, targetPlayerId || null);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'tradeProposed', {
        from: playerInfo.playerId,
        offer,
        request
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'proposeTrade',
      { offer, request, targetPlayerId },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    callback(result);
  });
  
  /** Accept or decline a trade offer */
  socket.on('respondToTrade', async ({ accept }, callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? buildTradeResponseTaskPayload(game, playerInfo.playerId, accept)
      : null;
    const result = GameLogic.respondToTrade(game, playerInfo.playerId, accept);
    
    if (result.success) {
      if (result.traded) {
        broadcastToGame(playerInfo.gameId, 'tradeAccepted', { 
          by: playerInfo.playerId 
        });
      } else {
        broadcastToGame(playerInfo.gameId, 'tradeDeclined', { 
          by: playerInfo.playerId 
        });
      }
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(
      game,
      playerInfo.playerId,
      'respondToTrade',
      { accept },
      result,
      requestStartedAt,
      benchmarkTaskPayload
    );
    callback(result);
  });
  
  /** Cancel an active trade offer */
  socket.on('cancelTrade', async (callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const result = GameLogic.cancelTrade(game, playerInfo.playerId);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'tradeCancelled', {});
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'cancelTrade', {}, result, requestStartedAt);
    callback(result);
  });
  
  /** Counter-offer: recipient proposes a different trade back to the proposer */
  socket.on('counterTrade', async (payload, callback) => {
    if (typeof callback !== 'function') return;
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    const { offer, request } = payload || {};
    if (!offer || typeof offer !== 'object' || !request || typeof request !== 'object') {
      callback({ success: false, error: 'Invalid offer or request' });
      return;
    }
    try {
      const game = games.get(playerInfo.gameId);
      const benchmarkTaskPayload = game?.benchmark?.enabled
        ? buildCounterTradeTaskPayload(game, playerInfo.playerId, offer, request)
        : null;
      const result = GameLogic.counterTrade(game, playerInfo.playerId, offer, request);
      if (result.success) {
        broadcastToGame(playerInfo.gameId, 'tradeProposed', {
          from: playerInfo.playerId,
          offer,
          request
        });
        broadcastGameState(playerInfo.gameId);
      }
      await recordBenchmarkAction(game, playerInfo.playerId, 'counterTrade', payload, result, requestStartedAt, benchmarkTaskPayload);
      callback(result);
    } catch (err) {
      console.error('counterTrade error:', err);
      callback({ success: false, error: err.message || 'Counter offer failed' });
    }
  });
  
  // --------------------------------------------------------------------
  // TURN MANAGEMENT
  // --------------------------------------------------------------------
  
  /** Advance to next player during setup phase */
  socket.on('advanceSetup', async (callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const result = GameLogic.advanceSetup(game, playerInfo.playerId);
    
    if (result.success) {
      if (game.benchmark?.enabled && game.phase === 'playing') {
        maybeStartBenchmarkTurn(game);
      }
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'advanceSetup', {}, result, requestStartedAt);
    callback(result);
  });
  
  /** End the current player's turn */
  socket.on('endTurn', async (callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const player = game?.players?.find(candidate => candidate.id === playerInfo.playerId);
    const canAffordDevCard = Boolean(player && hasRequiredResources(player, PLAN_COSTS.devCard) && ((Array.isArray(game?.devDeck) ? game.devDeck.length > 0 : true)));
    const canHoldKnightDecision = Boolean(player && !game?.devCardPlayedThisTurn && player.developmentCards?.includes('knight'));
    const canHoldRoadBuildingDecision = Boolean(player && !game?.devCardPlayedThisTurn && player.developmentCards?.includes('roadBuilding'));
    const shouldRecordBuildSave = game?.benchmark?.enabled && !hasTurnDecisionRecorded(game, playerInfo.playerId, 'buildVsSaveRecorded');
    const shouldRecordDevPurchase = game?.benchmark?.enabled && canAffordDevCard && !hasTurnDecisionRecorded(game, playerInfo.playerId, 'devPurchaseRecorded');
    const shouldRecordKnightHold = game?.benchmark?.enabled && canHoldKnightDecision && !hasTurnDecisionRecorded(game, playerInfo.playerId, 'knightRecorded');
    const shouldRecordRoadBuildingHold = game?.benchmark?.enabled && canHoldRoadBuildingDecision && !hasTurnDecisionRecorded(game, playerInfo.playerId, 'roadBuildingRecorded');
    const shouldRecordLongestRoadDefer = game?.benchmark?.enabled && shouldConsiderLongestRoadRace(game, playerInfo.playerId) && !hasTurnDecisionRecorded(game, playerInfo.playerId, 'longestRoadRecorded');
    const shouldRecordLargestArmyDefer = game?.benchmark?.enabled && shouldConsiderLargestArmyRace(game, playerInfo.playerId) && !hasTurnDecisionRecorded(game, playerInfo.playerId, 'largestArmyRecorded');
    const benchmarkTaskPayload = game?.benchmark?.enabled
      ? [
        shouldRecordBuildSave ? buildBuildVsSaveTaskPayload(game, playerInfo.playerId, 'save') : null,
        shouldRecordDevPurchase ? buildDevelopmentCardPurchaseTaskPayload(game, playerInfo.playerId, 'skip-dev-card') : null,
        shouldRecordKnightHold ? buildKnightDecisionTaskPayload(game, playerInfo.playerId, 'hold') : null,
        shouldRecordRoadBuildingHold ? buildRoadBuildingDecisionTaskPayload(game, playerInfo.playerId, 'hold') : null,
      ].filter(Boolean)
      : null;
    const result = GameLogic.endTurn(game, playerInfo.playerId);
    
    if (result.success) {
      if (result.specialBuildingPhase) {
        broadcastToGame(playerInfo.gameId, 'specialBuildingPhaseStarted', { 
          playerId: playerInfo.playerId,
          currentBuilder: game.players[game.specialBuildIndex]?.id
        });
      } else {
        broadcastToGame(playerInfo.gameId, 'turnEnded', { playerId: playerInfo.playerId });
      }
      maybeStartBenchmarkTurn(game);
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'endTurn', {}, result, requestStartedAt, benchmarkTaskPayload);
    if (result.success && game?.benchmark?.enabled) {
      if (shouldRecordBuildSave) {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'buildVsSaveRecorded');
      }
      if (shouldRecordDevPurchase) {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'devPurchaseRecorded');
      }
      if (shouldRecordKnightHold) {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'knightRecorded');
      }
      if (shouldRecordRoadBuildingHold) {
        markTurnDecisionRecorded(game, playerInfo.playerId, 'roadBuildingRecorded');
      }
      if (shouldRecordLongestRoadDefer) {
        await maybeCreatePendingLongTermEpisode(game, playerInfo.playerId, 'decide-pursue-longest-road', 'defer');
        markTurnDecisionRecorded(game, playerInfo.playerId, 'longestRoadRecorded');
      }
      if (shouldRecordLargestArmyDefer) {
        await maybeCreatePendingLongTermEpisode(game, playerInfo.playerId, 'decide-pursue-largest-army', 'defer');
        markTurnDecisionRecorded(game, playerInfo.playerId, 'largestArmyRecorded');
      }
    }
    callback(result);
  });
  
  /** End special building phase for current player (5-6 player games) */
  socket.on('endSpecialBuild', async (callback) => {
    const requestStartedAt = Date.now();
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const result = GameLogic.endSpecialBuild(game, playerInfo.playerId);
    
    if (result.success) {
      if (result.specialBuildingPhaseEnded) {
        broadcastToGame(playerInfo.gameId, 'specialBuildingPhaseEnded', {});
        broadcastToGame(playerInfo.gameId, 'turnEnded', { playerId: playerInfo.playerId });
      } else {
        broadcastToGame(playerInfo.gameId, 'specialBuildNext', { 
          currentBuilder: game.players[game.specialBuildIndex]?.id 
        });
      }
      maybeStartBenchmarkTurn(game);
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'endSpecialBuild', {}, result, requestStartedAt);
    callback(result);
  });
  
  // --------------------------------------------------------------------
  // UTILITY QUERIES
  // --------------------------------------------------------------------
  
  /** Get list of players with buildings on a hex (for robber stealing) */
  socket.on('getPlayersOnHex', ({ hexKey }, callback) => {
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) {
      callback({ success: false, error: 'Not in a game' });
      return;
    }
    
    const game = games.get(playerInfo.gameId);
    const playerIdx = game.players.findIndex(p => p.id === playerInfo.playerId);
    const playerIndices = GameLogic.getPlayersOnHex(game, hexKey, playerIdx);
    
    const players = playerIndices.map(idx => ({
      id: game.players[idx].id,
      name: game.players[idx].name,
      hasResources: Object.values(game.players[idx].resources).reduce((a, b) => a + b, 0) > 0
    }));
    
    callback({ success: true, players });
  });
  
  // --------------------------------------------------------------------
  // CHAT
  // --------------------------------------------------------------------
  
  /** Broadcast a chat message to all players in the game */
  socket.on('chatMessage', ({ message }) => {
    const playerInfo = playerSockets.get(socket.id);
    if (!playerInfo) return;
    
    const game = games.get(playerInfo.gameId);
    if (!game) return;
    
    const player = game.players.find(p => p.id === playerInfo.playerId);
    if (!player) return;
    
    broadcastToGame(playerInfo.gameId, 'chatMessage', {
      playerName: player.name,
      playerColor: player.color,
      message,
      timestamp: Date.now()
    });
  });
  
  // --------------------------------------------------------------------
  // CONNECTION LIFECYCLE
  // --------------------------------------------------------------------
  
  /** Handle player disconnection */
  socket.on('disconnect', () => {
    totalConnectedPlayers = Math.max(0, totalConnectedPlayers - 1);
    
    const playerInfo = playerSockets.get(socket.id);
    
    if (playerInfo) {
      const game = games.get(playerInfo.gameId);
      if (game) {
        const player = game.players.find(p => p.id === playerInfo.playerId);
        if (player) {
          broadcastToGame(playerInfo.gameId, 'playerDisconnected', { 
            playerName: player.name 
          });
        }
      }
      
      playerSockets.delete(socket.id);
    }
    
    console.log(`Player disconnected: ${socket.id} (Total: ${totalConnectedPlayers}/${MAX_TOTAL_PLAYERS})`);
  });
  
  /** Reconnect to an existing game (e.g., after page refresh) */
  socket.on('reconnect', ({ gameCode, playerId }, callback) => {
    const game = games.get(gameCode);
    
    if (!game) {
      callback({ success: false, error: 'Game not found' });
      return;
    }
    
    const player = game.players.find(p => p.id === playerId);
    if (!player) {
      callback({ success: false, error: 'Player not found in game' });
      return;
    }
    
    // Remove old socket mapping if exists
    for (const [socketId, info] of playerSockets.entries()) {
      if (info.gameId === gameCode && info.playerId === playerId) {
        playerSockets.delete(socketId);
        break;
      }
    }
    
    playerSockets.set(socket.id, { gameId: gameCode, playerId });
    socket.join(gameCode);
    
    callback({
      success: true,
      gameState: GameLogic.getPlayerView(game, playerId)
    });
    
    broadcastToGame(gameCode, 'playerReconnected', { playerName: player.name });
  });
});

// ============================================================================
// SERVER STARTUP
// ============================================================================

const PORT = process.env.PORT || 3001;
httpServer.listen(PORT, () => {
  console.log(`Catan server running on port ${PORT}`);
});

