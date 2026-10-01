# docker

<span class="read-badge">30s</span>

Same image on your laptop (no hardware) and on the Jetson.

## on the robot

```bash
make up      # docker compose up -d
make logs    # tail the agent
make down
```

The container binds `eth0` (`network_mode: host`, required for CycloneDDS
multicast), mounts `/dev/bus/usb` for cameras + Brio mic, and reads
`CYCLONEDDS_URI` from the robot's `cyclonedds.xml`.

## the stack

`docker compose up` brings up four services that share `.memory/`, the
CycloneDDS bus, and the camera nodes:

| service | what |
|---|---|
| `neon-agent` | the REPL agent (`agent.py`) + auto-started voice |
| `neon-dashboard` | FastAPI telemetry + camera + lidar + chat UI on `https://<robot-ip>:8080` |
| `neon-thinker` | 30s reflective heartbeat loop (`thinker_loop.py`) |
| `neon-telegram` | Telegram long-poll listener (`telegram_listener.py`) |

## on your mac (no robot)

There is no separate Mac compose file — the same image runs, but the DDS
motor tools return errors when they can't reach the bus on `eth0`. Use
`make run-bare` for a host venv to build and test tools against the Strands
loop without the robot hardware.

## env

Set these in `.env` next to `docker-compose.yml` (auto-loaded by compose):

```bash
AWS_BEARER_TOKEN_BEDROCK=...      # REPL agent (Claude Opus via Bedrock)
AWS_DEFAULT_REGION=us-west-2
OPENAI_API_KEY=sk-...             # default g1_speak voice provider
NEON_MODEL_ID=global.anthropic.claude-opus-4-8   # override the default model
G1_IFACE=eth0
CYCLONEDDS_URI=/home/unitree/cyclonedds_ws/cyclonedds.xml
TELEGRAM_BOT_TOKEN=123:ABC        # optional listeners
```

See `.env.example` for the full annotated list. Image built from `Dockerfile`.

## next

[systemd](systemd.md){ .md-button } [network](../reference/network.md){ .md-button }
