import { useState, useEffect } from 'react';
import './TradeModal.css';

const RESOURCES = ['brick', 'lumber', 'wool', 'grain', 'ore'];
const RESOURCE_ICONS = {
  brick: '🧱',
  lumber: '🪵',
  wool: '🐑',
  grain: '🌾',
  ore: '⛏️'
};

function TradeModal({ socket, gameState, myPlayer, isMyTurn, onClose, addNotification }) {
  const [tradeType, setTradeType] = useState('player'); // 'player' or 'bank'
  const [selectedPlayerId, setSelectedPlayerId] = useState(null);
  const [offer, setOffer] = useState({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
  const [request, setRequest] = useState({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
  const [bankGive, setBankGive] = useState(null);
  const [bankGet, setBankGet] = useState(null);
  const [showCounterForm, setShowCounterForm] = useState(false);
  const [counterOffer, setCounterOffer] = useState({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
  const [counterRequest, setCounterRequest] = useState({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });

  const pendingTrade = gameState.tradeOffer;
  const isTradeFromMe = pendingTrade?.from === gameState.myIndex;
  const otherPlayers = gameState.players.filter((_, idx) => idx !== gameState.myIndex);

  // Close panel when pending trade is completed/cancelled (for the proposer)
  useEffect(() => {
    if (!pendingTrade && tradeType === 'player') {
      setOffer({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
      setRequest({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
      setSelectedPlayerId(null);
    }
  }, [pendingTrade, tradeType]);

  // Reset counter form when incoming trade is no longer for us
  useEffect(() => {
    if (!pendingTrade || pendingTrade.from === gameState.myIndex) {
      setShowCounterForm(false);
      setCounterOffer({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
      setCounterRequest({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
    }
  }, [pendingTrade, gameState.myIndex]);

  const updateOffer = (resource, delta) => {
    const newAmount = Math.max(0, Math.min(myPlayer.resources[resource], offer[resource] + delta));
    setOffer({ ...offer, [resource]: newAmount });
  };

  const updateRequest = (resource, delta) => {
    const newAmount = Math.max(0, request[resource] + delta);
    setRequest({ ...request, [resource]: newAmount });
  };

  const handleProposeTrade = () => {
    if (!selectedPlayerId) {
      addNotification('Select a player to trade with');
      return;
    }
    const hasOffer = Object.values(offer).some(v => v > 0);
    const hasRequest = Object.values(request).some(v => v > 0);
    if (!hasOffer || !hasRequest) {
      addNotification('Must offer and request at least one resource');
      return;
    }
    socket.emit('proposeTrade', { offer, request, targetPlayerId: selectedPlayerId }, (response) => {
      if (!response.success) {
        addNotification(response.error);
      } else {
        const target = gameState.players.find(p => p.id === selectedPlayerId);
        addNotification(target ? `Trade proposed to ${target.name}!` : 'Trade proposed!');
      }
    });
  };

  const handleAcceptTrade = () => {
    socket.emit('respondToTrade', { accept: true }, (response) => {
      if (!response.success) {
        addNotification(response.error);
      } else {
        addNotification('Trade accepted!');
        onClose(); // Close modal after accepting
      }
    });
  };

  const handleDeclineTrade = () => {
    socket.emit('respondToTrade', { accept: false }, (response) => {
      if (!response.success) {
        addNotification(response.error);
      } else {
        onClose(); // Close modal after declining
      }
    });
  };

  const handleCancelTrade = () => {
    socket.emit('cancelTrade', (response) => {
      if (!response.success) {
        addNotification(response.error);
      } else {
        addNotification('Trade cancelled');
      }
    });
  };

  const updateCounterOffer = (resource, delta) => {
    const newAmount = Math.max(0, Math.min(myPlayer.resources[resource], counterOffer[resource] + delta));
    setCounterOffer({ ...counterOffer, [resource]: newAmount });
  };

  const updateCounterRequest = (resource, delta) => {
    const newAmount = Math.max(0, counterRequest[resource] + delta);
    setCounterRequest({ ...counterRequest, [resource]: newAmount });
  };

  const canSendCounter =
    Object.values(counterOffer).some(v => v > 0) &&
    Object.values(counterRequest).some(v => v > 0);

  const handleCounterTrade = () => {
    if (!canSendCounter) {
      addNotification('Add at least one resource to offer and one to request');
      return;
    }
    socket.emit('counterTrade', { offer: counterOffer, request: counterRequest }, (response) => {
      if (!response) {
        addNotification('No response from server');
        return;
      }
      if (!response.success) {
        addNotification(response.error || 'Counter offer failed');
        return;
      }
      addNotification('Counter offer sent!');
      setShowCounterForm(false);
      setCounterOffer({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
      setCounterRequest({ brick: 0, lumber: 0, wool: 0, grain: 0, ore: 0 });
    });
  };

  const handleBankTrade = () => {
    if (!bankGive || !bankGet || bankGive === bankGet) {
      addNotification('Select different resources to give and receive');
      return;
    }

    const ratio = gameState.tradeRatios?.[bankGive] || 4;
    
    if (myPlayer.resources[bankGive] < ratio) {
      addNotification(`Not enough ${bankGive} (need ${ratio})`);
      return;
    }

    socket.emit('bankTrade', { giveResource: bankGive, giveAmount: ratio, getResource: bankGet }, (response) => {
      if (response.success) {
        addNotification(`Traded ${ratio} ${bankGive} for 1 ${bankGet}!`);
        onClose(); // Close modal after successful trade
      } else {
        addNotification(response.error);
      }
    });
  };

  // If there's a pending trade from another player
  if (pendingTrade && !isTradeFromMe) {
    const trader = gameState.players[pendingTrade.from];

    if (showCounterForm) {
      return (
        <div className="trade-panel">
          <div className="trade-modal">
            <button className="close-btn" onClick={onClose}>×</button>
            <h2>Counter offer to {trader.name}</h2>
            <p className="counter-offer-hint">Propose a different trade back to {trader.name}</p>
            <div className="trade-builder">
              <div className="trade-section">
                <h4>You Offer:</h4>
                <div className="resource-selectors">
                  {RESOURCES.map(r => (
                    <div key={r} className="resource-selector">
                      <span className="icon">{RESOURCE_ICONS[r]}</span>
                      <div className="selector-controls">
                        <button onClick={() => updateCounterOffer(r, -1)} disabled={counterOffer[r] === 0}>−</button>
                        <span className="amount">{counterOffer[r]}</span>
                        <button onClick={() => updateCounterOffer(r, 1)} disabled={counterOffer[r] >= myPlayer.resources[r]}>+</button>
                      </div>
                      <span className="available">({myPlayer.resources[r]})</span>
                    </div>
                  ))}
                </div>
              </div>
              <div className="trade-section">
                <h4>You Request:</h4>
                <div className="resource-selectors">
                  {RESOURCES.map(r => (
                    <div key={r} className="resource-selector">
                      <span className="icon">{RESOURCE_ICONS[r]}</span>
                      <div className="selector-controls">
                        <button onClick={() => updateCounterRequest(r, -1)} disabled={counterRequest[r] === 0}>−</button>
                        <span className="amount">{counterRequest[r]}</span>
                        <button onClick={() => updateCounterRequest(r, 1)}>+</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="trade-actions trade-actions-counter">
              <button type="button" className="counter-back-btn" onClick={() => setShowCounterForm(false)}>
                ← Back
              </button>
              {!canSendCounter && (
                <p className="counter-validation-hint">Add at least one resource under &quot;You Offer&quot; and one under &quot;You Request&quot; to send.</p>
              )}
              <button
                type="button"
                className="propose-btn counter-send-btn"
                onClick={handleCounterTrade}
                disabled={!canSendCounter}
              >
                📤 Send counter offer
              </button>
            </div>
          </div>
        </div>
      );
    }

    return (
      <div className="trade-panel">
        <div className="trade-modal">
          <button className="close-btn" onClick={onClose}>×</button>
          <h2>Trade Offer from {trader.name}</h2>
          <div className="trade-display">
            <div className="trade-side">
              <h4>They Offer:</h4>
              <div className="resource-list">
                {RESOURCES.map(r => pendingTrade.offer[r] > 0 && (
                  <div key={r} className="resource-item">
                    <span className="icon">{RESOURCE_ICONS[r]}</span>
                    <span className="amount">{pendingTrade.offer[r]}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="trade-arrow">⇄</div>
            <div className="trade-side">
              <h4>They Request:</h4>
              <div className="resource-list">
                {RESOURCES.map(r => pendingTrade.request[r] > 0 && (
                  <div key={r} className="resource-item">
                    <span className="icon">{RESOURCE_ICONS[r]}</span>
                    <span className="amount">{pendingTrade.request[r]}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="trade-actions">
            <button className="accept-btn" onClick={handleAcceptTrade}>✓ Accept</button>
            <button className="decline-btn" onClick={handleDeclineTrade}>✗ Decline</button>
            <button type="button" className="counter-offer-btn" onClick={() => setShowCounterForm(true)}>
              ↩ Counter offer
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="trade-panel">
      <div className="trade-modal">
        <button className="close-btn" onClick={onClose}>×</button>
        <h2>Trade</h2>
        <div className="trade-tabs">
          <button className={tradeType === 'player' ? 'active' : ''} onClick={() => setTradeType('player')}>
            🤝 With Players
          </button>
          <button className={tradeType === 'bank' ? 'active' : ''} onClick={() => setTradeType('bank')}>
            🏦 With Bank/Ports
          </button>
        </div>

        {tradeType === 'player' ? (
          <>
            {pendingTrade && isTradeFromMe && (
              <div className="pending-trade">
                <p>
                  {pendingTrade.to !== undefined && gameState.players[pendingTrade.to]
                    ? `Waiting for ${gameState.players[pendingTrade.to].name} to respond...`
                    : 'Waiting for responses...'}
                </p>
                <button onClick={handleCancelTrade}>Cancel Trade</button>
              </div>
            )}

            {!pendingTrade && (
              <>
                <div className="trade-section trade-target-section">
                  <h4>Trade with:</h4>
                  <div className="player-selector">
                    {otherPlayers.map((player) => (
                      <button
                        key={player.id}
                        type="button"
                        className={`player-select-btn ${selectedPlayerId === player.id ? 'selected' : ''}`}
                        style={{ borderColor: selectedPlayerId === player.id ? player.color : undefined }}
                        onClick={() => setSelectedPlayerId(player.id)}
                      >
                        <span className="player-select-color" style={{ backgroundColor: player.color }} />
                        {player.name}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="trade-builder">
                  {/* Your offer */}
                  <div className="trade-section">
                    <h4>You Offer:</h4>
                    <div className="resource-selectors">
                      {RESOURCES.map(r => (
                        <div key={r} className="resource-selector">
                          <span className="icon">{RESOURCE_ICONS[r]}</span>
                          <div className="selector-controls">
                            <button onClick={() => updateOffer(r, -1)} disabled={offer[r] === 0}>−</button>
                            <span className="amount">{offer[r]}</span>
                            <button onClick={() => updateOffer(r, 1)} disabled={offer[r] >= myPlayer.resources[r]}>+</button>
                          </div>
                          <span className="available">({myPlayer.resources[r]})</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* What you want */}
                  <div className="trade-section">
                    <h4>You Request:</h4>
                    <div className="resource-selectors">
                      {RESOURCES.map(r => (
                        <div key={r} className="resource-selector">
                          <span className="icon">{RESOURCE_ICONS[r]}</span>
                          <div className="selector-controls">
                            <button onClick={() => updateRequest(r, -1)} disabled={request[r] === 0}>−</button>
                            <span className="amount">{request[r]}</span>
                            <button onClick={() => updateRequest(r, 1)}>+</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <button
                  className="propose-btn"
                  onClick={handleProposeTrade}
                  disabled={!isMyTurn || !selectedPlayerId}
                >
                  📢 Propose Trade {selectedPlayerId ? `to ${gameState.players.find(p => p.id === selectedPlayerId)?.name}` : ''}
                </button>
              </>
            )}
          </>
        ) : (
          <div className="bank-trade">
            {/* Show available ports */}
            {gameState.myPorts && gameState.myPorts.length > 0 && (
              <div className="my-ports">
                <h4>🚢 Your Ports:</h4>
                <div className="port-list">
                  {gameState.myPorts.map((port, idx) => (
                    <span key={idx} className="port-badge">
                      {port.icon} {port.ratio}:1 {port.resource ? port.resource : 'any'}
                    </span>
                  ))}
                </div>
              </div>
            )}
            
            <p className="bank-info">
              Select a resource to trade. Your rate depends on ports you own!
            </p>
            
            <div className="bank-trade-builder">
              <div className="bank-section">
                <h4>Give:</h4>
                <div className="bank-options">
                  {RESOURCES.map(r => {
                    const ratio = gameState.tradeRatios?.[r] || 4;
                    const hasEnough = myPlayer.resources[r] >= ratio;
                    return (
                      <button
                        key={r}
                        className={`bank-resource ${bankGive === r ? 'selected' : ''} ${ratio < 4 ? 'has-port' : ''}`}
                        onClick={() => setBankGive(r)}
                        disabled={!hasEnough}
                      >
                        <span className="icon">{RESOURCE_ICONS[r]}</span>
                        <span className="count">{myPlayer.resources[r]}</span>
                        <span className="ratio">{ratio}:1</span>
                      </button>
                    );
                  })}
                </div>
              </div>
              
              <div className="bank-arrow">→</div>
              
              <div className="bank-section">
                <h4>Receive (1x):</h4>
                <div className="bank-options">
                  {RESOURCES.map(r => (
                    <button
                      key={r}
                      className={`bank-resource ${bankGet === r ? 'selected' : ''}`}
                      onClick={() => setBankGet(r)}
                      disabled={r === bankGive}
                    >
                      <span className="icon">{RESOURCE_ICONS[r]}</span>
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <button 
              className="bank-trade-btn"
              onClick={handleBankTrade}
              disabled={!bankGive || !bankGet || bankGive === bankGet || myPlayer.resources[bankGive] < (gameState.tradeRatios?.[bankGive] || 4)}
            >
              🏦 Complete Trade {bankGive && `(${gameState.tradeRatios?.[bankGive] || 4}:1)`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default TradeModal;

