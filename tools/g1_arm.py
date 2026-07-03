"""
💪 G1 arm action tools — generalized gestures + list + release.

Supersets g1_handshake.py — lets you invoke ANY arm action by name or id,
plus list all available actions and force-release the arm.
"""
import threading
import time
from typing import Dict, Any, Optional

from strands import tool

from ._g1_common import (
    ensure_dds, get_arm_client, ensure_ai_mode, ensure_arm_ready_fsm,
    read_fsm_id, decode_code, HANDSHAKE_FSMS,
    _read_mode_machine_from_lowstate, ARM_READY_MODE_MACHINES,
    _normalize,
)

# rt/armsdk is single-writer — parallel ExecuteAction calls return rc=7400.
_ARM_LOCK = threading.Lock()



@tool
def g1_arm_action(
    action: str = "",
    action_id: Optional[int] = None,
    hold_seconds: float = 3.0,
    auto_release: bool = True,
    network_interface: str = "eth0",
    auto_transition: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    💪 Execute any G1 arm action by name OR numeric id.

    🚨 DO NOT CALL THIS IN PARALLEL. Each gesture takes ~hold_seconds (3s default)
    of physical motion. The robot has ONE arm controller (`rt/armsdk` is a single
    writer) — concurrent calls trigger rc=7400 and the gestures interfere
    visually (the user only sees the last one). If you need a sequence of
    gestures, call them one at a time and wait for each to finish.

    Available actions (name → id):
      release arm → 99    two-hand kiss → 11   left kiss → 12
      right kiss → 13     hands up → 15        clap → 17
      high five → 18      hug → 19             heart → 20
      right heart → 21    reject → 22          right hand up → 23
      x-ray → 24          face wave → 25       high wave → 26
      shake hand → 27

    Args:
        action: action name (see list above). Ignored if action_id >= 0.
        action_id: numeric id. Use -1 (default) to use `action` name instead.
        hold_seconds: time to hold pose before release. Default 3.0.
        auto_release: if True, send release arm (99) after hold. Default True.
        network_interface: DDS interface. Default 'eth0'.
        auto_transition: if True, try to reach FSM {500,501,801}. Default True.
        dry_run: check state only, no execution.
    """
    result: Dict[str, Any] = {
        "status": "error", "action": action, "action_id": None,
        "fsm_before": None, "fsm_after": None,
        "execute_rc": None, "release_rc": None, "message": "",
    }

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    # Resolve action id
    try:
        from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map
    except Exception as e:
        result["message"] = f"SDK import failed: {e}"
        return _normalize(result)

    if action_id is not None:
        resolved_id = int(action_id)
        # Find name for reporting
        name = next((k for k, v in action_map.items() if v == resolved_id), f"id={resolved_id}")
        result["action"] = name
    else:
        key = action.strip().lower()
        if key not in action_map:
            result["message"] = f"Unknown action '{action}'. Available: {sorted(action_map.keys())}"
            return _normalize(result)
        resolved_id = action_map[key]

    result["action_id"] = resolved_id

    # Ensure ai mode
    code, form = ensure_ai_mode()
    if code != 0:
        result["message"] = f"MotionSwitcher.CheckMode failed: {decode_code(code)}"
        return _normalize(result)

    # Ensure arm-ready FSM
    fsm_check = ensure_arm_ready_fsm(auto_transition=auto_transition)
    result["fsm_before"] = fsm_check["fsm_before"]
    result["fsm_after"] = fsm_check["fsm_after"]
    if not fsm_check["ok"]:
        result["message"] = fsm_check["message"]
        return _normalize(result)

    if dry_run:
        result["status"] = "dry_run"
        result["message"] = (
            f"DRY RUN — would execute action_id={resolved_id} ('{result['action']}'), "
            f"FSM={result['fsm_after']}"
        )
        return _normalize(result)

    # Execute
    try:
        arm = get_arm_client()
        with _ARM_LOCK:
            rc = arm.ExecuteAction(resolved_id)
    except Exception as e:
        result["message"] = f"ExecuteAction raised: {e}"
        return _normalize(result)

    result["execute_rc"] = rc
    if rc != 0:
        result["message"] = f"ExecuteAction('{result['action']}') rc={decode_code(rc)}"
        return _normalize(result)

    # Hold
    time.sleep(max(0.0, hold_seconds))

    # Release
    if auto_release and resolved_id != action_map["release arm"]:
        try:
            with _ARM_LOCK:
                release_rc = arm.ExecuteAction(action_map["release arm"])
            result["release_rc"] = release_rc
            fsm_after = read_fsm_id()
            if fsm_after is None:
                # FSM RPC busy after action — use LowState mode_machine fallback.
                mm = _read_mode_machine_from_lowstate()
                if mm in ARM_READY_MODE_MACHINES:
                    fsm_after = f"unknown (mode_machine={mm}, arm-ready)"
            result["fsm_after"] = fsm_after
            if release_rc == 0:
                result["status"] = "success"
                result["message"] = (
                    f"✅ '{result['action']}' executed and released. "
                    f"FSM={result['fsm_after']}"
                )
            else:
                result["status"] = "error"
                result["message"] = (
                    f"'{result['action']}' OK but release rc={decode_code(release_rc)}. "
                    f"⚠️ Arm may be holding — call g1_release_arm() to recover."
                )
        except Exception as e:
            result["release_rc"] = f"exception: {e}"
            result["status"] = "error"
            result["message"] = (
                f"'{result['action']}' OK but release threw: {e}. "
                f"Call g1_release_arm() to recover."
            )
    else:
        result["status"] = "success"
        result["message"] = f"✅ '{result['action']}' executed (no release)"

    return _normalize(result)


@tool
def g1_release_arm(network_interface: str = "eth0") -> Dict[str, Any]:
    """
    🆓 Force-release G1 arm (action id 99).

    Use when an earlier arm action left the arm holding.
    Safe to call anytime arm is initialized.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    try:
        arm = get_arm_client()
        with _ARM_LOCK:
            rc = arm.ExecuteAction(99)
    except Exception as e:
        result["message"] = f"ExecuteAction(99) raised: {e}"
        return _normalize(result)
    result["rc"] = rc
    result["status"] = "success" if rc == 0 else "error"
    result["message"] = f"Release arm rc={decode_code(rc)}"
    return _normalize(result)


@tool
def g1_list_arm_actions() -> Dict[str, Any]:
    """
    📋 List all G1 arm actions (name → id).

    Pure reference. Doesn't touch the robot.
    """
    try:
        from unitree_sdk2py.g1.arm.g1_arm_action_client import action_map
    except Exception as e:
        return _normalize({"status": "error", "message": f"SDK import failed: {e}"})
    return _normalize({
        "status": "success",
        "action_map": dict(action_map),
        "count": len(action_map),
        "message": (
            f"{len(action_map)} arm actions available. Arm actions require "
            f"FSM ∈ {sorted(HANDSHAKE_FSMS)}. Always follow with release "
            f"(id=99) or use g1_arm_action(auto_release=True)."
        ),
    })


@tool
def g1_get_arm_action_list_from_robot(
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    📋 Query the robot itself for its supported arm-action list.

    Calls ArmActionClient.GetActionList (API 7107). Useful to verify what
    the firmware actually supports vs the SDK's static action_map.
    """
    result: Dict[str, Any] = {"status": "error", "message": "", "data": None}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    try:
        arm = get_arm_client()
        code, data = arm.GetActionList()
    except Exception as e:
        result["message"] = f"GetActionList raised: {e}"
        return _normalize(result)
    result["rc"] = code
    if code == 0:
        result["status"] = "success"
        result["data"] = data
        result["message"] = f"OK — {len(data) if isinstance(data, (list, dict)) else '?'} entries"
    else:
        result["message"] = f"GetActionList rc={decode_code(code)}"
    return _normalize(result)
