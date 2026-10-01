# troubleshooting

<span class="read-badge">scan by symptom</span>

| symptom | rc | fix |
|---|:-:|---|
| nothing responds | 3104 | DDS can't reach the bus — see below |
| arm won't move | 7404 | wrong FSM → `g1_set_fsm(500)` |
| arm holding | 7401 | `g1_release_arm()` |
| arm occupied | 7400 | another writer — kill stale process |
| walking refused | 7302 | `g1_set_fsm(501)` first |
| SLAM/lidar silent | — | `g1_lidar_switch(on=True)` then `g1_slam_start()` |

## nothing responds (rc=3104) { .danger }

DDS can't reach the motor bus.

```bash
ip link show eth0                 # must say "state UP"
ping -c 3 192.168.123.161         # can we reach the MCU?
echo "$CYCLONEDDS_URI"            # should be a file:// URL
source env.sh
python3 -c "from tools import g1_get_state; print(g1_get_state())"
```

Ping fails → Jetson on wrong network. Re-plug ethernet / check `scripts/change-ip.txt`.

## arm issues { .motion }

```python
g1_arm_action(action="high wave", auto_transition=True)   # 7404: auto-fix FSM
g1_release_arm()                                          # 7401: release
```

`7400` (occupied) = competing writer:

```bash
pgrep -fa "agent.py"
# docker: docker compose restart neon-agent
# systemd: sudo systemctl restart neon
pkill -f agent.py
```

## telegram not responding { .safe }

1. `docker compose logs neon-telegram` (or `make tg` output)
2. `docker compose logs neon-telegram | grep -i telegram`
3. Your ID/username in `TELEGRAM_ALLOWED_USERS`?
4. Token valid? (regen via @BotFather)

## voice picks up echo / fan noise { .safe }

```python
g1_speak(action="start",
         audio_processing=True,   # WebRTC AEC+NS+AGC (required for speaker)
         stream_delay_ms=120,     # speaker→mic delay hint
         vad_threshold=0.7,       # higher = less twitchy
         silence_duration_ms=700)
```

Driving the mic directly? `g1_asr(duration_s=3.0)` sidesteps host noise.

## slow answers { .safe }

- Point `NEON_MODEL_ID` at a lighter Bedrock model (e.g. a Sonnet id)
- Too many heavy tool calls → check logs for stray vision calls
- Context overflow → `manage_messages(action='compact')`

## "pip wheel broken" { .safe }

The `unitree_sdk2_python` wheel is missing subpackages. Use the source clone:

```bash
make sdk    # clones + adds to PYTHONPATH (make run-bare does this automatically)
```

## still stuck?

```bash
tail -f /tmp/devduck/logs/devduck.log
```

Or ask the agent: `"read recent logs and tell me what's wrong"`. Else
[open an issue](https://github.com/cagataycali/neon-the-g1/issues) with rc + logs.
