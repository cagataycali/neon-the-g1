# extending neon

<span class="read-badge">60s</span>

Add a tool, a sensor, a listener, an MCP server — no refactors needed.

## a composed tool

`tools/g1_mytool.py`:

```python
from strands import tool
from ._g1_common import ensure_dds, get_loco_client, read_fsm_id, decode_code, HANDSHAKE_FSMS

@tool
def g1_mytool(param: int = 0, network_interface: str = "eth0") -> dict:
    """One-line intent the LLM reads."""
    if err := ensure_dds(network_interface):
        return {"status": "error", "content": [{"text": err}]}
    if read_fsm_id() not in HANDSHAKE_FSMS:
        return {"status": "error", "content": [{"text": "wrong FSM"}]}
    rc = get_loco_client().MyMethod(param)
    return {"status": "success" if rc == 0 else "error",
            "content": [{"text": f"MyMethod rc={decode_code(rc)}"}]}
```

Then in `tools/__init__.py`: `from .g1_mytool import g1_mytool`, append it to the
right bundle (the catalog counts follow at the next docs build) and run
`python3 -m pytest tests/` (ToolResult shape, import sanity). Every tool handed
to an agent is wrapped by `tools/tool_log.py`, so your calls appear in the
activity log without any code of yours.

## a universal SDK call

You don't wrap it — it's already reachable:

```python
use_unitree(service_name="loco", operation_name="MyNewRpc", parameters={"foo": 1})
```

Only make a composed tool if it needs FSM gating, mutex, or rich parsing.

## a sensor (no DDS)

Same shape without `ensure_dds`; return `{"text": ...}` and `{"json": data}`
content blocks and register it in `G1_SENSING_TOOLS`.

## a persona

`g1.py` is the one place: a prompt builder next to `_telegram_prompt`, a tool
list from `build_tools` or `build_voice_tools`, and a `NEON_PERSONA` name so
its calls are attributed in `agent_log`. `telegram_listener.py` is the
smallest example (one agent per incoming message).

## an MCP client

```bash
uvx --from neon-the-g1 neon-mcp --safe          # stdio for Claude Code / Desktop, no walking
neon-mcp --http --port 8022                      # HTTP, several clients
neon-mcp --no-robot                              # cross-persona stack only, no DDS
```

`neon/mcp.py` serves the toolset over the Model Context Protocol. Without
`--safe` a remote client can walk the robot; read [safety](safety.md) first.

## a doc page

1. `docs/section/page.md` (open with a `<span class="read-badge">…</span>`)
2. add to `nav:` in `mkdocs.yml`; numbers come from `{{facts:...}}` tokens
   (`docs/hooks/facts.py`), never typed
3. `pip install -r requirements-docs.txt && mkdocs build --strict` (CI runs the
   same on push to `main` and deploys to Pages)

Match the tone: **minimalist, dense, specific. No fluff.**

## contribute

Fork → branch → `python3 -m pytest tests/` green → Conventional Commits
(`feat:`/`fix:`/`docs:`) → PR. Non-trivial? Open an issue first.


---

[architecture](architecture.md){ .md-button } [composed tools](../tools/composed.md){ .md-button }
