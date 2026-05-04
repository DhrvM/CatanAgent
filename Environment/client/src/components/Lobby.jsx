import { useState } from 'react';
import RulesModal from './RulesModal';
import './Lobby.css';

function Lobby({ onCreateGame, onJoinGame, onObserveGame, error, setError, onOpenObservationDeck, onOpenSpectatorMode }) {
  const [mode, setMode] = useState(null); // null, 'create', 'join', 'observe'
  const [playerName, setPlayerName] = useState('');
  const [gameCode, setGameCode] = useState('');
  const [showRules, setShowRules] = useState(false);
  const [isExtended, setIsExtended] = useState(false);
  const [enableSpecialBuild, setEnableSpecialBuild] = useState(true);

  const handleSubmit = (e) => {
    e.preventDefault();
    
    if (mode !== 'observe' && !playerName.trim()) {
      setError('Please enter your name');
      return;
    }
    
    if (mode === 'create') {
      onCreateGame(playerName.trim(), isExtended, enableSpecialBuild);
    } else if (mode === 'join') {
      if (!gameCode.trim()) {
        setError('Please enter a game code');
        return;
      }
      onJoinGame(gameCode.trim().toUpperCase(), playerName.trim());
    } else if (mode === 'observe') {
      if (!gameCode.trim()) {
        setError('Please enter a game code');
        return;
      }
      onObserveGame(gameCode.trim().toUpperCase());
    }
  };

  return (
    <div className="lobby">
      <div className="lobby-bg"></div>
      
      {/* Rules Button */}
      <button 
        className="rules-btn"
        onClick={() => setShowRules(true)}
      >
        📜 Rules
      </button>
      
      <div className="lobby-content">
        <div className="lobby-header">
          <h1>CATAN</h1>
          <p className="subtitle">Online Multiplayer</p>
        </div>

        {mode === null ? (
          <div className="lobby-menu fade-in">
            <button 
              className="menu-btn create-btn"
              onClick={() => setMode('create')}
            >
              <span className="btn-icon">🏝️</span>
              Create New Game
            </button>
            
            <button 
              className="menu-btn join-btn"
              onClick={() => setMode('join')}
            >
              <span className="btn-icon">🚢</span>
              Join Game
            </button>

            <button
              type="button"
              className="menu-btn join-btn"
              onClick={onOpenObservationDeck}
            >
              <span className="btn-icon">Deck</span>
              Benchmark Deck
            </button>

            <button
              type="button"
              className="menu-btn join-btn"
              onClick={() => {
                onOpenSpectatorMode();
                setMode('observe');
              }}
            >
              <span className="btn-icon">👀</span>
              Spectate Game
            </button>
          </div>
        ) : (
          <form className="lobby-form fade-in" onSubmit={handleSubmit}>
            <button 
              type="button" 
              className="back-btn"
              onClick={() => { setMode(null); setError(null); }}
            >
              ← Back
            </button>
            
            <h2>{mode === 'create' ? 'Create New Game' : mode === 'join' ? 'Join Game' : 'Spectate Game'}</h2>
            
            {mode !== 'observe' && (
              <div className="form-group">
                <label htmlFor="playerName">Your Name</label>
                <input
                  id="playerName"
                  type="text"
                  value={playerName}
                  onChange={(e) => setPlayerName(e.target.value)}
                  placeholder="Enter your name"
                  maxLength={20}
                  autoFocus
                />
              </div>
            )}

            {mode === 'create' && (
              <>
                <div className="form-group game-mode-group">
                  <label>Game Mode</label>
                  <div className="game-mode-options">
                    <button
                      type="button"
                      className={`mode-option ${!isExtended ? 'active' : ''}`}
                      onClick={() => setIsExtended(false)}
                    >
                      <span className="mode-icon">🎲</span>
                      <span className="mode-label">Standard</span>
                      <span className="mode-desc">2-4 Players</span>
                    </button>
                    <button
                      type="button"
                      className={`mode-option ${isExtended ? 'active' : ''}`}
                      onClick={() => setIsExtended(true)}
                    >
                      <span className="mode-icon">👥</span>
                      <span className="mode-label">Extended</span>
                      <span className="mode-desc">2-6 Players</span>
                    </button>
                  </div>
                </div>
                
                {/* Special Building Phase option - only for extended mode */}
                {isExtended && (
                  <div className="form-group special-build-option">
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        checked={enableSpecialBuild}
                        onChange={(e) => setEnableSpecialBuild(e.target.checked)}
                      />
                      <span className="checkbox-text">
                        <span className="checkbox-title">🏗️ Special Building Phase</span>
                        <span className="checkbox-desc">Allow all players to build after each turn</span>
                      </span>
                    </label>
                  </div>
                )}
              </>
            )}
            
            {(mode === 'join' || mode === 'observe') && (
              <div className="form-group">
                <label htmlFor="gameCode">Game Code</label>
                <input
                  id="gameCode"
                  type="text"
                  value={gameCode}
                  onChange={(e) => setGameCode(e.target.value.toUpperCase())}
                  placeholder="Enter 6-letter code"
                  maxLength={6}
                  className="code-input"
                />
              </div>
            )}
            
            {error && <div className="error-message">{error}</div>}
            
            <button type="submit" className="submit-btn">
              {mode === 'create' ? 'Create Game' : mode === 'join' ? 'Join Game' : 'Start Spectating'}
            </button>
          </form>
        )}

        <div className="lobby-footer">
          <p>2-6 Players • First to 10 Victory Points Wins</p>
          <p className="credits">Created by <span className="creator-name">Viral Doshi</span></p>
        </div>
      </div>
      
      {/* Rules Modal */}
      {showRules && <RulesModal onClose={() => setShowRules(false)} />}
    </div>
  );
}

export default Lobby;
