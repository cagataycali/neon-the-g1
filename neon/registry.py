"""Register NEON's robot aliases into the strands-robots registry.

strands-robots already ships a ``unitree_g1`` entry (46-DOF humanoid, MJCF
``g1.xml``, lerobot_type ``unitree_g1``). We don't define a new robot — we add
friendly aliases ``"neon"`` and ``"g1"`` that resolve to it, so::

    from strands_robots import Robot
    sim  = Robot("g1")                  # MuJoCo sim (safe default)
    real = Robot("g1", mode="real",     # LeRobot unitree_g1 network driver
                 robot_ip="192.168.123.161")

Idempotent + best-effort: if ``g1`` already resolves (e.g. a newer
strands-robots adds the alias upstream) we no-op. Safe to call at import.
"""
from __future__ import annotations

import logging

log = logging.getLogger("neon.registry")

# Aliases we want pointing at the canonical strands-robots humanoid.
_CANONICAL = "unitree_g1"
_ALIASES = ("neon", "g1")


def ensure_registered() -> dict:
    """Make ``Robot("neon")`` and ``Robot("g1")`` resolve to ``unitree_g1``.

    Returns a small status dict describing what happened. Never raises —
    a missing/old strands-robots just yields ``{"status": "skipped", ...}``.
    """
    try:
        from strands_robots.registry import get_robot, resolve_name
    except Exception as e:  # strands-robots not installed yet
        log.debug("strands-robots registry unavailable: %s", e)
        return {"status": "skipped", "reason": f"registry import failed: {e}"}

    # Canonical must exist (it ships in robots.json).
    if get_robot(_CANONICAL) is None:
        log.warning("'%s' not in strands-robots registry — cannot alias.", _CANONICAL)
        return {"status": "error", "reason": f"{_CANONICAL} missing from registry"}

    done, already = [], []
    for alias in _ALIASES:
        try:
            if resolve_name(alias) == _CANONICAL:
                already.append(alias)
                continue
        except Exception:
            pass  # not resolvable yet → register it below

        try:
            from strands_robots.registry import register_robot

            info = get_robot(_CANONICAL)
            asset = info.get("asset", {})
            # asset["dir"] is relative to the strands-robots asset search path,
            # so the alias inherits the SAME downloaded MJCF — no new assets.
            register_robot(
                name=alias,
                model_xml=asset.get("model_xml", "g1.xml"),
                scene_xml=asset.get("scene_xml"),
                asset_dir=asset.get("dir", _CANONICAL),
                description=f"NEON — alias for {_CANONICAL} ({info.get('description','Unitree G1')})",
                category=info.get("category", "humanoid"),
                joints=info.get("joints", 46),
                robot_descriptions_module=asset.get("robot_descriptions_module"),
                hardware=info.get("hardware"),
                overwrite=True,
            )
            done.append(alias)
            log.info("Registered alias '%s' → %s", alias, _CANONICAL)
        except Exception as e:
            log.warning("Could not register alias '%s': %s", alias, e)

    return {"status": "ok", "registered": done, "already": already, "canonical": _CANONICAL}


def register() -> dict:
    """Entry-point shim (see pyproject scripts/plugins). Alias of ensure_registered."""
    return ensure_registered()
