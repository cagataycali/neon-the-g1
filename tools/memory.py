"""Memory tool — file system + SQLite persistence with raw SQL.

Storage:
  - <cwd>/.memory/notes/  : plain-text notes (full-text grep)
  - <cwd>/.memory/mem.db  : SQLite (key/value + raw SQL)
"""
from pathlib import Path
import sqlite3
import re
from typing import Optional
from strands import tool

ROOT = Path(__file__).resolve().parent.parent / ".memory"
NOTES = ROOT / "notes"
DB = ROOT / "mem.db"


def _init():
    NOTES.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kv (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS log (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            tag   TEXT,
            text  TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_log_tag ON log(tag);
    """)
    conn.commit()
    return conn


@tool
def memory(
    action: str,
    key: Optional[str] = None,
    value: Optional[str] = None,
    name: Optional[str] = None,
    text: Optional[str] = None,
    query: Optional[str] = None,
    tag: Optional[str] = None,
    sql: Optional[str] = None,
    limit: int = 20,
) -> str:
    """
    Persistent memory: filesystem notes + SQLite kv/log + raw SQL.

    Actions:
      Notes (filesystem):
        - "note_write":  name + text → save note
        - "note_read":   name → read note
        - "note_list":   list all notes
        - "note_search": query → grep across notes
        - "note_delete": name → delete

      Key/Value (SQLite):
        - "kv_set": key + value
        - "kv_get": key
        - "kv_del": key
        - "kv_list": optional query (LIKE on key)

      Log (SQLite, time-series):
        - "log_add":    text + optional tag
        - "log_recent": optional tag, limit

      Raw SQL:
        - "sql": sql (any statement; SELECT returns rows, others return rowcount)
    """
    conn = _init()

    # --- Notes ---
    if action == "note_write":
        if not name or text is None:
            return "name + text required"
        p = NOTES / f"{name}.txt"
        p.write_text(text)
        return f"✓ wrote {p} ({len(text)} chars)"

    if action == "note_read":
        p = NOTES / f"{name}.txt"
        return p.read_text() if p.exists() else f"not found: {name}"

    if action == "note_list":
        items = sorted(NOTES.glob("*.txt"))
        if not items:
            return "no notes"
        return "\n".join(f"{p.stem} ({p.stat().st_size}b)" for p in items)

    if action == "note_search":
        if not query:
            return "query required"
        rx = re.compile(query, re.IGNORECASE)
        hits = []
        for p in NOTES.glob("*.txt"):
            for i, line in enumerate(p.read_text().splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{p.stem}:{i}: {line.strip()}")
                    if len(hits) >= limit:
                        break
        return "\n".join(hits) if hits else "no matches"

    if action == "note_delete":
        p = NOTES / f"{name}.txt"
        if p.exists():
            p.unlink()
            return f"✓ deleted {name}"
        return f"not found: {name}"

    # --- KV ---
    if action == "kv_set":
        conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )
        conn.commit()
        return f"✓ {key}"

    if action == "kv_get":
        row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else f"not found: {key}"

    if action == "kv_del":
        n = conn.execute("DELETE FROM kv WHERE key=?", (key,)).rowcount
        conn.commit()
        return f"deleted {n}"

    if action == "kv_list":
        if query:
            rows = conn.execute(
                "SELECT key,value FROM kv WHERE key LIKE ? ORDER BY key", (f"%{query}%",)
            ).fetchall()
        else:
            rows = conn.execute("SELECT key,value FROM kv ORDER BY key").fetchall()
        return "\n".join(f"{k} = {v[:80]}" for k, v in rows) or "empty"

    # --- Log ---
    if action == "log_add":
        if text is None:
            return "text required"
        conn.execute("INSERT INTO log(tag,text) VALUES(?,?)", (tag, text))
        conn.commit()
        return "✓ logged"

    if action == "log_recent":
        if tag:
            rows = conn.execute(
                "SELECT created_at,tag,text FROM log WHERE tag=? ORDER BY id DESC LIMIT ?",
                (tag, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT created_at,tag,text FROM log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return "\n".join(f"[{ts}] ({t or '-'}) {x}" for ts, t, x in rows) or "empty"

    # --- Raw SQL ---
    if action == "sql":
        if not sql:
            return "sql required"
        try:
            cur = conn.execute(sql)
            if sql.strip().lower().startswith(("select", "pragma", "explain")):
                rows = cur.fetchmany(limit)
                if not rows:
                    return "no rows"
                cols = [d[0] for d in cur.description]
                head = " | ".join(cols)
                body = "\n".join(" | ".join(str(c) for c in r) for r in rows)
                return f"{head}\n{'-'*len(head)}\n{body}"
            conn.commit()
            return f"✓ rowcount={cur.rowcount}"
        except sqlite3.Error as e:
            return f"sql error: {e}"

    return f"unknown action: {action}"
