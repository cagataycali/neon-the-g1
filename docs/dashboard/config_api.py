#!/usr/bin/env python3
"""⚙️ NEON dashboard configuration API — .env editor, model id, wifi.

Backs the dashboard's admin panel. Everything here is auth-gated at the
route layer (server.py wraps these with require_auth).

Sources of truth
----------------
  ENV_FILE   the operator env the systemd service loads
             (default: ~/.config/neon/dashboard.env, override NEON_ENV_FILE)
  .env.example  read-only template shown in the editor (repo root)

WiFi
----
Uses `nmcli` (NetworkManager). list / scan / connect / status. Connecting
switches the *companion* WiFi (wlan0) — NOT the robot DDS link (eth0), which
must stay on 192.168.123.0/24.

Model
-----
The model id lives in the env as NEON_MODEL_ID (and STRANDS_MODEL_ID).
set_model rewrites the env, applies it to the dashboard's own agent at once
(chat_agent.rebuild) and reports the personas that still need a container
recreate; restart_services asks the host's neon-ctl (scripts/neon_ctl.py) to
do that through the .memory/ctl channel. In the container ENV_FILE is the
bind-mounted repo .env (NEON_ENV_FILE=/app/.env in docker-compose.yml), the
file compose itself loads, so a recreate picks the change up.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parent.parent.parent
ENV_FILE = Path(os.getenv("NEON_ENV_FILE", str(Path.home() / ".config" / "neon" / "dashboard.env"))).expanduser()
ENV_EXAMPLE = REPO / ".env.example"

# Keys we never expose raw over the API (return masked).
SECRET_RX = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|BEARER)", re.I)

# Curated known model ids for the picker (free-text also allowed).
KNOWN_MODELS = [
    "global.anthropic.claude-opus-4-8",
    "global.anthropic.claude-sonnet-4-5",
]


def _mod(name: str):
    """Import a sibling dashboard module the same way server.py does, so
    config_api and server share ONE module instance (chat_agent keeps the
    live agent in module globals; two copies would disagree)."""
    import importlib
    try:
        return importlib.import_module(f"docs.dashboard.{name}")
    except Exception:
        return importlib.import_module(name)


# ── .env parsing (order-preserving) ───────────────────────────────────
def _read_env_lines() -> List[str]:
    if ENV_FILE.exists():
        return ENV_FILE.read_text().splitlines()
    return []


def _parse(lines: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        out[k.strip()] = v.strip()
    return out


def _mask(key: str, val: str) -> str:
    if val and SECRET_RX.search(key):
        if len(val) <= 6:
            return "•" * len(val)
        return val[:3] + "•" * 6 + val[-2:]
    return val


def get_env(reveal: bool = False) -> Dict[str, Any]:
    """Return env vars (secrets masked unless reveal=True)."""
    env = _parse(_read_env_lines())
    items = []
    for k, v in env.items():
        items.append({
            "key": k,
            "value": v if reveal else _mask(k, v),
            "secret": bool(SECRET_RX.search(k)),
            "set": bool(v),
        })
    return {
        "path": str(ENV_FILE),
        "exists": ENV_FILE.exists(),
        "vars": items,
    }


def get_env_example() -> Dict[str, Any]:
    txt = ENV_EXAMPLE.read_text() if ENV_EXAMPLE.exists() else ""
    return {"path": str(ENV_EXAMPLE), "exists": ENV_EXAMPLE.exists(), "text": txt}


def set_env(updates: Dict[str, str]) -> Dict[str, Any]:
    """Upsert keys into ENV_FILE, preserving comments + order. Blank value
    keeps the key but empties it; sending key=None deletes the line."""
    lines = _read_env_lines()
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)

    seen = set()
    out: List[str] = []
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                seen.add(k)
                nv = updates[k]
                if nv is None:
                    continue  # delete
                out.append(f"{k}={nv}")
                continue
        out.append(ln)

    for k, v in updates.items():
        if k in seen or v is None:
            continue
        out.append(f"{k}={v}")

    ENV_FILE.write_text("\n".join(out) + "\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        pass
    return {"ok": True, "path": str(ENV_FILE), "updated": [k for k in updates if updates[k] is not None]}


# ── model id ──────────────────────────────────────────────────────────
PERSONAS = ["neon-agent", "neon-telegram", "neon-thinker", "neon-dashboard"]


def configured_model() -> str:
    env = _parse(_read_env_lines())
    return (env.get("NEON_MODEL_ID") or env.get("STRANDS_MODEL_ID")
            or os.getenv("NEON_MODEL_ID") or os.getenv("STRANDS_MODEL_ID")
            or "global.anthropic.claude-opus-4-8")


def get_model() -> Dict[str, Any]:
    """configured = what .env says (what a recreated persona will run);
    live = what the dashboard's own agent is answering with right now;
    boot = what this container started with (what the other personas run
    until they are recreated). pending lists the personas whose process
    still carries a different model than .env."""
    chat_agent = _mod("chat_agent")
    configured = configured_model()
    live = chat_agent.live_model()
    boot = _BOOT_MODEL
    pending = [p for p in PERSONAS if p != "neon-dashboard"] if boot != configured else []
    if live is not None and live != configured:
        pending = pending + ["neon-dashboard"]
    return {"current": configured, "configured": configured, "live": live, "boot": boot,
            "pending": pending, "known": KNOWN_MODELS, "ctl": _ctl().heartbeat()}


_BOOT_MODEL = os.getenv("NEON_MODEL_ID") or os.getenv("STRANDS_MODEL_ID") or "global.anthropic.claude-opus-4-8"
_MODEL_RX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}$")


def _ctl():
    return _mod("ctl")


def set_model(model_id: str) -> Dict[str, Any]:
    """Write the model to .env, apply it to the dashboard's own agent now
    (rebuild on the next message), and report which personas still need a
    recreate (POST /api/config/service/restart with their names)."""
    model_id = (model_id or "").strip()
    if not model_id:
        return {"ok": False, "error": "empty model_id"}
    if not _MODEL_RX.match(model_id):
        return {"ok": False, "error": "model id may only contain letters, digits and . _ : / -"}
    set_env({"STRANDS_MODEL_ID": model_id, "NEON_MODEL_ID": model_id})
    os.environ["NEON_MODEL_ID"] = model_id
    os.environ["STRANDS_MODEL_ID"] = model_id
    _mod("chat_agent").rebuild()
    pending = [p for p in PERSONAS if p != "neon-dashboard"]
    return {"ok": True, "model": model_id, "applied": ["neon-dashboard"], "pending": pending,
            "note": "dashboard chat uses the new model on its next message; "
                    "the other personas apply it when recreated"}


def restart_services(services: List[str]) -> Dict[str, Any]:
    """Recreate persona containers through neon-ctl. When the dashboard is
    in the list the result can never be read (this process dies), so the
    request is fire-and-forget and the UI polls /api/health."""
    services = [str(s) for s in (services or [])]
    ctl = _ctl()
    err = ctl.validate("recreate", services)
    if err:
        return {"ok": False, "error": err}
    if "neon-dashboard" in services:
        # recreate the others first so their result is visible, then ourselves
        others = [s for s in services if s != "neon-dashboard"]
        first = ctl.request_ctl("recreate", others, wait_s=120) if others else {"ok": True}
        if not first.get("ok"):
            return first
        own = ctl.request_ctl("recreate", ["neon-dashboard"], wait_s=0)
        return {"ok": True, "pending": True, "self_restart": True, "services": services,
                "others": first, "id": own.get("id"),
                "message": "recreating; poll /api/health until it answers again"}
    return ctl.request_ctl("recreate", services, wait_s=120)


# ── camera proxy token health (NEON_CAMERA_PROXY_TOKEN) ───────────────
def _jwt_exp(token: str) -> float | None:
    """exp claim of a JWT WITHOUT verifying it (health display only)."""
    import base64
    import json
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0)) or None
    except Exception:
        return None


def camera_token_health() -> Dict[str, Any]:
    """ok | expired | missing | invalid for the token in .env. 'expired'
    includes the 1980-dated tokens minted at a 1970 boot clock."""
    import time
    env = _parse(_read_env_lines())
    tok = env.get("NEON_CAMERA_PROXY_TOKEN") or os.getenv("NEON_CAMERA_PROXY_TOKEN", "")
    if not tok:
        return {"state": "missing", "exp": None}
    exp = _jwt_exp(tok)
    if exp is None:
        return {"state": "invalid", "exp": None}
    state = "ok" if exp > time.time() + 60 else "expired"
    return {"state": state, "exp": exp, "clock_synced": time.time() > 1_700_000_000}


def refresh_camera_token() -> Dict[str, Any]:
    """Mint a fresh token on the host (scripts/refresh_token.py via neon-ctl)
    and recreate the two consumers. Refused while the clock is unsynced."""
    import time
    if time.time() < 1_700_000_000:
        return {"ok": False, "error": "clock not synced yet; a token minted now would be dated 1970"}
    return _ctl().request_ctl("token-refresh", wait_s=150)


# ── wifi (nmcli) ──────────────────────────────────────────────────────
def _nmcli(*args: str, timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)


def wifi_available() -> bool:
    return shutil.which("nmcli") is not None


def wifi_status() -> Dict[str, Any]:
    if not wifi_available():
        return {"available": False, "error": "nmcli not found"}
    try:
        r = _nmcli("-t", "-f", "ACTIVE,SSID,SIGNAL,DEVICE", "device", "wifi")
        active = None
        for ln in r.stdout.splitlines():
            parts = ln.split(":")
            if parts and parts[0] == "yes":
                active = {"ssid": parts[1], "signal": parts[2] if len(parts) > 2 else "",
                          "device": parts[3] if len(parts) > 3 else ""}
                break
        return {"available": True, "connected": active is not None, "active": active}
    except Exception as e:
        return {"available": True, "error": str(e)}


def wifi_scan() -> Dict[str, Any]:
    if not wifi_available():
        return {"available": False, "error": "nmcli not found", "networks": []}
    try:
        _nmcli("device", "wifi", "rescan", timeout=10)
    except Exception:
        pass
    try:
        r = _nmcli("-t", "-f", "ACTIVE,SSID,SIGNAL,SECURITY", "device", "wifi", "list")
        nets = {}
        for ln in r.stdout.splitlines():
            p = ln.split(":")
            if len(p) < 4:
                continue
            ssid = p[1].strip()
            if not ssid:
                continue
            sig = int(p[2]) if p[2].isdigit() else 0
            # dedupe by strongest signal
            if ssid not in nets or sig > nets[ssid]["signal"]:
                nets[ssid] = {"ssid": ssid, "signal": sig,
                              "security": p[3] or "open", "active": p[0] == "yes"}
        ordered = sorted(nets.values(), key=lambda n: n["signal"], reverse=True)
        return {"available": True, "networks": ordered}
    except Exception as e:
        return {"available": True, "error": str(e), "networks": []}


def wifi_connect(ssid: str, password: str = "") -> Dict[str, Any]:
    if not wifi_available():
        return {"ok": False, "error": "nmcli not found"}
    ssid = (ssid or "").strip()
    if not ssid:
        return {"ok": False, "error": "empty ssid"}
    try:
        args = ["device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        r = _nmcli(*args, timeout=45)
        ok = r.returncode == 0
        return {"ok": ok, "ssid": ssid,
                "message": (r.stdout or r.stderr).strip()[:300]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "connect timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
