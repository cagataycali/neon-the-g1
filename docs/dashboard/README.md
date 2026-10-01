# 🌐 NEON G1 Dashboard

The Unitree G1 cockpit in the Strands design language: cameras, lidar, telemetry and the agent,
behind a passkey gate. **React + TypeScript + Vite** frontend, **FastAPI** backend, served behind a **Cloudflare Tunnel** at
**https://neon.cagatay.my**.

```
Browser ──► neon.cagatay.my (Cloudflare Tunnel)
                │
                ▼
        cloudflared ──► localhost:8080  (FastAPI / uvicorn)
                              │
                              ├─ GET /                → React SPA (frontend/dist)
                              ├─ GET /api/health      → liveness + loaded tools
                              ├─ GET /api/telemetry   → one-shot snapshot
                              ├─ GET /api/log?limit=N → cross-persona agent_log
                              ├─ GET /api/joints      → joint reference
                              └─ WS  /ws              → 1 Hz live telemetry + log
                                       │
                                       ▼
                              tools/g1_state, g1_battery, g1_mainboard,
                              g1_slam, g1_lidar, agent_log  (DDS on eth0)
```

## Layout

```
docs/dashboard/
├── server.py            FastAPI backend (polls G1 DDS tools + agent_log)
├── run.sh               launcher (sources env.sh, runs uvicorn)
├── README.md            this file
└── frontend/            React + TS + Vite source
    ├── src/
    │   ├── App.tsx              dashboard layout
    │   ├── types.ts             telemetry/log type defs
    │   ├── styles.css           Strands design tokens (paper / dark) + every component style
    │   ├── lib/scheme.ts        colour scheme toggle (auto / paper / dark, localStorage neon-scheme)
    │   ├── lib/useSocket.ts     auto-reconnecting WS hook
    │   ├── lib/util.ts          formatters
    │   └── components/          Brand (STRANDS wordmark), Icons (line icons), AuthGate,
    │                            StateCard, PostureCard, TelemetryGraphs, CameraCard,
    │                            LidarView, AgentDock, LogFeed, ConfigPanel, TeleopPanel
    └── dist/             built SPA (served by server.py)
```

## Cards

| Card | Source | Shows |
|---|---|---|
| Telemetry | `g1_battery` + `g1_read_lowstate` | SOC (the one green number; warn colour under 20 %), V / A / C, sparklines for voltage, current, roll, pitch, yaw, knee |
| Controller | `g1_get_state` | mode (ai), FSM id + name, arm ready / arm locked as outlined pills |
| Posture | `g1_read_lowstate` | SVG stick figure in ink (bends with the knee angle), posture pill, roll / pitch / yaw, torque |
| View stage | `/api/camera/*`, `/ws/lidar` | color / depth camera tiles or the Livox point cloud (ink near, green far) |
| Activity log (drawer) | `agent_log` | live cross-persona feed, 2px left rule per persona: voice green, telegram ink, shell muted, dispatch warn |
| Configuration (drawer) | `/api/config/*`, `/api/auth/*` | scheme, model id, WiFi, .env (secrets masked), passkeys |

## Design

The cockpit follows the Strands design language (the one used by the strands-labs/robots docs):

- **Tokens** live at the top of `frontend/src/styles.css` as `--sr-*` custom properties on `:root`
  (paper: white page, black ink, green `#007a3d`) and `html[data-scheme="dark"]` (black page, white
  ink, green `#00cc60`). Surfaces are flat: 1px borders, 8px radius, no blur, gradient or shadow.
  The only non-green signal colour is the warn amber (`#946e00` / `#f6bc00`): battery under 20 %,
  arm locked, offline, errors.
- **Scheme**: `index.html` sets `data-scheme` before paint from `localStorage["neon-scheme"]` or the
  OS preference; the Configuration drawer has an auto / paper / dark row (`lib/scheme.ts`).
- **Type**: JetBrains Mono for labels, pills, numbers and headings; Space Grotesk for body text.
  Both load from Google Fonts in `index.html`.
- **Brand**: `components/Brand.tsx` renders the pixel STRANDS wordmark (fill `--sr-accent`) with the
  `/ neon` project label; `public/neon.svg` is the Strands mark. `components/Icons.tsx` holds the
  inline line icons (no emoji anywhere in the UI).
- **Keyboard**: Escape closes the log and configuration drawers (`role="dialog"`); Enter sends a
  message to the agent. axe (WCAG 2.1 AA) reports 0 violations on every view in both schemes.

Every field degrades gracefully to "offline" if DDS is unreachable.

## Develop

```bash
# backend (terminal 1)
cd /home/unitree/neon-the-g1 && source env.sh
.venv/bin/python docs/dashboard/server.py --port 8080 --hz 1

# frontend hot-reload (terminal 2) — proxies /api + /ws to :8080
cd docs/dashboard/frontend
nvm use 20 && npm run dev      # http://localhost:5173

# production build → frontend/dist (what server.py serves)
npm run build
```

## Production (already installed as systemd user services)

```bash
systemctl --user status neon-dashboard   # FastAPI backend on :8080
systemctl --user status neon-tunnel      # cloudflared → neon.cagatay.my
systemctl --user restart neon-dashboard  # after a rebuild
journalctl --user -u neon-dashboard -f   # live backend logs
journalctl --user -u neon-tunnel -f      # live tunnel logs
```

Both are `enabled` + `loginctl enable-linger unitree` is set, so they
survive logout and reboot.

### Rebuild + redeploy after frontend changes

`docker-compose.yml` mounts `./docs/dashboard` over the image, so the container serves the
**host's** `frontend/dist`. Build it on the Jetson, then restart the container:

```bash
cd ~/neon-the-g1 && git pull
cd docs/dashboard/frontend && npm install && npm run build && cd -
docker compose restart neon-dashboard        # or: make dashboard (rebuilds the image too)
```

## Cloudflare tunnel (one-time setup, already done)

```bash
cloudflared tunnel login                              # browser auth on cagatay.my
cloudflared tunnel create neon                        # id 062723df-…
cloudflared tunnel route dns --overwrite-dns neon neon.cagatay.my
# ~/.cloudflared/config.yml → ingress neon.cagatay.my → http://localhost:8080
```

## Tunables (env vars on the dashboard service)

| var | default | meaning |
|---|---|---|
| `DASHBOARD_PORT` | 8080 | uvicorn port |
| `DASHBOARD_HZ` | 1 | telemetry broadcast rate |
| `G1_NETWORK_INTERFACE` | eth0 | DDS interface |


## 📹 Camera streaming (added)

The G1 has **no DDS video topic** — cameras are USB (RealSense D435i / Logitech
Brio 4K) and the headset path uses WebRTC (teleimager). The dashboard grabs
USB frames in a background thread (reusing `tools/use_camera` internals) and
serves MJPEG:

| endpoint | purpose |
|---|---|
| `GET /api/camera/stream`   | `multipart/x-mixed-replace` MJPEG — point an `<img src>` at it |
| `GET /api/camera/snapshot` | single JPEG (latest frame) |
| `GET /api/camera/status`   | grabber state (backend, frames, fps, resolution) |

`camera_stream.py` holds ONE capture device open (V4L2/RealSense dislike
open/close churn); all HTTP clients share the latest frame. Falls back through
Logitech Brio → any free V4L2 node → RealSense. Renders a "NO CAMERA SIGNAL"
placeholder if nothing opens.

Tunables (env on the service):

| var | default | meaning |
|---|---|---|
| `DASHBOARD_CAM_SOURCE` | auto | auto / logitech / realsense / v4l2 |
| `DASHBOARD_CAM_W` / `_H` | 1280 / 720 | capture resolution |
| `DASHBOARD_CAM_FPS` | 15 | grab + stream rate |
| `DASHBOARD_CAM_Q` | 70 | JPEG quality |

In the UI: **Telemetry tab → Robot Camera card** ("Start live feed").

## 🥽 WebXR teleop (integrated)

The **Teleop tab** surfaces the existing Quest-3 WebXR experience
(`docs/teleop/index.html` driven by `neon.teleop.xr_bridge`):

- Auto-fills the three URLs from the robot IP:
  - **Open on Quest 3** → `https://<ip>:8013/` (HTTPS static page)
  - **Pose uplink (WSS)** → `wss://<ip>:8012/xr`
  - **Camera downlink (WebRTC)** → `https://<ip>:60001/offer` (teleimager)
- Embeds an iframe preview of the teleop page.
- Shows the launch command (`./scripts/run_xr_teleop.sh`).

The headset connects **directly to the robot** (low latency, needs the
self-signed cert) — the dashboard is the launchpad + telemetry HUD, not the
video relay for VR (WebXR needs the robot's stereo WebRTC, not MJPEG).

## 🔬 End-to-end data flow (verified 2026-06-25)

Probed every source live on the robot. Status:

| field | source | result |
|---|---|---|
| 🔋 battery | `rt/lf/bmsstate` (DDS sub) | ✅ live — 78% · 52V · 36°C |
| 🤖 lowstate | `rt/lowstate` (DDS sub) | ✅ live — posture · IMU rpy · knee · `mode_machine` |
| 📡 lidar | `rt/utlidar` (DDS sub) | ✅ live cloud |
| 🧠 state | loco **RPC** (CheckMode/GetFsmId) | ⚠️ wedged on robot (`rc=3102/3104`) → graceful fallback |
| 🛰️ mainboard | `rt/mainboardstate` | not published by this FW → "offline" |
| 🗺️ slam | kiss-icp | idle until `g1_slam_start` |
| 📹 camera | USB Brio `/dev/video0` (MJPEG) | ✅ live 1280×720 @ 15fps |

### The two bugs that were fixed
1. **`_unwrap` only read `content[0].text`** — but tools return data in
   `content[*].json` blocks (battery/lowstate were silently dropped → blank UI).
   Rewrote to merge ALL json + text blocks into a flat dict.
2. **`g1_get_state` loco RPC hangs 5s+** when the controller is idle, which was
   running *inline in the asyncio event loop* → froze the whole server (every
   endpoint returned 000). Fixed by:
   - A **background collector thread** that refreshes a shared snapshot.
   - **Per-tool hard wall-clock timeouts** via a `ThreadPoolExecutor`
     (`future.result(timeout=2.0)`) so a wedged RPC can never block HTTP/WS.
   - **Atomic snapshot swap + last-good retention** so slow-tick refreshes
     never flash `null` battery/posture in the UI.

### loco RPC "wedged" — what it means
`rc=3102/3104` = the robot's locomotion RPC service isn't answering. This is a
**robot-side** state (controller idle / not in an active FSM), not a dashboard
bug. The dashboard detects it, times out cleanly, and shows arm-ready derived
from LowState `mode_machine` (∈{5,6}) with an "RPC wedged · LowState" badge.
Real FSM id/name reappears automatically once the loco controller engages
(e.g. after a stand command or remote power-on).

## 🚀 v2 — LiDAR 3D, multi-camera, in-dashboard agent (2026-06-25)

Inspired by Ari's `g1-example-codebase` (aiohttp + controllers + R3F). NEON's
dashboard now matches/exceeds it while keeping our FastAPI + tools/ reuse.

### New backend modules
- `lidar_stream.py` — reuses `tools/g1_lidar` DDS sub + `_cloud_to_numpy`,
  streams **compact binary** point clouds (7 bytes/pt: x/y/z int16 + intensity u8)
  over `WS /ws/lidar` at 10 Hz. `GET /api/lidar/status` for stats.
- `camera_stream.py` (rewritten) — **multi-camera manager**: `realsense_color`,
  `realsense_depth` (pyrealsense2 colorized TURBO), `brio`. Each holds one device,
  served at `GET /api/camera/{id}/stream|snapshot`, listed at `GET /api/cameras`.
- `chat_agent.py` — builds a NEON `build_agent("shell")` with the **full robot
  toolset**; `POST /api/chat`, `GET /api/chat/status`, `POST /api/chat/reset`.
  Mirrors turns into the cross-persona `agent_log`. Creds via
  `EnvironmentFile=~/.config/neon/dashboard.env` (AWS bearer token).

### New frontend (4 tabs)
- **📊 Telemetry** — battery, controller state, posture+IMU, multi-camera, system, log
- **📡 LiDAR** — React-Three-Fiber 3D point cloud (`useLidar` decodes the binary WS,
  `LidarView` renders with OrbitControls, distance-ramp neon coloring)
- **🥽 Teleop** — LOCAL `https://<lan-ip>:8013/` (8012 WSS pose, 60001 WebRTC cam)
- **🧠 Agent** — chat with NEON in-browser; it can call any robot tool

### Access
| URL | what |
|---|---|
| `https://192.168.1.175:8080/` | LOCAL dashboard (HTTPS, self-signed) |
| `https://neon.cagatay.my/` | public (cloudflare tunnel → HTTPS origin, noTLSVerify) |
| `https://192.168.1.175:8013/` | WebXR teleop page (start `./scripts/run_xr_teleop.sh`) |

Dashboard is now **HTTPS** (`DASHBOARD_SSL=1`, cert from `~/.config/xr_teleoperate`)
so the teleop HTTPS iframe embeds without mixed-content blocking, and WS auto-
upgrades to `wss://`.

### deps added to .venv
`strands-agents`, `strands-agents-tools`, `requests`, `fastapi`, `uvicorn`,
plus frontend `three` + `@react-three/fiber` + `@react-three/drei`.

### Why teleop isn't on neon.cagatay.my
WebXR needs the headset ↔ robot **directly** on the LAN (low latency + the Quest
must be local). The dashboard tab is the launchpad; teleop runs LAN-only by design.

## 🎛️ v3 — Cockpit redesign + dockerized + full-control agent (2026-06-25)

10-cycle iteration. Major changes:

### Docker
- Renamed compose service `g1-agent` → **`neon-agent`** (+ `g1-bt`→`neon-bt`).
- New **`neon-dashboard`** compose service: runs `docs/dashboard/server.py` on
  :8080 HTTPS, shares DDS (host net) + USB cameras + `.memory`.
- Dockerfile **stage 0** builds the React frontend (node:20) → copied into the
  runtime image's `docs/dashboard/frontend/dist`.
- `make dashboard` / `make dashboard-logs` targets.
- `requirements.txt` += fastapi, uvicorn[standard].

### Full-control agent in the dashboard
- `chat_agent.py` builds the SAME agent as `agent.py` — NEON_PROMPT (unhinged
  embodied persona), all 24 robot tools, same model. `/api/chat` controls the
  G1 exactly like the REPL.
- **Non-blocking**: agent.py gets `NEON_LAZY_AGENT=1` (skips its eager DDS
  build); the dashboard builds its own agent in a background thread (status
  returns `building:true` instantly). Live-state is injected from the
  dashboard's CACHED snapshot (not blocking DDS) so a wedged loco RPC never
  stalls a chat turn. Verified ~5s round-trips.

### Cockpit layout (3 zones, no tabs for the main view)
- **LEFT**: cameras (multi-cam grid) + LiDAR 3D (always visible)
- **CENTER**: realtime telemetry — battery hero + SOC bar + 6 live sparklines
  (voltage, current, roll, pitch, yaw, knee), controller state, posture, log
- **RIGHT**: NEON agent chat (always visible, full-height, quick-prompt chips)
- **Teleop** is a toggle button (full-screen overlay) since it's LAN-only.
- New: `Sparkline.tsx` (zero-dep SVG charts), `TelemetryGraphs.tsx`
  (rolling 120-sample buffers), `useTimeSeries.ts`.

### Access unchanged
`https://192.168.1.175:8080` (local) · `https://neon.cagatay.my` (tunnel).
