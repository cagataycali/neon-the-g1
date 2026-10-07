"""GET /api/camera/<id>/snapshot answers 503 'no frame' instead of a 200 slate.

2026-10-07 18:55Z: the thinker received the dashboard's "NO CAMERA SIGNAL"
placeholder as a real photo (HTTP 200) and described the slate. Machines read
snapshots; they must hear "no frame". The MJPEG stream keeps the slate for
the browser.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 3000 + b"\xff\xd9"


class _Cam:
    def __init__(self, jpg, err=None, age=None):
        self._jpg, self._err, self._age = jpg, err, age

    def latest(self):
        return self._jpg

    def status(self):
        return {"error": self._err, "last_frame_age": self._age}


class _Mgr:
    def __init__(self, cams):
        self.cams = cams

    def get(self, cam_id):
        return self.cams.get(cam_id)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DASHBOARD_NO_AUTH", "1")
    sys.modules.pop("docs.dashboard.server", None)
    import docs.dashboard.server as server
    mgr = _Mgr({"live": _Cam(JPEG, age=0.1), "cold": _Cam(None, err="pyrealsense2 stalled", age=None)})
    monkeypatch.setattr(server, "_get_cam_mgr", lambda: mgr)
    monkeypatch.setattr(server._auth, "AUTH_ENABLED", False, raising=False)
    return TestClient(server.app)


def test_live_camera_returns_the_jpeg(client):
    r = client.get("/api/camera/live/snapshot")
    assert r.status_code == 200 and r.content == JPEG


def test_no_frame_is_a_503_not_a_slate(client):
    r = client.get("/api/camera/cold/snapshot")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "no frame" and body["detail"] == "pyrealsense2 stalled"
    assert r.headers.get("Retry-After") == "3"
    assert not r.content.startswith(b"\xff\xd8"), "never a picture when there is no frame"
