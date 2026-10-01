# troubleshooting

<span class="read-badge">scan by symptom</span>

| symptom | rc | fix |
|---|:-:|---|
| nothing responds | 3104 | DDS can't reach the bus, see below |
| arm won't move | 7404 | wrong FSM → `g1_set_fsm(500)` |
| arm holding | 7401 | `g1_release_arm()` |
| arm occupied | 7400 | another writer, kill stale process |
| walking refused | 7302 | `g1_set_fsm(501)` first |
| SLAM/lidar silent | - | `g1_lidar_switch(on=True)` then `g1_slam_start()` |
| "the robot did not move" | 0 | `moved=false`: not in FSM 501, or a request under 0.1 m; say `set fsm 501` |
| no photo, `401` in the log | - | camera token minted at the 1970 boot clock; `make token-refresh` |
| voice silent, tools work | - | snoozed; `make voice-status`, unmute from the dashboard pill or `make unmute` |
| model changed, persona still old | - | "Apply to all personas" in Configuration, or `docker compose up -d --force-recreate` |

## nothing responds (rc=3104) { .danger }

`ip link show eth0` must say UP, `ping 192.168.123.161` must answer, and
`CYCLONEDDS_URI` must be set (`source env.sh`). Ping fails: the Jetson is on
the wrong network, see [network](../reference/network.md).

## arm issues { .motion }

`7404` wrong FSM: `g1_arm_action(..., auto_transition=True)` fixes it;
`7401` holding: `g1_release_arm()`; `7400` occupied: a second writer on
`rt/armsdk`, find it with `make ps` and restart that persona.

## telegram not responding { .safe }

`docker compose logs neon-telegram`, your id in `TELEGRAM_ALLOWED_USERS`, the
token still valid at @BotFather.

## echo or fan noise in voice { .safe }

Tune in this order: `stream_delay_ms` (120 for the chest speaker), make sure the
USB mic was picked (`g1_speak(action="debug")`), then `vad_threshold` 0.7 to
0.85 and `silence_duration_ms` 700 to 1000. Details on
[voice architecture](../voice-architecture.md#echo-troubleshooting).

## cameras { .safe }

The dashboard is the only process that opens the cameras; everyone else pulls
`GET /api/camera/<id>/snapshot` with a service token. `401`/`403` in a
consumer log means the token is stale: the Jetson boots at 1970 until NTP and a
token minted then is dated 1980. `/api/health` reports
`camera_proxy_token: ok|expired|missing|invalid`; the Configuration env tab has a
Refresh button, `make token-refresh` does the same from a shell, and
`use_camera` re-mints once on its own. Still black: `docker compose logs
neon-dashboard` for the device open, and `ls /dev/video*` after a replug.

## voice is quiet { .safe }

`make voice-status`. Muted gates the mic and the speaker; a snooze clears itself
at its deadline, unmute earlier from the dashboard pill, `make unmute`, or
Telegram `/unmute`. Not muted and still quiet: `journalctl -u neon-voice -n 50`
(provider key, mic not found, `VOICE_MIC_NAME`).

## slow answers { .safe }

- Pick a lighter Bedrock model in Configuration (applies to the dashboard at
  once, "Apply to all personas" recreates the rest)
- Too many heavy tool calls: check the activity log for stray vision calls
- Context overflow: `manage_messages(action='compact')`

## "pip wheel broken" { .safe }

The `unitree_sdk2_python` wheel is missing subpackages; `make sdk` clones the
source (`make run-bare` does it for you).

## still stuck?

```bash
make log-show                           # last 30 turns, every persona, tool rows with rc
journalctl -u neon-voice -f             # the voice process
docker compose logs -f neon-dashboard   # cameras, auth, the dashboard agent
```

Or ask the agent: `"read recent logs and tell me what's wrong"`. Else
[open an issue](https://github.com/cagataycali/neon-the-g1/issues) with rc + logs.
