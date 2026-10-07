"""take_photo from the dashboard chat agent: the tool runs INSIDE the dashboard process.

2026-10-07: the agent answered "NEON can't see right now" while the cockpit showed
a live stream. take_photo is an async tool; it made a blocking HTTPS request to
https://localhost:8080 — the very uvicorn loop it was running on — so the server
could never answer itself (TLS handshake timed out after 5 s), and the "raw
device" fallback failed because the dashboard owns the RealSense.

Two guarantees pinned here:
  1. inside the dashboard process the frame comes straight from the camera
     manager (no network at all);
  2. the capture never runs on the event loop (asyncio.to_thread).
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("strands")
# tools/__init__ pulls g1_speak -> g1_bidi_audio, which imports pyaudio; not needed here.
if "pyaudio" not in sys.modules:
    try:
        import pyaudio  # noqa: F401
    except Exception:
        fake = types.ModuleType("pyaudio")
        fake.PyAudio = object
        fake.paInt16 = 8
        sys.modules["pyaudio"] = fake

from tools import vision  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 3000 + b"\xff\xd9"


class _Grabber:
    def __init__(self, data):
        self._data = data

    def latest(self):
        return self._data


class _Mgr:
    def __init__(self, frames):
        self.frames = frames
        self.asked = []

    def get(self, cam_id):
        self.asked.append(cam_id)
        return _Grabber(self.frames.get(cam_id))


@pytest.fixture
def dashboard_process(monkeypatch):
    """Pretend this process is the dashboard: camera_stream is imported and built."""
    mod = types.ModuleType("docs.dashboard.camera_stream")
    mgr = _Mgr({"realsense_color": JPEG, "brio": None})
    mod._MGR = mgr
    monkeypatch.setitem(sys.modules, "docs.dashboard.camera_stream", mod)
    monkeypatch.setattr(vision, "_DASH_CAM", "brio")       # the misconfigured default: brio has no signal

    def no_network(*a, **k):
        raise AssertionError("take_photo must not call the dashboard over HTTP from inside the dashboard")
    monkeypatch.setattr(vision._urlreq, "urlopen", no_network)
    return mgr


def test_inside_the_dashboard_the_frame_comes_from_the_camera_manager(dashboard_process, tmp_path):
    out = vision._capture_frame_dashboard(output=tmp_path / "f.jpg")
    assert out.read_bytes() == JPEG
    # brio (configured) had no frame, so it moved on to the RealSense
    assert dashboard_process.asked == ["brio", "realsense_color"]


def test_outside_the_dashboard_it_still_goes_over_http(monkeypatch):
    monkeypatch.delitem(sys.modules, "docs.dashboard.camera_stream", raising=False)
    monkeypatch.delitem(sys.modules, "camera_stream", raising=False)
    calls = []

    def fake_urlopen(req, timeout=5, context=None):
        calls.append(req.full_url)
        raise OSError("dashboard down")
    monkeypatch.setattr(vision._urlreq, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="no dashboard camera delivered a frame"):
        vision._capture_frame_dashboard()
    assert calls and all("/api/camera/" in u for u in calls)


def test_take_photo_does_not_block_the_event_loop(dashboard_process, monkeypatch):
    """A capture that takes 0.3 s must leave the loop free to run other tasks."""
    import time as _time

    def slow_capture(device=0):
        _time.sleep(0.3)
        return vision._capture_frame_dashboard()
    monkeypatch.setattr(vision, "_capture_frame", slow_capture)
    monkeypatch.setattr(vision, "platform", types.SimpleNamespace(system=lambda: "Linux"))
    fn = getattr(vision.take_photo, "original_function", None) or getattr(vision.take_photo, "_tool_func", None)
    assert fn is not None, "need the undecorated coroutine"

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0.02)
                ticks += 1
        t = asyncio.create_task(ticker())
        result = await fn(tool_context=None, question="what do you see?")
        await t
        return result, ticks

    result, ticks = asyncio.run(scenario())
    assert result["status"] == "success", result
    assert result["content"][1]["image"]["source"]["bytes"] == JPEG
    assert ticks >= 10, f"loop was starved during the capture (ticks={ticks})"


class _VoiceAgent:
    """What take_photo sees inside a strands.bidi session: an agent with .send."""

    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)


def test_in_a_voice_session_the_frame_and_question_go_into_the_stream(dashboard_process, monkeypatch):
    """strands.bidi (1.58+): one user message = [ImageBlock(jpeg), TextBlock(question)].
    The experimental BidiImageInputEvent + OpenAI monkey-patch are gone."""
    from strands.types.content import TextBlock
    from strands.types.media import ImageBlock

    monkeypatch.setattr(vision, "_capture_frame", lambda device=0: vision._capture_frame_dashboard())
    fn = getattr(vision.take_photo, "original_function", None) or getattr(vision.take_photo, "_tool_func", None)
    agent = _VoiceAgent()
    ctx = types.SimpleNamespace(agent=agent)

    result = asyncio.run(fn(tool_context=ctx, question="  who is there?  "))
    assert result["status"] == "success", result
    assert "content" not in result, "a voice session must not also return an image block"
    assert len(agent.sent) == 1, "the image and the question are ONE message"
    blocks = agent.sent[0]
    assert isinstance(blocks, list) and len(blocks) == 2
    assert isinstance(blocks[0], ImageBlock) and blocks[0].format == "jpeg"
    assert blocks[0].source["bytes"] == JPEG
    assert isinstance(blocks[1], TextBlock) and blocks[1].text == "who is there?"

    # no question: the image alone, still a list the agent accepts
    agent.sent.clear()
    asyncio.run(fn(tool_context=ctx, question=""))
    assert len(agent.sent) == 1 and [type(b) for b in agent.sent[0]] == [ImageBlock]
    assert not hasattr(vision, "_patch_openai_image_support")


def test_inside_the_dashboard_no_frame_means_an_error_not_a_raw_device_open(dashboard_process, monkeypatch):
    """When the dashboard holds no frame, take_photo must NOT open pyrealsense2 in-process:
    with a flapping USB bus that enumeration blocks while holding the GIL and froze the
    whole dashboard (2026-10-07 18:38Z). It reports instead."""
    dashboard_process.frames["realsense_color"] = None          # nothing captured yet
    monkeypatch.setattr(vision, "platform", types.SimpleNamespace(system=lambda: "Linux"))

    def never(*a, **k):
        raise AssertionError("raw device capture must not run inside the dashboard")
    monkeypatch.setattr(vision, "_capture_frame_linux", never)
    with pytest.raises(RuntimeError, match="hold no frame"):
        vision._capture_frame(device=0)


def test_outside_the_dashboard_a_failed_snapshot_still_falls_back_to_the_device(monkeypatch):
    monkeypatch.delitem(sys.modules, "docs.dashboard.camera_stream", raising=False)
    monkeypatch.delitem(sys.modules, "camera_stream", raising=False)
    monkeypatch.setattr(vision, "platform", types.SimpleNamespace(system=lambda: "Linux"))
    monkeypatch.setattr(vision, "_capture_frame_dashboard", lambda **k: (_ for _ in ()).throw(OSError("dashboard down")))
    monkeypatch.setattr(vision, "_capture_frame_linux", lambda device=0: Path("/tmp/raw.jpg"))
    assert vision._capture_frame(device=0) == Path("/tmp/raw.jpg")
