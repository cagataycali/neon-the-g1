#!/usr/bin/env python3
"""neon-ctl: the host side of the dashboard's control channel.

Runs on the Jetson as the ``unitree`` user (systemd user unit
``scripts/systemd/neon-ctl.service``), never inside a container. Every two
seconds it reads ``.memory/ctl/*.json`` requests written by the dashboard
container (the ``.memory`` directory is bind-mounted into every persona) and
executes exactly one of these:

    {"op": "recreate", "services": ["neon-telegram", ...]}
        docker compose up -d --force-recreate <services>
        (names validated against the four compose services)
    {"op": "restart-voice"}
        sudo systemctl restart neon-voice   (sudoers: scripts/systemd/neon-ctl.sudoers)
    {"op": "token-refresh"}
        scripts/refresh_token.py, then recreate neon-telegram + neon-thinker
    {"op": "status"}
        voice unit state + docker ps, no side effects

The answer lands in ``<id>.result.json``. Requests older than ten minutes are
dropped unexecuted (a stale wish from before a reboot must not recreate the
stack at boot). Each poll also writes ``heartbeat.json`` so the dashboard can
show whether this script is alive, and clears an elapsed voice snooze
(``voice.muted_until`` in the memory kv) so ``make voice-status`` agrees with
the listener.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CTL_DIR = REPO / ".memory" / "ctl"
POLL_S = 2.0
STALE_S = 600.0
RESULT_KEEP_S = 3600.0

SERVICES = ("neon-agent", "neon-dashboard", "neon-telegram", "neon-thinker")
OPS = ("recreate", "restart-voice", "token-refresh", "status")
VOICE_UNIT = "neon-voice"
PY = str(REPO / ".venv" / "bin" / "python") if (REPO / ".venv" / "bin" / "python").exists() else sys.executable


def log(msg: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg, flush=True)


def run(cmd: list[str], timeout: float) -> dict:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(REPO))
        out = (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
        return {"ok": r.returncode == 0, "rc": r.returncode, "output": out[-4000:]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -1, "output": f"timed out after {timeout:.0f} s: {' '.join(cmd)}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "rc": -1, "output": f"{type(e).__name__}: {e}"}


def docker_ps() -> dict:
    r = run(["docker", "ps", "--format", "{{.Names}} {{.Status}}"], 15)
    rows = {}
    for ln in r["output"].splitlines():
        name, _, status = ln.partition(" ")
        if name.startswith("neon-"):
            rows[name] = status
    return rows


def voice_active() -> str:
    r = run(["systemctl", "is-active", VOICE_UNIT], 10)
    return r["output"].splitlines()[0] if r["output"] else "unknown"


def op_recreate(services: list[str]) -> dict:
    bad = [s for s in services if s not in SERVICES]
    if bad or not services:
        return {"ok": False, "output": f"refused: services must be among {', '.join(SERVICES)} (got {services})"}
    res = run(["docker", "compose", "up", "-d", "--force-recreate", *services], 240)
    res["services"] = services
    res["containers"] = docker_ps()
    return res


def op_restart_voice() -> dict:
    res = run(["sudo", "-n", "/bin/systemctl", "restart", VOICE_UNIT], 60)
    time.sleep(1.0)
    res["voice"] = voice_active()
    return res


def op_token_refresh() -> dict:
    if time.time() < 1_700_000_000:
        return {"ok": False, "output": "refused: the clock is not synced yet (a token minted now would carry a 1970 date)"}
    res = run([PY, str(REPO / "scripts" / "refresh_token.py")], 60)
    if res["ok"]:
        rec = op_recreate(["neon-telegram", "neon-thinker"])
        res["recreate"] = rec
        res["ok"] = rec["ok"]
        res["output"] = (res["output"] + "\n" + rec["output"]).strip()[-4000:]
    return res


def op_status() -> dict:
    return {"ok": True, "voice": voice_active(), "containers": docker_ps()}


def handle(req: dict) -> dict:
    op = req.get("op")
    if op not in OPS:
        return {"ok": False, "output": f"refused: unknown op {op!r}"}
    if op == "recreate":
        return op_recreate([str(s) for s in (req.get("services") or [])])
    if op == "restart-voice":
        return op_restart_voice()
    if op == "token-refresh":
        return op_token_refresh()
    return op_status()


def clear_expired_snooze() -> None:
    """voice.muted_until elapsed -> voice.muted=false, muted_until cleared."""
    try:
        import sqlite3
        db = REPO / ".memory" / "mem.db"
        if not db.exists():
            return
        conn = sqlite3.connect(str(db), timeout=2)
        try:
            row = conn.execute("SELECT value FROM kv WHERE key='voice.muted_until'").fetchone()
            if not row or not str(row[0]).strip():
                return
            until = float(row[0])
            if time.time() >= until:
                conn.execute("INSERT OR REPLACE INTO kv(key,value,updated_at) VALUES('voice.muted','false',CURRENT_TIMESTAMP)")
                conn.execute("INSERT OR REPLACE INTO kv(key,value,updated_at) VALUES('voice.muted_until','',CURRENT_TIMESTAMP)")
                conn.commit()
                log("voice snooze elapsed: unmuted")
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        log(f"snooze check failed: {e}")


def write_heartbeat() -> None:
    hb = {"ts": time.time(), "pid": os.getpid(), "voice": voice_active()}
    tmp = CTL_DIR / "heartbeat.json.tmp"
    tmp.write_text(json.dumps(hb))
    os.replace(tmp, CTL_DIR / "heartbeat.json")


def sweep_results() -> None:
    now = time.time()
    for p in CTL_DIR.glob("*.result.json"):
        try:
            if now - p.stat().st_mtime > RESULT_KEEP_S:
                p.unlink()
        except FileNotFoundError:
            pass


def poll_once() -> None:
    for p in sorted(CTL_DIR.glob("*.json")):
        if p.name in ("heartbeat.json",) or p.name.endswith(".result.json"):
            continue
        try:
            req = json.loads(p.read_text())
        except Exception as e:  # noqa: BLE001
            log(f"unreadable request {p.name}: {e}")
            p.unlink(missing_ok=True)
            continue
        rid = str(req.get("id") or p.stem)
        result_path = CTL_DIR / f"{rid}.result.json"
        p.unlink(missing_ok=True)  # claim it before executing: never run a request twice
        age = time.time() - float(req.get("ts") or 0)
        if age > STALE_S:
            res = {"ok": False, "output": f"refused: request is {int(age)} s old (stale)"}
        else:
            log(f"{rid}: {req.get('op')} {req.get('services') or ''}")
            t0 = time.time()
            res = handle(req)
            res["took_s"] = round(time.time() - t0, 1)
        res.update({"id": rid, "op": req.get("op"), "ts": time.time()})
        tmp = CTL_DIR / f"{rid}.result.json.tmp"
        tmp.write_text(json.dumps(res))
        os.replace(tmp, result_path)
        log(f"{rid}: {'ok' if res.get('ok') else 'FAILED'} in {res.get('took_s', 0)} s")


def main() -> int:
    if not shutil.which("docker"):
        log("docker not on PATH; recreate requests will fail")
    CTL_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CTL_DIR, 0o777)  # the containers run as root but the owner may differ; results must be writable both ways
    log(f"neon-ctl watching {CTL_DIR} (poll {POLL_S:.0f} s)")
    n = 0
    while True:
        try:
            poll_once()
            write_heartbeat()
            if n % 5 == 0:
                clear_expired_snooze()
            if n % 300 == 0:
                sweep_results()
        except Exception as e:  # noqa: BLE001
            log(f"poll error: {e}")
        n += 1
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
