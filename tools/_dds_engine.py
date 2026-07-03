"""Generic DDS engine for neon — subscribe, publish, snapshot any topic.

CycloneDDS + unitree_sdk2py.

Usage:
    from ._dds_engine import DDSController, TOPIC_CATALOG, msg_to_dict
"""
from __future__ import annotations

import importlib
import logging
import subprocess
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Topic catalog — topic → (idl_module, class_name, description, category)
# ---------------------------------------------------------------------------
TOPIC_CATALOG: Dict[str, Tuple[str, str, str, str]] = {
    # --- READ-ONLY: robot state ---
    "rt/lowstate":             ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowState_",       "IMU, joints, motors (~1kHz)",  "state"),
    "rt/lf/lowstate":          ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowState_",       "Low-freq LowState variant",    "state"),
    "rt/bmsstate":             ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "BmsState_",       "Battery management",           "state"),
    "rt/lf/bmsstate":          ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "BmsState_",       "Battery (low-freq)",           "state"),
    "rt/mainboardstate":       ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "MainBoardState_", "Fan / board temps",            "state"),
    "rt/pressuresensorstate":  ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "PressSensorState_","Foot pressure sensors",       "state"),
    "rt/lf/sportmodestate":    ("unitree_sdk2py.idl.unitree_go.msg.dds_", "SportModeState_", "Motion state (low-freq)",      "state"),
    "rt/lf/secondary_imu":     ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowState_",       "Secondary IMU",                "state"),
    "rt/multiplestate":        ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowState_",       "Combined state",               "state"),
    # --- READ-ONLY: LiDAR / SLAM ---
    "rt/utlidar/cloud_livox_mid360": ("unitree_sdk2py.idl.sensor_msgs.msg.dds_", "PointCloud2_", "Livox Mid-360 point cloud", "lidar"),
    "rt/utlidar/lidar_state":        ("unitree_sdk2py.idl.unitree_go.msg.dds_",  "LidarState_",  "LiDAR sensor state",        "lidar"),
    # --- READ-ONLY: joystick ---
    "rt/wirelesscontroller":   ("unitree_sdk2py.idl.unitree_go.msg.dds_", "WirelessController_", "Remote joystick (silent unpaired)", "joystick"),
    # --- CONTROL (WRITE) — dangerous ---
    "rt/lowcmd":               ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowCmd_",         "Low-level motor cmd (🚨)",     "control"),
    "rt/armsdk":               ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowCmd_",         "Arm SDK override (🚨)",         "control"),
    "rt/user_lowcmd":          ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "LowCmd_",         "User low-level cmd (🚨)",       "control"),
    "rt/bmscmd":               ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "BmsCmd_",         "BMS cmd (🚨 power/reboot)",    "control"),
    # --- G1 HANDS (Inspire/Unitree 5/7-DoF hand) ---
    "rt/inspire/state":        ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "HandState_",      "Inspire hand state (joint pos/tau)", "hand"),
    "rt/inspire/cmd":          ("unitree_sdk2py.idl.unitree_hg.msg.dds_", "HandCmd_",        "Inspire hand command (🚨 write)",    "hand"),
    # --- SLAM / Odometry (experimental — topic naming varies) ---
    "rt/odom":                 ("unitree_sdk2py.idl.nav_msgs.msg.dds_",   "Odometry_",       "Robot odometry (nav_msgs)",          "slam"),
    "rt/unitree_slam/odom":    ("unitree_sdk2py.idl.nav_msgs.msg.dds_",   "Odometry_",       "Unitree SLAM odometry",              "slam"),
    "rt/unitree_slam/global_map": ("unitree_sdk2py.idl.sensor_msgs.msg.dds_", "PointCloud2_", "Unitree SLAM global map",           "slam"),
    # --- WRITE: non-motion config ---
    "rt/utlidar/switch":       ("unitree_sdk2py.idl.std_msgs.msg.dds_",   "String_",         "LiDAR ON/OFF switch",           "config"),
}

DANGEROUS_PUB_TOPICS = {"rt/lowcmd", "rt/armsdk", "rt/user_lowcmd", "rt/inspire/cmd", "rt/bmscmd"}

# Shared lock from _g1_common — protects against concurrent
# ChannelSubscriber/Publisher creation, which segfaults CycloneDDS bindings.
from ._g1_common import _DDS_INIT_LOCK


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _import_idl(module_path: str, class_name: str):
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def msg_to_dict(msg: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """Best-effort recursive conversion of an IDL DDS message into JSON-safe dict."""
    if msg is None:
        return None
    if isinstance(msg, (bool, int, float, str)):
        return msg
    if isinstance(msg, (bytes, bytearray)):
        return {"__bytes__": True, "len": len(msg)}
    if depth >= max_depth:
        return str(msg)[:500]
    if isinstance(msg, (list, tuple)):
        if len(msg) > 128:
            return {
                "__truncated_list__": True,
                "len": len(msg),
                "head": [msg_to_dict(v, depth + 1, max_depth) for v in msg[:16]],
            }
        return [msg_to_dict(v, depth + 1, max_depth) for v in msg]
    if isinstance(msg, dict):
        return {k: msg_to_dict(v, depth + 1, max_depth) for k, v in msg.items()}

    out: Dict[str, Any] = {}
    for attr in dir(msg):
        if attr.startswith("_"):
            continue
        try:
            val = getattr(msg, attr)
        except Exception:
            continue
        if callable(val):
            continue
        try:
            out[attr] = msg_to_dict(val, depth + 1, max_depth)
        except Exception as e:
            out[attr] = f"<unserializable: {e}>"
    return out or str(msg)[:500]


# ---------------------------------------------------------------------------
# Subscription handle
# ---------------------------------------------------------------------------
class _SubHandle:
    __slots__ = ("topic", "cls", "sub", "buffer", "lock", "count", "started_at", "last_ts")

    def __init__(self, topic: str, cls: Any, sub: Any, max_buffer: int = 20):
        self.topic = topic
        self.cls = cls
        self.sub = sub
        self.buffer: Deque[Tuple[float, Any]] = deque(maxlen=max_buffer)
        self.lock = threading.Lock()
        self.count = 0
        self.started_at = time.time()
        self.last_ts = 0.0


# ---------------------------------------------------------------------------
# DDSController
# ---------------------------------------------------------------------------
class DDSController:
    """Generic DDS subscribe/publish over known + discoverable G1 topics."""

    def __init__(self):
        self._subs: Dict[str, _SubHandle] = {}
        self._pubs: Dict[str, Any] = {}
        self._lock = threading.Lock()

    # --- catalog ------------------------------------------------------
    def list_topics(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for topic, (mod, cls, desc, cat) in TOPIC_CATALOG.items():
            if category and cat != category:
                continue
            out.append({
                "topic": topic,
                "type": f"{mod}.{cls}",
                "description": desc,
                "category": cat,
                "subscribed": topic in self._subs,
                "dangerous_to_publish": topic in DANGEROUS_PUB_TOPICS,
            })
        return out

    def discover(self, timeout: float = 5.0) -> Dict[str, Any]:
        try:
            proc = subprocess.run(
                ["cyclonedds", "ls", "--suppress-progress-bar"],
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            return {"ok": False, "message": "cyclonedds CLI not found on $PATH"}
        except subprocess.TimeoutExpired:
            return {"ok": False, "message": f"cyclonedds ls timed out after {timeout}s"}

        topics = sorted({
            line.strip().split()[0]
            for line in proc.stdout.splitlines()
            if line.strip().startswith("rt/")
        })
        return {
            "ok": True, "live_topics": topics, "count": len(topics),
            "stderr_preview": proc.stderr[:500] if proc.stderr else "",
        }

    # --- subscribe ----------------------------------------------------
    def subscribe(
        self,
        topic: str,
        type_module: Optional[str] = None,
        type_class: Optional[str] = None,
        max_buffer: int = 20,
        queue_depth: int = 10,
    ) -> Dict[str, Any]:
        if topic in self._subs:
            return {"ok": True, "message": f"already subscribed: {topic}",
                    "count": self._subs[topic].count}

        if type_module and type_class:
            mod_path, cls_name = type_module, type_class
        elif topic in TOPIC_CATALOG:
            mod_path, cls_name, _, _ = TOPIC_CATALOG[topic]
        else:
            return {"ok": False, "message": (
                f"Unknown topic '{topic}'. Pass type_module + type_class, "
                "or add to TOPIC_CATALOG.")}

        try:
            cls = _import_idl(mod_path, cls_name)
        except Exception as e:
            return {"ok": False, "message": f"IDL import failed: {e}"}
        try:
            from unitree_sdk2py.core.channel import ChannelSubscriber
        except Exception as e:
            return {"ok": False, "message": f"SDK import failed: {e}"}

        handle = _SubHandle(topic, cls, None, max_buffer=max_buffer)

        def _cb(msg):
            with handle.lock:
                handle.count += 1
                handle.last_ts = time.time()
                handle.buffer.append((handle.last_ts, msg))

        try:
            with _DDS_INIT_LOCK:
                sub = ChannelSubscriber(topic, cls)
                sub.Init(_cb, queue_depth)
        except Exception as e:
            return {"ok": False, "message": f"ChannelSubscriber init failed: {e}"}

        handle.sub = sub
        with self._lock:
            self._subs[topic] = handle

        return {"ok": True, "topic": topic, "type": f"{mod_path}.{cls_name}",
                "message": f"subscribed to {topic}"}

    def unsubscribe(self, topic: str) -> Dict[str, Any]:
        with self._lock:
            h = self._subs.pop(topic, None)
        if h is None:
            return {"ok": False, "message": f"not subscribed: {topic}"}
        h.sub = None
        return {"ok": True, "message": f"unsubscribed: {topic}", "count": h.count}

    def read(self, topic: str, n: int = 1, as_dict: bool = True) -> Dict[str, Any]:
        h = self._subs.get(topic)
        if h is None:
            return {"ok": False, "message": f"not subscribed: {topic}. call subscribe() first"}
        with h.lock:
            items = list(h.buffer)[-max(1, int(n)):]
            count = h.count
            last_ts = h.last_ts
        msgs = []
        for ts, raw in items:
            msgs.append({
                "ts": ts,
                "age_s": round(time.time() - ts, 3),
                "data": msg_to_dict(raw) if as_dict else repr(raw)[:500],
            })
        return {
            "ok": True, "topic": topic, "messages": msgs,
            "total_count": count, "last_ts": last_ts,
            "last_age_s": round(time.time() - last_ts, 3) if last_ts else None,
        }

    def snapshot(
        self,
        topic: str,
        timeout: float = 2.0,
        type_module: Optional[str] = None,
        type_class: Optional[str] = None,
    ) -> Dict[str, Any]:
        if topic not in self._subs:
            r = self.subscribe(topic, type_module, type_class)
            if not r.get("ok"):
                return r

        h = self._subs[topic]
        t0 = time.time()
        while time.time() - t0 < timeout:
            with h.lock:
                if h.buffer:
                    ts, raw = h.buffer[-1]
                    return {"ok": True, "topic": topic, "ts": ts,
                            "age_s": round(time.time() - ts, 3),
                            "data": msg_to_dict(raw)}
            time.sleep(0.02)

        with h.lock:
            if h.buffer:
                ts, raw = h.buffer[-1]
                return {"ok": True, "topic": topic, "ts": ts,
                        "age_s": round(time.time() - ts, 3),
                        "data": msg_to_dict(raw),
                        "note": "stale — no new message within timeout"}
        return {"ok": False, "message": f"no message on {topic} within {timeout}s"}

    def stats(self) -> Dict[str, Any]:
        out = {}
        now = time.time()
        for topic, h in self._subs.items():
            with h.lock:
                out[topic] = {
                    "count": h.count,
                    "uptime_s": round(now - h.started_at, 1),
                    "last_age_s": round(now - h.last_ts, 3) if h.last_ts else None,
                    "buffered": len(h.buffer),
                }
        return {"subscriptions": out, "publishers": sorted(self._pubs.keys())}

    # --- publish ------------------------------------------------------
    def publish(
        self,
        topic: str,
        payload: Dict[str, Any],
        type_module: Optional[str] = None,
        type_class: Optional[str] = None,
        unsafe: bool = False,
    ) -> Dict[str, Any]:
        if topic in TOPIC_CATALOG:
            mod_path, cls_name, _, _ = TOPIC_CATALOG[topic]
        elif type_module and type_class:
            mod_path, cls_name = type_module, type_class
        else:
            return {"ok": False, "message": f"Unknown topic '{topic}' and no type given"}

        if topic in DANGEROUS_PUB_TOPICS and not unsafe:
            return {"ok": False, "message": (
                f"Refusing to publish to DANGEROUS topic '{topic}' — "
                "pass unsafe=True if you really mean it. "
                "Prefer g1_arm_action / g1_move_velocity / g1_stand instead.")}

        try:
            cls = _import_idl(mod_path, cls_name)
        except Exception as e:
            return {"ok": False, "message": f"IDL import failed: {e}"}
        try:
            from unitree_sdk2py.core.channel import ChannelPublisher
        except Exception as e:
            return {"ok": False, "message": f"SDK import failed: {e}"}

        pub = self._pubs.get(topic)
        if pub is None:
            try:
                with _DDS_INIT_LOCK:
                    pub = ChannelPublisher(topic, cls)
                    pub.Init()
            except Exception as e:
                return {"ok": False, "message": f"ChannelPublisher init failed: {e}"}
            self._pubs[topic] = pub

        try:
            msg = cls()
            for key, val in (payload or {}).items():
                if hasattr(msg, key):
                    setattr(msg, key, val)
                else:
                    logger.warning("publish: %s has no attr '%s' — skipping", cls_name, key)
        except Exception as e:
            return {"ok": False, "message": f"message construction failed: {e}"}

        try:
            pub.Write(msg)
        except Exception as e:
            return {"ok": False, "message": f"Write failed: {e}"}

        return {
            "ok": True, "topic": topic, "type": f"{mod_path}.{cls_name}",
            "payload_keys": list((payload or {}).keys()),
            "message": f"published 1 msg to {topic}",
        }


# Convenience singleton — one instance per process
_SINGLETON: Optional[DDSController] = None


def get_dds() -> DDSController:
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = DDSController()
    return _SINGLETON
