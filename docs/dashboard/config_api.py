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
The model id lives in the env as STRANDS_MODEL_ID (and NEON_MODEL_ID).
Changing it rewrites the env; the agent picks it up on next (re)build.
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
def get_model() -> Dict[str, Any]:
    env = _parse(_read_env_lines())
    current = env.get("STRANDS_MODEL_ID") or env.get("NEON_MODEL_ID") or os.getenv("NEON_MODEL_ID", "global.anthropic.claude-opus-4-8")
    return {"current": current, "known": KNOWN_MODELS}


def set_model(model_id: str) -> Dict[str, Any]:
    model_id = (model_id or "").strip()
    if not model_id:
        return {"ok": False, "error": "empty model_id"}
    set_env({"STRANDS_MODEL_ID": model_id, "NEON_MODEL_ID": model_id})
    return {"ok": True, "model": model_id,
            "note": "restart the dashboard service (or the agent rebuilds) to apply"}


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
