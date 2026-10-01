"""🎥 Camera-readiness gate for NEON consumers (voice / thinker / telegram).

The dashboard container is the SOLE owner of the USB cameras (V4L2 + RealSense
are single-open). Every other persona pulls frames from the dashboard's
auth-gated HTTP snapshot proxy. Two failure modes at boot / after auth-clear:

  1. BOOT RACE — a consumer starts before the dashboard has come up AND begun
     producing frames. Its camera calls fail and it never self-recovers.
  2. STALE TOKEN — `make auth-clear` regenerates the dashboard's JWT secret,
     invalidating the NEON_CAMERA_PROXY_TOKEN baked into .env at process start.

`wait_for_cameras()` fixes both: it (re)mints a fresh service token from the
dashboard auth store, upserts it into .env AND the live os.environ, then blocks
until the dashboard reports at least one camera streaming (running + frames>0)
— with a soft timeout so it never hangs boot forever.

Call it once at the top of a consumer's main() before starting the agent.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
ENV_FILE = REPO / ".env"
KEY = "NEON_CAMERA_PROXY_TOKEN"
PROXY_KEY = "NEON_CAMERA_PROXY"
DEFAULT_PROXY = "https://localhost:8080"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


def _log(msg: str) -> None:
    print(f"[camera_ready] {msg}", flush=True)


CLOCK_SANE_EPOCH = 1_700_000_000  # the Jetson boots at 1970 until NTP (no RTC battery)


def _token_sane(tok: str) -> bool:
    """A service token minted at a 1970 boot clock decodes fine (iat=47,
    exp 1980) but every consumer gets 401 from the middleware. The image's
    auth.py may predate the clock guard, so check the claims here too."""
    try:
        import base64 as _b64
        import json as _json
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        c = _json.loads(_b64.urlsafe_b64decode(p))
        return float(c.get("iat", 0)) >= CLOCK_SANE_EPOCH and float(c.get("exp", 0)) > time.time() + 60
    except Exception:
        return False


def _mint_token() -> Optional[str]:
    """Mint a fresh service JWT from the dashboard auth store.

    Tries in-process import first (host venv), then the dashboard container.
    Never returns (or persists) a token minted at a 1970 clock, and does not
    mint at all while this process's own clock is unsynced.
    """
    import sys as _sys

    if time.time() < CLOCK_SANE_EPOCH:
        _log("clock not synced yet; not minting a service token")
        return None
    dash = str(REPO / "docs" / "dashboard")
    if dash not in _sys.path:
        _sys.path.insert(0, dash)
    try:
        import auth  # type: ignore

        tok = auth.service_token("voice")
        if tok and _token_sane(tok):
            return tok
        if tok:
            _log("auth store returned a token minted at a 1970 clock; ignoring it")
    except Exception:
        pass
    # fallback: mint inside the dashboard container
    try:
        import subprocess

        out = subprocess.run(
            [
                "docker", "exec", "neon-dashboard", "python", "-c",
                'import sys; sys.path.insert(0,"docs/dashboard"); '
                'import auth; print(auth.service_token("voice"))',
            ],
            capture_output=True, text=True, timeout=15,
        )
        tok = (out.stdout or "").strip()
        return tok if tok and _token_sane(tok) else None
    except Exception:
        return None


def _upsert_env_file(key: str, val: str) -> None:
    if not val:
        return
    try:
        lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
        out, seen = [], False
        for ln in lines:
            if ln.startswith(f"{key}="):
                out.append(f"{key}={val}")
                seen = True
            else:
                out.append(ln)
        if not seen:
            out.append(f"{key}={val}")
        ENV_FILE.write_text("\n".join(out) + "\n")
    except Exception as e:
        _log(f"could not update .env: {e}")


def _http(url: str, token: Optional[str] = None, timeout: float = 6.0):
    """Return (status_code, body_bytes). status_code=None on network error."""
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception:
        return None, b""


def wait_for_cameras(
    timeout: float = 120.0,
    poll: float = 3.0,
    proxy: Optional[str] = None,
) -> bool:
    """Block until dashboard cameras are streaming (or soft-timeout).

    Side effects: refreshes NEON_CAMERA_PROXY_TOKEN in os.environ + .env.
    Returns True if cameras confirmed live, False on soft timeout.
    """
    base = (proxy or os.getenv(PROXY_KEY) or DEFAULT_PROXY).rstrip("/")
    os.environ.setdefault(PROXY_KEY, base)

    # 1. mint + persist a fresh token up-front (self-heal stale .env)
    token = _mint_token()
    if token:
        os.environ[KEY] = token
        _upsert_env_file(KEY, token)
        _log("minted fresh service token → os.environ + .env")
    else:
        token = os.getenv(KEY, "")
        if token and not _token_sane(token):
            _log("existing token was minted at a 1970 clock; dropping it (use_camera re-mints on 401)")
            token = ""
            os.environ.pop(KEY, None)
        else:
            _log("could not mint; using existing token (may be stale)")

    deadline = time.time() + timeout
    while time.time() < deadline:
        # 2a. dashboard reachable? (open endpoint, no auth)
        code, _ = _http(f"{base}/api/health")
        if code != 200:
            _log(f"dashboard /api/health not ready (HTTP {code}); waiting…")
            time.sleep(poll)
            continue

        # 2b. token valid + cameras list?
        code, body = _http(f"{base}/api/cameras", token)
        if code == 401:
            _log("cameras 401 — re-minting token")
            token = _mint_token() or token
            if token:
                os.environ[KEY] = token
                _upsert_env_file(KEY, token)
            time.sleep(poll)
            continue
        if code != 200:
            _log(f"cameras endpoint HTTP {code}; waiting…")
            time.sleep(poll)
            continue

        # 2c. at least one camera running with frames > 0?
        try:
            cams = json.loads(body).get("cameras", [])
        except Exception:
            cams = []
        live = [c for c in cams if c.get("running") and (c.get("frames") or 0) > 0]
        if live:
            ids = ", ".join(c.get("id", "?") for c in live)
            _log(f"✅ cameras live: {ids}")
            return True

        _log("cameras up but no frames yet; waiting…")
        time.sleep(poll)

    _log(f"soft timeout after {timeout}s — proceeding (consumer will retry)")
    return False
