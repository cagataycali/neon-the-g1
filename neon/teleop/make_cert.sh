#!/usr/bin/env bash
# Generate a self-signed cert for WebXR (Quest 3 needs a secure context).
# Output → ~/.config/xr_teleoperate/{cert,key}.pem (televuer + xr_bridge auto-find).
set -euo pipefail
DIR="${1:-$HOME/.config/xr_teleoperate}"
mkdir -p "$DIR"
IP="${ROBOT_IP:-$(hostname -I 2>/dev/null | awk '{print $1}')}"
IP="${IP:-192.168.123.161}"
echo "🔐 generating self-signed cert for IP=$IP → $DIR"
openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$DIR/key.pem" -out "$DIR/cert.pem" -days 3650 \
  -subj "/CN=$IP" \
  -addext "subjectAltName=IP:$IP,DNS:localhost,IP:127.0.0.1"
echo "✅ done. On the Quest, browse to https://$IP:8013/ and accept the warning once."
echo "   (or trust $DIR/cert.pem on the headset for no warning)"
