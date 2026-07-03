#!/usr/bin/env python3
"""Telegram listener for NEON (G1) — multi-turn agent + voice control.

Each incoming message:
  1. is recorded in tg_history (done by listener itself before callback)
  2. is pushed into the voice_bridge so NEON's voice persona hears about it
  3. spawns a fresh Strands Agent via g1.build_agent("telegram", ...)
  4. agent uses telegram(action='send_message', ...) to reply

Slash commands handled directly (no LLM round-trip):
  /start    - register chat_id
  /clear    - wipe this chat's history
  /history  - dump last N messages
  /mute     - mute the voice agent (memory kv voice.muted=true)
  /unmute   - unmute
  /voice    - report voice agent mute state
  /state    - dump live G1 state
  /battery  - quick battery check
"""
import os
import sys
from datetime import datetime

from g1 import build_agent
from tools.memory import memory as mem_tool
from tools.voice_bridge import push as voice_push
from tools.agent_log import record as alog
from tools.telegram import (
    listen as telegram_listen,
    format_history_for_prompt,
    record_message,
    download_file,
    _api,
    _conn,
)


def _send(chat_id: str, text: str, parse_mode: str = "Markdown"):
    r = _api("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode)
    if r.get("ok"):
        record_message(chat_id, "assistant", text, msg_id=r["result"]["message_id"])
    return r


def handle_message(msg: dict):
    chat_id = str(msg["chat"]["id"])
    text = msg.get("text", "").strip()
    user = msg.get("from", {})
    username = user.get("username", "") or user.get("first_name", "?")

    if not text:
        return

    print(f"[{datetime.now():%H:%M:%S}] @{username} ({chat_id}): {text[:80] if text else '(media)'}")

    # ── media (photo/voice/document) — relay as briefing to voice agent ──
    photos = msg.get("photo") or []
    if photos:
        # photo is list of size variants — pick the largest
        biggest = max(photos, key=lambda p: p.get("file_size", 0))
        path = download_file(biggest["file_id"])
        caption = msg.get("caption", "")
        if path:
            briefing = f"@{username} sent a photo at {path}"
            if caption:
                briefing += f" with caption: {caption}"
            voice_push("telegram", briefing, importance=1)
            alog("telegram", "user", briefing, meta={"chat_id": chat_id, "file": str(path)})
            _send(chat_id, f"📸 photo received → {path.name} (relayed to voice)")
        else:
            _send(chat_id, "⚠️ photo download failed")
        return

    voice_payload = msg.get("voice")
    if voice_payload:
        path = download_file(voice_payload["file_id"])
        if path:
            briefing = f"@{username} sent a voice note at {path}"
            voice_push("telegram", briefing, importance=1)
            alog("telegram", "user", briefing, meta={"chat_id": chat_id, "file": str(path)})
            _send(chat_id, f"🎤 voice note received → {path.name}")
        else:
            _send(chat_id, "⚠️ voice download failed")
        return

    if not text:
        return

    # ── slash commands ──
    if text == "/start":
        _send(chat_id,
              f"🤖 *NEON* (G1 robot) is connected.\n\n"
              f"Your chat_id: `{chat_id}`\n\n"
              f"Talk to me — I have full robot control + memory + voice.\n"
              f"Voice: /mute /unmute /voice\n"
              f"Robot: /state /battery")
        return

    if text == "/clear":
        c = _conn()
        n = c.execute("DELETE FROM tg_history WHERE chat_id=?", (chat_id,)).rowcount
        c.commit(); c.close()
        _api("sendMessage", chat_id=chat_id, text=f"🧹 cleared {n} messages from memory")
        return

    if text == "/history":
        block = format_history_for_prompt(chat_id, limit=30) or "(empty)"
        _api("sendMessage", chat_id=chat_id, text=f"```\n{block[:3500]}\n```", parse_mode="Markdown")
        return

    if text == "/mute":
        mem_tool(action="kv_set", key="voice.muted", value="true")
        _send(chat_id, "🔇 voice muted. mic input silenced; speaker stays open.")
        return

    if text == "/unmute":
        mem_tool(action="kv_set", key="voice.muted", value="false")
        _send(chat_id, "🎙 voice unmuted.")
        return

    if text == "/voice":
        v = mem_tool(action="kv_get", key="voice.muted")
        muted = isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on")
        provider = os.getenv("VOICE_PROVIDER", "openai")
        _send(chat_id, f"🎙 voice agent — provider: `{provider}` — {'🔇 MUTED' if muted else '🟢 LIVE'}")
        return

    if text == "/state":
        try:
            from tools.g1_state import g1_get_state
            r = g1_get_state(network_interface=os.getenv("G1_NETWORK_INTERFACE", "eth0"))
            _send(chat_id, f"```json\n{r}\n```"[:3500], parse_mode="Markdown")
        except Exception as e:
            _send(chat_id, f"⚠️ state error: {e}")
        return

    if text == "/battery":
        try:
            from tools.g1_battery import g1_battery
            r = g1_battery(network_interface=os.getenv("G1_NETWORK_INTERFACE", "eth0"))
            _send(chat_id, f"```json\n{r}\n```"[:3500], parse_mode="Markdown")
        except Exception as e:
            _send(chat_id, f"⚠️ battery error: {e}")
        return

    # Relay to voice agent: user is talking via DM, voice should hear about it
    voice_push("telegram", f"@{username}: {text}", importance=1)
    alog("telegram", "user", f"@{username}: {text}", meta={"chat_id": chat_id})

    # ── normal LLM turn ──
    agent = build_agent("telegram", chat_id=chat_id, username=username)
    try:
        result = agent(f"@{username} just sent: {text}")
        alog("telegram", "assistant", str(result)[:2000], meta={"chat_id": chat_id})
    except Exception as e:
        err_text = f"⚠️ error: {e}"
        _api("sendMessage", chat_id=chat_id, text=err_text)
        record_message(chat_id, "assistant", err_text)
        print(f"[error] {e}")


def main():
    print("🤖 NEON — Telegram listener starting...")
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        print("✗ TELEGRAM_BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)
    print(f"   ALLOWED_USERS = {os.getenv('TELEGRAM_ALLOWED_USERS','(any)')}")
    print(f"   HISTORY_LIMIT = {os.getenv('TELEGRAM_HISTORY_LIMIT','20')}")
    try:
        telegram_listen(handle_message)
    except KeyboardInterrupt:
        print("\n👋 listener stopped")


if __name__ == "__main__":
    main()
