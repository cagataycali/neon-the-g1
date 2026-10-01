#!/usr/bin/env python3
"""🔑 Refresh the NEON service token in .env files.

Mints the current thinker service token (auth.service_token) — derived from
the auth store's JWT secret — and writes it to NEON_CAMERA_PROXY_TOKEN in the
repo-root .env and the operator env, so the thinker/telegram can keep pulling
camera frames from the (auth-gated) dashboard.

Run after `make auth-clear` (which regenerates the JWT secret) or whenever the
token needs to be re-synced. Idempotent.

Usage:
    scripts/refresh_token.py            # write to .env + dashboard.env
    scripts/refresh_token.py --print    # just print the token
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "docs" / "dashboard"))

KEY = "NEON_CAMERA_PROXY_TOKEN"
PROXY_KEY = "NEON_CAMERA_PROXY"
DEFAULT_PROXY = "https://localhost:8080"

ENV_FILES = [
    REPO / ".env",
    Path(os.getenv("NEON_ENV_FILE", str(Path.home() / ".config" / "neon" / "dashboard.env"))),
]


def _upsert(path: Path, updates: dict) -> None:
    """Set key=value in an env file, preserving order + comments."""
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    out = []
    for ln in lines:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(ln)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def main() -> int:
    import time
    import auth  # from docs/dashboard
    if time.time() < auth.CLOCK_SANE_EPOCH:
        print(f"refusing: clock not synced (time.time()={int(time.time())}); a token minted now "
              "would be dated 1970 and every consumer would get 401. Wait for time-sync.target.",
              file=sys.stderr)
        return 2
    token = auth.service_token("thinker")

    if "--print" in sys.argv:
        print(token)
        return 0

    for f in ENV_FILES:
        _upsert(f, {KEY: token, PROXY_KEY: DEFAULT_PROXY})
        print(f"✓ {KEY} written → {f}")
    print("\n🔄 Restart the consumers to pick it up:")
    print("   docker compose up -d --force-recreate neon-thinker neon-telegram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
