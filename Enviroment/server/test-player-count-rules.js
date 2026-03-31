import assert from 'assert';
import * as GameLogic from './gameLogic.js';

const standardGame = GameLogic.createGame('standard', { id: 'p1', name: 'Player1' }, false, false);
GameLogic.addPlayer(standardGame, { id: 'p2', name: 'Player2' });
assert.strictEqual(GameLogic.startGame(standardGame).success, false, 'standard CATAN should not start with 2 players');
GameLogic.addPlayer(standardGame, { id: 'p3', name: 'Player3' });
assert.strictEqual(GameLogic.startGame(standardGame).success, true, 'standard CATAN should start with 3 players');

const extendedGame = GameLogic.createGame('extended', { id: 'p1', name: 'Player1' }, true, true);
GameLogic.addPlayer(extendedGame, { id: 'p2', name: 'Player2' });
GameLogic.addPlayer(extendedGame, { id: 'p3', name: 'Player3' });
GameLogic.addPlayer(extendedGame, { id: 'p4', name: 'Player4' });
assert.strictEqual(GameLogic.startGame(extendedGame).success, false, 'extended CATAN should not start with 4 players');
GameLogic.addPlayer(extendedGame, { id: 'p5', name: 'Player5' });
assert.strictEqual(GameLogic.startGame(extendedGame).success, true, 'extended CATAN should start with 5 players');

console.log('Player count rule tests passed');
