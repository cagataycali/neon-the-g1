"""Unified cross-persona reasoning log.

ALL four personas (shell, thinker, telegram, voice) write here and read from
here. The log is injected into every system prompt so each persona knows what
the others have been doing.

Schema:
  agent_log(id, persona, role, text, meta_json, ts)
    persona  : shell | thinker | telegram | voice
    role     : user | assistant | system | tool
    text     : the message body
    meta     : optional JSON (chat_id, repo, tool_name, …)

Trimming rule: we keep the last N rows in `format_for_prompt()` so prompts stay
bounded. Old rows live forever in the DB for analytics — no auto-deletion.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Optional

DB = Path(__file__).resolve().parent.parent / ".memory" / "mem.db"
DEFAULT_LIMIT = 30
MAX_TEXT = 600       # truncate per-message text in prompt block


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=5)
    c.execute("""
        CREATE TABLE IF NOT EXISTS agent_log(
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT NOT NULL,
            role    TEXT NOT NULL,
            text    TEXT NOT NULL,
            meta    TEXT,
            ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_alog_ts ON agent_log(id DESC)")
    c.commit()
    return c


def record(persona: str, role: str, text: str, meta: Optional[dict] = None) -> int:
    """Append a single turn to the unified log."""
    if not text:
        return 0
    c = _conn()
    cur = c.execute(
        "INSERT INTO agent_log(persona, role, text, meta) VALUES(?,?,?,?)",
        (persona, role, str(text)[:8000], json.dumps(meta) if meta else None),
    )
    c.commit()
    rid = cur.lastrowid
    c.close()
    return rid


def recent(limit: int = DEFAULT_LIMIT, persona: Optional[str] = None) -> list:
    """Last N rows, oldest-first."""
    c = _conn()
    if persona:
        rows = c.execute(
            "SELECT persona, role, text, meta, ts FROM agent_log "
            "WHERE persona=? ORDER BY id DESC LIMIT ?",
            (persona, limit),
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT persona, role, text, meta, ts FROM agent_log "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    c.close()
    rows.reverse()
    return [
        {"persona": p, "role": r, "text": t,
         "meta": json.loads(m) if m else None, "ts": ts}
        for p, r, t, m, ts in rows
    ]


def format_for_prompt(limit: int = DEFAULT_LIMIT,
                       exclude_persona: Optional[str] = None) -> str:
    """Format unified log as a system-prompt context block.

    Excluding the current persona's own rows (`exclude_persona`) avoids
    feeding the agent its own past output as if it were context — the
    agent already has its own conversation memory for that. The point is
    cross-persona awareness.
    """
    rows = recent(limit)
    if exclude_persona:
        rows = [r for r in rows if r["persona"] != exclude_persona]
    if not rows:
        return ""
    lines = [f"\n## 🧠 Unified Reasoning Log (last {len(rows)} cross-persona turns):"]
    for r in rows:
        persona = r["persona"]
        role = r["role"]
        text = r["text"][:MAX_TEXT]
        if len(r["text"]) > MAX_TEXT:
            text += "…"
        meta = r["meta"] or {}
        meta_bits = []
        if "chat_id" in meta: meta_bits.append(f"chat={meta['chat_id']}")
        if "repo" in meta:    meta_bits.append(f"repo={meta['repo']}")
        if "tool" in meta:    meta_bits.append(f"tool={meta['tool']}")
        meta_str = f" ({', '.join(meta_bits)})" if meta_bits else ""
        lines.append(f"[{r['ts']}] [{persona}/{role}]{meta_str}: {text}")
    return "\n".join(lines) + "\n"


def prune(keep_last_n: int = 10000) -> int:
    """Keep the last ``keep_last_n`` rows; delete older. Returns deleted rowcount."""
    c = _conn()
    n = c.execute(
        "DELETE FROM agent_log WHERE id NOT IN "
        "(SELECT id FROM agent_log ORDER BY id DESC LIMIT ?)",
        (keep_last_n,),
    ).rowcount
    c.commit()
    c.close()
    return n


def stats() -> dict:
    c = _conn()
    by_persona = c.execute(
        "SELECT persona, COUNT(*) FROM agent_log GROUP BY persona ORDER BY 2 DESC"
    ).fetchall()
    total = c.execute("SELECT COUNT(*) FROM agent_log").fetchone()[0]
    c.close()
    return {"total": total, "by_persona": dict(by_persona)}


def clear(persona: Optional[str] = None) -> int:
    c = _conn()
    if persona:
        n = c.execute("DELETE FROM agent_log WHERE persona=?", (persona,)).rowcount
    else:
        n = c.execute("DELETE FROM agent_log").rowcount
    c.commit()
    c.close()
    return n
