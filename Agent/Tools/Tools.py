from typing import Any, Dict, List
from SocketClient import CatanSocketClient



def GetPlayerResourcesAll(client: CatanSocketClient) -> Dict[str, Any]:
    """
    Fair view:
      - self: full breakdown + total
      - others: total only
    """
    state = client.wait_for_state(timeout_s=5.0)
    players = state.get("players")
    my_index = state.get("myIndex")

    if not isinstance(players, list):
        raise ValueError("gameState missing players list")
    if not isinstance(my_index, int):
        raise ValueError("gameState missing myIndex")

    out: List[Dict[str, Any]] = []
    for idx, p in enumerate(players):
        pid = p.get("id")
        name = p.get("name")
        r = p.get("resources")

        if idx == my_index:
            if not isinstance(r, dict):
                raise ValueError("Expected dict resources for self but got masked value")
            total = sum(int(v) for v in r.values())
            out.append({
                "playerId": pid,
                "name": name,
                "isSelf": True,
                "resources": {
                    "brick": int(r.get("brick", 0)),
                    "lumber": int(r.get("lumber", 0)),
                    "wool": int(r.get("wool", 0)),
                    "grain": int(r.get("grain", 0)),
                    "ore": int(r.get("ore", 0)),
                },
                "totalResources": total,
            })
        else:
            # others are masked: resources is a number (total count)
            total = int(r) if r is not None else 0
            out.append({
                "playerId": pid,
                "name": name,
                "isSelf": False,
                "totalResources": total,
            })

    return {"ok": True, "players": out}


def GetPlayerResourcesById(client: CatanSocketClient, player_id: str) -> Dict[str, Any]:
    """
    Fair view by playerId:
      - if playerId is self: breakdown + total
      - else: total only
    """
    state = client.wait_for_state(timeout_s=5.0)
    players = state.get("players")
    my_index = state.get("myIndex")

    if not isinstance(players, list):
        raise ValueError("gameState missing players list")
    if not isinstance(my_index, int):
        raise ValueError("gameState missing myIndex")

    # find player + index
    target = None
    target_idx = None
    for idx, p in enumerate(players):
        if p.get("id") == player_id:
            target = p
            target_idx = idx
            break

    if target is None:
        return {"ok": False, "error": f"playerId not found: {player_id}"}

    name = target.get("name")
    r = target.get("resources")

    if target_idx == my_index:
        if not isinstance(r, dict):
            raise ValueError("Expected dict resources for self but got masked value")
        total = sum(int(v) for v in r.values())
        return {
            "ok": True,
            "playerId": player_id,
            "name": name,
            "isSelf": True,
            "resources": {
                "brick": int(r.get("brick", 0)),
                "lumber": int(r.get("lumber", 0)),
                "wool": int(r.get("wool", 0)),
                "grain": int(r.get("grain", 0)),
                "ore": int(r.get("ore", 0)),
            },
            "totalResources": total,
        }

    # opponent: masked total
    total = int(r) if r is not None else 0
    return {
        "ok": True,
        "playerId": player_id,
        "name": name,
        "isSelf": False,
        "totalResources": total,
    }