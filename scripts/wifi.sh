#!/usr/bin/env bash
# 📶 NEON WiFi setup — scan, pick, connect. First-user friendly.
#
# Manages the Jetson's *companion* WiFi (wlan0). Does NOT touch the robot's
# DDS link (eth0, 192.168.123.0/24) — that stays put.
#
# Usage:
#   scripts/wifi.sh                 # interactive: list networks → pick → password
#   scripts/wifi.sh <SSID>          # connect to SSID (prompts for password)
#   scripts/wifi.sh <SSID> <PASS>   # connect non-interactively
#   scripts/wifi.sh --status        # show current connection
#   scripts/wifi.sh --scan          # just list networks
set -euo pipefail

if ! command -v nmcli >/dev/null 2>&1; then
  echo "✗ nmcli (NetworkManager) not found — can't manage WiFi here." >&2
  exit 1
fi

status() {
  echo "📶 Current WiFi:"
  nmcli -t -f ACTIVE,SSID,SIGNAL,DEVICE dev wifi 2>/dev/null \
    | awk -F: '$1=="yes"{printf "   ● %s  (%s%%, %s)\n",$2,$3,$4}' \
    || echo "   (not connected)"
}

scan() {
  echo "📡 Scanning…" >&2
  nmcli dev wifi rescan >/dev/null 2>&1 || true
  sleep 1
  # dedupe by SSID, strongest signal wins; show index
  nmcli -t -f ACTIVE,SSID,SIGNAL,SECURITY dev wifi list 2>/dev/null \
    | awk -F: '$2!=""' \
    | sort -t: -k3 -nr \
    | awk -F: '!seen[$2]++'
}

connect() {
  local ssid="$1" pass="${2:-}"
  echo "🔗 Connecting to \"$ssid\"…"
  if [ -n "$pass" ]; then
    nmcli dev wifi connect "$ssid" password "$pass"
  else
    nmcli dev wifi connect "$ssid"
  fi
  echo
  status
}

case "${1:-}" in
  --status|-s) status; exit 0 ;;
  --scan)      scan | awk -F: '{printf "  %s  (%s%%)  %s\n",$2,$3,($4==""?"open":$4)}'; exit 0 ;;
  "" )
    # interactive
    mapfile -t NETS < <(scan)
    if [ "${#NETS[@]}" -eq 0 ]; then echo "No networks found."; exit 1; fi
    echo "📶 Available networks:"
    i=1
    for n in "${NETS[@]}"; do
      ssid=$(echo "$n" | cut -d: -f2)
      sig=$(echo "$n" | cut -d: -f3)
      sec=$(echo "$n" | cut -d: -f4); [ -z "$sec" ] && sec="open"
      lock=$([ "$sec" = "open" ] && echo " " || echo "🔒")
      printf "  %2d) %s %-28s %s%%\n" "$i" "$lock" "$ssid" "$sig"
      i=$((i+1))
    done
    echo
    read -rp "Pick a number (or type SSID): " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#NETS[@]}" ]; then
      ssid=$(echo "${NETS[$((choice-1))]}" | cut -d: -f2)
      sec=$(echo "${NETS[$((choice-1))]}" | cut -d: -f4)
    else
      ssid="$choice"; sec="?"
    fi
    if [ "$sec" = "open" ]; then
      connect "$ssid"
    else
      read -rsp "Password for \"$ssid\": " pass; echo
      connect "$ssid" "$pass"
    fi
    ;;
  * )
    # SSID given as arg
    ssid="$1"; pass="${2:-}"
    if [ -z "$pass" ]; then
      read -rsp "Password for \"$ssid\" (blank if open): " pass; echo
    fi
    connect "$ssid" "$pass"
    ;;
esac
