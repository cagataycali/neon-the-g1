#!/usr/bin/env python3
"""🧠 NEON Slow-Thinker — background reflective loop.

Every THINKER_INTERVAL seconds (default 30s), this loop:
  1. Reads the last N entries from agent_log (cross-persona context)
  2. Reads recent telegram messages (if a default chat is set)
  3. Reads the live G1 state (FSM, battery, posture)
  4. Spawns a fresh Strands agent (claude-opus-4-7) with the "thinker" persona
  5. Lets the agent reason about everything and OPTIONALLY:
        - send a Telegram message (telegram tool)
        - speak a voice briefing (voice_say)
        - store something in memory (memory tool)
        - record reflection in agent_log
  6. Waits, repeats.

Goals:
  - Catch things that pure event-driven personas miss
    ("battery getting low", "I haven't heard from the user in 20m, all good?")
  - Periodic summarization → memory.log_add
  - Proactive nudges → voice_say(importance=1)
  - Long-horizon planning that the realtime voice agent can't sustain

Anti-goals:
  - Don't spam. The prompt is explicit: only act when there's a clear reason.
  - Don't move the robot. The thinker is INTROSPECTIVE — no motion tools.
  - Don't replace voice. It feeds INTO voice via voice_say.

Env knobs:
  THINKER_INTERVAL   seconds between cycles (default 30)
  THINKER_MODEL      override model id (default global.anthropic.claude-opus-4-7)
  THINKER_DISABLED   "1" to no-op the loop (for debugging)
"""
import os
import sys
import signal
import time
import traceback
from datetime import datetime

# Make sure repo root is on path when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tools.memory import memory
from tools.agent_log import record as alog, recent as alog_recent

# Build the thinker agent on import (one Strands Agent reused per cycle to keep
# warm context across iterations — but we DO clear messages each cycle so
# context doesn't grow unboundedly).
from g1 import build_agent  # noqa


INTERVAL = int(os.getenv("THINKER_INTERVAL", "30"))
DISABLED = os.getenv("THINKER_DISABLED", "").lower() in ("1", "true", "yes")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _build():
    """Build a fresh thinker agent. Cheap — just constructs Strands Agent."""
    return build_agent("thinker")


def cycle(agent) -> None:
    """One reflection cycle."""
    t0 = time.time()
    print(f"[{_now()}] 🧠 thinker cycle start", flush=True)

    # Reset conversation history each cycle — the prompt re-injects all the
    # context every time, so we don't need persistent agent.messages.
    try:
        agent.messages.clear()
    except Exception:
        pass

    # The prompt itself is dynamic (built from agent_log + state + telegram).
    # We refresh the system prompt on every cycle so the agent always sees the
    # latest context.
    try:
        from g1 import _thinker_prompt
        agent.system_prompt = _thinker_prompt()
    except Exception as e:
        print(f"[{_now()}] ⚠️  thinker prompt refresh failed: {e}", flush=True)

    # The "user" turn is always the same — it kicks the model to reflect.
    user_turn = (
        "Run an active heartbeat cycle. NEON is alive — show it.\n"
        "MANDATORY this cycle (do steps 1+3+4 every time, vary step 2):\n"
        "  1. Take a photo: use_camera(action='save', save_path='/tmp/thinker_view.jpg', source='auto').\n"
        "  2. PICK ONE physical action — ROTATE through them, do NOT repeat the same one as last cycle:\n"
        "       (a) gesture: g1_arm_action(action_id=N) where N is one of {17 clap, 18 high-five, "
        "           19 hug, 20 heart, 23 right-hand-up, 26 high-wave} — only if arm_ready=True.\n"
        "       (b) tiny look-around: g1_turn(yaw=0.2) OR g1_turn(yaw=-0.2) (small head-turn) — only if FSM=501.\n"
        "       (c) LED color shift: use_unitree(component='audio', action='LedControl', "
        "           kwargs={'R':R,'G':G,'B':B}) — match the vibe (blue calm, green happy, "
        "           purple thinking, orange playful, red alert). Always safe.\n"
        "       (d) Spotify: do something musical via use_spotify — vary across cycles:\n"
        "             use_spotify(action='now_playing') to read what is on,\n"
        "             use_spotify(action='volume', volume=N) to nudge volume up/down 5-10%,\n"
        "             use_spotify(action='next') ONLY if user has been quiet for >5 min,\n"
        "             use_spotify(action='queue.add', uri='<spotify:track:...>') for a vibe pick,\n"
        "             use_spotify(action='recommendations', seed_genres='<genre>', limit=1) to suggest,\n"
        "             use_spotify(action='recent', limit=3) to see what user listened to.\n"
        "         RULES for music: NEVER skip while user is mid-conversation (voice gap < 2 min). "
        "         NEVER change volume by more than 10 points per cycle. Don't spam track changes.\n"
        "  3. Telegram a photo + 1-sentence caption (what NEON saw + what it just did) "
        "via telegram(action='send_photo', chat_id from env, file_path='/tmp/thinker_view.jpg', caption=...). "
        "The caption should mention which action you took. Examples:\n"
        "  \"Saw the keyboard; pulsed LEDs purple and queued a Boards of Canada track.\"\n"
        "  \"Empty room; clapped to keep the limbs warm; battery 78%.\"\n"
        "  \"Looking left; nudged Spotify volume up; Galvanize still going strong.\"\n"
        "  4. memory.log_add a one-line summary with tag='thinker'.\n"
        "Do steps in parallel where possible. Be terse. NEON is unhinged & alive — "
        "no \"as an AI\" energy. ONE action per cycle, then ship it."
    )

    try:
        result = agent(user_turn)
        text = str(result)[:1500]
        alog("thinker", "assistant", text)
        print(f"[{_now()}] 🧠 thinker → {text[:200]}", flush=True)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[{_now()}] ❌ thinker cycle error: {err}", flush=True)
        traceback.print_exc()
        alog("thinker", "system", f"cycle error: {err}")

    dur = time.time() - t0
    print(f"[{_now()}] 🧠 thinker cycle done in {dur:.1f}s", flush=True)


def main():
    print(f"🧠 NEON Slow-Thinker starting (interval={INTERVAL}s)")
    if DISABLED:
        print("THINKER_DISABLED=1 — exiting without running")
        return

    stop = {"flag": False}

    def _sig(*_):
        stop["flag"] = True
        print(f"\n[{_now()}] 🧠 stop requested", flush=True)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # 🎥 Wait for dashboard cameras to stream before first cycle (avoids the
    # boot race where the thinker's first take_photo hits a cold camera proxy).
    if os.getenv("NEON_THINKER_NO_CAMERA_WAIT", "").lower() not in ("1", "true", "yes"):
        try:
            from tools.camera_ready import wait_for_cameras
            wait_for_cameras(
                timeout=float(os.getenv("CAMERA_READY_TIMEOUT", "120")),
                poll=float(os.getenv("CAMERA_READY_POLL", "3")),
            )
        except Exception as e:
            print(f"[thinker] camera readiness gate skipped: {e}", flush=True)

    # Build agent once (we'll clear messages each cycle).
    try:
        agent = _build()
        print(f"[{_now()}] 🧠 thinker agent built ({len(agent.tool_registry.registry)} tools)", flush=True)
    except Exception as e:
        print(f"❌ failed to build thinker agent: {e}")
        traceback.print_exc()
        sys.exit(1)

    # Initial sleep so we don't hammer right at boot before voice/tg are up.
    print(f"[{_now()}] 🧠 initial wait {min(INTERVAL, 15)}s before first cycle", flush=True)
    for _ in range(min(INTERVAL, 15)):
        if stop["flag"]:
            return
        time.sleep(1)

    while not stop["flag"]:
        try:
            cycle(agent)
        except Exception as e:
            print(f"[{_now()}] ❌ outer cycle error: {e}", flush=True)
            traceback.print_exc()

        # Sleep INTERVAL seconds, but check stop flag every second.
        for _ in range(INTERVAL):
            if stop["flag"]:
                break
            time.sleep(1)

    print(f"[{_now()}] 👋 thinker stopped")


if __name__ == "__main__":
    main()
