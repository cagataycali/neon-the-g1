#!/usr/bin/env python3
"""🔥 Enrol Neon as a body of your tiny (tiny.technology) — one command, on the robot.

    make tiny-enroll                    # mint + enrol (pairing code + QR if the robot has no session)
    scripts/tiny_enroll.py --print      # just print the bearer tiny will use
    scripts/tiny_enroll.py --dry-run    # show the row, send nothing

tiny dials OUT to the dashboard (kind:endpoint): GET /api/telemetry, GET /api/camera/snapshot,
POST /api/chat {prompt} — every call carries `Authorization: Bearer <token>`. That token is the
dashboard's own long-lived *service JWT* (auth.service_token("tiny"), ~10 y, cached in the auth
store) — the same mechanism the thinker uses for camera frames — so nothing in server.py changes.

The row on tiny is {name: neon-the-g1, kind: endpoint, platform: neon-the-g1, url: https://neon.cagatay.my,
capabilities: [body:neon-the-g1, …]}. Re-running is safe: tiny re-points the existing row in place
(one row per body). The bearer travels only into that row; it is never printed unless you ask (--print).

Order of resolution for the token: host python with docs/dashboard deps → `docker compose exec
neon-dashboard` (the auth store is the same ./.memory volume) → give up with the exact command.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
URL = os.getenv("NEON_PUBLIC_URL", "https://neon.cagatay.my")
BODY = "neon-the-g1"
NAME = os.getenv("NEON_TINY_NAME", BODY)
TOKEN_NAME = "tiny"


def mint_host() -> str | None:
    sys.path.insert(0, str(REPO / "docs" / "dashboard"))
    try:
        os.chdir(REPO)  # auth.AUTH_STORE default is relative: .memory/.neon_auth.json
        import auth  # type: ignore
        return auth.service_token(TOKEN_NAME)
    except Exception as e:  # missing fastapi/jwt on the host is the normal case
        print(f"(host mint unavailable: {e.__class__.__name__}: {e}) — trying the dashboard container", file=sys.stderr)
        return None


def mint_docker() -> str | None:
    docker = shutil.which("docker")
    if not docker:
        return None
    code = f"import sys; sys.path.insert(0,'docs/dashboard'); import auth; print(auth.service_token({TOKEN_NAME!r}))"
    r = subprocess.run([docker, "compose", "exec", "-T", "neon-dashboard", "python", "-c", code],
                       cwd=REPO, capture_output=True, text=True)
    tok = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if r.returncode == 0 and tok.count(".") == 2:
        return tok
    print(f"(docker mint failed: {(r.stderr or r.stdout).strip()[:200]})", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", action="store_true", help="print the bearer and exit")
    ap.add_argument("--dry-run", action="store_true", help="show the row tiny would get; send nothing")
    ap.add_argument("--name", default=NAME, help=f"row name on tiny (default {NAME})")
    a = ap.parse_args()

    tok = mint_host() or mint_docker()
    if not tok:
        print("could not mint the service token — on the robot, with the dashboard running:\n"
              f"  docker compose exec neon-dashboard python -c \"import sys; sys.path.insert(0,'docs/dashboard'); import auth; print(auth.service_token('{TOKEN_NAME}'))\"\n"
              f"then: npx tiny-tech@latest enroll --endpoint {URL} --secret <that> --body {BODY} --name {a.name}", file=sys.stderr)
        return 2
    if a.print:
        print(tok)
        return 0

    npx = shutil.which("npx")
    if not npx:
        print(f"npx not found — install node ≥18, then: npx tiny-tech@latest enroll --endpoint {URL} --secret <token> --body {BODY} --name {a.name}", file=sys.stderr)
        return 2
    cmd = [npx, "-y", "tiny-tech@latest", "enroll", "--endpoint", URL, "--body", BODY, "--name", a.name]
    if a.dry_run:
        cmd.append("--dry-run")
    # the bearer rides the environment, not argv (ps must not show it)
    env = {**os.environ, "TINY_ENDPOINT_SECRET": tok}
    print(f"→ {' '.join(cmd)}  (secret via TINY_ENDPOINT_SECRET)", file=sys.stderr)
    return subprocess.call(cmd, cwd=REPO, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
