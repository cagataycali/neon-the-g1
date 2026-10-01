"""Voice mute / snooze state shared by every persona and the dashboard.

Two keys in the memory kv (``.memory/mem.db``, bind-mounted into every
container and read by the voice listener on the host):

    voice.muted        "true" | "false"
    voice.muted_until  unix epoch (float as text) | ""   a snooze deadline

``MuteFlag`` in :mod:`tools.g1_bidi_audio`, ``tools.voice_control`` (the
agent's own tool), ``docs/dashboard/voice_api.py`` and ``scripts/neon_ctl.py``
all go through this module, so "muted" means the same thing everywhere: a
set deadline that has elapsed counts as unmuted and is cleared on read.
``make voice-status`` reads the same keys.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Same file and table as tools/memory.py (kept free of that import so the
# dashboard and host scripts can read the flag without the robot toolset).
DB = Path(__file__).resolve().parent.parent / ".memory" / "mem.db"

KEY_MUTED = "voice.muted"
KEY_UNTIL = "voice.muted_until"
_TRUE = ("1", "true", "yes", "on")


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=5)
    conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                 "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    return conn


def _get(key: str) -> str:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0].strip() if row and isinstance(row[0], str) else ""


def _set(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET "
                     "value=excluded.value, updated_at=CURRENT_TIMESTAMP", (key, value))


def muted_until() -> Optional[float]:
    raw = _get(KEY_UNTIL)
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def is_muted(clear_expired: bool = True) -> bool:
    """True while the voice is muted. An elapsed snooze deadline reads as
    unmuted and (by default) rewrites the kv so every reader agrees."""
    flag = _get(KEY_MUTED).lower() in _TRUE
    until = muted_until()
    if until is not None and time.time() >= until:
        if clear_expired:
            unmute()
        return False
    return flag


def mute(minutes: Optional[float] = None) -> Dict[str, Any]:
    """Mute now; with ``minutes`` the mute lifts itself at the deadline."""
    until = time.time() + float(minutes) * 60 if minutes and minutes > 0 else None
    _set(KEY_MUTED, "true")
    _set(KEY_UNTIL, f"{until:.0f}" if until else "")
    return status(clear_expired=False)


def unmute() -> Dict[str, Any]:
    _set(KEY_MUTED, "false")
    _set(KEY_UNTIL, "")
    return {"muted": False, "muted_until": None, "remaining_s": 0}


def status(clear_expired: bool = True) -> Dict[str, Any]:
    m = is_muted(clear_expired=clear_expired)
    until = muted_until() if m else None
    remaining = max(0, int(until - time.time())) if until else 0
    return {"muted": m, "muted_until": until, "remaining_s": remaining}
