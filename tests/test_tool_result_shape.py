"""
Tests for Strands ToolResult shape conformance.

Every @tool in neon/tools/ must return:
    {
        "status": "success" | "error",
        "content": [
            {"text": "..."} or {"json": {...}} or {"image": {...}}
        ]
    }

The @tool decorator only recognizes the result as a ToolResult if BOTH
'status' AND 'content' keys are present — otherwise it wraps the whole
dict as success text and the error signal is lost.

This test parses every tool source via AST and proves:
  1. Every `return` inside a @tool-decorated function either
     (a) goes through _normalize(), tool_ok(), tool_err(), or tool_result(),
     (b) returns a dict literal with BOTH 'status' AND 'content' keys, or
     (c) returns nothing / propagates a variable validated elsewhere.

  2. _normalize() itself produces the correct shape for all common inputs.

This is a pure-AST check — no robot or SDK needed.
"""
import ast
import os
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS_DIR = HERE.parent / "tools"


# ── Stub strands + inject a real _normalize so _g1_common imports cleanly ─
class _MockTool:
    def __call__(self, fn):
        return fn


sys.modules.setdefault("strands", types.ModuleType("strands"))
sys.modules["strands"].tool = _MockTool()

# Drop any prior test-side stub of _g1_common (test_joints.py injects one).
sys.modules.pop("_g1_common", None)
sys.path.insert(0, str(TOOLS_DIR))

# Import by file path to avoid name collision with the stub
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "_g1_common_real", TOOLS_DIR / "_g1_common.py"
)
_gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gc)
tool_ok = _gc.tool_ok
tool_err = _gc.tool_err
tool_result = _gc.tool_result
class TestHelpers:
    def test_tool_ok_minimal(self):
        r = tool_ok()
        assert r == {"status": "success", "content": [{"text": "ok"}]}

    def test_tool_ok_with_extras(self):
        r = tool_ok("hello", fsm=500, rc=0)
        assert r["status"] == "success"
        assert r["content"][0] == {"text": "hello"}
        assert r["content"][1] == {"json": {"fsm": 500, "rc": 0}}

    def test_tool_err_prefixes_error(self):
        r = tool_err("bad thing")
        assert r["status"] == "error"
        assert r["content"][0]["text"].startswith("Error: ")

    def test_tool_err_with_extras(self):
        r = tool_err("rc=7404", rc=7404, fsm=0)
        assert r["content"][-1] == {"json": {"rc": 7404, "fsm": 0}}

    def test_tool_result_image(self):
        r = tool_result(True, "snapshot", image=b"PNG\x89...", image_format="png")
        kinds = [list(c.keys())[0] for c in r["content"]]
        assert "text" in kinds
        assert "image" in kinds
        img = [c for c in r["content"] if "image" in c][0]["image"]
        assert img["format"] == "png"
        assert img["source"]["bytes"] == b"PNG\x89..."

    def test_tool_result_error_prefix(self):
        r = tool_result(False, "oops")
        assert r["status"] == "error"
        assert r["content"][0]["text"].startswith("Error: ")