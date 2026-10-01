# telegram control

<span class="read-badge">setup once</span>

Text `wave` from the couch. Robot waves. No SSH, no laptop.

**Prereqs:** G1 on WiFi with outbound internet, a bot token from
[@BotFather](https://t.me/BotFather), your ID from [@userinfobot](https://t.me/userinfobot).

## setup

```bash
# .env on the robot
TELEGRAM_BOT_TOKEN=1234567890:ABC-DEF...
TELEGRAM_ALLOWED_USERS=149632499      # comma-separated usernames or IDs; omit = allow any
TELEGRAM_DEFAULT_CHAT_ID=149632499    # where the voice persona relays briefings
TELEGRAM_HISTORY_LIMIT=20             # messages injected per chat into the prompt
```

Then start the listener (spawns a fresh telegram-persona agent per message):

```bash
make tg                               # foreground
make tg-bg                            # nohup background
# or via docker compose — the neon-telegram service runs it automatically:
docker compose restart neon-telegram
docker compose logs -f neon-telegram | grep -i telegram   # "listener starting"
```

## use it

```
you:  check state       → FSM 500, arm_ready, 86%. All green.
you:  wave hello        → waved
you:  what do you see    → (photo) A person at a desk with a laptop.
```

## allowlist

Only IDs/usernames in `TELEGRAM_ALLOWED_USERS` are processed; others dropped
silently. Comma-separate for multiple. Leaving it unset allows any sender.

## walking over chat

Same policy as by voice: your message is the consent, the persona looks first,
walks only in FSM 501 and replies with the measured distance or the reason it
did not move. Chat is an awkward place for a fall: stay in the room.

## slash commands

`/state`, `/battery` answer without the model; `/mute`, `/unmute`, `/voice`
flip or show the voice mute flag (same kv as the dashboard and `voice_control`);
`/history`, `/clear` manage the per-chat memory.

## privacy

Messages transit Telegram's servers (not E2E). Camera frames in replies leave
your network. Sensitive? Stick to the REPL over SSH.
