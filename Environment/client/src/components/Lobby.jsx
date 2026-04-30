import { useState } from 'react';
import RulesModal from './RulesModal';
import './Lobby.css';

function defaultBenchmarkState() {
  return {
    enabled: false,
    runId: '',
    benchmarkId: 'catan-benchmark-v1',
    taskId: '',
    gameType: 'full-game',
    mapLayoutId: 'map-a',
    opponentPolicySet: 'mixed-opponents',
    scenarioTags: '',
    baselineAgentId: '',
    agentId: '',
    agentVersion: '1.0.0',
    playerKey: '',
    baseline: false,
    startingSeat: '',
  };
}

function Lobby({ onCreateGame, onJoinGame, error, setError, onOpenObservationDeck }) {
  const [mode, setMode] = useState(null); // null, 'create', 'join'
  const [playerName, setPlayerName] = useState('');
  const [gameCode, setGameCode] = useState('');
  const [showRules, setShowRules] = useState(false);
  const [isExtended, setIsExtended] = useState(false);
  const [enableSpecialBuild, setEnableSpecialBuild] = useState(true);
  const [benchmarkConfig, setBenchmarkConfig] = useState(defaultBenchmarkState());

  const updateBenchmarkField = (field, value) => {
    setBenchmarkConfig(current => ({
      ...current,
      [field]: value,
    }));
  };

  const buildBenchmarkPayload = () => {
    if (!benchmarkConfig.enabled) return null;

    const trimmedAgentId = benchmarkConfig.agentId.trim() || playerName.trim();
    const trimmedRunId = benchmarkConfig.runId.trim();
    const scenarioTags = benchmarkConfig.scenarioTags
      .split(',')
      .map(tag => tag.trim())
      .filter(Boolean);

    return {
      enabled: true,
      runId: trimmedRunId || undefined,
      benchmarkId: benchmarkConfig.benchmarkId.trim() || 'catan-benchmark-v1',
      taskId: benchmarkConfig.taskId.trim() || null,
      gameType: benchmarkConfig.gameType || 'full-game',
      mapLayoutId: benchmarkConfig.mapLayoutId.trim() || 'map-a',
      opponentPolicySet: benchmarkConfig.opponentPolicySet.trim() || 'mixed-opponents',
      scenarioTags,
      baselineAgentId: benchmarkConfig.baselineAgentId.trim() || null,
      player: {
        playerKey: benchmarkConfig.playerKey.trim() || undefined,
        agentId: trimmedAgentId,
        agentVersion: benchmarkConfig.agentVersion.trim() || '1.0.0',
        baseline: benchmarkConfig.baseline,
        startingSeat: benchmarkConfig.startingSeat === '' ? undefined : Number(benchmarkConfig.startingSeat),
      },
    };
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    
    if (!playerName.trim()) {
      setError('Please enter your name');
      return;
    }
    
    if (mode === 'create') {
      onCreateGame(playerName.trim(), isExtended, enableSpecialBuild, buildBenchmarkPayload());
    } else if (mode === 'join') {
      if (!gameCode.trim()) {
        setError('Please enter a game code');
        return;
      }
      onJoinGame(gameCode.trim().toUpperCase(), playerName.trim(), buildBenchmarkPayload());
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
            
            <h2>{mode === 'create' ? 'Create New Game' : 'Join Game'}</h2>
            
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

            <div className="form-group special-build-option benchmark-section">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={benchmarkConfig.enabled}
                  onChange={(e) => updateBenchmarkField('enabled', e.target.checked)}
                />
                <span className="checkbox-text">
                  <span className="checkbox-title">Benchmark-enabled game</span>
                  <span className="checkbox-desc">Attach benchmark metadata and telemetry to this player slot.</span>
                </span>
              </label>
            </div>

            {benchmarkConfig.enabled && (
              <div className="benchmark-grid">
                <div className="form-group">
                  <label htmlFor="runId">Run ID</label>
                  <input
                    id="runId"
                    type="text"
                    value={benchmarkConfig.runId}
                    onChange={(e) => updateBenchmarkField('runId', e.target.value)}
                    placeholder="run-001"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="benchmarkId">Benchmark ID</label>
                  <input
                    id="benchmarkId"
                    type="text"
                    value={benchmarkConfig.benchmarkId}
                    onChange={(e) => updateBenchmarkField('benchmarkId', e.target.value)}
                    placeholder="catan-benchmark-v1"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="agentId">Agent ID</label>
                  <input
                    id="agentId"
                    type="text"
                    value={benchmarkConfig.agentId}
                    onChange={(e) => updateBenchmarkField('agentId', e.target.value)}
                    placeholder="baseline-bot"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="agentVersion">Agent Version</label>
                  <input
                    id="agentVersion"
                    type="text"
                    value={benchmarkConfig.agentVersion}
                    onChange={(e) => updateBenchmarkField('agentVersion', e.target.value)}
                    placeholder="1.0.0"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="mapLayoutId">Map Layout ID</label>
                  <input
                    id="mapLayoutId"
                    type="text"
                    value={benchmarkConfig.mapLayoutId}
                    onChange={(e) => updateBenchmarkField('mapLayoutId', e.target.value)}
                    placeholder="map-a"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="opponentPolicySet">Opponent Policy Set</label>
                  <input
                    id="opponentPolicySet"
                    type="text"
                    value={benchmarkConfig.opponentPolicySet}
                    onChange={(e) => updateBenchmarkField('opponentPolicySet', e.target.value)}
                    placeholder="mixed-opponents"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="taskId">Task ID</label>
                  <input
                    id="taskId"
                    type="text"
                    value={benchmarkConfig.taskId}
                    onChange={(e) => updateBenchmarkField('taskId', e.target.value)}
                    placeholder="Leave blank for full-game benchmark"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="gameType">Game Type</label>
                  <input
                    id="gameType"
                    type="text"
                    value={benchmarkConfig.gameType}
                    onChange={(e) => updateBenchmarkField('gameType', e.target.value)}
                    placeholder="full-game"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="baselineAgentId">Baseline Agent ID</label>
                  <input
                    id="baselineAgentId"
                    type="text"
                    value={benchmarkConfig.baselineAgentId}
                    onChange={(e) => updateBenchmarkField('baselineAgentId', e.target.value)}
                    placeholder="baseline-bot"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="playerKey">Player Key</label>
                  <input
                    id="playerKey"
                    type="text"
                    value={benchmarkConfig.playerKey}
                    onChange={(e) => updateBenchmarkField('playerKey', e.target.value)}
                    placeholder="run-001:baseline"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="startingSeat">Starting Seat</label>
                  <input
                    id="startingSeat"
                    type="number"
                    min="0"
                    value={benchmarkConfig.startingSeat}
                    onChange={(e) => updateBenchmarkField('startingSeat', e.target.value)}
                    placeholder="0"
                  />
                </div>

                <div className="form-group">
                  <label htmlFor="scenarioTags">Scenario Tags</label>
                  <input
                    id="scenarioTags"
                    type="text"
                    value={benchmarkConfig.scenarioTags}
                    onChange={(e) => updateBenchmarkField('scenarioTags', e.target.value)}
                    placeholder="alternate-map, resource-scarcity"
                  />
                </div>

                <div className="form-group benchmark-checkbox-row">
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={benchmarkConfig.baseline}
                      onChange={(e) => updateBenchmarkField('baseline', e.target.checked)}
                    />
                    <span className="checkbox-text">
                      <span className="checkbox-title">This player is the baseline</span>
                      <span className="checkbox-desc">Use this when creating or joining with the reference agent.</span>
                    </span>
                  </label>
                </div>
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
            
            {mode === 'join' && (
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
              {mode === 'create' ? 'Create Game' : 'Join Game'}
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
