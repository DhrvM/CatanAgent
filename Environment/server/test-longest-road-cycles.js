import assert from 'assert';
import * as GameLogic from './gameLogic.js';

function emptyRoads(game) {
  Object.values(game.edges).forEach(edge => {
    edge.road = false;
    edge.owner = null;
  });
}

function resetPlayers(game) {
  game.players.forEach(player => {
    player.victoryPoints = 0;
    player.hasLongestRoad = false;
    player.roadLength = 0;
  });
  game.longestRoadPlayer = null;
  game.longestRoadLength = 4;
}

function placeRoadsDirectly(game, owner, roadKeys) {
  roadKeys.forEach(edgeKey => {
    assert(game.edges[edgeKey], `expected edge ${edgeKey} to exist`);
    game.edges[edgeKey] = { road: true, owner };
  });
}

const game = GameLogic.createGame('longest-road-cycle-test', { id: 'p1', name: 'P1' });
GameLogic.addPlayer(game, { id: 'p2', name: 'P2' });
game.phase = 'playing';

emptyRoads(game);
resetPlayers(game);
placeRoadsDirectly(game, 0, ['e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3', 'e_0_0_4', 'e_0_0_5']);
GameLogic.updateLongestRoad(game);
assert.strictEqual(game.players[0].roadLength, 6, 'closed six-edge loop counts once per edge');
assert.strictEqual(game.longestRoadPlayer, 0, 'six-edge loop can claim Longest Road');

emptyRoads(game);
resetPlayers(game);
placeRoadsDirectly(game, 0, ['e_0_0_0', 'e_0_0_1', 'e_0_0_2', 'e_0_0_3', 'e_0_0_4', 'e_0_0_5', 'e_0_-1_1']);
GameLogic.updateLongestRoad(game);
assert.strictEqual(game.players[0].roadLength, 7, 'loop plus one branch counts each road at most once');
assert(game.players[0].roadLength <= 7, 'cycle traversal cannot inflate beyond owned road count');

console.log('Longest road cycle tests passed');
