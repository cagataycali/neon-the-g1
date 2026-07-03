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

try:
    import pyrealsense2 as rs
    _RS = True
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
    """Single RealSense pipeline shared by color + depth cams.

    The D435i cannot be opened by two independent rs.pipeline() instances
    (second fails with 'failed to set power state'). So we open ONE pipeline
    streaming both color and depth, and hand out the latest frame of each
    kind to whichever _Cam asks. Reference-counted start/stop.
    """
    _instance = None
    _lock = __import__("threading").Lock()

    def __init__(self, width=640, height=480, fps=15):
        self.width, self.height, self.fps = width, height, fps
        self._pipe = None
        self._refs = 0
        self._plock = __import__("threading").Lock()
        self._latest_color = None
        self._latest_depth = None
        self._grab_lock = __import__("threading").Lock()

    @classmethod
    def get(cls, width=640, height=480, fps=15):
        with cls._lock:
            if cls._instance is None:
                cls._instance = _SharedRealSense(width, height, fps)
            return cls._instance

    def acquire(self):
        with self._plock:
            if self._pipe is None:
                pipe = rs.pipeline()
                cfg = rs.config()
                cfg.enable_stream(rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps)
                cfg.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
                pipe.start(cfg)
                self._pipe = pipe
                log.info("📷 shared RealSense pipeline started (color+depth)")
            self._refs += 1
            return True

    def release(self):
        with self._plock:
            self._refs -= 1
            if self._refs <= 0 and self._pipe is not None:
                try:
                    self._pipe.stop()
                except Exception:
                    pass
                self._pipe = None
                self._refs = 0
                log.info("📷 shared RealSense pipeline stopped")

    def _pump(self):
        """Grab one frameset and cache color+depth. Thread-safe."""
        with self._grab_lock:
            if self._pipe is None:
                return
            frames = self._pipe.wait_for_frames(timeout_ms=1000)
            cf = frames.get_color_frame()
            df = frames.get_depth_frame()
            if cf:
                self._latest_color = np.asanyarray(cf.get_data())
            if df:
                depth = np.asanyarray(df.get_data())
                clipped = np.clip(depth, 0, _DEPTH_MAX_MM).astype(np.float32)
                d8 = (clipped / _DEPTH_MAX_MM * 255).astype(np.uint8)
                self._latest_depth = cv2.applyColorMap(d8, cv2.COLORMAP_TURBO)

    def read_color(self):
        self._pump()
        return self._latest_color

    def read_depth(self):
        self._pump()
        return self._latest_depth


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
            self._rs_shared = _SharedRealSense.get(self.width, self.height, self.fps)
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
                jpg = _jpeg(frame, self.quality)
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
        return {
            "id": self.id, "kind": self.kind, "backend": self._backend,
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
