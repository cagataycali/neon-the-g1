# docker

<span class="read-badge">30s</span>

The compose stack is the canonical way to run neon on the Jetson. One image,
four containers, host networking so CycloneDDS multicast reaches `eth0`.

```bash
make run     # build if needed, compose up -d, attach to the REPL
make up      # detached
make logs    # tail every container
make down    # stop containers + the bare-metal listeners
```

## the stack

| container | role |
|---|---|
| `neon-agent` | the REPL agent (`agent.py`), auto-starts voice unless `NEON_NO_SPEECH=1` |
| `neon-dashboard` | FastAPI + React cockpit on `https://<robot>:8080`; the **only** process that opens the USB cameras |
| `neon-thinker` | 30 s reflective heartbeat (`thinker_loop.py`) |
| `neon-telegram` | Telegram long-poll listener (`telegram_listener.py`) |

All four share `.memory/` (SQLite: memory, `agent_log`, voice bridge, auth
store) and bind-mount `tools/` from the host, so a tool edit is live after a
container restart without a rebuild. `neon-dashboard` also bind-mounts
`docs/dashboard` (it serves the host's `frontend/dist`) and `.env` (the
Configuration drawer edits it).

Two things are **not** containers: the voice listener (`neon-voice`, a system
unit, needs the USB mic and ALSA directly) and `neon-ctl`, a small host service
the dashboard asks to recreate containers or restart voice, since containers
cannot reach the docker socket. Both are described on [systemd](systemd.md).

## cameras

V4L2 and RealSense devices are single-open. The dashboard owns them; every
other persona pulls a JPEG from `GET /api/camera/<id>/snapshot` with a service
token (`NEON_CAMERA_PROXY`, `NEON_CAMERA_PROXY_TOKEN`), so `take_photo` and
`use_camera` work in voice, Telegram, the thinker and the REPL alike. The
Jetson has no RTC battery and boots at 1970 until NTP: a token minted before
the clock is sane is refused, the units wait for `time-sync.target`, and
`make token-refresh` re-syncs the token into `.env` if a consumer still sees
`401`.

## env

`.env` next to `docker-compose.yml` is auto-loaded; `.env.example` is the
annotated list. The ones that matter:

```bash
AWS_BEARER_TOKEN_BEDROCK=...   AWS_DEFAULT_REGION=us-west-2
NEON_MODEL_ID={{facts:default_model}}   # the dashboard rewrites this line
OPENAI_API_KEY=sk-...                   # default voice provider
VOICE_PROVIDER=openai  VOICE_NAME=alloy  VOICE_MODEL=gpt-realtime
G1_NETWORK_INTERFACE=eth0               # the DDS interface, never change on the robot
TELEGRAM_BOT_TOKEN=  TELEGRAM_ALLOWED_USERS=  TELEGRAM_DEFAULT_CHAT_ID=
NEON_CAMERA_PROXY=https://localhost:8080  NEON_CAMERA_PROXY_TOKEN=   # written by make token-refresh
NEON_NO_SPEECH=1                        # REPL without the voice auto-start
```

## without the robot

The same image runs on a laptop; the DDS tools return `rc=3104` because there
is no bus on `eth0`, everything else (memory, Telegram, dashboard UI) works.
`make run-bare` gives a host venv for tool development.

## next

[systemd](systemd.md){ .md-button } [network](../reference/network.md){ .md-button }
