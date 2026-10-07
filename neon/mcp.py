#!/usr/bin/env python3
"""MCP server entrypoint for neon-the-g1.

Exposes the FULL NEON toolset — 53 FSM-gated Unitree G1 robot tools (state,
posture, arms, locomotion, audio, LiDAR, SLAM, camera, DDS) plus the
cross-persona stack (memory, telegram, voice_say, take_photo,
use_github) — over the Model Context Protocol.

That means you can drive the physical G1 from Claude Code, Claude Desktop,
Kiro, Cursor, or any MCP-compatible client.

Built on strands-mcp-server (https://github.com/cagataycali/strands-mcp-server),
same shim technique as strands-transformers.

⚠️  SAFETY: this exposes locomotion + FSM tools. The remote MCP client can
    make the robot MOVE. Run only on a gantry / clear space, and prefer
    --safe to drop walking tools. See AGENTS.md safety rules.

Usage (zero-install via uvx — package name is ``neon-the-g1``):
    # stdio mode (Claude Code / Claude Desktop) — default
    uvx --from neon-the-g1 neon-mcp --safe

    # HTTP mode (multi-client, background-capable)
    uvx --from neon-the-g1 neon-mcp --http --port 8022

Or pip-installed:
    pip install "neon-the-g1[mcp]"
    neon-mcp             # stdio
    neon-mcp --safe      # drop locomotion (state/posture/arms/audio/sensing only)

Claude Code:
    claude mcp add neon -- uvx --from neon-the-g1 neon-mcp --safe

Claude Desktop config:
    {
      "mcpServers": {
        "neon": {
          "command": "uvx",
          "args": ["--from", "neon-the-g1", "neon-mcp", "--safe"]
        }
      }
    }
"""
from __future__ import annotations

import argparse
import logging
import sys

# MCP stdio servers MUST log to stderr — stdout is the protocol channel.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("neon.mcp")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="neon-mcp",
        description="neon-the-g1 MCP server — drive the Unitree G1 over MCP",
    )
    parser.add_argument("--http", action="store_true",
                        help="Run HTTP transport instead of stdio (default: stdio)")
    parser.add_argument("--port", type=int, default=8022,
                        help="HTTP port (default: 8022)")
    parser.add_argument("--stateless", action="store_true",
                        help="Stateless HTTP mode (multi-node scalable)")
    parser.add_argument("--safe", action="store_true",
                        help="Drop locomotion/walking tools (state/posture/arms/audio/sensing only)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Exclude the telegram tool")
    parser.add_argument("--no-robot", action="store_true",
                        help="Exclude ALL G1 robot tools (cross-persona stack only)")
    parser.add_argument("--agent-invocation", action="store_true",
                        help="Also expose invoke_agent for full conversations (default: off)")
    parser.add_argument("--debug", action="store_true", help="Debug logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        from strands import Agent
        from strands_mcp_server.mcp_server import mcp_server
    except ImportError as e:
        logger.error(
            f"Missing dependency: {e}\n"
            'Install with: pip install "neon-the-g1[mcp]"  (or: pip install strands-mcp-server)'
        )
        sys.exit(1)

    # Single source of truth for NEON's toolset — same list every persona uses.
    from g1 import build_tools, MODEL_ID, _shell_prompt

    tools = build_tools(
        include_telegram=not args.no_telegram,
        include_robot=not args.no_robot,
    )

    # --safe: strip the 🔴 locomotion tools so a remote client can't walk the robot.
    if args.safe:
        try:
            from tools import G1_LOCOMOTION_TOOLS
            walk_names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in G1_LOCOMOTION_TOOLS}
            before = len(tools)
            tools = [
                t for t in tools
                if getattr(t, "tool_name", getattr(t, "__name__", "")) not in walk_names
            ]
            logger.info(f"--safe: dropped {before - len(tools)} locomotion tool(s)")
        except Exception as e:
            logger.warning(f"--safe requested but could not filter locomotion tools: {e}")

    # mcp_server must be registered on the agent to be invokable.
    tools = list(tools) + [mcp_server]

    try:
        system_prompt = _shell_prompt()
    except Exception as e:
        logger.warning(f"live-state prompt failed ({e}); using static prompt")
        system_prompt = (
            "NEON — Unitree G1 robot agent exposed over MCP. FSM-gated control of "
            "state, posture, arms, locomotion, audio, LiDAR, SLAM, camera + memory/"
            "telegram/voice. Check g1_get_state() before motion; release arms after "
            "gestures; never walk without explicit approval."
        )

    logger.info(f"🤖 neon-the-g1 MCP server: {len(tools) - 1} tools ready "
                f"(safe={args.safe}, robot={not args.no_robot})")

    agent = Agent(
        name="neon-the-g1-mcp",
        model=MODEL_ID,
        tools=tools,
        load_tools_from_directory=False,
        system_prompt=system_prompt,
        callback_handler=None,
    )

    transport = "http" if args.http else "stdio"
    logger.info(f"Starting MCP server (transport={transport})")

    # Call the raw tool function directly (NOT agent.tool.mcp_server) —
    # agent.tool.* marks the agent as mid-invocation, and since stdio mode
    # blocks forever, all nested tool calls would then be rejected by the SDK.
    _fn = (getattr(mcp_server, "_tool_func", None)
           or getattr(mcp_server, "original_function", None)
           or mcp_server)
    _fn(
        action="start",
        transport=transport,
        port=args.port,
        stateless=args.stateless,
        expose_agent=args.agent_invocation,
        agent=agent,
    )

    if args.http:
        # HTTP runs in a background thread — keep the process alive.
        import time
        logger.info(f"HTTP MCP server live at http://localhost:{args.port}/mcp (Ctrl+C to stop)")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            logger.info("Shutting down")


if __name__ == "__main__":
    main()
