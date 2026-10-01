#!/usr/bin/env python3
"""Always-on bidirectional voice agent for the G1 robot.

Picks provider based on env (VOICE_PROVIDER, default: openai).
Auto-restarts on transient errors. Honours mute via memory kv `voice.muted`.

Provider matrix:
  openai         OpenAI Realtime API             OPENAI_API_KEY (recommended for G1)
  nova_sonic     AWS Bedrock (us-east-1)         AWS_BEARER_TOKEN_BEDROCK or AWS creds
  gemini         Gemini Live                     GOOGLE_API_KEY or GEMINI_API_KEY

Audio path:
  Brio mic → AEC → 16→24 ratecv → bidi model
  bidi model 24k → 24→16 ratecv → G1 chest speaker (DDS PlayStream)
"""
import asyncio
import os
import signal
import sys
import time

from g1 import build_voice_agent

PROVIDER = os.getenv("VOICE_PROVIDER", "openai").lower()
VOICE = os.getenv("VOICE_NAME", "")
NO_AEC = os.getenv("VOICE_NO_AEC", "").lower() in ("1", "true", "yes")
RESTART_DELAY = int(os.getenv("VOICE_RESTART_DELAY", "5"))
NETWORK_IF = os.getenv("G1_NETWORK_INTERFACE", "eth0")


async def run_once():
    agent, audio_io = build_voice_agent(
        provider=PROVIDER,
        voice=VOICE or None,
        audio_processing=not NO_AEC,
        network_interface=NETWORK_IF,
    )
    if not audio_io.start_speaker():
        print(f"⚠ G1 chest speaker init failed — check DDS / network_interface={NETWORK_IF}", file=sys.stderr)
        # Continue anyway — audio frames will be dropped silently
    model_id = getattr(agent.model, "model_id", "default")
    print(f"🎙 NEON voice up "
          f"(provider={PROVIDER}, model={model_id}, "
          f"voice={VOICE or 'default'}, aec={'off' if NO_AEC else 'on'}, "
          f"if={NETWORK_IF})", file=sys.stderr)
    print(f"   live mute: memory kv 'voice.muted' (true/false). "
          f"/mute and /unmute over Telegram.", file=sys.stderr)
    try:
        await agent.run(
            inputs=[audio_io.input(), audio_io.briefing_input()],
            outputs=[audio_io.output(), audio_io.log_output()],
        )
    finally:
        audio_io.shutdown()


def main():
    if not PROVIDER:
        print("VOICE_PROVIDER not set", file=sys.stderr)
        sys.exit(1)

    stop = {"flag": False}
    def _sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # Boot-time camera readiness gate: wait for the dashboard to own the
    # cameras AND start streaming before we bring voice up (else take_photo
    # 401s until manual restart). Also self-heals a stale proxy token.
    # Skip with NEON_VOICE_NO_CAMERA_WAIT=1.
    if os.getenv("NEON_VOICE_NO_CAMERA_WAIT", "").lower() not in ("1", "true", "yes"):
        try:
            from tools.camera_ready import wait_for_cameras
            wait_for_cameras(
                timeout=float(os.getenv("CAMERA_READY_TIMEOUT", "120")),
                poll=float(os.getenv("CAMERA_READY_POLL", "3")),
            )
        except Exception as e:
            print(f"[voice] camera readiness gate skipped: {e}", file=sys.stderr)

    while not stop["flag"]:
        try:
            asyncio.run(run_once())
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[voice err] {e}", file=sys.stderr)
            if stop["flag"]:
                break
            time.sleep(RESTART_DELAY)
    print("👋 g1 voice listener stopped", file=sys.stderr)


if __name__ == "__main__":
    main()
