"""Agents++ shadow authorization probe (ABXAGNTS-911).

Pure unit tests: no robot, no DDS, no network. The real
``agentspp_strands_tools`` package is imported (installed in the dev venv)
but its network entry points are monkeypatched with fakes.
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agentspp_strands_tools as pkg

from tools import agent_log, agentspp_authz, tool_log


# ─────────────────────────────────────────────────────────────────────────
# fakes
# ─────────────────────────────────────────────────────────────────────────
class FakeClient:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.attempts = 0
        self.sessions = 0
        self.agents = 0
        self.creds = 0
        self.pres = 0

    def _maybe_fail(self):
        if self.fail:
            raise RuntimeError("fake Agents++ down")

    def create_session(self, bearer_token=None, mock_human_did=None):
        self.attempts += 1
        self._maybe_fail()
        self.sessions += 1
        return {"sessionId": "sess-1", "humanDid": mock_human_did or "did:web:anon"}

    def create_agent(self, session, name):
        self._maybe_fail()
        self.agents += 1
        return {"did": f"did:agents:{name}", "name": name}

    def issue_credential(self, session, agent, profile=None, columns=None,
                         actions=None, capabilities=None, expires_in=None, **kw):
        self._maybe_fail()
        self.creds += 1
        return "vc.jwt"  # /credentials returns the VC JWT as a JSON string

    def build_presentation(self, session, agent, credential, audience=None,
                           nonce=None, lifetime=None):
        self._maybe_fail()
        self.pres += 1
        return {"presentation": "vp.jwt", "jti": "jti-1"}


def _fake_gate(decision=None, reason=None, error=None):
    """Replacement for pkg.run_authorization_gate capturing its kwargs."""
    calls = []

    def _gate(client, mode, **kw):
        calls.append({"mode": mode, **kw})
        return SimpleNamespace(
            mode=mode,
            attempted=True,
            capability=kw.get("capability"),
            decision=decision,
            reason=reason,
            error=error,
            extra={},
        )

    _gate.calls = calls
    return _gate


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    path = tmp_path / "mem.db"
    monkeypatch.setattr(agent_log, "DB", path)
    monkeypatch.setattr(agentspp_authz, "_artifacts", {})
    monkeypatch.setattr(agentspp_authz, "_failed_at", {})
    agentspp_authz._noticed.clear()
    yield path


def _rows(path):
    c = sqlite3.connect(path)
    c.execute("""CREATE TABLE IF NOT EXISTS agent_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, persona TEXT, role TEXT,
        text TEXT, meta TEXT, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    rows = c.execute("SELECT persona, role, text, meta FROM agent_log ORDER BY id").fetchall()
    c.close()
    return [(p, r, t, json.loads(m) if m else None) for p, r, t, m in rows]


# ─────────────────────────────────────────────────────────────────────────
# capability resolution (NEON vocabulary)
# ─────────────────────────────────────────────────────────────────────────
def test_resolve_map_direct():
    assert agentspp_authz.resolve_neon_capability("g1_turn") == "robot.motion.walk"
    assert agentspp_authz.resolve_neon_capability("g1_walk_forward") == "robot.motion.walk"
    assert agentspp_authz.resolve_neon_capability("g1_stop_move") == "robot.motion.stop"
    assert agentspp_authz.resolve_neon_capability("g1_arm_action") == "robot.arm.gesture"
    assert agentspp_authz.resolve_neon_capability("g1_dds_publish") == "robot.dds.publish.raw"


def test_resolve_map_use_unitree_by_service_op():
    assert agentspp_authz.resolve_neon_capability(
        "use_unitree", {"service_name": "loco", "operation_name": "SetFsmId"}
    ) == "robot.control.fsm.change"
    assert agentspp_authz.resolve_neon_capability(
        "use_unitree", {"service_name": "loco", "operation_name": "Move"}
    ) == "robot.motion.walk"
    assert agentspp_authz.resolve_neon_capability(
        "use_unitree", {"service_name": "motion_switcher", "operation_name": "ReleaseMode"}
    ) == "robot.control.fsm.change"


def test_resolve_map_unmapped_and_readonly():
    assert agentspp_authz.resolve_neon_capability("g1_get_state") is None
    assert agentspp_authz.resolve_neon_capability("memory") is None
    # read-only SDK op: no capability, no probe
    assert agentspp_authz.resolve_neon_capability(
        "use_unitree", {"service_name": "loco", "operation_name": "GetFsmId"}
    ) is None
    # mapped tool name but not use_unitree: args ignored
    assert agentspp_authz.resolve_neon_capability("other", {"service_name": "loco"}) is None


# ─────────────────────────────────────────────────────────────────────────
# mode gating
# ─────────────────────────────────────────────────────────────────────────
def test_mode_off_is_noop(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "off")
    gate = _fake_gate(decision={"decision": "ALLOW", "decision_id": "d-1"})
    monkeypatch.setattr(pkg, "run_authorization_gate", gate)
    out = agentspp_authz.probe("g1_turn", {"vx": 0.2}, "shell", blocking=True)
    assert out is None
    assert gate.calls == []
    assert _rows(agent_log.DB) == []


def test_invalid_mode_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "enforce2")
    assert agentspp_authz.authz_mode() == "off"


def test_unmapped_operation_skips_probe(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "shadow")
    gate = _fake_gate(decision={"decision": "ALLOW", "decision_id": "d-1"})
    monkeypatch.setattr(pkg, "run_authorization_gate", gate)
    out = agentspp_authz.probe("g1_get_state", {}, "shell", blocking=True)
    assert out is None
    assert gate.calls == []


# ─────────────────────────────────────────────────────────────────────────
# bootstrap (session -> agent -> VC -> VP)
# ─────────────────────────────────────────────────────────────────────────
def test_bootstrap_provisions_and_caches(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(pkg, "default_client", lambda *a, **k: fake)
    art = agentspp_authz._get_artifacts("voice")
    assert art is not None
    assert art["session"] == "sess-1"
    assert art["agent_did"] == "did:agents:neon-voice"
    assert art["vc"] == "vc.jwt"
    assert art["vp"] == "vp.jwt"
    # all mapped capabilities granted by default
    assert "robot.motion.walk" in art["capabilities"]
    assert "robot.dds.publish.raw" in art["capabilities"]
    # cached: a second provision is free
    agentspp_authz._get_artifacts("voice")
    assert fake.agents == 1 and fake.creds == 1 and fake.pres == 1


def test_bootstrap_vc_capabilities_override(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(pkg, "default_client", lambda *a, **k: fake)
    monkeypatch.setenv("NEON_AUTHZ_VC_CAPABILITIES", "robot.motion.walk")
    art = agentspp_authz._get_artifacts("telegram")
    assert art["capabilities"] == "robot.motion.walk"


def test_bootstrap_failure_cooldown(monkeypatch):
    fake = FakeClient(fail=True)
    monkeypatch.setattr(pkg, "default_client", lambda *a, **k: fake)
    assert agentspp_authz._get_artifacts("shell") is None
    assert agentspp_authz._get_artifacts("shell") is None  # cooldown: no retry storm
    assert fake.attempts == 1


def test_bootstrap_mock_human_did_default(monkeypatch):
    seen = {}

    class SpyClient(FakeClient):
        def create_session(self, bearer_token=None, mock_human_did=None):
            seen["human"] = mock_human_did
            return super().create_session(bearer_token, mock_human_did)

    monkeypatch.setattr(pkg, "default_client", lambda *a, **k: SpyClient())
    agentspp_authz._get_artifacts("shell")
    assert seen["human"] == "did:web:neon-dev"


# ─────────────────────────────────────────────────────────────────────────
# probe outcome recording
# ─────────────────────────────────────────────────────────────────────────
def test_probe_blocking_records_decision(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "shadow")
    gate = _fake_gate(decision={"decision": "ALLOW", "decision_id": "d-42",
                                "reason": "capability granted"})
    monkeypatch.setattr(pkg, "run_authorization_gate", gate)
    agentspp_authz._artifacts["shell"] = {
        "session": "s", "agent_did": "a", "vc": "v", "vp": "p", "capabilities": "c",
    }
    meta = agentspp_authz.probe("g1_turn", {"vx": 0.2}, "shell",
                                tool_use_id="tu-1", blocking=True)
    assert meta is not None
    assert meta["decision"] == "ALLOW"
    assert meta["decision_id"] == "d-42"
    assert meta["tool_use_id"] == "tu-1"
    # gate was called with NEON capability + artifacts
    call = gate.calls[0]
    assert call["capability"] == "robot.motion.walk"
    assert call["session"] == "s" and call["agent_did"] == "a"
    assert call["vc"] == "v" and call["vp"] == "p"
    assert call["persona"] == "shell"
    # logged with role=authz, correlatable
    rows = [r for r in _rows(agent_log.DB) if r[1] == "authz"]
    assert len(rows) == 1
    assert rows[0][0] == "shell"
    assert "d-42" in rows[0][2]
    assert rows[0][3]["decision"] == "ALLOW"


def test_probe_records_denay_and_error(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "shadow")
    gate = _fake_gate(decision={"decision": "DENY", "decision_id": "d-9",
                                "reason": "credential missing capability"})
    monkeypatch.setattr(pkg, "run_authorization_gate", gate)
    agentspp_authz._artifacts["shell"] = {
        "session": "s", "agent_did": "a", "vc": "v", "vp": "p", "capabilities": "c",
    }
    meta = agentspp_authz.probe("g1_dds_publish", {}, "shell", blocking=True)
    assert meta["decision"] == "DENY"


def test_probe_survives_gate_exception(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "shadow")

    def boom(client, mode, **kw):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(pkg, "run_authorization_gate", boom)
    agentspp_authz._artifacts["shell"] = {
        "session": "s", "agent_did": "a", "vc": "v", "vp": "p", "capabilities": "c",
    }
    meta = agentspp_authz.probe("g1_turn", {}, "shell", blocking=True)
    assert meta["decision"] is None
    assert "gate error" in (meta["reason"] or "")


def test_probe_shadow_runs_async_and_never_blocks(monkeypatch):
    monkeypatch.setenv("NEON_AUTHZ_MODE", "shadow")
    gate = _fake_gate(decision={"decision": "ALLOW", "decision_id": "d-x"})
    monkeypatch.setattr(pkg, "run_authorization_gate", gate)
    agentspp_authz._artifacts["voice"] = {
        "session": "s", "agent_did": "a", "vc": "v", "vp": "p", "capabilities": "c",
    }
    t0 = time.monotonic()
    out = agentspp_authz.probe("g1_walk_forward", {"steps": 2}, "voice")
    assert out is None  # fire-and-forget
    assert time.monotonic() - t0 < 0.2  # did not wait on the gate
    # Wait for the daemon thread to FINISH (gate called AND log row landed in the
    # tmp DB). Ending earlier would let the thread write to the restored real DB.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        authz_rows = [r for r in _rows(agent_log.DB) if r[1] == "authz"]
        if gate.calls and authz_rows:
            break
        time.sleep(0.02)
    assert gate.calls, "shadow probe thread never ran the gate"
    assert authz_rows, "shadow probe row never landed in the tmp agent_log"
    assert authz_rows[0][0] == "voice"


# ─────────────────────────────────────────────────────────────────────────
# prompt hygiene: authz rows must not leak into the system prompt
# ─────────────────────────────────────────────────────────────────────────
def test_format_for_prompt_excludes_authz_role():
    agent_log.record("shell", "tool", "g1_get_state ran fine")
    agent_log.record("shell", "authz", "SECRET-AUTHZ-LINE cap=robot.motion.walk")
    block = agent_log.format_for_prompt(exclude_persona=None)
    assert "g1_get_state ran fine" in block
    assert "SECRET-AUTHZ-LINE" not in block


# ─────────────────────────────────────────────────────────────────────────
# LoggedTool wiring: probe fires, masked args preserved, failures contained
# ─────────────────────────────────────────────────────────────────────────
class _FakeInner:
    tool_name = "fake-inner"

    def __call__(self, *a, **k):
        return {"status": "success", "content": [{"text": "ok"}]}

    def stream(self, *a, **k):
        async def _gen():
            yield {"status": "success", "content": [{"text": "ok"}]}

        return _gen()


def test_loggedtool_direct_call_probes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        agentspp_authz, "probe", lambda name, args, persona, tool_use_id=None:
        calls.append((name, args, persona)) or None
    )
    t = tool_log.LoggedTool(_FakeInner(), persona="shell")
    t(service_name="loco", operation_name="SetFsmId", fsm_id=5)
    assert len(calls) == 1
    name, args, persona = calls[0]
    assert name == "fake-inner"
    assert persona == "shell"
    assert args["operation_name"] == "SetFsmId"


def test_loggedtool_stream_probes_and_survives_probe_bomb(monkeypatch):
    def bomb(name, args, persona, tool_use_id=None):
        raise RuntimeError("probe must not kill the tool")

    monkeypatch.setattr(agentspp_authz, "probe", bomb)
    t = tool_log.LoggedTool(_FakeInner(), persona="shell")
    tu = {"toolUseId": "tu-9", "input": {"service_name": "loco",
                                          "operation_name": "SetFsmId",
                                          "api_token": "topsecret"}}

    async def _collect(gen):
        out = []
        async for e in gen:
            out.append(e)
        return out

    results = asyncio.run(_collect(t.stream(tu, None)))
    assert results[-1]["status"] == "success"
    row = [r for r in _rows(agent_log.DB) if r[3] and r[3].get("tool_use_id") == "tu-9"]
    assert row, "tool log row missing"


def test_mask_preserves_unitree_routing_args():
    masked = tool_log._mask({
        "service_name": "loco",
        "operation_name": "SetFsmId",
        "api_token": "topsecret",
    })
    assert masked["service_name"] == "loco"
    assert masked["operation_name"] == "SetFsmId"
    assert "topsecret" not in json.dumps(masked)
