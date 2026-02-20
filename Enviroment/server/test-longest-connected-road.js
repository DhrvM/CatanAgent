// Strongest tests for longest connected road logic (path-based, not count-based)
import * as GameLogic from './gameLogic.js';

const COLORS = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  cyan: '\x1b[36m',
  magenta: '\x1b[35m'
};

function log(msg, color = 'reset') {
  console.log(`${COLORS[color]}${msg}${COLORS.reset}`);
}
function assert(condition, message) {
  if (condition) {
    log(`  ✓ ${message}`, 'green');
    return true;
  } else {
    log(`  ✗ ${message}`, 'red');
    return false;
  }
}
function logSection(title) {
  console.log('\n' + '='.repeat(60));
  log(title, 'cyan');
  console.log('='.repeat(60));
}
function logSubSection(title) {
  console.log('\n' + '-'.repeat(50));
  log(title, 'magenta');
  console.log('-'.repeat(50));
}

function createTestGame() {
  const game = GameLogic.createGame('test', { id: 'p1', name: 'Player1' }, false, false);
  GameLogic.addPlayer(game, { id: 'p2', name: 'Player2' });
  GameLogic.addPlayer(game, { id: 'p3', name: 'Player3' });
  GameLogic.addPlayer(game, { id: 'p4', name: 'Player4' });
  GameLogic.startGame(game);
  game.phase = 'playing';
  game.turnPhase = 'main';
  return game;
}

// ---- Helpers that match the engine’s graph model ----

// Engine uses edge key (e_q_r_dir) to derive endpoints.
// This matches getEdgeVertices(q,r,dir) in gameLogic.js.
function edgeEndpoints(eKey) {
  const m = eKey.match(/e_(-?\d+)_(-?\d+)_(\d+)/);
  if (!m) throw new Error(`Bad edge key: ${eKey}`);
  const q = parseInt(m[1], 10);
  const r = parseInt(m[2], 10);
  const dir = parseInt(m[3], 10);

  // dir mapping in gameLogic.js:
  // 0: v0-v1, 1: v1-v2, 2: v2-v3, 3: v3-v4, 4: v4-v5, 5: v5-v0
  const vA = GameLogic.vertexKey(q, r, dir);
  const vB = GameLogic.vertexKey(q, r, (dir + 1) % 6);
  return [vA, vB];
}

function placeRoadDirect(game, playerIndex, eKey) {
  if (!game.edges[eKey]) throw new Error(`Edge does not exist in board: ${eKey}`);
  game.edges[eKey].road = true;
  game.edges[eKey].owner = playerIndex;
  game.players[playerIndex].roads--;
}

function placeSettlementDirect(game, playerIndex, vKey) {
  if (!game.vertices[vKey]) throw new Error(`Vertex does not exist in board: ${vKey}`);
  game.vertices[vKey].building = 'settlement';
  game.vertices[vKey].owner = playerIndex;
  game.players[playerIndex].settlements--;
  game.players[playerIndex].victoryPoints++;
}

// Find a SIMPLE PATH of length N edges by walking vertex adjacency via getVertexEdges,
// while avoiding reused edges. Returns { edges: [...], vertices: [...] } where
// vertices length = edges length + 1 in order.
function findSimplePath(game, n, forbiddenVertices = new Set()) {
  const allEdges = Object.keys(game.edges);

  // Try multiple starts (board is small, brute-force is fine for tests)
  for (const startEdge of allEdges) {
    const [a, b] = edgeEndpoints(startEdge);
    if (forbiddenVertices.has(a) || forbiddenVertices.has(b)) continue;

    // Try both directions as path start
    const attempts = [
      { startVertex: a, nextVertex: b },
      { startVertex: b, nextVertex: a }
    ];

    for (const { startVertex, nextVertex } of attempts) {
      const usedEdges = new Set([startEdge]);
      const pathEdges = [startEdge];
      const pathVertices = [startVertex, nextVertex];

      // Extend path by repeatedly choosing an unused adjacent edge at current end vertex
      while (pathEdges.length < n) {
        const currentVertex = pathVertices[pathVertices.length - 1];
        if (forbiddenVertices.has(currentVertex)) break;

        const candidates = GameLogic.getVertexEdges(currentVertex)
          .filter(e => game.edges[e]) // must be a valid edge on this board
          .filter(e => !usedEdges.has(e));

        let extended = false;
        for (const e of candidates) {
          // ensure it continues the chain (shares currentVertex as an endpoint)
          const [x, y] = edgeEndpoints(e);
          if (x !== currentVertex && y !== currentVertex) continue;

          const otherVertex = (x === currentVertex) ? y : x;
          if (forbiddenVertices.has(otherVertex)) continue;

          usedEdges.add(e);
          pathEdges.push(e);
          pathVertices.push(otherVertex);
          extended = true;
          break;
        }

        if (!extended) break;
      }

      if (pathEdges.length === n) {
        return { edges: pathEdges, vertices: pathVertices };
      }
    }
  }

  throw new Error(`Could not find a simple path of length ${n} on this board`);
}

// Choose a vertex that is guaranteed to be an internal “connector” in the path
// (i.e., not an endpoint), so an opponent settlement will cut it.
function middleVertexOfPath(pathVertices) {
  if (pathVertices.length < 3) throw new Error('Need at least 2 edges to have an internal vertex');
  return pathVertices[Math.floor(pathVertices.length / 2)];
}

// Pick a path for player1 that is unlikely to overlap player0’s path by forbidding vertices.
function pickDisjointPath(game, n, takenVertices) {
  return findSimplePath(game, n, new Set(takenVertices));
}

logSection('LONGEST CONNECTED ROAD LOGIC (PATH-BASED)');

logSubSection('Connected road beats previous holder only if > previous length (ties do NOT steal)');
{
  const game = createTestGame();

  // Player 0: build exactly 5-edge path
  const p0 = findSimplePath(game, 5);
  // Put a settlement somewhere valid (not required by longest road calc, but mirrors gameplay)
  placeSettlementDirect(game, 0, p0.vertices[0]);
  for (const e of p0.edges) placeRoadDirect(game, 0, e);

  GameLogic.updateLongestRoad(game);
  assert(game.longestRoadPlayer === 0, 'Player 0 gets longest road with a 5-edge connected path');
  assert(game.players[0].roadLength === 5, 'Player 0 roadLength is exactly 5');

  // Player 1: build a DISJOINT 5-edge path (tie)
  const p1_5 = pickDisjointPath(game, 5, p0.vertices);
  placeSettlementDirect(game, 1, p1_5.vertices[0]);
  for (const e of p1_5.edges) placeRoadDirect(game, 1, e);

  GameLogic.updateLongestRoad(game);
  assert(game.longestRoadPlayer === 0, 'Tie at 5 does NOT steal longest road from Player 0');
  assert(game.players[1].hasLongestRoad === false, 'Player 1 does not get hasLongestRoad on tie');

  // Extend Player 1 to 6 by adding one more adjacent edge continuing their path.
  const forbidden = new Set(p0.vertices);
  const p1_6 = pickDisjointPath(game, 6, forbidden);
  // Ensure we only add the missing edge(s) beyond the 5 we already placed.
  // Easiest: place all edges again (idempotent for road=true, but we won’t double-decrement pieces).
  // Instead, only place edges that are not yet owned/road.
  for (const e of p1_6.edges) {
    if (!game.edges[e].road) placeRoadDirect(game, 1, e);
    else if (game.edges[e].owner !== 1) throw new Error(`Edge overlap / ownership conflict: ${e}`);
  }

  GameLogic.updateLongestRoad(game);
  assert(game.players[1].roadLength === 6, 'Player 1 roadLength is 6 after extending');
  assert(game.longestRoadPlayer === 1, 'Player 1 takes longest road with a 6-edge connected path');
  assert(game.players[1].hasLongestRoad === true, 'Player 1 hasLongestRoad flag set');
  assert(game.players[0].hasLongestRoad === false, 'Player 0 lost hasLongestRoad flag');
}

logSubSection('Disconnected roads do not sum: only the longest connected segment counts');
{
  const game = createTestGame();

  // Build a 3-edge path, then a separate 2-edge disjoint path for the SAME player
  const segA = findSimplePath(game, 3);
  const segB = pickDisjointPath(game, 2, segA.vertices);

  placeSettlementDirect(game, 0, segA.vertices[0]);

  for (const e of segA.edges) placeRoadDirect(game, 0, e);
  for (const e of segB.edges) placeRoadDirect(game, 0, e);

  GameLogic.updateLongestRoad(game);
  assert(game.players[0].roadLength === 3, 'roadLength is the longest connected segment (3), not 3+2');
  assert(game.longestRoadPlayer === null, 'No longest road awarded when best connected length < 5');
}

logSubSection('Road cut by opponent settlement reduces connected length (and removes award if it drops)');
{
  const game = createTestGame();

  // Player 0: build a 6-edge simple path so cutting can drop it below 5
  const p0 = findSimplePath(game, 6);
  placeSettlementDirect(game, 0, p0.vertices[0]);
  for (const e of p0.edges) placeRoadDirect(game, 0, e);

  GameLogic.updateLongestRoad(game);
  assert(game.players[0].roadLength === 6, 'Player 0 roadLength starts at 6');
  assert(game.longestRoadPlayer === 0, 'Player 0 initially holds longest road at 6');

  // Opponent places settlement at an INTERNAL vertex of the path -> should cut into two smaller segments
  const cutV = middleVertexOfPath(p0.vertices);
  placeSettlementDirect(game, 1, cutV);

  GameLogic.updateLongestRoad(game);
  assert(game.players[0].roadLength < 6, 'Player 0 roadLength decreases after opponent settlement cuts continuity');

  // Your updateLongestRoad() removes the award if the previous holder’s road is cut shorter than prevLength
  // (even if still >=5). This assertion matches your current implementation.
  assert(game.longestRoadPlayer === null, 'Longest road is removed after cut (per current implementation)');
  assert(game.players[0].hasLongestRoad === false, 'Previous holder hasLongestRoad cleared');
}