"""neon-the-g1 — a Strands agent for the Unitree G1+ humanoid.

Two control layers, one agent:
    • Layer 1 — live DDS control (FSM-gated arms/walk/posture/voice) via this
                repo's ``tools/`` package, running ON the robot.
    • Layer 2 — strands-robots ``Robot("g1")``: MuJoCo sim (safe default),
                VLA policies (GR00T/Cosmos/ACT), dataset recording, fleet mesh,
                and ``mode="real"`` LeRobot network driver.

Quick start::

    from strands import Agent
    from neon import neon

    agent = Agent(tools=neon())                 # sim + DDS + voice/memory
    agent("create a sim world, add the g1, run the mock policy for 30 steps")

    # or just the Layer-2 Robot tool:
    from neon import make_robot_tool
    g1 = make_robot_tool("g1", mode="sim")
    Agent(tools=[g1])("stand up and balance")
"""
from .registry import ensure_registered, register
from .robot import make_robot_tool, neon

# Register the g1/neon aliases as soon as the package is imported, so a bare
# ``Robot("g1")`` works even if the caller never touched neon.robot.
try:
    ensure_registered()
except Exception:  # never block import
    pass

__all__ = ["neon", "make_robot_tool", "ensure_registered", "register"]
__version__ = "0.1.0"
