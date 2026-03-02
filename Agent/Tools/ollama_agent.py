from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from SocketClient import CatanSocketClient
from catan_action_menu import Action, build_action_menu
from ollama_client import OllamaChat, OllamaConfig

SYSTEM_PROMPT = """You are a Catan-playing bot.

You must choose EXACTLY ONE action from the provided action list.
Return ONLY valid JSON using this schema:

{"index": <integer>}

Rules:
- index must be a valid index into the actions array (0..len(actions)-1)
- do not output any extra keys or text
- if unsure, choose the safest option (often endTurn, or in setup choose index 0)
"""


# ----------------------------
# Small helpers
# ----------------------------
def compact_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """Keep state small + stable for the model."""
    me = None
    players = state.get("players")
    myi = state.get("myIndex")
    if isinstance(players, list) and isinstance(myi, int) and 0 <= myi < len(players):
        me = players[myi]

    return {
        "phase": state.get("phase"),
        "setupPhase": state.get("setupPhase"),
        "turnPhase": state.get("turnPhase"),
        "currentPlayerIndex": state.get("currentPlayerIndex"),
        "myIndex": state.get("myIndex"),
        "robber": state.get("robber"),
        "diceRoll": state.get("diceRoll"),
        "me": {
            "name": (me or {}).get("name"),
            "resources": (me or {}).get("resources"),
            "victoryPoints": (me or {}).get("victoryPoints"),
            "tradeRatios": (me or {}).get("tradeRatios"),
            "devCards": (me or {}).get("developmentCards"),
            "newDevCards": (me or {}).get("newDevCards"),
        },
    }


def actions_to_json(actions: List[Action]) -> List[Dict[str, Any]]:
    return [{"type": a.type, "payload": a.payload, "score": round(float(a.score), 3)} for a in actions]


def safe_call(
    client: CatanSocketClient,
    event: str,
    payload: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Socket.io call wrapper.
    - Treat payload None OR {} as "no payload" (important for some server handlers).
    - Always return a dict-shaped response.
    """
    try:
        if payload is None or payload == {}:
            resp = client.sio.call(event, timeout=timeout)
        else:
            resp = client.sio.call(event, payload, timeout=timeout)

        if isinstance(resp, dict):
            return resp
        return {"success": True, "raw": resp}
    except Exception as e:
        return {"success": False, "error": str(e)}


def try_actions_until_success(
    client: CatanSocketClient,
    actions: List[Action],
    max_tries: int = 300,
    timeout: int = 10,
) -> Tuple[Optional[Action], Dict[str, Any]]:
    """
    Try actions in order until one succeeds. Returns (action_that_worked, resp).
    Useful when we don't have a "legal moves" API (setup + robber).
    """
    last_resp: Dict[str, Any] = {"success": False, "error": "no attempts"}
    for i, a in enumerate(actions[:max_tries]):
        resp = safe_call(client, a.type, a.payload, timeout=timeout)
        print("TRY", i, a.type, a.payload, "=>", resp)
        last_resp = resp
        if isinstance(resp, dict) and resp.get("success"):
            return a, resp
    return None, last_resp


def maybe_choose_robber_victim(client: CatanSocketClient, hex_key: str) -> Optional[str]:
    """
    Uses your server utility event getPlayersOnHex to pick a victim with resources if possible.
    Returns a playerId string or None.
    """
    resp = safe_call(client, "getPlayersOnHex", {"hexKey": hex_key}, timeout=10)
    if not resp.get("success"):
        return None

    players = resp.get("players")
    if not isinstance(players, list):
        return None

    candidates = [
        p
        for p in players
        if isinstance(p, dict) and p.get("hasResources") and isinstance(p.get("id"), str)
    ]
    if not candidates:
        return None
    return candidates[0]["id"]


def pick_action_index(ollama: OllamaChat, state: Dict[str, Any], actions: List[Action]) -> int:
    """
    Ask Ollama to pick one action index.
    Robustness:
    - If only 1 action, return 0 (no model call).
    - If invalid JSON / invalid index, default to 0.
    """
    if not actions:
        return -1
    if len(actions) == 1:
        return 0

    msg = {"state": compact_state(state), "actions": actions_to_json(actions)}
    out = ollama.chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(msg)},
        ],
        json_only=True,
    )

    obj = OllamaChat.safe_json_loads(out)
    if not isinstance(obj, dict) or not isinstance(obj.get("index"), int):
        return 0

    idx = int(obj["index"])
    if idx < 0 or idx >= len(actions):
        return 0
    return idx


# ----------------------------
# Phase handlers
# ----------------------------
def handle_setup_turn(
    client: CatanSocketClient,
    actions: List[Action],
    last_setup_settlement: Optional[str],
) -> Tuple[bool, Optional[str]]:
    """
    Setup logic:
      - If no settlement placed yet: brute-force settlement placements until success.
      - Else: brute-force road placements until success, then advanceSetup.
    Returns (did_something, new_last_setup_settlement).
    """
    # Settlement placement
    if last_setup_settlement is None:
        settlement_actions = [a for a in actions if a.type == "placeSettlement"]
        if not settlement_actions:
            return False, last_setup_settlement

        a_ok, resp_ok = try_actions_until_success(client, settlement_actions, max_tries=600, timeout=10)
        if a_ok and resp_ok.get("success"):
            return True, a_ok.payload.get("vertexKey")
        return False, last_setup_settlement

    # Road placement + advance
    road_actions = [a for a in actions if a.type == "placeRoad"]
    if not road_actions:
        return False, last_setup_settlement

    a_ok, resp_ok = try_actions_until_success(client, road_actions, max_tries=600, timeout=10)
    if a_ok and resp_ok.get("success"):
        adv = safe_call(client, "advanceSetup", None, timeout=10)
        print("ACTION advanceSetup =>", adv)
        return True, None

    return False, last_setup_settlement


def handle_robber_turn(
    client: CatanSocketClient,
    actions: List[Action],
) -> bool:
    """
    Robber logic:
      - Create moveRobber attempts with stealFromPlayerId filled in (or None).
      - Try until one succeeds.
    Returns did_something.
    """
    robber_raw = [a for a in actions if a.type == "moveRobber"]
    if not robber_raw:
        return False

    robber_actions: List[Action] = []
    for a in robber_raw:
        hk = a.payload.get("hexKey")
        if isinstance(hk, str):
            victim = maybe_choose_robber_victim(client, hk)
            robber_actions.append(Action("moveRobber", {"hexKey": hk, "stealFromPlayerId": victim}, score=a.score))
        else:
            robber_actions.append(a)

    a_ok, resp_ok = try_actions_until_success(client, robber_actions, max_tries=400, timeout=10)
    print("ACTION moveRobber =>", getattr(a_ok, "payload", None), resp_ok)
    return bool(a_ok and resp_ok.get("success"))


# ----------------------------
# Main loop
# ----------------------------
def main() -> None:
    SERVER_URL = "http://localhost:3001"
    GAME_CODE = "L8LJNY"  # change if you want
    PLAYER_NAME = "OllamaBot"

    client = CatanSocketClient(SERVER_URL)
    client.connect()
    client.join_game(GAME_CODE, PLAYER_NAME)

    ollama = OllamaChat(OllamaConfig(model="qwen3:8b"))

    last_setup_settlement: Optional[str] = None

    print("✅ agent running")

    while True:
        state = client.latest_state()
        if not state:
            time.sleep(0.2)
            continue

        actions = build_action_menu(state, last_setup_settlement)
        if not actions:
            time.sleep(0.2)
            continue

        # Debug
        print(
            "phase", state.get("phase"),
            "setupPhase", state.get("setupPhase"),
            "turnPhase", state.get("turnPhase"),
            "myIndex", state.get("myIndex"),
            "currentPlayerIndex", state.get("currentPlayerIndex"),
            "isMyTurn", state.get("isMyTurn"),
        )
        print("last_setup_settlement", last_setup_settlement)
        print("menu types", [a.type for a in actions[:5]], "… total", len(actions))

        phase = state.get("phase")
        turn_phase = state.get("turnPhase")

        # ----------------------------
        # SETUP (no Ollama)
        # ----------------------------
        if phase == "setup":
            _, last_setup_settlement = handle_setup_turn(client, actions, last_setup_settlement)
            time.sleep(0.15)
            continue

        # ----------------------------
        # FORCED TURN PHASES (no Ollama)
        # ----------------------------
        if turn_phase == "robber":
            handle_robber_turn(client, actions)
            time.sleep(0.15)
            continue

        if turn_phase == "discard":
            # NOTE: proper discard logic depends on your server payload schema.
            # For now, don't do anything destructive.
            print("In discard phase; skipping (need discardCards logic).")
            time.sleep(0.25)
            continue

        if turn_phase == "specialBuild":
            resp = safe_call(client, "endSpecialBuild", {}, timeout=10)
            print("ACTION endSpecialBuild =>", resp)
            time.sleep(0.15)
            continue

        # ----------------------------
        # NORMAL PLAY (Ollama picks)
        # ----------------------------
        idx = pick_action_index(ollama, state, actions)
        if idx < 0:
            time.sleep(0.2)
            continue

        chosen = actions[idx]

        # If the menu ever includes moveRobber here, still ensure victim key is provided
        if chosen.type == "moveRobber":
            hk = chosen.payload.get("hexKey")
            if isinstance(hk, str):
                victim = maybe_choose_robber_victim(client, hk)
                chosen = Action(
                    "moveRobber",
                    {"hexKey": hk, "stealFromPlayerId": victim},
                    score=chosen.score,
                )

        resp = safe_call(client, chosen.type, chosen.payload, timeout=10)
        print("ACTION", chosen.type, chosen.payload, "=>", resp)

        time.sleep(0.15)


if __name__ == "__main__":
    main()