"""G1 LiDAR @tool — Livox MID-360 point cloud access.

Yoinked and adapted from `controllers/lidar.py` in the server codebase.
The server-side LidarMonitor was coupled to aiohttp WebSocket broadcast;
this version is a pure DDS subscriber + numpy parser suitable for the
Strands agent toolkit. It keeps a singleton subscriber and exposes @tools
for:

  - `g1_lidar_state()`      → current LiDAR sensor state (frequency, state code)
  - `g1_lidar_snapshot()`   → one downsampled frame (XYZI numpy or dict)
  - `g1_lidar_switch()`     → turn the LiDAR ON/OFF via rt/utlidar/switch
  - `g1_lidar_stats()`      → basic stats about the latest cloud

Other in-process modules can call `get_latest_cloud()` directly to
avoid dict serialization overhead.
"""
from __future__ import annotations

import time
import threading
from typing import Dict, Any, Optional, List

import numpy as np
from strands import tool

from ._g1_common import ensure_dds, _normalize, _DDS_INIT_LOCK


_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "cloud_sub": None,
    "state_sub": None,
    "switch_pub": None,
    "latest_cloud": None,      # raw PointCloud2_ message
    "latest_cloud_ts": 0.0,
    "latest_state": None,      # LidarState_ message
    "latest_state_ts": 0.0,
    "cloud_cb_count": 0,
}


def _ensure_subs() -> Optional[str]:
    """Lazy-initialize DDS subscribers/publisher. Returns None on success."""
    if _STATE["cloud_sub"] is not None:
        return None

    try:
        from unitree_sdk2py.core.channel import ChannelSubscriber, ChannelPublisher
        from unitree_sdk2py.idl.sensor_msgs.msg.dds_ import PointCloud2_
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import LidarState_
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_
    except Exception as e:
        return f"SDK import failed: {e}"

    def _on_cloud(m):
        with _LOCK:
            _STATE["latest_cloud"] = m
            _STATE["latest_cloud_ts"] = time.time()
            _STATE["cloud_cb_count"] += 1

    def _on_state(m):
        with _LOCK:
            _STATE["latest_state"] = m
            _STATE["latest_state_ts"] = time.time()

    try:
        with _DDS_INIT_LOCK:
            cloud_sub = ChannelSubscriber("rt/utlidar/cloud_livox_mid360", PointCloud2_)
            cloud_sub.Init(_on_cloud, 10)
            state_sub = ChannelSubscriber("rt/utlidar/lidar_state", LidarState_)
            state_sub.Init(_on_state, 10)
            switch_pub = ChannelPublisher("rt/utlidar/switch", String_)
            switch_pub.Init()
        with _LOCK:
            _STATE["cloud_sub"] = cloud_sub
            _STATE["state_sub"] = state_sub
            _STATE["switch_pub"] = switch_pub
            _STATE["_String_"] = String_
        return None
    except Exception as e:
        return f"LiDAR subscribe/publish init failed: {e}"


def _cloud_to_numpy(msg, max_points: int = 50000) -> Optional[np.ndarray]:
    """Parse a PointCloud2_ into (N, 4) float32 [x, y, z, intensity].

    Uses Livox MID-360 layout (x@0, y@4, z@8, intensity@12, point_step=16).
    Returns None on empty/invalid clouds.
    """
    try:
        point_step = msg.point_step
        data = bytes(msg.data)
        total_points = msg.width * msg.height
        if total_points == 0 or point_step == 0:
            return None

        raw = np.frombuffer(data, dtype=np.uint8)
        base_offsets = np.arange(total_points, dtype=np.int64) * point_step
        stride = max(1, total_points // max_points)
        sampled_bases = base_offsets[::stride]
        valid_mask = (sampled_bases + 15) < len(raw)
        sampled_bases = sampled_bases[valid_mask]
        if sampled_bases.size == 0:
            return None

        byte_cols = np.arange(16, dtype=np.int64)
        gathered = raw[sampled_bases[:, None] + byte_cols[None, :]]  # (N, 16) uint8
        xyzi = gathered.reshape(-1).view(np.float32).reshape(-1, 4)

        nonzero = ~((xyzi[:, 0] == 0) & (xyzi[:, 1] == 0) & (xyzi[:, 2] == 0))
        xyzi = xyzi[nonzero]
        return xyzi.astype(np.float32) if len(xyzi) > 0 else None
    except Exception:
        return None


# ---- Public (intra-package) helpers for in-process consumers ----

def get_latest_cloud() -> Optional[Any]:
    """Return the latest raw PointCloud2_ message (for in-process subscribers)."""
    with _LOCK:
        return _STATE["latest_cloud"]


def add_cloud_callback(cb) -> Optional[str]:
    """Attach a fan-out callback that runs on every new point cloud.

    Lets a consumer hook into the stream without re-subscribing.
    Dispatch happens on a polling thread (~10ms latency, NOT on the DDS
    thread) — but it shields the DDS callback from slow consumers.
    The callback receives the PointCloud2_ message and may block briefly.
    """
    err = _ensure_subs()
    if err:
        return err

    with _LOCK:
        _STATE.setdefault("fanout", [])
        if cb not in _STATE["fanout"]:
            _STATE["fanout"].append(cb)
        if _STATE.get("_driver_thread"):
            return None  # already running

    _STATE.setdefault("_last_dispatch_ts", 0.0)

    def _driver():
        while True:
            with _LOCK:
                ts = _STATE["latest_cloud_ts"]
                m = _STATE["latest_cloud"]
                last = _STATE["_last_dispatch_ts"]
                fanout = list(_STATE.get("fanout", []))
            if ts > last and m is not None:
                with _LOCK:
                    _STATE["_last_dispatch_ts"] = ts
                for fn in fanout:
                    try:
                        fn(m)
                    except Exception:
                        pass
            time.sleep(0.01)

    t = threading.Thread(target=_driver, daemon=True, name="g1-lidar-fanout")
    with _LOCK:
        _STATE["_driver_thread"] = t
    t.start()
    return None


# ---- Tools ----

@tool
def g1_lidar_state(
    timeout: float = 1.5,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Read current Livox MID-360 LiDAR state (frequency, state code, uptime).

    Safe, read-only. State topic: `rt/utlidar/lidar_state`.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    err = _ensure_subs()
    if err:
        return _normalize({"status": "error", "message": err})

    t0 = time.time()
    while time.time() - t0 < timeout:
        with _LOCK:
            m = _STATE["latest_state"]
            ts = _STATE["latest_state_ts"]
        if m is not None and ts >= t0 - timeout:
            return _normalize({
                "status": "success",
                "cloud_frequency_hz": float(m.cloud_frequency),
                "lidar_state_code": int(m.lidar_state),
                "work_tik": int(m.work_tik),
                "protocol_version": str(m.protocolVersion),
                "age_s": round(time.time() - ts, 2),
            })
        time.sleep(0.05)

    with _LOCK:
        m = _STATE["latest_state"]
        ts = _STATE["latest_state_ts"]
    if m is not None:
        return _normalize({
            "status": "success",
            "cloud_frequency_hz": float(m.cloud_frequency),
            "lidar_state_code": int(m.lidar_state),
            "work_tik": int(m.work_tik),
            "protocol_version": str(m.protocolVersion),
            "stale_s": round(time.time() - ts, 1),
        })
    return _normalize({"status": "error", "message": f"no LidarState in {timeout}s"})


@tool
def g1_lidar_snapshot(
    max_points: int = 4000,
    as_dict: bool = True,
    timeout: float = 2.0,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Grab one downsampled LiDAR frame.

    Args:
        max_points: downsample target (stride-based). Default 4000.
        as_dict: if True (default) return list-of-dicts [{x,y,z,i}, ...].
                 If False return a numpy summary (count + bbox) — saves tokens.
        timeout: max wait for a fresh frame.

    Returns:
        dict with `points` (or `summary`), `count`, `age_s`, and bbox.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    err = _ensure_subs()
    if err:
        return _normalize({"status": "error", "message": err})

    t0 = time.time()
    msg = None
    while time.time() - t0 < timeout:
        with _LOCK:
            m = _STATE["latest_cloud"]
            ts = _STATE["latest_cloud_ts"]
        if m is not None and ts >= t0 - 0.5:
            msg = m
            msg_ts = ts
            break
        time.sleep(0.05)

    if msg is None:
        with _LOCK:
            msg = _STATE["latest_cloud"]
            msg_ts = _STATE["latest_cloud_ts"]
    if msg is None:
        return _normalize({"status": "error", "message": f"no PointCloud2 in {timeout}s"})

    pts = _cloud_to_numpy(msg, max_points=max_points)
    if pts is None or len(pts) == 0:
        return _normalize({"status": "error", "message": "empty cloud"})

    bbox = {
        "xmin": float(pts[:, 0].min()), "xmax": float(pts[:, 0].max()),
        "ymin": float(pts[:, 1].min()), "ymax": float(pts[:, 1].max()),
        "zmin": float(pts[:, 2].min()), "zmax": float(pts[:, 2].max()),
    }

    out: Dict[str, Any] = {
        "status": "success",
        "count": int(len(pts)),
        "bbox": bbox,
        "age_s": round(time.time() - msg_ts, 2),
    }
    if as_dict:
        out["points"] = [
            {"x": round(float(p[0]), 3), "y": round(float(p[1]), 3),
             "z": round(float(p[2]), 3), "i": round(float(p[3]), 1)}
            for p in pts
        ]
    else:
        out["summary"] = {
            "mean_xyz": [float(pts[:, 0].mean()), float(pts[:, 1].mean()), float(pts[:, 2].mean())],
            "mean_intensity": float(pts[:, 3].mean()),
            "range_m": float(np.linalg.norm(pts[:, :3], axis=1).max()),
        }
    return _normalize(out)


@tool
def g1_lidar_switch(
    on: bool = True,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Turn the LiDAR ON or OFF via `rt/utlidar/switch`.

    Args:
        on: True → publish "ON", False → publish "OFF".
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    err = _ensure_subs()
    if err:
        return _normalize({"status": "error", "message": err})

    try:
        msg_cls = _STATE["_String_"]
        msg = msg_cls(data=("ON" if on else "OFF"))
        _STATE["switch_pub"].Write(msg)
        return _normalize({"status": "success", "message": f"lidar switch → {msg.data}"})
    except Exception as e:
        return _normalize({"status": "error", "message": f"switch publish failed: {e}"})


@tool
def g1_lidar_stats(network_interface: str = "eth0") -> Dict[str, Any]:
    """Report LiDAR subscriber stats: callbacks received, last frame age."""
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    err = _ensure_subs()
    if err:
        return _normalize({"status": "error", "message": err})

    with _LOCK:
        count = _STATE["cloud_cb_count"]
        ts = _STATE["latest_cloud_ts"]
        has = _STATE["latest_cloud"] is not None
    return _normalize({
        "status": "success",
        "cloud_callbacks": int(count),
        "has_cloud": has,
        "last_cloud_age_s": round(time.time() - ts, 2) if ts else None,
        "fanout_callbacks": len(_STATE.get("fanout", [])),
    })
