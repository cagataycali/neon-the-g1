"""XR bridge — WSS server that turns Quest 3 WebXR poses into G1 motion.

Two roles in one process:
  1. HTTPS static server  — serves docs/teleop/index.html to the Quest browser
     (WebXR mandates a secure context). Default :8013.
  2. WSS pose server      — receives Vuer-format HAND_MOVE/CONTROLLER_MOVE/
     CAMERA_MOVE JSON, fills a WebXRPoseSource, and (optionally) runs the
     xr_teleoperate IK→DDS control loop on the G1. Default :8012, path /xr.

Run on the robot/Jetson::

    python -m neon.teleop.xr_bridge --arm G1_29 --network-interface eth0 \
        --cert ~/.config/xr_teleoperate/cert.pem --key ~/.config/xr_teleoperate/key.pem

    # dry-run (no robot/DDS, just verify the pose stream from the headset):
    python -m neon.teleop.xr_bridge --dry-run

Then on the Quest 3, open  https://<robot-ip>:8013/  → "Enter XR & Teleop".

Safety: the control loop only starts after the first valid motion frame, ramps
arm speed gradually (speed_gradual_max), and refuses to walk. Hand pinch /
controller trigger drives the gripper; aButton (right) requests stop.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import ssl
import sys
import threading
from functools import partial
from pathlib import Path

from .pose_source import WebXRPoseSource

log = logging.getLogger("neon.teleop.xr_bridge")

# repo root (…/neon-the-g1) and the html dir
_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parent.parent
_HTML_DIR = _REPO_ROOT / "docs" / "teleop"


def _resolve_cert(cert, key):
    """Mirror televuer's cert resolution (env → ~/.config → repo)."""
    cert = cert or os.getenv("XR_TELEOP_CERT")
    key = key or os.getenv("XR_TELEOP_KEY")
    if cert and key:
        return cert, key
    conf = Path.home() / ".config" / "xr_teleoperate"
    if (conf / "cert.pem").exists() and (conf / "key.pem").exists():
        return str(conf / "cert.pem"), str(conf / "key.pem")
    if (_REPO_ROOT / "cert.pem").exists():
        return str(_REPO_ROOT / "cert.pem"), str(_REPO_ROOT / "key.pem")
    return None, None


class XRBridge:
    """Owns the pose source + WS server; optionally drives the G1 control loop."""

    def __init__(self, use_hand_tracking=True, arm="G1_29", network_interface=None,
                 motion=False, sim=False, dry_run=False):
        self.source = WebXRPoseSource(use_hand_tracking=use_hand_tracking)
        self.use_hand_tracking = use_hand_tracking
        self.arm = arm
        self.network_interface = network_interface
        self.motion = motion
        self.sim = sim
        self.dry_run = dry_run
        self._clients = set()
        self._running = False

    # ───────────────── WS message handling ─────────────────
    def handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        t = msg.get("type")
        v = msg.get("value") or {}
        if t == "HAND_MOVE":
            self.source.update_hands(v)
        elif t == "CONTROLLER_MOVE":
            self.source.update_controllers(v)
        elif t == "CAMERA_MOVE":
            cam = (v.get("camera") or {}).get("matrix")
            if cam:
                self.source.update_camera(cam)

    async def _ws_handler(self, websocket):
        peer = getattr(websocket, "remote_address", "?")
        self._clients.add(websocket)
        log.info("XR client connected: %s", peer)
        try:
            await websocket.send(json.dumps({"type": "STATE", "value": {"ok": True, "arm": self.arm}}))
            async for raw in websocket:
                self.handle_message(raw)
        except Exception as e:
            log.debug("ws closed: %s", e)
        finally:
            self._clients.discard(websocket)
            log.info("XR client disconnected: %s", peer)

    async def serve_ws(self, host, port, ssl_ctx):
        import websockets
        log.info("WSS pose server → wss://%s:%d/xr", host, port)
        async with websockets.serve(self._ws_handler, host, port, ssl=ssl_ctx, max_size=2**20):
            await asyncio.Future()  # run forever

    # ───────────────── HTTPS static (serves the HTML) ─────────────────
    def serve_https(self, host, port, certfile, keyfile):
        import http.server
        import socketserver

        handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(_HTML_DIR))

        class _Srv(socketserver.ThreadingTCPServer):
            allow_reuse_address = True

        httpd = _Srv((host, port), handler)
        if certfile and keyfile:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile, keyfile)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            scheme = "https"
        else:
            scheme = "http"
        log.info("Static server → %s://%s:%d/  (serving %s)", scheme, host, port, _HTML_DIR)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    # ───────────────── G1 control loop (IK→DDS) ─────────────────
    def run_control_loop(self, frequency=30.0):
        """Consume self.source via xr_teleoperate's wrapper math + IK → DDS.

        Imports are lazy so the bridge runs in --dry-run on any machine. On the
        robot, xr_teleoperate/teleop must be importable (PYTHONPATH) and DDS up.
        """
        if self.dry_run:
            log.info("dry-run: control loop disabled, streaming poses only.")
            self._dry_run_monitor()
            return

        import numpy as np
        from unitree_sdk2py.core.channel import ChannelFactoryInitialize
        from teleop.robot_control.robot_arm import G1_29_ArmController, G1_23_ArmController
        from teleop.robot_control.robot_arm_ik import G1_29_ArmIK, G1_23_ArmIK
        from teleop.utils.motion_switcher import MotionSwitcher

        ChannelFactoryInitialize(1 if self.sim else 0, networkInterface=self.network_interface)

        if self.arm == "G1_23":
            arm_ik, arm_ctrl = G1_23_ArmIK(), G1_23_ArmController(motion_mode=self.motion, simulation_mode=self.sim)
        else:
            arm_ik, arm_ctrl = G1_29_ArmIK(), G1_29_ArmController(motion_mode=self.motion, simulation_mode=self.sim)

        if not self.motion:
            ms = MotionSwitcher(); ms.Enter_Debug_Mode()

        # Reuse televuer's WORLD→robot transform via TeleVuerWrapper, but feed it
        # our WebXRPoseSource instead of a live TeleVuer (duck-typed).
        from televuer.tv_wrapper import TeleVuerWrapper
        wrapper = TeleVuerWrapper.__new__(TeleVuerWrapper)   # bypass __init__ (no Vuer)
        wrapper.tvuer = self.source
        wrapper.use_hand_tracking = self.use_hand_tracking
        wrapper.return_hand_rot_data = False
        wrapper.arm_reference_mode = "head_yaw"

        log.info("⏳ waiting for first XR motion frame…")
        while not self.source.motion_data_ready:
            import time as _t; _t.sleep(0.05)
        log.info("🚀 motion data live — ramping arm speed, starting control loop @ %.0f Hz", frequency)
        arm_ctrl.speed_gradual_max()

        import time as _t
        period = 1.0 / frequency
        self._running = True
        while self._running:
            t0 = _t.time()
            try:
                td = wrapper.get_tele_data()
                cur_q = arm_ctrl.get_current_dual_arm_q()
                cur_dq = arm_ctrl.get_current_dual_arm_dq()
                sol_q, sol_tau = arm_ik.solve_ik(td.left_wrist_pose, td.right_wrist_pose, cur_q, cur_dq)
                arm_ctrl.ctrl_dual_arm(sol_q, sol_tau)
                # right A button = stop
                if not self.use_hand_tracking and self.source.right_ctrl_aButton:
                    log.info("🔴 right aButton → stop"); break
            except Exception as e:
                log.warning("control loop tick error: %s", e)
            dt = _t.time() - t0
            if dt < period:
                _t.sleep(period - dt)
        log.info("control loop stopped.")

    def _dry_run_monitor(self):
        import time
        while True:
            time.sleep(1.0)
            if self.source.motion_data_ready:
                if self.use_hand_tracking:
                    lp = self.source.left_hand_pinchValue; rp = self.source.right_hand_pinchValue
                    head = self.source.head_pose[:3, 3]
                    log.info("📡 hands live | Lpinch=%.2f Rpinch=%.2f head=[% .2f % .2f % .2f]",
                             lp, rp, *head)
                else:
                    log.info("📡 ctrl live | Ltrig=%.2f Rtrig=%.2f Astop=%s",
                             self.source.left_ctrl_triggerValue,
                             self.source.right_ctrl_triggerValue,
                             self.source.right_ctrl_aButton)


def main(argv=None):
    ap = argparse.ArgumentParser(description="NEON WebXR teleop bridge (Quest 3 → G1)")
    ap.add_argument("--input-mode", choices=["hand", "controller"], default="hand")
    ap.add_argument("--arm", choices=["G1_29", "G1_23"], default="G1_29")
    ap.add_argument("--network-interface", default=None, help="DDS iface, e.g. eth0")
    ap.add_argument("--frequency", type=float, default=30.0)
    ap.add_argument("--motion", action="store_true", help="locomotion (controller) mode")
    ap.add_argument("--sim", action="store_true", help="DDS domain 1 (Isaac sim)")
    ap.add_argument("--dry-run", action="store_true", help="stream poses only, no robot/DDS")
    ap.add_argument("--ws-port", type=int, default=8012)
    ap.add_argument("--http-port", type=int, default=8013)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--cert", default=None)
    ap.add_argument("--key", default=None)
    ap.add_argument("--no-http", action="store_true", help="don't serve the HTML page")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cert, key = _resolve_cert(args.cert, args.key)
    if not cert:
        log.warning("⚠️  No TLS cert found — Quest WebXR requires HTTPS/WSS. "
                    "Generate one: make xr-cert (or set XR_TELEOP_CERT/KEY).")

    bridge = XRBridge(
        use_hand_tracking=args.input_mode == "hand",
        arm=args.arm, network_interface=args.network_interface,
        motion=args.motion, sim=args.sim, dry_run=args.dry_run,
    )

    # static HTTPS server for the page
    if not args.no_http:
        bridge.serve_https(args.host, args.http_port, cert, key)

    # control loop in a background thread
    threading.Thread(target=bridge.run_control_loop, kwargs={"frequency": args.frequency}, daemon=True).start()

    # WSS pose server in the main asyncio loop
    ssl_ctx = None
    if cert and key:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert, key)

    try:
        asyncio.run(bridge.serve_ws(args.host, args.ws_port, ssl_ctx))
    except KeyboardInterrupt:
        bridge._running = False
        log.info("bye.")


if __name__ == "__main__":
    main()
