"""
🔍 G1 state inspection tools — FSM, mode, posture, IMU, joints.

READ-ONLY tools (safe to call anytime). No robot motion triggered.
"""
import logging
import time
from typing import Dict, Any

from strands import tool

logger = logging.getLogger(__name__)

from ._g1_common import (
    ensure_dds, get_loco_client, get_motion_switcher,
    read_fsm_id, read_fsm_mode, read_balance_mode, read_stand_height,
    read_swing_height, last_loco_rc,
    FSM_NAMES, HANDSHAKE_FSMS, ARM_READY_MODE_MACHINES, decode_code,
    _DDS_INIT_LOCK,
    _get_lowstate_cached,
    _read_mode_machine_from_lowstate,
    _normalize,
)


@tool
def g1_get_state(network_interface: str = "eth0") -> Dict[str, Any]:
    """
    🔍 Read full G1 state: MotionSwitcher mode, FSM id/mode, balance mode, stand height.

    This is a READ-ONLY tool — totally safe. Use before any motion command
    to verify the robot is in a reachable state.

    Returns:
        Dict with:
            status: 'success' or 'error'
            mode: MotionSwitcher form (should be {'name':'ai',...})
            fsm_id: current FSM id (500/501/801 = arm-ready)
            fsm_name: human-readable FSM name
            fsm_mode: internal FSM sub-mode
            balance_mode: current balance mode
            stand_height: current stand height
            arm_ready: bool — True if arm actions will work
            message: summary
    """
    result: Dict[str, Any] = {
        "status": "error",
        "mode": None,
        "fsm_id": None,
        "fsm_name": None,
        "fsm_mode": None,
        "balance_mode": None,
        "stand_height": None,
        "swing_height": None,
        "arm_ready": False,
        "message": "",
    }

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    try:
        msc = get_motion_switcher()
        code, form = msc.CheckMode()
        if code == 0:
            result["mode"] = form
        else:
            # CheckMode rc != 0. Often rc=3104 (timeout) when MotionSwitcher
            # service is unresponsive but the robot is otherwise fine — see
            # ensure_ai_mode() in _g1_common.py for the same workaround.
            # Surface the rc so the user sees an actionable error rather
            # than a silent "mode=?".
            result["mode_rc"] = code
            logger.debug("CheckMode returned rc=%s form=%r", code, form)
    except Exception as e:
        result["message"] = f"MotionSwitcher error: {e}"
        return _normalize(result)

    try:
        loco = get_loco_client()
        fsm_id = read_fsm_id(loco)
        result["fsm_id"] = fsm_id
        result["fsm_name"] = FSM_NAMES.get(fsm_id, "unknown")

        # Short-circuit: if FSM read timed out (rc=3104), the loco RPC service
        # is wedged. Skip the next four RPCs (fsm_mode, balance, stand, swing) —
        # they would each take ~3s to time out and rebuild the client uselessly.
        # Fall back to LowState mode_machine for arm_ready.
        if fsm_id is None and last_loco_rc(7001) == 3104:
            mm = _read_mode_machine_from_lowstate()
            result["mode_machine"] = mm
            result["arm_ready"] = mm in ARM_READY_MODE_MACHINES
            result["short_circuited"] = True
        else:
            result["fsm_mode"] = read_fsm_mode(loco)
            result["balance_mode"] = read_balance_mode(loco)
            result["stand_height"] = read_stand_height(loco)
            result["swing_height"] = read_swing_height(loco)
            result["arm_ready"] = fsm_id in HANDSHAKE_FSMS
    except Exception as e:
        result["message"] = f"LocoClient error: {e}"
        return _normalize(result)

    result["status"] = "success"
    mode_name = result["mode"].get("name") if isinstance(result["mode"], dict) else "?"

    # If the FSM read came back None, surface the underlying rc so the
    # message is actionable rather than "FSM=None (unknown)".
    if fsm_id is None:
        rc = last_loco_rc(7001)
        rc_str = decode_code(rc) if rc is not None else "no-call"
        result["fsm_rc"] = rc
        if result.get("short_circuited"):
            mm = result.get("mode_machine")
            ar = result.get("arm_ready", False)
            result["message"] = (
                f"mode={mode_name} FSM=None (loco RPC wedged, rc={rc_str}); "
                f"mode_machine={mm} arm_ready={ar} (via LowState fallback). "
                "Loco RPC service may be stuck — motion via FSM may fail, "
                "but arm/walk gates can use the LowState path."
            )
        else:
            result["message"] = (
                f"mode={mode_name} FSM=None (read failed, rc={rc_str}) "
                f"arm_ready=False — RPC may be wedged; client was auto-rebuilt, retry."
            )
    else:
        result["message"] = (
            f"mode={mode_name} FSM={fsm_id} ({result['fsm_name']}) "
            f"arm_ready={result['arm_ready']}"
        )
    return _normalize(result)


@tool
def g1_read_lowstate(network_interface: str = "eth0", timeout: float = 3.0) -> Dict[str, Any]:
    """
    🔍 Read raw LowState: IMU rpy, joint angles, torques, posture estimate.

    READ-ONLY. Subscribes to rt/lowstate for one message, then returns.
    Useful for checking physical posture before attempting transitions.

    Args:
        network_interface: DDS interface. Default 'eth0'.
        timeout: Seconds to wait for a LowState message. Default 3.0.

    Returns:
        Dict with imu (rpy), legs (hip/knee angles), posture heuristic,
        max_tau (leg torque), mode_machine, tick.
    """
    result: Dict[str, Any] = {"status": "error", "message": ""}

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    m = _get_lowstate_cached(timeout=timeout)
    if m is None:
        result["message"] = f"No LowState received within {timeout}s"
        return _normalize(result)

    try:
        q = [ms.q for ms in m.motor_state[:12]]
        L_hip_pitch, L_knee = q[0], q[3]
        R_hip_pitch, R_knee = q[6], q[9]
        avg_knee = (abs(L_knee) + abs(R_knee)) / 2
        if avg_knee < 0.4:
            posture = "STANDING"
        elif avg_knee < 1.2:
            posture = "SQUATTING / BENT"
        else:
            posture = "SITTING / DEEP_SQUAT"

        max_tau = max(abs(ms.tau_est) for ms in m.motor_state[:12])

        result.update({
            "status": "success",
            "mode_machine": m.mode_machine,
            "mode_pr": m.mode_pr,
            "tick": m.tick,
            "imu_rpy": [round(float(r), 4) for r in m.imu_state.rpy],
            "legs": {
                "L_hip_pitch": round(L_hip_pitch, 4),
                "L_knee": round(L_knee, 4),
                "R_hip_pitch": round(R_hip_pitch, 4),
                "R_knee": round(R_knee, 4),
                "avg_knee": round(avg_knee, 4),
            },
            "posture": posture,
            "max_leg_tau": round(max_tau, 2),
            "message": f"posture={posture} avg_knee={avg_knee:.3f} max_tau={max_tau:.2f}",
        })
    except Exception as e:
        result["message"] = f"Parse error: {e}"

    return _normalize(result)


@tool
def g1_list_fsm_states() -> Dict[str, Any]:
    """
    📋 List all known G1 FSM ids and their meanings.

    Pure reference — no robot interaction. Useful to know what to pass
    to use_unitree("loco", "SetFsmId", {"fsm_id": <id>}).

    Returns:
        Dict with 'states' (id -> name) and 'arm_ready_fsms' (the safe ones).
    """
    return _normalize({
        "status": "success",
        "states": FSM_NAMES,
        "arm_ready_fsms": sorted(HANDSHAKE_FSMS),
        "message": (
            "Arm actions (handshake, wave, etc) only work when FSM ∈ "
            f"{sorted(HANDSHAKE_FSMS)}. Use use_unitree('loco', 'SetFsmId', "
            "{'fsm_id': 500}) to enter ready state, fsm_id 1 for Damp (safe limp)."
        ),
    })
