"""WebXRPoseSource — a televuer.TeleVuer-compatible pose buffer fed by WebXR.

The Quest 3 browser (docs/teleop/index.html) streams the SAME data Vuer would:
column-major 4x4 SE(3) matrices in OpenXR basis. This class stores the latest
poses and exposes the EXACT property surface that televuer.TeleVuer /
TeleVuerWrapper read, so the upstream coordinate transforms + IK work unchanged.

Mirrored surface (subset actually consumed by tv_wrapper.get_tele_data):
    head_pose, left_arm_pose, right_arm_pose                 -> (4,4) np
    left_hand_positions, right_hand_positions                -> (25,3)
    left_hand_orientations, right_hand_orientations          -> (25,3,3)
    left_hand_pinch / pinchValue / squeeze / squeezeValue    (+ right_*)
    left_ctrl_trigger / triggerValue / squeeze / ... thumbstick / aButton/bButton
    motion_data_ready
"""
from __future__ import annotations

import threading
import numpy as np


def _mat_from_colmajor(flat16):
    """16-float column-major → (4,4). Matches televuer reshape(order='F')."""
    a = np.asarray(flat16, dtype=float)
    if a.size != 16:
        return np.eye(4)
    return a.reshape(4, 4, order="F")


class WebXRPoseSource:
    """Thread-safe latest-pose store; drop-in for televuer.TeleVuer."""

    def __init__(self, use_hand_tracking: bool = True):
        self.use_hand_tracking = use_hand_tracking
        self._lock = threading.Lock()

        self._head = np.eye(4)
        self._left_arm = np.eye(4)
        self._right_arm = np.eye(4)
        self._motion_ready = False

        # hand tracking
        self._left_hand_pos = np.zeros((25, 3))
        self._right_hand_pos = np.zeros((25, 3))
        self._left_hand_ori = np.tile(np.eye(3), (25, 1, 1))
        self._right_hand_ori = np.tile(np.eye(3), (25, 1, 1))
        self._left_pinch = False;  self._left_pinchValue = 0.0
        self._left_squeeze = False; self._left_squeezeValue = 0.0
        self._right_pinch = False; self._right_pinchValue = 0.0
        self._right_squeeze = False; self._right_squeezeValue = 0.0

        # controller
        self._lc = {"trigger": False, "triggerValue": 0.0, "squeeze": False,
                    "squeezeValue": 0.0, "thumbstick": False,
                    "thumbstickValue": [0.0, 0.0], "aButton": False, "bButton": False}
        self._rc = dict(self._lc)

    # ───────────────────────── ingest (from WS) ─────────────────────────
    def update_camera(self, matrix16):
        with self._lock:
            self._head = _mat_from_colmajor(matrix16)

    def update_hands(self, value: dict):
        """value = {left:[400], right:[400], leftState:{...}, rightState:{...}}."""
        with self._lock:
            for side, arm_attr, pos_attr, ori_attr in (
                ("left", "_left_arm", "_left_hand_pos", "_left_hand_ori"),
                ("right", "_right_arm", "_right_hand_pos", "_right_hand_ori"),
            ):
                buf = value.get(side)
                if not buf or len(buf) < 400:
                    continue
                # joint 0 (wrist) 4x4 → arm pose (matches televuer hand_data[0:16])
                setattr(self, arm_attr, _mat_from_colmajor(buf[0:16]))
                pos = np.zeros((25, 3)); ori = np.zeros((25, 3, 3))
                for i in range(25):
                    base = i * 16
                    # column-major: translation lives at indices 12,13,14
                    pos[i] = [buf[base + 12], buf[base + 13], buf[base + 14]]
                    # upper-left 3x3 (column-major reads 0,1,2 / 4,5,6 / 8,9,10)
                    ori[i] = np.array([
                        [buf[base + 0], buf[base + 4], buf[base + 8]],
                        [buf[base + 1], buf[base + 5], buf[base + 9]],
                        [buf[base + 2], buf[base + 6], buf[base + 10]],
                    ])
                setattr(self, pos_attr, pos)
                setattr(self, ori_attr, ori)

            ls = value.get("leftState") or {}
            rs = value.get("rightState") or {}
            self._left_pinch = bool(ls.get("pinch", False))
            self._left_pinchValue = float(ls.get("pinchValue", 0.0))
            self._left_squeeze = bool(ls.get("squeeze", False))
            self._left_squeezeValue = float(ls.get("squeezeValue", 0.0))
            self._right_pinch = bool(rs.get("pinch", False))
            self._right_pinchValue = float(rs.get("pinchValue", 0.0))
            self._right_squeeze = bool(rs.get("squeeze", False))
            self._right_squeezeValue = float(rs.get("squeezeValue", 0.0))
            self._motion_ready = True

    def update_controllers(self, value: dict):
        """value = {left:[16], right:[16], leftState:{...}, rightState:{...}}."""
        with self._lock:
            if value.get("left") and len(value["left"]) >= 16:
                self._left_arm = _mat_from_colmajor(value["left"][:16])
            if value.get("right") and len(value["right"]) >= 16:
                self._right_arm = _mat_from_colmajor(value["right"][:16])
            if value.get("leftState"):
                self._lc.update(value["leftState"])
            if value.get("rightState"):
                self._rc.update(value["rightState"])
            self._motion_ready = True

    # ───────────────────────── televuer surface ─────────────────────────
    @property
    def head_pose(self):
        with self._lock: return self._head.copy()

    @property
    def left_arm_pose(self):
        with self._lock: return self._left_arm.copy()

    @property
    def right_arm_pose(self):
        with self._lock: return self._right_arm.copy()

    @property
    def left_hand_positions(self):
        with self._lock: return self._left_hand_pos.copy()

    @property
    def right_hand_positions(self):
        with self._lock: return self._right_hand_pos.copy()

    @property
    def left_hand_orientations(self):
        with self._lock: return self._left_hand_ori.copy()

    @property
    def right_hand_orientations(self):
        with self._lock: return self._right_hand_ori.copy()

    @property
    def left_hand_pinch(self):       return self._left_pinch
    @property
    def left_hand_pinchValue(self):  return self._left_pinchValue
    @property
    def left_hand_squeeze(self):     return self._left_squeeze
    @property
    def left_hand_squeezeValue(self):return self._left_squeezeValue
    @property
    def right_hand_pinch(self):      return self._right_pinch
    @property
    def right_hand_pinchValue(self): return self._right_pinchValue
    @property
    def right_hand_squeeze(self):    return self._right_squeeze
    @property
    def right_hand_squeezeValue(self):return self._right_squeezeValue

    @property
    def left_ctrl_trigger(self):       return bool(self._lc["trigger"])
    @property
    def left_ctrl_triggerValue(self):  return float(self._lc["triggerValue"])
    @property
    def left_ctrl_squeeze(self):       return bool(self._lc["squeeze"])
    @property
    def left_ctrl_squeezeValue(self):  return float(self._lc["squeezeValue"])
    @property
    def left_ctrl_thumbstick(self):    return bool(self._lc["thumbstick"])
    @property
    def left_ctrl_thumbstickValue(self):return np.array(self._lc["thumbstickValue"])
    @property
    def left_ctrl_aButton(self):       return bool(self._lc["aButton"])
    @property
    def left_ctrl_bButton(self):       return bool(self._lc["bButton"])
    @property
    def right_ctrl_trigger(self):      return bool(self._rc["trigger"])
    @property
    def right_ctrl_triggerValue(self): return float(self._rc["triggerValue"])
    @property
    def right_ctrl_squeeze(self):      return bool(self._rc["squeeze"])
    @property
    def right_ctrl_squeezeValue(self): return float(self._rc["squeezeValue"])
    @property
    def right_ctrl_thumbstick(self):   return bool(self._rc["thumbstick"])
    @property
    def right_ctrl_thumbstickValue(self):return np.array(self._rc["thumbstickValue"])
    @property
    def right_ctrl_aButton(self):      return bool(self._rc["aButton"])
    @property
    def right_ctrl_bButton(self):      return bool(self._rc["bButton"])

    @property
    def motion_data_ready(self):
        with self._lock: return self._motion_ready
