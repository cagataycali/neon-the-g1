# cli tips

<span class="read-badge">60s</span>

The targets you type every day, grouped by job. `make help` prints all of them.

## run and watch

```bash
make run                  # compose up + attach to the REPL
make dashboard            # only the cockpit container, with its log
make logs / make ps       # container logs / every neon process (docker, systemd, bare-metal)
make exec                 # shell inside neon-agent
make ask Q="status?"      # one-shot query, no REPL
make down                 # stop everything, containers and listeners
```

## voice

```bash
make voice / voice-bg     # listener in the foreground / background (the robot runs it as neon-voice)
make mute / unmute        # the same flag as the dashboard pill and the voice_control tool
make voice-status         # muted or live, with the snooze deadline
make voice-push MSG="hi"  # inject a briefing the voice persona speaks
make test-voice           # provider config check, no audio
```

## telegram, thinker, memory

```bash
make tg / tg-bg           # listener (the compose stack runs it as neon-telegram)
make test-tg              # token + connectivity
make thinker-once         # one reflective cycle, then exit
make log-show             # last 30 cross-persona turns (agent_log)
make prompts-get P=voice  # the effective persona prompt; prompts-reset P=voice reverts an override
```

## dashboard auth and cameras

```bash
make auth-status          # enrolled passkeys, setup state
make auth-clear           # wipe passkeys, reopen setup; re-mints the service token
make token-refresh        # NEON_CAMERA_PROXY_TOKEN back into .env, recreate the consumers
```

## boot, network, teleop

```bash
make setup / persist      # first-run onboarding / install the boot unit
make wifi [SSID= PASS=]   # pick a network; wifi-status, wifi-scan
make xr-cert / xr-teleop  # WebXR teleop (see the guide)
```

## env

One table, on [docker](../start/docker.md#env); `.env.example` is annotated.

## REPL shortcuts

```
> ! ip link show eth0          # ! prefix = shell command
> damp                         # FSM 1, always safe
> stop                         # vx=vy=vyaw=0
> release arm                  # id 99
```

`exit`/`q` to quit. Ctrl-C once interrupts the turn, twice exits.

## one-off debug (no agent)

```bash
python3 -c "from tools import g1_get_state; print(g1_get_state()['fsm_id'])"
python3 -c "from tools import g1_dds_list_topics; print(g1_dds_list_topics())"
journalctl -u neon-voice -f | grep -E 'tool=|ERROR'
```

---

[troubleshooting](troubleshooting.md){ .md-button } [extending](extending.md){ .md-button }
