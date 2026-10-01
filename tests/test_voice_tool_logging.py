"""Every tool call of every persona is logged (journal line + agent_log role=tool).

Pure unit tests: a fake tool and a real ``@tool`` function wrapped by
``tools.tool_log.LoggedTool``; no robot, no network, no DDS.
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

strands = pytest.importorskip("strands")
from strands import tool  # noqa: E402
from strands.tools.registry import ToolRegistry  # noqa: E402
from strands.types.tools import AgentTool  # noqa: E402

from tools import agent_log  # noqa: E402
from tools.tool_log import LoggedTool, summarize_result, wrap_tools  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "mem.db"
    monkeypatch.setattr(agent_log, "DB", path)
    return path


def _rows(path):
    c = sqlite3.connect(path)
    rows = c.execute("SELECT persona, role, text, meta FROM agent_log ORDER BY id").fetchall()
    c.close()
    return [(p, r, t, json.loads(m) if m else None) for p, r, t, m in rows]


def _run(tool_obj, tool_use):
    async def go():
        last = None
        async for ev in tool_obj.stream(tool_use, {}):
            last = ev
        return getattr(last, "tool_result", last)
    return asyncio.run(go())


@tool
def g1_fake_walk(distance: float = 0.3, token: str = "") -> dict:
    """A locomotion-shaped tool that reports it did not move."""
    return {"status": "success", "content": [
        {"text": "SDK accepted (rc=0) but no displacement measured"},
        {"json": {"rc": 0, "moved": False, "requested_m": distance, "measured_m": 0.01}},
    ]}


@tool
def g1_fake_boom() -> dict:
    """A tool that raises."""
    raise RuntimeError("DDS exploded")


def test_wrapper_keeps_the_model_facing_spec():
    w = LoggedTool(g1_fake_walk, "voice")
    assert isinstance(w, AgentTool)
    assert w.tool_name == g1_fake_walk.tool_name == "g1_fake_walk"
    assert w.tool_spec == g1_fake_walk.tool_spec
    assert w.tool_type == g1_fake_walk.tool_type
    # idempotent
    assert wrap_tools([w], "voice")[0] is w
    # registers like any tool (this is what Agent/BidiAgent do with the list)
    reg = ToolRegistry()
    reg.process_tools([w])
    assert reg.registry["g1_fake_walk"] is w


def test_stream_logs_journal_line_and_agent_log_row(db, capsys):
    w = LoggedTool(g1_fake_walk, "voice")
    res = _run(w, {"toolUseId": "t1", "name": "g1_fake_walk",
                   "input": {"distance": 0.3, "token": "sk-secret"}})
    assert res["status"] == "success"
    err = capsys.readouterr().err
    assert "[tool voice] g1_fake_walk(" in err
    assert "status=success rc=0 moved=false" in err
    assert "no displacement measured" in err
    assert "sk-secret" not in err and '"token": "***"' in err
    rows = _rows(db)
    assert len(rows) == 1
    persona, role, text, meta = rows[0]
    assert (persona, role) == ("voice", "tool")
    assert meta["tool"] == "g1_fake_walk" and meta["tool_name"] == "g1_fake_walk"
    assert meta["status"] == "success" and meta["rc"] == 0 and meta["moved"] is False
    assert meta["measured_m"] == 0.01 and meta["tool_use_id"] == "t1"
    assert isinstance(meta["duration_ms"], int)
    assert meta["args"]["token"] == "***" and meta["args"]["distance"] == 0.3


def test_logs_even_when_the_consumer_stops_at_the_result_like_the_executor(db, capsys):
    """strands' ToolExecutor breaks out of tool.stream() at the first ToolResultEvent and never
    resumes the generator: the log must be written before that yield (bug found live 17:4xZ)."""
    w = LoggedTool(g1_fake_walk, "voice")

    async def executor_like():
        gen = w.stream({"toolUseId": "t-exec", "name": "g1_fake_walk", "input": {"distance": 0.3}}, {})
        async for ev in gen:
            if getattr(ev, "tool_result", None) is not None or (isinstance(ev, dict) and "status" in ev):
                break           # exactly what ToolExecutor._stream does
        await gen.aclose()
    asyncio.run(executor_like())
    rows = _rows(db)
    assert len(rows) == 1 and rows[0][3]["tool"] == "g1_fake_walk" and rows[0][3]["moved"] is False
    assert "[tool voice] g1_fake_walk(" in capsys.readouterr().err

    # a consumer that abandons the generator before any result still gets one row (from finally)
    class Slow(AgentTool):
        tool_name = "slow"
        tool_spec = {"name": "slow", "description": "x", "inputSchema": {"json": {"type": "object"}}}
        tool_type = "python"

        async def stream(self, tool_use, invocation_state, **kwargs):
            yield {"type": "tool_stream", "data": "working"}
            yield {"status": "success", "content": [{"text": "late"}]}

    async def abandon():
        gen = LoggedTool(Slow(), "voice").stream({"toolUseId": "t-ab", "name": "slow", "input": {}}, {})
        async for _ in gen:
            break
        await gen.aclose()
    asyncio.run(abandon())
    rows = _rows(db)
    assert len(rows) == 2 and rows[1][3]["tool"] == "slow"


def test_exception_is_logged_then_propagates(db, capsys):
    w = LoggedTool(g1_fake_boom, "telegram")
    # strands' @tool catches exceptions and renders an error ToolResult...
    res = _run(w, {"toolUseId": "t2", "name": "g1_fake_boom", "input": {}})
    assert res["status"] == "error"
    err = capsys.readouterr().err
    assert "[tool telegram] g1_fake_boom({}) status=error" in err
    assert "DDS exploded" in err
    rows = _rows(db)
    assert rows[0][3]["status"] == "error"

    # ...and a tool whose stream itself raises is logged as an exception and re-raised.
    class Boom(AgentTool):
        tool_name = "boom"
        tool_spec = {"name": "boom", "description": "x", "inputSchema": {"json": {"type": "object"}}}
        tool_type = "python"

        async def stream(self, tool_use, invocation_state, **kwargs):
            raise ValueError("executor-level failure")
            yield  # pragma: no cover

    with pytest.raises(ValueError):
        _run(LoggedTool(Boom(), "thinker"), {"toolUseId": "t3", "name": "boom", "input": {"a": 1}})
    err = capsys.readouterr().err
    assert "[tool thinker] boom(" in err and "status=exception" in err
    assert _rows(db)[-1][3]["status"] == "exception"


def test_agent_log_failure_never_breaks_the_tool(db, monkeypatch, capsys):
    def broken(*a, **k):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(agent_log, "record", broken)
    w = LoggedTool(g1_fake_walk, "voice")
    res = _run(w, {"toolUseId": "t4", "name": "g1_fake_walk", "input": {}})
    assert res["status"] == "success"
    assert "agent_log write failed" in capsys.readouterr().err


def test_summarize_result_shapes():
    s = summarize_result({"status": "error", "content": [{"text": "Error: FSM=0 not in [501, 801]"}]})
    assert s["status"] == "error" and s["rc"] is None and "FSM=0" in s["message"]
    s = summarize_result({"status": "success", "content": [{"image": {"format": "jpeg", "source": {"bytes": b"x"}}},
                                                           {"json": {"rc": 0}}]})
    assert s["message"] == "<image>" and s["rc"] == 0
    s = summarize_result("plain")
    assert s["message"] == "plain"
    long = {"status": "success", "content": [{"text": "x" * 1000}]}
    assert len(summarize_result(long)["message"]) <= 300


def test_build_voice_tools_are_all_wrapped(monkeypatch):
    g1 = pytest.importorskip("g1")
    tools = g1.build_voice_tools(persona="voice")
    # every AgentTool is wrapped; a TOOL_SPEC module (strands_tools.shell) is wrapped when the
    # strands loader can resolve it and passed through otherwise (test_tool_result_shape stubs
    # the strands package, so the loader may be unavailable in a shared session)
    assert tools and not any(isinstance(t, AgentTool) and not isinstance(t, LoggedTool) for t in tools)
    names = {getattr(t, "tool_name", None) for t in tools}
    assert {"g1_walk_forward", "g1_turn", "g1_move_velocity", "g1_stop_move", "take_photo"} <= names
    # the dashboard's chat_agent (no persona argument) is attributed to "dashboard", not "voice"
    monkeypatch.setitem(sys.modules, "docs.dashboard.chat_agent", type(sys)("docs.dashboard.chat_agent"))
    monkeypatch.delenv("NEON_PERSONA", raising=False)
    assert g1.build_voice_tools()[0]._persona == "dashboard"
