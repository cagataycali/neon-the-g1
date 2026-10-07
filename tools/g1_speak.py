"""🎙️ g1_speak — thin tool wrapper around the bidi voice infrastructure.

The HEAVY lifting (BidiAgent + AEC + DDS speaker + briefing input + log
output + tool injection) is in:
  - tools/g1_bidi_audio.py   ← G1BidiAudioIO
  - g1.py                    ← build_voice_agent() factory
  - g1_speech_listener.py    ← top-level always-on entrypoint

This tool exists so the REPL agent (agent.py) can start/stop the same
bidi session in-process via:
    g1_speak(action="start")   # spawn bg thread, run agent.run() forever
    g1_speak(action="stop")
    g1_speak(action="status")
    g1_speak(action="say", text="hello")  # one-shot TtsMaker (no bidi)

Architectural note (vs the original g1_speak.py):
- The OLD g1_speak built its own ad-hoc BidiAgent with `tools=[]` — meaning
  the voice persona couldn't actually DO anything (no robot control, no
  memory, no telegram, nothing).
- The NEW g1_speak delegates to `g1.build_voice_agent()` which wires in
  G1_ALL_TOOLS + telegram + memory + voice_say + take_photo.
  The voice persona is a FULL participant.
- It also adds the briefing channel (telegram → bidi mid-conversation)
  and the log channel (transcripts → unified agent_log).
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from strands import tool


# Lazy bidi import probe
_BIDI_OK = None
_BIDI_ERR = None


def _probe_bidi():
    global _BIDI_OK, _BIDI_ERR
    if _BIDI_OK is True:
        return True
    if _BIDI_OK is False:
        return False
    try:
        import pywebrtc_audio  # noqa
        import pyaudio          # noqa
        from strands.bidi import BidiAgent  # noqa
        _BIDI_OK = True
        return True
    except Exception as e:
        _BIDI_ERR = str(e)
        _BIDI_OK = False
        return False


# ─── Module-level state ───────────────────────────────────────────────
_STATE: dict = {
    "running": False,
    "thread": None,
    "stop_event": None,
    "session_id": None,
    "agent": None,
    "audio_io": None,
    "started_at": 0,
    "model_id": "",
    "provider": "",
    "voice": "",
    "last_error": "",
}


def _runner_main(
    *,
    provider: str,
    voice: Optional[str],
    audio_processing: bool,
    network_interface: str,
    stream_delay_ms: int,
    vad_threshold: float,
    silence_duration_ms: int,
    stop_event: threading.Event,
):
    """Background thread: build bidi voice agent, run forever (until stop_event)."""
    try:
        # Late import — only when actually starting
        from g1 import build_voice_agent

        agent, audio_io = build_voice_agent(
            provider=provider,
            voice=voice,
            audio_processing=audio_processing,
            network_interface=network_interface,
            stream_delay_ms=stream_delay_ms,
            vad_threshold=vad_threshold,
            silence_duration_ms=silence_duration_ms,
        )
        if not audio_io.start_speaker():
            _STATE["last_error"] = "g1 chest speaker init failed (DDS)"
            return

        _STATE["agent"] = agent
        _STATE["audio_io"] = audio_io
        _STATE["model_id"] = (agent.model.get_config() or {}).get("model_id", "default")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async_stop = asyncio.Event()

        async def _bridge_stop():
            while not stop_event.is_set():
                await asyncio.sleep(0.2)
            async_stop.set()

        async def _run():
            run_task = asyncio.create_task(
                agent.run(
                    inputs=[audio_io.input(), audio_io.briefing_input()],
                    outputs=[audio_io.output(), audio_io.log_output()],
                )
            )
            bridge = asyncio.create_task(_bridge_stop())
            done, pending = await asyncio.wait(
                [bridge, run_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            try:
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception:
                pass

        try:
            loop.run_until_complete(_run())
        finally:
            try:
                audio_io.shutdown()
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
    except Exception as e:
        _STATE["last_error"] = f"{type(e).__name__}: {e}"
    finally:
        _STATE["running"] = False


@tool
def g1_speak(
    action: str = "status",
    text: str = "",
    provider: str = "",
    voice: str = "",
    audio_processing: bool = True,
    stream_delay_ms: int = 120,
    vad_threshold: float = 0.7,
    silence_duration_ms: int = 700,
    network_interface: str = "eth0",
) -> dict:
    """🎙️ G1 bidirectional voice agent (start/stop/status/say).

    The bidi voice agent uses: Brio mic → AEC → bidi model → G1 chest speaker (DDS).

    It has the FULL G1 toolset wired in (FSM-gated robot control, telegram,
    memory, take_photo with bidi image injection, voice_say, etc.).
    The voice persona is a full participant
    in the cross-persona log + briefing system.

    Args:
        action: "start" | "stop" | "status" | "say" | "debug"
        text: For action="say" — speak text once via G1 TtsMaker (no bidi)
        provider: "openai" | "nova_sonic" | "gemini" (default: env VOICE_PROVIDER or openai)
        voice: provider-specific voice name (default: env VOICE_NAME or alloy/tiffany/Kore)
        audio_processing: WebRTC AEC + NS + AGC. Default True (REQUIRED for chest speaker)
        stream_delay_ms: AEC speaker→mic delay hint. 120 for G1 DDS path
        vad_threshold: 0.0-1.0. Higher = less twitchy. 0.7 stops echo triggers
        silence_duration_ms: How long of silence ends a turn. 700 = relaxed
        network_interface: DDS interface (default eth0)

    Returns: dict with status + diagnostic content
    """
    if not _probe_bidi():
        return {
            "status": "error",
            "content": [{"text": f"bidi/audio deps missing: {_BIDI_ERR}"}],
        }

    # ─── start ─────────────────────────────────────────────────────────
    if action == "start":
        if _STATE["running"]:
            return {
                "status": "success",
                "content": [{"text":
                    f"🎙 already running session={_STATE['session_id']} "
                    f"provider={_STATE['provider']} model={_STATE['model_id']}"
                }],
            }

        prov = (provider or os.getenv("VOICE_PROVIDER") or "openai").lower()
        if prov in ("openai", "openai_realtime") and not os.getenv("OPENAI_API_KEY"):
            return {"status": "error",
                    "content": [{"text": "OPENAI_API_KEY env var not set"}]}

        v = voice or os.getenv("VOICE_NAME") or None

        sid = f"g1speak-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        _STATE["session_id"] = sid
        _STATE["provider"] = prov
        _STATE["voice"] = v or "(default)"
        _STATE["started_at"] = time.time()
        _STATE["last_error"] = ""

        stop_event = threading.Event()
        _STATE["stop_event"] = stop_event
        _STATE["running"] = True

        t = threading.Thread(
            target=_runner_main,
            kwargs=dict(
                provider=prov,
                voice=v,
                audio_processing=audio_processing,
                network_interface=network_interface,
                stream_delay_ms=stream_delay_ms,
                vad_threshold=vad_threshold,
                silence_duration_ms=silence_duration_ms,
                stop_event=stop_event,
            ),
            daemon=True,
            name="g1_speak_runner",
        )
        _STATE["thread"] = t
        t.start()

        # Wait briefly for init
        time.sleep(0.5)

        return {
            "status": "success",
            "content": [{"text":
                f"🎙 g1_speak started\n"
                f"  session: {sid}\n"
                f"  provider: {prov}\n"
                f"  voice: {v or '(default)'}\n"
                f"  AEC: {'on' if audio_processing else 'OFF'} (delay={stream_delay_ms}ms)\n"
                f"  VAD threshold={vad_threshold} silence={silence_duration_ms}ms\n"
                f"  network: {network_interface}\n"
                f"  Listening on Brio mic, speaking via G1 chest speaker (DDS).\n"
                f"  Briefings via voice_bridge, transcripts → agent_log.\n"
                f"  Tip: g1_speak(action='status') / 'debug' for diagnostics"
            }],
        }

    # ─── stop ─────────────────────────────────────────────────────────
    if action == "stop":
        if not _STATE["running"]:
            return {"status": "success", "content": [{"text": "🎙 not running"}]}
        ev = _STATE.get("stop_event")
        if ev:
            ev.set()
        t = _STATE.get("thread")
        if t:
            t.join(timeout=5.0)
        _STATE["running"] = False
        return {
            "status": "success",
            "content": [{"text":
                f"🎙 stopped session={_STATE['session_id']}, "
                f"last_err={_STATE.get('last_error') or '(none)'}"
            }],
        }

    # ─── status ───────────────────────────────────────────────────────
    if action == "status":
        from tools.g1_bidi_audio import STATS
        running = _STATE["running"]
        uptime = time.time() - _STATE["started_at"] if running else 0
        return {
            "status": "success",
            "content": [{"text":
                f"🎙 g1_speak status\n"
                f"  running: {running}\n"
                f"  session: {_STATE['session_id']}\n"
                f"  provider: {_STATE['provider']}\n"
                f"  voice: {_STATE['voice']}\n"
                f"  model: {_STATE['model_id']}\n"
                f"  uptime: {uptime:.1f}s\n"
                f"  frames_captured: {STATS['frames_captured']}\n"
                f"  g1_frames_sent: {STATS['g1_frames_sent']}\n"
                f"  ref_buf_qsize: {STATS['ref_buf_qsize']}\n"
                f"  energy: mean={STATS['energy_mean_abs']} max={STATS['energy_max_abs']}\n"
                f"  last_error: {_STATE.get('last_error') or '(none)'}"
            }],
        }

    # ─── say (one-shot, no bidi) ──────────────────────────────────────
    if action == "say":
        if not text:
            return {"status": "error", "content": [{"text": "say requires text=..."}]}
        try:
            from tools._g1_common import ensure_dds, get_audio_client
            err = ensure_dds(network_interface)
            if err:
                return {"status": "error", "content": [{"text": f"DDS init: {err}"}]}
            ac = get_audio_client()
            rc = ac.TtsMaker(text, 0)
            return {
                "status": "success" if rc == 0 else "error",
                "content": [{"text":
                    f"🎙 TtsMaker rc={rc} text={text!r}"
                    + ("" if rc == 0 else " (try shorter text or check controller)")
                }],
            }
        except Exception as e:
            return {"status": "error", "content": [{"text": f"say failed: {e}"}]}

    # ─── debug ────────────────────────────────────────────────────────
    if action == "debug":
        from tools.g1_bidi_audio import STATS, autopick_brio
        from tools.voice_bridge import stats as vb_stats
        from tools.agent_log import stats as al_stats

        # Enumerate input devices (key for echo debugging)
        devices_out = "(pyaudio not available)"
        try:
            import pyaudio as _pa
            p = _pa.PyAudio()
            try:
                lines = []
                brio_idx = autopick_brio(p)
                env_idx = os.getenv("BRIO_DEVICE_INDEX", "").strip()
                lines.append(f"  BRIO_DEVICE_INDEX env: {env_idx or '(unset)'}")
                lines.append(f"  autopick result: {brio_idx if brio_idx is not None else '(not found)'}")
                lines.append("  all input devices:")
                for i in range(p.get_device_count()):
                    try:
                        d = p.get_device_info_by_index(i)
                        if d.get("maxInputChannels", 0) > 0:
                            mark = "★" if i == brio_idx else " "
                            lines.append(f"   {mark} [{i}] {d['name']!r} "
                                         f"(rate={int(d.get('defaultSampleRate',0))}, "
                                         f"ch={d.get('maxInputChannels')})")
                    except Exception as e:
                        lines.append(f"     [{i}] err: {e}")
                devices_out = "\n".join(lines)
            finally:
                p.terminate()
        except Exception as e:
            devices_out = f"(enum failed: {e})"

        # PA snapshot
        pa_default = pa_sources = "(pactl unavailable)"
        try:
            import shutil as _shutil, subprocess as _sp
            if _shutil.which("pactl"):
                pa_default = "\n".join(
                    l for l in _sp.check_output(["pactl", "info"], text=True, timeout=2).splitlines()
                    if "Default" in l
                )
                pa_sources = _sp.check_output(["pactl", "list", "sources", "short"], text=True, timeout=2).strip()
        except Exception as e:
            pa_default = f"pactl err: {e}"

        out = (
            f"🎙 g1_speak debug\n"
            f"--- state ---\n"
            f"running={_STATE['running']} session={_STATE['session_id']} "
            f"provider={_STATE['provider']} voice={_STATE['voice']} model={_STATE['model_id']}\n"
            f"--- audio stats ---\n"
            f"frames_captured={STATS['frames_captured']} "
            f"g1_frames_sent={STATS['g1_frames_sent']} "
            f"ref_buf_qsize={STATS['ref_buf_qsize']}\n"
            f"energy_mean_abs={STATS['energy_mean_abs']} energy_max_abs={STATS['energy_max_abs']}\n"
            f"--- input devices (★ = autopicked) ---\n{devices_out}\n"
            f"--- pulseaudio ---\n{pa_default}\n{pa_sources}\n"
            f"--- voice_bridge ---\n{vb_stats()}\n"
            f"--- agent_log ---\n{al_stats()}\n"
            f"--- last error ---\n"
            f"{_STATE.get('last_error') or '(none)'}"
        )
        return {"status": "success", "content": [{"text": out}]}

    return {
        "status": "error",
        "content": [{"text": f"unknown action {action!r} — use start/stop/status/say/debug"}],
    }
