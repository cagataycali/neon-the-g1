"""g1_walk_forward / g1_turn / g1_move_velocity report what the robot DID.

Fake LocoClient + fake odometry, no DDS. The SDK answering rc=0 is not
motion: only a measured displacement makes moved=true.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("strands")
from tools import g1_locomotion as loco_mod  # noqa: E402
from tools._g1_common import pose_delta  # noqa: E402


class FakeLoco:
    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def SetVelocity(self, vx, vy, vyaw, duration):
        self.calls.append(("SetVelocity", vx, vy, vyaw, duration))
        return self.rc

    def Move(self, vx, vy, vyaw, continous_move=False):
        self.calls.append(("Move", vx, vy, vyaw, continous_move))


def _flat(env):
    """status + merged json blob + text of a Strands ToolResult."""
    out = {"status": env["status"], "text": ""}
    for c in env["content"]:
        if "text" in c:
            out["text"] += c["text"]
        if "json" in c:
            out.update(c["json"])
    return out


@pytest.fixture
def rig(monkeypatch):
    """Patch the DDS surface of g1_locomotion; returns a dict to configure poses."""
    fake = FakeLoco()
    cfg = {"poses": [], "fsm": 501, "slept": []}
    monkeypatch.setattr(loco_mod, "ensure_dds", lambda net="eth0": None)
    monkeypatch.setattr(loco_mod, "get_loco_client", lambda *a, **k: fake)
    monkeypatch.setattr(loco_mod, "read_fsm_id", lambda loco=None: cfg["fsm"])
    monkeypatch.setattr(loco_mod, "_sleep", lambda s: cfg["slept"].append(s))

    def read_pose(timeout=1.0, max_age=0.5):
        if not cfg["poses"]:
            return None
        return cfg["poses"].pop(0)
    monkeypatch.setattr(loco_mod, "read_pose", read_pose)
    cfg["loco"] = fake
    return cfg


def _pose(x, y, yaw, source="odom"):
    return {"x": x, "y": y, "yaw": yaw, "ts": 0.0, "source": source, "stale": False}


def test_walk_forward_reports_measured_motion(rig):
    rig["poses"] = [_pose(0.0, 0.0, 0.0), _pose(0.27, 0.02, 0.01)]
    r = _flat(loco_mod.g1_walk_forward(distance=0.3))
    assert r["status"] == "success" and r["moved"] is True
    assert r["rc"] == 0 and r["requested_m"] == 0.3 and r["measured_m"] == 0.271
    assert r["text"].startswith("moved 0.27 m of 0.30 m requested")
    # 0.3 m at the default 0.2 m/s = 1.5 s, then settle 0.5 s before measuring
    name, vx, vy, vyaw, dur = rig["loco"].calls[0]
    assert (name, vx, vy, vyaw) == ("SetVelocity", 0.2, 0.0, 0.0) and abs(dur - 1.5) < 1e-9
    assert len(rig["slept"]) == 1 and abs(rig["slept"][0] - 2.0) < 1e-9


def test_walk_forward_rc0_without_displacement_is_an_error(rig):
    rig["poses"] = [_pose(0.0, 0.0, 0.0), _pose(0.004, -0.002, 0.0)]
    r = _flat(loco_mod.g1_walk_forward(distance=0.3))
    assert r["status"] == "error" and r["moved"] is False and r["rc"] == 0
    assert "SDK accepted (rc=0) but no displacement measured" in r["text"]
    assert "did NOT move" in r["text"]
    assert r["measured_m"] == 0.004


def test_walk_forward_without_odometry_never_claims_motion(rig):
    rig["poses"] = []   # read_pose -> None twice
    r = _flat(loco_mod.g1_walk_forward(distance=0.5))
    assert r["moved"] is None and r["measured_m"] is None
    assert "could not be verified" in r["text"] and "Do not claim" in r["text"]


def test_walk_forward_clamps_tiny_requests_and_slow_speeds(rig):
    rig["poses"] = [_pose(0, 0, 0), _pose(0.1, 0, 0)]
    r = _flat(loco_mod.g1_walk_forward(distance=0.02, speed=0.05))
    # 2 cm -> 0.1 m floor; 0.05 m/s -> 0.15 m/s floor for short walks; backwards keeps the sign
    assert rig["loco"].calls[0][1] == 0.15
    assert abs(rig["loco"].calls[0][4] - (0.1 / 0.15)) < 1e-9
    assert r["requested_m"] == 0.1 and r["moved"] is True
    rig["poses"] = [_pose(0, 0, 0), _pose(-0.9, 0, 0)]
    r = _flat(loco_mod.g1_walk_forward(distance=-5.0, speed=9.0))
    assert rig["loco"].calls[-1][1] == -0.5 and abs(rig["loco"].calls[-1][4] - 2.0) < 1e-9
    assert r["requested_m"] == 1.0 and r["moved"] is True


def test_turn_measures_yaw_and_wraps(rig):
    # yaw crosses the +pi seam: 3.0 -> -3.0 is a +0.283 rad CCW turn, not -6 rad
    rig["poses"] = [_pose(0, 0, 3.0), _pose(0, 0, -3.0)]
    r = _flat(loco_mod.g1_turn(angle_rad=0.3))
    assert r["moved"] is True and abs(r["measured_rad"] - 0.283) < 1e-3
    assert "rad requested" in r["text"]
    # IMU-only fallback (no x/y) still verifies a turn
    rig["poses"] = [_pose(None, None, 0.0, "imu"), _pose(None, None, 0.01, "imu")]
    r = _flat(loco_mod.g1_turn(angle_rad=0.5))
    assert r["moved"] is False and r["status"] == "error" and r["measured_m"] is None


def test_fsm_gate_and_sdk_errors_are_not_motion(rig):
    rig["fsm"] = 0
    r = _flat(loco_mod.g1_walk_forward(distance=0.3))
    assert r["status"] == "error" and "Walking requires 501" in r["text"]
    assert rig["loco"].calls == [] and "moved" not in r
    rig["fsm"] = 501
    rig["loco"].rc = 3104
    rig["poses"] = [_pose(0, 0, 0)]
    r = _flat(loco_mod.g1_walk_forward(distance=0.3))
    assert r["status"] == "error" and r["rc"] == 3104 and "did not move" in r["text"]
    assert rig["slept"] == []   # no wait when the SDK refused


def test_continuous_move_keeps_its_semantics(rig):
    r = _flat(loco_mod.g1_move_velocity(vx=0.1, vy=0.0, vyaw=0.0, continuous=True))
    assert r["status"] == "success" and rig["loco"].calls == [("Move", 0.1, 0.0, 0.0, True)]
    assert "moved" not in r and rig["slept"] == []


def test_pose_delta_shapes():
    assert pose_delta(None, _pose(0, 0, 0)) == {"measured_m": None, "measured_rad": None, "source": None}
    d = pose_delta(_pose(1, 1, 0.1), _pose(1.3, 1.4, 0.3))
    assert d["measured_m"] == 0.5 and d["measured_rad"] == 0.2 and d["source"] == "odom"
