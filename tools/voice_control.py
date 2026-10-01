"""voice_control: the agent silences itself, snoozes, and sets the speaker volume.

One tool so the voice persona can obey "be quiet for an hour" or "louder"
without a dashboard. Mute state is the shared kv in :mod:`tools.voice_state`
(the dashboard's voice sheet and ``make voice-status`` read the same keys);
the volume goes through the G1 ``AudioClient`` Get/SetVolume RPC that the
speaker already uses.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from strands import tool

from . import voice_state
from ._g1_common import ensure_dds, get_audio_client, tool_err, tool_ok

DEFAULT_SNOOZE_MIN = 30


def _volume_get() -> Optional[int]:
    audio = get_audio_client()
    ret = audio.GetVolume()
    data = ret[1] if isinstance(ret, tuple) and len(ret) > 1 else ret
    if isinstance(data, (bytes, str)):
        try:
            data = json.loads(data)
        except Exception:
            return None
    if isinstance(data, dict):
        v = data.get("volume")
        return int(v) if v is not None else None
    return None


def _volume_set(level: int) -> int:
    audio = get_audio_client()
    ret = audio.SetVolume(int(level))
    return int(ret[0]) if isinstance(ret, tuple) else int(ret or 0)


def _fmt(st: Dict[str, Any]) -> str:
    if not st["muted"]:
        return "voice LIVE (mic open, speaker on)"
    if st.get("remaining_s"):
        m, s = divmod(int(st["remaining_s"]), 60)
        return f"voice MUTED for another {m}m{s:02d}s (mic and speaker silent)"
    return "voice MUTED until unmuted (mic and speaker silent)"


@tool
def voice_control(action: str, minutes: Optional[float] = None, level: Optional[int] = None) -> Dict[str, Any]:
    """Mute, snooze or unmute NEON's own voice, or set the head speaker volume.

    Muted means silent: the mic input is dropped AND nothing is played, so stop
    talking right after you mute yourself. The dashboard topbar shows the
    same state and can lift it.

    Args:
        action: "mute" (optionally with minutes), "snooze" (mute for minutes,
            default 30), "unmute", "status", or "volume" (with level).
        minutes: for mute/snooze, how long until the voice comes back by itself.
        level: for volume, 0-100.

    Returns:
        ToolResult with muted / muted_until / remaining_s (+ volume when known).
    """
    act = (action or "").strip().lower()
    if act in ("mute", "snooze"):
        mins = minutes if minutes is not None else (DEFAULT_SNOOZE_MIN if act == "snooze" else None)
        if mins is not None and (mins < 0 or mins > 24 * 60):
            return tool_err("minutes must be between 0 and 1440", minutes=mins)
        st = voice_state.mute(mins)
        return tool_ok(_fmt(st), **st)
    if act == "unmute":
        st = voice_state.unmute()
        return tool_ok(_fmt(st), **st)
    if act == "status":
        st = voice_state.status()
        try:
            if not ensure_dds():
                st["volume"] = _volume_get()
        except Exception as e:  # DDS down: mute state still answers
            st["volume_error"] = str(e)[:120]
        vol = f", volume {st['volume']}" if st.get("volume") is not None else ""
        return tool_ok(_fmt(st) + vol, **st)
    if act == "volume":
        if level is None:
            return tool_err("volume needs level 0-100")
        lvl = max(0, min(100, int(level)))
        err = ensure_dds()
        if err:
            return tool_err(err)
        try:
            rc = _volume_set(lvl)
        except Exception as e:
            return tool_err(f"SetVolume raised: {e}")
        if rc != 0:
            return tool_err(f"SetVolume rc={rc}", rc=rc, level=lvl)
        return tool_ok(f"speaker volume set to {lvl}", volume=lvl, rc=rc)
    return tool_err("action must be one of mute, snooze, unmute, status, volume", action=action)
