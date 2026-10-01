"""Vision tool — capture a camera frame and inject it into the bidi voice agent.

Uses BidiImageInputEvent so the realtime model sees the image natively in
its own multimodal context (no separate vision-API round-trip):

    user speech ──┐
                  ▼
                bidi model (gpt-realtime-2 / Gemini Live) ──► spoken reply
                  ▲       ▲
                  │       │
                audio   take_photo() injects:
                          • BidiImageInputEvent (the JPEG)
                          • optional follow-up BidiTextInputEvent (the question)

Gemini Live handles BidiImageInputEvent out of the box. The OpenAI Realtime
provider in strands hasn't wired image dispatch yet, so we patch
BidiOpenAIRealtimeModel.send() at import time to add it. The wire format
matches OpenAI's documented `input_image` content block for
conversation.item.create.

Devices:
  Linux (G1 Jetson):
    0 = auto-pick RealSense D435i (preferred) → fallback to Brio
    1 = force Logitech Brio (V4L2)
  macOS (dev):
    0 = FaceTime HD Camera, 1 = screen capture
    `ffmpeg -f avfoundation -list_devices true -i ""` to enumerate.
"""
from __future__ import annotations
import asyncio
import base64
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from strands import tool
from strands.experimental.bidi.types.events import (
    BidiImageInputEvent,
    BidiTextInputEvent,
)

CACHE_DIR = Path(tempfile.gettempdir()) / "lookout_vision"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ── Patch OpenAI Realtime to support BidiImageInputEvent ──────────────
# Gemini Live already handles it; OpenAI's strands integration doesn't
# yet (only text/audio/tool-result). The OpenAI Realtime API itself DOES
# accept `input_image` content blocks in conversation.item.create as of
# gpt-realtime-2.
def _patch_openai_image_support() -> None:
    """Add BidiImageInputEvent dispatch to BidiOpenAIRealtimeModel.

    Idempotent — safe to call multiple times.
    """
    try:
        from strands.experimental.bidi.models.openai_realtime import (
            BidiOpenAIRealtimeModel,
        )
    except ImportError:
        return  # OpenAI bidi not available, nothing to patch

    if getattr(BidiOpenAIRealtimeModel, "_image_patched", False):
        return

    async def _send_image_content(self, image_input: BidiImageInputEvent) -> None:
        """Send an image as an input_image content block on a user message."""
        b64 = image_input.image
        mime = image_input.mime_type or "image/jpeg"
        # OpenAI accepts data: URLs OR plain b64 in image_url field.
        # The data: URL form is the safest cross-version contract.
        data_url = f"data:{mime};base64,{b64}"
        item = {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_image", "image_url": data_url}],
        }
        await self._send_event({"type": "conversation.item.create", "item": item})

    # Wrap the existing send() to dispatch image events to our handler.
    _orig_send = BidiOpenAIRealtimeModel.send

    async def _patched_send(self, content):
        if isinstance(content, BidiImageInputEvent):
            if not self._connection_id:
                raise RuntimeError("model not started | call start before sending")
            await self._send_image_content(content)
            return
        await _orig_send(self, content)

    BidiOpenAIRealtimeModel._send_image_content = _send_image_content
    BidiOpenAIRealtimeModel.send = _patched_send
    BidiOpenAIRealtimeModel._image_patched = True


# Patch immediately on import so anybody importing tools.vision (or
# tools/__init__) gets image support before they construct the model.
_patch_openai_image_support()


# ── camera capture ────────────────────────────────────────────────────
# Linux (G1 Jetson): use the use_camera tool — handles RealSense + Brio + V4L2.
# macOS (dev):       use ffmpeg avfoundation (FaceTime / screen capture).
def _capture_frame_macos(device: int = 0, output: Path = None) -> Path:
    output = output or (CACHE_DIR / f"frame_{int(time.time())}.jpg")
    cmd = [
        "ffmpeg", "-y",
        "-f", "avfoundation",
        "-framerate", "30",
        "-video_size", "1280x720",
        "-i", str(device),
        "-frames:v", "1",
        "-q:v", "3",
        str(output),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or not output.exists():
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-500:]}")
    return output



# ââ dashboard snapshot (shared frame, avoids device contention) âââââââ
# The bare-metal / containerized dashboard (docs/dashboard/server.py) holds
# the single USB camera open 24/7 for its MJPEG grabber. V4L2 is single-open,
# so the voice agent CANNOT also open /dev/video0 directly. Instead we pull a
# JPEG from the dashboard's shared snapshot endpoint. One camera, many readers.
import os as _os
import urllib.request as _urlreq
import ssl as _ssl

_DASH_URL = _os.getenv("NEON_DASHBOARD_URL", "https://localhost:8080").rstrip("/")
_DASH_CAM = _os.getenv("NEON_DASHBOARD_CAM", "brio")  # brio | realsense_color


def _dashboard_service_token() -> "Optional[str]":
    """Mint/reuse the dashboard's 'thinker' service JWT (same signing secret)."""
    tok = _os.getenv("NEON_DASHBOARD_TOKEN")
    if tok:
        return tok
    try:
        import sys as _sys
        _dash = str(Path(__file__).resolve().parent.parent / "docs" / "dashboard")
        if _dash not in _sys.path:
            _sys.path.insert(0, _dash)
        from auth import service_token  # type: ignore
        return service_token("voice")
    except Exception:
        return None


def _capture_frame_dashboard(output: Path = None, cam: str = None) -> Path:
    """Grab a JPEG from the dashboard's shared camera snapshot endpoint."""
    output = output or (CACHE_DIR / f"frame_{int(time.time())}.jpg")
    cam = cam or _DASH_CAM
    url = f"{_DASH_URL}/api/camera/{cam}/snapshot"
    tok = _dashboard_service_token()
    req = _urlreq.Request(url)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    with _urlreq.urlopen(req, timeout=5, context=ctx) as resp:
        data = resp.read()
    # Guard against the placeholder / tiny error JSON masquerading as a frame.
    if not data or len(data) < 2000 or data[:2] != b"\xff\xd8":
        raise RuntimeError(
            f"dashboard snapshot invalid (cam={cam}, {len(data)} bytes) â "
            "camera may be offline or endpoint returned placeholder/JSON"
        )
    output.write_bytes(data)
    return output


def _capture_frame_linux(device: int = 0, output: Path = None) -> Path:
    """Use the use_camera tool to capture a frame on Linux/Jetson.

    On the G1 we have a RealSense D435i (head) + Logitech Brio (also head).
    """
    output = output or (CACHE_DIR / f"frame_{int(time.time())}.jpg")
    from tools.use_camera import use_camera

    # device==1 historically meant "screen" on macOS — on Linux we map any
    # non-zero device hint to "logitech" (Brio); 0 stays "auto" (prefers RS).
    source = "logitech" if device else "auto"
    r = use_camera(action="save", source=source, save_path=str(output), warmup=3)
    if r.get("status") != "success" or not Path(r.get("path", "")).exists():
        # fallback to logitech if auto failed (e.g. realsense busy)
        if source == "auto":
            r = use_camera(action="save", source="logitech", save_path=str(output), warmup=3)
        if r.get("status") != "success":
            raise RuntimeError(f"use_camera save failed: {r.get('message') or r}")
    return Path(r["path"])


def _capture_frame(device: int = 0) -> Path:
    sysname = platform.system()
    if sysname == "Darwin":
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg not found in PATH. brew install ffmpeg")
        return _capture_frame_macos(device=device)
    if sysname == "Linux":
        # PRIMARY: pull from the dashboard's shared snapshot (no device
        # contention â the dashboard already owns the single USB camera).
        # Skip only if explicitly disabled via NEON_VISION_NO_DASHBOARD=1.
        if _os.getenv("NEON_VISION_NO_DASHBOARD", "").strip().lower() not in ("1", "true", "yes"):
            cam = _DASH_CAM if not device else "brio"
            try:
                return _capture_frame_dashboard(cam=cam)
            except Exception as _e:
                # dashboard down / camera offline â fall through to raw device
                print(f"[vision] dashboard snapshot failed ({_e}); trying raw device")
        return _capture_frame_linux(device=device)
    raise NotImplementedError(f"vision capture not implemented on {sysname}")


# ── @tool exposed to the bidi voice agent ─────────────────────────────
@tool(context=True)
async def take_photo(
    tool_context,
    question: str = "",
    device: int = 0,
) -> dict:
    """Capture a frame from the camera (or screen) and inject it into the
    voice agent's multimodal context. The model sees the image natively
    and replies in audio.

    Use this when the user says "look at me", "what do you see?",
    "describe my desk", "is anyone in the room?", etc.

    Args:
        question: Optional follow-up question to send as text after the image.
                  If empty, the model decides what to say based on its
                  ongoing conversation context. Most of the time you should
                  pass the user's actual question here so the reply is
                  focused.
        device: Camera selector.
                Linux: 0 = RealSense D435i (head, preferred) auto-fallback to Brio,
                       1 = force Logitech Brio.
                macOS: 0 = FaceTime, 1 = screen capture.

    Returns:
        dict with status + image_path (cached JPEG) + question.

    Examples:
        take_photo(question="What do you see?")
        take_photo(question="How many people are in the frame?")
        take_photo(question="What's on my whiteboard?", device=1)
    """
    agent = getattr(tool_context, "agent", None) if tool_context else None
    if agent is None or not hasattr(agent, "send"):
        return {
            "status": "error",
            "message": "no bidi agent context — take_photo only works inside a "
                       "running voice agent (BidiAgent)",
        }

    try:
        image_path = _capture_frame(device=device)
    except Exception as e:
        return {"status": "error", "stage": "capture", "message": str(e)}

    img_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    # Inject the image directly into the multimodal stream. The realtime
    # model will see it as a user-role input_image content block.
    try:
        await agent.send(
            BidiImageInputEvent(image=img_b64, mime_type="image/jpeg")
        )
        if question.strip():
            await agent.send(BidiTextInputEvent(text=question.strip(), role="user"))
    except Exception as e:
        return {
            "status": "error",
            "stage": "inject",
            "message": str(e),
            "image_path": str(image_path),
        }

    return {
        "status": "success",
        "image_path": str(image_path),
        "question": question or "(no follow-up question — model will decide)",
        "device": device,
        "note": "Image injected into bidi stream. Model will respond in audio.",
    }


# ── @tool: standalone camera capture (NO bidi/voice dependency) ───────
# take_photo() injects into the voice agent's multimodal stream and ONLY
# works inside a running BidiAgent. When voice is disabled (NEON_NO_SPEECH=1)
# or when a fleet/MHS caller invokes over the state plane, there is no bidi
# context — take_photo errors out. This tool returns the frame *as-is*:
# a base64-encoded JPEG in a single JSON-safe text block, so it survives
# MHS/zenoh transport (which JSON-serializes results and cannot carry raw
# `bytes`). Use this for headless capture, dashboards, and fleet perception.
def _prime_camera_proxy() -> None:
    """Ensure NEON_CAMERA_PROXY + NEON_CAMERA_PROXY_TOKEN are set so use_camera
    can pull a shared frame from the dashboard (fast, no device contention).

    - NEON_CAMERA_PROXY defaults to https://localhost:8080 if unset.
    - The token is minted from the dashboard's auth store (service_token),
      cached so we only mint once per process. Best-effort: on any failure we
      leave env untouched and use_camera falls back to direct device capture.
    """
    import os as _os
    if not _os.getenv("NEON_CAMERA_PROXY", "").strip():
        _os.environ["NEON_CAMERA_PROXY"] = "https://localhost:8080"
    if _os.getenv("NEON_CAMERA_PROXY_TOKEN", "").strip():
        return  # already have a token
    if getattr(_prime_camera_proxy, "_tried", False):
        return  # don't retry token minting every call
    _prime_camera_proxy._tried = True
    try:
        import sys as _sys
        from pathlib import Path as _P
        dash = str(_P(__file__).resolve().parent.parent / "docs" / "dashboard")
        if dash not in _sys.path:
            _sys.path.insert(0, dash)
        from auth import service_token  # type: ignore
        tok = service_token("mhs")
        if tok:
            _os.environ["NEON_CAMERA_PROXY_TOKEN"] = tok
    except Exception:
        pass  # fall back to direct device capture


@tool
def capture_camera(
    source: str = "auto",
    width: int = 1280,
    height: int = 720,
    quality: int = 80,
    include_base64: bool = True,
    save: bool = False,
    save_path: str = "",
    warmup: int = 2,
) -> dict:
    """Capture a single camera frame and return it as-is (no voice agent needed).

    Unlike take_photo (which injects into the live voice model), this tool
    just grabs a frame and returns the JPEG. Safe to call headless, from the
    MHS fleet plane, dashboards, or any non-voice context.

    The image is returned base64-encoded inside a JSON-safe text payload so it
    survives MHS/zenoh transport (raw bytes cannot be JSON-serialized).

    Args:
        source: "auto" (RealSense→Brio), "realsense", or "logitech".
        width/height: requested resolution (negotiated to nearest supported).
                      Default 1280x720 keeps the base64 payload reasonable.
        quality: JPEG quality 1..100 (default 80).
        include_base64: if False, omit the base64 blob (metadata only) — useful
                        when you just want to confirm a frame is grabbable or
                        when saving to disk.
        save: if True, also write the JPEG to disk.
        save_path: explicit path for save (default: cache dir, timestamped).

    Returns:
        dict with status + width/height/source/backend/bytes and, when
        include_base64 is True, image_b64 (JPEG) + mime_type. On save, also path.

    Examples:
        capture_camera()                                   # auto source, b64 JPEG
        capture_camera(source="logitech", width=1920, height=1080)
        capture_camera(include_base64=False, save=True)    # save only, no blob
    """
    import base64 as _b64
    import os as _os
    try:
        from tools.use_camera import use_camera
    except Exception:
        from use_camera import use_camera  # type: ignore

    # FAST PATH: prime the dashboard camera proxy. The dashboard holds the USB
    # camera open 24/7 and serves pre-grabbed frames over HTTP in ~30ms —
    # versus 4-9s for a cold V4L2/RealSense open (which blows the MHS RPC
    # 5s timeout). We auto-mint the dashboard service JWT from the shared
    # auth store so no manual token wiring is needed. use_camera reads
    # NEON_CAMERA_PROXY / NEON_CAMERA_PROXY_TOKEN and prefers the proxy for
    # color-only captures, falling back to direct device if the proxy is down.
    _prime_camera_proxy()

    # Grab a color frame via the unified camera tool (handles RS/Brio/proxy).
    # NOTE: default source="logitech" + low warmup keeps this FAST (< 5s) so it
    # completes within the MHS RPC timeout. source="auto" probes RealSense first
    # (pyrealsense2 warmup + 5s timeouts) which can push total latency past 8s
    # and time out the fleet caller. Use source="realsense"/"auto" only when you
    # explicitly need depth-capable head cam and can tolerate the extra latency.
    r = use_camera(action="capture", source=source, width=width,
                   height=height, quality=quality, warmup=warmup)
    if r.get("status") != "success":
        return {"status": "error", "stage": "capture",
                "message": r.get("message", "capture failed"),
                "meta": r.get("meta")}

    # Pull the JPEG bytes out of the inline image content block.
    jpeg = None
    caption = ""
    for blk in (r.get("content") or []):
        if "image" in blk:
            jpeg = blk["image"]["source"]["bytes"]
        elif "text" in blk and not caption:
            caption = blk["text"]
    if not jpeg:
        return {"status": "error", "stage": "encode",
                "message": "no image bytes in capture result",
                "meta": r.get("meta")}

    meta = r.get("meta") or {}
    csize = meta.get("color_size") or [width, height]
    out: dict = {
        "status": "success",
        "source": meta.get("source", source),
        "backend": meta.get("backend"),
        "width": csize[0],
        "height": csize[1],
        "bytes": len(jpeg),
        "timestamp": r.get("timestamp"),
        "caption": caption,
    }

    if save or save_path:
        from pathlib import Path as _P
        from datetime import datetime as _dt
        sp = save_path or str(
            CACHE_DIR / f"capture_{_dt.now():%Y%m%d_%H%M%S}_{out['source']}.jpg"
        )
        _P(sp).parent.mkdir(parents=True, exist_ok=True)
        _P(sp).write_bytes(jpeg)
        out["path"] = sp

    if include_base64:
        out["mime_type"] = "image/jpeg"
        out["image_b64"] = _b64.b64encode(jpeg).decode("ascii")

    return out
