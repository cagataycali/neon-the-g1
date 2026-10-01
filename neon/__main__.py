"""`neon` CLI — quick REPL / one-shot wrapper around an Agent(tools=neon()).

Usage::
    neon                       # interactive REPL (sim mode, DDS if on-robot)
    neon "stand up and wave"   # one-shot
    neon --mode real "walk forward 0.3m"
    neon --no-dds "spin up a sim and run the mock policy"
"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="neon", description="NEON — Unitree G1 agent")
    ap.add_argument("query", nargs="*", help="one-shot prompt (omit for REPL)")
    ap.add_argument("--mode", default="sim", choices=["sim", "real", "auto"],
                    help="strands-robots Robot mode (default: sim)")
    ap.add_argument("--name", default="g1", help="robot alias (g1|neon)")
    ap.add_argument("--no-dds", action="store_true", help="skip Layer-1 DDS tools")
    ap.add_argument("--no-walk", action="store_true", help="exclude locomotion tools")
    ap.add_argument("--robot-ip", default=None, help="G1 controller IP (mode=real)")
    args = ap.parse_args()

    from strands import Agent
    from neon import neon as _neon

    kw = {}
    if args.robot_ip:
        kw["robot_ip"] = args.robot_ip

    tools = _neon(
        mode=args.mode, name=args.name,
        dds=not args.no_dds, locomotion=not args.no_walk, **kw,
    )
    agent = Agent(tools=tools)
    print(f"🤖 NEON ready · {len(tools)} tools · mode={args.mode}")

    if args.query:
        agent(" ".join(args.query))
        return

    while True:
        try:
            q = input("\nneon> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("exit", "quit", "q"):
            break
        if q:
            agent(q)


if __name__ == "__main__":
    main()
