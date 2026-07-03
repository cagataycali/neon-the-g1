"""🎵 G1 audio — composed helpers beyond the raw SDK.

The SDK's AudioClient (TtsMaker, Get/SetVolume, LedControl, PlayStream, ...)
is fully reachable via ``use_unitree(service='audio', ...)``. This module keeps
only what the SDK doesn't give you directly:
  - g1_play_wav(file_path)  reads a WAV → raw PCM → PlayStream
  - g1_asr(duration_s)      raw _Call to the undocumented ASR API (id 1002)
"""
from __future__ import annotations

import os
import wave
from typing import Any, Dict

from strands import tool

from ._g1_common import ensure_dds, get_audio_client, decode_code, _unwrap_rc, _normalize


@tool
def g1_play_wav(
    file_path: str,
    app_name: str = "devduck",
    stream_id: str = "default",
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    🎵 Play a WAV file through the G1 head speaker.

    The SDK's ``PlayStream`` takes raw PCM bytes — this helper does the
    file I/O and forwards the frames. Use 16kHz mono PCM WAV (or similar;
    the SDK accepts arbitrary PCM but robot audio hardware is 16kHz).

    Args:
        file_path: path to WAV file on this host.
        app_name: app identifier used by the G1 audio mixer.
        stream_id: stream identifier within the app.
        network_interface: DDS interface (default 'eth0').

    Returns:
        dict with status, rc, message and audio metadata.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    if not os.path.isfile(file_path):
        result["message"] = f"File not found: {file_path}"
        return _normalize(result)

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    try:
        with wave.open(file_path, "rb") as w:
            n_channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            framerate = w.getframerate()
            frames = w.readframes(w.getnframes())
    except Exception as e:
        result["message"] = f"WAV read failed: {e}"
        return _normalize(result)

    try:
        audio = get_audio_client()
        ret = audio.PlayStream(app_name, stream_id, frames)
    except Exception as e:
        result["message"] = f"PlayStream raised: {e}"
        return _normalize(result)

    result["rc"] = _unwrap_rc(ret)
    result["status"] = "success"
    result["message"] = (
        f"Playing {file_path} ({framerate}Hz, {n_channels}ch, "
        f"{sampwidth*8}bit, {len(frames)} bytes) via app='{app_name}'"
    )
    return _normalize(result)


@tool
def g1_asr(
    pcm_file: str = "",
    duration_s: float = 3.0,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    🎧 Onboard ASR (speech-to-text) via AudioClient API 1002.

    The SDK registers API-ID 1002 in ``AudioClient.Init()`` but doesn't
    expose a Python helper. We call it raw via ``client._Call(1002, ...)``.

    Two modes:
      - If ``pcm_file`` is given, upload its raw PCM and transcribe.
      - Otherwise ask the service to listen on its own mic for ``duration_s``.

    The exact request schema isn't publicly documented — this returns the
    raw response so you can discover it.

    Args:
        pcm_file: optional path (on the robot) to a raw PCM file to transcribe.
        duration_s: if ``pcm_file`` is empty, listen for this many seconds.
        network_interface: DDS interface (default 'eth0').
    """
    import json as _json
    result: Dict[str, Any] = {"status": "error", "rc": None, "data": None, "message": ""}

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    try:
        audio = get_audio_client()
        payload: Dict[str, Any] = {"duration": float(duration_s)}
        if pcm_file:
            payload["pcm_file"] = pcm_file
        code, data = audio._Call(1002, _json.dumps(payload))
    except Exception as e:
        result["message"] = f"ASR _Call raised: {e}"
        return _normalize(result)

    result["rc"] = code
    if code == 0:
        try:
            result["data"] = _json.loads(data) if isinstance(data, str) else data
        except Exception:
            result["data"] = str(data)[:500]
        result["status"] = "success"
        keys = (list(result['data'].keys())
                if isinstance(result['data'], dict) else type(result['data']).__name__)
        result["message"] = f"ASR ok — response keys: {keys}"
    else:
        result["message"] = (
            f"ASR rc={decode_code(code)} (API 1002 may not be enabled on this firmware)"
        )
    return _normalize(result)
