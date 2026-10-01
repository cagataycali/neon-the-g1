#!/usr/bin/env bash
# 🎥 Boot-time camera readiness gate for NEON voice/thinker/telegram.
#
# The dashboard container is the SOLE owner of the USB cameras (V4L2/RealSense
# are single-open). Consumers (voice, thinker, telegram) pull frames from the
# dashboard's auth-gated HTTP proxy. At boot the dashboard takes time to come
# up AND to start producing frames — if a consumer starts first, its camera
# calls 401/fail and it never recovers until manually restarted.
#
# This script (run as ExecStartPre) blocks until the dashboard is:
#   1. reachable            (GET /api/health, open endpoint)
#   2. authenticating       (fresh service token accepted by /api/cameras)
#   3. actually streaming    (>=1 camera with running=true and frames>0)
#
# It ALSO self-heals the token: after `make auth-clear` the dashboard mints a
# new JWT secret, invalidating the token baked into .env. We mint a fresh one
# from the dashboard's auth store and upsert it into .env so the consumer that
# starts right after us inherits a valid token via EnvironmentFile.
#
# Exit 0 once cameras are live (or after a soft timeout — we don't want to
# block boot forever; the consumer's own proxy fallback + token-refresh will
# retry). Never hard-fails the unit.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO}/.env"
DASH_URL="${NEON_CAMERA_PROXY:-https://localhost:8080}"
TIMEOUT="${CAMERA_READY_TIMEOUT:-120}"   # seconds; soft cap so boot never hangs
POLL="${CAMERA_READY_POLL:-3}"
KEY="NEON_CAMERA_PROXY_TOKEN"

log() { echo "[wait_camera_ready] $*"; }

# ── 1. mint a fresh service token from the dashboard auth store ───────────
mint_token() {
  # Prefer the dashboard container (has fastapi + auth store). Fall back to
  # host python if the module imports cleanly.
  local tok=""
  if command -v docker >/dev/null 2>&1; then
    tok=$(docker exec neon-dashboard python -c \
      'import sys; sys.path.insert(0,"docs/dashboard"); import auth; print(auth.service_token("voice"))' \
      2>/dev/null | tr -d '\r\n' || true)
  fi
  if [ -z "$tok" ]; then
    tok=$("${REPO}/.venv/bin/python" -c \
      'import sys; sys.path.insert(0,"docs/dashboard"); import auth; print(auth.service_token("voice"))' \
      2>/dev/null | tr -d '\r\n' || true)
  fi
  echo "$tok"
}

upsert_env() {
  local key="$1" val="$2"
  [ -z "$val" ] && return 0
  if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s#^${key}=.*#${key}=${val}#" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

# curl helper (self-signed → -k). Returns body; sets global HTTP_CODE.
HTTP_CODE=""
fetch() {
  local url="$1" tok="${2:-}"
  local args=(-sk -m 6 -w '\n%{http_code}')
  [ -n "$tok" ] && args+=(-H "Authorization: Bearer ${tok}")
  local out; out=$(curl "${args[@]}" "$url" 2>/dev/null || true)
  HTTP_CODE="${out##*$'\n'}"
  echo "${out%$'\n'*}"
}

# ── 2. mint + persist a fresh token up-front ──────────────────────────────
TOKEN=$(mint_token)
if [ -n "$TOKEN" ]; then
  upsert_env "$KEY" "$TOKEN"
  log "minted fresh service token → .env"
else
  # fall back to whatever's already in .env
  TOKEN=$(grep "^${KEY}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
  log "could not mint; using existing .env token (may be stale)"
fi

# ── 3. poll until cameras are live (or soft timeout) ──────────────────────
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  now=$(date +%s)
  if [ "$now" -ge "$deadline" ]; then
    log "soft timeout after ${TIMEOUT}s — proceeding anyway (consumer will retry)"
    exit 0
  fi

  # 3a. dashboard reachable? (open endpoint)
  fetch "${DASH_URL}/api/health" >/dev/null
  if [ "$HTTP_CODE" != "200" ]; then
    log "dashboard /api/health not ready (HTTP ${HTTP_CODE:-none}); waiting…"
    sleep "$POLL"; continue
  fi

  # 3b. token valid + cameras streaming?
  body=$(fetch "${DASH_URL}/api/cameras" "$TOKEN")
  if [ "$HTTP_CODE" = "401" ]; then
    # token went stale mid-wait (rare) — re-mint once
    log "cameras 401 — re-minting token"
    TOKEN=$(mint_token); upsert_env "$KEY" "$TOKEN"
    sleep "$POLL"; continue
  fi
  if [ "$HTTP_CODE" != "200" ]; then
    log "cameras endpoint HTTP ${HTTP_CODE:-none}; waiting…"
    sleep "$POLL"; continue
  fi

  # 3c. is at least one camera running with frames > 0?
  # crude JSON scan (no jq dependency): look for "running":true and "frames":N>0
  if echo "$body" | grep -q '"running":[[:space:]]*true' \
     && echo "$body" | grep -qE '"frames":[[:space:]]*[1-9]'; then
    log "✅ cameras live: $(echo "$body" | grep -oE '"id":"[^"]+"' | tr '\n' ' ')"
    exit 0
  fi

  log "cameras up but no frames yet; waiting…"
  sleep "$POLL"
done
