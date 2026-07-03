"""Mainboard & pressure-sensor state tools.

Subscribes to `rt/mainboardstate` (fans, board temps) and
`rt/pressuresensorstate` (pressure sensors) and exposes them as @tools.

Yoinked in spirit from `state_monitor.py` in the server codebase but
adapted to the cached-singleton pattern used by the rest of neon.
"""
from __future__ import annotations

import time
import threading
from typing import Dict, Any, Optional, List

from strands import tool

from ._g1_common import ensure_dds, _normalize, _DDS_INIT_LOCK


_LOCK = threading.Lock()
_CACHE: Dict[str, Any] = {
    "mb": {"last": None, "ts": 0.0, "sub": None},
    "ps": {"last": None, "ts": 0.0, "sub": None},
}


def _ensure_mainboard_sub() -> Optional[str]:
    if _CACHE["mb"]["sub"] is not None:
        return None
    try:
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import MainBoardState_
    except Exception as e:
        return f"SDK import failed: {e}"

    def _cb(m):
        with _LOCK:
            _CACHE["mb"]["last"] = m
            _CACHE["mb"]["ts"] = time.time()

    try:
        with _DDS_INIT_LOCK:
            sub = ChannelSubscriber("rt/mainboardstate", MainBoardState_)
            sub.Init(_cb, 2)
        _CACHE["mb"]["sub"] = sub
        return None
    except Exception as e:
        return f"MainBoardState_ subscribe failed: {e}"


def _ensure_pressure_sub() -> Optional[str]:
    if _CACHE["ps"]["sub"] is not None:
        return None
    try:
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import PressSensorState_
    except Exception as e:
        return f"SDK import failed: {e}"

    def _cb(m):
        with _LOCK:
            _CACHE["ps"]["last"] = m
            _CACHE["ps"]["ts"] = time.time()

    try:
        with _DDS_INIT_LOCK:
            sub = ChannelSubscriber("rt/pressuresensorstate", PressSensorState_)
            sub.Init(_cb, 2)
        _CACHE["ps"]["sub"] = sub
        return None
    except Exception as e:
        return f"PressSensorState_ subscribe failed: {e}"


def _mb_to_dict(m) -> Dict[str, Any]:
    """Best-effort MainBoardState_ → dict. Field names vary across FW."""
    out: Dict[str, Any] = {"status": "success"}
    for name in ("fan_state", "fan_speed", "temperature", "cpu_temperature",
                 "sys_state", "sys_bat_state", "bms_state", "tick"):
        if hasattr(m, name):
            val = getattr(m, name)
            try:
                out[name] = list(val) if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)) else (int(val) if isinstance(val, (int, bool)) else float(val))
            except Exception:
                out[name] = str(val)
    return _normalize(out)


def _ps_to_dict(m) -> Dict[str, Any]:
    """Best-effort PressSensorState_ → dict."""
    out: Dict[str, Any] = {"status": "success"}
    for name in ("pressure", "temperature", "voltage", "timestamp", "tick"):
        if hasattr(m, name):
            val = getattr(m, name)
            try:
                out[name] = list(val) if hasattr(val, "__iter__") and not isinstance(val, (str, bytes)) else (int(val) if isinstance(val, (int, bool)) else float(val))
            except Exception:
                out[name] = str(val)
    return _normalize(out)


@tool
def g1_mainboard(
    timeout: float = 1.5,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Read current mainboard state (fans, board temperatures, CPU temp).

    Safe, read-only. Fields returned depend on firmware version.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    err = _ensure_mainboard_sub()
    if err:
        return _normalize({"status": "error", "message": err})

    t0 = time.time()
    while time.time() - t0 < timeout:
        with _LOCK:
            m = _CACHE["mb"]["last"]
            ts = _CACHE["mb"]["ts"]
        if m is not None and ts >= t0 - timeout:
            out = _mb_to_dict(m)
            out["age_s"] = round(time.time() - ts, 2)
            return _normalize(out)
        time.sleep(0.05)

    with _LOCK:
        m = _CACHE["mb"]["last"]
        ts = _CACHE["mb"]["ts"]
    if m is not None:
        out = _mb_to_dict(m)
        out["stale_s"] = round(time.time() - ts, 1)
        return _normalize(out)

    return _normalize({"status": "error", "message": f"no MainBoardState in {timeout}s"})


@tool
def g1_pressure(
    timeout: float = 1.5,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Read current pressure-sensor state (feet/ground contact).

    Safe, read-only. Array indices map to sensor positions per SDK docs.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    err = _ensure_pressure_sub()
    if err:
        return _normalize({"status": "error", "message": err})

    t0 = time.time()
    while time.time() - t0 < timeout:
        with _LOCK:
            m = _CACHE["ps"]["last"]
            ts = _CACHE["ps"]["ts"]
        if m is not None and ts >= t0 - timeout:
            out = _ps_to_dict(m)
            out["age_s"] = round(time.time() - ts, 2)
            return _normalize(out)
        time.sleep(0.05)

    with _LOCK:
        m = _CACHE["ps"]["last"]
        ts = _CACHE["ps"]["ts"]
    if m is not None:
        out = _ps_to_dict(m)
        out["stale_s"] = round(time.time() - ts, 1)
        return _normalize(out)

    return _normalize({"status": "error", "message": f"no PressSensorState in {timeout}s"})
