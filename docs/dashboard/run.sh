#!/usr/bin/env bash
# Launch the NEON G1 dashboard backend with proper DDS env.
set -e
cd "$(dirname "$0")/../.."        # → repo root
source env.sh 2>/dev/null || true
export G1_NETWORK_INTERFACE="${G1_NETWORK_INTERFACE:-eth0}"
export DASHBOARD_HZ="${DASHBOARD_HZ:-1}"
exec .venv/bin/python docs/dashboard/server.py --port "${DASHBOARD_PORT:-8080}" --hz "$DASHBOARD_HZ"
