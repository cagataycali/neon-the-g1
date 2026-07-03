"""Generic DDS @tool wrappers — subscribe, snapshot, publish ANY G1 topic.

Lets the agent peek at topics that aren't covered by the other @tools
(e.g. `rt/wirelesscontroller`, `rt/lf/sportmodestate`, `rt/odom`, ...).

Engine lives in `_dds_engine.py` — this module is just the @tool facade.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from strands import tool

from ._g1_common import ensure_dds, _normalize
from ._dds_engine import get_dds


@tool
def g1_dds_list_topics(
    category: str = "",
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """📋 List the curated G1 DDS topic catalog.

    Categories: state, lidar, joystick, control, config.
    Safe, read-only. Doesn't touch the robot.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        topics = get_dds().list_topics(category=category or None)
        return _normalize({"status": "success", "count": len(topics), "topics": topics})
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_discover(
    timeout: float = 5.0,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """🔎 Discover live DDS topics via `cyclonedds ls`.

    Returns the full set of topics currently present on the DDS network —
    useful for finding undocumented topics.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return get_dds().discover(timeout=timeout)
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_snapshot(
    topic: str,
    timeout: float = 2.0,
    type_module: str = "",
    type_class: str = "",
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """📸 Take ONE snapshot of a DDS topic (subscribe-if-needed + wait).

    For catalog topics, type is auto-resolved. For unknown topics, pass
    type_module + type_class.

    Examples:
        g1_dds_snapshot("rt/wirelesscontroller")
        g1_dds_snapshot("rt/lf/sportmodestate")
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return get_dds().snapshot(
            topic=topic, timeout=timeout,
            type_module=type_module or None,
            type_class=type_class or None,
        )
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_subscribe(
    topic: str,
    type_module: str = "",
    type_class: str = "",
    max_buffer: int = 20,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """🔔 Open a long-lived subscription to a DDS topic.

    Messages buffer in the background; call g1_dds_read(topic, n) to retrieve.
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return get_dds().subscribe(
            topic=topic,
            type_module=type_module or None,
            type_class=type_class or None,
            max_buffer=max_buffer,
        )
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_read(
    topic: str,
    n: int = 1,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """📖 Read last N buffered messages from an active DDS subscription."""
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return get_dds().read(topic=topic, n=int(n))
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_unsubscribe(
    topic: str,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """❌ Drop a long-lived DDS subscription."""
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return get_dds().unsubscribe(topic=topic)
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_stats(network_interface: str = "eth0") -> Dict[str, Any]:
    """📊 Show all active DDS subscriptions and publishers with counts + ages."""
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return _normalize({"status": "success", **get_dds().stats()})
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})


@tool
def g1_dds_publish(
    topic: str,
    payload: Optional[Dict[str, Any]] = None,
    type_module: str = "",
    type_class: str = "",
    unsafe: bool = False,
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """📤 Publish a single message to a DDS topic.

    🚨 Dangerous topics (rt/lowcmd, rt/armsdk, rt/user_lowcmd) require
    unsafe=True. Prefer g1_arm_action / g1_move_velocity / etc. instead.

    Example (LiDAR switch):
        g1_dds_publish("rt/utlidar/switch", {"data": "ON"})
    """
    err = ensure_dds(network_interface)
    if err:
        return _normalize({"status": "error", "message": err})
    try:
        return get_dds().publish(
            topic=topic, payload=payload or {},
            type_module=type_module or None,
            type_class=type_class or None,
            unsafe=bool(unsafe),
        )
    except Exception as e:
        return _normalize({"status": "error", "message": str(e)})
