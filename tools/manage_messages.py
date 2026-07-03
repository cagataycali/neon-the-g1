"""Manage agent.messages — list/drop/compact/clear/stats.

Operates on the *current* agent's conversation memory. Critical for
long-running personas (telegram listener spawns fresh agents per turn so
this matters less, but voice agent runs a single bidi session that can
accumulate huge turn counts).

Turn-aware: respects toolUse/toolResult pairing so we don't strand orphaned
tool blocks. A "turn" = one user message + one assistant message + any
intervening tool calls.
"""
from __future__ import annotations
import json
from typing import Optional, Any
from strands import tool


def _agent_from_kwargs(kwargs):
    """Strands injects the calling agent as `agent` kwarg."""
    return kwargs.get("agent")


def _turn_boundaries(messages: list) -> list[tuple[int, int]]:
    """Group messages into [(start, end_exclusive), ...] turn ranges.

    A turn starts at a user message and runs until just before the next user
    message (or end). Tool blocks live inside their owning turn.
    """
    boundaries = []
    cur_start = None
    for i, m in enumerate(messages):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
        if role == "user":
            if cur_start is not None:
                boundaries.append((cur_start, i))
            cur_start = i
    if cur_start is not None:
        boundaries.append((cur_start, len(messages)))
    return boundaries


def _short(m: Any, max_len: int = 100) -> str:
    role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "?")
    content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
    if isinstance(content, list):
        # Strands' content blocks
        parts = []
        for blk in content:
            if not isinstance(blk, dict):
                parts.append(str(blk)[:60])
                continue
            if "text" in blk:
                parts.append(blk["text"][:60])
            elif "toolUse" in blk:
                parts.append(f"<tool:{blk['toolUse'].get('name','?')}>")
            elif "toolResult" in blk:
                parts.append(f"<result>")
        content = " | ".join(parts)
    s = str(content)[:max_len]
    return f"[{role}] {s}"


@tool
def manage_messages(
    action: str = "stats",
    turns: Optional[str] = None,
    start: Optional[int] = None,
    end: Optional[int] = None,
    role: Optional[str] = None,
    summary_len: int = 100,
    **kwargs,
) -> str:
    """Manage the current agent's conversation history.

    Actions:
      - "stats":   show counts (messages, turns, total chars)
      - "list":    show all messages with index + role + preview
      - "list_turns": show turn-grouped view
      - "drop":    remove turns by index. Use turns="0,2,5" or start+end.
      - "compact": strip toolUse/toolResult blocks from turns, keeping text only.
                   Use turns="0,1,2" or start+end. Default: compact all but last 3.
      - "clear":   remove ALL messages (full reset)

    Notes:
      - Operates on the calling agent's `.messages` directly
      - Turn = one user msg + assistant + any tool blocks between
      - Respects pairing — won't leave orphaned toolUse without toolResult
    """
    agent = _agent_from_kwargs(kwargs)
    if agent is None or not hasattr(agent, "messages"):
        return "no agent context — this tool only works inside a running agent"

    msgs = agent.messages
    bounds = _turn_boundaries(msgs)

    if action == "stats":
        total_chars = sum(len(json.dumps(m, default=str)) for m in msgs)
        by_role = {}
        for m in msgs:
            r = m.get("role") if isinstance(m, dict) else getattr(m, "role", "?")
            by_role[r] = by_role.get(r, 0) + 1
        return (
            f"messages: {len(msgs)}  turns: {len(bounds)}  total_chars: {total_chars:,}\n"
            f"by_role: {by_role}"
        )

    if action == "list":
        lines = []
        for i, m in enumerate(msgs):
            r = m.get("role") if isinstance(m, dict) else getattr(m, "role", "?")
            if role and r != role:
                continue
            lines.append(f"{i:>3}: {_short(m, summary_len)}")
        return "\n".join(lines) or "(empty)"

    if action == "list_turns":
        lines = []
        for ti, (s, e) in enumerate(bounds):
            lines.append(f"turn {ti}: msgs[{s}:{e}]  preview: {_short(msgs[s], 80)}")
        return "\n".join(lines) or "(empty)"

    if action == "drop":
        if turns:
            idxs = sorted({int(x) for x in turns.split(",") if x.strip()}, reverse=True)
        elif start is not None and end is not None:
            idxs = list(range(end - 1, start - 1, -1))
        else:
            return "drop requires turns='0,2,5' OR start+end"
        removed = 0
        for ti in idxs:
            if 0 <= ti < len(bounds):
                s, e = bounds[ti]
                del msgs[s:e]
                removed += (e - s)
                # rebuild bounds (indices shifted)
                bounds = _turn_boundaries(msgs)
        return f"✓ dropped {len(idxs)} turn(s) ({removed} message(s)). Now: {len(msgs)} msgs / {len(bounds)} turns"

    if action == "compact":
        # Pick which turns to compact
        if turns:
            target_turns = [int(x) for x in turns.split(",") if x.strip()]
        elif start is not None and end is not None:
            target_turns = list(range(start, end))
        else:
            # default: compact all but last 3
            target_turns = list(range(max(0, len(bounds) - 3)))

        affected = 0
        # Walk in reverse so index math doesn't shift under us
        for ti in sorted(target_turns, reverse=True):
            if not (0 <= ti < len(bounds)):
                continue
            s, e = bounds[ti]
            for j in range(s, e):
                m = msgs[j]
                if not isinstance(m, dict):
                    continue
                content = m.get("content")
                if isinstance(content, list):
                    text_only = [b for b in content
                                 if isinstance(b, dict) and "text" in b]
                    if text_only and len(text_only) != len(content):
                        m["content"] = text_only
                        affected += 1
            bounds = _turn_boundaries(msgs)
        return f"✓ compacted {len(target_turns)} turn(s), modified {affected} message(s). Now: {len(msgs)} msgs"

    if action == "clear":
        n = len(msgs)
        msgs.clear()
        return f"✓ cleared {n} messages"

    return f"unknown action: {action}"
