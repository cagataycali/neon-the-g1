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
