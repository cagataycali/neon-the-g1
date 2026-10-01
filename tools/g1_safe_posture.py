"""Safe compound posture tools — Damp-preamble transitions.

⚠️⚠️⚠️ CRITICAL OPERATIONAL NOTES (2026-05-19 incident review):
=================================================================
These tools are NOT general-purpose "recover from any pose" helpers.
They are designed for transitions from a KNOWN GOOD pose:

  g1_safe_squat_to_stand:  use ONLY when robot is in FSM 3 (Sit) OR
                           already in a controller-managed squat
                           (FSM ∈ {3, 706}). NOT when avg_knee > 1.0
                           and FSM is None — Damp will just collapse it.

  g1_safe_lie_to_stand:    use ONLY from face-up lying (FSM 702-eligible).

  g1_safe_stand_to_squat:  use ONLY from FSM 500/501/801 with the robot
                           upright and stable.

The Damp() preamble exists to SMOOTH a controller→controller handoff,
not to wake an uncontrolled robot. A robot that's NOT actively held by
a controller will COLLAPSE further when Damp fires.

What you probably want when robot is in an unknown pose:
  • avg_knee > ~1.0 (deep squat, sagging): the robot's controller has
    let go. Damping won't help. Issue ``g1_set_fsm(706)`` directly
    (Squat2StandUp) to ASK the controller to lift it. If the RPC
    times out (rc=3104), the controller process is dead — physical
    intervention required (power-cycle the loco service, re-engage
    the remote, gantry support).
  • avg_knee < 0.4 (standing): nothing to recover from.
  • In between (~0.4–1.0): bent-knee balance. ``g1_set_fsm(500)`` will
    retract to Start.
=================================================================

These tools are extracted from the upstream server's LocoController
"Safe*" pattern, where they were ALWAYS preceded by a state check.
We now bake that check in — see ``_assert_safe_for_damp``.
"""
from __future__ import annotations

import time
from typing import Dict, Any, Optional

from strands import tool

from ._g1_common import (
    ensure_dds, get_loco_client, decode_code, read_fsm_id,
    _get_lowstate_cached, _normalize,
)


def _read_avg_knee(timeout: float = 1.0) -> Optional[float]:
    """Quick LowState read of the average knee angle.

    Uses the shared LowState cache. Returns None if LowState isn't
    reachable (robot off / DDS dead).
    """
    m = _get_lowstate_cached(timeout=timeout)
    if m is None:
        return None
    try:
        q = [ms.q for ms in m.motor_state[:12]]
        return (abs(q[3]) + abs(q[9])) / 2  # left knee + right knee
    except Exception:
        return None


def _assert_safe_for_damp(
    expected_fsms: set[int],
    pose_check: bool = True,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Refuse to issue Damp() unless the robot is in a controller-managed FSM
    or the caller has set ``force=True``.

    Returns None if safe to proceed, or a Strands error dict if NOT safe.
    """
    if force:
        return None

    fsm = read_fsm_id()
    if fsm is None:
        return _normalize({
            "status": "error",
            "message": (
                "Refusing Damp preamble: FSM is unknown (RPC didn't respond). "
                "The loco controller may be dead or unreachable. Damping a "
                "non-actively-controlled robot causes it to COLLAPSE. "
                "Power-cycle the controller / use the remote to re-engage, "
                "or pass force=True if you really know the robot is balanced."
            ),
            "fsm": None,
            "remediation": [
                "Check the robot is on a gantry or has manual support",
                "Power-cycle the loco controller / press 'L1+A' on the remote",
                "Re-run g1_get_state() until FSM != None",
                "If you must proceed, call again with force=True",
            ],
        })

    if fsm not in expected_fsms:
        return _normalize({
            "status": "error",
            "message": (
                f"Refusing Damp preamble: FSM={fsm} is not in {sorted(expected_fsms)}. "
                "Damping outside a controller-managed state risks collapse."
            ),
            "fsm": fsm,
            "expected_fsms": sorted(expected_fsms),
            "remediation": [
                f"Use g1_set_fsm() to enter one of {sorted(expected_fsms)} first",
                "Or skip the Damp preamble: call the bare loco operation via "
                "use_unitree(service='loco', operation='Squat2StandUp', parameters={})",
                "Or pass force=True if you accept the risk",
            ],
        })

    if pose_check:
        avg_knee = _read_avg_knee()
        if avg_knee is not None and avg_knee > 1.4:
            return _normalize({
                "status": "error",
                "message": (
                    f"Refusing Damp preamble: avg_knee={avg_knee:.2f} rad "
                    "(deep squat / sagging). The robot is likely already "
                    "uncontrolled — Damp will not help, and Squat2StandUp "
                    "may not have enough range to recover."
                ),
                "avg_knee": round(avg_knee, 3),
                "remediation": [
                    "Lift the robot manually onto its gantry until it's mid-squat",
                    "Use the remote (L2+A) to put it in Sit (FSM 3)",
                    "Then call this tool again",
                    "Or pass force=True with full physical support in place",
                ],
            })
    return None


@tool
def g1_safe_squat_to_stand(
    preamble_s: float = 0.5,
    network_interface: str = "eth0",
    force: bool = False,
) -> Dict[str, Any]:
    """Damp briefly, then Squat2StandUp. Safer variant for SQUAT→STAND only.

    🚨 PRECONDITIONS (enforced unless ``force=True``):
       • FSM ∈ {3 (Sit), 4 (StandUp), 706 (Squat2StandUp)}  — controller active
       • avg_knee ≤ 1.4 rad — robot not collapsed

    🚨 If the robot is in an unknown FSM (RPC silent) or in a deep
        uncontrolled squat (avg_knee > 1.4), this tool will REFUSE to fire
        rather than risk collapsing the robot further. See remediation in
        the error response.

    For "the robot has fallen / is sagging and I need to bring it up":
       1. Get physical support (gantry, hands)
       2. Use the remote to enter FSM 3 (Sit) manually
       3. Then call this tool

    Args:
        preamble_s: How long to hold Damp before issuing Squat2StandUp.
        network_interface: DDS interface (default 'eth0').
        force: Bypass safety checks. ONLY use with the robot physically
               supported. Default False.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})

    safety_err = _assert_safe_for_damp(
        expected_fsms={3, 4, 706}, pose_check=True, force=force,
    )
    if safety_err:
        return safety_err

    loco = get_loco_client()
    try:
        loco.Damp()
        time.sleep(max(0.0, preamble_s))
        loco.Squat2StandUp()
        return _normalize({
            "status": "success",
            "message": f"Damp → sleep({preamble_s}s) → Squat2StandUp dispatched",
        })
    except Exception as e:
        return _normalize({"status": "error", "message": f"SafeSquat2StandUp failed: {e}"})


@tool
def g1_safe_lie_to_stand(
    preamble_s: float = 0.5,
    network_interface: str = "eth0",
    force: bool = False,
) -> Dict[str, Any]:
    """Damp briefly, then Lie2StandUp. Safer variant for FACE-UP→STAND only.

    🚨 PRECONDITIONS (enforced unless ``force=True``):
       • FSM ∈ {1 (Damp), 702 (Lie2StandUp)}  — controller active
       • Robot is face-up on the floor (NOT face-down, NOT side)

    🚨 Damping a robot that is NOT actively held by a controller (FSM=None)
        will leave it limp. From a face-up lying pose this is usually fine
        (it's already on the floor) — but we still gate the FSM to avoid
        firing during a fall.

    Args:
        preamble_s: Hold Damp before issuing Lie2StandUp.
        network_interface: DDS interface (default 'eth0').
        force: Bypass FSM check. ONLY use when the robot is confirmed
               face-up on a flat surface. Default False.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})

    safety_err = _assert_safe_for_damp(
        expected_fsms={1, 702}, pose_check=False, force=force,
    )
    if safety_err:
        return safety_err

    loco = get_loco_client()
    try:
        loco.Damp()
        time.sleep(max(0.0, preamble_s))
        loco.Lie2StandUp()
        return _normalize({
            "status": "success",
            "message": f"Damp → sleep({preamble_s}s) → Lie2StandUp dispatched",
        })
    except Exception as e:
        return _normalize({"status": "error", "message": f"SafeLie2StandUp failed: {e}"})


@tool
def g1_safe_stand_to_squat(
    preamble_s: float = 0.5,
    network_interface: str = "eth0",
    force: bool = False,
) -> Dict[str, Any]:
    """Damp briefly, then transition to squat (FSM 2). STAND→SQUAT only.

    🚨 PRECONDITIONS (enforced unless ``force=True``):
       • FSM ∈ {500 (Start), 501 (Walk), 801 (BalanceExpert)}  — upright
       • Robot is actively balancing

    ⚠️ SDK quirk: ``LocoClient.StandUp2Squat()`` has a bug — upstream it
       calls ``SetFsmId(706)`` which is actually *Squat2StandUp*. We work
       around by calling ``SetFsmId(2)`` (Squat) directly.

    Args:
        preamble_s: Hold Damp before transitioning.
        network_interface: DDS interface (default 'eth0').
        force: Bypass FSM check. Default False.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})

    safety_err = _assert_safe_for_damp(
        expected_fsms={500, 501, 801}, pose_check=False, force=force,
    )
    if safety_err:
        return safety_err

    loco = get_loco_client()
    try:
        loco.Damp()
        time.sleep(max(0.0, preamble_s))
        # Work around SDK bug: StandUp2Squat() goes to FSM 706 which is stand-up!
        # Use SetFsmId(2) to enter Squat directly.
        rc = loco.SetFsmId(2)
        return _normalize({
            "status": "success" if rc == 0 else "error",
            "rc": rc,
            "message": f"Damp → sleep({preamble_s}s) → SetFsmId(2=Squat) rc={rc}",
        })
    except Exception as e:
        return _normalize({"status": "error", "message": f"SafeStandUp2Squat failed: {e}"})
