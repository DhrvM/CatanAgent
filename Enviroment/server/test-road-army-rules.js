import assert from 'assert';
import * as GameLogic from './gameLogic.js';

function createTestGame() {
  const game = GameLogic.createGame('rules', { id: 'p1', name: 'Player1' }, false, false);
  GameLogic.addPlayer(game, { id: 'p2', name: 'Player2' });
  GameLogic.addPlayer(game, { id: 'p3', name: 'Player3' });
  GameLogic.addPlayer(game, { id: 'p4', name: 'Player4' });
  GameLogic.startGame(game);
  game.phase = 'playing';
  game.turnPhase = 'main';
  return game;
}

function placeRoadDirect(game, playerIndex, edgeKey) {
  game.edges[edgeKey] = { road: true, owner: playerIndex };
}

function placeSettlementDirect(game, playerIndex, vertexKey) {
  game.vertices[vertexKey] = { building: 'settlement', owner: playerIndex };
}

function setTurn(game, playerIndex) {
  game.currentPlayerIndex = playerIndex;
  game.turnPhase = 'main';
  game.devCardPlayedThisTurn = false;
}

function playKnight(game, playerIndex) {
  setTurn(game, playerIndex);
  game.players[playerIndex].developmentCards.push('knight');
  const result = GameLogic.playDevCard(game, game.players[playerIndex].id, 'knight');
  assert.strictEqual(result.success, true, `player ${playerIndex} should be able to play a knight`);
}

function assertLongestRoadState(game, holder, length) {
  assert.strictEqual(game.longestRoadPlayer, holder);
  assert.strictEqual(game.longestRoadLength, length);
  game.players.forEach((player, index) => {
    assert.strictEqual(player.hasLongestRoad, index === holder);
  });
}

{
  const game = createTestGame();
  placeRoadDirect(game, 0, 'e_0_0_0');
  placeRoadDirect(game, 0, 'e_0_0_1');
  placeRoadDirect(game, 0, 'e_0_0_2');
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, null, 4);
  assert.strictEqual(game.players[0].roadLength, 3, 'three connected roads should count as length 3');
}

{
  const game = createTestGame();
  ['e_0_0_5', 'e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3'].forEach(edge => placeRoadDirect(game, 0, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);
  assert.strictEqual(game.players[0].roadLength, 5, 'five connected roads should claim longest road');
}

{
  const game = createTestGame();
  ['e_0_0_5', 'e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3'].forEach(edge => placeRoadDirect(game, 0, edge));
  GameLogic.updateLongestRoad(game);
  ['e_1_0_5', 'e_1_0_0', 'e_1_0_1', 'e_1_0_2', 'e_1_0_3'].forEach(edge => placeRoadDirect(game, 1, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);
}

{
  const game = createTestGame();
  ['e_0_0_5', 'e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3'].forEach(edge => placeRoadDirect(game, 0, edge));
  GameLogic.updateLongestRoad(game);

  ['e_1_0_4', 'e_1_0_5', 'e_1_0_0', 'e_1_0_1', 'e_1_0_2', 'e_1_0_3'].forEach(edge => placeRoadDirect(game, 1, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 1, 6);
}

{
  const game = createTestGame();
  ['e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3', 'e_0_0_4'].forEach(edge => placeRoadDirect(game, 1, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 1, 5);

  ['e_-2_0_5', 'e_-2_0_0', 'e_-2_0_1', 'e_-2_0_2', 'e_-2_0_3'].forEach(edge => placeRoadDirect(game, 0, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 1, 5);

  placeSettlementDirect(game, 2, 'v_0_0_2');
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);
}

{
  const game = createTestGame();
  ['e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3', 'e_0_0_4'].forEach(edge => placeRoadDirect(game, 0, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);

  ['e_2_0_5', 'e_2_0_0', 'e_2_0_1', 'e_2_0_2', 'e_2_0_3'].forEach(edge => placeRoadDirect(game, 1, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);

  ['e_-2_0_5', 'e_-2_0_0', 'e_-2_0_1', 'e_-2_0_2', 'e_-2_0_3'].forEach(edge => placeRoadDirect(game, 2, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);

  placeSettlementDirect(game, 3, 'v_0_0_2');
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, null, 4);
  assert.strictEqual(game.players[1].roadLength, 5, 'player 1 should still have a valid road length of 5');
  assert.strictEqual(game.players[2].roadLength, 5, 'player 2 should still have a valid road length of 5');
}

{
  const game = createTestGame();
  placeSettlementDirect(game, 0, 'v_0_0_1');
  ['e_0_0_5', 'e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3'].forEach(edge => placeRoadDirect(game, 0, edge));
  GameLogic.updateLongestRoad(game);
  assertLongestRoadState(game, 0, 5);
}

{
  const game = createTestGame();
  placeRoadDirect(game, 0, 'e_0_0_1');
  placeRoadDirect(game, 0, 'e_1_0_4');
  GameLogic.updateLongestRoad(game);
  assert.strictEqual(game.players[0].roadLength, 1, 'equivalent edge representations should count as one physical road');
}

{
  const game = createTestGame();
  game.players[0].developmentCards.push('knight', 'knight', 'knight');
  assert.strictEqual(game.largestArmyPlayer, null, 'unplayed knight cards should not count toward largest army');

  playKnight(game, 0);
  playKnight(game, 0);
  assert.strictEqual(game.largestArmyPlayer, null, 'largest army should not award before three knights');

  playKnight(game, 0);
  assert.strictEqual(game.largestArmyPlayer, 0, 'first player to three knights should claim largest army');
  assert.strictEqual(game.players[0].hasLargestArmy, true);

  playKnight(game, 1);
  playKnight(game, 1);
  playKnight(game, 1);
  assert.strictEqual(game.largestArmyPlayer, 0, 'tying the holder should not steal largest army');

  playKnight(game, 1);
  assert.strictEqual(game.largestArmyPlayer, 1, 'exceeding the holder should steal largest army');
  assert.strictEqual(game.players[1].hasLargestArmy, true);
  assert.strictEqual(game.players[0].hasLargestArmy, false);
}

console.log('Road and army rule tests passed');
