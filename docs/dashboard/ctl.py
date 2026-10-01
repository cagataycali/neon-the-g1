"""Container side of the neon-ctl control channel.

The dashboard runs in a container without docker.sock or systemd, yet the
owner wants three host actions from the UI: recreate persona containers
(a model change), restart the voice listener (a voice profile change) and
refresh the camera proxy token. The channel is a directory of JSON files in
the shared ``.memory`` bind mount:

    .memory/ctl/<id>.json          request  {"op": ..., "services": [...]}
    .memory/ctl/<id>.result.json   result   {"ok": bool, "op": ..., "output": ...}

``scripts/neon_ctl.py`` on the host polls the directory every two seconds and
executes an allow-list. Nothing here has privileges; a request is a wish the
host may refuse. ``request_ctl`` writes the wish and waits for the answer.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent.parent
CTL_DIR = Path(os.getenv("NEON_CTL_DIR", str(REPO / ".memory" / "ctl")))

OPS = ("recreate", "restart-voice", "token-refresh", "status")
SERVICES = ("neon-agent", "neon-dashboard", "neon-telegram", "neon-thinker")
# How long the agent (the host script) is considered alive after its last tick.
ALIVE_WINDOW_S = 10.0


def heartbeat() -> Dict[str, Any]:
    """The host script writes .memory/ctl/heartbeat.json every poll."""
    p = CTL_DIR / "heartbeat.json"
    try:
        d = json.loads(p.read_text())
        age = time.time() - float(d.get("ts", 0))
        return {"alive": age < ALIVE_WINDOW_S, "age_s": round(age, 1), **{k: v for k, v in d.items() if k != "ts"}}
    except Exception:
        return {"alive": False, "age_s": None}


def validate(op: str, services: Optional[List[str]] = None) -> Optional[str]:
    """Return an error sentence, or None when the request is well formed."""
    if op not in OPS:
        return f"unknown op {op!s}; allowed: {', '.join(OPS)}"
    if op == "recreate":
        if not services:
            return "recreate needs at least one service"
        bad = [s for s in services if s not in SERVICES]
        if bad:
            return f"unknown service(s) {', '.join(bad)}; allowed: {', '.join(SERVICES)}"
    return None


def request_ctl(op: str, services: Optional[List[str]] = None, wait_s: float = 90.0,
                timeout_ok: bool = False) -> Dict[str, Any]:
    """Write a request and wait for the host to answer.

    ``timeout_ok`` returns ``{"ok": True, "pending": True}`` instead of an
    error when the deadline passes; the dashboard recreating itself can never
    read its own result, so that caller passes ``wait_s=0``.
    """
    err = validate(op, services)
    if err:
        return {"ok": False, "error": err}
    CTL_DIR.mkdir(parents=True, exist_ok=True)
    rid = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    req = {"id": rid, "op": op, "services": list(services or []), "ts": time.time()}
    tmp = CTL_DIR / f"{rid}.json.tmp"
    tmp.write_text(json.dumps(req))
    os.replace(tmp, CTL_DIR / f"{rid}.json")
    result_path = CTL_DIR / f"{rid}.result.json"
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if result_path.exists():
            try:
                res = json.loads(result_path.read_text())
            except Exception:
                time.sleep(0.2)
                continue
            res.setdefault("ok", False)
            res["id"] = rid
            return res
        time.sleep(0.5)
    hb = heartbeat()
    if timeout_ok or wait_s == 0:
        return {"ok": True, "pending": True, "id": rid, "agent": hb}
    if not hb["alive"]:
        return {"ok": False, "id": rid, "pending": True,
                "error": "neon-ctl is not running on the host (systemctl --user status neon-ctl)"}
    return {"ok": False, "id": rid, "pending": True, "error": f"no answer from neon-ctl within {int(wait_s)} s"}
