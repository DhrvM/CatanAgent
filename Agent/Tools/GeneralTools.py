"""
This file contains general-purpose tool call definitions for agents and the functions.

"""
ENUM_PLAYERS = {
    "PLAYER_1": "Player 1",
    "PLAYER_2": "Player 2",
    "PLAYER_3": "Player 3",
    "ALL": "All",
}

def send_chat(message: str, player: ENUM_PLAYERS) -> bool:
    """
    Send a chat message to the specified player. Return True if successful, False otherwise.
    """
    return False


def getSelfResources() -> dict:
    """
    Get the resources of the agent and return dict of {type: count}
    """
    return {}