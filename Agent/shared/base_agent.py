"""
BaseAgent — abstract base class for all agents in the multi-agent system.

Enforces the communication topology defined in ALLOWED_CHANNELS.
Each agent holds direct references to its allowed peers and invokes
methods on them via call_agent().
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List

from Agent.shared.scratchpad import Scratchpad, AgentMessage


# ── Communication topology ────────────────────────────────────────

ALLOWED_CHANNELS: Dict[str, set] = {
    "strategy":    {"development", "trading", "risk"},
    "development": {"strategy", "risk"},
    "risk":        {"strategy", "development"},
    "trading":     {"strategy"},
}


class BaseAgent(ABC):
    """
    Abstract base for Strategy, Development, Trading, and Risk agents.

    Provides:
      - Peer registration with topology enforcement
      - call_agent() for direct method invocation on peers
      - send_message() / get_messages() for async scratchpad messaging
    """

    def __init__(self, name: str, scratchpad: Scratchpad) -> None:
        self.name = name
        self.scratchpad = scratchpad
        self._peers: Dict[str, "BaseAgent"] = {}

    # ── Peer management ───────────────────────────────────────────

    def register_peer(self, agent: "BaseAgent") -> None:
        """
        Register a peer agent.  Only agents in ALLOWED_CHANNELS for
        this agent's name are accepted.
        """
        allowed = ALLOWED_CHANNELS.get(self.name, set())
        if agent.name in allowed:
            self._peers[agent.name] = agent
        else:
            print(
                f"[BaseAgent] WARNING: {self.name} tried to register "
                f"peer {agent.name}, which is not in allowed channels."
            )

    # ── Direct method invocation on peers ─────────────────────────

    def call_agent(self, peer_name: str, method: str, **kwargs) -> Any:
        """
        Call a method on a peer agent.

        Raises PermissionError if the channel is not allowed.
        Raises ValueError   if the peer was never registered.
        Raises AttributeError if the peer doesn't have the method.
        """
        allowed = ALLOWED_CHANNELS.get(self.name, set())
        if peer_name not in allowed:
            raise PermissionError(
                f"{self.name} is not allowed to talk to {peer_name}"
            )

        peer = self._peers.get(peer_name)
        if not peer:
            raise ValueError(
                f"Peer '{peer_name}' not registered on {self.name}. "
                f"Registered peers: {list(self._peers.keys())}"
            )

        fn = getattr(peer, method, None)
        if fn is None or not callable(fn):
            raise AttributeError(
                f"{peer_name} has no callable method '{method}'"
            )

        return fn(**kwargs)

    # ── Scratchpad messaging ──────────────────────────────────────

    def send_message(
        self, to: str, msg_type: str, content: Dict[str, Any],
    ) -> None:
        """
        Write a message to the scratchpad.
        Enforces allowed channels.
        """
        allowed = ALLOWED_CHANNELS.get(self.name, set())
        if to not in allowed:
            raise PermissionError(
                f"{self.name} cannot send messages to {to}"
            )

        self.scratchpad.append_message(AgentMessage(
            from_agent=self.name,
            to_agent=to,
            message_type=msg_type,
            content=content,
            timestamp=time.time(),
        ))

    def get_messages(self) -> List[AgentMessage]:
        """Read all messages addressed to this agent."""
        return self.scratchpad.get_messages_for(self.name)
