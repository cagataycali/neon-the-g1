"""Vision tool — capture a camera frame and show it to the model.

In a voice session the JPEG goes straight into the realtime stream as an
ImageBlock (strands.bidi, 1.58+), so the model sees it natively in its own
multimodal context (no separate vision-API round-trip):

    user speech ──┐
                  ▼
                bidi model (gpt-realtime-2 / Gemini Live / Nova Sonic) ──► spoken reply
                  ▲       ▲
                  │       │
                audio   take_photo() sends one list:
                          • ImageBlock(format="jpeg", source={"bytes": ...})
                          • optional TextBlock (the question)

Every strands.bidi provider dispatches ImageBlock itself (OpenAI as an
`input_image` content block, Gemini as inline data), so the import-time
monkey-patch the experimental package needed is gone. In every other persona
(dashboard chat, telegram, thinker) the JPEG comes back as a tool-result
image block.

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
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from strands import tool
from strands.types.content import TextBlock
from strands.types.media import ImageBlock

CACHE_DIR = Path(tempfile.gettempdir()) / "lookout_vision"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


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
        tok = service_token("voice")
        try:
            from tools.camera_ready import _token_sane
        except Exception:
            from camera_ready import _token_sane  # type: ignore
        return tok if tok and _token_sane(tok) else None
    except Exception:
        return None


def _capture_frame_in_process(output: Path = None, cam: str = None) -> Optional[Path]:
    """When THIS process is the dashboard (chat agent), the camera manager is a
    module away: read its latest JPEG directly. An HTTPS round trip to
    ourselves cannot work from a tool that runs on the server's own event
    loop (the server never gets to answer: TLS handshake timed out, 2026-10-07)."""
    mgr = None
    for modname in ("docs.dashboard.camera_stream", "camera_stream"):
        mod = sys.modules.get(modname)
        if mod is not None and getattr(mod, "_MGR", None) is not None:
            mgr = mod._MGR
            break
    if mgr is None:
        return None
    order = [cam] if cam else [_DASH_CAM] + [c for c in ("realsense_color", "brio") if c != _DASH_CAM]
    for c in order:
        try:
            grabber = mgr.get(c)
        except Exception:
            grabber = None
        if grabber is None:
            continue
        data = grabber.latest()
        if data and len(data) >= 2000 and data[:2] == b"\xff\xd8":
            out = output or (CACHE_DIR / f"frame_{int(time.time())}.jpg")
            out.write_bytes(data)
            return out
    raise RuntimeError(f"dashboard cameras hold no frame right now ({order})")


def _capture_frame_dashboard(output: Path = None, cam: str = None) -> Path:
    """Grab a JPEG from the dashboard's shared camera snapshot endpoint.

    With no explicit camera, try the configured one (NEON_DASHBOARD_CAM) and
    then the other head camera, so a process that was not handed the env var
    does not fail on a camera with no signal.
    """
    local = _capture_frame_in_process(output=output, cam=cam)
    if local is not None:
        return local
    if cam is None:
        order = [_DASH_CAM] + [c for c in ("realsense_color", "brio") if c != _DASH_CAM]
        last: Exception | None = None
        for c in order:
            try:
                return _capture_frame_dashboard(output=output, cam=c)
            except Exception as e:  # noqa: PERF203 - two cameras, two tries
                last = e
        raise RuntimeError(f"no dashboard camera delivered a frame ({order}): {last}")
    output = output or (CACHE_DIR / f"frame_{int(time.time())}.jpg")
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
            try:
                return _capture_frame_dashboard(cam="brio" if device else None)
            except Exception as _e:
                # dashboard down / camera offline â fall through to raw device
                print(f"[vision] dashboard snapshot failed ({_e}); trying raw device")
        return _capture_frame_linux(device=device)
    raise NotImplementedError(f"vision capture not implemented on {sysname}")


# ── @tool exposed to the bidi voice agent ─────────────────────────────
def _image_message(jpeg: bytes, question: str = "") -> list:
    """The content blocks take_photo sends to a BidiAgent: the JPEG, then the
    question when there is one. One list = one user message for the model."""
    blocks: list = [ImageBlock(format="jpeg", source={"bytes": jpeg})]
    if question.strip():
        blocks.append(TextBlock(text=question.strip()))
    return blocks


@tool(context=True)
async def take_photo(
    tool_context,
    question: str = "",
    device: int = 0,
) -> dict:
    """Capture a frame from the head camera and show it to the model.

    In a voice session the JPEG is injected into the realtime stream and the
    model replies in audio; in every other persona (dashboard chat, shell,
    telegram, thinker) the JPEG comes back as a tool-result image block.

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
    bidi = agent is not None and hasattr(agent, "send")

    try:
        # Off the event loop: the capture blocks on HTTP/USB, and in the
        # dashboard persona this coroutine runs on the server's own loop.
        image_path = await asyncio.to_thread(_capture_frame, device)
    except Exception as e:
        return {"status": "error", "stage": "capture", "message": str(e)}

    if not bidi:
        # Not a voice session (dashboard chat, shell REPL, telegram, thinker):
        # there is no realtime stream to inject into, so hand the frame back as
        # a tool-result image block the text model sees natively (same shape as
        # tools/use_camera.py). The model then answers the question itself.
        jpeg = image_path.read_bytes()
        caption = f"photo {image_path.stem} from the head camera ({len(jpeg)} bytes)"
        if question.strip():
            caption += f"; question: {question.strip()}"
        return {
            "status": "success",
            "content": [
                {"text": caption},
                {"image": {"format": "jpeg", "source": {"bytes": jpeg}}},
            ],
        }

    # Send the image (and the question) into the multimodal stream as ONE user
    # message; the provider turns the ImageBlock into its own wire format.
    try:
        await agent.send(_image_message(image_path.read_bytes(), question))
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
        "note": "Image sent into the voice stream. Model will respond in audio.",
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
