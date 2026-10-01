# network

<span class="read-badge">45s · ref</span>

Where `neon`, the MCU, and your laptop all sit.

## topology

```mermaid
flowchart LR
  subgraph LAN["🏠 your LAN · WiFi"]
    M["💻 Mac dev<br/>192.168.1.0/24"]
  end

  subgraph G1["🤖 Unitree G1"]
    direction TB
    J["🧠 Jetson Orin NX<br/>192.168.123.164<br/>runs neon agent"]
    C["⚙️ Main MCU<br/>192.168.123.161<br/>sport_mode · loco_service<br/>arm_action"]
    Motors["🦾 motors · arms<br/>🔊 audio · 💡 LEDs"]

    J -.->|"CycloneDDS multicast<br/>on eth0"| C
    C -->|"SPI bus"| Motors
  end

  M <-->|"SSH · WiFi"| J
  M <-->|"optional ethernet"| C

  classDef dev stroke:#007a3d,stroke-width:1.5px
  classDef robot stroke:#666464,stroke-width:1.5px
  class M dev
  class J,C,Motors robot
```

## address plan

| host | iface | ip | role |
|---|---|---|---|
| Jetson | `eth0` | `192.168.123.164/24` | our agent runs here |
| MCU | `eth0` | `192.168.123.161/24` | Unitree embedded — no SSH |
| Jetson | `wlan0` | DHCP on your LAN, `ubuntu.local` over mDNS | companion WiFi: SSH, dashboard, Telegram |
| your Mac | `en0` / `en5` | WiFi or static `192.168.123.100` via USB-C → RJ45 | dev machine |

**Always use `network_interface="eth0"` in tools.** It's the only interface
that sees the motor bus.

## connecting

### SSH in

```bash
ssh unitree@192.168.123.164            # over ethernet (static route)
```

Default password: `123` (yes really).

### WiFi

`make wifi` (interactive) or `make wifi SSID=x PASS=y`; `make wifi-status`,
`make wifi-scan`; the dashboard Configuration drawer has the same picker. The
hostname `ubuntu.local` follows the robot across networks. When `wlan0` loses
every known network the `neon-wifi-watchdog.timer` (every minute) connects the
low-priority fallback profile `neon_net`: create a hotspot with that name on
your phone and the robot comes back reachable. `scripts/change-ip.txt` has the
manual `nmcli` steps.

### mac ethernet setup (dev)

If you want to SSH from Mac over ethernet:

```
System Settings → Network → USB 10/100/1000 LAN →
  Configure IPv4: Manually
  IP:      192.168.123.100
  Subnet:  255.255.255.0
  Router:  (blank)
```

Don't assign `.161` or `.164` — those are the robot.

## CycloneDDS

```bash
cat /home/unitree/cyclonedds_ws/cyclonedds.xml
```

Key settings:

- `NetworkInterfaceAddress=eth0`
- `MaxMessageSize=65500`
- `Discovery ParticipantIndex=auto`

Our tools read this from `CYCLONEDDS_URI`. `env.sh` sets it.

## ports

Nothing exposed by default on the robot itself. When the compose stack runs:

| port | service |
|---|---|
| 8080 | neon-dashboard (HTTPS, self-signed; also published through a Cloudflare tunnel, see [dashboard](../guide/dashboard.md)) |
| 8012 / 8013 | WebXR teleop bridge (WSS / HTTPS) — only when running |
| N/A | Telegram uses long-poll (no inbound port) |

## sanity checks

```bash
ip link show eth0                           # state UP?
ping -c 3 192.168.123.161                  # MCU reachable? (it has no sshd)
echo $CYCLONEDDS_URI                        # env set?
python3 -c "from tools import g1_get_state; print(g1_get_state())"
```

Every tool answering `rc=3104` means `eth0` is down, the wrong interface is
selected, or `CYCLONEDDS_URI` is unset; the symptom table is on
[troubleshooting](../guide/troubleshooting.md).
