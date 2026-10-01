"""
🚶 G1 locomotion tools — walking, turning, velocity control.

🚨🚨🚨 DANGER: these tools make the robot WALK.
Only run when:
  - robot is in an open, safe area (no obstacles, no people nearby)
  - OR robot is on a gantry with wheels free
  - emergency stop is within reach
Per user instruction: DO NOT run walking tests automatically.
Prefer g1_stop_move() to halt.
"""
import math
import time
from typing import Dict, Any, Optional

from strands import tool

from ._g1_common import (
    ensure_dds, get_loco_client, read_fsm_id, decode_code, WALK_FSMS,
    _read_mode_machine_from_lowstate, ARM_READY_MODE_MACHINES,
    _normalize, read_pose, pose_delta,
)

# Honesty thresholds: a command "moved" the robot only when the odometry
# (rt/odommodestate) shows at least this much, or a quarter of the request.
MIN_WALK_M = 0.1          # a request below this is rounded up: 5 cm produces nothing visible
MAX_WALK_M = 1.0          # per request
MIN_SPEED_SHORT = 0.15    # m/s floor when the request is <= 0.3 m
MOVED_FLOOR_M = 0.05
MOVED_FLOOR_RAD = 0.05
SETTLE_S = 0.5            # wait after the commanded duration before measuring
MAX_WAIT_S = 10.0

_sleep = time.sleep       # patched by tests


def _moved(measured: Optional[float], requested: float, floor: float) -> Optional[bool]:
    """None = could not measure; otherwise did the robot cover enough of the request."""
    if measured is None:
        return None
    return abs(measured) >= max(floor, 0.25 * abs(requested))


def _fmt(v: Optional[float], unit: str) -> str:
    return "unknown" if v is None else f"{abs(v):.2f} {unit}"


@tool
def g1_move_velocity(
    vx: float,
    vy: float,
    vyaw: float,
    duration: float = 1.0,
    continuous: bool = False,
    network_interface: str = "eth0",
    force: bool = False,
) -> Dict[str, Any]:
    """
    🚶 Command G1 to move at a given velocity for a duration, then REPORT
    what the robot actually did (odometry before/after).

    🚨 DANGER: causes the robot to WALK. Requires FSM ∈ {501, 801}.
    Defaults to single 1-second impulse for safety.

    The result says moved=true/false and "moved 0.28 m" or "SDK accepted
    (rc=0) but no displacement measured". Never tell the user the robot
    moved unless moved is true.

    Args:
        vx: forward velocity (m/s). Typical safe: 0.1–0.3. Max ~0.8.
        vy: lateral velocity (m/s). Typical: ±0.1–0.2.
        vyaw: rotation rate (rad/s). Typical: ±0.3.
        duration: seconds to keep velocity. Default 1.0. Ignored if continuous.
        continuous: if True, set duration to ~10 days (until explicitly stopped). Not measured.
        network_interface: DDS interface. Default 'eth0'.
        force: bypass FSM safety check. Default False (RECOMMENDED).

    Returns dict with rc, fsm_before, moved, requested_m, measured_m,
    requested_rad, measured_rad, message.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": "",
                             "fsm_before": None}

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    loco = get_loco_client()
    fsm = read_fsm_id(loco)
    result["fsm_before"] = fsm

    # If FSM RPC is wedged (rc=3104), check mode_machine from LowState as
    # a second source of truth. mode_machine ∈ {5, 6} means the robot is
    # in a balance / walk-ready state — same condition as arm-ready.
    fsm_for_gate = fsm
    if fsm is None:
        mm = _read_mode_machine_from_lowstate()
        if mm in ARM_READY_MODE_MACHINES:
            result["mode_machine"] = mm
            result["message_note"] = (
                f"FSM RPC wedged but mode_machine={mm} (walk-ready via LowState). "
                "Proceeding."
            )
            # Treat as walk-ready for the gate
            fsm_for_gate = 501

    if not force and fsm_for_gate not in WALK_FSMS:
        result["message"] = (
            f"FSM={fsm} (mode_machine fallback={result.get('mode_machine')}) "
            f"not in {sorted(WALK_FSMS)}. Walking requires 501. "
            "Call g1_stand() first, or set force=True. ABORTING for safety."
        )
        return _normalize(result)

    # Clamp duration for safety: a runaway value walks the robot for hours.
    if not continuous:
        duration = max(0.0, min(10.0, float(duration)))
        if duration <= 0:
            result["message"] = "duration<=0 (non-continuous), refusing"
            return _normalize(result)

    before = None if continuous else read_pose(timeout=1.0)
    try:
        if continuous:
            loco.Move(vx, vy, vyaw, continous_move=True)
            rc = 0
        else:
            rc = loco.SetVelocity(float(vx), float(vy), float(vyaw), float(duration))
    except Exception as e:
        result["message"] = f"SetVelocity raised: {e}"
        return _normalize(result)

    result["rc"] = rc
    head = (f"SetVelocity(vx={vx}, vy={vy}, vyaw={vyaw}, "
            f"dur={'∞' if continuous else duration}) rc={decode_code(rc)}")
    if continuous or rc != 0:
        result["status"] = "success" if rc == 0 else "error"
        result["message"] = head if rc == 0 else f"{head}: the robot did not move"
        return _normalize(result)

    # Measure: wait for the command to play out, then compare poses.
    _sleep(min(float(duration) + SETTLE_S, MAX_WAIT_S))
    after = read_pose(timeout=1.0)
    delta = pose_delta(before, after)
    requested_m = round(math.hypot(vx, vy) * duration, 3)
    requested_rad = round(vyaw * duration, 3)
    result.update({
        "requested_m": requested_m, "requested_rad": requested_rad,
        "measured_m": delta["measured_m"], "measured_rad": delta["measured_rad"],
        "odom_source": delta["source"],
    })
    moved_lin = _moved(delta["measured_m"], requested_m, MOVED_FLOOR_M) if requested_m > 0 else None
    moved_rot = _moved(delta["measured_rad"], requested_rad, MOVED_FLOOR_RAD) if requested_rad != 0 else None
    verdicts = [v for v in (moved_lin, moved_rot) if v is not None]
    moved: Optional[bool] = (all(verdicts) if verdicts else None)
    result["moved"] = moved

    parts = []
    if requested_m > 0:
        parts.append(f"{_fmt(delta['measured_m'], 'm')} of {requested_m:.2f} m requested")
    if requested_rad != 0:
        parts.append(f"{_fmt(delta['measured_rad'], 'rad')} of {abs(requested_rad):.2f} rad requested")
    detail = ", ".join(parts)
    if moved is True:
        result["status"] = "success"
        result["message"] = f"moved {detail} ({head})"
    elif moved is False:
        result["status"] = "error"
        result["message"] = (f"SDK accepted (rc=0) but no displacement measured: {detail}. "
                             f"The robot did NOT move; tell the user so. ({head})")
    else:
        result["status"] = "success"
        result["message"] = (f"SDK accepted (rc=0) but motion could not be verified: "
                             f"no odometry sample ({detail or 'nothing requested'}). "
                             f"Do not claim the robot moved. ({head})")
    return _normalize(result)


@tool
def g1_stop_move(network_interface: str = "eth0") -> Dict[str, Any]:
    """
    🛑 Stop all velocity commands (sets vx=vy=vyaw=0).

    SAFE to call anytime — doesn't change FSM, just kills movement.
    Use this as emergency stop for walking.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    loco = get_loco_client()
    try:
        loco.StopMove()
        result["rc"] = 0
        result["status"] = "success"
        result["message"] = "StopMove issued (vx=vy=vyaw=0)"
    except Exception as e:
        result["message"] = f"StopMove raised: {e}"
    return _normalize(result)


@tool
def g1_walk_forward(
    distance: float = 0.3,
    speed: float = 0.2,
    network_interface: str = "eth0",
    force: bool = False,
) -> Dict[str, Any]:
    """
    🚶 Walk forward `distance` metres and REPORT the measured displacement.

    🚨 DANGER: causes walking. Requires FSM 501. Look first (take_photo).
    The result is honest: moved=true with "moved 0.28 m", or moved=false with
    "SDK accepted (rc=0) but no displacement measured". Only say the robot
    walked when moved is true; when false, read the message to the user.

    Args:
        distance: metres to travel (negative = backwards). Default 0.3.
            Clamped to [0.1, 1.0] in magnitude: below 0.1 m nothing visible happens.
        speed: forward velocity in m/s. Default 0.2. Clamped [0.05, 0.5];
            at least 0.15 m/s when the distance is 0.3 m or less.
        force: bypass FSM check.
    """
    d = abs(float(distance))
    d = max(MIN_WALK_M, min(MAX_WALK_M, d))
    sign = 1.0 if float(distance) >= 0 else -1.0
    speed = max(0.05, min(0.5, abs(float(speed))))
    if d <= 0.3:
        speed = max(speed, MIN_SPEED_SHORT)
    duration = d / speed
    return g1_move_velocity(
        vx=sign * speed, vy=0.0, vyaw=0.0, duration=duration,
        continuous=False, network_interface=network_interface, force=force,
    )


@tool
def g1_turn(
    angle_rad: float,
    yaw_rate: float = 0.3,
    network_interface: str = "eth0",
    force: bool = False,
) -> Dict[str, Any]:
    """
    🔄 Turn in place by approximately `angle_rad` radians and REPORT the
    measured yaw change (moved=true/false, "turned 0.48 rad").

    🚨 DANGER: turning in place still causes leg motion. Requires FSM 501.

    Args:
        angle_rad: rotation in radians (positive = CCW). Clamped ±2π.
        yaw_rate: rotation speed rad/s. Default 0.3. Clamped [0.1, 0.6].
        force: bypass FSM check.
    """
    angle_rad = max(-2 * math.pi, min(2 * math.pi, float(angle_rad)))
    yaw_rate = max(0.1, min(0.6, abs(float(yaw_rate))))
    vyaw = yaw_rate if angle_rad >= 0 else -yaw_rate
    duration = abs(angle_rad) / yaw_rate
    return g1_move_velocity(
        vx=0.0, vy=0.0, vyaw=vyaw, duration=duration,
        continuous=False, network_interface=network_interface, force=force,
    )


@tool
def g1_wave_hand_loco(
    turn: bool = False,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    👋 Built-in loco 'wave hand' task (LocoClient.WaveHand).

    This is a DIFFERENT path than the arm-action-based waves.
    Uses task id 0 (no turn) or 1 (wave + turn around).
    Does not require FSM 500+ because it uses SetTaskId, but behavior
    depends on current FSM.

    Args:
        turn: if True, robot turns around while waving (task id 1).
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    loco = get_loco_client()
    try:
        loco.WaveHand(turn_flag=bool(turn))
        result["rc"] = 0
        result["status"] = "success"
        result["message"] = f"WaveHand(turn={turn}) issued"
    except Exception as e:
        result["message"] = f"WaveHand raised: {e}"
    return _normalize(result)


@tool
def g1_shake_hand_loco(
    stage: int = -1,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    🤝 Built-in loco 'shake hand' task (LocoClient.ShakeHand).

    Different from the arm-action handshake: this uses SetTaskId and has stages.
    Call twice with 3s between calls to complete the motion.

    Args:
        stage: -1 (toggle internal stage), 0 (reach out), 1 (shake).
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    loco = get_loco_client()
    try:
        rc = loco.ShakeHand(stage=int(stage))
    except Exception as e:
        result["message"] = f"ShakeHand raised: {e}"
        return _normalize(result)
    result["rc"] = rc if rc is not None else 0
    result["status"] = "success"
    result["message"] = f"ShakeHand(stage={stage}) issued, rc={decode_code(result['rc'])}"
    return _normalize(result)



@tool
def g1_set_task_id(
    task_id: int,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    🎯 Raw LocoClient.SetTaskId — dispatches a built-in locomotion task.

    Known task ids (discovered via example scripts):
        0  → WaveHand (no turn)
        1  → WaveHand (with turn)
        2  → ShakeHand stage 1 (reach out)
        3  → ShakeHand stage 2 (shake)

    Prefer g1_wave_hand_loco / g1_shake_hand_loco unless you're exploring
    new task ids. Behavior depends on current FSM.

    Args:
        task_id: integer task id.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    loco = get_loco_client()
    try:
        loco.SetTaskId(int(task_id))
        result["rc"] = 0
        result["status"] = "success"
        result["message"] = f"SetTaskId({task_id}) dispatched"
    except Exception as e:
        result["message"] = f"SetTaskId raised: {e}"
    return _normalize(result)
