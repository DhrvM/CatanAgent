/**
 * ============================================================================
 * CATAN CLIENT APPLICATION
 * ============================================================================
 * 
 * Main React application for the Catan game client.
 * Handles:
 * - Socket.io connection management
 * - Game state management
 * - Session persistence (localStorage)
 * - Global notifications
 * - Keep-alive pings (for Render free tier)
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { io } from 'socket.io-client';
import Lobby from './components/Lobby';
import GameBoard from './components/GameBoard';
import ObservationDeck from './components/ObservationDeck';
import SpectatorBoard from './components/SpectatorBoard';
import './App.css';

// Server URL resolution order:
//   1. VITE_SERVER_URL (build-time env, set via .env.production or Vercel dashboard)
//   2. Hosted Render backend when served from any non-localhost origin (e.g. Vercel)
//   3. http://localhost:3001 for local `npm run dev`
// This self-healing fallback guarantees production deployments work even when the
// build-time env var is missing or the Vercel "Production Branch" is misconfigured.
const HOSTED_SERVER_URL = 'https://catanagent.onrender.com';
const LOCAL_SERVER_URL = 'http://localhost:3001';
const resolveServerUrl = () => {
  const fromEnv = import.meta.env.VITE_SERVER_URL;
  if (fromEnv) return fromEnv;
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '';
    return isLocal ? LOCAL_SERVER_URL : HOSTED_SERVER_URL;
  }
  return LOCAL_SERVER_URL;
};
const SERVER_URL = resolveServerUrl();

// Keep server alive by pinging every 4 minutes (Render free tier spins down after 15 min)
const KEEP_ALIVE_INTERVAL = 4 * 60 * 1000;

function App() {
  // ============================================================================
  // STATE MANAGEMENT
  // ============================================================================

  const [socket, setSocket] = useState(null);           // Socket.io connection
  const [connected, setConnected] = useState(false);     // Connection status
  const [gameState, setGameState] = useState(null);      // Current game state from server
  const [playerId, setPlayerId] = useState(null);        // This player's unique ID
  const [gameCode, setGameCode] = useState(null);        // Current game room code
  const [error, setError] = useState(null);              // Error messages for display
  const [chatMessages, setChatMessages] = useState([]);  // Chat message history
  const [notifications, setNotifications] = useState([]); // Toast notifications
  const [serverFull, setServerFull] = useState(false);   // Server capacity flag
  const [spectatorEvents, setSpectatorEvents] = useState([]);
  const prevSpectatorStateRef = useRef(null);
  const resolveViewMode = () => {
    if (window.location.hash === '#observation-deck') return 'deck';
    if (window.location.hash === '#spectate') return 'spectate';
    return 'game';
  };
  const [viewMode, setViewMode] = useState(resolveViewMode());

  useEffect(() => {
    const syncViewMode = () => {
      setViewMode(resolveViewMode());
    };

    window.addEventListener('hashchange', syncViewMode);
    return () => window.removeEventListener('hashchange', syncViewMode);
  }, []);

  // ============================================================================
  // SOCKET CONNECTION & EVENT HANDLERS
  // ============================================================================

  useEffect(() => {
    if (viewMode === 'deck') {
      setConnected(true);
      return undefined;
    }

    const newSocket = io(SERVER_URL);
    const appendSpectatorEvent = (event) => {
      setSpectatorEvents(prev => {
        const next = [{ id: `${Date.now()}-${Math.random()}`, timestamp: Date.now(), ...event }, ...prev];
        return next.slice(0, 120);
      });
    };
    const appendLeadershipEvents = (state) => {
      const prev = prevSpectatorStateRef.current;
      prevSpectatorStateRef.current = state;
      if (!prev || !state) return;

      if (prev.longestRoadPlayer !== state.longestRoadPlayer) {
        appendSpectatorEvent({
          type: 'longestRoadChanged',
          from: prev.longestRoadPlayer,
          to: state.longestRoadPlayer,
        });
      }
      if (prev.largestArmyPlayer !== state.largestArmyPlayer) {
        appendSpectatorEvent({
          type: 'largestArmyChanged',
          from: prev.largestArmyPlayer,
          to: state.largestArmyPlayer,
        });
      }
    };

    newSocket.on('connect', () => {
      console.log('Connected to server');
      setConnected(true);
      setServerFull(false);

      // Try to reconnect to existing game
      const savedGame = localStorage.getItem('catanGame');
      if (savedGame) {
        const { gameCode, playerId } = JSON.parse(savedGame);
        newSocket.emit('reconnect', { gameCode, playerId }, (response) => {
          if (response.success) {
            setGameCode(gameCode);
            setPlayerId(playerId);
            setGameState(response.gameState);
          } else {
            localStorage.removeItem('catanGame');
          }
        });
      }
    });

    newSocket.on('disconnect', () => {
      console.log('Disconnected from server');
      setConnected(false);
    });

    newSocket.on('serverFull', ({ message }) => {
      console.log('Server is full:', message);
      setServerFull(true);
      setConnected(false);
    });

    newSocket.on('gameState', (state) => {
      appendLeadershipEvents(state);
      setGameState(state);
    });
    newSocket.on('observerGameState', (state) => {
      appendLeadershipEvents(state);
      setGameState(state);
    });

    newSocket.on('playerJoined', ({ playerName }) => {
      addNotification(`${playerName} joined the game`);
    });

    newSocket.on('playerDisconnected', ({ playerName }) => {
      addNotification(`${playerName} disconnected`);
    });

    newSocket.on('playerReconnected', ({ playerName }) => {
      addNotification(`${playerName} reconnected`);
    });

    newSocket.on('gameStarted', () => {
      addNotification('Game started! Place your first settlement.');
    });

    newSocket.on('diceRolled', ({ roll, playerId: rollerId }) => {
      // Notification handled in GameBoard
      appendSpectatorEvent({ type: 'diceRolled', roll, playerId: rollerId });
    });

    newSocket.on('chatMessage', (msg) => {
      setChatMessages(prev => [...prev, msg]);
    });

    newSocket.on('tradeProposed', ({ from, offer, request }) => {
      appendSpectatorEvent({ type: 'tradeProposed', from, offer, request });
    });

    newSocket.on('tradeAccepted', ({ by }) => {
      addNotification('Trade completed!');
      appendSpectatorEvent({ type: 'tradeAccepted', by });
    });

    newSocket.on('tradeDeclined', ({ by }) => {
      appendSpectatorEvent({ type: 'tradeDeclined', by });
    });

    newSocket.on('tradeCancelled', () => {
      addNotification('Trade cancelled');
      appendSpectatorEvent({ type: 'tradeCancelled' });
    });

    newSocket.on('resourcesDistributed', ({ fromRoll, allGains }) => {
      appendSpectatorEvent({ type: 'resourcesDistributed', fromRoll, allGains });
    });

    newSocket.on('robberMoved', ({ hexKey, by, stealFromPlayerId }) => {
      appendSpectatorEvent({ type: 'robberMoved', hexKey, by, stealFromPlayerId });
    });

    newSocket.on('robberResolved', ({ thief, thiefName, victim, victimName, stolen, resource }) => {
      appendSpectatorEvent({
        type: 'robberResolved',
        thief,
        thiefName,
        victim,
        victimName,
        stolen,
        resource,
      });
    });

    newSocket.on('stealResult', (payload) => {
      appendSpectatorEvent({
        type: 'stealResult',
        resultType: payload?.type,
        resource: payload?.resource,
        otherPlayer: payload?.otherPlayer,
      });
    });

    newSocket.on('settlementPlaced', ({ playerId: by, vertexKey }) => {
      appendSpectatorEvent({ type: 'settlementPlaced', by, vertexKey });
    });

    newSocket.on('roadPlaced', ({ playerId: by, edgeKey }) => {
      appendSpectatorEvent({ type: 'roadPlaced', by, edgeKey });
    });

    newSocket.on('cityBuilt', ({ playerId: by, vertexKey }) => {
      appendSpectatorEvent({ type: 'cityBuilt', by, vertexKey });
    });

    newSocket.on('turnEnded', ({ playerId: by }) => {
      appendSpectatorEvent({ type: 'turnEnded', by });
    });

    newSocket.on('devCardBought', ({ playerId: by }) => {
      appendSpectatorEvent({ type: 'devCardBought', by });
    });

    newSocket.on('devCardPlayed', ({ playerId: by, cardType }) => {
      appendSpectatorEvent({ type: 'devCardPlayed', by, cardType });
    });

    newSocket.on('gameDeleted', ({ message }) => {
      addNotification(message || 'The game has been deleted by the host.');
      setGameState(null);
      setGameCode(null);
      setPlayerId(null);
      setChatMessages([]);
      localStorage.removeItem('catanGame');
    });

    setSocket(newSocket);

    return () => {
      newSocket.close();
    };
  }, [viewMode]);

  // ============================================================================
  // KEEP-ALIVE PING (prevents Render free tier from sleeping)
  // ============================================================================

  useEffect(() => {
    const pingServer = async () => {
      try {
        await fetch(`${SERVER_URL}/ping`);
        console.log('Keep-alive ping sent');
      } catch (err) {
        console.log('Keep-alive ping failed:', err.message);
      }
    };

    // Initial ping
    pingServer();

    // Set up interval
    const interval = setInterval(pingServer, KEEP_ALIVE_INTERVAL);

    return () => clearInterval(interval);
  }, []);

  // ============================================================================
  // NOTIFICATION HELPERS
  // ============================================================================

  /** Add a toast notification that stays scoped to the last two game rounds. */
  const addNotification = useCallback((message, options = {}) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const roundNumber = Number.isFinite(Number(options.roundNumber))
      ? Number(options.roundNumber)
      : Number.isFinite(Number(gameState?.roundNumber))
        ? Number(gameState.roundNumber)
        : null;
    setNotifications(prev => {
      const next = [...prev, { id, message, roundNumber }];
      if (roundNumber === null) return next.slice(-8);
      return next
        .filter(n => n.roundNumber === null || n.roundNumber >= roundNumber - 1)
        .slice(-8);
    });
    setTimeout(() => {
      setNotifications(prev => prev.filter(n => n.id !== id));
    }, 8000);
  }, [gameState?.roundNumber]);

  const dismissNotification = useCallback((id) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  }, []);

  useEffect(() => {
    const roundNumber = Number(gameState?.roundNumber);
    if (!Number.isFinite(roundNumber)) return;
    setNotifications(prev => prev.filter(n => n.roundNumber === null || n.roundNumber >= roundNumber - 1));
  }, [gameState?.roundNumber]);

  // ============================================================================
  // GAME ACTIONS
  // ============================================================================

  /** Create a new game room as the host */
  const handleCreateGame = useCallback((playerName, isExtended = false, enableSpecialBuild = true) => {
    if (!socket) return;

    socket.emit('createGame', { playerName, isExtended, enableSpecialBuild }, (response) => {
      if (response.success) {
        setGameCode(response.gameCode);
        setPlayerId(response.playerId);
        setGameState(response.gameState);
        localStorage.setItem('catanGame', JSON.stringify({
          gameCode: response.gameCode,
          playerId: response.playerId
        }));
      } else {
        setError(response.error);
      }
    });
  }, [socket]);

  /** Join an existing game room using a code */
  const handleJoinGame = useCallback((code, playerName) => {
    if (!socket) return;

    socket.emit('joinGame', { gameCode: code, playerName }, (response) => {
      if (response.success) {
        setGameCode(response.gameCode);
        setPlayerId(response.playerId);
        setGameState(response.gameState);
        localStorage.setItem('catanGame', JSON.stringify({
          gameCode: response.gameCode,
          playerId: response.playerId
        }));
      } else {
        setError(response.error);
      }
    });
  }, [socket]);

  /** Leave the current game and return to lobby */
  const handleLeaveGame = useCallback(() => {
    setGameState(null);
    setGameCode(null);
    setPlayerId(null);
    setChatMessages([]);
    setSpectatorEvents([]);
    prevSpectatorStateRef.current = null;
    localStorage.removeItem('catanGame');
  }, []);

  const openObservationDeck = useCallback(() => {
    window.location.hash = 'observation-deck';
  }, []);

  const openSpectatorMode = useCallback(() => {
    window.location.hash = 'spectate';
  }, []);

  const closeObservationDeck = useCallback(() => {
    window.location.hash = '';
  }, []);

  const handleObserveGame = useCallback((code) => {
    if (!socket) return;
    socket.emit('observeGame', { gameCode: code }, (response) => {
      if (response.success) {
        setSpectatorEvents([]);
        prevSpectatorStateRef.current = null;
        setGameCode(response.gameCode);
        setPlayerId(null);
        setGameState(response.gameState);
      } else {
        setError(response.error || 'Failed to observe game');
      }
    });
  }, [socket]);

  /** Delete the current game (host only) */
  const handleDeleteGame = useCallback(() => {
    if (!socket) return;

    socket.emit('deleteGame', (response) => {
      if (response.success) {
        setGameState(null);
        setGameCode(null);
        setPlayerId(null);
        setChatMessages([]);
        localStorage.removeItem('catanGame');
        addNotification('Game deleted successfully.');
      } else {
        addNotification(response.error || 'Failed to delete game.');
      }
    });
  }, [socket, addNotification]);

  // ============================================================================
  // RENDER
  // ============================================================================

  if (viewMode === 'deck') {
    return (
      <div className="app">
        <ObservationDeck serverUrl={SERVER_URL} onBack={closeObservationDeck} />
      </div>
    );
  }

  if (viewMode === 'spectate' && gameState) {
    return (
      <div className="app">
        <SpectatorBoard
          gameState={gameState}
          gameCode={gameCode}
          spectatorEvents={spectatorEvents}
          onLeave={handleLeaveGame}
        />
      </div>
    );
  }

  // Server at capacity - show retry screen
  if (serverFull) {
    return (
      <div className="loading-screen server-full">
        <div className="loading-content">
          <h1>CATAN</h1>
          <div className="server-full-icon">🏰</div>
          <h2>Server at Capacity</h2>
          <p>Too many players are currently online!</p>
          <p className="server-full-hint">Please try again in a few minutes.</p>
          <button
            className="retry-btn"
            onClick={() => window.location.reload()}
          >
            🔄 Try Again
          </button>
        </div>
      </div>
    );
  }

  // Connecting to server - show loading screen
  if (!connected) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <h1>CATAN</h1>
          <p>Connecting to server...</p>
          <div className="loading-spinner"></div>
        </div>
      </div>
    );
  }

  // No active game - show lobby for creating/joining games
  if (!gameState) {
    return (
      <Lobby
        onCreateGame={handleCreateGame}
        onJoinGame={handleJoinGame}
        onObserveGame={handleObserveGame}
        error={error}
        setError={setError}
        onOpenObservationDeck={openObservationDeck}
        onOpenSpectatorMode={openSpectatorMode}
      />
    );
  }

  // Active game - render the game board
  return (
    <>
      <div className="app">
        <GameBoard
          socket={socket}
          gameState={gameState}
          playerId={playerId}
          gameCode={gameCode}
          chatMessages={chatMessages}
          onLeaveGame={handleLeaveGame}
          onDeleteGame={handleDeleteGame}
          addNotification={addNotification}
        />
      </div>

      {/* Toast notifications - rendered via Portal to document.body for proper z-index */}
      {createPortal(
        <div className="notifications">
          {notifications.map(n => (
            <div key={n.id} className="notification fade-in">
              <span className="notification-message">{n.message}</span>
              <button
                type="button"
                className="notification-dismiss"
                onClick={() => dismissNotification(n.id)}
                aria-label="Dismiss notification"
                title="Dismiss"
              >
                ×
              </button>
            </div>
          ))}
        </div>,
        document.body
      )}
    </>
  );
}

export default App;
