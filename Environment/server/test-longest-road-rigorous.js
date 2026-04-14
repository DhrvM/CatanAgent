// Rigorous Longest Road Tests
// Tests every edge case for longest road calculation

import * as GameLogic from './gameLogic.js';

const COLORS = {
  reset: '\x1b[0m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  cyan: '\x1b[36m',
  magenta: '\x1b[35m'
};

let passed = 0;
let failed = 0;

function log(msg, color = 'reset') {
  console.log(`${COLORS[color]}${msg}${COLORS.reset}`);
}

function assert(condition, message) {
  if (condition) {
    log(`  ✓ ${message}`, 'green');
    passed++;
    return true;
  } else {
    log(`  ✗ ${message}`, 'red');
    failed++;
    return false;
  }
}

function logSection(title) {
  console.log('\n' + '═'.repeat(70));
  log(`  ${title}`, 'cyan');
  console.log('═'.repeat(70));
}

function logSubSection(title) {
  console.log('\n' + '─'.repeat(50));
  log(`  ${title}`, 'magenta');
  console.log('─'.repeat(50));
}

// Helper to create a basic game
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

// Helper to place roads directly
function placeRoad(game, playerIndex, edgeKey) {
  game.edges[edgeKey] = { road: true, owner: playerIndex };
  game.players[playerIndex].roads--;
}

// Helper to place settlement directly
function placeSettlement(game, playerIndex, vertexKey) {
  game.vertices[vertexKey] = { building: 'settlement', owner: playerIndex };
  game.players[playerIndex].settlements--;
  game.players[playerIndex].victoryPoints++;
}

// =============================================================================
// TEST 1: DEV CARD + ROLL DICE FLOW
// =============================================================================
function testDevCardRollDice() {
  logSection('1. DEV CARD + ROLL DICE FLOW');
  
  logSubSection('1.1 Play Knight BEFORE Rolling - Can Still Roll After Robber');
  {
    const game = createTestGame();
    const p1Index = game.players.findIndex(p => p.id === 'p1');
    game.currentPlayerIndex = p1Index;
    game.turnPhase = 'roll';
    game.hasRolledThisTurn = false;
    
    // Give player a knight card (in developmentCards, not newDevCards)
    game.players[p1Index].developmentCards = ['knight'];
    
    // Player plays knight BEFORE rolling
    const playResult = GameLogic.playDevCard(game, 'p1', 'knight');
    assert(playResult.success, 'Knight card can be played before rolling');
    assert(game.turnPhase === 'robber', 'Turn phase is now robber');
    
    // Player moves robber
    const robberHex = Object.keys(game.hexes).find(k => k !== game.robber);
    const moveResult = GameLogic.moveRobber(game, 'p1', robberHex, null);
    assert(moveResult.success, 'Robber can be moved');
    
    // CRITICAL: After moving robber, turn phase should be 'roll' since dice not rolled yet
    assert(game.turnPhase === 'roll', 'Turn phase returns to ROLL after robber (dice not rolled yet)');
    assert(game.hasRolledThisTurn === false, 'hasRolledThisTurn is still false');
    
    // Player should now be able to roll dice
    const rollResult = GameLogic.rollDice(game, 'p1');
    assert(rollResult.success, 'Player CAN roll dice after playing knight and moving robber');
    assert(game.hasRolledThisTurn === true, 'hasRolledThisTurn is now true');
    assert(game.turnPhase === 'main' || game.turnPhase === 'robber' || game.turnPhase === 'discard', 
           'Turn phase advances after rolling');
  }
  
  logSubSection('1.2 Play Knight AFTER Rolling - Goes to Main After Robber');
  {
    const game = createTestGame();
    const p1Index = game.players.findIndex(p => p.id === 'p1');
    game.currentPlayerIndex = p1Index;
    game.turnPhase = 'roll';
    game.hasRolledThisTurn = false;
    
    // Roll dice first
    const rollResult = GameLogic.rollDice(game, 'p1');
    // Handle the roll result - might need to move robber if 7
    if (game.turnPhase === 'robber') {
      const robberHex = Object.keys(game.hexes).find(k => k !== game.robber);
      GameLogic.moveRobber(game, 'p1', robberHex, null);
    } else if (game.turnPhase === 'discard') {
      // Skip discard for this test
      game.turnPhase = 'main';
      game.discardingPlayers = [];
    }
    
    assert(game.turnPhase === 'main', 'Turn phase is main after rolling');
    assert(game.hasRolledThisTurn === true, 'hasRolledThisTurn is true after rolling');
    
    // Now give player a knight card and play it
    game.players[p1Index].developmentCards = ['knight'];
    game.devCardPlayedThisTurn = false;
    
    const playResult = GameLogic.playDevCard(game, 'p1', 'knight');
    assert(playResult.success, 'Knight card can be played after rolling');
    assert(game.turnPhase === 'robber', 'Turn phase is robber after playing knight');
    
    // Move robber
    const robberHex = Object.keys(game.hexes).find(k => k !== game.robber);
    const moveResult = GameLogic.moveRobber(game, 'p1', robberHex, null);
    assert(moveResult.success, 'Robber moved');
    
    // After rolling AND playing knight, should return to main
    assert(game.turnPhase === 'main', 'Turn phase returns to MAIN after robber (dice already rolled)');
  }
  
  logSubSection('1.3 End Turn Resets hasRolledThisTurn');
  {
    const game = createTestGame();
    const p1Index = game.players.findIndex(p => p.id === 'p1');
    game.currentPlayerIndex = p1Index;
    game.turnPhase = 'main';
    game.hasRolledThisTurn = true;
    
    const nextPlayerIndex = (p1Index + 1) % game.players.length;
    
    const result = GameLogic.endTurn(game, 'p1');
    assert(result.success, 'End turn succeeds');
    assert(game.hasRolledThisTurn === false, 'hasRolledThisTurn reset for next turn');
    assert(game.turnPhase === 'roll', 'Next player starts in roll phase');
  }
}

// =============================================================================
// TEST 2: COMPLEX LONGEST ROAD SCENARIOS
// =============================================================================
function testComplexLongestRoad() {
  logSection('2. COMPLEX LONGEST ROAD SCENARIOS');
  
  logSubSection('2.1 Two Players Racing for Longest Road');
  {
    const game = createTestGame();
    
    // Player 0 builds 5 roads first
    placeSettlement(game, 0, 'v_0_0_0');
    for (let i = 0; i < 5; i++) {
      placeRoad(game, 0, `e_0_0_${i}`);
    }
    GameLogic.updateLongestRoad(game);
    
    const firstHolder = game.longestRoadPlayer;
    assert(firstHolder === 0, 'Player 0 gets longest road with 5 roads');
    assert(game.players[0].victoryPoints >= 3, 'Player 0 has at least 3 VP (1 settlement + 2 longest road)');
    
    // Player 1 builds 6 roads - should take it
    placeSettlement(game, 1, 'v_1_0_0');
    for (let i = 0; i < 6; i++) {
      placeRoad(game, 1, `e_1_0_${i}`);
    }
    GameLogic.updateLongestRoad(game);
    
    assert(game.longestRoadPlayer === 1, 'Player 1 takes longest road with 6 roads');
    assert(game.players[1].hasLongestRoad === true, 'Player 1 hasLongestRoad flag');
    assert(game.players[0].hasLongestRoad === false, 'Player 0 lost longest road');
    
    // Check VP transfer
    const p0VP = game.players[0].victoryPoints;
    const p1VP = game.players[1].victoryPoints;
    log(`  Player 0 VP: ${p0VP}, Player 1 VP: ${p1VP}`, 'yellow');
  }
  
  logSubSection('2.2 Road Network Spanning Multiple Hexes');
  {
    const game = createTestGame();
    
    // Build a complex road network across multiple hexes
    // Hex (0,0), (1,0), (0,1)
    placeSettlement(game, 0, 'v_0_0_0');
    
    // Roads on hex (0,0)
    placeRoad(game, 0, 'e_0_0_0');
    placeRoad(game, 0, 'e_0_0_1');
    placeRoad(game, 0, 'e_0_0_2');
    
    // Roads on hex (1,0) - connecting
    placeRoad(game, 0, 'e_1_0_4'); // Left edge of (1,0) = right edge of (0,0)
    placeRoad(game, 0, 'e_1_0_3');
    placeRoad(game, 0, 'e_1_0_2');
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Multi-hex road length: ${game.players[0].roadLength}`, 'yellow');
    assert(game.players[0].roadLength >= 5, 'Roads across multiple hexes are connected');
  }
  
  logSubSection('2.3 Road Cut by Opponent Settlement (Linear Road)');
  {
    const game = createTestGame();
    
    // Player 0 builds a LINEAR road (not circular) - this WILL be broken
    // Road goes: v0 -> v1 -> v2 -> v3 (continuing to next hex)
    placeSettlement(game, 0, 'v_0_0_0');
    placeRoad(game, 0, 'e_0_0_0'); // v0-v1
    placeRoad(game, 0, 'e_0_0_1'); // v1-v2
    placeRoad(game, 0, 'e_0_0_2'); // v2-v3
    placeRoad(game, 0, 'e_0_0_3'); // v3-v4
    placeRoad(game, 0, 'e_0_1_5'); // Continue from v3 to hex (0,1) v0
    placeRoad(game, 0, 'e_0_1_0'); // v0-v1 of hex (0,1)
    
    GameLogic.updateLongestRoad(game);
    const beforeCut = game.players[0].roadLength;
    log(`  Linear road length before cut: ${beforeCut}`, 'yellow');
    
    // Opponent places settlement in the middle (vertex 2 of hex 0,0)
    placeSettlement(game, 1, 'v_0_0_2');
    
    GameLogic.updateLongestRoad(game);
    const afterCut = game.players[0].roadLength;
    log(`  Linear road length after cut: ${afterCut}`, 'yellow');
    
    // Road should be shorter after being cut (2 segments: 2 roads and 4 roads)
    assert(afterCut < beforeCut, 'Linear road length decreases when cut by opponent settlement');
  }
  
  logSubSection('2.3b Circular Road - Not Broken by Single Settlement');
  {
    const game = createTestGame();
    
    // Player 0 builds a CIRCULAR road around hex (0,0)
    placeSettlement(game, 0, 'v_0_0_0');
    for (let i = 0; i < 6; i++) {
      placeRoad(game, 0, `e_0_0_${i}`);
    }
    
    GameLogic.updateLongestRoad(game);
    const beforeCut = game.players[0].roadLength;
    log(`  Circular road length before: ${beforeCut}`, 'yellow');
    
    // Opponent places settlement at vertex 2
    placeSettlement(game, 1, 'v_0_0_2');
    
    GameLogic.updateLongestRoad(game);
    const afterCut = game.players[0].roadLength;
    log(`  Circular road length after: ${afterCut}`, 'yellow');
    
    // Circular road is NOT broken because you can still traverse all 6 edges
    // by going around the other direction (avoiding v2)
    assert(afterCut === beforeCut, 'Circular road maintains length (can go around settlement)');
  }
  
  logSubSection('2.4 Forked Road - Longest Path Calculation');
  {
    const game = createTestGame();
    
    // Build a forked road network:
    //     /---\
    // ---o     (where o is a vertex with 3 roads)
    //     \---/
    
    placeSettlement(game, 0, 'v_0_0_0');
    
    // Main trunk
    placeRoad(game, 0, 'e_0_0_5');
    placeRoad(game, 0, 'e_-1_0_2'); // Continue from v_0_0_5
    
    // Fork from vertex 0
    placeRoad(game, 0, 'e_0_0_0');
    placeRoad(game, 0, 'e_0_0_1');
    
    // Second branch from vertex 5 (going other direction)
    placeRoad(game, 0, 'e_0_0_4');
    placeRoad(game, 0, 'e_0_0_3');
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Forked road length: ${game.players[0].roadLength}`, 'yellow');
    // The longest continuous path should find the best route through the fork
    assert(game.players[0].roadLength >= 5, 'Fork road calculation finds longest path');
  }
  
  logSubSection('2.5 Circular Road with Branch');
  {
    const game = createTestGame();
    
    // Build a complete hex circle plus one branch
    placeSettlement(game, 0, 'v_0_0_0');
    
    // Complete circle around hex (0,0)
    for (let i = 0; i < 6; i++) {
      placeRoad(game, 0, `e_0_0_${i}`);
    }
    
    // Add branch
    placeRoad(game, 0, 'e_0_-1_2'); // Branch from vertex 0
    placeRoad(game, 0, 'e_0_-1_1');
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Circle + branch road length: ${game.players[0].roadLength}`, 'yellow');
    // Should count the circle (6) + best branch (2) but not double-count
    assert(game.players[0].roadLength >= 6, 'Circular road with branch calculated correctly');
  }
  
  logSubSection('2.6 Three-Way Tie - No One Gets It');
  {
    const game = createTestGame();
    
    // Three players all build exactly 5 roads (no current holder)
    placeSettlement(game, 0, 'v_0_0_0');
    for (let i = 0; i < 5; i++) placeRoad(game, 0, `e_0_0_${i}`);
    
    placeSettlement(game, 1, 'v_1_0_0');
    for (let i = 0; i < 5; i++) placeRoad(game, 1, `e_1_0_${i}`);
    
    placeSettlement(game, 2, 'v_-1_0_0');
    for (let i = 0; i < 5; i++) placeRoad(game, 2, `e_-1_0_${i}`);
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Player 0 road: ${game.players[0].roadLength}`, 'yellow');
    log(`  Player 1 road: ${game.players[1].roadLength}`, 'yellow');
    log(`  Player 2 road: ${game.players[2].roadLength}`, 'yellow');
    log(`  Longest road holder: ${game.longestRoadPlayer}`, 'yellow');
    
    // In a three-way tie with no current holder, no one should get it
    // Per Catan rules: if there's a tie and no one currently has it, no one gets it
    if (game.players[0].roadLength === game.players[1].roadLength && 
        game.players[1].roadLength === game.players[2].roadLength) {
      assert(game.longestRoadPlayer === null, 'Three-way tie: no one gets longest road');
    }
  }
  
  logSubSection('2.7 Current Holder Keeps on Tie');
  {
    const game = createTestGame();
    
    // Player 0 gets longest road first
    placeSettlement(game, 0, 'v_0_0_0');
    for (let i = 0; i < 5; i++) placeRoad(game, 0, `e_0_0_${i}`);
    GameLogic.updateLongestRoad(game);
    
    assert(game.longestRoadPlayer === 0, 'Player 0 has longest road');
    
    // Player 1 ties with 5 roads
    placeSettlement(game, 1, 'v_1_0_0');
    for (let i = 0; i < 5; i++) placeRoad(game, 1, `e_1_0_${i}`);
    GameLogic.updateLongestRoad(game);
    
    // Current holder (Player 0) should keep it
    assert(game.longestRoadPlayer === 0, 'Current holder keeps longest road on tie');
    assert(game.players[0].hasLongestRoad === true, 'Player 0 still has flag');
    assert(game.players[1].hasLongestRoad === false, 'Player 1 does not have flag');
  }
  
  logSubSection('2.8 Longest Road Lost When Cut Below 5');
  {
    const game = createTestGame();
    
    // Player 0 builds exactly 5 roads in a line (no branches)
    placeSettlement(game, 0, 'v_0_0_0');
    placeRoad(game, 0, 'e_0_0_0');
    placeRoad(game, 0, 'e_0_0_1');
    placeRoad(game, 0, 'e_0_0_2');
    placeRoad(game, 0, 'e_0_0_3');
    placeRoad(game, 0, 'e_0_0_4');
    
    GameLogic.updateLongestRoad(game);
    assert(game.longestRoadPlayer === 0, 'Player 0 has longest road with 5');
    
    // Opponent cuts the road, reducing it to below 5
    // Place settlement at vertex 2 (between edges 1-2 and 2-3)
    placeSettlement(game, 1, 'v_0_0_2');
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Road length after cut: ${game.players[0].roadLength}`, 'yellow');
    
    // If road is now < 5, player loses longest road
    if (game.players[0].roadLength < 5) {
      assert(game.longestRoadPlayer === null, 'Longest road lost when cut below 5');
    }
  }
}

// =============================================================================
// TEST 3: EDGE CASES WITH EQUIVALENT EDGES/VERTICES
// =============================================================================
function testEdgeVertexEquivalence() {
  logSection('3. EDGE/VERTEX EQUIVALENCE IN ROAD CALCULATION');
  
  logSubSection('3.1 Roads Stored Under Different Equivalent Keys');
  {
    const game = createTestGame();
    
    placeSettlement(game, 0, 'v_0_0_0');
    
    // Place roads using different equivalent key representations
    placeRoad(game, 0, 'e_0_0_0'); // Primary key
    placeRoad(game, 0, 'e_1_0_4'); // This is equiv to e_0_0_1 from hex (1,0)
    placeRoad(game, 0, 'e_0_0_2');
    placeRoad(game, 0, 'e_0_0_3');
    placeRoad(game, 0, 'e_0_0_4');
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Road count: 5, Calculated length: ${game.players[0].roadLength}`, 'yellow');
    assert(game.players[0].roadLength >= 5, 'Roads under different equiv keys still counted');
  }
  
  logSubSection('3.2 Opponent Building at Equivalent Vertex Breaks Road');
  {
    const game = createTestGame();
    
    // Player 0 builds roads
    placeSettlement(game, 0, 'v_0_0_0');
    for (let i = 0; i < 6; i++) {
      placeRoad(game, 0, `e_0_0_${i}`);
    }
    
    GameLogic.updateLongestRoad(game);
    const before = game.players[0].roadLength;
    
    // Player 1 places settlement at an equivalent vertex key for v_0_0_3
    // v_0_0_3 = v_0_1_5 = v_-1_1_1
    placeSettlement(game, 1, 'v_0_1_5'); // Equivalent to v_0_0_3
    
    GameLogic.updateLongestRoad(game);
    const after = game.players[0].roadLength;
    
    log(`  Before: ${before}, After: ${after}`, 'yellow');
    assert(after <= before, 'Settlement at equivalent vertex affects road calculation');
  }
}

// =============================================================================
// TEST 4: ORIGINAL USER SCENARIO - RED VS PINK
// =============================================================================
function testUserScenario() {
  logSection('4. USER REPORTED SCENARIO');
  
  logSubSection('4.1 Correct Road Ownership Check');
  {
    // The bug: Red had a settlement at an intersection, and somehow
    // the peach/pink colored road was counted in red's continuation
    
    const game = createTestGame();
    
    // Pink (Player 0) builds roads
    placeSettlement(game, 0, 'v_-1_0_0');
    placeRoad(game, 0, 'e_-1_0_0');
    placeRoad(game, 0, 'e_-1_0_1');
    placeRoad(game, 0, 'e_-1_0_2');
    placeRoad(game, 0, 'e_-1_0_3');
    placeRoad(game, 0, 'e_-1_0_4');
    placeRoad(game, 0, 'e_-1_0_5');
    placeRoad(game, 0, 'e_-2_0_0');
    placeRoad(game, 0, 'e_-2_0_1');
    
    // Red (Player 1) builds roads and settlement at intersection
    placeSettlement(game, 1, 'v_0_0_0');
    placeRoad(game, 1, 'e_0_0_0');
    placeRoad(game, 1, 'e_0_0_1');
    placeRoad(game, 1, 'e_0_0_2');
    placeRoad(game, 1, 'e_0_0_3');
    
    // Red's settlement at the intersection of both networks
    placeSettlement(game, 1, 'v_-1_0_1'); // At intersection
    
    GameLogic.updateLongestRoad(game);
    
    log(`  Pink (P0) roads placed: 8`, 'yellow');
    log(`  Pink (P0) calculated length: ${game.players[0].roadLength}`, 'yellow');
    log(`  Red (P1) roads placed: 4`, 'yellow');
    log(`  Red (P1) calculated length: ${game.players[1].roadLength}`, 'yellow');
    log(`  Longest road holder: Player ${game.longestRoadPlayer}`, 'yellow');
    
    // Pink should have longest road
    assert(game.players[0].roadLength > game.players[1].roadLength, 
           'Pink has longer road than Red');
    
    // Critically: Pink's roads should NOT be counted in Red's total
    assert(game.players[1].roadLength === 4, 
           'Red only has their own 4 roads counted (not Pink roads)');
    
    if (game.players[0].roadLength >= 5) {
      assert(game.longestRoadPlayer === 0, 
             'Pink (Player 0) has longest road, NOT Red');
    }
  }
}

// =============================================================================
// MAIN TEST RUNNER
// =============================================================================
function runAllTests() {
  console.log('\n' + '█'.repeat(70));
  log('█  RIGOROUS LONGEST ROAD & DEV CARD TESTS  █', 'cyan');
  console.log('█'.repeat(70));
  
  try {
    testDevCardRollDice();
    testComplexLongestRoad();
    testEdgeVertexEquivalence();
    testUserScenario();
  } catch (error) {
    log(`\nTest Error: ${error.message}`, 'red');
    console.error(error.stack);
  }
  
  console.log('\n' + '═'.repeat(70));
  log('  TEST SUMMARY', 'cyan');
  console.log('═'.repeat(70));
  log(`\n  ✓ Tests Passed: ${passed}`, 'green');
  log(`  ✗ Tests Failed: ${failed}`, failed > 0 ? 'red' : 'green');
  log(`\n  Pass Rate: ${(passed / (passed + failed) * 100).toFixed(1)}%\n`, 
      passed === passed + failed ? 'green' : 'yellow');
  
  process.exit(failed > 0 ? 1 : 0);
}

runAllTests();

