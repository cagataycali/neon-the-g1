"""
🦴 G1 joint reference — joint-index / torque / position maps.

READ-ONLY reference tool. No robot motion. Useful so the agent knows
which joint index maps to which body part when reading LowState motors[].

The 29-DoF G1 layout (some DoFs invalid on smaller variants):

    Legs (left):   HipPitch, HipRoll, HipYaw, Knee, AnklePitch, AnkleRoll
    Legs (right):  HipPitch, HipRoll, HipYaw, Knee, AnklePitch, AnkleRoll
    Waist:         Yaw, Roll, Pitch         (Roll/Pitch invalid if waist locked)
    Arm (left):    ShoulderPitch, ShoulderRoll, ShoulderYaw, Elbow,
                   WristRoll, WristPitch, WristYaw
    Arm (right):   (same 7 as left)

Total: 29 joints.

Also surfaces recommended Kp / Kd values from the SDK low-level example.
Useful context when reading torques or designing a custom low-level cmd.
"""
from __future__ import annotations

from typing import Any, Dict, List

from strands import tool

try:
    from ._g1_common import _normalize
except ImportError:  # pragma: no cover — direct import for tests
    from _g1_common import _normalize


# Joint index map — mirrors SDK G1JointIndex class
G1_JOINT_NAMES: List[str] = [
    # Left leg (0-5)
    "LeftHipPitch", "LeftHipRoll", "LeftHipYaw", "LeftKnee",
    "LeftAnklePitch", "LeftAnkleRoll",
    # Right leg (6-11)
    "RightHipPitch", "RightHipRoll", "RightHipYaw", "RightKnee",
    "RightAnklePitch", "RightAnkleRoll",
    # Waist (12-14)
    "WaistYaw", "WaistRoll", "WaistPitch",
    # Left arm (15-21)
    "LeftShoulderPitch", "LeftShoulderRoll", "LeftShoulderYaw", "LeftElbow",
    "LeftWristRoll", "LeftWristPitch", "LeftWristYaw",
    # Right arm (22-28)
    "RightShoulderPitch", "RightShoulderRoll", "RightShoulderYaw", "RightElbow",
    "RightWristRoll", "RightWristPitch", "RightWristYaw",
]

# Invalid-on-some-variants map
INVALID_NOTES: Dict[int, str] = {
    13: "invalid on 23dof/29dof with waist locked",
    14: "invalid on 23dof/29dof with waist locked",
    20: "invalid on 23dof",
    21: "invalid on 23dof",
    27: "invalid on 23dof",
    28: "invalid on 23dof",
}

# Recommended gains from SDK low-level example (29-motor layout)
KP_RECOMMENDED: List[float] = [
    60, 60, 60, 100, 40, 40,           # left leg
    60, 60, 60, 100, 40, 40,           # right leg
    60, 40, 40,                        # waist
    40, 40, 40, 40, 40, 40, 40,        # left arm
    40, 40, 40, 40, 40, 40, 40,        # right arm
]
KD_RECOMMENDED: List[float] = [
    1, 1, 1, 2, 1, 1,                  # left leg
    1, 1, 1, 2, 1, 1,                  # right leg
    1, 1, 1,                           # waist
    1, 1, 1, 1, 1, 1, 1,               # left arm
    1, 1, 1, 1, 1, 1, 1,               # right arm
]

# Body-part groupings
GROUPS: Dict[str, List[int]] = {
    "left_leg":  list(range(0, 6)),
    "right_leg": list(range(6, 12)),
    "waist":     list(range(12, 15)),
    "left_arm":  list(range(15, 22)),
    "right_arm": list(range(22, 29)),
}


@tool
def g1_joint_reference(group: str = "") -> Dict[str, Any]:
    """
    🦴 Return the G1 joint-index reference (name, group, Kp, Kd).

    READ-ONLY, no robot interaction. Useful when interpreting
    ``g1_read_lowstate().motors[i]`` — this tells you *which* joint motor[i]
    is.

    Args:
        group: optional filter. One of "left_leg", "right_leg", "waist",
            "left_arm", "right_arm". Empty = all.
    """
    joints = []
    indices = GROUPS.get(group) if group else list(range(len(G1_JOINT_NAMES)))
    if group and indices is None:
        return _normalize({
            "status": "error",
            "message": f"unknown group '{group}'. Valid: {sorted(GROUPS.keys())}",
        })

    for i in indices:
        row = {
            "index": i,
            "name": G1_JOINT_NAMES[i],
            "kp_recommended": KP_RECOMMENDED[i],
            "kd_recommended": KD_RECOMMENDED[i],
        }
        if i in INVALID_NOTES:
            row["note"] = INVALID_NOTES[i]
        joints.append(row)

    return _normalize({
        "status": "success",
        "count": len(joints),
        "joints": joints,
        "groups": list(GROUPS.keys()),
        "message": (
            f"{len(joints)} G1 joints"
            + (f" in group '{group}'" if group else " total")
        ),
    })


@tool
def g1_joint_name(index: int) -> Dict[str, Any]:
    """🦴 Look up a single joint name by numeric index (0-28)."""
    if not (0 <= index < len(G1_JOINT_NAMES)):
        return _normalize({
            "status": "error",
            "message": f"index {index} out of range [0, {len(G1_JOINT_NAMES)-1}]",
        })
    return _normalize({
        "status": "success",
        "index": index,
        "name": G1_JOINT_NAMES[index],
        "kp_recommended": KP_RECOMMENDED[index],
        "kd_recommended": KD_RECOMMENDED[index],
        "note": INVALID_NOTES.get(index, ""),
        "group": next((g for g, idxs in GROUPS.items() if index in idxs), None),
    })


@tool
def g1_joint_index(name: str) -> Dict[str, Any]:
    """🦴 Look up a joint index by name (case-insensitive, e.g. 'LeftKnee')."""
    key = name.strip().lower()
    for i, n in enumerate(G1_JOINT_NAMES):
        if n.lower() == key:
            return _normalize({
                "status": "success",
                "name": n,
                "index": i,
                "kp_recommended": KP_RECOMMENDED[i],
                "kd_recommended": KD_RECOMMENDED[i],
                "group": next(
                    (g for g, idxs in GROUPS.items() if i in idxs), None
                ),
            })
    return _normalize({
        "status": "error",
        "message": f"no joint named '{name}'. Try one of: {G1_JOINT_NAMES}",
    })
