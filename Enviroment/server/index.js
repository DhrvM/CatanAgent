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

function scoreRoadPlacement(game, edgeKeyValue, lastSettlement) {
  const endpoints = getEdgeVerticesFromKey(edgeKeyValue);
  const forwardVertex = endpoints.find(vKey => !GameLogic.areVerticesEqual(vKey, lastSettlement)) || endpoints[0] || null;
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
    blockingValue: 0,
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

async function recordBenchmarkTaskFromAction(game, playerId, benchmarkTaskPayload, timing = {}) {
  if (!game?.benchmark?.enabled) return null;
  if (!benchmarkTaskPayload) return null;

  const player = game.players.find(candidate => candidate.id === playerId);
  if (!player) return null;

  if (benchmarkTaskPayload.taskId === 'settlement-location-selection' && benchmarkTaskPayload.selectedOptionId) {
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
      latencyMs: timing.decisionTimeMs ?? null,
      response: {
        selectedOptionId: benchmarkTaskPayload.selectedOptionId,
      },
      evaluationContext: benchmarkTaskPayload.evaluationContext,
    });
  }

  if (benchmarkTaskPayload.taskId === 'road-placement-direction' && benchmarkTaskPayload.selectedOptionId) {
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
      latencyMs: timing.decisionTimeMs ?? null,
      response: {
        selectedOptionId: benchmarkTaskPayload.selectedOptionId,
      },
      evaluationContext: benchmarkTaskPayload.evaluationContext,
    });
  }

  return null;
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
    const result = GameLogic.discardCards(game, playerInfo.playerId, resources);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'discardCards', { resources }, result, requestStartedAt);
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
    await recordBenchmarkAction(game, playerInfo.playerId, 'moveRobber', { hexKey, stealFromPlayerId }, result, requestStartedAt);
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
    const benchmarkTaskPayload = game?.benchmark?.enabled && isSetup
      ? {
        taskId: 'settlement-location-selection',
        selectedOptionId: vertexKey,
        evaluationContext: buildSettlementEvaluationContext(game, playerInfo.playerId),
      }
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
    const benchmarkTaskPayload = game?.benchmark?.enabled && isSetup && lastSettlement
      ? {
        taskId: 'road-placement-direction',
        selectedOptionId: edgeKey,
        evaluationContext: buildRoadEvaluationContext(game, playerInfo.playerId, lastSettlement),
      }
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
    const result = GameLogic.upgradeToCity(game, playerInfo.playerId, vertexKey);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'cityBuilt', { 
        vertexKey, 
        playerId: playerInfo.playerId 
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'upgradeToCity', { vertexKey }, result, requestStartedAt);
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
    const result = GameLogic.buyDevCard(game, playerInfo.playerId);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'buyDevCard', {}, result, requestStartedAt);
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
    const result = GameLogic.playDevCard(game, playerInfo.playerId, cardType, params);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'devCardPlayed', { 
        cardType, 
        playerId: playerInfo.playerId 
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'playDevCard', { cardType, params }, result, requestStartedAt);
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
    const result = GameLogic.yearOfPlentyPick(game, playerInfo.playerId, resource);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'yearOfPlentyPick', { resource }, result, requestStartedAt);
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
    const result = GameLogic.bankTrade(game, playerInfo.playerId, giveResource, giveAmount, getResource);
    
    if (result.success) {
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'bankTrade', { giveResource, giveAmount, getResource }, result, requestStartedAt);
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
    const result = GameLogic.proposeTrade(game, playerInfo.playerId, offer, request, targetPlayerId || null);
    
    if (result.success) {
      broadcastToGame(playerInfo.gameId, 'tradeProposed', {
        from: playerInfo.playerId,
        offer,
        request
      });
      broadcastGameState(playerInfo.gameId);
    }
    await recordBenchmarkAction(game, playerInfo.playerId, 'proposeTrade', { offer, request, targetPlayerId }, result, requestStartedAt);
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
    await recordBenchmarkAction(game, playerInfo.playerId, 'respondToTrade', { accept }, result, requestStartedAt);
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
      const result = GameLogic.counterTrade(game, playerInfo.playerId, offer, request);
      if (result.success) {
        broadcastToGame(playerInfo.gameId, 'tradeProposed', {
          from: playerInfo.playerId,
          offer,
          request
        });
        broadcastGameState(playerInfo.gameId);
      }
      await recordBenchmarkAction(game, playerInfo.playerId, 'counterTrade', payload, result, requestStartedAt);
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
    await recordBenchmarkAction(game, playerInfo.playerId, 'endTurn', {}, result, requestStartedAt);
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

