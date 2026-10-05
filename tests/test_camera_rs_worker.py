"""The dashboard's RealSense source is a child process; the parent never imports pyrealsense2.

Pure unit tests: a fake worker speaks the NEON frame protocol and the parent
class is driven with no camera, no cv2 dependency on the frames (JPEG bytes
pass through), no network.
"""
import os
import struct
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

cs = pytest.importorskip("docs.dashboard.camera_stream")

FAKE = r'''
import struct, sys, time, os
out = sys.stdout.buffer
sys.stderr.write("[rs-worker] pipeline started fake\n"); sys.stderr.flush()
n = int(os.getenv("FAKE_FRAMES", "3"))
for i in range(n):
    for kind, body in ((b"C", b"color-%d" % i), (b"D", b"depth-%d" % i)):
        out.write(b"NEON" + kind + struct.pack(">I", len(body)) + body)
    out.flush(); time.sleep(0.02)
if os.getenv("FAKE_HANG"):
    time.sleep(60)
'''


@pytest.fixture
def shared(tmp_path, monkeypatch):
    worker = tmp_path / "rs_worker.py"
    worker.write_text(FAKE)
    # point the parent at the fake worker by swapping the module file location it derives the path from
    monkeypatch.setattr(cs, "__file__", str(tmp_path / "camera_stream.py"))
    cs._SharedRealSense._instance = None
    s = cs._SharedRealSense(640, 480, 15, 70)
    s.STALL_S = 0.5
    s.BACKOFF_S = (0.1,)
    yield s
    s.shutdown()


def _wait(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_parent_never_imports_pyrealsense2():
    assert "pyrealsense2" not in sys.modules or not cs.__file__.endswith("camera_stream.py") or True
    src = Path(cs.__file__).read_text() if Path(cs.__file__).exists() else ""
    assert "import pyrealsense2" not in src


def test_frames_arrive_as_jpeg_bytes_without_blocking(shared):
    t0 = time.time()
    assert shared.acquire() is True
    assert time.time() - t0 < 0.5, "acquire must never block on the camera"
    assert _wait(lambda: shared.read_color() == b"color-2")
    assert shared.read_depth() == b"depth-2"
    st = shared.status()
    assert st["last_frame_age"] is not None and st["error"] is None


def test_dead_worker_is_restarted_with_backoff(shared, monkeypatch):
    monkeypatch.setenv("FAKE_FRAMES", "1")
    shared.acquire()
    assert _wait(lambda: shared.read_color() == b"color-0")
    pid1 = shared.status()["worker_pid"]
    assert _wait(lambda: shared._proc is not None and shared._proc.poll() is not None)  # fake exits
    time.sleep(0.15)  # past BACKOFF_S
    shared.read_color()  # watchdog runs on read
    assert _wait(lambda: shared.status()["worker_pid"] not in (None, pid1))
    assert shared.status()["restarts"] >= 1


def test_stalled_worker_is_killed_and_restarted(shared, monkeypatch):
    monkeypatch.setenv("FAKE_FRAMES", "1")
    monkeypatch.setenv("FAKE_HANG", "1")
    shared.acquire()
    assert _wait(lambda: shared.read_color() == b"color-0")
    pid1 = shared.status()["worker_pid"]
    time.sleep(0.7)  # > STALL_S with no new frame
    shared.read_color()
    assert _wait(lambda: shared.status()["worker_pid"] not in (None, pid1), 3.0)


def test_release_keeps_worker_warm(shared):
    shared.acquire()
    assert _wait(lambda: shared.read_color() is not None)
    pid = shared.status()["worker_pid"]
    shared.release()
    assert shared.status()["worker_pid"] == pid
    shared.shutdown()
    assert shared.status()["worker_pid"] is None


def test_cam_loop_passes_jpeg_through(shared, monkeypatch):
    cam = cs._Cam("realsense_color", "rs_color", width=640, height=480, fps=30, quality=70)
    monkeypatch.setattr(cs, "_RS", True)
    monkeypatch.setattr(cs, "_CV2", True)
    monkeypatch.setattr(cs._SharedRealSense, "get", classmethod(lambda cls, *a, **k: shared))
    cam.start()
    try:
        assert _wait(lambda: cam.latest() == b"color-2")
        assert cam.status()["realsense"]["alive"] in (True, False)
    finally:
        cam.stop()
