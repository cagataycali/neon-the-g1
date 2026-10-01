"""
📡 LiDAR point-cloud WebSocket streamer for the NEON dashboard.

Reuses tools/g1_lidar's DDS subscriber (rt/utlidar/cloud_livox_mid360) and the
_cloud_to_numpy parser, then streams a COMPACT BINARY format to the browser
(7 bytes/point) — the same wire format dashboard uses, decoded by the
React useLidarStream hook:

    per point: x_i16(LE), y_i16(LE), z_i16(LE), intensity_u8
    scale: xyz * 100 → int16 (1 cm resolution)

A background thread polls the latest cloud at ~10 Hz, encodes once, and the
FastAPI WS handler fans the bytes out to all connected clients.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import numpy as np

log = logging.getLogger("neon-dashboard.lidar")

_parse = None
_get_latest_cloud = None
_ensure_subs = None
try:
    from tools.g1_lidar import _cloud_to_numpy as _parse  # noqa
    from tools.g1_lidar import get_latest_cloud as _get_latest_cloud  # noqa
    from tools.g1_lidar import _ensure_subs as _ensure_subs  # noqa
except Exception as e:  # pragma: no cover
    log.warning(f"g1_lidar helpers unavailable: {e}")


def _encode_compact(xyzi: np.ndarray) -> bytes:
    """(N,4) float32 [x,y,z,intensity] → 7 bytes/point binary."""
    if xyzi is None or len(xyzi) == 0:
        return b""
    n = len(xyzi)
    xyz_i16 = np.clip(xyzi[:, :3] * 100.0, -32767, 32767).astype(np.int16)
    inten_u8 = np.clip(xyzi[:, 3], 0, 255).astype(np.uint8)
    xyz_u8 = xyz_i16.view(np.uint8).reshape(n, 6)
    out = np.empty((n, 7), dtype=np.uint8)
    out[:, :6] = xyz_u8
    out[:, 6] = inten_u8
    return out.tobytes()


class LidarStreamer:
    """Background poller that keeps the latest compact-encoded cloud ready."""

    def __init__(self, max_points: int = 4000, hz: float = 10.0,
                 network_interface: str = "eth0"):
        self.max_points = max_points
        self.hz = hz
        self.iface = network_interface
        self._latest: Optional[bytes] = None
        self._latest_ts = 0.0
        self._point_count = 0
        self._frame_count = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._error: Optional[str] = None

    def start(self) -> None:
        if self._running or _parse is None:
            if _parse is None:
                self._error = "g1_lidar helpers unavailable"
            return
        # make sure the DDS subscriber is up
        try:
            from tools._g1_common import ensure_dds
            ensure_dds(self.iface)
        except Exception as e:
            log.debug(f"ensure_dds: {e}")
        if _ensure_subs is not None:
            err = _ensure_subs()
            if err:
                self._error = err
                log.warning(f"lidar subs: {err}")
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="dash-lidar")
        self._thread.start()
        log.info("📡 lidar streamer started")

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        period = 1.0 / max(self.hz, 1)
        while self._running:
            t0 = time.time()
            try:
                msg = _get_latest_cloud() if _get_latest_cloud else None
                if msg is not None:
                    xyzi = _parse(msg, max_points=self.max_points)
                    if xyzi is not None and len(xyzi):
                        enc = _encode_compact(xyzi)
                        with self._lock:
                            self._latest = enc
                            self._latest_ts = time.time()
                            self._point_count = len(xyzi)
                            self._frame_count += 1
            except Exception as e:
                log.debug(f"lidar loop: {e}")
            dt = time.time() - t0
            if dt < period:
                time.sleep(period - dt)

    def latest(self) -> Optional[bytes]:
        with self._lock:
            return self._latest

    def status(self) -> dict:
        return {
            "running": self._running,
            "point_count": self._point_count,
            "frames": self._frame_count,
            "hz": self.hz,
            "max_points": self.max_points,
            "last_frame_age": (round(time.time() - self._latest_ts, 2)
                               if self._latest_ts else None),
            "error": self._error,
        }


_STREAMER: Optional[LidarStreamer] = None


def get_lidar_streamer() -> LidarStreamer:
    global _STREAMER
    if _STREAMER is None:
        import os
        _STREAMER = LidarStreamer(
            max_points=int(os.getenv("DASHBOARD_LIDAR_POINTS", "4000")),
            hz=float(os.getenv("DASHBOARD_LIDAR_HZ", "10")),
            network_interface=os.getenv("G1_NETWORK_INTERFACE", "eth0"),
        )
    return _STREAMER
