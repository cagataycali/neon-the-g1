"""Agents++ authorization probe for NEON tool calls (ABXAGNTS-911, shadow phase).

What this is
    Before a mapped (high-risk) operation executes, ask Agents++
    ``POST /authorization/decide`` (via the ``agentspp-strands-tools``
    package) and record the ALLOW/DENY + ``decision_id`` in the journal
    (stderr) and in ``agent_log`` under role="authz". Shadow mode: the
    decision is telemetry — it never blocks the tool. Blocking enforcement
    is a later phase (ABXAGNTS-912/913) and will call ``probe(...,
    blocking=True)``.

What this is NOT
    - The capability vocabulary owner is NEON: the maps below define
      NEON's own ``operation -> capability`` names. The package is
      host-agnostic and ships none of them.
    - Not a safety mechanism. FSM/locks/clamps inside the tools stay as
      they are; the gate answers "is this agent allowed", not "is this
      physically safe".

Modes — ``NEON_AUTHZ_MODE`` env, default ``off`` (behavioral baseline):
    off      no Agents++ calls, zero overhead
    shadow   probe mapped operations, log the decision, never block
    enforce  foundation phase: identical to shadow + explicit DENY marker
             (blocking is deferred to ABXAGNTS-912/913)

Other env vars:
    AGENTSPP_BASE_URL            Agents++ instance (default http://localhost:3100)
    AGENTSPP_BEARER_TOKEN        production OIDC token for the session
    NEON_AUTHZ_HUMAN_DID         mock human DID in dev (default did:web:neon-dev)
    NEON_AUTHZ_VC_CAPABILITIES   comma-separated capability subset to issue in
                                 the VC (default: all of ALL_CAPABILITIES).
                                 Narrow it to observe DENY outcomes in shadow.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Any, Dict, List, Mapping, Optional

# ─────────────────────────────────────────────────────────────────────────
# NEON operation -> capability vocabulary
# First wave (assessment §9.1): walk / FSM change / raw DDS publish.
# Arm gestures (assessment §9.2) included: they physically move the robot.
# Adding a row here is how the enforcement scope grows in later phases.
# ─────────────────────────────────────────────────────────────────────────
NEON_OPERATION_CAPABILITY_MAP: Dict[str, str] = {
    # locomotion (tools/g1_locomotion.py)
    "g1_move_velocity": "robot.motion.walk",
    "g1_walk_forward": "robot.motion.walk",
    "g1_turn": "robot.motion.walk",
    "g1_wave_hand_loco": "robot.motion.walk",
    "g1_shake_hand_loco": "robot.motion.walk",
    "g1_stop_move": "robot.motion.stop",
    # arm (tools/g1_arm.py)
    "g1_arm_action": "robot.arm.gesture",
    "g1_release_arm": "robot.arm.gesture",
    # DDS escape hatch (tools/g1_dds.py)
    "g1_dds_publish": "robot.dds.publish.raw",
}

# use_unitree is the universal SDK hammer: resolve (service, operation).
_USE_UNITREE_OPS: Dict[tuple, str] = {
    ("loco", "SetFsmId"): "robot.control.fsm.change",
    ("loco", "SetVelocity"): "robot.motion.walk",
    ("loco", "Move"): "robot.motion.walk",
    ("loco", "WaveHand"): "robot.motion.walk",
    ("loco", "ShakeHand"): "robot.motion.walk",
    ("loco", "StopMove"): "robot.motion.stop",
    ("motion_switcher", "ReleaseMode"): "robot.control.fsm.change",
}

ALL_CAPABILITIES: List[str] = sorted(
    set(NEON_OPERATION_CAPABILITY_MAP.values()) | set(_USE_UNITREE_OPS.values())
)


def resolve_neon_capability(
    tool_name: str, args: Optional[Mapping[str, Any]] = None
) -> Optional[str]:
    """NEON's own operation -> capability resolution. None = not mapped."""
    cap = NEON_OPERATION_CAPABILITY_MAP.get(tool_name)
    if cap:
        return cap
    if tool_name == "use_unitree" and isinstance(args, Mapping):
        key = (str(args.get("service_name", "")), str(args.get("operation_name", "")))
        if key in _USE_UNITREE_OPS:
            return _USE_UNITREE_OPS[key]
    return None


_VALID_MODES = ("off", "shadow", "enforce")


def authz_mode() -> str:
    m = os.getenv("NEON_AUTHZ_MODE", "off").strip().lower()
    return m if m in _VALID_MODES else "off"


# ─────────────────────────────────────────────────────────────────────────
# Artifact bootstrap (per persona): session -> agent -> VC -> VP
# ─────────────────────────────────────────────────────────────────────────
_RETRY_COOLDOWN_S = 60.0
_lock = threading.Lock()
_artifacts: Dict[str, Dict[str, Any]] = {}
_failed_at: Dict[str, float] = {}
_noticed: set = set()


def _notice_once(key: str, msg: str) -> None:
    if key in _noticed:
        return
    _noticed.add(key)
    try:
        print(f"[authz] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _load_package():
    """Import the package lazily so NEON runs fine without it installed."""
    try:
        import agentspp_strands_tools as pkg
        return pkg
    except Exception as e:
        _notice_once(
            "pkg",
            f"NEON_AUTHZ_MODE is set but agentspp-strands-tools is not "
            f"importable: {e!r}. Install it (pip install "
            f"-e /path/to/agentspp-strands-tools, or the [agentspp] extra). "
            f"Probes disabled.",
        )
        return None


def _get_artifacts(persona: str) -> Optional[Dict[str, Any]]:
    """Provision (and cache) session/agent_did/vc/vp for a persona.

    Returns None on failure; retries are rate-limited to one attempt per
    ``_RETRY_COOLDOWN_S`` so a downed Agents++ instance can't storm the
    tool path.
    """
    pkg = _load_package()
    if pkg is None:
        return None
    with _lock:
        cached = _artifacts.get(persona)
        if cached:
            return cached
        last = _failed_at.get(persona)
        if last is not None and time.monotonic() - last < _RETRY_COOLDOWN_S:
            return None
    try:
        client = pkg.default_client()  # reads AGENTSPP_BASE_URL
        bearer = os.getenv("AGENTSPP_BEARER_TOKEN")
        session_resp = (
            client.create_session(bearer_token=bearer)
            if bearer
            else client.create_session(
                mock_human_did=os.getenv("NEON_AUTHZ_HUMAN_DID", "did:web:neon-dev")
            )
        )
        session = session_resp["sessionId"]
        agent = client.create_agent(session, name=f"neon-{persona}")
        agent_did = agent["did"]
        caps_env = os.getenv("NEON_AUTHZ_VC_CAPABILITIES", "")
        caps = (
            [c.strip() for c in caps_env.split(",") if c.strip()]
            if caps_env
            else list(ALL_CAPABILITIES)
        )
        vc = client.issue_credential(
            session, agent_did, profile="capability",
            capabilities=[{"action": c} for c in caps],
        )
        if isinstance(vc, Mapping):
            vc = vc.get("vc") or vc.get("credential")
        pres = client.build_presentation(session, agent_did, vc)
        vp = pres["presentation"] if isinstance(pres, Mapping) else pres
        artifacts = {
            "session": session,
            "agent_did": agent_did,
            "vc": vc,
            "vp": vp,
            "capabilities": ",".join(caps),
        }
        with _lock:
            _artifacts[persona] = artifacts
            _failed_at.pop(persona, None)
        print(
            f"[authz] artifacts ready persona={persona} agent={agent_did} "
            f"capabilities={artifacts['capabilities']}",
            file=sys.stderr, flush=True,
        )
        return artifacts
    except Exception as e:
        with _lock:
            _failed_at[persona] = time.monotonic()
        _notice_once(
            f"bootstrap:{persona}",
            f"Agents++ artifact bootstrap failed (persona={persona}): {e!r}. "
            f"Retrying at most every {_RETRY_COOLDOWN_S:.0f}s.",
        )
        return None


def _reset_bootstrap_state() -> None:
    """Test hook: clear caches so a test can re-provision."""
    with _lock:
        _artifacts.clear()
        _failed_at.clear()
    _noticed.clear()


# ─────────────────────────────────────────────────────────────────────────
# The probe
# ─────────────────────────────────────────────────────────────────────────
def _probe_sync(
    tool_name: str,
    args: Optional[Mapping[str, Any]],
    persona: str,
    capability: str,
    tool_use_id: Optional[str],
) -> Optional[Any]:
    """Run one decide call and record the outcome. Never raises."""
    from . import agent_log

    mode = authz_mode()
    meta: Dict[str, Any] = {
        "tool": tool_name,
        "capability": capability,
        "mode": mode,
        "decision": None,
        "decision_id": None,
        "reason": None,
        "error": None,
    }
    if tool_use_id:
        meta["tool_use_id"] = tool_use_id

    artifacts = _get_artifacts(persona)
    if artifacts is None:
        meta["reason"] = "artifacts unavailable"
    else:
        pkg = _load_package()
        try:
            res = pkg.run_authorization_gate(
                pkg.default_client(),
                mode,
                session=artifacts["session"],
                agent_did=artifacts["agent_did"],
                persona=persona,
                channel=persona,
                tool=tool_name,
                operation=tool_name,
                vc=artifacts["vc"],
                vp=artifacts["vp"],
                capability=capability,
                context={"args": dict(args) if isinstance(args, Mapping) else args},
            )
            decision = res.decision or {}
            meta["decision"] = decision.get("decision")
            meta["decision_id"] = decision.get("decision_id")
            meta["reason"] = decision.get("reason") or res.reason
            meta["error"] = (
                {"status": res.error.get("status"), "error": res.error.get("error")}
                if res.error
                else None
            )
        except Exception as e:  # the gate contract is never-raise; belt & braces
            meta["reason"] = f"gate error: {e!r}"

    verdict = meta["decision"] or ("ERROR" if meta["error"] or meta["reason"] == "artifacts unavailable" else "UNKNOWN")
    line = (
        f"[authz-{mode} {persona}] {tool_name} cap={capability} "
        f"-> {verdict} decision_id={meta['decision_id']} "
        f"reason={meta['reason'] or ''}"
    )
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        agent_log.record(persona, "authz", line[:2000], meta=meta)
    except Exception:
        pass
    return meta


def probe(
    tool_name: str,
    args: Optional[Mapping[str, Any]],
    persona: str,
    tool_use_id: Optional[str] = None,
    blocking: bool = False,
) -> Optional[Any]:
    """Pre-invocation authorization probe (shadow phase).

    No-op unless ``NEON_AUTHZ_MODE`` is shadow/enforce AND the operation
    resolves to a capability. In the shadow phase the probe runs on a
    daemon thread so the tool path (especially the latency-critical voice
    path) never waits on Agents++. Returns None (fire-and-forget) unless
    ``blocking=True`` (enforcement phase, later ticket).
    """
    try:
        if authz_mode() == "off":
            return None
        capability = resolve_neon_capability(tool_name, args)
        if capability is None:
            return None
        if blocking:
            return _probe_sync(tool_name, args, persona, capability, tool_use_id)
        threading.Thread(
            target=_probe_sync,
            args=(tool_name, args, persona, capability, tool_use_id),
            daemon=True,
        ).start()
        return None
    except Exception as e:  # the authz path must never take down a tool
        _notice_once(f"probe:{tool_name}", f"authz probe failed: {e!r}")
        return None
