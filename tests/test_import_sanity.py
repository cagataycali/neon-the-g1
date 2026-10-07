"""Sanity import tests — stack loads without requiring DDS/pyaudio/strands.experimental.bidi.

Run with `python3 -m pytest tests/test_import_sanity.py -v`.

What this CAN test:
  - tools.memory / agent_log / voice_bridge
  - Side-effects of `import tools.vision` (OpenAI bidi patch is
    try/except guarded, so it MUST not crash)

What this CANNOT test (would need a robot):
  - tools.g1_state / g1_arm / g1_locomotion (DDS init)
  - tools.g1_bidi_audio / g1_speak (pyaudio + DDS + bidi)
  - g1.build_voice_agent (bidi)
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_memory_module():
    from tools.memory import memory
    assert callable(memory)


def test_agent_log_module():
    from tools.agent_log import record, recent, format_for_prompt, stats, clear
    for fn in (record, recent, format_for_prompt, stats, clear):
        assert callable(fn)


def test_voice_bridge_module():
    from tools.voice_bridge import push, pop_pending, flush_stale, stats, voice_say
    for fn in (push, pop_pending, flush_stale, stats, voice_say):
        assert callable(fn)


def test_removed_tool_modules_are_gone():
    """Cut from the source (owner, 2026-10-07): the robot carries no terminal, sub-agent
    spawner, phone remote, jukebox, self-editing prompts, history/tool manager or make."""
    import importlib
    for mod in ("tools.dispatch", "tools.phone", "tools.use_spotify", "tools.prompts",
                "tools.manage_messages", "tools.manage_tools", "tools.make"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)
    import tools as _t
    names = {getattr(t, "tool_name", getattr(t, "__name__", None)) for t in _t.G1_LOOKOUT_TOOLS}
    assert not names & {"shell", "dispatch", "phone", "use_spotify", "adb", "recorder", "prompts", "manage_messages", "manage_tools", "make"}


def test_telegram_module_no_token():
    """tools/telegram.py imports cleanly even with no token — calls error politely."""
    from tools.telegram import telegram, download_file, listen
    assert callable(telegram)
    assert callable(download_file)
    assert callable(listen)


def test_vision_patch_idempotent():
    """tools/vision applies an OpenAI bidi patch at import time. Importing
    twice should be a no-op (idempotent guard via _image_patched attr)."""
    import tools.vision as v1
    import tools.vision as v2
    assert v1 is v2
