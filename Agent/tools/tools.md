# Catan Agent — Tool Reference

All tools available to the agent, grouped by source file.

---

## `game_tools.py`

Internal helpers for action generation and board heuristics. These are **not** directly callable tools — they power the action menu that feeds the agent's decision loop.

### `is_my_turn`
- **Description:** Check whether it is the agent's turn.
- **Parameters:** `state` (Dict) — full game state

### `build_action_menu`
- **Description:** Build the full list of candidate `Action` objects for the current game state and phase (setup, roll, discard, robber, main). This is the primary entry point that the agent loop calls.
- **Parameters:**
  - `state` (Dict) — full game state
  - `last_setup_settlement` (str, optional) — vertex key of the settlement just placed during setup (controls whether the menu offers settlements vs. roads)

#### Internal action generators used by `build_action_menu`:

| Generator | Phase | Description |
|---|---|---|
| `_ranked_setup_settlements` | setup | Rank all vertices by setup heuristic; returns top-k vertex keys |
| `_ranked_setup_roads` | setup | Rank edges near the last settlement; returns top-k edge keys |
| `_build_discard_action` | discard | Auto-build a discard payload (discard from most-held resources first) |
| `_bank_trade_actions` | main | Enumerate legal bank/port trades from hand + `tradeRatios` |
| `_city_upgrade_actions` | main | List own settlements eligible for city upgrade (3 ore + 2 grain) |
| `_buy_dev_card_actions` | main | Suggest buying a dev card when affordable (1 ore + 1 grain + 1 wool) |
| `_trade_offer_response_actions` | main | Provide accept / decline / counter for an incoming trade offer |

---

## `state_tools.py`

Read-only query tools for inspecting player resources and development cards.

### `GetPlayerResourcesAll`
- **Description:** Returns a fair-view summary of **all** players' resources. Self gets a full breakdown; opponents get total count only.
- **Parameters:** `client` (CatanSocketClient)
- **Returns:** `{ ok, players: [{ playerId, name, isSelf, resources?, totalResources }] }`

### `GetPlayerResourcesById`
- **Description:** Returns a fair-view resource summary for a **single** player by ID.
- **Parameters:**
  - `client` (CatanSocketClient)
  - `player_id` (str)
- **Returns:** `{ ok, playerId, name, isSelf, resources?, totalResources }`

### `GetPlayerDevCardsAll`
- **Description:** Returns a fair-view summary of **all** players' development cards. Self gets breakdown by type; opponents get total count only.
- **Parameters:** `client` (CatanSocketClient)
- **Returns:** `{ ok, players: [{ playerId, name, isSelf, devCards?, totalDevCards }] }`

---

## `trading_tools.py`

Higher-level trading tools that directly call the game server via socket events.

### `get_trading_context`
- **Description:** Trade-focused snapshot: your hand, trade ratios, ports, other players' visible info, and the active trade offer (if any).
- **Parameters:** `client` (CatanSocketClient)
- **Returns:** `{ hand, trade_ratios, ports, other_players, active_trade_offer }`

### `get_bank_trade_options`
- **Description:** Enumerate all legal bank/port trades based on current hand and `tradeRatios`.
- **Parameters:** `client` (CatanSocketClient)
- **Returns:** `{ options: [{ give_resource, give_amount, get_resource }] }`

### `execute_bank_trade`
- **Description:** Execute a bank or port trade on the server.
- **Parameters:**
  - `client` (CatanSocketClient)
  - `give_resource` (str) — resource to give
  - `give_amount` (int) — amount to give
  - `get_resource` (str) — resource to receive
- **Returns:** `{ success, error, new_hand }`

### `propose_player_trade`
- **Description:** Propose a player-to-player trade.
- **Parameters:**
  - `client` (CatanSocketClient)
  - `offer` (Dict[str, int]) — resources you offer
  - `request` (Dict[str, int]) — resources you want
  - `target_player_id` (str, optional)
- **Returns:** `{ success, error }`

### `respond_to_trade_offer`
- **Description:** Respond to an incoming player trade offer.
- **Parameters:**
  - `client` (CatanSocketClient)
  - `decision` (str) — `"accept"` or `"decline"`
- **Returns:** `{ success, error }`

### `counter_trade_offer`
- **Description:** Send a counter-offer to the active trade.
- **Parameters:**
  - `client` (CatanSocketClient)
  - `offer` (Dict[str, int]) — resources you offer
  - `request` (Dict[str, int]) — resources you want
- **Returns:** `{ success, error }`

### `cancel_my_trade_offer`
- **Description:** Cancel your own outstanding trade offer.
- **Parameters:** `client` (CatanSocketClient)
- **Returns:** `{ success, error }`

### `query_strategy_alignment_for_trade`
- **Description:** *(Stub)* Ask the strategy sub-agent whether a trade aligns with your goals. Currently returns neutral.
- **Parameters:**
  - `player_id` (str)
  - `trade` (Dict)
- **Returns:** `{ alignment_score, reasoning, recommendation }`

---

## `registry.py`

Formal tool definitions registered for **OpenAI function-calling**. Each tool has an OpenAI-compatible schema, a handler function, and phase-availability metadata.

### 1. `roll_dice`
- **Description:** Roll the dice at the start of your turn.
- **Parameters:** *(none)*
- **Phases:** `roll`

### 2. `place_settlement`
- **Description:** Place a settlement at a vertex. Costs 1 brick + 1 lumber + 1 wool + 1 grain (free during setup).
- **Parameters:**
  - `vertex_key` (string, required) — e.g. `"v_0_-1_2"`
  - `is_setup` (boolean, optional) — true during setup phase
- **Phases:** `main`, `setup`

### 3. `place_road`
- **Description:** Place a road at an edge. Costs 1 brick + 1 lumber (free during setup). Must be adjacent to last settlement during setup.
- **Parameters:**
  - `edge_key` (string, required) — e.g. `"e_0_-1_2"`
  - `is_setup` (boolean, optional) — true during setup phase
  - `last_settlement` (string, optional) — vertex key of last placed settlement (setup only)
- **Phases:** `main`, `setup`

### 4. `upgrade_to_city`
- **Description:** Upgrade a settlement to a city. Costs 3 ore + 2 grain. Doubles resource production.
- **Parameters:**
  - `vertex_key` (string, required) — vertex key of YOUR settlement
- **Phases:** `main`

### 5. `buy_dev_card`
- **Description:** Buy a development card. Costs 1 ore + 1 grain + 1 wool.
- **Parameters:** *(none)*
- **Phases:** `main`

### 6. `play_dev_card`
- **Description:** Play a development card. Types: `knight`, `roadBuilding`, `yearOfPlenty`, `monopoly`. Only 1 per turn; cannot play cards bought this turn.
- **Parameters:**
  - `card_type` (string, required) — enum: `knight`, `roadBuilding`, `yearOfPlenty`, `monopoly`
  - `params` (object, optional) — extra params, e.g. `{"resource": "brick"}` for monopoly
- **Phases:** `roll`, `main`

### 7. `bank_trade`
- **Description:** Trade resources with the bank. Default 4:1, reduced by ports.
- **Parameters:**
  - `give_resource` (string, required) — enum: `brick`, `lumber`, `wool`, `grain`, `ore`
  - `give_amount` (integer, required) — usually 4, 3, or 2
  - `get_resource` (string, required) — enum: `brick`, `lumber`, `wool`, `grain`, `ore`
- **Phases:** `main`

### 8. `propose_trade`
- **Description:** Propose a trade to other players.
- **Parameters:**
  - `offer` (object, required) — e.g. `{"brick": 1, "lumber": 1}`
  - `request` (object, required) — e.g. `{"grain": 1}`
- **Phases:** `main`

### 9. `respond_to_trade`
- **Description:** Accept or decline an incoming trade offer.
- **Parameters:**
  - `accept` (boolean, required) — true to accept, false to decline
- **Phases:** `main`

### 10. `discard_cards`
- **Description:** Discard cards when a 7 is rolled and you have more than 7 cards. Must discard half (rounded down).
- **Parameters:**
  - `resources` (object, required) — e.g. `{"brick": 2, "lumber": 1}`
- **Phases:** `discard`

### 11. `move_robber`
- **Description:** Move the robber to a new hex and optionally steal a resource. Cannot place on current hex.
- **Parameters:**
  - `hex_key` (string, required) — e.g. `"0,-1"`
  - `steal_from_player_id` (string, optional)
- **Phases:** `robber`

### 12. `end_turn`
- **Description:** End your turn and pass to the next player.
- **Parameters:** *(none)*
- **Phases:** `main`

### 13. `get_building_spots`
- **Description:** List the best legal spots to build, ranked by production score.
- **Parameters:**
  - `building_type` (string, optional) — enum: `settlement`, `city`
- **Phases:** `main`, `setup`

### 14. `get_trade_options`
- **Description:** List all available bank/port trades based on current hand and trade ratios.
- **Parameters:** *(none)*
- **Phases:** `main`

### 15. `get_game_summary`
- **Description:** Get a detailed text summary of the current game state, resources, buildings, and opponents.
- **Parameters:** *(none)*
- **Phases:** `roll`, `main`, `robber`, `discard`, `setup`, `specialBuild`

### 16. `advance_setup`
- **Description:** Advance to the next player during setup phase. Call after placing settlement + road.
- **Parameters:** *(none)*
- **Phases:** `setup`

### 17. `year_of_plenty_pick`
- **Description:** Pick a free resource when using the Year of Plenty development card. Called twice (once per resource).
- **Parameters:**
  - `resource` (string, required) — enum: `brick`, `lumber`, `wool`, `grain`, `ore`
- **Phases:** `main`
