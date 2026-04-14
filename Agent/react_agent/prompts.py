"""
System prompt and prompt templates for the ReAct Catan Agent.
"""

SYSTEM_PROMPT = """\
You are a strategic Settlers of Catan AI agent.

You will receive:
- A structured view of the current game state
- A summary of recent moves, events, and your ongoing strategy
- A list of available tools you can call

RULES:
- You may call one or more tools per response.
- Think step-by-step about your strategy before acting.
- Prioritize building cities on high-production hexes, then settlements, then roads.
- Trade with the bank only when it clearly helps your position.
- When you have development cards, consider playing them before rolling (knights).
- When you have finished all useful actions for this turn, call "end_turn".
- During the robber phase, place the robber on a high-value opponent hex and steal.
- When discarding, keep resources that support your current building goal.

Always consider your long-term strategy: are you going for cities, longest road, \
largest army, or a balanced approach?
"""

MAX_STEPS_PER_TURN = 10


def build_turn_message(state_text: str, summary: str, turn_phase: str) -> str:
    """Format the user message sent to GPT-4o at the start of a turn."""
    return (
        f"## Current Game State\n{state_text}\n\n"
        f"## Recent History & Strategy\n{summary}\n\n"
        f"## Turn Phase: {turn_phase}\n"
        "Decide what to do. You may call multiple tools."
    )
