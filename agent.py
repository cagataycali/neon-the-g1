"""
NEON — Unitree G1 EDU+ humanoid agent (REPL entry point).

Thin wrapper around `g1.build_shell_agent()`. ALL persona/tool/prompt logic
lives in `g1.py` — the single source of truth shared by every persona
(shell / voice / telegram / thinker). This file just runs the REPL loop:

  - builds the shell-persona agent (slim latency-tuned toolset)
  - refreshes LIVE robot state (FSM/posture/battery) into the prompt each turn
  - auto-starts bidirectional voice on launch (set NEON_NO_SPEECH=1 to skip)

Run:  make run  -  make run-bare  -  make ask Q="..."  -  python agent.py
"""
from __future__ import annotations
import os

from g1 import build_shell_agent, _shell_prompt

agent = build_shell_agent()


def main() -> None:
    print("NEON ready.")
    print(f"Tools loaded: {len(agent.tool_names)}")
    print("Type 'exit' / 'quit' / 'q' to leave, Ctrl-C to force.\n")

    # Auto-start bidirectional speech (set NEON_NO_SPEECH=1 to disable).
    if os.getenv("NEON_NO_SPEECH", "").lower() not in ("1", "true", "yes"):
        try:
            r = agent.tool.g1_speak(action="start")
            for c in (r.get("content") or []):
                if (t := c.get("text", "")):
                    print(t.split("\n")[0])
        except Exception as e:
            print(f"⚠️  g1_speak auto-start failed: {e}")

    while True:
        try:
            user_input = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in ("exit", "quit", "q"):
            break
        if not user_input.strip():
            continue

        # 🔁 refresh live state into system prompt each turn
        try:
            agent.system_prompt = _shell_prompt()
        except Exception as e:
            print(f"⚠️  state refresh failed: {e}")

        agent(user_input)


if __name__ == "__main__":
    main()
