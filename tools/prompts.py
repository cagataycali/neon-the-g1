"""Persona system prompt store — SQLite-backed, hot-editable by any agent.

Each persona has ONE current prompt + a version history. The `lookout._*_prompt`
builders consult `get_override()` first; if a row exists, that text replaces
the hardcoded default (after still wrapping it with the dynamic context blocks
like agent log + telegram history). If no row, builders use their defaults.

Schema:
  prompts(persona PRIMARY KEY, text, updated_at, updated_by)
  prompt_history(id, persona, text, ts, source)   -- every update is logged

This is exposed as a Strands @tool so the agents themselves can do:
    prompts(action='get', persona='voice')
    prompts(action='set', persona='voice', text='New prompt body…')
    prompts(action='reset', persona='voice')
    prompts(action='history', persona='voice')
    prompts(action='list')
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
from typing import Optional
from strands import tool

DB = Path(__file__).resolve().parent.parent / ".memory" / "mem.db"
VALID_PERSONAS = {"shell", "thinker", "telegram", "voice"}


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=5)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS prompts(
            persona     TEXT PRIMARY KEY,
            text        TEXT NOT NULL,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_by  TEXT
        );
        CREATE TABLE IF NOT EXISTS prompt_history(
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT NOT NULL,
            text    TEXT NOT NULL,
            ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source  TEXT
        );
    """)
    c.commit()
    return c


# ── library API (used by lookout.py) ───────────────────────────────────
def get_override(persona: str) -> Optional[str]:
    """Return the override prompt text or None if persona uses default."""
    c = _conn()
    row = c.execute("SELECT text FROM prompts WHERE persona=?", (persona,)).fetchone()
    c.close()
    return row[0] if row else None


def set_override(persona: str, text: str, source: str = "tool"):
    """Write/replace persona prompt + log to history."""
    if persona not in VALID_PERSONAS:
        raise ValueError(f"unknown persona: {persona}")
    c = _conn()
    c.execute(
        "INSERT INTO prompts(persona, text, updated_by) VALUES(?,?,?) "
        "ON CONFLICT(persona) DO UPDATE SET text=excluded.text, "
        "updated_at=CURRENT_TIMESTAMP, updated_by=excluded.updated_by",
        (persona, text, source),
    )
    c.execute(
        "INSERT INTO prompt_history(persona, text, source) VALUES(?,?,?)",
        (persona, text, source),
    )
    c.commit()
    c.close()


def reset_override(persona: str) -> bool:
    """Remove override → persona reverts to hardcoded default. Returns True if removed."""
    c = _conn()
    n = c.execute("DELETE FROM prompts WHERE persona=?", (persona,)).rowcount
    c.commit()
    c.close()
    return bool(n)


# ── @tool exposed to all agents ────────────────────────────────────────
@tool
def prompts(
    action: str = "list",
    persona: Optional[str] = None,
    text: Optional[str] = None,
    limit: int = 5,
) -> str:
    """Manage persona system prompts. SQLite-backed, takes effect on next agent invocation.

    Actions:
      - "get":     persona → return current effective prompt (override OR default-marker)
      - "set":     persona + text → persist override (used on next build_agent call)
      - "reset":   persona → drop override, revert to hardcoded default
      - "history": persona [+limit] → show last N versions
      - "list":    show all personas + whether they're overridden
      - "diff":    persona → show how override differs from default (size only)

    Personas: shell | thinker | telegram | voice
    """
    if action == "list":
        c = _conn()
        rows = c.execute(
            "SELECT persona, length(text), updated_at, updated_by FROM prompts ORDER BY persona"
        ).fetchall()
        c.close()
        out = ["persona      override   size    updated_at           by"]
        out.append("─" * 70)
        seen = set()
        for p, sz, ts, by in rows:
            seen.add(p)
            out.append(f"{p:<12} YES        {sz:<7} {ts}  {by or '?'}")
        for p in sorted(VALID_PERSONAS - seen):
            out.append(f"{p:<12} default    —       —                    —")
        return "\n".join(out)

    if not persona or persona not in VALID_PERSONAS:
        return f"persona must be one of: {sorted(VALID_PERSONAS)}"

    if action == "get":
        v = get_override(persona)
        if v is None:
            return f"[{persona}] using HARDCODED default (no override stored)"
        return f"[{persona}] OVERRIDE ({len(v)} chars):\n\n{v}"

    if action == "set":
        if not text:
            return "text required for set"
        set_override(persona, text, source="tool")
        return f"✓ {persona} prompt updated ({len(text)} chars) — takes effect on next agent invocation"

    if action == "reset":
        ok = reset_override(persona)
        return f"✓ {persona} reverted to default" if ok else f"{persona} was already on default"

    if action == "history":
        c = _conn()
        rows = c.execute(
            "SELECT id, ts, source, length(text) FROM prompt_history "
            "WHERE persona=? ORDER BY id DESC LIMIT ?",
            (persona, limit),
        ).fetchall()
        c.close()
        if not rows:
            return f"no history for {persona}"
        lines = [f"id    ts                   source   size"]
        for i, ts, src, sz in rows:
            lines.append(f"{i:<5} {ts}  {src or '?':<8} {sz}")
        return "\n".join(lines)

    if action == "diff":
        v = get_override(persona)
        if v is None:
            return f"{persona}: using default (no override)"
        return f"{persona}: override is {len(v)} chars (default is hardcoded in lookout.py)"

    return f"unknown action: {action}"
