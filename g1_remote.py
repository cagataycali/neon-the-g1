#!/usr/bin/env python3
"""
g1_remote — bridge a real Unitree G1's DDS state into the strands mesh.

Why this exists (instead of `Robot("unitree_g1", mode="real")`):

  1. strands_robots/hardware_robot.py::_create_minimal_config has NO entry for
     `unitree_g1` — its `config_mapping` only knows so100/so101/koch/openarm.
     Calling `Robot("unitree_g1", mode="real", ...)` raises:
         ValueError: Unsupported robot type: unitree_g1.
     Despite the registry advertising `has_real=True`.

  2. lerobot's `UnitreeG1` driver requires a heavy stack (torch, mujoco,
     IK, locomotion controller) that is not appropriate to install on the
     G1's onboard Python 3.11 venv (strands-robots requires >=3.12).

  3. CycloneDDS is multicast on the robot's internal `eth0` (192.168.123.0/24)
     — that subnet is not reachable from any LAN host. The G1 itself is the
     only correct DDS endpoint.

So this file runs **on the G1** alongside neon-the-g1/, uses the existing
`tools/_g1_common.py` SDK helpers to read LowState + FSM, and publishes the
exact `strands/{peer}/**` schema that bridge.py subscribes to.

Topology:
    G1 (eth0=192.168.123.x ← DDS, wlan0=192.168.1.175 ← Zenoh)
        │
        │  (this script)  →  zenoh peer "g1"
        │                    publishes strands/g1/{presence,state}
        ▼
    Bridge on Jetson (192.168.1.151:8000)  ← already subscribed strands/**
        │
        ▼
    Browser dashboard

Usage on the G1:
    cd /home/unitree/neon-the-g1 && source env.sh
    python3 /tmp/g1_remote.py [--peer g1] [--state-hz 25] [--presence-hz 1]
"""
from __future__ import annotations
import argparse, json, logging, os, signal, socket, sys, threading, time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("g1_remote")


def _import_g1_tools():
    """Import neon tools/_g1_common (must be on PYTHONPATH or via cwd)."""
    # Try a few likely locations
    candidates = [
        "/home/unitree/neon-the-g1",
    ]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.insert(0, here)
    candidates.insert(0, os.path.dirname(here))
    for c in candidates:
        if os.path.isdir(os.path.join(c, "tools")):
            if c not in sys.path:
                sys.path.insert(0, c)
            break
    # Avoid running tools/__init__.py (drags in strands/devduck). Import directly.
    tools_dir = os.path.join(c, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import _g1_common as g1c  # type: ignore
    return g1c


def _joint_names_from_lowstate(ls) -> list[str]:
    """Best-effort generic joint name list — Unitree G1 has 29 body DOFs."""
    n = len(getattr(ls, "motor_state", []) or [])
    # G1+ has 29 main motors; lerobot's canonical names are useful but optional.
    # Keeping it simple → j0..jN-1; the dashboard already handles dict {name:val}.
    return [f"j{i}" for i in range(n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--peer",         default="g1",   help="zenoh peer_id (becomes strands/{peer}/**)")
    ap.add_argument("--state-hz",     type=float, default=25.0)
    ap.add_argument("--presence-hz",  type=float, default=1.0)
    ap.add_argument("--iface",        default="eth0", help="DDS network interface")
    args = ap.parse_args()

    # Import zenoh
    try:
        import zenoh
    except ImportError:
        log.error("eclipse-zenoh not installed in this venv")
        return 1

    # Bring up DDS via neon helpers
    g1c = _import_g1_tools()
    init_err = g1c.ensure_dds(args.iface)
    if init_err:
        log.warning("DDS init said: %s", init_err)
    sub_err = g1c._ensure_lowstate_subscriber()
    if sub_err:
        log.warning("lowstate sub: %s", sub_err)

    log.info("opening zenoh peer for %s ...", args.peer)
    zsess = zenoh.open(zenoh.Config())
    log.info("zenoh peer up; publishing strands/%s/{presence,state}", args.peer)

    # Pull a baseline LowState so we know joint count
    ls0 = g1c._get_lowstate_cached(timeout=3.0)
    if ls0 is None:
        log.error("no LowState received from G1 within 3s — is the robot up?")
        # keep going; later iterations may succeed once LowState arrives
        n_joints = 29
    else:
        n_joints = len(getattr(ls0, "motor_state", []) or [])
        log.info("got first LowState — %d motors", n_joints)

    joint_names = [f"j{i}" for i in range(n_joints)]

    presence_payload = {
        "robot_id":    args.peer,
        "robot_type":  "humanoid",
        "hostname":    socket.gethostname(),
        "cameras":     [],            # extend later if D435 / head cam wired
        "topics":      ["state", "imu", "health"],
        "action_keys": [f"{n}.pos" for n in joint_names] + ["gripper.pos"],
        "task_status": "idle",
        "instruction": "",
        "connected":   True,
        "mode":        "real",
    }

    stop = {"flag": False}
    def _stop(*_):
        stop["flag"] = True
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    state_period    = 1.0 / max(args.state_hz, 0.1)
    presence_period = 1.0 / max(args.presence_hz, 0.1)
    last_state = 0.0
    last_pres  = 0.0
    seq = 0

    while not stop["flag"]:
        now = time.time()

        # ── presence ─────────────────────────────────────────────────────
        if now - last_pres > presence_period:
            try:
                fsm = g1c.read_fsm_id() if hasattr(g1c, "read_fsm_id") else None
            except Exception:
                fsm = None
            payload = dict(presence_payload, timestamp=now,
                           task_status=("ready" if fsm in (500, 501, 801) else f"fsm_{fsm}"))
            try:
                zsess.put(f"strands/{args.peer}/presence", json.dumps(payload).encode())
            except Exception as e:
                log.warning("presence put failed: %s", e)
            last_pres = now

        # ── state ────────────────────────────────────────────────────────
        if now - last_state > state_period:
            ls = g1c._get_lowstate_cached(timeout=0.05)
            if ls is not None:
                ms = list(getattr(ls, "motor_state", []) or [])
                joints = {}
                for i, m in enumerate(ms[:n_joints]):
                    q = getattr(m, "q", None)
                    if q is not None:
                        joints[f"j{i}.pos"] = float(q)
                imu = getattr(ls, "imu_state", None)
                rpy = list(getattr(imu, "rpy", []) or []) if imu else []
                payload = {
                    "peer_id": args.peer,
                    "t": now,
                    "seq": seq,
                    "joints": joints,
                    "task": {"status": "real", "instruction": "", "steps": seq},
                }
                if rpy:
                    payload["imu"] = {"rpy": rpy}
                try:
                    zsess.put(f"strands/{args.peer}/state", json.dumps(payload).encode())
                except Exception as e:
                    log.warning("state put failed: %s", e)
                seq += 1
            last_state = now

        time.sleep(0.005)

    log.info("shutting down zenoh ...")
    try:
        zsess.close()
    except Exception as e:
        log.warning("zenoh close: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
