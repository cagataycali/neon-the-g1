"""Dashboard speaker volume: GET/POST /api/voice/volume through voice_api.

Pure unit tests: the AudioClient Get/SetVolume helpers in tools.voice_control
are replaced by a fake, so no robot, no DDS. Pins the contract the Voice
sheet's - / slider / + row relies on: clamp to 0-100, delta reads first,
exactly one of level/delta, rc != 0 is an error that keeps the old level.
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docs.dashboard import voice_api  # noqa: E402


class FakeSpeaker:
    def __init__(self, level=40, rc=0, get_none=False):
        self.level, self.rc, self.get_none, self.sets = level, rc, get_none, []

    def _volume_get(self):
        return None if self.get_none else self.level

    def _volume_set(self, lvl):
        self.sets.append(lvl)
        if self.rc == 0:
            self.level = lvl
        return self.rc


@pytest.fixture
def speaker(monkeypatch):
    spk = FakeSpeaker()
    monkeypatch.setattr(voice_api, "_volume_backend", lambda: spk)
    monkeypatch.setitem(voice_api._VOLUME_CACHE, "volume", None)
    return spk


def test_get_reads_the_robot(speaker):
    out = voice_api.volume()
    assert out == {"ok": True, "volume": 40, "step": voice_api.VOLUME_STEP}


def test_delta_reads_first_then_sets(speaker):
    out = voice_api.set_volume(delta=10)
    assert out["ok"] and out["volume"] == 50 and out["previous"] == 40
    assert speaker.sets == [50]


@pytest.mark.parametrize("delta,expect", [(100, 100), (-100, 0)])
def test_delta_is_clamped(speaker, delta, expect):
    assert voice_api.set_volume(delta=delta)["volume"] == expect


@pytest.mark.parametrize("level,expect", [(0, 0), (100, 100), (250, 100), (-5, 0), ("65", 65), (72.6, 72)])
def test_level_is_clamped_and_coerced(speaker, level, expect):
    out = voice_api.set_volume(level=level)
    assert out["ok"] and out["volume"] == expect and speaker.level == expect


def test_exactly_one_of_level_or_delta(speaker):
    assert not voice_api.set_volume()["ok"]
    assert not voice_api.set_volume(level=10, delta=5)["ok"]
    assert speaker.sets == []


def test_garbage_is_rejected_before_the_rpc(speaker):
    assert not voice_api.set_volume(level="loud")["ok"]
    assert speaker.sets == []


def test_rc_nonzero_is_an_error_and_keeps_the_old_level(speaker):
    speaker.rc = 7400
    out = voice_api.set_volume(level=80)
    assert out["ok"] is False and "rc=7400" in out["error"] and speaker.level == 40


def test_get_none_falls_back_to_cache_for_delta(speaker):
    voice_api.set_volume(level=30)          # primes the cache
    speaker.get_none = True
    out = voice_api.set_volume(delta=10)
    assert out["ok"] and out["volume"] == 40


def test_get_none_without_cache_asks_for_an_absolute_level(speaker):
    speaker.get_none = True
    out = voice_api.set_volume(delta=10)
    assert not out["ok"] and "absolute" in out["error"]


def test_backend_unavailable_is_a_clean_error(monkeypatch):
    def boom():
        raise RuntimeError("ChannelFactoryInitialize failed: no eth0")
    monkeypatch.setattr(voice_api, "_volume_backend", boom)
    assert voice_api.volume()["ok"] is False
    assert "ChannelFactoryInitialize" in voice_api.set_volume(level=5)["error"]


def test_server_routes_exist():
    fastapi = pytest.importorskip("fastapi")  # noqa: F841
    from docs.dashboard import server
    paths = {(r.path, tuple(sorted(r.methods))) for r in server.app.routes if hasattr(r, "methods")}
    assert ("/api/voice/volume", ("GET",)) in paths
    assert ("/api/voice/volume", ("POST",)) in paths
