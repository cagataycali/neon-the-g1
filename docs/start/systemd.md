# systemd

<span class="read-badge">45s</span>

What runs at boot on the Jetson, and the one-command shortcut.

## the units

| unit | kind | runs |
|---|---|---|
| `neon-compose.service` | user, `scripts/systemd/` | `docker compose up -d`: agent, dashboard, telegram, thinker |
| `neon-voice.service` | system, `scripts/systemd/` | `g1_speech_listener.py` in the host venv: USB mic and ALSA need the host |
| `neon-ctl.service` | user, `scripts/systemd/` | `scripts/neon_ctl.py`: the dashboard's host channel (recreate containers, restart voice, refresh the camera token); its sudoers file allows exactly `systemctl restart/status/is-active neon-voice` |
| `neon-wifi-watchdog.timer` | system, `scripts/systemd/` | every minute, falls back to the `neon_net` hotspot profile when `wlan0` has no known network |
| `neon-docker.service` | system, repo root | what `make setup` installs: `docker compose up --build -d`, the compose stack alone |

`neon-compose` and `neon-voice` are ordered `After=time-sync.target`: the Jetson
has no RTC battery and boots at 1970, and a dashboard service token minted at
that clock is refused everywhere (see [docker](docker.md#cameras)).

```bash
mkdir -p ~/.config/systemd/user
cp scripts/systemd/neon-compose.service scripts/systemd/neon-ctl.service ~/.config/systemd/user/
loginctl enable-linger "$USER"
systemctl --user daemon-reload && systemctl --user enable --now neon-compose neon-ctl
sudo cp scripts/systemd/neon-voice.service /etc/systemd/system/ && sudo systemctl enable --now neon-voice
sudo install -m 0440 scripts/systemd/neon-ctl.sudoers /etc/sudoers.d/neon-ctl && sudo visudo -c
```

## the shortcut

```bash
cd ~/neon-the-g1 && make setup
```

First run creates `.env` and stops for your keys; the second builds, starts the
stack and installs `neon-docker.service`. Voice and `neon-ctl` are not part of
it; install them from the table above.

## manage

```bash
systemctl --user status neon-compose neon-ctl
sudo systemctl status neon-voice          # journalctl -u neon-voice -f for the voice log
make ps                                   # every neon process: docker, systemd, bare-metal
make voice-status                         # muted or live
make uninstall-service                    # removes neon + neon-docker (the make setup units)
```

## next

[docker](docker.md){ .md-button } [troubleshooting](../guide/troubleshooting.md){ .md-button }
