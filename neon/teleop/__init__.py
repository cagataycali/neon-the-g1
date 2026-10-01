"""NEON WebXR teleop — drive the G1 from a Meta Quest 3 browser.

Pipeline:
    Quest 3 (docs/teleop/index.html, native WebXR)
        → WSS pose stream (Vuer-compatible HAND_MOVE/CONTROLLER_MOVE/CAMERA_MOVE)
        → neon.teleop.xr_bridge  (this package)
        → shared pose buffers (drop-in for televuer.TeleVuer)
        → xr_teleoperate IK (G1_29_ArmIK.solve_ik)
        → G1 arm DDS (G1_29_ArmController.ctrl_dual_arm)

The bridge exposes a ``WebXRPoseSource`` whose properties mirror
``televuer.TeleVuer`` (head_pose, left/right_arm_pose, hand positions/orientations,
pinch/trigger), so the upstream ``TeleVuerWrapper`` math + IK run UNCHANGED — we
simply swap the transport (native WebXR over WSS) for Vuer's own client.
"""
from .pose_source import WebXRPoseSource
from .xr_bridge import XRBridge, main

__all__ = ["WebXRPoseSource", "XRBridge", "main"]
