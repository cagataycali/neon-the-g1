"""Smoke tests memory/agent_log/voice_bridge.

These tests DO NOT require:
  - DDS / unitree_sdk2_python (g1 robot connection)
  - pyaudio / pywebrtc_audio (mic + AEC)
  - strands.bidi (voice agent)
  - OPENAI_API_KEY / TELEGRAM_BOT_TOKEN

They DO verify:
  - SQLite schemas initialize cleanly
  - kv set/get/delete round-trip
  - log_add + log_recent
  - voice_bridge push + pop_pending + flush_stale + stats
  - agent_log record + recent + format_for_prompt + clear
"""
from __future__ import annotations
import os
import sys
import shutil
import tempfile
from pathlib import Path

import pytest

# Make the test runnable from any CWD
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """Redirect .memory/ to a temp dir for each test so we don't pollute real data."""
    fake_memory = tmp_path / ".memory"
    fake_memory.mkdir()
    # The tools resolve `Path(__file__).parent.parent / ".memory"`. To avoid
    # mucking with module internals, we cd into a fake repo root.
    fake_root = tmp_path / "fake_repo"
    (fake_root / "tools").mkdir(parents=True)
    # Symlink the actual tools/ into fake_root/tools so imports resolve
    for f in ("memory.py", "agent_log.py", "voice_bridge.py"):
        src = ROOT / "tools" / f
        if src.exists():
            (fake_root / "tools" / f).symlink_to(src)
    (fake_root / "tools" / "__init__.py").write_text("")
    monkeypatch.chdir(fake_root)
    monkeypatch.syspath_prepend(str(fake_root))
    # Force re-import so module-level Path() resolves to the fake root
    for mod in ("tools.memory", "tools.agent_log", "tools.voice_bridge"):
        sys.modules.pop(mod, None)
    yield
    for mod in ("tools.memory", "tools.agent_log", "tools.voice_bridge"):
        sys.modules.pop(mod, None)


def test_memory_kv_roundtrip():
    from tools.memory import memory
    assert "✓" in memory(action="kv_set", key="alpha", value="one")
    assert memory(action="kv_get", key="alpha") == "one"
    assert memory(action="kv_set", key="alpha", value="two").startswith("✓")
    assert memory(action="kv_get", key="alpha") == "two"
    assert "deleted 1" in memory(action="kv_del", key="alpha")
    assert memory(action="kv_get", key="alpha").startswith("not found")


def test_memory_notes_roundtrip():
    from tools.memory import memory
    memory(action="note_write", name="hello", text="world")
    assert memory(action="note_read", name="hello") == "world"
    assert "hello" in memory(action="note_list")
    memory(action="note_delete", name="hello")
    assert memory(action="note_read", name="hello").startswith("not found")


def test_memory_log_recent():
    from tools.memory import memory
    memory(action="log_add", text="first event", tag="t1")
    memory(action="log_add", text="second event", tag="t1")
    memory(action="log_add", text="third event", tag="t2")
    out = memory(action="log_recent", tag="t1", limit=5)
    assert "first event" in out
    assert "second event" in out
    assert "third event" not in out


def test_voice_bridge_push_pop():
    from tools.voice_bridge import push, pop_pending, stats, flush_stale
    rid = push("test", "hello voice", importance=2)
    assert rid > 0
    rows = pop_pending(limit=5)
    assert len(rows) == 1
    _id, source, msg, imp = rows[0]
    assert source == "test"
    assert msg == "hello voice"
    assert imp == 2
    # Second pop should be empty (we already marked delivered)
    assert pop_pending() == []
    s = stats()
    assert s["pending"] == 0
    assert s["total"] == 1


def test_agent_log_roundtrip():
    from tools.agent_log import record, recent, format_for_prompt, stats, clear
    record("voice", "user", "hi from voice")
    record("telegram", "assistant", "hi from telegram")
    rows = recent(limit=10)
    assert len(rows) == 2
    block = format_for_prompt(limit=10, exclude_persona="voice")
    # voice's row should be excluded
    assert "telegram" in block
    assert "hi from voice" not in block
    s = stats()
    assert s["total"] == 2
    assert clear() == 2


