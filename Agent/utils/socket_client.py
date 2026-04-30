import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

import socketio


class CatanSocketClient:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)

        self._latest_state: Optional[Dict[str, Any]] = None
        self._state_lock = threading.Lock()
        self._state_event = threading.Event()

        # Event history for MoveSummarizer
        self._event_history: deque[Dict[str, Any]] = deque(maxlen=200)
        self._event_lock = threading.Lock()

        self.game_code: Optional[str] = None
        self.player_id: Optional[str] = None

        @self.sio.event
        def connect():
            print("[socket] connected")

        @self.sio.event
        def disconnect():
            print("[socket] disconnected")

        # Server broadcasts player-specific views on this event
        @self.sio.on("gameState")
        def on_game_state(state: Dict[str, Any]):
            with self._state_lock:
                self._latest_state = state
            self._state_event.set()

        # ── broadcast event listeners for history tracking ──
        for evt in (
            "diceRolled", "settlementPlaced", "roadPlaced", "cityBuilt",
            "devCardPlayed", "tradeProposed", "tradeAccepted",
            "tradeDeclined", "tradeCancelled", "robberMoved",
            "resourcesDistributed", "stealResult", "turnEnded",
            "gameStarted", "playerJoined", "playerDisconnected",
            "specialBuildingPhaseStarted", "specialBuildingPhaseEnded",
        ):
            self.sio.on(evt, lambda data, _evt=evt: self._record_event(_evt, data))

    def connect(self):
        self.sio.connect(self.server_url, transports=["websocket"])

    def close(self):
        try:
            self.sio.disconnect()
        except Exception:
            pass

    def create_game(self, player_name: str, is_extended: bool = False, enable_special_build: bool = True):
        """
        Mirrors server: socket.on('createGame', ({ playerName, isExtended, enableSpecialBuild }, callback) => ...)
        """
        ack = self.sio.call(
            "createGame",
            {"playerName": player_name, "isExtended": is_extended, "enableSpecialBuild": enable_special_build},
            timeout=10,
        )
        if not ack.get("success"):
            raise RuntimeError(f"createGame failed: {ack}")
        self.game_code = ack["gameCode"]
        self.player_id = ack["playerId"]
        with self._state_lock:
            self._latest_state = ack.get("gameState")
        self._state_event.set()
        return ack

    def join_game(self, game_code: str, player_name: str):
        """
        Mirrors server: socket.on('joinGame', ({ gameCode, playerName }, callback) => ...)
        """
        ack = self.sio.call("joinGame", {"gameCode": game_code, "playerName": player_name}, timeout=10)
        if not ack.get("success"):
            raise RuntimeError(f"joinGame failed: {ack}")
        self.game_code = ack["gameCode"]
        self.player_id = ack["playerId"]
        with self._state_lock:
            self._latest_state = ack.get("gameState")
        self._state_event.set()
        return ack

    def latest_state(self) -> Optional[Dict[str, Any]]:
        with self._state_lock:
            return self._latest_state

    def wait_for_state(self, timeout_s: float = 5.0) -> Dict[str, Any]:
        ok = self._state_event.wait(timeout=timeout_s)
        if not ok:
            raise TimeoutError("Timed out waiting for gameState")
        st = self.latest_state()
        if st is None:
            raise RuntimeError("No state available")
        return st

    def end_turn(self):
        return self.sio.call("endTurn", timeout=10)

    def call(self, event: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 10):
        return self.sio.call(event, payload or {}, timeout=timeout)

    # ── event history ────────────────────────────────────────
    def _record_event(self, event_type: str, data: Any) -> None:
        with self._event_lock:
            self._event_history.append({
                "type": event_type,
                "data": data if isinstance(data, dict) else {},
                "timestamp": time.time(),
            })

    def get_events_since(self, timestamp: float) -> List[Dict[str, Any]]:
        """Return all events recorded after *timestamp*."""
        with self._event_lock:
            return [e for e in self._event_history if e["timestamp"] > timestamp]

    def get_all_events(self) -> List[Dict[str, Any]]:
        """Return all recorded events."""
        with self._event_lock:
            return list(self._event_history)
