import assert from 'assert';
import * as GameLogic from './gameLogic.js';

function createTestGame() {
  const game = GameLogic.createGame('counter', { id: 'p1', name: 'Player1' }, false, false);
  GameLogic.addPlayer(game, { id: 'p2', name: 'Player2' });
  GameLogic.addPlayer(game, { id: 'p3', name: 'Player3' });
  GameLogic.startGame(game);
  game.phase = 'playing';
  game.turnPhase = 'main';
  return game;
}

function setCurrentPlayer(game, playerIndex) {
  game.currentPlayerIndex = playerIndex;
  game.turnPhase = 'main';
}

function giveResources(player, resources) {
  Object.entries(resources).forEach(([resource, amount]) => {
    player.resources[resource] += amount;
  });
}

{
  const game = createTestGame();
  const trader = game.players[0];
  const responder = game.players[1];
  const bystander = game.players[2];

  setCurrentPlayer(game, 0);
  giveResources(trader, { brick: 2 });
  giveResources(responder, { wool: 1 });

  let result = GameLogic.proposeTrade(
    game,
    trader.id,
    { brick: 2, lumber: 0, wool: 0, grain: 0, ore: 0 },
    { brick: 0, lumber: 0, wool: 1, grain: 0, ore: 0 }
  );
  assert.strictEqual(result.success, true, 'broadcast trade should be created');
  assert.strictEqual(game.tradeOffer.to, undefined, 'broadcast trade should not target a single player');

  result = GameLogic.counterTrade(
    game,
    responder.id,
    { brick: 0, lumber: 0, wool: 1, grain: 0, ore: 0 },
    { brick: 1, lumber: 0, wool: 0, grain: 0, ore: 0 }
  );
  assert.strictEqual(result.success, true, 'broadcast recipient should be able to counter');
  assert.strictEqual(game.tradeOffer.from, 1, 'counter offer should now originate from responder');
  assert.strictEqual(game.tradeOffer.to, 0, 'counter offer should target the original proposer');

  result = GameLogic.counterTrade(
    game,
    bystander.id,
    { brick: 0, lumber: 0, wool: 0, grain: 1, ore: 0 },
    { brick: 1, lumber: 0, wool: 0, grain: 0, ore: 0 }
  );
  assert.strictEqual(result.success, false, 'once countered, only the new targeted recipient may counter');
}

{
  const game = createTestGame();
  const trader = game.players[0];
  const target = game.players[1];
  const bystander = game.players[2];

  setCurrentPlayer(game, 0);
  giveResources(trader, { brick: 2 });
  giveResources(target, { wool: 1 });

  let result = GameLogic.proposeTrade(
    game,
    trader.id,
    { brick: 2, lumber: 0, wool: 0, grain: 0, ore: 0 },
    { brick: 0, lumber: 0, wool: 1, grain: 0, ore: 0 },
    target.id
  );
  assert.strictEqual(result.success, true, 'targeted trade should be created');

  result = GameLogic.counterTrade(
    game,
    bystander.id,
    { brick: 0, lumber: 0, wool: 0, grain: 1, ore: 0 },
    { brick: 1, lumber: 0, wool: 0, grain: 0, ore: 0 }
  );
  assert.strictEqual(result.success, false, 'bystander should not be able to counter a targeted offer');

  result = GameLogic.counterTrade(
    game,
    target.id,
    { brick: 0, lumber: 0, wool: 1, grain: 0, ore: 0 },
    { brick: 1, lumber: 0, wool: 0, grain: 0, ore: 0 }
  );
  assert.strictEqual(result.success, true, 'targeted recipient should still be able to counter');
}

console.log('Counter-offer tests passed');
