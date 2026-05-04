import HexBoard from './HexBoard';
import PlayerPanel from './PlayerPanel';
import './SpectatorBoard.css';

const RESOURCE_ICONS = {
  brick: '🧱',
  lumber: '🪵',
  wool: '🐑',
  grain: '🌾',
  ore: '⛏️',
};

function renderTradeSide(resources = {}) {
  return Object.entries(resources)
    .filter(([, amount]) => Number(amount) > 0)
    .map(([resource, amount]) => `${RESOURCE_ICONS[resource] || resource} ${amount}`)
    .join('  ');
}

function eventText(event, players) {
  const nameById = (id) => players.find((p) => p.id === id)?.name || 'Unknown';
  const nameByIndex = (idx) => players[idx]?.name || `P${idx}`;
  const place = (key) => (key ? ` @ ${key}` : '');
  const devCardLabel = (cardType) => {
    const labels = {
      knight: 'Knight',
      roadBuilding: 'Road Building',
      yearOfPlenty: 'Year of Plenty',
      monopoly: 'Monopoly',
      victoryPoint: 'Victory Point',
    };
    return labels[cardType] || cardType || 'Development Card';
  };
  if (event.type === 'diceRolled') {
    const roller = event.playerId ? nameById(event.playerId) : 'A player';
    return `🎲 ${roller} rolled ${event.roll ?? '?'}`;
  }
  if (event.type === 'tradeProposed') {
    const proposer = nameById(event.from);
    return `🤝 ${proposer} proposed: ${renderTradeSide(event.offer)}  ⇄  ${renderTradeSide(event.request)}`;
  }
  if (event.type === 'tradeAccepted') {
    return `✅ Trade accepted by ${nameById(event.by)}`;
  }
  if (event.type === 'tradeDeclined') {
    return `❌ Trade declined by ${nameById(event.by)}`;
  }
  if (event.type === 'tradeCancelled') {
    return '🛑 Active trade was cancelled';
  }
  if (event.type === 'resourcesDistributed') {
    const segments = (event.allGains || [])
      .map(({ playerName, gains }) => {
        const gainsText = renderTradeSide(gains);
        if (!gainsText) return null;
        return `${playerName}: ${gainsText}`;
      })
      .filter(Boolean);
    return `🎲 Roll ${event.fromRoll}: ${segments.join(' | ')}`;
  }
  if (event.type === 'robberMoved') {
    const mover = event.by ? nameById(event.by) : 'A player';
    const target = event.stealFromPlayerId ? ` targeting ${nameById(event.stealFromPlayerId)}` : '';
    return `🦹 ${mover} moved robber to ${event.hexKey || 'a tile'}${target}`;
  }
  if (event.type === 'robberResolved') {
    const thief = event.thiefName || (event.thief ? nameById(event.thief) : 'A player');
    const victim = event.victimName || (event.victim ? nameById(event.victim) : 'no one');
    if (event.stolen) {
      return `🥷 ${thief} stole ${RESOURCE_ICONS[event.resource] || event.resource || 'a card'} from ${victim}`;
    }
    return `🥷 ${thief} attempted robber action on ${victim} but stole nothing`;
  }
  if (event.type === 'stealResult') {
    return `🥷 ${event.otherPlayer || 'A player'} ${event.resultType === 'stole' ? 'lost' : 'stole'} ${RESOURCE_ICONS[event.resource] || event.resource || 'a card'}`;
  }
  if (event.type === 'settlementPlaced') {
    return `🏠 ${nameById(event.by)} built a settlement${place(event.vertexKey)}`;
  }
  if (event.type === 'roadPlaced') {
    return `🛣️ ${nameById(event.by)} built a road${place(event.edgeKey)}`;
  }
  if (event.type === 'cityBuilt') {
    return `🏙️ ${nameById(event.by)} upgraded to a city${place(event.vertexKey)}`;
  }
  if (event.type === 'turnEnded') {
    return `⏭️ ${nameById(event.by)} ended their turn`;
  }
  if (event.type === 'longestRoadChanged') {
    if (event.to == null || event.to < 0) return '🛣️ Longest Road is now unclaimed';
    const fromText = event.from != null && event.from >= 0 ? `${nameByIndex(event.from)} -> ` : '';
    return `🛣️ Longest Road: ${fromText}${nameByIndex(event.to)}`;
  }
  if (event.type === 'largestArmyChanged') {
    if (event.to == null || event.to < 0) return '🛡️ Largest Army is now unclaimed';
    const fromText = event.from != null && event.from >= 0 ? `${nameByIndex(event.from)} -> ` : '';
    return `🛡️ Largest Army: ${fromText}${nameByIndex(event.to)}`;
  }
  if (event.type === 'devCardBought') {
    return `🃏 ${nameById(event.by)} bought a development card (could be a victory point card)`;
  }
  if (event.type === 'devCardPlayed') {
    return `🃏 ${nameById(event.by)} played ${devCardLabel(event.cardType)}`;
  }
  if (event.type === 'activeTrade') {
    return `📌 Active trade by ${nameByIndex(event.from)}: ${renderTradeSide(event.offer)} ⇄ ${renderTradeSide(event.request)}`;
  }
  return 'Event';
}

function SpectatorBoard({ gameState, gameCode, spectatorEvents = [], onLeave }) {
  const players = gameState?.players || [];
  const currentPlayer = players[gameState.currentPlayerIndex] || null;
  const activeTrade = gameState.tradeOffer;
  const feed = [
    ...(activeTrade ? [{ id: 'active-trade', type: 'activeTrade', from: activeTrade.from, offer: activeTrade.offer, request: activeTrade.request, timestamp: 0 }] : []),
    ...spectatorEvents,
  ].slice(0, 60);

  return (
    <div className="game-board spectator-game-board">
      <div className="game-header">
        <div className="game-code-display">
          <span className="label">Game Code:</span>
          <span className="code">{gameCode}</span>
        </div>
        <div className="turn-indicator">
          <div className="current-player-badge spectator-badge">
            {currentPlayer ? currentPlayer.name : 'Waiting'}
          </div>
          <span className="status-message">
            Spectating | Phase: {gameState.phase} / {gameState.turnPhase} | Round {gameState.roundNumber || 0}
          </span>
        </div>
        <div className="header-actions">
          <button className="leave-btn" onClick={onLeave}>Leave Spectate</button>
        </div>
      </div>

      <div className="game-main">
        <div className="sidebar left-sidebar">
          <h3>Players</h3>
          {players.map((player, idx) => (
            <PlayerPanel
              key={player.id || idx}
              player={player}
              isCurrentTurn={idx === gameState.currentPlayerIndex}
              isMe={false}
              longestRoad={gameState.longestRoadPlayer === idx}
              largestArmy={gameState.largestArmyPlayer === idx}
              gameOver={gameState.phase === 'finished'}
            />
          ))}
        </div>

        <div className="board-container">
          <HexBoard
            hexes={gameState.hexes || {}}
            vertices={gameState.vertices || {}}
            edges={gameState.edges || {}}
            robber={gameState.robber}
            players={players}
            ports={gameState.ports || []}
            selectedAction={null}
            isMyTurn={false}
            canBuildNow={false}
            myIndex={-1}
            gamePhase={gameState.phase}
            turnPhase={gameState.turnPhase}
            onPlaceSettlement={() => {}}
            onPlaceRoad={() => {}}
            onUpgradeToCity={() => {}}
            onHexClick={() => {}}
            onHexRightClick={() => {}}
            lastPlacedSettlement={null}
            freeRoads={0}
          />
        </div>

        <div className="sidebar right-sidebar spectator-feed-sidebar">
          <h3>Live Feed</h3>
          <div className="spectator-feed-list">
            {feed.length ? feed.map((event) => (
              <div key={event.id || `${event.type}-${event.timestamp}`} className="spectator-feed-item">
                {eventText(event, players)}
              </div>
            )) : <div className="spectator-empty">Waiting for events...</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

export default SpectatorBoard;
