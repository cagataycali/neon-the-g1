"""Sanity import tests — stack loads without requiring DDS/pyaudio/strands.bidi.

Run with `python3 -m pytest tests/test_import_sanity.py -v`.

What this CAN test:
  - tools.memory / agent_log / voice_bridge
  - `import tools.vision` has no bidi side effects any more

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


def test_retired_robot_tool_modules_are_gone():
    """Retired (owner, 2026-10-07): kimodo never reached end to end; the posture,
    SLAM and safe-posture tools are gone, FSM control stays reachable through
    use_unitree("loco", "SetFsmId", ...). No bundle may still carry their names."""
    import importlib
    for mod in ("tools.kimodo", "tools.g1_posture", "tools.g1_slam", "tools.g1_safe_posture"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(mod)
    assert not (ROOT / "motions").exists()
    assert not (ROOT / "docs" / "tools" / "motion-gen.md").exists()
    import tools as _t
    for bundle in ("G1_POSTURE_TOOLS", "G1_SLAM_TOOLS", "G1_MOTION_GEN_TOOLS"):
        assert not hasattr(_t, bundle), bundle
    retired = {"kimodo", "g1_set_fsm", "g1_set_stand_height", "g1_set_swing_height",
               "g1_balance_stand", "g1_safe_squat_to_stand", "g1_safe_lie_to_stand",
               "g1_safe_stand_to_squat"} | {
                   f"g1_slam_{s}" for s in ("start", "stop", "pose", "reset", "accumulate",
                                            "save", "load", "list_maps", "stats")}
    for bundle in ("G1_ALL_TOOLS", "G1_SAFE_TOOLS", "G1_SENSING_TOOLS", "G1_LOOKOUT_TOOLS"):
        names = {getattr(t, "tool_name", getattr(t, "__name__", None)) for t in getattr(_t, bundle)}
        assert not names & retired, (bundle, names & retired)
    assert len(_t.G1_ALL_TOOLS) == 38


def test_telegram_module_no_token():
    """tools/telegram.py imports cleanly even with no token — calls error politely."""
    from tools.telegram import telegram, download_file, listen
    assert callable(telegram)
    assert callable(download_file)
    assert callable(listen)


def test_vision_imports_without_the_experimental_bidi_package():
    """tools/vision used to monkey-patch strands.experimental.bidi at import time;
    with strands.bidi (1.58+) it imports nothing bidi-specific and patches nothing."""
    import tools.vision as v1
    import tools.vision as v2
    assert v1 is v2
    assert not hasattr(v1, "_patch_openai_image_support")
    assert "strands.experimental.bidi" not in sys.modules
