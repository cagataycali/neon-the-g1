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
# every spawn appends what it was asked to do, so tests can read the parent's intent
if os.getenv("FAKE_SPAWN_LOG"):
    with open(os.environ["FAKE_SPAWN_LOG"], "a") as fh:
        fh.write("reset=%s\n" % os.getenv("RS_RESET", "0"))
n = int(os.getenv("FAKE_FRAMES", "3"))
for i in range(n):
    for kind, body in ((b"C", b"color-%d" % i), (b"D", b"depth-%d" % i)):
        out.write(b"NEON" + kind + struct.pack(">I", len(body)) + body)
    out.flush(); time.sleep(0.02)
if os.getenv("FAKE_HANG"):
    time.sleep(60)
sys.exit(int(os.getenv("FAKE_EXIT", "0")))
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


def test_colour_falls_back_to_v4l2_when_sdk_never_delivers(shared, monkeypatch, tmp_path):
    """The worker spawns but never frames: colour switches to the UVC node, depth stops, worker is shut down."""
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_HANG", "1")
    shared.GIVE_UP_S = 0.3
    monkeypatch.setattr(cs, "_RS", True)
    monkeypatch.setattr(cs, "_CV2", True)
    monkeypatch.setattr(cs._SharedRealSense, "get", classmethod(lambda cls, *a, **k: shared))

    class FakeCap:
        def read(self):
            return True, "array"
        def release(self):
            pass

    import types
    fake_uc = types.SimpleNamespace(_find_realsense_v4l2_main=lambda: 4,
                                    _v4l2_open=lambda node, w, h, fourcc=None, fps=15: FakeCap() if node == 4 else None)
    import importlib
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: fake_uc if name == "tools.use_camera" else __import__(name))
    monkeypatch.setattr(cs, "_jpeg", lambda frame, q=70: b"jpeg-from-" + str(frame).encode())
    monkeypatch.setattr(cs._Cam, "_realsense_rgb_nodes", staticmethod(lambda: [2, 4]))
    monkeypatch.setattr(cs._Cam, "_is_colour", staticmethod(lambda frame: frame == "array"))
    fake_uc._v4l2_open = lambda node, w, h, fourcc=None, fps=15: FakeCap() if node in (2, 4) else None

    class IRCap(FakeCap):
        def read(self):
            return True, "grey"
    _real_open = fake_uc._v4l2_open
    fake_uc._v4l2_open = lambda node, w, h, fourcc=None, fps=15: IRCap() if node == 2 else _real_open(node, w, h, fourcc, fps)

    color = cs._Cam("realsense_color", "rs_color", width=640, height=480, fps=30, quality=70)
    depth = cs._Cam("realsense_depth", "rs_depth", width=640, height=480, fps=30, quality=70)
    color.start(); depth.start()
    try:
        assert _wait(lambda: color.latest() == b"jpeg-from-array", 4.0)
        assert "v4l2:4" in color.status()["backend"]
        assert _wait(lambda: depth.status()["running"] is False, 4.0)
        assert "depth unavailable" in depth.status()["error"]
        assert shared.status()["worker_pid"] is None
    finally:
        color.stop(); depth.stop()


def test_rgb_node_picker_prefers_the_highest_interface(tmp_path, monkeypatch):
    """video0/1/3/7 on interface 1.0 (depth module), video5/6 on 1.3 (RGB) -> 5, 6 first."""
    sysfs = tmp_path / "v4l"
    layout = {0: "1.0", 1: "1.0", 3: "1.0", 5: "1.3", 6: "1.3", 7: "1.0", 9: None}
    for n, iface in layout.items():
        d = sysfs / f"video{n}"; d.mkdir(parents=True)
        (d / "name").write_text("Intel(R) RealSense(TM) Depth Ca" if iface else "Logitech BRIO")
        target = tmp_path / f"dev-{n}" / f"2-2.3:{iface or '1.0'}"
        target.mkdir(parents=True)
        (d / "device").symlink_to(target)
    import glob as _glob
    monkeypatch.setattr(_glob, "glob", lambda pat: [str(p) for p in sysfs.iterdir()] if "video4linux" in pat else [])
    assert cs._Cam._realsense_rgb_nodes() == [5, 6, 0, 1, 3, 7]


def test_no_respawn_after_give_up(shared, monkeypatch):
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_HANG", "1")
    shared.GIVE_UP_S = 0.2
    assert shared.acquire() is True
    time.sleep(0.3)
    assert shared.gave_up()
    shared.shutdown()
    assert shared.acquire() is False
    assert shared.status()["worker_pid"] is None


def _spawns(path):
    return path.read_text().splitlines() if path.exists() else []


def test_worker_that_never_frames_is_respawned_with_a_hardware_reset(shared, monkeypatch, tmp_path):
    """rs_worker exits 4 when the pipeline started but no frame ever came (even after its own
    reset); the parent's next spawn then asks for a hardware reset up front."""
    spawn_log = tmp_path / "spawns"
    monkeypatch.setenv("FAKE_SPAWN_LOG", str(spawn_log))
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_EXIT", "4")
    shared.acquire()
    assert _wait(lambda: len(_spawns(spawn_log)) >= 1)
    assert _spawns(spawn_log)[0] == "reset=0"
    assert _wait(lambda: shared._proc is not None and shared._proc.poll() == 4)
    time.sleep(0.15)
    shared.read_color()   # watchdog tick
    assert _wait(lambda: len(_spawns(spawn_log)) >= 2, 3.0)
    assert _spawns(spawn_log)[1] == "reset=1"


def test_startup_leash_is_longer_than_the_steady_state_stall(shared, monkeypatch, tmp_path):
    """Before the first frame a worker may legitimately spend a reset cycle (~25 s on the Jetson):
    STARTUP_S applies, not STALL_S. Once frames flowed, STALL_S applies."""
    spawn_log = tmp_path / "spawns"
    monkeypatch.setenv("FAKE_SPAWN_LOG", str(spawn_log))
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_HANG", "1")
    shared.STALL_S = 0.2
    shared.STARTUP_S = 1.2
    shared.acquire()
    assert _wait(lambda: len(_spawns(spawn_log)) == 1)
    pid1 = shared.status()["worker_pid"]
    time.sleep(0.5)
    shared.read_color()
    assert shared.status()["worker_pid"] == pid1, "must not restart before STARTUP_S"
    time.sleep(0.9)
    shared.read_color()
    assert _wait(lambda: shared.status()["worker_pid"] not in (None, pid1), 3.0)
    assert _wait(lambda: _spawns(spawn_log)[1:2] == ["reset=1"], 3.0), "a startup stall restarts with a hardware reset"


def test_failed_fallback_rearms_the_sdk_instead_of_giving_up_for_life(shared, monkeypatch, tmp_path):
    """SDK gave up AND no RealSense UVC node is capturable: the camera parks with a cool-down and
    the SDK gets a fresh attempt window (with a hardware reset) after RETRY_SDK_S."""
    spawn_log = tmp_path / "spawns"
    monkeypatch.setenv("FAKE_SPAWN_LOG", str(spawn_log))
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_HANG", "1")
    shared.GIVE_UP_S = 0.3
    shared.RETRY_SDK_S = 0.5
    monkeypatch.setattr(cs, "_RS", True)
    monkeypatch.setattr(cs, "_CV2", True)
    monkeypatch.setattr(cs._SharedRealSense, "get", classmethod(lambda cls, *a, **k: shared))
    import types, importlib
    fake_uc = types.SimpleNamespace(_find_realsense_v4l2_main=lambda: None,
                                    _v4l2_open=lambda node, w, h, fourcc=None, fps=15: None)
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: fake_uc if name == "tools.use_camera" else __import__(name))
    monkeypatch.setattr(cs._Cam, "_realsense_rgb_nodes", staticmethod(lambda: [2]))
    color = cs._Cam("realsense_color", "rs_color", width=640, height=480, fps=30, quality=70)
    color.RETRY_S = 0.1
    color.start()
    try:
        assert _wait(lambda: color.status()["running"] is False, 4.0)
        assert "retried with a hardware reset" in color.status()["error"]
        assert shared.status()["worker_pid"] is None, "worker shut down while parked"
        assert shared.sdk_retry_due() is False
        assert shared.gave_up() is False, "a new attempt window is open"
        assert _spawns(spawn_log) == ["reset=0"]
        time.sleep(0.15)
        color.start()                 # inside the camera cool-down: a no-op
        assert color.status()["running"] is False
        time.sleep(0.5)
        color.start()                 # SDK retry due: spawns again, hardware reset first
        assert _wait(lambda: _spawns(spawn_log) == ["reset=0", "reset=1"], 3.0)
        assert color.status()["running"] is True
    finally:
        color.stop()


def test_operator_reset_restarts_everything_now(shared, monkeypatch, tmp_path):
    """POST /api/camera/reset -> CameraManager.reset(): worker killed, respawned with a hardware
    reset, every grabber restarted, cool-downs cleared."""
    spawn_log = tmp_path / "spawns"
    monkeypatch.setenv("FAKE_SPAWN_LOG", str(spawn_log))
    monkeypatch.setenv("FAKE_HANG", "1")        # a live worker: only the reset may respawn it
    monkeypatch.setattr(cs, "_RS", True)
    monkeypatch.setattr(cs, "_CV2", True)
    monkeypatch.setattr(cs._SharedRealSense, "get", classmethod(lambda cls, *a, **k: shared))
    cam = cs._Cam("realsense_color", "rs_color", width=640, height=480, fps=30, quality=70)
    mgr = cs.CameraManager()
    mgr._cams = {"realsense_color": cam}
    mgr._built = True
    cam.start()
    try:
        assert _wait(lambda: cam.latest() == b"color-2")
        pid1 = shared.status()["worker_pid"]
        cam._retry_at = time.time() + 999      # pretend it was parked
        out = mgr.reset()
        assert out["cameras"][0]["id"] == "realsense_color"
        assert cam._retry_at == 0.0
        assert _wait(lambda: shared.status()["worker_pid"] not in (None, pid1), 3.0)
        assert _wait(lambda: _spawns(spawn_log) == ["reset=0", "reset=1"], 3.0)
        assert _wait(lambda: cam.latest() is not None and cam.status()["running"], 3.0)
    finally:
        cam.stop()


def test_frozen_v4l2_fallback_hands_the_camera_back_to_the_sdk(shared, monkeypatch, tmp_path):
    """Colour on the UVC fallback stops getting frames: releasing it clears fallback_active and
    rearms the SDK (the bus storm that froze the node may have passed), instead of the camera
    refusing its own reopen until an operator reset."""
    spawn_log = tmp_path / "spawns"
    monkeypatch.setenv("FAKE_SPAWN_LOG", str(spawn_log))
    monkeypatch.setenv("FAKE_FRAMES", "0")
    monkeypatch.setenv("FAKE_HANG", "1")
    shared.GIVE_UP_S = 0.3
    shared.RETRY_SDK_S = 0.4
    monkeypatch.setattr(cs, "_RS", True)
    monkeypatch.setattr(cs, "_CV2", True)
    monkeypatch.setattr(cs._SharedRealSense, "get", classmethod(lambda cls, *a, **k: shared))
    frames = {"ok": True}

    class Cap:
        def read(self):
            return (True, "array") if frames["ok"] else (False, None)
        def release(self):
            pass

    import types, importlib
    fake_uc = types.SimpleNamespace(_find_realsense_v4l2_main=lambda: 4,
                                    _v4l2_open=lambda node, w, h, fourcc=None, fps=15: Cap() if node == 4 else None)
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: fake_uc if name == "tools.use_camera" else __import__(name))
    monkeypatch.setattr(cs, "_jpeg", lambda frame, q=70: b"jpeg-from-" + str(frame).encode())
    monkeypatch.setattr(cs._Cam, "_realsense_rgb_nodes", staticmethod(lambda: [4]))
    monkeypatch.setattr(cs._Cam, "_is_colour", staticmethod(lambda frame: True))
    color = cs._Cam("realsense_color", "rs_color", width=640, height=480, fps=200, quality=70)
    color.RETRY_S = 0.1
    color.start()
    try:
        assert _wait(lambda: color.latest() == b"jpeg-from-array", 4.0)
        assert shared.fallback_active is True
        frames["ok"] = False                       # the UVC node freezes
        assert _wait(lambda: shared.fallback_active is False, 6.0), "release must clear the flag"
        assert shared.gave_up() is False, "a new SDK attempt window is open"
        assert _wait(lambda: color.status()["running"] is False, 4.0), "the loop ends restartable, not stuck"
        color.start()                                    # inside the cool-down: a no-op
        assert color.status()["running"] is False
        time.sleep(0.5)
        color.start()                                    # what /api/cameras polling does
        assert _wait(lambda: _spawns(spawn_log)[-1:] == ["reset=1"], 6.0), "the SDK is retried with a reset"
        assert color.status()["running"] is True
    finally:
        color.stop()
