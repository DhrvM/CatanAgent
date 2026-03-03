from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional, Tuple

from SocketClient import CatanSocketClient
from GeneralTools import Action, build_action_menu
from ollama_client import OllamaChat, OllamaConfig

SYSTEM_PROMPT = """You are a strong Settlers of Catan bot.

You are given a JSON object with:
- a compact view of the game state from your perspective
- an array "actions", where each item has:
  { "type": <string>, "payload": <object>, "score": <number> }.

Your job is to choose EXACTLY ONE action index to execute now.
Higher "score" generally means a stronger action according to a rules-based heuristic
(e.g. good city spots, useful dev-card buys, sensible trades).

You must return ONLY valid JSON using this exact schema:

{"index": <integer>}

Rules:
- index must be an integer between 0 and len(actions)-1
- do not output any extra keys, comments, or text
- prefer higher-scoring actions when they clearly improve winning chances
- value building strong production (settlements/cities), good trades, and dev cards
- avoid obviously bad trades that give away value without helping your plans
- if all options look bad or unclear, pick the safest one (often endTurn)
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
            "tradeRatios": state.get("tradeRatios"),
            "devCards": (me or {}).get("developmentCards"),
            "newDevCards": (me or {}).get("newDevCards"),
        },
    }


def actions_to_json(actions: List[Action]) -> List[Dict[str, Any]]:
    return [{"type": a.type, "payload": a.payload, "score": round(float(a.score), 3)} for a in actions]


def _pick_by_heuristic(actions: List[Action]) -> int:
    """
    Fast, deterministic fallback that never blocks.
    Chooses highest score, with a light type preference to break ties.
    """
    if not actions:
        return -1

    type_bonus = {
        # building / points
        "upgradeToCity": 5.0,
        "placeSettlement": 4.0,
        "placeRoad": 2.0,
        "buyDevCard": 1.5,
        # turn flow
        "rollDice": 10.0,
        "discardCards": 10.0,
        "moveRobber": 3.0,
        "endSpecialBuild": 2.0,
        # trades
        "respondToTrade": 1.0,
        "bankTrade": 0.5,
        # safe fallback
        "endTurn": -1.0,
    }

    best_i = 0
    best_val = float("-inf")
    for i, a in enumerate(actions):
        try:
            s = float(a.score)
        except Exception:
            s = 0.0
        val = s + type_bonus.get(a.type, 0.0)
        if val > best_val:
            best_val = val
            best_i = i
    return best_i


def _should_use_ollama(actions: List[Action]) -> bool:
    """
    Use Ollama only when it adds value and won't risk stalling the agent loop.

    Small menus are better handled deterministically.
    Trade menus are also handled deterministically to avoid model stalls.
    """
    if not actions:
        return False

    if len(actions) <= 6:
        return False

    types = {a.type for a in actions}
    if any(t in types for t in ("bankTrade", "respondToTrade", "counterTrade", "cancelTrade")):
        return False

    return True


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

    # IMPORTANT: when bank trades appear, the menu can get large.
    # Some local models stall on long JSON lists. In that case, do not call the model.
    if len(actions) >= 25:
        return _pick_by_heuristic(actions)

    if not _should_use_ollama(actions):
        return _pick_by_heuristic(actions)

    msg = {"state": compact_state(state), "actions": actions_to_json(actions)}
    try:
        out = ollama.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(msg)},
            ],
            json_only=True,
        )
    except Exception:
        # Never freeze the agent on LLM issues.
        return _pick_by_heuristic(actions)

    obj = OllamaChat.safe_json_loads(out)
    if not isinstance(obj, dict) or not isinstance(obj.get("index"), int):
        return _pick_by_heuristic(actions)

    idx = int(obj["index"])
    if idx < 0 or idx >= len(actions):
        return _pick_by_heuristic(actions)
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
    GAME_CODE = "E73987"  # change if you want
    PLAYER_NAME = "OllamaBot"

    client = CatanSocketClient(SERVER_URL)
    client.connect()
    client.join_game(GAME_CODE, PLAYER_NAME)

    # Keep LLM responsive: don't let a slow generation stall the whole agent loop.
    ollama = OllamaChat(OllamaConfig(model="qwen3:8b", timeout_s=12, num_ctx=3072))

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

        # If an aggressive action (like a trade) keeps failing, avoid getting stuck:
        # fall back to endTurn next loop rather than spamming invalid moves.
        if not resp.get("success") and chosen.type != "endTurn":
            print("Last action failed; will bias toward ending turn next cycle.")

        time.sleep(0.15)


if __name__ == "__main__":
    main()