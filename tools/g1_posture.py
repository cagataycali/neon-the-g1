"""🧍 G1 posture — tools that add value beyond the raw SDK.

Plain 1:1 SDK calls (Damp, Sit, ZeroTorque, HighStand, ...) are reachable via
``use_unitree(loco, <Method>)``. This module keeps only the ones that do more:
  - g1_set_fsm            round-trips fsm_before/fsm_after (via _Call 7001)
  - g1_set_stand_height   negative → HighStand fallback
  - g1_set_swing_height   wraps the undocumented API-7103
  - g1_balance_stand      convenience wrapper around BalanceStand
"""
from __future__ import annotations

import time
from typing import Any, Dict

from strands import tool

from ._g1_common import (
    decode_code, ensure_dds, get_loco_client, read_fsm_id,
    FSM_NAMES,
    _normalize,
)


@tool
def g1_set_fsm(
    fsm_id: int,
    network_interface: str = "eth0",
    wait: float = 3.0,
) -> Dict[str, Any]:
    """
    🎛️ Set G1 FSM and return fsm_before/fsm_after.

    Why this tool (and not just ``use_unitree(loco, SetFsmId)``)?
    We round-trip via ``_Call(7001, ...)`` after a wait so the LLM sees the
    *actual* resulting FSM (useful when a transition is refused silently).

    Common targets:
        1   = Damp (soft limp; safe rest state on gantry)
        500 = Start (balance stand, arm-ready)
        501 = Walk (arm-ready, ready to accept velocity)
        801 = BalanceExpert
        3   = Sit
        0   = 🚨 ZeroTorque (fully limp — robot COLLAPSES off-gantry)

    Args:
        fsm_id: target FSM id.
        network_interface: DDS interface. Default 'eth0'.
        wait: seconds to wait before reading the new FSM.

    Returns:
        dict with fsm_before, fsm_after, rc, message.
    """
    result: Dict[str, Any] = {
        "status": "error", "fsm_before": None, "fsm_after": None,
        "rc": None, "message": "",
    }

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    loco = get_loco_client()
    result["fsm_before"] = read_fsm_id(loco)

    try:
        rc = loco.SetFsmId(int(fsm_id))
    except Exception as e:
        result["message"] = f"SetFsmId raised: {e}"
        return _normalize(result)

    result["rc"] = rc
    time.sleep(wait)
    result["fsm_after"] = read_fsm_id(loco)
    result["status"] = "success" if rc == 0 else "error"
    result["message"] = (
        f"SetFsmId({fsm_id}={FSM_NAMES.get(fsm_id,'?')}) rc={decode_code(rc)} "
        f"| FSM {result['fsm_before']} -> {result['fsm_after']}"
    )
    return _normalize(result)


@tool
def g1_set_stand_height(
    height: float,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    📏 Set G1 stand height, with negative-value ergonomic fallback.

    Why this tool: the SDK's ``SetStandHeight`` accepts a float in meters,
    but ``HighStand`` uses a ``UINT32_MAX`` sentinel value. We let the LLM
    pass a simple negative number and map that to HighStand.

    Args:
        height: Stand height. Special values:
            0.0        = LOW stand (crouched)
            negative   = HighStand (max)
            0.0..~0.8  = actual meters
        network_interface: DDS interface. Default 'eth0'.

    Returns dict with rc.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    loco = get_loco_client()
    try:
        if height < 0:
            loco.HighStand()
            rc = 0
            label = "HighStand"
        else:
            rc = loco.SetStandHeight(float(height))
            label = f"{height}m"
    except Exception as e:
        result["message"] = f"SetStandHeight raised: {e}"
        return _normalize(result)

    result["rc"] = rc
    result["status"] = "success" if rc == 0 else "error"
    result["message"] = f"SetStandHeight({label}) rc={decode_code(rc)}"
    return _normalize(result)


@tool
def g1_set_swing_height(
    height: float,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    🦵 Set G1 swing height (how high legs lift while walking).

    Not exposed by the Python SDK directly — we call API 7103 via raw _Call.
    Typical safe range: 0.05–0.15 m. Higher = larger step clearance but
    more energy.

    Args:
        height: swing height in meters. Clamped to [0.0, 0.2].
        network_interface: DDS interface. Default 'eth0'.
    """
    height = max(0.0, min(0.2, float(height)))
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}

    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)

    from ._g1_common import set_swing_height as _sh
    loco = get_loco_client()
    rc = _sh(height, loco=loco)
    result["rc"] = rc
    result["status"] = "success" if rc == 0 else "error"
    result["message"] = (
        f"SetSwingHeight({height:.3f}m) "
        f"rc={decode_code(rc) if rc is not None else 'exception'}"
    )
    return _normalize(result)


@tool
def g1_balance_stand(
    balance_mode: int = 0,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    ⚖️ Enter BalanceStand (FSM 801) with given balance_mode.

    Wraps LocoClient.BalanceStand (which internally calls SetBalanceMode).
    To actually reach FSM 801 you may also need ``g1_set_fsm(801)``.

    Args:
        balance_mode: 0 (default / static), 3 (dynamic) — see memory.md.
    """
    result: Dict[str, Any] = {"status": "error", "rc": None, "message": ""}
    err = ensure_dds(network_interface)
    if err:
        result["message"] = err
        return _normalize(result)
    loco = get_loco_client()
    try:
        loco.BalanceStand(int(balance_mode))
        result["rc"] = 0
        result["status"] = "success"
        result["message"] = f"BalanceStand(mode={balance_mode}) dispatched"
    except Exception as e:
        result["message"] = f"BalanceStand raised: {e}"
    return _normalize(result)
