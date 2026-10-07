"""Every persona prompt renders.

2026-10-07: the voice listener died in a 5 s restart loop with
"[voice err] Format specifier missing precision" after a prompt edit put a
literal `{"fsm_id": ...}` inside the f-string that builds the VOICE prompt
(an f-string reads `{"fsm_id": ...}` as the expression "fsm_id" with a
format spec). The prompts are only rendered at agent start, so nothing else
ran them. This renders all four, with the robot-only modules stubbed.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("strands")
pytest.importorskip("strands_tools")

for _name, _attrs in {
    "pyaudio": {"PyAudio": object, "paInt16": 8},
    "pywebrtc_audio": {"AudioProcessor": object},
}.items():
    try:
        __import__(_name)
    except Exception:
        _m = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_m, _k, _v)
        sys.modules[_name] = _m


@pytest.mark.parametrize("name", ["_voice_prompt", "_telegram_prompt", "_thinker_prompt", "_shell_prompt"])
def test_every_persona_prompt_renders(name, monkeypatch):
    import g1

    monkeypatch.setenv("TELEGRAM_DEFAULT_CHAT_ID", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "owner")
    fn = getattr(g1, name)
    text = fn("1", "owner") if name == "_telegram_prompt" else fn()
    assert isinstance(text, str) and len(text) > 500
    assert "{{" not in text and "}}" not in text, "a doubled brace leaked into the prompt"


def test_voice_prompt_names_the_fsm_call_with_real_braces():
    import g1

    assert 'use_unitree("loco", "SetFsmId", {"fsm_id": <id>})' in g1._voice_prompt()
