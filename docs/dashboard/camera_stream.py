"""
📷 Multi-camera MJPEG streamer for the NEON dashboard.

The G1 has NO DDS video topic — cameras are USB. This manages independent
background grabbers for each available camera and serves them as MJPEG +
snapshot. Each camera holds ONE device open (V4L2 / RealSense pipeline) and
all HTTP clients share its latest frame.

Cameras (auto-detected, lazy-started on first request):
  • realsense_color : RealSense D435i color (pyrealsense2, falls back to V4L2)
  • realsense_depth : RealSense D435i colorized depth heatmap (pyrealsense2 only)
  • brio            : Logitech Brio 4K (V4L2)

API (per camera id):
  /api/camera/{id}/stream    → multipart MJPEG
  /api/camera/{id}/snapshot  → single JPEG
  /api/cameras               → list available cameras + status
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Dict, List, Optional

log = logging.getLogger("neon-dashboard.camera")

try:
    import cv2
    import numpy as np
    _CV2 = True
except Exception:
    _CV2 = False

# pyrealsense2 is NEVER imported in this process: see rs_worker.py. Its
# device enumeration holds the GIL while blocking in the kernel's USB hub lock,
# which froze the whole dashboard when a USB port was flapping (2026-10-05).
# _RS only says whether the worker can run.
try:
    import importlib.util as _ilu
    import os as _os_rs
    if _os_rs.getenv("NEON_NO_REALSENSE", "").lower() in ("1", "true", "yes"):
        raise ImportError("RealSense disabled via NEON_NO_REALSENSE")
    _RS = _ilu.find_spec("pyrealsense2") is not None
except Exception:
    _RS = False

_encode_jpeg = None
try:
    from tools.use_camera import _encode_jpeg as _ej
    _encode_jpeg = _ej
except Exception as e:  # pragma: no cover
    log.warning(f"use_camera._encode_jpeg unavailable: {e}")


def _jpeg(frame, quality=70) -> Optional[bytes]:
    if _encode_jpeg:
        return _encode_jpeg(frame, quality=quality)
    if _CV2:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None
    return None


_DEPTH_MAX_MM = 4000


class _SharedRealSense:
    """Single RealSense source shared by the colour + depth cams.

    The D435i cannot be opened by two independent rs.pipeline() instances
    (second fails with 'failed to set power state'), so ONE source streams
    both and hands the latest JPEG of each kind to whichever _Cam asks.

    The pipeline lives in a child process (rs_worker.py) so that libusb /
    pybind11 stalls can never hold this process's GIL. The reader thread
    parses frames off the child's stdout; a watchdog restarts the child when
    it exits or stops producing (with back-off), and ``acquire`` never blocks.
    """
    _instance = None
    _lock = threading.Lock()

    STALL_S = 12.0          # no frame for this long -> restart the worker
    BACKOFF_S = (2, 5, 10, 20, 30)

    def __init__(self, width=640, height=480, fps=15, quality=70):
        self.width, self.height, self.fps, self.quality = width, height, fps, quality
        self._plock = threading.Lock()
        self._proc = None
        self._reader = None
        self._refs = 0
        self._latest_color: Optional[bytes] = None
        self._latest_depth: Optional[bytes] = None
        self._last_frame = 0.0
        self._started_at = 0.0
        self._restarts = 0
        self.error: Optional[str] = None

    @classmethod
    def get(cls, width=640, height=480, fps=15, quality=70):
        with cls._lock:
            if cls._instance is None:
                cls._instance = _SharedRealSense(width, height, fps, quality)
            return cls._instance

    # -- lifecycle -----------------------------------------------------------
    def _spawn(self) -> None:
        """Start the worker (non-blocking). Caller holds _plock."""
        import subprocess
        env = dict(os.environ, RS_WIDTH=str(self.width), RS_HEIGHT=str(self.height),
                   RS_FPS=str(self.fps), RS_JPEG_Q=str(self.quality))
        worker = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rs_worker.py")
        self._proc = subprocess.Popen([sys.executable, worker], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, env=env, bufsize=0)
        self._started_at = time.time()
        self._last_frame = 0.0
        self._reader = threading.Thread(target=self._read_loop, args=(self._proc,),
                                        daemon=True, name="rs-reader")
        self._reader.start()
        threading.Thread(target=self._stderr_loop, args=(self._proc,), daemon=True,
                         name="rs-stderr").start()
        log.info(f"shared RealSense worker started (pid {self._proc.pid})")

    def acquire(self) -> bool:
        with self._plock:
            self._refs += 1
            if self._proc is None or self._proc.poll() is not None:
                self._spawn()
            return True

    def release(self) -> None:
        # The dashboard is the single RealSense owner; keep the worker warm so
        # a camera re-open (fail counter in _Cam) never churns the pipeline.
        with self._plock:
            self._refs = max(0, self._refs - 1)

    def shutdown(self) -> None:
        with self._plock:
            self._kill()

    def _kill(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.kill()
            p.wait(timeout=3)
        except Exception:
            pass

    # -- child I/O -----------------------------------------------------------
    def _stderr_loop(self, proc) -> None:
        try:
            for line in iter(proc.stderr.readline, b""):
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    log.info(text)
                    if "failed" in text or "no frames" in text:
                        self.error = text
        except Exception:
            pass

    def _read_loop(self, proc) -> None:
        import struct
        f = proc.stdout
        try:
            while True:
                head = f.read(9)
                if len(head) < 9:
                    break
                if head[:4] != b"NEON":
                    # resync on garbage
                    continue
                kind, n = head[4:5], struct.unpack(">I", head[5:9])[0]
                buf = bytearray()
                while len(buf) < n:
                    chunk = f.read(n - len(buf))
                    if not chunk:
                        return
                    buf.extend(chunk)
                data = bytes(buf)
                if kind == b"C":
                    self._latest_color = data
                elif kind == b"D":
                    self._latest_depth = data
                self._last_frame = time.time()
                self.error = None
        except Exception as e:
            log.debug(f"rs reader: {e}")
        finally:
            rc = proc.poll()
            log.warning(f"shared RealSense worker ended (rc={rc})")

    def _watchdog(self) -> None:
        """Called on every read: restart a dead or stalled worker (bounded back-off)."""
        with self._plock:
            if self._refs <= 0:
                return
            p = self._proc
            now = time.time()
            dead = p is None or p.poll() is not None
            ref = self._last_frame or self._started_at
            stalled = (not dead) and ref and (now - ref) > self.STALL_S
            if not (dead or stalled):
                return
            wait = self.BACKOFF_S[min(self._restarts, len(self.BACKOFF_S) - 1)]
            if now - self._started_at < wait:
                return
            if stalled:
                log.warning(f"shared RealSense worker stalled {now - ref:.0f}s, restarting")
                self._kill()
            self._restarts += 1
            if self._last_frame:
                self._restarts = 1       # it worked once: short back-off
            self._spawn()

    # -- reads (never block) -------------------------------------------------
    def read_color(self) -> Optional[bytes]:
        self._watchdog()
        return self._latest_color

    def read_depth(self) -> Optional[bytes]:
        self._watchdog()
        return self._latest_depth

    def status(self) -> dict:
        p = self._proc
        return {"worker_pid": p.pid if p else None, "alive": bool(p and p.poll() is None),
                "restarts": self._restarts, "error": self.error,
                "last_frame_age": round(time.time() - self._last_frame, 2) if self._last_frame else None}


class _Cam:
    """One camera grabber. kind ∈ {v4l2, rs_color, rs_depth}."""

    def __init__(self, cam_id: str, kind: str, *, v4l2_node: int = -1,
                 width=1280, height=720, fps=15, quality=70):
        self.id = cam_id
        self.kind = kind
        self.v4l2_node = v4l2_node
        self.width, self.height, self.fps, self.quality = width, height, fps, quality
        self._jpeg: Optional[bytes] = None
        self._ts = 0.0
        self._frames = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._error: Optional[str] = None
        self._cap = None
        self._rs_pipe = None
        self._rs_shared = None
        self._backend = None

    def start(self):
        if self._running or not _CV2:
            if not _CV2:
                self._error = "cv2 missing"
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"cam-{self.id}")
        self._thread.start()

    def stop(self):
        self._running = False

    def _open(self) -> bool:
        import importlib
        uc = importlib.import_module("tools.use_camera")
        if self.kind == "v4l2":
            node = self.v4l2_node
            cap = uc._v4l2_open(node, self.width, self.height, fourcc="MJPG", fps=self.fps)
            if cap is None:
                cap = uc._v4l2_open(node, self.width, self.height, fps=self.fps)
            if cap is not None:
                ok, _ = cap.read()
                if ok:
                    self._cap = cap
                    self._backend = f"v4l2:{node}"
                    return True
                cap.release()
            self._error = f"v4l2 node {node} unavailable"
            return False
        # RealSense kinds
        if not _RS:
            self._error = "pyrealsense2 missing"
            return False
        try:
            self._rs_shared = _SharedRealSense.get(self.width, self.height, self.fps, self.quality)
            self._rs_shared.acquire()
            self._rs_pipe = True  # marker: using shared pipeline
            self._backend = f"realsense:{self.kind}"
            return True
        except Exception as e:
            self._error = f"realsense: {e}"
            return False

    def _read(self):
        if self._cap is not None:
            ok, frame = self._cap.read()
            return frame if ok else None
        if self._rs_pipe is not None and getattr(self, "_rs_shared", None) is not None:
            if self.kind == "rs_depth":
                return self._rs_shared.read_depth()
            return self._rs_shared.read_color()
        return None

    def _release(self):
        try:
            if self._cap is not None:
                self._cap.release()
        except Exception:
            pass
        self._cap = None
        try:
            if self._rs_shared is not None:
                self._rs_shared.release()
        except Exception:
            pass
        self._rs_pipe = None
        self._rs_shared = None

    def _loop(self):
        if not self._open():
            self._running = False
            log.warning(f"📷 {self.id} open failed: {self._error}")
            return
        log.info(f"📷 {self.id} opened ({self._backend})")
        period = 1.0 / max(self.fps, 1)
        fails = 0
        while self._running:
            t0 = time.time()
            try:
                frame = self._read()
                if frame is None:
                    fails += 1
                    if fails > 30:
                        self._release()
                        if not self._open():
                            break
                        fails = 0
                    time.sleep(0.05)
                    continue
                fails = 0
                # the RealSense worker hands out JPEG already; v4l2 gives arrays
                jpg = bytes(frame) if isinstance(frame, (bytes, bytearray)) else _jpeg(frame, self.quality)
                if self._rs_shared is not None and jpg is not None and jpg == self._jpeg:
                    time.sleep(0.02)   # same frame as last time: nothing new yet
                    continue
                if jpg:
                    with self._lock:
                        self._jpeg = jpg
                        self._ts = time.time()
                        self._frames += 1
            except Exception as e:
                log.debug(f"📷 {self.id} grab: {e}")
                time.sleep(0.1)
            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)
        self._release()

    def latest(self) -> Optional[bytes]:
        with self._lock:
            return self._jpeg

    def status(self) -> dict:
        rs_st = self._rs_shared.status() if self._rs_shared is not None else None
        return {
            "id": self.id, "kind": self.kind, "backend": self._backend, "realsense": rs_st,
            "running": self._running, "frames": self._frames,
            "resolution": [self.width, self.height], "fps": self.fps,
            "last_frame_age": round(time.time() - self._ts, 2) if self._ts else None,
            "error": self._error,
        }


class CameraManager:
    """Owns all camera grabbers; lazy-starts on first request."""

    def __init__(self):
        self._cams: Dict[str, _Cam] = {}
        self._built = False
        self._lock = threading.Lock()

    def _build(self):
        if self._built:
            return
        with self._lock:
            if self._built:
                return
            import importlib, os
            uc = importlib.import_module("tools.use_camera")
            # Discover Logitech Brio node — but ONLY trust it if a genuine
            # Logitech by-id symlink exists (avoids false-positives that
            # grab a RealSense node when the Brio isn't plugged in).
            brio = None
            try:
                import glob as _glob
                has_logi = bool(_glob.glob("/dev/v4l/by-id/usb-046d_Logi*"))
                if has_logi:
                    brio = uc._find_logitech_main()
                else:
                    log.info("📷 no Logitech by-id symlink — Brio not connected, skipping")
            except Exception:
                brio = None
            q = int(os.getenv("DASHBOARD_CAM_Q", "70"))
            fps = int(os.getenv("DASHBOARD_CAM_FPS", "15"))
            # RealSense color + depth (pyrealsense2)
            if _RS:
                self._cams["realsense_color"] = _Cam(
                    "realsense_color", "rs_color", width=640, height=480, fps=fps, quality=q)
                self._cams["realsense_depth"] = _Cam(
                    "realsense_depth", "rs_depth", width=640, height=480, fps=fps, quality=q)
            else:
                # RealSense color via V4L2 — auto-detect the node (numbers shift
                # on replug/reboot). Override with DASHBOARD_RS_V4L2 if needed.
                env_node = os.getenv("DASHBOARD_RS_V4L2")
                if env_node is not None:
                    rs_node = int(env_node)
                else:
                    try:
                        rs_node = uc._find_realsense_v4l2_main()
                    except Exception:
                        rs_node = None
                if rs_node is not None:
                    self._cams["realsense_color"] = _Cam(
                        "realsense_color", "v4l2", v4l2_node=rs_node,
                        width=1280, height=720, fps=fps, quality=q)
                    log.info(f"📷 realsense_color via V4L2 node {rs_node} (auto-detected)")
                else:
                    log.warning("📷 no RealSense V4L2 node found — realsense_color disabled")
            # Brio
            if brio is not None:
                self._cams["brio"] = _Cam(
                    "brio", "v4l2", v4l2_node=brio, width=1280, height=720,
                    fps=fps, quality=q)
            self._built = True
            log.info(f"📷 cameras: {list(self._cams.keys())} (RS={_RS}, brio={brio})")

    def get(self, cam_id: str) -> Optional[_Cam]:
        self._build()
        cam = self._cams.get(cam_id)
        if cam and not cam._running:
            cam.start()
        return cam

    def list(self) -> List[dict]:
        self._build()
        # lazy-start all so status reflects reality
        for c in self._cams.values():
            if not c._running:
                c.start()
        return [c.status() for c in self._cams.values()]

    def start_all(self):
        self._build()
        for c in self._cams.values():
            c.start()


_MGR: Optional[CameraManager] = None


def get_manager() -> CameraManager:
    global _MGR
    if _MGR is None:
        _MGR = CameraManager()
    return _MGR
