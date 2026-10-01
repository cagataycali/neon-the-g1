#!/usr/bin/env bash
# 📶 NEON WiFi watchdog — keep the companion link (wlan0) alive.
#
# NetworkManager already auto-connects to the strongest KNOWN network and
# falls back to the low-priority `neon_net` hotspot profile. This watchdog is
# the belt-and-suspenders layer: if wlan0 ends up with NO connection (all
# known nets gone, NM wedged), it force-connects the `neon_net` fallback so
# the robot is always reachable and the operator can re-pick WiFi from the
# dashboard afterwards.
#
# Does NOT touch eth0 (robot DDS, 192.168.123.0/24).
#
# Env:
#   NEON_FALLBACK_SSID   (default: neon_net)
#   NEON_FALLBACK_PSK    (default: 1234567890)
#   NEON_WIFI_IFACE      (default: wlan0)
set -uo pipefail

IFACE="${NEON_WIFI_IFACE:-wlan0}"
FALLBACK_SSID="${NEON_FALLBACK_SSID:-neon_net}"
FALLBACK_PSK="${NEON_FALLBACK_PSK:-1234567890}"

log() { echo "[$(date '+%F %T')] wifi-watchdog: $*"; }

command -v nmcli >/dev/null 2>&1 || { log "nmcli not found"; exit 0; }

# Is wlan0 connected to a wifi network right now?
active_ssid="$(nmcli -t -f DEVICE,TYPE,STATE,CONNECTION dev status 2>/dev/null \
  | awk -F: -v i="$IFACE" '$1==i && $3=="connected"{print $4}')"

if [ -n "$active_ssid" ]; then
  # connected to something — nothing to do
  exit 0
fi

log "$IFACE not connected — attempting fallback"

# Ensure the neon_net fallback profile exists (self-heal if deleted)
if ! nmcli -t -f NAME con show 2>/dev/null | grep -qx "$FALLBACK_SSID"; then
  log "creating $FALLBACK_SSID profile"
  nmcli con add type wifi con-name "$FALLBACK_SSID" ifname "$IFACE" ssid "$FALLBACK_SSID" >/dev/null 2>&1
  nmcli con modify "$FALLBACK_SSID" \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$FALLBACK_PSK" \
    connection.autoconnect yes connection.autoconnect-priority -10 >/dev/null 2>&1
fi

# Rescan + let NM try known networks first
nmcli dev wifi rescan >/dev/null 2>&1 || true
sleep 3

# Still nothing? force the fallback hotspot
active_ssid="$(nmcli -t -f DEVICE,STATE,CONNECTION dev status 2>/dev/null \
  | awk -F: -v i="$IFACE" '$1==i && $2=="connected"{print $3}')"
if [ -z "$active_ssid" ]; then
  log "forcing connect to $FALLBACK_SSID"
  nmcli con up "$FALLBACK_SSID" >/dev/null 2>&1 \
    && log "connected to $FALLBACK_SSID" \
    || log "fallback connect failed (hotspot not in range?)"
fi
