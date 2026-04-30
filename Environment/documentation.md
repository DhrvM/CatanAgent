# Catan `Enviroment` Codebase Documentation

## High-Level Architecture

- `server/index.js` is the real-time API layer (Socket.IO + Express endpoints).
- `server/gameLogic.js` is the authoritative game engine and state transition system.
- `client/src/App.jsx` is the app shell (socket lifecycle + routing between lobby/game).
- `client/src/components/*` render game UI and dispatch socket actions.
- `server/test-*.js` are standalone integration/edge-case test runners.
- `render.yaml`, `vercel.json`, `static.json`, and `vite.config.js` define deployment/runtime behavior.

## Folder Map

- `Enviroment/README.md`: project overview, features, setup, deployment notes.
- `Enviroment/server/package.json`: server runtime and scripts (`start`, `dev`).
- `Enviroment/server/index.js`: socket event orchestration and game room/session management.
- `Enviroment/server/gameLogic.js`: complete Catan rules implementation.
- `Enviroment/server/test-game.js`: broad mechanics smoke/integration test.
- `Enviroment/server/test-full-game.js`: end-to-end full-game scenario checks.
- `Enviroment/server/test-edge-cases.js`: targeted edge-case tests (roads/resources/visibility).
- `Enviroment/server/test-longest-road-rigorous.js`: deep longest-road and dev-card flow tests.
- `Enviroment/client/src/main.jsx`: React entrypoint.
- `Enviroment/client/src/App.jsx`: socket bootstrap, reconnect, global notifications, lobby/game switch.
- `Enviroment/client/src/components/*.jsx`: all gameplay UI modules.
- `Enviroment/client/index.html`, `vite.config.js`: frontend boot and dev server config.
- `Enviroment/client/vercel.json`, `static.json`: SPA routing + headers for static deployments.
- `Enviroment/render.yaml`: Render blueprint for server + client services.
- `Enviroment/.gitignore`, `Enviroment/LICENSE`: repo hygiene + legal terms.

## Backend

### `server/index.js`

#### Key State Variables

- `app`, `httpServer`, `io`: Express + HTTP + Socket.IO stack.
- `games: Map<gameCode, gameState>`: in-memory game store.
- `playerSockets: Map<socketId, {gameId, playerId}>`: socket/player mapping.
- `MAX_CONCURRENT_GAMES`, `MAX_TOTAL_PLAYERS`, `totalConnectedPlayers`: capacity guardrails.

#### Functions

- `generateGameCode()`: creates 6-char room codes.
- `broadcastGameState(gameId)`: sends player-specific redacted game state to each socket.
- `broadcastToGame(gameId, event, data)`: sends same event payload to all players.

#### Socket Event Groups

- Lobby/session: `createGame`, `joinGame`, `startGame`, `shuffleBoard`, `reconnect`, `disconnect`.
- Turn actions: `rollDice`, `discardCards`, `moveRobber`.
- Build/actions: `placeSettlement`, `placeRoad`, `upgradeToCity`, `buyDevCard`, `playDevCard`, `yearOfPlentyPick`.
- Trading: `bankTrade`, `proposeTrade`, `respondToTrade`, `cancelTrade`.
- Turn progression: `advanceSetup`, `endTurn`, `endSpecialBuild`.
- Utility/chat: `getPlayersOnHex`, `chatMessage`.
- HTTP endpoints: `/`, `/health`, `/status`, `/ping`.

---

### `server/gameLogic.js`

#### Exported Constants

- `RESOURCES`
- `TERRAIN`
- `BUILDING_COSTS`
- `DEV_CARDS`
- `PLAYER_COLORS`
- `PORT_TYPES`

#### Core Game Object Fields

- Flow: `phase`, `setupPhase`, `currentPlayerIndex`, `turnPhase`
- Extension: `isExtended`, `enableSpecialBuild`, `specialBuildingPhase`, `specialBuildIndex`
- Board: `hexes`, `vertices`, `edges`, `ports`, `robber`
- Players: resources/cards/pieces/VP/army/road stats
- Achievements: `longestRoadPlayer`, `largestArmyPlayer`, etc.
- Turn flags: `tradeOffer`, `discardingPlayers`, `freeRoads`, `yearOfPlentyPicks`, `devCardPlayedThisTurn`, `hasRolledThisTurn`

#### Key/Geometry Functions

- `hexKey(q, r)`
- `vertexKey(hexQ, hexR, direction)`
- `edgeKey(hexQ, hexR, direction)`
- `getHexVertices(q, r)`
- `normalizeVertex(hexQ, hexR, dir, hexes)`
- `areVerticesEqual(vKey1, vKey2)`
- `getEquivalentEdges(q, r, dir)`
- `getVertexEdges(vKey)`
- `getAdjacentVertices(vKey, hexes)`

#### Game Lifecycle Functions

- `createGame(gameId, hostPlayer, isExtended = false, enableSpecialBuild = true)`
- `addPlayer(game, player)`
- `startGame(game)`
- `shuffleBoard(game)`

#### Turn/Resource Functions

- `rollDice(game, playerId)`
- `discardCards(game, playerId, resources)`
- `moveRobber(game, playerId, hexKey, stealFromPlayerId)`

#### Building Functions

- `canPlaceSettlement(game, playerId, vKey, isSetup = false)`
- `placeSettlement(game, playerId, vKey)`
- `canPlaceRoad(game, playerId, eKey, isSetup = false, lastSettlement = null)`
- `placeRoad(game, playerId, eKey, isSetup = false, lastSettlement = null)`
- `upgradeToCity(game, playerId, vKey)`

#### Development Card Functions

- `buyDevCard(game, playerId)`
- `playDevCard(game, playerId, cardType, params = {})`
- `yearOfPlentyPick(game, playerId, resource)`

#### Trading Functions

- `getPlayerPorts(game, playerIndex)`
- `getTradeRatio(game, playerIndex, resource)`
- `bankTrade(game, playerId, giveResource, giveAmount, getResource)`
- `proposeTrade(game, playerId, offer, request)`
- `respondToTrade(game, playerId, accept)`
- `cancelTrade(game, playerId)`

#### Turn Progression / Extension Functions

- `endTurn(game, playerId)`
- `endSpecialBuild(game, playerId)`
- `canSpecialBuild(game, playerId)`
- `advanceSetup(game, playerId)`

#### Scoring/View Utilities

- `updateLongestRoad(game)`
- `getPlayerView(game, playerId)`
- `getVertexAdjacentHexes(game, vKey)`
- `getPlayersOnHex(game, hKey, excludePlayer = null)`

## Frontend

### `client/src/main.jsx`

- Mounts `<App />` inside `React.StrictMode`.

### `client/src/App.jsx`

#### State Variables

- `socket`, `connected`, `gameState`, `playerId`, `gameCode`
- `error`, `chatMessages`, `notifications`, `serverFull`

#### Core Handlers

- `addNotification(message)`
- `handleCreateGame(playerName, isExtended, enableSpecialBuild)`
- `handleJoinGame(code, playerName)`
- `handleLeaveGame()`

#### Responsibilities

- Socket connect/reconnect lifecycle
- Server keep-alive ping loop
- Event listeners for game/chat/trade notifications
- Conditional rendering: loading → lobby → game

---

### Component Docs

#### `Lobby.jsx`

- Local state: `mode`, `playerName`, `gameCode`, `showRules`, `isExtended`, `enableSpecialBuild`
- Main function: `handleSubmit`
- Purpose: create/join flow + mode selection + rules modal trigger

#### `GameBoard.jsx`

- Main in-game orchestration component
- Local state covers action selection, modals, robber/steal flow, unread chat, dice visibility, notifications
- Key handlers:
  - `handleRollDice`
  - `handlePlaceSettlement`
  - `handlePlaceRoad`
  - `handleUpgradeToCity`
  - `handleHexClick`
  - `handleStealFromPlayer`
  - `handleEndTurn`
  - `handleBuyDevCard`
  - `handleStartGame`
  - `handleShuffleBoard`
  - `handleSendChat`
  - `handleEndSpecialBuild`
- Computes game status messages and renders all gameplay UI sections

#### `HexBoard.jsx`

- Geometry helpers:
  - `axialToPixel`
  - `getVertexPosition`
  - `hexPath`
  - `posKey`
  - `getNumberColor`
  - `getTerrainIcon`
- Interaction validation helpers:
  - `canPlaceAtVertex`
  - `canUpgradeVertex`
  - `canPlaceAtEdge`
  - `canClickHex`
- Parsing/render helpers:
  - `parseVertexKey`
  - `parseEdgeKey`
  - `getEdgeEndpoints`
- Memoized derived data:
  - `roads`
  - `clickableEdges`
  - `uniqueVertices`
- Renders board hexes, roads, settlements/cities, ports, and placement targets

#### `ActionPanel.jsx`

- Constants: `BUILDING_COSTS`
- Helper: `hasResources(player, costs)`
- Derives action availability and renders build/trade/dev/end turn actions

#### `PlayerPanel.jsx`

- Computes `totalCards`, `devCardCount`, `hiddenVP`
- `handleRightClick` passes info requests upward
- Displays player summary, pieces, and achievements

#### `ResourceCards.jsx`

- Constant metadata: `RESOURCES`
- `handleContextMenu` supports right-click info and selectable deselection
- Supports compact/count-only mode and selectable mode

#### `TradeModal.jsx`

- Constants: `RESOURCES`, `RESOURCE_ICONS`
- State: `tradeType`, `offer`, `request`, `bankGive`, `bankGet`
- Handlers:
  - `updateOffer`
  - `updateRequest`
  - `handleProposeTrade`
  - `handleAcceptTrade`
  - `handleDeclineTrade`
  - `handleCancelTrade`
  - `handleBankTrade`
- Handles both player trades and bank/port trading

#### `DevCardModal.jsx`

- Constants: `DEV_CARD_INFO`, `RESOURCES`, `RESOURCE_ICONS`
- State: `selectedCard`, `monopolyResource`
- Handlers:
  - `handlePlayCard`
  - `handleMonopoly`
  - `handleYearOfPlentyPick`
- Displays playable and newly purchased (locked this turn) cards

#### `DiscardModal.jsx`

- State: selected resource counts
- Handlers: `updateSelected`, `handleDiscard`
- Enforces exact discard count when 7 is rolled

#### `DiceDisplay.jsx`

- Constant: `DICE_FACES`
- State: roll animation flag
- Right-click info payload for dice probability/robber behavior

#### `Chat.jsx`

- State: `input`
- Refs/effects for auto-scroll
- Handlers: `handleSubmit`, `formatTime`

#### `InfoPopup.jsx`

- Central dictionaries:
  - `INFO_DATA`
  - `NUMBER_PROBABILITIES`
- Component: `InfoPopup`
- Hook: `useInfoPopup()` with `showInfo`, `showHexInfo`, `closePopup`

#### `RulesModal.jsx`

- State: `activeSection`
- Contains in-app rules text sections and tabbed rendering

#### `CardReveal.jsx`

- Constant: `DEV_CARD_INFO`
- State: `isAnimating`, `isFlipped`
- Animated dev-card reveal overlay

#### `Confetti.jsx`

- Constant: `CONFETTI_COLORS`
- State: generated confetti pieces
- Winner overlay + optional “Back to Lobby” callback

## Test Scripts

### `server/test-game.js`

- Broad gameplay simulation and assertions.
- Includes helper functions to detect valid placements and piece counts.

### `server/test-full-game.js`

- Most complete test suite across setup, build, trade, robber, dev cards, and edge errors.
- Tracks pass/fail summary with detailed sections.

### `server/test-edge-cases.js`

- Edge-focused tests:
  - Longest road corner cases
  - Shared-vertex resource distribution
  - Hidden VP visibility logic
  - Coordinate and equivalence behavior
  - Dev-card restrictions

### `server/test-longest-road-rigorous.js`

- Deep tests for:
  - Longest road with ties/loops/branches/cuts
  - Equivalent edge/vertex handling
  - Knight-before-roll and knight-after-roll turn-phase behavior

## Deployment/Config Files

- `client/vite.config.js`: Vite + React plugin, dev server host/port.
- `client/vercel.json`: SPA rewrites + security headers.
- `client/static.json`: static host SPA fallback.
- `render.yaml`: Render multi-service deploy template.
- `client/index.html`: HTML shell, root mount, font imports.

## Documentation/Legal/Misc

- `Enviroment/README.md`: user-facing project docs.
- `Enviroment/LICENSE`: MIT + project disclaimer.
- `Enviroment/.gitignore`: ignored dependencies/build/env/log files.
- `server/package.json`, `client/package.json`: scripts and dependency manifests.