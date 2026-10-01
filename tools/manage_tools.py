"""Runtime tool management — add/remove/list tools on the calling agent.

Supports:
  - "list":    enumerate currently-loaded tools
  - "add":     load tool(s) from "package:name1,name2;package2:name3" or single "package:tool"
  - "remove":  unregister a tool by name
  - "discover": list available callables in a module
  - "create":  define a new @tool from inline Python source code (sandboxed compile)

Tools loaded by `add` survive only for the lifetime of the calling agent —
this is by design. Persistent tool config lives in `lookout.build_tools()`.
For permanent additions, edit lookout.py.
"""
from __future__ import annotations
import importlib
import sys
import textwrap
import types
from typing import Optional, Any
from strands import tool


def _agent_from_kwargs(kwargs):
    return kwargs.get("agent")


def _load_from_spec(spec: str) -> list:
    """Parse 'pkg:tool1,tool2;pkg2:tool3' → list of callables."""
    out = []
    for group in spec.split(";"):
        group = group.strip()
        if not group:
            continue
        parts = group.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"bad spec '{group}', expected pkg:tool[,tool]")
        pkg, names = parts[0].strip(), [n.strip() for n in parts[1].split(",")]
        mod = importlib.import_module(pkg)
        for name in names:
            if not name:
                continue
            if not hasattr(mod, name):
                raise AttributeError(f"{pkg} has no '{name}'")
            out.append(getattr(mod, name))
    return out


@tool
def manage_tools(
    action: str = "list",
    tools: Optional[str] = None,
    name: Optional[str] = None,
    code: Optional[str] = None,
    **kwargs,
) -> str:
    """Manage the calling agent's tool registry at runtime.

    Actions:
      - "list":     show currently registered tools
      - "add":      tools='pkg:tool1,tool2' → load and register
      - "remove":   name='toolname' → unregister
      - "discover": tools='package_name' → list @tool callables in that module
      - "create":   code='...python source defining @tool decorated functions...'
                    → exec and register them on this agent

    Notes:
      - Changes are LIVE for the calling agent only.
      - Persistent additions: edit `lookout.build_tools()`.
    """
    agent = _agent_from_kwargs(kwargs)
    if agent is None:
        return "no agent context"

    registry = getattr(agent, "tool_registry", None)
    if registry is None:
        return "agent has no tool_registry (incompatible)"

    reg_dict = getattr(registry, "registry", {})

    if action == "list":
        if not reg_dict:
            return "(no tools)"
        names = sorted(reg_dict.keys())
        return f"{len(names)} tools:\n  " + "\n  ".join(names)

    if action == "add":
        if not tools:
            return "tools spec required, e.g. 'tools.memory:memory'"
        try:
            new = _load_from_spec(tools)
        except Exception as e:
            return f"✗ load failed: {e}"
        if not new:
            return "no tools resolved from spec"
        # Use the registry's process_tools API if available
        if hasattr(registry, "process_tools"):
            registry.process_tools(new)
        else:
            for t in new:
                reg_dict[getattr(t, "tool_name", t.__name__)] = t
        return f"✓ added {len(new)} tool(s): {[getattr(t,'tool_name',t.__name__) for t in new]}"

    if action == "remove":
        if not name:
            return "name required"
        if name in reg_dict:
            del reg_dict[name]
            return f"✓ removed '{name}'"
        return f"not found: {name}"

    if action == "discover":
        if not tools:
            return "tools='package_name' required"
        try:
            mod = importlib.import_module(tools)
        except Exception as e:
            return f"✗ import failed: {e}"
        found = []
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            attr = getattr(mod, attr_name)
            # Strands @tool decorated callables expose tool_spec or tool_name
            if hasattr(attr, "tool_spec") or hasattr(attr, "tool_name"):
                found.append(attr_name)
        if not found:
            return f"no @tool-decorated callables found in {tools}"
        return f"@tool callables in {tools}:\n  " + "\n  ".join(found)

    if action == "create":
        if not code:
            return "code required"
        # Compile & exec into a fresh module namespace
        ns: dict[str, Any] = {}
        try:
            exec(compile(textwrap.dedent(code), "<manage_tools.create>", "exec"),
                 ns)
        except Exception as e:
            return f"✗ compile/exec failed: {e}"
        # Find @tool-decorated callables in the namespace
        new_tools = [v for k, v in ns.items()
                     if not k.startswith("_") and (hasattr(v, "tool_spec") or hasattr(v, "tool_name"))]
        if not new_tools:
            return "✗ no @tool-decorated functions found in code"
        if hasattr(registry, "process_tools"):
            registry.process_tools(new_tools)
        else:
            for t in new_tools:
                reg_dict[getattr(t, "tool_name", t.__name__)] = t
        return f"✓ created {len(new_tools)} tool(s): {[getattr(t,'tool_name',t.__name__) for t in new_tools]}"

    return f"unknown action: {action}"
