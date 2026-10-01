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

Then in `tools/__init__.py`: `from .g1_mytool import g1_mytool` → append to the
right bundle → `python3.11 -m pytest tests/` (verifies ToolResult shape + import sanity).

## a universal SDK call

You don't wrap it — it's already reachable:

```python
use_unitree(service_name="loco", operation_name="MyNewRpc", parameters={"foo": 1})
```

Only make a composed tool if it needs FSM gating, mutex, or rich parsing.

## a sensor (no DDS)

```python
@tool
def my_sensor(action: str = "read") -> dict:
    """What it reads."""
    data = _do_read()
    return {"status": "success", "content": [{"text": f"{len(data)} samples"}, {"json": data}]}
```

Register in `G1_SENSING_TOOLS`.

## a listener

```bash
export TELEGRAM_BOT_TOKEN=... TELEGRAM_ALLOWED_USERS=12345
make tg          # or: docker compose up neon-telegram
```

`telegram_listener.py` reads these and spawns a telegram-persona agent per
incoming message.

## an MCP server

```bash
devduck --mcp                                   # stdio (Claude Desktop)
# or consume external servers:
export MCP_SERVERS='{"mcpServers":{"x":{"command":"uvx","args":["some-mcp"]}}}'
```

All its tools appear in neon automatically.

## a doc page

1. `docs/section/page.md` (open with a `<span class="read-badge">…</span>`)
2. add to `nav:` in `mkdocs.yml`
3. `python3.11 -m mkdocs serve` to preview

Match the tone: **minimalist, dense, specific. No fluff.**

## contribute

Fork → branch → `python3.11 -m pytest tests/` green → Conventional Commits
(`feat:`/`fix:`/`docs:`) → PR. Non-trivial? Open an issue first.


---

[architecture](architecture.md){ .md-button } [composed tools](../tools/composed.md){ .md-button }
