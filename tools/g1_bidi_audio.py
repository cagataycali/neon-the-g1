"""G1 bidirectional audio IO.

Architecture:

    Brio Mic (PyAudio 16k)
      ├─ AEC (WebRTC, far_buf = post-PlayStream bytes G1 actually played)
      ├─ ratecv 16k → 24k (OpenAI requires ≥24k input)
      └─ → BidiInput → bidi model

    bidi model 24k audio
      ├─ ratecv 24k → 16k
      ├─ → G1 chest speaker via DDS PlayStream
      └─ POST-PlayStream → ref_buf 16k (AEC reference; correct timing)

    Briefing input: SQLite poll → BidiTextInputEvent (cross-process)
    Log output:     BidiTranscriptStreamEvent → tools.agent_log.record()

Why feed ref_buf POST-PlayStream (not at speaker-output time):
- The G1 chest speaker writer has a queue. If we feed ref_buf when we
  ENQUEUE bytes, AEC sees the reference earlier than the speaker actually
  emits → misaligned far signal → echo bleeds through.
- Feeding ref_buf in the writer thread, AFTER PlayStream returns rc=0,
  aligns the AEC far signal with what the speaker JUST emitted. With
  stream_delay_ms set to the DDS round-trip delay, AEC subtracts cleanly.
"""
from __future__ import annotations
import asyncio
import audioop
import base64
import logging
import os as _os
import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Optional

import numpy as np
import pyaudio
from pywebrtc_audio import AudioProcessor

from strands.experimental.bidi.io.audio import _BidiAudioBuffer
from strands.experimental.bidi.types.events import (
    BidiAudioInputEvent,
    BidiAudioStreamEvent,
    BidiInterruptionEvent,
    BidiOutputEvent,
    BidiTextInputEvent,
    BidiTranscriptStreamEvent,
)
from strands.experimental.bidi.types.io import BidiInput, BidiOutput

if TYPE_CHECKING:
    from strands.experimental.bidi.agent.agent import BidiAgent as BidiAgentType

from tools.memory import memory
from tools.voice_bridge import pop_pending, flush_stale
from tools.agent_log import record as alog

log = logging.getLogger("g1_bidi_audio")

# ─── Audio config ─────────────────────────────────────────────────────────
MIC_RATE = 16000
G1_RATE = 16000
OPENAI_RATE = 24000
FRAME_SIZE = 160
PYAUDIO_FRAMES = 160
SILENCE_16K = np.zeros(FRAME_SIZE, dtype=np.int16).tobytes()

DEFAULT_STREAM_DELAY_MS = 120
MUTE_POLL_SECONDS = 1.0

STATS: dict = {
    "frames_captured": 0,
    "g1_frames_sent": 0,
    "ref_buf_qsize": 0,
    "energy_mean_abs": 0,
    "energy_max_abs": 0,
}


# ─── Mute state ───────────────────────────────────────────────────────────
class MuteState:
    """Polls memory kv `voice.muted` once/second; audio callbacks read flag."""
    def __init__(self):
        self._muted = False
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True, name="g1-mute-poll")
        self._t.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                v = memory(action="kv_get", key="voice.muted")
                self._muted = isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on")
            except Exception:
                pass
            self._stop.wait(MUTE_POLL_SECONDS)

    @property
    def muted(self) -> bool:
        return self._muted

    def stop(self):
        self._stop.set()


# ─── Mic auto-pick (generalized: DJI / Brio / any USB mic) ─────────────────
# Priority of name substrings to match, highest first. Override with
# VOICE_MIC_NAME (comma-separated substrings, case-insensitive).
DEFAULT_MIC_KEYWORDS = ["DJI", "Logi", "Brio", "USB", "Mic"]


def _mic_keywords() -> list:
    """Ordered list of name substrings to match a preferred input device."""
    import os as _o
    env = _o.getenv("VOICE_MIC_NAME", "").strip()
    if env:
        return [k.strip() for k in env.split(",") if k.strip()]
    return DEFAULT_MIC_KEYWORDS


def autopick_mic(p: pyaudio.PyAudio) -> Optional[int]:
    """Find the preferred input device on this Jetson.

    Matches VOICE_MIC_NAME substrings (or DEFAULT_MIC_KEYWORDS) in priority
    order, case-insensitive. Falls back to the first device with input
    channels. Works for DJI receiver, Logitech Brio, or any USB mic.
    """
    keywords = [k.lower() for k in _mic_keywords()]

    # Collect all input-capable devices once.
    inputs = []
    for i in range(p.get_device_count()):
        try:
            d = p.get_device_info_by_index(i)
        except Exception:
            continue
        if d.get("maxInputChannels", 0) <= 0:
            continue
        inputs.append((i, str(d.get("name", ""))))

    # Priority match: earlier keyword wins.
    for kw in keywords:
        for i, name in inputs:
            if kw in name.lower():
                log.info(f"autopick_mic: matched '{name}' (idx={i}) via '{kw}'")
                return i

    # No keyword hit. Prefer PulseAudio 'pulse'/'default' virtual sources over
    # raw Tegra APE routing devices (which aren't usable capture endpoints).
    for pref in ("pulse", "default"):
        for i, name in inputs:
            if name.strip().lower() == pref:
                log.info(f"autopick_mic: no keyword match, using PA '{name}' (idx={i})")
                return i

    # Last resort: skip Tegra APE/HDA virtual devices if a non-Tegra input exists.
    non_tegra = [(i, n) for i, n in inputs
                 if "tegra" not in n.lower() and "ape" not in n.lower()
                 and "hda" not in n.lower()]
    pool = non_tegra or inputs
    if pool:
        i, name = pool[0]
        log.info(f"autopick_mic: no keyword match, using '{name}' (idx={i})")
        return i
    return None


# Backward-compat alias — old callers still work.
def autopick_brio(p: pyaudio.PyAudio) -> Optional[int]:
    return autopick_mic(p)


# Candidate capture rates to try, in order. Brio supports 16k directly;
# DJI Mic Mini (USB) only supports 48k. We open at whatever the device
# accepts, then downsample to 16k for AEC + the model pipeline.
CAPTURE_RATE_CANDIDATES = [16000, 48000, 44100, 32000]


def pick_capture_rate(p: pyaudio.PyAudio, device_index: Optional[int],
                      channels: int) -> int:
    """Return the first supported input rate for the device (default 48000)."""
    for rate in CAPTURE_RATE_CANDIDATES:
        try:
            if p.is_format_supported(
                rate,
                input_device=device_index,
                input_channels=channels,
                input_format=pyaudio.paInt16,
            ):
                return rate
        except Exception:
            continue
    return 48000  # safe fallback for USB mics


def activate_mic_via_pa():
    """Best-effort: set preferred mic as PA default-source, unmute, raise gain.

    Matches VOICE_MIC_NAME substrings (or defaults) against `pactl` sources.
    """
    import shutil
    import subprocess
    if not shutil.which("pactl"):
        return
    keywords = [k.lower() for k in _mic_keywords()]
    try:
        out = subprocess.check_output(
            ["pactl", "list", "sources", "short"], text=True, timeout=2
        )
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and ".monitor" not in parts[1]:
                rows.append(parts[1])  # source name

        target = None
        for kw in keywords:
            for name in rows:
                if kw in name.lower():
                    target = name
                    break
            if target:
                break
        if not target:
            return
        log.info(f"activate_mic_via_pa: selecting PA source '{target}'")
        subprocess.run(["pactl", "set-default-source", target], check=False, timeout=2)
        subprocess.run(["pactl", "set-source-mute", target, "0"], check=False, timeout=2)
        subprocess.run(["pactl", "set-source-volume", target, "60000"], check=False, timeout=2)
    except Exception as e:
        log.debug(f"PA mic activation failed: {e}")


# Backward-compat alias.
def activate_brio_via_pa():
    return activate_mic_via_pa()


# ─── G1 chest speaker writer (DDS, with POST-PlayStream AEC ref feed) ─────
class G1SpeakerWriter:
    """Drains 16k int16 PCM → AudioClient.PlayStream over DDS.

    KEY: After each successful PlayStream, splits the chunk into 160-sample
    frames and pushes them to ref_buf. This is the moment to feed AEC because
    PlayStream returns when DDS has accepted the bytes — speaker emit is
    imminent. With stream_delay_ms set to the DDS roundtrip delay (~120ms
    on G1), AEC's far signal is aligned with the mic's near signal.
    """

    SILENCE_FLUSH = 0.7
    APP_NAME = "devduck.g1_speak"

    def __init__(self,
                 network_interface: str = "eth0",
                 ref_buf: Optional[queue.Queue] = None):
        self.q: queue.Queue[bytes | None] = queue.Queue(maxsize=200)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, daemon=True, name="g1-speaker")
        self._network_interface = network_interface
        self._client = None
        self._ref_buf = ref_buf
        self._frame_residual = b""

    def start(self) -> bool:
        try:
            from tools._g1_common import ensure_dds, get_audio_client
            err = ensure_dds(self._network_interface)
            if err:
                log.warning(f"g1 speaker DDS init failed: {err}")
                return False
            self._client = get_audio_client()
            self._t.start()
            return True
        except Exception as e:
            log.warning(f"g1 speaker init failed: {e}")
            return False

    def stop(self):
        self._stop.set()
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass

    def feed(self, pcm16k: bytes):
        try:
            self.q.put_nowait(pcm16k)
        except queue.Full:
            pass

    def clear(self):
        """Drain audio queue + ref_buf + reset frame residual.

        Called on BidiInterruptionEvent. AEC must NOT subtract stale audio
        from the user's interruption — drain everything.
        """
        drained = 0
        while not self.q.empty():
            try:
                self.q.get_nowait()
                drained += 1
            except queue.Empty:
                break
        self._frame_residual = b""
        if self._ref_buf is not None:
            while not self._ref_buf.empty():
                try:
                    self._ref_buf.get_nowait()
                except queue.Empty:
                    break
        return drained

    def _feed_ref(self, chunk: bytes) -> None:
        """Slice PCM into 160-sample frames; push to ref_buf. Drop-oldest on full."""
        if self._ref_buf is None:
            return
        buf = self._frame_residual + chunk
        frame_bytes = FRAME_SIZE * 2
        offset = 0
        while offset + frame_bytes <= len(buf):
            frame = np.frombuffer(buf[offset:offset + frame_bytes], dtype=np.int16).copy()
            try:
                self._ref_buf.put_nowait(frame)
            except queue.Full:
                try:
                    self._ref_buf.get_nowait()
                    self._ref_buf.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass
            offset += frame_bytes
        self._frame_residual = buf[offset:]
        STATS["ref_buf_qsize"] = self._ref_buf.qsize()

    def _loop(self):
        if self._client is None:
            return
        stream_id = None
        last_chunk_t = 0
        while not self._stop.is_set():
            try:
                chunk = self.q.get(timeout=0.2)
            except queue.Empty:
                if stream_id and (time.time() - last_chunk_t) > self.SILENCE_FLUSH:
                    stream_id = None
                continue
            if chunk is None:
                break
            if stream_id is None:
                stream_id = str(int(time.time() * 1000))
            try:
                rc, _ = self._client.PlayStream(self.APP_NAME, stream_id, chunk)
                if rc == 0:
                    STATS["g1_frames_sent"] += 1
                    # Feed AEC reference IMMEDIATELY after PlayStream returns —
                    # this gives accurate timing alignment for AEC.
                    self._feed_ref(chunk)
                last_chunk_t = time.time()
            except Exception as e:
                log.debug(f"g1 PlayStream err: {e}")


# ─── Bidi I/O classes ─────────────────────────────────────────────────────
class _MicInput(BidiInput):
    """Brio mic → AEC → ratecv 16→24 → bidi model."""

    def __init__(self, ap: Optional[AudioProcessor], ref_buf: queue.Queue,
                 mute: MuteState, device_index: Optional[int]):
        self._ap = ap
        self._ref_buf = ref_buf
        self._mute = mute
        self._device_index = device_index
        self._buffer = _BidiAudioBuffer()
        self._ratecv_state = None

    async def start(self, agent: "BidiAgentType") -> None:
        cfg = agent.model.config["audio"]
        self._channels = cfg["channels"]
        self._format = cfg["format"]
        self._target_rate = cfg["input_rate"]

        self._buffer.start()
        self._audio = pyaudio.PyAudio()

        # Detect the device's supported capture rate. Brio → 16k, DJI USB → 48k.
        self._capture_rate = pick_capture_rate(
            self._audio, self._device_index, self._channels
        )
        # Downsample state: capture_rate → 16k (for AEC + pipeline).
        self._downsample_state = None
        # frames_per_buffer scaled so each callback ≈ one 10ms/16k FRAME_SIZE.
        fpb = int(PYAUDIO_FRAMES * self._capture_rate / MIC_RATE)
        log.info(
            f"_MicInput: device={self._device_index} "
            f"capture_rate={self._capture_rate} target_rate={self._target_rate} "
            f"fpb={fpb}"
        )
        self._stream = self._audio.open(
            channels=self._channels,
            format=pyaudio.paInt16,
            frames_per_buffer=fpb,
            input=True,
            rate=self._capture_rate,
            input_device_index=self._device_index,
            stream_callback=self._callback,
        )

    async def stop(self) -> None:
        try:
            self._stream.close()
        except Exception:
            pass
        try:
            self._audio.terminate()
        except Exception:
            pass
        self._buffer.stop()

    async def __call__(self) -> BidiAudioInputEvent:
        data = await asyncio.to_thread(self._buffer.get)
        return BidiAudioInputEvent(
            audio=base64.b64encode(data).decode("utf-8"),
            channels=self._channels,
            format=self._format,
            sample_rate=self._target_rate,
        )

    def _callback(self, in_data: bytes, frame_count: int, *_: Any):
        try:
            # If capturing above 16k (e.g. DJI USB @48k), downsample to 16k
            # first so AEC and the rest of the pipeline stay at 16k.
            if getattr(self, "_capture_rate", MIC_RATE) != MIC_RATE:
                in_data, self._downsample_state = audioop.ratecv(
                    in_data, 2, self._channels, self._capture_rate,
                    MIC_RATE, self._downsample_state
                )
            near = np.frombuffer(in_data, dtype=np.int16)
            try:
                arr = np.abs(near.astype(np.int32))
                STATS["energy_mean_abs"] = int(arr.mean())
                STATS["energy_max_abs"] = int(arr.max())
                STATS["frames_captured"] += 1
            except Exception:
                pass

            if self._mute.muted:
                if self._target_rate != MIC_RATE:
                    n_samples = int(FRAME_SIZE * self._target_rate / MIC_RATE)
                    self._buffer.put(np.zeros(n_samples, dtype=np.int16).tobytes())
                else:
                    self._buffer.put(SILENCE_16K)
                return (None, pyaudio.paContinue)

            if self._ap is not None:
                try:
                    far = self._ref_buf.get_nowait()
                except queue.Empty:
                    far = np.zeros(FRAME_SIZE, dtype=np.int16)
                cleaned = self._ap.process(near, far).tobytes()
            else:
                cleaned = in_data

            if self._target_rate != MIC_RATE:
                resampled, self._ratecv_state = audioop.ratecv(
                    cleaned, 2, 1, MIC_RATE, self._target_rate, self._ratecv_state
                )
            else:
                resampled = cleaned
            self._buffer.put(resampled)
        except Exception as e:
            log.debug(f"mic callback err: {e}")
        return (None, pyaudio.paContinue)


class _G1SpeakerOutput(BidiOutput):
    """bidi 24k → ratecv → G1 chest speaker (DDS).

    Note: ref_buf feeding now happens in G1SpeakerWriter._loop AFTER
    PlayStream returns. This output handler just resamples and queues.
    """

    def __init__(self, writer: G1SpeakerWriter):
        self._writer = writer
        self._target_rate = OPENAI_RATE
        self._ratecv_state = None

    async def start(self, agent: "BidiAgentType") -> None:
        cfg = agent.model.config["audio"]
        self._target_rate = cfg["output_rate"]

    async def stop(self) -> None:
        pass

    async def __call__(self, event: BidiOutputEvent) -> None:
        if isinstance(event, BidiAudioStreamEvent):
            pcm = base64.b64decode(event["audio"])
            if self._target_rate != G1_RATE:
                pcm16, self._ratecv_state = audioop.ratecv(
                    pcm, 2, 1, self._target_rate, G1_RATE, self._ratecv_state
                )
            else:
                pcm16 = pcm
            self._writer.feed(pcm16)

        elif isinstance(event, BidiInterruptionEvent):
            # Drain audio queue + ref_buf + frame_residual in one call
            self._writer.clear()
            self._ratecv_state = None


class _BriefingInput(BidiInput):
    """Pulls briefings from voice_bridge SQLite queue → BidiTextInputEvent."""
    POLL_SECONDS = 2.0
    BATCH_SIZE = 5

    def __init__(self, mute: MuteState):
        self._mute = mute

    async def start(self, agent) -> None:
        flush_stale()

    async def stop(self) -> None:
        pass

    async def __call__(self) -> BidiTextInputEvent:
        while True:
            await asyncio.sleep(self.POLL_SECONDS)
            rows = pop_pending(self.BATCH_SIZE)
            if not rows:
                continue
            if self._mute.muted:
                continue
            lines = []
            for _id, source, msg, imp in rows:
                tag = "URGENT" if imp >= 2 else "info"
                lines.append(f"[{source}/{tag}] {msg}")
            briefing = "[BRIEFING] " + " | ".join(lines)
            alog("voice", "system", briefing)
            return BidiTextInputEvent(text=briefing, role="user")


class _LogOutput(BidiOutput):
    """Records bidi transcripts to unified agent_log."""
    async def start(self, agent) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def __call__(self, event: BidiOutputEvent) -> None:
        if isinstance(event, BidiTranscriptStreamEvent):
            if not event.get("is_final"):
                return
            role = event.get("role", "assistant")
            text = (event.get("text") or "").strip()
            if text:
                alog("voice", role, text)


# ─── Public facade ────────────────────────────────────────────────────────
class G1BidiAudioIO:
    """Mic + G1 chest speaker IO with WebRTC AEC + briefing channel + log channel.

    `BidiProcessedAudioIO` API
    Use:
        audio_io = G1BidiAudioIO(network_interface="eth0")
        audio_io.start_speaker()  # spin up DDS writer thread
        await agent.run(
            inputs=[audio_io.input(), audio_io.briefing_input()],
            outputs=[audio_io.output(), audio_io.log_output()],
        )
        audio_io.shutdown()
    """

    def __init__(self,
                 network_interface: str = "eth0",
                 audio_processing: bool = True,
                 stream_delay_ms: int = DEFAULT_STREAM_DELAY_MS):
        self._network_interface = network_interface
        self._audio_processing = audio_processing
        # Cap at 500 frames (~5s @ 100Hz) — prevents unbounded growth if AEC stalls
        self._ref_buf: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self.mute = MuteState()
        # Pass ref_buf to writer so it can feed AEC ref POST-PlayStream
        self._writer = G1SpeakerWriter(network_interface=network_interface,
                                       ref_buf=self._ref_buf)
        self._writer_started = False

        self._ap = (
            AudioProcessor(
                sample_rate=MIC_RATE,
                echo_cancellation=True,
                noise_suppression=True,
                auto_gain_control=True,
                stream_delay_ms=stream_delay_ms,
            ) if audio_processing else None
        )

        # Pick mic up front (best-effort PA activation)
        activate_mic_via_pa()

        # Allow explicit override via BRIO_DEVICE_INDEX (Docker-friendly)
        env_idx = _os.getenv("BRIO_DEVICE_INDEX", "").strip()
        if env_idx.isdigit():
            self._device_index = int(env_idx)
            log.info(f"using BRIO_DEVICE_INDEX={self._device_index} from env")
        else:
            p = pyaudio.PyAudio()
            try:
                self._device_index = autopick_mic(p)
                if self._device_index is None:
                    try:
                        self._device_index = p.get_default_input_device_info()["index"]
                    except Exception:
                        self._device_index = None
            finally:
                p.terminate()

    def start_speaker(self) -> bool:
        if self._writer_started:
            return True
        ok = self._writer.start()
        self._writer_started = ok
        return ok

    def input(self) -> _MicInput:
        return _MicInput(self._ap, self._ref_buf, self.mute, self._device_index)

    def output(self) -> _G1SpeakerOutput:
        return _G1SpeakerOutput(self._writer)

    def log_output(self) -> _LogOutput:
        return _LogOutput()

    def briefing_input(self) -> _BriefingInput:
        return _BriefingInput(self.mute)

    def shutdown(self):
        self.mute.stop()
        self._writer.stop()
