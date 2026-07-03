"""G1 SLAM @tool — kiss-icp pose estimation + map save/load.

Exposes pure @tools the Strands agent can call:

  - `g1_slam_start()`        → begin kiss-icp registration
  - `g1_slam_stop()`         → stop and leave accumulated map intact
  - `g1_slam_pose()`         → current SE(3) pose estimate (x,y,z,theta)
  - `g1_slam_reset()`        → clear odometry + map
  - `g1_slam_accumulate()`   → toggle map accumulation on/off
  - `g1_slam_save(name)`     → save ~/maps/<name>.npz
  - `g1_slam_load(name)`     → load saved map (with optional ICP relocalize)
  - `g1_slam_list_maps()`    → list saved maps
  - `g1_slam_stats()`        → frames processed, map point count, etc.

Map points are stored per-process (not broadcast). Call `g1_slam_map_points`
to retrieve them as numpy/dict.
"""
from __future__ import annotations

import itertools
import threading
import time
import queue
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

import numpy as np
from strands import tool

from . import g1_lidar
from ._g1_common import ensure_dds, _normalize

try:
    import open3d as _o3d
except ImportError:
    _o3d = None

logger = logging.getLogger(__name__)

MAPS_DIR = Path.home() / "maps"
_ICP_FITNESS_THRESHOLD = 0.3
_VOXEL_DEDUP_SIZE = 0.05  # 5 cm


def _voxel_dedup(pts: np.ndarray) -> np.ndarray:
    """Deduplicate (N,4) float32 XYZI to one point per 5 cm voxel cell."""
    if len(pts) == 0:
        return pts
    scale = 1.0 / _VOXEL_DEDUP_SIZE
    keys = np.floor(pts[:, :3] * scale).astype(np.int32)
    _, unique_idx = np.unique(keys, axis=0, return_index=True)
    return pts[unique_idx]


class _SlamRunner:
    """Singleton kiss-icp runner. State is process-wide, thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._odometry = None
        self._pose_matrix: Optional[np.ndarray] = None
        self._gravity_rotation: Optional[np.ndarray] = None
        self._map_chunks: List[np.ndarray] = []
        self._map_generation: int = 0
        self._pose_history: List[dict] = []
        self._running: bool = False
        self._accumulating: bool = False
        self._frame_q: queue.Queue = queue.Queue(maxsize=1)
        self._worker: Optional[threading.Thread] = None
        self._kiss_icp_ok: bool = False
        self._frames_processed: int = 0
        self._pose_offset: Optional[np.ndarray] = None
        self._relocalize_pending: bool = False
        self._frames_since_load: int = 0

    def start(self) -> str:
        if self._running:
            return "already running"
        MAPS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            from kiss_icp.kiss_icp import KissICP  # noqa: F401
            self._kiss_icp_ok = True
        except ImportError:
            return "kiss-icp not installed (pip install kiss-icp)"

        err = g1_lidar._ensure_subs()
        if err:
            return err

        err = g1_lidar.add_cloud_callback(self._on_cloud)
        if err:
            return err

        self._running = True
        self._worker = threading.Thread(target=self._process_loop, daemon=True, name="g1-slam")
        self._worker.start()
        return "ok"

    def stop(self):
        self._running = False
        # Drain queue so worker can exit
        try:
            self._frame_q.put_nowait(None)
        except queue.Full:
            pass

    def _on_cloud(self, msg):
        if not self._running or not self._kiss_icp_ok:
            return
        try:
            self._frame_q.put_nowait(msg)
        except queue.Full:
            pass  # drop: worker still processing previous frame

    def _process_loop(self):
        while self._running:
            msg = self._frame_q.get()
            if msg is None:
                break
            try:
                self._process_frame(msg)
            except Exception:
                logger.exception("kiss-icp frame processing failed")

    def _make_odometry(self):
        from kiss_icp.kiss_icp import KissICP
        from kiss_icp.config import KISSConfig
        cfg = KISSConfig()
        cfg.data.deskew = False
        cfg.data.max_range = 40.0
        cfg.data.min_range = 1.0
        cfg.mapping.voxel_size = 0.3
        return KissICP(config=cfg)

    def _process_frame(self, msg):
        pts = g1_lidar._cloud_to_numpy(msg, max_points=50000)
        if pts is None or len(pts) < 10:
            return

        valid = np.isfinite(pts[:, :3]).all(axis=1)
        pts = pts[valid]
        if len(pts) < 10:
            return
        norms = np.linalg.norm(pts[:, :3], axis=1)
        range_mask = (norms > 1.0) & (norms < 40.0)
        pts = pts[range_mask]
        xyz = np.ascontiguousarray(pts[:, :3].astype(np.float64))
        if len(xyz) < 10:
            return

        if self._gravity_rotation is not None:
            xyz = np.ascontiguousarray((self._gravity_rotation @ xyz.T).T)

        try:
            if self._odometry is None:
                self._odometry = self._make_odometry()
            timestamps = np.zeros(len(xyz), dtype=np.float64)
            self._odometry.register_frame(xyz, timestamps)
            pose_matrix = self._odometry.last_pose
        except Exception:
            logger.exception("kiss-icp register_frame failed")
            self._kiss_icp_ok = False
            return

        with self._lock:
            self._frames_processed += 1
            self._frames_since_load += 1
            relocalize_pending = self._relocalize_pending and self._frames_since_load >= 10
            map_snapshot = (np.vstack(self._map_chunks)
                            if (relocalize_pending and self._map_chunks) else None)
            gen_at_reloc = self._map_generation if relocalize_pending else None

        if relocalize_pending:
            offset = self._try_relocalize(xyz, map_snapshot)
            with self._lock:
                if self._map_generation == gen_at_reloc:
                    self._relocalize_pending = False
                    if offset is not None:
                        self._pose_offset = offset

        if self._pose_offset is not None:
            pose_matrix = self._pose_offset @ pose_matrix

        with self._lock:
            self._pose_matrix = pose_matrix
            pose_dict = {
                "x": float(pose_matrix[0, 3]),
                "y": float(pose_matrix[1, 3]),
                "z": float(pose_matrix[2, 3]),
                "theta": float(np.arctan2(pose_matrix[1, 0], pose_matrix[0, 0])),
                "timestamp": time.time(),
            }
            self._pose_history.append(pose_dict)
            if len(self._pose_history) > 2000:
                self._pose_history = self._pose_history[-2000:]

        if self._accumulating:
            ones = np.ones((len(xyz), 1), dtype=np.float64)
            xyz_h = np.hstack([xyz, ones])
            world_xyz = (pose_matrix @ xyz_h.T).T[:, :3]
            world_pts = np.hstack([world_xyz.astype(np.float32), pts[:, 3:4]])

            chunks_to_compact = None
            with self._lock:
                self._map_chunks.append(world_pts)
                if len(self._map_chunks) > 100:
                    chunks_to_compact = self._map_chunks
                    self._map_chunks = []
                    gen_at_steal = self._map_generation

            if chunks_to_compact is not None:
                combined = np.vstack(chunks_to_compact)
                deduped = _voxel_dedup(combined)
                with self._lock:
                    if self._map_generation == gen_at_steal:
                        self._map_chunks = [deduped] + self._map_chunks

    def _try_relocalize(self, xyz: np.ndarray, map_pts: Optional[np.ndarray]) -> Optional[np.ndarray]:
        if _o3d is None or map_pts is None or len(map_pts) < 100:
            return None
        src = _o3d.geometry.PointCloud()
        src.points = _o3d.utility.Vector3dVector(xyz)
        tgt = _o3d.geometry.PointCloud()
        tgt.points = _o3d.utility.Vector3dVector(map_pts[:, :3].astype(np.float64))
        src = src.voxel_down_sample(0.5)
        tgt = tgt.voxel_down_sample(0.5)
        result = _o3d.pipelines.registration.registration_icp(
            src, tgt,
            max_correspondence_distance=1.0,
            init=np.eye(4),
            estimation_method=_o3d.pipelines.registration.TransformationEstimationPointToPoint(),
        )
        if result.fitness < _ICP_FITNESS_THRESHOLD:
            return None
        T = np.asarray(result.transformation)
        if np.linalg.norm(T[:3, 3]) > 50.0 or (T[0, 0] + T[1, 1] + T[2, 2]) < 0.0:
            return None
        return T

    # ---- Query API ----

    def get_pose(self) -> Optional[dict]:
        with self._lock:
            return dict(self._pose_history[-1]) if self._pose_history else None

    def get_stats(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "kiss_icp_ok": self._kiss_icp_ok,
                "accumulating": self._accumulating,
                "frames_processed": self._frames_processed,
                "map_chunks": len(self._map_chunks),
                "map_point_count": sum(len(c) for c in self._map_chunks),
                "pose_history_len": len(self._pose_history),
                "has_pose": self._pose_matrix is not None,
            }

    def set_accumulating(self, val: bool):
        with self._lock:
            self._accumulating = bool(val)

    def reset(self):
        with self._lock:
            self._odometry = None
            self._pose_matrix = None
            self._map_chunks.clear()
            self._pose_history.clear()
            self._gravity_rotation = None
            self._pose_offset = None
            self._relocalize_pending = False
            self._map_generation += 1
            self._frames_since_load = 0
            self._frames_processed = 0

    # ---- Map save/load ----

    @staticmethod
    def _safe_map_path(name: str) -> Optional[Path]:
        try:
            path = (MAPS_DIR / f"{name}.npz").resolve()
        except Exception:
            return None
        if not str(path).startswith(str(MAPS_DIR.resolve())):
            return None
        return path

    def save_map(self, name: str) -> dict:
        path = self._safe_map_path(name)
        if path is None:
            return {"ok": False, "error": "invalid map name"}
        with self._lock:
            chunks = list(self._map_chunks)
            gravity_rotation = (self._gravity_rotation.copy()
                                if self._gravity_rotation is not None
                                else np.eye(3, dtype=np.float64))
            last_pose = (self._pose_matrix.copy()
                         if self._pose_matrix is not None
                         else np.eye(4, dtype=np.float64))
        if not chunks:
            return {"ok": False, "error": "no map data accumulated"}
        combined = np.vstack(chunks)
        try:
            MAPS_DIR.mkdir(parents=True, exist_ok=True)
            np.savez(path, points=combined,
                     gravity_rotation=gravity_rotation, last_pose=last_pose,
                     point_count=np.array(len(combined)))
            return {"ok": True, "path": str(path), "point_count": int(len(combined))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def load_map(self, name: str, relocalize: bool = False) -> dict:
        path = self._safe_map_path(name)
        if path is None:
            return {"ok": False, "error": "invalid map name"}
        legacy = path.with_suffix(".npy")
        if not path.exists() and legacy.exists():
            use_path, is_legacy = legacy, True
        elif path.exists():
            use_path, is_legacy = path, False
        else:
            return {"ok": False, "error": f"map file not found: {path}"}

        try:
            if is_legacy:
                pts = np.load(use_path)
                gravity_rotation = None
                last_pose = None
            else:
                data = np.load(use_path)
                pts = data["points"]
                gravity_rotation = data["gravity_rotation"] if "gravity_rotation" in data else None
                last_pose = data["last_pose"] if "last_pose" in data else None

            with self._lock:
                self._map_chunks = [pts]
                self._odometry = None
                self._pose_matrix = None
                self._pose_history.clear()
                self._pose_offset = None
                if gravity_rotation is not None:
                    self._gravity_rotation = gravity_rotation
                self._relocalize_pending = bool(relocalize)
                self._frames_since_load = 0

            out: Dict[str, Any] = {
                "ok": True, "path": str(use_path),
                "point_count": int(len(pts)),
                "relocalize_requested": bool(relocalize),
            }
            if last_pose is not None:
                out["last_pose"] = last_pose.tolist()
            return out
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def list_maps() -> List[dict]:
        if not MAPS_DIR.exists():
            return []
        seen = set()
        maps = []
        for p in itertools.chain(sorted(MAPS_DIR.glob("*.npz")),
                                 sorted(MAPS_DIR.glob("*.npy"))):
            if p.stem in seen:
                continue
            seen.add(p.stem)
            try:
                if p.suffix == ".npz":
                    data = np.load(p)
                    count = (int(data["point_count"]) if "point_count" in data
                             else len(data["points"]))
                else:
                    pts = np.load(p, mmap_mode="r")
                    count = len(pts)
                    del pts
                maps.append({"name": p.stem, "point_count": int(count), "path": str(p)})
            except Exception as e:
                maps.append({"name": p.stem, "point_count": None, "path": str(p),
                             "error": str(e)})
        return maps


_RUNNER = _SlamRunner()


# ---- @tool wrappers ----

@tool
def g1_slam_start(network_interface: str = "eth0") -> Dict[str, Any]:
    """Start kiss-icp pose estimation on the LiDAR stream.

    Pose accumulates immediately; call g1_slam_accumulate(True) to also
    build an accumulated world map.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    msg = _RUNNER.start()
    return _normalize({"status": "success" if msg == "ok" else "error", "message": msg})


@tool
def g1_slam_stop() -> Dict[str, Any]:
    """Stop SLAM processing. Pose history and accumulated map are kept."""
    _RUNNER.stop()
    return _normalize({"status": "success", "message": "SLAM stopped (state retained)"})


@tool
def g1_slam_pose() -> Dict[str, Any]:
    """Current SE(3) pose estimate as {x, y, z, theta, timestamp}.

    theta = yaw angle (radians) extracted from the rotation matrix.
    """
    pose = _RUNNER.get_pose()
    if pose is None:
        return _normalize({"status": "error", "message": "no pose yet — is SLAM started?"})
    return _normalize({"status": "success", **pose})


@tool
def g1_slam_reset() -> Dict[str, Any]:
    """Reset odometry and clear the accumulated map."""
    _RUNNER.reset()
    return _normalize({"status": "success", "message": "SLAM reset"})


@tool
def g1_slam_accumulate(on: bool = True) -> Dict[str, Any]:
    """Toggle map accumulation. Pose estimation continues regardless."""
    _RUNNER.set_accumulating(on)
    return _normalize({"status": "success", "accumulating": on})


@tool
def g1_slam_save(name: str) -> Dict[str, Any]:
    """Save the accumulated map to `~/maps/<name>.npz`.

    Args:
        name: filename stem (no extension, no path traversal).
    """
    result = _RUNNER.save_map(name)
    return _normalize({"status": "success" if result.get("ok") else "error", **result})


@tool
def g1_slam_load(name: str, relocalize: bool = False) -> Dict[str, Any]:
    """Load a previously saved map.

    Args:
        name: filename stem to load.
        relocalize: if True, attempt ICP alignment against the map after
                    a few frames so the robot's current pose is snapped
                    onto the loaded map frame. Requires open3d.
    """
    result = _RUNNER.load_map(name, relocalize=relocalize)
    return _normalize({"status": "success" if result.get("ok") else "error", **result})


@tool
def g1_slam_list_maps() -> Dict[str, Any]:
    """List saved maps under `~/maps/`."""
    maps = _RUNNER.list_maps()
    return _normalize({"status": "success", "maps": maps, "count": len(maps)})


@tool
def g1_slam_stats() -> Dict[str, Any]:
    """SLAM runner stats: frames processed, map size, current pose available."""
    return _normalize({"status": "success", **_RUNNER.get_stats()})
