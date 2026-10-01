# cli tips

<span class="read-badge">⏱ 60s</span>

The stuff you type every day.

## make targets

```bash
make run                  # docker compose up + attach to agent.py REPL
make run-bare             # host venv REPL (DEV; builds venv + clones SDK)
make up / down            # docker up (detached) / stop everything
make logs                 # tail container logs
make voice / tg / thinker # run a listener in foreground
make voice-bg / tg-bg     # …in background
make test-tg / test-voice # telegram connectivity / voice config check
make sdk / venv           # clone SDK / build venv
make install-service      # systemd unit (bare-metal)
make ask Q="status?"      # one-shot query
make help                 # all targets
```

## env vars

```bash
# model (Bedrock by default)
AWS_BEARER_TOKEN_BEDROCK=...
AWS_DEFAULT_REGION=us-west-2
NEON_MODEL_ID=global.anthropic.claude-opus-4-8   # override default

# network / dds (never change on robot)
G1_IFACE=eth0
G1_NETWORK_INTERFACE=eth0
CYCLONEDDS_URI=/home/unitree/cyclonedds_ws/cyclonedds.xml

# listeners
TELEGRAM_BOT_TOKEN=123:abc
TELEGRAM_ALLOWED_USERS=149632499
TELEGRAM_DEFAULT_CHAT_ID=149632499

# voice
NEON_NO_SPEECH=1                 # skip g1_speak auto-start at REPL boot
VOICE_PROVIDER=openai            # openai | nova_sonic | gemini
VOICE_NAME=alloy
```

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
tail -f /tmp/devduck/logs/devduck.log | grep -E '(rc=|ERROR|g1_)'
```


---

[troubleshooting](troubleshooting.md){ .md-button } [extending](extending.md){ .md-button }
