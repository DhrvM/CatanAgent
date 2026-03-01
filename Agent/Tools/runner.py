import time
from SocketClient import CatanSocketClient
from Tools import GetPlayerResourcesAll

SERVER_URL = "http://localhost:3001"
GAME_CODE = "4A59WH"  # <-- change

def is_my_setup_turn(state) -> bool:
    if not state:
        return False
    return (
        state.get("phase") == "setup"
        and state.get("myIndex") is not None
        and state.get("currentPlayerIndex") is not None
        and int(state["myIndex"]) == int(state["currentPlayerIndex"])
    )

def all_vertex_keys_from_state(state):
    # state.hexes is an object keyed by "q,r" -> {q,r,...}
    hexes = state.get("hexes", {}) or {}
    for _, h in hexes.items():
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            yield f"v_{q}_{r}_{d}"

def all_edge_keys_from_state(state):
    hexes = state.get("hexes", {}) or {}
    for _, h in hexes.items():
        q, r = h.get("q"), h.get("r")
        if q is None or r is None:
            continue
        for d in range(6):
            yield f"e_{q}_{r}_{d}"

def place_one_setup_settlement_and_road(client: CatanSocketClient) -> bool:
    """
    Returns True if we successfully placed (settlement+road+advanceSetup).
    """
    state = client.latest_state()
    if not is_my_setup_turn(state):
        return False

    print("My setup turn. Searching for a legal settlement...")

    # 1) Place settlement (setup)
    last_settlement = None
    for vkey in all_vertex_keys_from_state(state):
        try:
            resp = client.sio.call("placeSettlement", {"vertexKey": vkey, "isSetup": True}, timeout=10)
        except Exception:
            continue
        if isinstance(resp, dict) and resp.get("success"):
            last_settlement = vkey
            print("Placed settlement at:", vkey)
            break

    if not last_settlement:
        print("No legal settlement found right now (unexpected).")
        return False

    # 2) Place road (setup)
    print("Searching for a legal road connected to:", last_settlement)
    state2 = client.latest_state() or state
    placed_road = False
    for ekey in all_edge_keys_from_state(state2):
        try:
            resp = client.sio.call(
                "placeRoad",
                {"edgeKey": ekey, "isSetup": True, "lastSettlement": last_settlement},
                timeout=10,
            )
        except Exception:
            continue
        if isinstance(resp, dict) and resp.get("success"):
            placed_road = True
            print("Placed road at:", ekey)
            break

    if not placed_road:
        print("Could not find a legal road (unexpected).")
        return False

    # 3) Advance setup (matches UI behavior)
    try:
        client.sio.call("advanceSetup", timeout=10)
    except Exception as e:
        print("advanceSetup call failed:", e)
        return False

    print("Advanced setup.\n")
    return True

if __name__ == "__main__":
    client = CatanSocketClient(SERVER_URL)
    client.connect()
    client.join_game(GAME_CODE, player_name="ObserverBot")

    placements_done = 0
    print("Connected. Will auto-place twice during setup when it's my turn.")

    while placements_done < 2:
        # Wait until we see it become our setup turn, then place once.
        if place_one_setup_settlement_and_road(client):
            placements_done += 1
            print(f"Setup placements done: {placements_done}/2")
            # small delay so we don't double-trigger on the same turn state
            time.sleep(0.5)
        else:
            time.sleep(0.25)

    print("Finished 2 setup placements. Now just staying connected (no more moves).")
    while True:
        st = client.latest_state()
        if st:
            phase = st.get("phase")
            me = st.get("myIndex")
            players = st.get("players")

            # If phase is no longer setup, we're definitely past placement
            if phase != "setup":
                break

            # Or if self resources are now non-zero, resources were assigned
            if isinstance(me, int) and isinstance(players, list) and 0 <= me < len(players):
                r = players[me].get("resources")
                if isinstance(r, dict) and sum(int(v) for v in r.values()) > 0:
                    break

        time.sleep(0.25)

    print("Initial resources snapshot:")
    print(GetPlayerResourcesAll(client))

    print("Now just staying connected (no more moves).")
    while True:
        time.sleep(1)

