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


def _count_dev_cards(dev_cards: Any) -> int:
    # dev_cards might be list, dict, or int depending on masking
    if dev_cards is None:
        return 0
    if isinstance(dev_cards, int):
        return dev_cards
    if isinstance(dev_cards, list):
        return len(dev_cards)
    if isinstance(dev_cards, dict):
        # if stored as {"knight":2,...}
        return sum(int(v) for v in dev_cards.values())
    return 0

def _dev_breakdown_for_self(dev_cards: Any) -> Dict[str, int]:
    """
    Normalize self dev cards into a {type: count} dict.
    Handles common shapes:
      - list of strings: ["knight","victoryPoint",...]
      - list of objects: [{"type":"knight"}, ...]
      - dict already: {"knight":2,...}
    """
    if dev_cards is None:
        return {}

    if isinstance(dev_cards, dict):
        return {str(k): int(v) for k, v in dev_cards.items()}

    counts: Dict[str, int] = {}
    if isinstance(dev_cards, list):
        for item in dev_cards:
            if isinstance(item, str):
                t = item
            elif isinstance(item, dict):
                # common keys
                t = item.get("type") or item.get("card") or item.get("name")
                if t is None:
                    t = "unknown"
            else:
                t = "unknown"
            counts[t] = counts.get(t, 0) + 1
    else:
        # unexpected shape
        counts["unknown"] = _count_dev_cards(dev_cards)

    return counts

def GetPlayerDevCardsAll(client: CatanSocketClient) -> Dict[str, Any]:
    """
    Fair view:
      - self: dev breakdown + total
      - others: total only
    """
    state = client.latest_state() or client.wait_for_state(timeout_s=5.0)
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
        dev = p.get("developmentCards")

        if idx == my_index:
            breakdown = _dev_breakdown_for_self(dev)
            total = sum(breakdown.values())
            out.append({
                "playerId": pid,
                "name": name,
                "isSelf": True,
                "devCards": breakdown,
                "totalDevCards": total,
            })
        else:
            # masked: usually an int count
            out.append({
                "playerId": pid,
                "name": name,
                "isSelf": False,
                "totalDevCards": _count_dev_cards(dev),
            })

    return {"ok": True, "players": out}