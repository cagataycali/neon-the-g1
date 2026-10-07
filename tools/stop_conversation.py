"""stop_conversation — let the voice model end the current conversation.

strands.bidi (1.58+) ships no stop tool (the experimental package did). This is
the same thing in eight lines: the model calls it when the user says goodbye /
"we're done", it asks the agent to cancel, and BidiAgent.run() returns after
this tool group finishes (BidiConnectionStopEvent reason=user_request). The
voice listener then starts a fresh conversation, so the robot keeps listening.

"Stop talking" / "be quiet" is NOT this tool: that is voice_control(mute),
which keeps the conversation open and silent.
"""
from __future__ import annotations

from strands import tool


@tool(context=True)
def stop_conversation(tool_context) -> dict:
    """End the current voice conversation (the user said goodbye or asked to stop).

    The agent closes this session after the tool returns; a new one starts when
    the user speaks again. For "be quiet" use voice_control(action="mute") instead.

    Returns:
        dict with status.
    """
    agent = getattr(tool_context, "agent", None) if tool_context else None
    cancel = getattr(agent, "cancel", None)
    if not callable(cancel):
        return {"status": "error", "message": "no running voice agent to stop"}
    cancel()
    return {"status": "success", "message": "conversation ending after this turn"}
