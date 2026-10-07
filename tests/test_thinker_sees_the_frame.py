"""The thinker's cycle starts FROM the picture.

2026-10-07: the model was told to use_camera(action='save') and got back a path,
never pixels; captions said nothing about the frame. Now thinker_loop grabs the
frame itself and puts it into the user turn as an image content block, before
the text, and the saved file is what Telegram sends.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 3000 + b"\xff\xd9"


@pytest.fixture
def thinker(monkeypatch):
    # thinker_loop imports g1 (DDS, pyaudio...) at module import; stub the two names it uses
    g1 = types.ModuleType("g1")
    g1.build_agent = lambda persona: None
    g1._thinker_prompt = lambda: "prompt"
    monkeypatch.setitem(sys.modules, "g1", g1)
    for m in ("thinker_loop",):
        sys.modules.pop(m, None)
    import thinker_loop
    return thinker_loop


def test_turn_has_the_image_block_first_then_the_text(thinker):
    turn = thinker.build_turn("do the cycle", JPEG)
    assert isinstance(turn, list) and len(turn) == 2
    assert turn[0] == {"image": {"format": "jpeg", "source": {"bytes": JPEG}}}
    assert turn[1] == {"text": "do the cycle"}


def test_without_a_frame_the_turn_is_plain_text(thinker):
    assert thinker.build_turn("do the cycle", None) == "do the cycle"


def test_cycle_captures_before_the_model_turn_and_never_asks_the_model_to_shoot(thinker, monkeypatch, tmp_path):
    photo = tmp_path / "view.jpg"
    monkeypatch.setattr(thinker, "PHOTO_PATH", str(photo))

    def fake_capture(output=None, cam=None):
        Path(output).write_bytes(JPEG)
        return Path(output)
    vision = types.ModuleType("tools.vision"); vision._capture_frame_dashboard = fake_capture
    monkeypatch.setitem(sys.modules, "tools.vision", vision)
    monkeypatch.setattr(thinker, "alog", lambda *a, **k: None)

    seen = {}

    class Agent:
        messages = []
        system_prompt = ""
        def __call__(self, prompt):
            seen["prompt"] = prompt
            return "ok"

    thinker.cycle(Agent())
    prompt = seen["prompt"]
    assert prompt[0]["image"]["source"]["bytes"] == JPEG, "the model sees the pixels, not a path"
    text = prompt[1]["text"]
    assert "use_camera" not in text and "take_photo(" not in text
    assert str(photo) in text, "the saved file is named so send_photo can use it"
    assert photo.read_bytes() == JPEG


def test_no_frame_tells_the_model_not_to_work_around_it(thinker, monkeypatch):
    vision = types.ModuleType("tools.vision")
    vision._capture_frame_dashboard = lambda output=None, cam=None: (_ for _ in ()).throw(RuntimeError("no frame"))
    monkeypatch.setitem(sys.modules, "tools.vision", vision)
    monkeypatch.setattr(thinker, "alog", lambda *a, **k: None)
    seen = {}

    class Agent:
        messages = []
        system_prompt = ""
        def __call__(self, prompt):
            seen["prompt"] = prompt
            return "ok"

    thinker.cycle(Agent())
    assert isinstance(seen["prompt"], str)
    assert "No frame is available" in seen["prompt"] and "Do NOT call use_camera" in seen["prompt"]
