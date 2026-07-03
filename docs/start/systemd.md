# systemd

<span class="read-badge">⏱ 30s</span>

Run `neon` as a system service — auto-start on boot, restart on failure, alive
without an SSH session.

## fastest path — one command

Fresh SSH into the robot? This gets you from zero to a reboot-persistent
agent in one command:

```bash
cd ~/neon-the-g1
make setup
```

`make setup`:

1. copies `.env.example` → `.env` (first run) and asks you to fill in your
   keys — `AWS_BEARER_TOKEN_BEDROCK`, `OPENAI_API_KEY`, optional
   `TELEGRAM_BOT_TOKEN`
2. on the second `make setup` (once `.env` is filled) it **builds + starts**
   the docker stack detached, then **installs the systemd unit** so it
   auto-starts on every boot

```
$ make setup
📝 created .env from .env.example
   → EDIT .env now and fill in your secrets …
   Then re-run:  make setup

# … edit .env …

$ make setup
✅ .env present — building + starting neon (docker) …
🔒 persisting to systemd so it survives reboots …
✅ neon-docker.service installed + enabled
🎉 neon is live and will auto-start on boot.
```

Then attach to the REPL any time with `make run`.


## install

```bash
make install-service          # bare venv  → neon.service
make install-service-docker   # docker     → supervises `docker compose up`
```

`install-service` copies `neon.service` to `/etc/systemd/system/`, runs as
user `unitree` from this repo, sources `CYCLONEDDS_URI` + `PYTHONPATH`, execs
`.venv/bin/python agent.py`, and sets `Restart=on-failure · RestartSec=5`.
Logs append to `/var/log/g1-agent.log`.

## manage

```bash
sudo systemctl status  neon
sudo systemctl restart neon
make service-logs                     # sudo journalctl -u neon -f
sudo systemctl disable neon
```

## uninstall

```bash
make uninstall-service                # disables + removes neon + neon-docker units
```

## next

[FSM reference](../reference/fsm.md){ .md-button } [network](../reference/network.md){ .md-button }
