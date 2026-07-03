"""Voice bridge — async text-input queue feeding the bidi voice agent.

Other processes (watch_loop, telegram_listener, …) push one-line briefings
into a SQLite table. The voice listener pulls them in its event loop and
emits `BidiTextInputEvent` so the model speaks them out loud (or stays
silent, per persona policy).

Why SQLite (not in-memory queue): the four daemons are separate processes.
We already have `.memory/mem.db` shared between them. One more table costs
~nothing and survives restarts.

Schema:
  voice_bridge(id, source, text, importance, created_at, delivered)
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import List, Tuple, Optional

DB = Path(__file__).resolve().parent.parent / ".memory" / "mem.db"
STALE_SECONDS = 300        # discard briefings older than 5 min on startup


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=5)
    c.execute("""
        CREATE TABLE IF NOT EXISTS voice_bridge(
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,        -- thinker | telegram | manual
            text        TEXT NOT NULL,
            importance  INTEGER DEFAULT 1,    -- 0=silent log, 1=normal, 2=urgent
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            delivered   INTEGER DEFAULT 0
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_vb_pending ON voice_bridge(delivered, id)")
    c.commit()
    return c


def push(source: str, text: str, importance: int = 1) -> int:
    """Push a briefing for the voice agent. Returns row id."""
    if not text or not text.strip():
        return 0
    c = _conn()
    cur = c.execute(
        "INSERT INTO voice_bridge(source, text, importance) VALUES(?,?,?)",
        (source, text.strip()[:2000], importance),
    )
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def pop_pending(limit: int = 5) -> List[Tuple[int, str, str, int]]:
    """Fetch up to `limit` undelivered briefings and mark them delivered.

    Returns list of (id, source, text, importance), oldest first.
    """
    c = _conn()
    rows = c.execute(
        "SELECT id, source, text, importance FROM voice_bridge "
        "WHERE delivered=0 ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()
    if rows:
        ids = [r[0] for r in rows]
        c.executemany("UPDATE voice_bridge SET delivered=1 WHERE id=?",
                      [(i,) for i in ids])
        c.commit()
    c.close()
    return rows


def flush_stale():
    """Mark briefings older than STALE_SECONDS as delivered (skip them).

    Called by the voice listener on startup so we don't replay yesterday's
    chatter when the service comes back online.
    """
    c = _conn()
    n = c.execute(
        f"UPDATE voice_bridge SET delivered=1 "
        f"WHERE delivered=0 AND created_at < datetime('now', '-{STALE_SECONDS} seconds')"
    ).rowcount
    c.commit()
    c.close()
    return n


def prune(max_pending: int = 10000, max_delivered_days: int = 7) -> dict:
    """Maintenance pruner — call periodically (e.g. once an hour).

    Removes:
      - delivered rows older than ``max_delivered_days`` days
      - oldest pending rows beyond ``max_pending`` (treats them as stale,
        marks delivered=1 so they don't replay; doesn't actually delete)

    Returns dict with rows pruned.
    """
    c = _conn()
    pruned = c.execute(
        f"DELETE FROM voice_bridge WHERE delivered=1 AND created_at < datetime('now', '-{int(max_delivered_days)} days')"
    ).rowcount
    # If pending exceeds cap, mark oldest as delivered to drop them
    pending = c.execute("SELECT COUNT(*) FROM voice_bridge WHERE delivered=0").fetchone()[0]
    capped = 0
    if pending > max_pending:
        capped = c.execute(
            "UPDATE voice_bridge SET delivered=1 WHERE id IN "
            "(SELECT id FROM voice_bridge WHERE delivered=0 ORDER BY id LIMIT ?)",
            (pending - max_pending,),
        ).rowcount
    c.commit()
    c.close()
    return {"deleted": pruned, "capped": capped, "pending_after": min(pending, max_pending)}


def stats() -> dict:
    c = _conn()
    pending = c.execute("SELECT COUNT(*) FROM voice_bridge WHERE delivered=0").fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM voice_bridge").fetchone()[0]
    c.close()
    return {"pending": pending, "total": total}


# ── @tool wrapper for cross-persona use ──────────────────────────────
from strands import tool


@tool
def voice_say(text: str, importance: int = 1) -> dict:
    """Send a message to the voice agent so it speaks it out loud.

    Use this from any other persona (telegram, thinker, shell) to make the
    voice agent speak something to the user. The voice agent picks it up
    from a shared SQLite queue within ~2 seconds.

    Args:
        text: What the voice agent should speak (or react to). Plain text.
        importance: 0=silent log, 1=normal info (default), 2=URGENT
                    URGENT briefings are read immediately even mid-conversation.

    Returns:
        dict with 'id' of the queued message and 'status'.

    Examples:
        voice_say("hey, the user just said hi over telegram")
        voice_say("CRITICAL: production is down", importance=2)
        voice_say("just confirming: telegram message received", importance=1)
    """
    if not text or not text.strip():
        return {"status": "error", "message": "empty text"}
    rid = push(source="telegram", text=text.strip(), importance=int(importance))
    return {
        "status": "success",
        "id": rid,
        "queued": text.strip()[:200],
        "importance": int(importance),
        "message": f"Voice agent will speak this within ~2s",
    }
