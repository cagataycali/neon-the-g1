"""Dispatch — spawn a devduck agent IN-PROCESS as a tool, optionally on a schedule.

Uses the Python API (devduck.DevDuck) instead of subprocess so we can:
  - Capture full `agent.messages` (toolUse/toolResult/text blocks)
  - Persist conversation history to SQLite (.memory/dispatch/dispatch.db)
  - Write a summary line to the unified agent_log (cross-persona awareness)
  - Optionally schedule recurring/one-shot fires (cron or run_at)

Each dispatch builds a fresh DevDuck (own model/tools/prompt/MCP). The
calling process keeps a reference to dd.agent so messages stay structured.

Modes:
  - sync:  build + run + persist + return text (blocks)
  - async: thread-spawn, return id, log streams to file
  - bg:    alias for async

Scheduling (NEW):
  - schedule="*/5 * * * *"     → cron, fires every 5 min
  - run_at="2026-05-26T18:00"  → one-shot at ISO time
  - runs=N                      → max fires (default infinite for cron, 1 for run_at)
"""
from __future__ import annotations
import os
import io
import json
import sqlite3
import threading
import time
import traceback
import contextlib
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
from strands import tool

# Reuse devduck's cron parser (no extra dep)
try:
    from devduck.tools.scheduler import _parse_cron, _cron_matches
except Exception:
    _parse_cron = None
    _cron_matches = None

# Optional: write to unified cross-persona log
try:
    from tools.agent_log import record as _alog_record
except Exception:
    _alog_record = None

ROOT = Path(__file__).resolve().parent.parent / ".memory" / "dispatch"
ROOT.mkdir(parents=True, exist_ok=True)
DB = ROOT / "dispatch.db"

_RUNNING: Dict[str, threading.Thread] = {}
_LOCK = threading.Lock()
_TICKER_STARTED = False
_TICKER_LOCK = threading.Lock()


def _conn():
    c = sqlite3.connect(DB, timeout=10)
    c.execute("""
        CREATE TABLE IF NOT EXISTS dispatches(
            id            TEXT PRIMARY KEY,
            prompt        TEXT,
            model         TEXT,
            tools         TEXT,
            mode          TEXT,
            status        TEXT,    -- running | done | failed
            result_text   TEXT,
            messages_json TEXT,
            error         TEXT,
            log_path      TEXT,
            schedule_id   TEXT,    -- FK if produced by a schedule
            started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ended_at      TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS dispatch_schedules(
            id            TEXT PRIMARY KEY,
            prompt        TEXT,
            schedule      TEXT,    -- cron expr OR null
            run_at        TEXT,    -- ISO datetime OR null
            runs_max      INTEGER, -- max fires (NULL = infinite)
            runs_done     INTEGER DEFAULT 0,
            last_fired_at TIMESTAMP,
            last_minute   TEXT,    -- YYYY-MM-DD HH:MM (debounce)
            config_json   TEXT,    -- model/tools/system_prompt/etc as JSON
            enabled       INTEGER DEFAULT 1,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Self-heal migrations: add columns we depend on
    cols = {r[1] for r in c.execute("PRAGMA table_info(dispatches)").fetchall()}
    if "schedule_id" not in cols:
        c.execute("ALTER TABLE dispatches ADD COLUMN schedule_id TEXT")
    c.commit()
    return c


def _new_id(prefix: str = "d") -> str:
    return f"{prefix}{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"


def _serialize_messages(messages) -> List[Dict[str, Any]]:
    out = []
    for m in messages or []:
        try:
            if isinstance(m, dict):
                out.append(m)
            elif hasattr(m, "model_dump"):
                out.append(m.model_dump())
            elif hasattr(m, "__dict__"):
                out.append(dict(m.__dict__))
            else:
                out.append({"role": "unknown", "content": str(m)[:2000]})
        except Exception as e:
            out.append({"role": "unknown", "content": str(m)[:2000], "_err": str(e)})
    return out


def _build_agent(
    model: Optional[str] = None,
    model_provider: Optional[str] = None,
    tools_cfg: Optional[str] = None,
    system_prompt: Optional[str] = None,
    mcp_servers: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
    enable_servers: bool = False,
    load_tools_from_directory: bool = True,
):
    """Build fresh DevDuck. Mutates os.environ then restores."""
    saved = {}
    overrides = {
        "DEVDUCK_AUTO_START_SERVERS": "true" if enable_servers else "false",
        "DEVDUCK_AMBIENT_MODE": "false",
        "BYPASS_TOOL_CONSENT": "true",
        "DEVDUCK_LOAD_TOOLS_FROM_DIR": "true" if load_tools_from_directory else "false",
    }
    if model:
        overrides["STRANDS_MODEL_ID"] = model
    if model_provider:
        overrides["MODEL_PROVIDER"] = model_provider
    if tools_cfg:
        overrides["DEVDUCK_TOOLS"] = tools_cfg
    if system_prompt:
        overrides["SYSTEM_PROMPT"] = system_prompt
    if mcp_servers:
        overrides["MCP_SERVERS"] = mcp_servers
    if env_vars:
        for k, v in env_vars.items():
            overrides[str(k)] = str(v)

    for k, v in overrides.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        from devduck import DevDuck
        return DevDuck(auto_start_servers=enable_servers, load_mcp_servers=True)
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


def _run_one(did: str, prompt: str, build_kwargs: Dict[str, Any], log_path: Path,
             schedule_id: Optional[str] = None):
    """Worker: build agent, run prompt, persist."""
    c = _conn()
    try:
        with open(log_path, "w", encoding="utf-8", buffering=1) as logf:
            with contextlib.redirect_stdout(logf), contextlib.redirect_stderr(logf):
                print(f"[dispatch:{did}] building…")
                dd = _build_agent(**build_kwargs)
                print(f"[dispatch:{did}] running…")
                t0 = time.time()
                result = dd(prompt)
                print(f"[dispatch:{did}] done in {time.time()-t0:.1f}s")

        result_text = str(result)[:50000]
        msgs = _serialize_messages(getattr(dd.agent, "messages", []))
        c.execute(
            "UPDATE dispatches SET status='done', result_text=?, messages_json=?, ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (result_text, json.dumps(msgs)[:5_000_000], did),
        )
        c.commit()
        if _alog_record:
            try:
                tag_extra = f" [sched:{schedule_id}]" if schedule_id else ""
                _alog_record("dispatch", "user", (prompt[:500] + tag_extra), meta={"dispatch_id": did, "schedule_id": schedule_id})
                _alog_record("dispatch", "assistant", result_text[:2000], meta={"dispatch_id": did, "msgs": len(msgs)})
            except Exception:
                pass
    except Exception as e:
        tb = traceback.format_exc()
        try:
            with open(log_path, "a", encoding="utf-8") as logf:
                logf.write(f"\n[ERROR]\n{tb}\n")
        except Exception:
            pass
        c.execute(
            "UPDATE dispatches SET status='failed', error=?, ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (f"{e}\n{tb}"[:5000], did),
        )
        c.commit()
        if _alog_record:
            try:
                _alog_record("dispatch", "system", f"FAILED: {e}", meta={"dispatch_id": did})
            except Exception:
                pass
    finally:
        c.close()
        with _LOCK:
            _RUNNING.pop(did, None)


# ── Scheduler tick loop ──────────────────────────────────────────────

def _start_ticker():
    global _TICKER_STARTED
    with _TICKER_LOCK:
        if _TICKER_STARTED:
            return
        t = threading.Thread(target=_ticker_loop, daemon=True, name="dispatch-ticker")
        t.start()
        _TICKER_STARTED = True


def _ticker_loop():
    """Check every 30s for due schedules and fire them."""
    while True:
        try:
            _check_due_schedules()
        except Exception as e:
            print(f"[dispatch-ticker] error: {e}")
        time.sleep(30)


def _check_due_schedules():
    """Iterate enabled schedules, fire any that are due (cron OR run_at)."""
    now = datetime.now()
    cur_minute = now.strftime("%Y-%m-%d %H:%M")
    c = _conn()
    rows = c.execute(
        "SELECT id, prompt, schedule, run_at, runs_max, runs_done, last_minute, config_json "
        "FROM dispatch_schedules WHERE enabled=1"
    ).fetchall()
    for row in rows:
        sid, prompt, sched, run_at, runs_max, runs_done, last_minute, cfg_json = row
        # Already fired this minute? skip
        if last_minute == cur_minute:
            continue
        # Hit max runs?
        if runs_max is not None and runs_done >= runs_max:
            c.execute("UPDATE dispatch_schedules SET enabled=0 WHERE id=?", (sid,))
            continue
        # Decide if due
        due = False
        if sched and _parse_cron and _cron_matches:
            cron = _parse_cron(sched)
            if cron and _cron_matches(cron, now):
                due = True
        if (not due) and run_at:
            try:
                target = datetime.fromisoformat(run_at)
                if now >= target and runs_done == 0:
                    due = True
            except Exception:
                pass
        if not due:
            continue
        # Fire
        cfg = json.loads(cfg_json) if cfg_json else {}
        did = _new_id()
        log_path = ROOT / f"{did}.log"
        c.execute(
            "INSERT INTO dispatches(id, prompt, model, tools, mode, status, log_path, schedule_id) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (did, prompt[:4000], cfg.get("model") or "auto", cfg.get("tools_cfg") or "default",
             "scheduled", "running", str(log_path), sid),
        )
        c.execute(
            "UPDATE dispatch_schedules SET runs_done=runs_done+1, last_minute=?, last_fired_at=CURRENT_TIMESTAMP WHERE id=?",
            (cur_minute, sid),
        )
        c.commit()
        # If run_at one-shot, disable
        if run_at and not sched:
            c.execute("UPDATE dispatch_schedules SET enabled=0 WHERE id=?", (sid,))
            c.commit()
        # Spawn worker thread
        t = threading.Thread(
            target=_run_one,
            args=(did, prompt, cfg, log_path, sid),
            daemon=True,
        )
        with _LOCK:
            _RUNNING[did] = t
        t.start()
        print(f"⏰ [dispatch-ticker] fired schedule {sid} → {did}")
    c.close()


# ── Tool entry point ─────────────────────────────────────────────────

@tool
def dispatch(
    action: str = "run",
    prompt: Optional[str] = None,
    mode: str = "sync",
    model: Optional[str] = None,
    model_provider: Optional[str] = None,
    tools: Optional[str] = None,
    system_prompt: Optional[str] = None,
    mcp_servers: Optional[str] = None,
    env_vars: Optional[Dict[str, str]] = None,
    work_dir: Optional[str] = None,
    timeout: int = 600,
    dispatch_id: Optional[str] = None,
    enable_servers: bool = False,
    load_tools_from_directory: bool = True,
    tail: int = 100,
    # Scheduling
    schedule: Optional[str] = None,
    run_at: Optional[str] = None,
    runs: Optional[int] = None,
    schedule_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Spawn a devduck instance as a tool, optionally on a schedule.

    Builds an in-process `devduck.DevDuck` agent with custom config, runs the
    prompt, persists `agent.messages` to SQLite, writes a summary to the
    unified cross-persona log, and (optionally) schedules recurring fires.

    Actions:
      Run/inspect:
        - "run":      spawn + run NOW. Modes: sync | async | bg.
        - "list":     list dispatch runs
        - "status":   inspect one (dispatch_id required)
        - "result":   final assistant text (dispatch_id required)
        - "messages": full agent.messages dump (dispatch_id required)
        - "logs":     tail captured stdout (dispatch_id required)
        - "wait":     block until done (dispatch_id required)
        - "clean":    purge finished runs older than 24h
      Schedule:
        - "schedule":   register a schedule (requires prompt + schedule|run_at)
        - "schedules":  list active schedules
        - "unschedule": remove schedule (schedule_id required)

    Modes (action="run"):
      sync   — block + return text
      async  — thread-spawn, return id
      bg     — alias for async

    Scheduling:
      schedule="*/5 * * * *"     → cron expression, fires on match
      run_at="2026-05-26T18:00"  → one-shot at ISO datetime
      runs=N                      → cap total fires (default ∞ for cron, 1 for run_at)

      Scheduled fires use the same model/tools/system_prompt/etc you pass in
      this call. Ticker runs every 30s. Schedule survives only while THIS
      process is alive (in-process; if you need cross-process persistence,
      use devduck's own `scheduler` tool).

    Args:
      prompt:                     Query for the dispatched agent (required for run/schedule)
      model:                      STRANDS_MODEL_ID override
      model_provider:             bedrock / anthropic / openai / ollama / etc
      tools:                      DEVDUCK_TOOLS string
      system_prompt:              Custom SYSTEM_PROMPT for the child
      mcp_servers:                MCP_SERVERS JSON string
      env_vars:                   Dict of extra env vars
      work_dir:                   cwd for the child
      timeout:                    sync-mode timeout (seconds, default 600)
      enable_servers:             allow child to start its own zenoh/proxy/etc
      load_tools_from_directory:  load ./tools/*.py auto-discovery (default True)

    Returns:
      Dict with status + content.
    """
    try:
        # If schedule/run_at present and action defaults to "run", treat as schedule create
        if action == "run" and (schedule or run_at):
            action = "schedule"

        if action == "run":
            if not prompt:
                return {"status": "error", "content": [{"text": "prompt required"}]}

            did = dispatch_id or _new_id()
            log_path = ROOT / f"{did}.log"
            build_kwargs = dict(
                model=model, model_provider=model_provider, tools_cfg=tools,
                system_prompt=system_prompt, mcp_servers=mcp_servers,
                env_vars=env_vars, enable_servers=enable_servers,
                load_tools_from_directory=load_tools_from_directory,
            )

            c = _conn()
            c.execute(
                "INSERT INTO dispatches(id, prompt, model, tools, mode, status, log_path) "
                "VALUES(?,?,?,?,?,?,?)",
                (did, prompt[:4000], model or "auto", tools or "default",
                 mode, "running", str(log_path)),
            )
            c.commit()
            c.close()

            saved_cwd = None
            if work_dir and os.path.isdir(work_dir):
                saved_cwd = os.getcwd()
                os.chdir(work_dir)
            try:
                if mode == "sync":
                    t = threading.Thread(target=_run_one, args=(did, prompt, build_kwargs, log_path), daemon=True)
                    with _LOCK:
                        _RUNNING[did] = t
                    t.start()
                    t.join(timeout=timeout)
                    if t.is_alive():
                        c = _conn()
                        c.execute("UPDATE dispatches SET status='failed', error='timeout', ended_at=CURRENT_TIMESTAMP WHERE id=?", (did,))
                        c.commit(); c.close()
                        return {"status": "error", "content": [{"text": f"[dispatch:{did}] timed out (thread still bg)"}]}
                    c = _conn()
                    row = c.execute("SELECT status, result_text, error FROM dispatches WHERE id=?", (did,)).fetchone()
                    c.close()
                    status, text, err = row
                    if status == "done":
                        out = text or ""
                        if len(out) > 8000:
                            out = out[:4000] + f"\n\n... [truncated {len(out)-8000}] ...\n\n" + out[-4000:]
                        return {"status": "success", "content": [{"text": f"[dispatch:{did}] done\n\n{out}"}]}
                    return {"status": "error", "content": [{"text": f"[dispatch:{did}] {status}: {err}"}]}
                elif mode in ("async", "bg"):
                    t = threading.Thread(target=_run_one, args=(did, prompt, build_kwargs, log_path), daemon=True)
                    with _LOCK:
                        _RUNNING[did] = t
                    t.start()
                    return {"status": "success", "content": [{
                        "text": f"[dispatch:{did}] spawned async\n  log: {log_path}\n  poll: dispatch(action='status', dispatch_id='{did}')"
                    }]}
                else:
                    return {"status": "error", "content": [{"text": f"unknown mode: {mode}"}]}
            finally:
                if saved_cwd:
                    os.chdir(saved_cwd)

        elif action == "schedule":
            if not prompt:
                return {"status": "error", "content": [{"text": "prompt required"}]}
            if not (schedule or run_at):
                return {"status": "error", "content": [{"text": "schedule (cron) or run_at (ISO datetime) required"}]}
            if schedule and not _parse_cron:
                return {"status": "error", "content": [{"text": "cron support unavailable (devduck.tools.scheduler missing)"}]}
            if schedule:
                if not _parse_cron(schedule):
                    return {"status": "error", "content": [{"text": f"invalid cron: {schedule}"}]}
            if run_at:
                try:
                    datetime.fromisoformat(run_at)
                except Exception:
                    return {"status": "error", "content": [{"text": f"invalid run_at ISO: {run_at}"}]}

            sid = schedule_id or _new_id("s")
            cfg = dict(
                model=model, model_provider=model_provider, tools_cfg=tools,
                system_prompt=system_prompt, mcp_servers=mcp_servers,
                env_vars=env_vars, enable_servers=enable_servers,
                load_tools_from_directory=load_tools_from_directory,
            )
            # Default runs_max: 1 for run_at one-shots, NULL (infinite) for cron
            runs_max = runs
            if runs_max is None and run_at and not schedule:
                runs_max = 1

            c = _conn()
            c.execute(
                "INSERT INTO dispatch_schedules(id, prompt, schedule, run_at, runs_max, config_json) "
                "VALUES(?,?,?,?,?,?)",
                (sid, prompt[:4000], schedule, run_at, runs_max, json.dumps(cfg)),
            )
            c.commit()
            c.close()
            _start_ticker()
            return {"status": "success", "content": [{
                "text": f"[schedule:{sid}] registered\n"
                        f"  prompt: {prompt[:80]}\n"
                        f"  cron:   {schedule or '(none)'}\n"
                        f"  run_at: {run_at or '(none)'}\n"
                        f"  runs:   {runs_max if runs_max is not None else '∞'}\n"
                        f"  inspect: dispatch(action='schedules')"
            }]}

        elif action == "schedules":
            c = _conn()
            rows = c.execute(
                "SELECT id, schedule, run_at, runs_done, runs_max, last_fired_at, enabled, substr(prompt,1,60) "
                "FROM dispatch_schedules ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            c.close()
            if not rows:
                return {"status": "success", "content": [{"text": "no schedules"}]}
            lines = [f"{'ID':22} {'CRON':16} {'RUN_AT':20} RUNS  ENB LAST_FIRED          PROMPT"]
            for r in rows:
                sid, sched, rat, done, mx, last, enb, pp = r
                runs_str = f"{done}/{mx if mx is not None else '∞'}"
                lines.append(f"{sid:22} {(sched or '-'):16} {(rat or '-'):20} {runs_str:5} {enb:3} {(last or '-')[:19]:19} {pp or ''}")
            return {"status": "success", "content": [{"text": "\n".join(lines)}]}

        elif action == "unschedule":
            if not schedule_id:
                return {"status": "error", "content": [{"text": "schedule_id required"}]}
            c = _conn()
            cur = c.execute("DELETE FROM dispatch_schedules WHERE id=?", (schedule_id,))
            c.commit()
            c.close()
            return {"status": "success" if cur.rowcount else "error",
                    "content": [{"text": f"removed {cur.rowcount} schedule(s)"}]}

        elif action == "list":
            c = _conn()
            rows = c.execute(
                "SELECT id, mode, status, started_at, schedule_id, substr(prompt,1,80) "
                "FROM dispatches ORDER BY started_at DESC LIMIT 50"
            ).fetchall()
            c.close()
            if not rows:
                return {"status": "success", "content": [{"text": "no dispatches"}]}
            lines = [f"{'ID':22} {'MODE':9} {'STATUS':8} STARTED              SCHED                  PROMPT"]
            for r in rows:
                lines.append(f"{r[0]:22} {r[1]:9} {r[2]:8} {r[3][:19]:19} {(r[4] or '-'):22} {r[5] or ''}")
            return {"status": "success", "content": [{"text": "\n".join(lines)}]}

        elif action == "status":
            if not dispatch_id:
                return {"status": "error", "content": [{"text": "dispatch_id required"}]}
            c = _conn()
            row = c.execute(
                "SELECT id, prompt, model, tools, mode, status, error, schedule_id, started_at, ended_at FROM dispatches WHERE id=?",
                (dispatch_id,),
            ).fetchone()
            c.close()
            if not row:
                return {"status": "error", "content": [{"text": f"no dispatch {dispatch_id}"}]}
            cols = ["id", "prompt", "model", "tools", "mode", "status", "error", "schedule_id", "started_at", "ended_at"]
            return {"status": "success", "content": [{"text": json.dumps(dict(zip(cols, row)), indent=2, default=str)}]}

        elif action == "result":
            if not dispatch_id:
                return {"status": "error", "content": [{"text": "dispatch_id required"}]}
            c = _conn()
            row = c.execute("SELECT status, result_text, error FROM dispatches WHERE id=?", (dispatch_id,)).fetchone()
            c.close()
            if not row:
                return {"status": "error", "content": [{"text": f"no dispatch {dispatch_id}"}]}
            status, text, err = row
            if status == "running":
                return {"status": "success", "content": [{"text": f"[{dispatch_id}] still running"}]}
            return {"status": "success" if status == "done" else "error",
                    "content": [{"text": f"[{dispatch_id}] {status}\n\n{text or err or ''}"}]}

        elif action == "messages":
            if not dispatch_id:
                return {"status": "error", "content": [{"text": "dispatch_id required"}]}
            c = _conn()
            row = c.execute("SELECT messages_json FROM dispatches WHERE id=?", (dispatch_id,)).fetchone()
            c.close()
            if not row or not row[0]:
                return {"status": "error", "content": [{"text": f"no messages for {dispatch_id}"}]}
            msgs = json.loads(row[0])
            lines = [f"=== {len(msgs)} messages ==="]
            for i, m in enumerate(msgs):
                role = m.get("role", "?")
                content = m.get("content", "")
                preview = json.dumps(content)[:300] if not isinstance(content, str) else content[:300]
                lines.append(f"[{i:02d}] {role}: {preview}")
            return {"status": "success", "content": [{"text": "\n".join(lines)}]}

        elif action == "logs":
            if not dispatch_id:
                return {"status": "error", "content": [{"text": "dispatch_id required"}]}
            c = _conn()
            row = c.execute("SELECT log_path FROM dispatches WHERE id=?", (dispatch_id,)).fetchone()
            c.close()
            if not row:
                return {"status": "error", "content": [{"text": f"no dispatch {dispatch_id}"}]}
            lp = Path(row[0])
            if not lp.exists():
                return {"status": "success", "content": [{"text": "(no log yet)"}]}
            data = lp.read_text(encoding="utf-8", errors="replace").splitlines()
            tail_lines = data[-tail:] if len(data) > tail else data
            return {"status": "success", "content": [{"text": "\n".join(tail_lines)}]}

        elif action == "wait":
            if not dispatch_id:
                return {"status": "error", "content": [{"text": "dispatch_id required"}]}
            with _LOCK:
                t = _RUNNING.get(dispatch_id)
            if t:
                t.join(timeout=timeout)
                if t.is_alive():
                    return {"status": "error", "content": [{"text": f"wait timed out after {timeout}s"}]}
            return dispatch(action="status", dispatch_id=dispatch_id)

        elif action == "clean":
            c = _conn()
            cur = c.execute(
                "DELETE FROM dispatches WHERE status != 'running' "
                "AND started_at < datetime('now', '-1 day')"
            )
            c.commit()
            n = cur.rowcount
            c.close()
            return {"status": "success", "content": [{"text": f"cleaned {n} old dispatches"}]}

        else:
            return {"status": "error", "content": [{"text": f"unknown action: {action}"}]}

    except Exception as e:
        return {"status": "error", "content": [{"text": f"dispatch error: {e}\n{traceback.format_exc()}"}]}
