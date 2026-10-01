"""Tests for the neon ↔ strands-robots integration layer.

These cover the registry aliasing and the neon() bundle composition logic
WITHOUT requiring on-robot DDS hardware. strands-robots (sim) is optional:
tests skip cleanly when it's not installed.
"""
import importlib.util

import pytest

_HAS_SR = importlib.util.find_spec("strands_robots") is not None
_sr = pytest.mark.skipif(not _HAS_SR, reason="strands-robots not installed")


@_sr
def test_aliases_resolve_to_unitree_g1():
    from neon.registry import ensure_registered
    from strands_robots.registry import resolve_name

    r = ensure_registered()
    assert r["status"] == "ok"
    assert r["canonical"] == "unitree_g1"
    # g1 is an upstream alias; neon is ours. Both must resolve to the G1 asset.
    assert resolve_name("g1") == "unitree_g1"
    # neon resolves to a registered entry (itself, sharing the g1 asset)
    assert resolve_name("neon") in ("unitree_g1", "neon")


@_sr
def test_make_robot_tool_returns_agenttool():
    from neon import make_robot_tool

    g1 = make_robot_tool("g1", mode="sim", mesh=False)
    assert g1 is not None
    # strands-robots Robot/sim engines expose a tool_name (AgentTool contract).
    assert getattr(g1, "tool_name", None)


@_sr
def test_neon_bundle_includes_robot_tool():
    from neon import neon

    # robot-only bundle: exactly the Layer-2 Robot tool.
    tools = neon(mode="sim", mesh=False, dds=False, locomotion=False, lookout=False)
    names = [getattr(t, "tool_name", type(t).__name__) for t in tools]
    assert len(tools) == 1
    assert names[0].startswith("g1")  # g1_sim


def test_neon_importable_without_strands_robots():
    """neon() must degrade gracefully — Layer 1 (DDS) works even if Layer 2
    (strands-robots) is missing. robot_tool=False never touches strands_robots."""
    from neon import neon

    tools = neon(robot_tool=False, dds=False, locomotion=False, lookout=False)
    assert tools == []
