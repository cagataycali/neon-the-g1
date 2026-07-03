"""Battery / BMS state tool.

Subscribes to `rt/lf/bmsstate` (with fallback topic names across firmware
revisions) and exposes the latest SOC/voltage/current as an @tool.

Extracted from inline code that previously lived in `agent.py` so it's
available to other callers and the LLM via normal tool dispatch.
"""
from __future__ import annotations

import time
import threading
from typing import Dict, Any, Optional

from strands import tool

from ._g1_common import ensure_dds, _normalize, _DDS_INIT_LOCK


_BMS_LOCK = threading.Lock()
_BMS_CACHE: Dict[str, Any] = {"last": None, "ts": 0.0, "sub": None}

# Candidate BMS DDS topics seen across G1 firmware versions
_BMS_TOPICS = ("rt/lf/bmsstate", "rt/bmsstate", "rt/bms_state")


def _parse_bms(m) -> Dict[str, Any]:
    """Convert a BmsState_ message into a plain dict."""
    vraw = list(m.bmsvoltage) if hasattr(m, "bmsvoltage") else []
    v_total_mv = vraw[0] if vraw else 0
    voltage_v = (
        round(v_total_mv / 1000.0, 2) if v_total_mv > 1000
        else round(float(v_total_mv), 2)
    )
    return _normalize({
        "status": "success",
        "soc_pct": int(m.soc),
        "soh_pct": int(m.soh),
        "voltage_v": voltage_v,
        "current_a": round(m.current / 1000.0, 2),  # mA → A
        "cycle": int(m.cycle),
        "temp_max_c": int(max(m.temperature)) if len(m.temperature) else None,
        "voltage_cells": vraw,
    })


def _ensure_subscriber() -> Optional[str]:
    """Start a long-lived BMS subscriber if we don't have one yet.

    Returns None on success or an error string.
    """
    if _BMS_CACHE.get("sub") is not None:
        return None

    try:
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.unitree_hg.msg.dds_ import BmsState_
    except Exception as e:
        return f"SDK import failed: {e}"

    def _cb(m):
        with _BMS_LOCK:
            _BMS_CACHE["last"] = _parse_bms(m)
            _BMS_CACHE["ts"] = time.time()

    for topic in _BMS_TOPICS:
        try:
            with _DDS_INIT_LOCK:
                sub = ChannelSubscriber(topic, BmsState_)
                sub.Init(_cb, 2)
            _BMS_CACHE["sub"] = sub
            _BMS_CACHE["topic"] = topic
            return None
        except Exception:
            continue

    return f"No BMS topic available (tried {list(_BMS_TOPICS)})"


@tool
def g1_battery(
    timeout: float = 1.5,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Read current battery / BMS state of the G1.

    Returns SOC%, SOH%, pack voltage (V), pack current (A), charge cycle
    count, and max cell temperature. Safe, read-only. Uses a cached
    subscriber so repeated calls are cheap.

    Args:
        timeout: Seconds to wait for a fresh BMS message if cache is empty.
        network_interface: DDS interface (default 'eth0').

    Returns:
        dict with status/soc_pct/soh_pct/voltage_v/current_a/cycle/
        temp_max_c, plus `stale_s` when the value is older than `timeout`.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})

    err = _ensure_subscriber()
    if err:
        return _normalize({"status": "error", "message": err})

    # Fast path: return cache if fresh
    with _BMS_LOCK:
        cached = _BMS_CACHE.get("last")
        cached_ts = _BMS_CACHE.get("ts", 0.0)

    if cached and (time.time() - cached_ts) < timeout:
        out = dict(cached)
        out["topic"] = _BMS_CACHE.get("topic")
        return _normalize(out)

    # Slow path: wait up to `timeout` for a fresh message
    t0 = time.time()
    while time.time() - t0 < timeout:
        with _BMS_LOCK:
            ts = _BMS_CACHE.get("ts", 0.0)
            if ts > t0:
                result = dict(_BMS_CACHE["last"])
                result["topic"] = _BMS_CACHE.get("topic")
                return _normalize(result)
        time.sleep(0.05)

    # Fall back to stale cache if we have one
    with _BMS_LOCK:
        if _BMS_CACHE.get("last") is not None:
            result = dict(_BMS_CACHE["last"])
            result["stale_s"] = round(time.time() - _BMS_CACHE["ts"], 1)
            result["topic"] = _BMS_CACHE.get("topic")
            return _normalize(result)

    return _normalize({"status": "error", "message": f"no BMS msg in {timeout}s"})
