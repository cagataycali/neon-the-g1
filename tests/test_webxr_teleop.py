"""Tests for the NEON WebXR teleop bridge pose decoding.

Validates the column-major SE(3) decoding that makes the Quest 3 WebXR stream
line up 1:1 with televuer's expectations — no robot/DDS/network needed.
"""
import math
import numpy as np

from neon.teleop.pose_source import WebXRPoseSource, _mat_from_colmajor

c, s = math.cos(math.pi / 2), math.sin(math.pi / 2)
RZ = np.array([[c, -s, 0, 0.1], [s, c, 0, 0.2], [0, 0, 1, 0.3], [0, 0, 0, 1.0]])


def test_colmajor_roundtrip():
    flat = RZ.flatten(order="F").tolist()  # WebXR transform.matrix is column-major
    assert np.allclose(_mat_from_colmajor(flat), RZ)


def _hand_buf():
    buf = []
    for i in range(25):
        Mi = RZ.copy()
        Mi[:3, 3] = [0.01 * i, 0.02 * i, 0.03 * i]
        buf += Mi.flatten(order="F").tolist()
    return buf


def test_hand_decode_positions_orientations():
    src = WebXRPoseSource(use_hand_tracking=True)
    src.update_hands({
        "left": _hand_buf(), "right": _hand_buf(),
        "leftState": {"pinch": True, "pinchValue": 0.83},
        "rightState": {"pinchValue": 0.1},
    })
    # wrist (joint 0) rotation preserved
    assert np.allclose(src.left_arm_pose[:3, :3], RZ[:3, :3])
    # positions (25,3)
    P = src.left_hand_positions
    assert P.shape == (25, 3)
    assert np.allclose(P[5], [0.05, 0.10, 0.15])
    # orientations (25,3,3)
    O = src.left_hand_orientations
    assert O.shape == (25, 3, 3)
    assert np.allclose(O[3], RZ[:3, :3])
    # state surface
    assert src.left_hand_pinch is True
    assert abs(src.left_hand_pinchValue - 0.83) < 1e-9
    assert src.motion_data_ready is True


def test_controller_decode():
    src = WebXRPoseSource(use_hand_tracking=False)
    flat = RZ.flatten(order="F").tolist()
    src.update_controllers({
        "left": flat, "right": flat,
        "leftState": {"trigger": True, "triggerValue": 0.7, "thumbstickValue": [0.5, -0.3]},
        "rightState": {"aButton": True},
    })
    assert np.allclose(src.left_arm_pose, RZ)
    assert src.left_ctrl_triggerValue == 0.7
    assert list(src.left_ctrl_thumbstickValue) == [0.5, -0.3]
    assert src.right_ctrl_aButton is True


def test_bridge_message_routing():
    from neon.teleop.xr_bridge import XRBridge
    b = XRBridge(use_hand_tracking=True, dry_run=True)
    import json
    b.handle_message(json.dumps({"type": "CAMERA_MOVE",
                                 "value": {"camera": {"matrix": RZ.flatten(order="F").tolist()}}}))
    assert np.allclose(b.source.head_pose, RZ)
