"""NEON robot tool-bundle factory — fuses two control layers into one Agent.

  Layer 1: live DDS control (this repo's ``tools/``) — FSM-gated arms/walk/
           posture/voice, real-time, runs ON the robot.
  Layer 2: strands-robots ``Robot("g1")`` — MuJoCo sim, VLA policies, dataset
           recording, fleet mesh; ``mode="real"`` drives joints via LeRobot.

``neon()`` returns both layers so one ``Agent(tools=neon())`` can gesture/walk
over DDS AND run a sim/policy. See docs/guide/strands-robots.md for the design.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .registry import ensure_registered

log = logging.getLogger("neon.robot")


def make_robot_tool(
    name: str = "g1",
    mode: str = "sim",
    mesh: bool | None = None,
    **kwargs: Any,
):
    """Build the strands-robots ``Robot`` AgentTool for NEON (Layer 2).

    Args:
        name: registry name/alias — "g1" or "neon" (both → unitree_g1).
        mode: "sim" (default, MuJoCo), "real" (LeRobot network driver), or "auto".
        mesh: attach Zenoh fleet mesh (None = honour STRANDS_MESH env).
        **kwargs: forwarded to ``Robot()`` (e.g. robot_ip=..., cameras=...,
                  data_config=..., position=...).

    Returns:
        A ``strands_robots.Robot`` instance (an AgentTool), or ``None`` if
        strands-robots isn't installed (Layer 1 still works standalone).
    """
    ensure_registered()
    try:
        from strands_robots import Robot
    except Exception as e:
        log.warning("strands-robots unavailable — Layer 2 (sim/policy) off: %s", e)
        return None

    try:
        return Robot(name, mode=mode, mesh=mesh, **kwargs)
    except Exception as e:
        log.warning("Robot(%r, mode=%r) failed: %s", name, mode, e)
        return None


def neon(
    *,
    mode: str = "sim",
    name: str = "g1",
    dds: bool = True,
    lookout: bool = True,
    locomotion: bool = True,
    robot_tool: bool = True,
    mesh: bool | None = None,
    **robot_kwargs: Any,
) -> list:
    """Compose NEON's full toolset (Layer 1 DDS + Layer 2 strands-robots).

    Args:
        mode: strands-robots Robot mode — "sim" (default) | "real" | "auto".
        name: robot registry alias for Layer 2 ("g1" or "neon").
        dds: include Layer-1 live DDS robot tools (state/posture/arm/audio/...).
        lookout: include cross-persona stack (memory/voice/telegram/...).
        locomotion: include walking tools (g1_move_velocity, g1_walk_forward, ...).
                    Set False for a no-walk safe bundle.
        robot_tool: include the strands-robots ``Robot`` AgentTool (Layer 2).
        mesh: Zenoh fleet mesh for Layer 2 (None = STRANDS_MESH env default).
        **robot_kwargs: forwarded to ``Robot()`` — e.g.
                        robot_ip="192.168.123.161", data_config=..., cameras=...

    Returns:
        A flat list of Strands tools/AgentTools ready for ``Agent(tools=...)``.

    Example::

        from strands import Agent
        from neon import neon

        # Sim + full DDS + voice/memory in one agent:
        agent = Agent(tools=neon(mode="sim"))

        # Real hardware policy path + live DDS gestures:
        agent = Agent(tools=neon(mode="real", robot_ip="192.168.123.161"))
    """
    tools: list = []

    # ── Layer 1: live DDS + cross-persona (this repo's tools/) ──────────
    if dds or lookout or locomotion:
        try:
            import tools as _t  # the flat tools/ package shipped with this repo

            if dds:
                # Everything EXCEPT walking (state, posture, arm, audio, sensing,
                # use_unitree universal hammer).
                tools.extend(getattr(_t, "G1_SAFE_TOOLS", []))
            if locomotion:
                tools.extend(getattr(_t, "G1_LOCOMOTION_TOOLS", []))
            if lookout:
                tools.extend(getattr(_t, "G1_LOOKOUT_TOOLS", []))
        except Exception as e:
            log.warning("Layer 1 (DDS tools/) unavailable: %s", e)

    # ── Layer 2: strands-robots Robot (sim / policy / dataset / mesh) ───
    if robot_tool:
        rt = make_robot_tool(name=name, mode=mode, mesh=mesh, **robot_kwargs)
        if rt is not None:
            tools.append(rt)

    # De-dup while preserving order (some bundles overlap).
    seen, out = set(), []
    for t in tools:
        key = id(t)
        if key not in seen:
            seen.add(key)
            out.append(t)

    log.info("neon() composed %d tools (mode=%s, dds=%s, layer2=%s)",
             len(out), mode, dds, robot_tool)
    return out
