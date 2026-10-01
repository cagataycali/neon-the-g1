#!/usr/bin/env python3
"""
🌐 NEON G1 Dashboard backend.

Polls the G1's DDS state tools (state, battery, mainboard, lidar, slam) plus
the cross-persona memory/agent_log, exposes them over:

  • GET  /api/health            → liveness
  • GET  /api/telemetry         → one-shot full snapshot
  • GET  /api/log?limit=N       → unified agent_log (cross-persona)
  • GET  /api/joints            → joint reference table
  • WS   /ws                    → live telemetry stream (1 Hz default)

Serves the built React app from ./frontend/dist at /.

Run:
    cd /home/unitree/neon-the-g1 && source env.sh
    .venv/bin/python docs/dashboard/server.py --port 8080 --hz 1

The robot tools are best-effort: if DDS is unreachable (no robot / wrong
iface) the endpoints still return with status='error' per-field so the
dashboard renders gracefully in "offline" mode.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── make neon's tools importable ─────────────────────────────────────────
HERE = Path(__file__).resolve()
REPO = HERE.parent.parent.parent  # docs/dashboard/server.py → repo root
for p in (str(REPO), str(REPO / "unitree_sdk2_python")):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("neon-dashboard")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn

# ── auth + config (dashboard admin) ──────────────────────────────────────
try:
    from docs.dashboard import auth as _auth
    from docs.dashboard import config_api as _cfg
except Exception:
    import auth as _auth          # type: ignore
    import config_api as _cfg     # type: ignore

# ── lazy robot tool imports (don't crash if SDK/DDS missing) ─────────────
IFACE = os.getenv("G1_NETWORK_INTERFACE", os.getenv("G1_IFACE", "eth0"))
_TOOLS: Dict[str, Any] = {}


def _load_tools() -> None:
    """Best-effort import of G1 state tools + cross-persona log."""
    try:
        from tools.g1_state import g1_get_state, g1_read_lowstate
        from tools.g1_battery import g1_battery
        from tools.g1_mainboard import g1_mainboard
        _TOOLS["state"] = g1_get_state
        _TOOLS["lowstate"] = g1_read_lowstate
        _TOOLS["battery"] = g1_battery
        _TOOLS["mainboard"] = g1_mainboard
        log.info("✓ G1 state tools loaded")
    except Exception as e:  # pragma: no cover
        log.warning(f"G1 state tools unavailable: {e}")

    try:
        from tools.g1_slam import g1_slam_pose, g1_slam_stats
        _TOOLS["slam_pose"] = g1_slam_pose
        _TOOLS["slam_stats"] = g1_slam_stats
    except Exception as e:
        log.debug(f"SLAM tools unavailable: {e}")

    try:
        from tools.g1_lidar import g1_lidar_stats
        _TOOLS["lidar"] = g1_lidar_stats
    except Exception as e:
        log.debug(f"LiDAR tool unavailable: {e}")

    try:
        from tools import agent_log as _alog
        _TOOLS["agent_log"] = _alog
        log.info("✓ agent_log loaded")
    except Exception as e:
        log.warning(f"agent_log unavailable: {e}")


def _unwrap(res: Any) -> Any:
    """Normalize a Strands tool result to a flat dict.

    Tools return one of:
      • plain dict (already normalized)
      • {"status":..., "content":[{"json": {...}}]}          ← structured
      • {"status":..., "content":[{"text": "..."}]}           ← text (maybe JSON)
      • {"status":..., "content":[{"text": "..."},{"json":{...}}]}  ← both

    We merge: start with top-level status, overlay every json block, and if a
    text block is itself JSON, parse + overlay it too. Plain text is kept as
    `message` so the UI always has something.
    """
    if not isinstance(res, dict):
        return {"status": "error", "message": str(res)[:300]}

    content = res.get("content")
    if not isinstance(content, list):
        return res  # already a flat dict

    out: Dict[str, Any] = {}
    if "status" in res:
        out["status"] = res["status"]

    for block in content:
        if not isinstance(block, dict):
            continue
        if "json" in block and isinstance(block["json"], dict):
            out.update(block["json"])
        elif "text" in block:
            txt = block["text"]
            try:
                parsed = json.loads(txt)
                if isinstance(parsed, dict):
                    out.update(parsed)
                else:
                    out.setdefault("message", str(txt)[:500])
            except Exception:
                out.setdefault("message", str(txt)[:500])
    if not out:
        out = {"status": res.get("status", "unknown")}
    return out


import concurrent.futures as _futures

# Dedicated pool so one wedged DDS RPC (e.g. g1_get_state loco timeout) never
# starves the others or the HTTP event loop.
_POOL = _futures.ThreadPoolExecutor(max_workers=6, thread_name_prefix="g1tool")


def _safe(name: str, fn, _timeout: float = 2.5, **kwargs) -> Dict[str, Any]:
    """Run a (blocking) tool with a HARD wall-clock timeout via the pool.

    Crucial because g1_get_state can hang for many seconds when the loco RPC
    service is wedged (we observed [ClientStub] send request error + no return).
    A future.result(timeout) guarantees we never block longer than _timeout —
    the orphaned thread finishes on its own and its result is discarded.
    """
    if name not in _TOOLS:
        return {"status": "unavailable", "message": f"{name} tool not loaded"}
    fut = _POOL.submit(lambda: _unwrap(fn(**kwargs)))
    try:
        return fut.result(timeout=_timeout)
    except _futures.TimeoutError:
        return {"status": "error",
                "message": f"{name}: timed out after {_timeout}s (DDS RPC wedged?)"}
    except Exception as e:
        return {"status": "error", "message": f"{name}: {e}"}


# Background-refreshed telemetry cache. The collector thread refreshes this on a
# tick; HTTP + WS just read _SNAPSHOT (instant, never blocks).
_SNAPSHOT: Dict[str, Any] = {"ts": 0.0, "iface": IFACE}
_SNAP_LOCK = threading.Lock()


def _refresh_snapshot(include_slow: bool = True) -> Dict[str, Any]:
    """Build a fresh snapshot (blocking, runs in collector thread)."""
    snap: Dict[str, Any] = {"ts": time.time(), "iface": IFACE}
    # Fast, reliable subscriber-based reads first.
    if "battery" in _TOOLS:
        snap["battery"] = _safe("battery", _TOOLS["battery"], _timeout=2.5,
                                network_interface=IFACE, timeout=1.0)
    if "lowstate" in _TOOLS:
        snap["lowstate"] = _safe("lowstate", _TOOLS["lowstate"], _timeout=2.5,
                                 network_interface=IFACE, timeout=1.0)
    # state uses loco RPC which can wedge → short hard timeout, degrade gracefully
    if "state" in _TOOLS:
        snap["state"] = _safe("state", _TOOLS["state"], _timeout=2.0,
                              network_interface=IFACE)
    if include_slow:
        if "mainboard" in _TOOLS:
            snap["mainboard"] = _safe("mainboard", _TOOLS["mainboard"], _timeout=2.5,
                                      network_interface=IFACE, timeout=1.0)
        if "slam_pose" in _TOOLS:
            snap["slam"] = _safe("slam_pose", _TOOLS["slam_pose"], _timeout=2.0)
        if "lidar" in _TOOLS:
            snap["lidar"] = _safe("lidar", _TOOLS["lidar"], _timeout=2.0)
    # Atomic swap: replace the dict contents in one locked step so readers
    # never observe a half-populated snapshot (was causing battery=None on
    # slow ticks). Also preserve last-good values for fields that errored this
    # cycle so the UI doesn't flicker to "offline".
    with _SNAP_LOCK:
        prev = dict(_SNAPSHOT)
        merged = dict(snap)
        for k in ("battery", "lowstate", "state", "mainboard", "slam", "lidar"):
            cur = merged.get(k)
            # if this cycle's value errored/timed out but we had a good one, keep it
            if (isinstance(cur, dict) and cur.get("status") in ("error", "unavailable", None)
                    and isinstance(prev.get(k), dict)
                    and prev[k].get("status") == "success"):
                # keep prev good value but tag staleness
                stale = dict(prev[k])
                stale["_stale"] = True
                merged[k] = stale
        _SNAPSHOT.clear()
        _SNAPSHOT.update(merged)
    return snap


def collect_telemetry(include_slow: bool = True) -> Dict[str, Any]:
    """Return the latest cached snapshot (instant). Never blocks the caller.

    If the cache is empty (cold start), do ONE inline refresh of just the fast
    fields so the first request isn't blank.
    """
    with _SNAP_LOCK:
        if _SNAPSHOT.get("ts", 0):
            return dict(_SNAPSHOT)
    # cold start — fast fields only, bounded
    return _refresh_snapshot(include_slow=False)


def _collector_loop():
    """Daemon: refresh the snapshot on a tick, fully off the event loop."""
    n = 0
    while True:
        try:
            _refresh_snapshot(include_slow=(n % 5 == 0))
            n += 1
        except Exception as e:
            log.debug(f"collector: {e}")
        time.sleep(1.0)


def collect_log(limit: int = 30) -> List[Dict[str, Any]]:
    alog = _TOOLS.get("agent_log")
    if not alog:
        return []
    try:
        return alog.recent(limit=limit)
    except Exception as e:
        log.debug(f"agent_log.recent failed: {e}")
        return []


def log_stats() -> Dict[str, Any]:
    alog = _TOOLS.get("agent_log")
    if not alog:
        return {}
    try:
        return alog.stats()
    except Exception:
        return {}


# ── FastAPI app ──────────────────────────────────────────────────────────
app = FastAPI(title="NEON G1 Dashboard", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DIST = HERE.parent / "frontend" / "dist"



# ═══════════════════════════════════════════════════════════════════════
# 🔐 AUTH — WebAuthn passkey gate. Everything under /api/* and /ws requires a
#    valid session token once a passkey is enrolled. /auth/* + / are open so
#    the login/enrollment UI can load.
# ═══════════════════════════════════════════════════════════════════════
from starlette.middleware.base import BaseHTTPMiddleware

# paths that never require auth (login flow, SPA shell, static assets)
_OPEN_PREFIXES = ("/auth/", "/assets/", "/neon.svg", "/favicon", "/manifest")
_OPEN_EXACT = {"/", "/api/health"}


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not _auth.AUTH_ENABLED:
            return await call_next(request)
        if path in _OPEN_EXACT or any(path.startswith(p) for p in _OPEN_PREFIXES):
            return await call_next(request)
        # WS handled in-handler (middleware can't 401 a WS cleanly)
        if path.startswith("/ws"):
            return await call_next(request)
        # everything else under /api requires a session
        if path.startswith("/api/"):
            try:
                _auth.require_auth(request)
            except Exception:
                return JSONResponse({"error": "authentication required"}, status_code=401)
        return await call_next(request)


app.add_middleware(_AuthMiddleware)


# ── auth ceremony routes (open) ──────────────────────────────────────────
@app.get("/auth/status")
async def auth_status(request: Request):
    return JSONResponse(_auth.status(request))


@app.post("/auth/register/begin")
async def auth_register_begin(request: Request, payload: dict = None):
    payload = payload or {}
    # if credentials already exist, enrolling more needs a valid session
    if _auth.has_credentials():
        _auth.require_auth(request)
    return JSONResponse(_auth.begin_registration(
        request, label=payload.get("label", "admin passkey"),
        bootstrap=payload.get("bootstrap", "")))


@app.post("/auth/register/finish")
async def auth_register_finish(request: Request, payload: dict):
    return JSONResponse(_auth.finish_registration(
        request, payload["challenge_id"], payload["credential"]))


@app.post("/auth/login/begin")
async def auth_login_begin(request: Request):
    return JSONResponse(_auth.begin_authentication(request))


@app.post("/auth/login/finish")
async def auth_login_finish(request: Request, payload: dict):
    return JSONResponse(_auth.finish_authentication(
        request, payload["challenge_id"], payload["credential"]))


@app.get("/api/auth/credentials")
async def auth_credentials():
    return JSONResponse({"credentials": _auth.list_credentials()})


@app.delete("/api/auth/credentials/{cred_id}")
async def auth_delete_credential(cred_id: str):
    return JSONResponse(_auth.delete_credential(cred_id))


# ── config: .env editor, model, wifi (all auth-gated by middleware) ──────
@app.get("/api/config/env")
async def config_env(reveal: bool = False):
    return JSONResponse(_cfg.get_env(reveal=reveal))


@app.get("/api/config/env/example")
async def config_env_example():
    return JSONResponse(_cfg.get_env_example())


@app.post("/api/config/env")
async def config_env_set(payload: dict):
    updates = payload.get("updates") or {}
    return JSONResponse(_cfg.set_env(updates))


@app.get("/api/config/model")
async def config_model():
    return JSONResponse(_cfg.get_model())


@app.post("/api/config/model")
async def config_model_set(payload: dict):
    return JSONResponse(_cfg.set_model(payload.get("model_id", "")))


@app.get("/api/config/wifi/status")
async def config_wifi_status():
    return JSONResponse(await asyncio.to_thread(_cfg.wifi_status))


@app.get("/api/config/wifi/scan")
async def config_wifi_scan():
    return JSONResponse(await asyncio.to_thread(_cfg.wifi_scan))


@app.post("/api/config/wifi/connect")
async def config_wifi_connect(payload: dict):
    return JSONResponse(await asyncio.to_thread(
        _cfg.wifi_connect, payload.get("ssid", ""), payload.get("password", "")))


@app.post("/api/config/service/restart")
async def config_service_restart(payload: dict | None = None):
    """Recreate persona containers to apply env/model changes.

    Body {"services": [...]} among neon-agent, neon-telegram, neon-thinker,
    neon-dashboard (default: all four). Executed on the host by neon-ctl
    (scripts/neon_ctl.py) through the .memory/ctl channel; this container has
    neither docker.sock nor systemd. When neon-dashboard is included the
    answer is 202-shaped (pending) and the UI polls /api/health.
    """
    services = (payload or {}).get("services") or list(_cfg.PERSONAS)
    res = await asyncio.to_thread(_cfg.restart_services, services)
    return JSONResponse(res, status_code=202 if res.get("pending") and res.get("ok") else 200)


@app.get("/api/config/ctl")
async def config_ctl_status():
    """Is the host-side neon-ctl alive (heartbeat age), plus the voice unit state it saw."""
    return JSONResponse(_cfg._ctl().heartbeat())


@app.get("/api/config/token")
async def config_token_health():
    return JSONResponse(_cfg.camera_token_health())


@app.post("/api/config/token/refresh")
async def config_token_refresh():
    """Mint a fresh NEON_CAMERA_PROXY_TOKEN on the host and recreate telegram + thinker."""
    return JSONResponse(await asyncio.to_thread(_cfg.refresh_camera_token))


@app.get("/api/health")
async def health():
    tok = _cfg.camera_token_health()
    return {"status": "ok", "ts": time.time(), "iface": IFACE,
            "tools": sorted(_TOOLS.keys()),
            "camera_proxy_token": tok["state"],
            "camera_proxy_token_exp": tok.get("exp")}


@app.get("/api/telemetry")
async def telemetry():
    return JSONResponse(collect_telemetry(include_slow=True))


@app.get("/api/log")
async def get_log(limit: int = 30):
    return JSONResponse({"log": collect_log(limit), "stats": log_stats()})


@app.get("/api/joints")
async def joints():
    try:
        from tools.g1_joints import g1_joint_reference
        return JSONResponse(_unwrap(g1_joint_reference()))
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


# ── Camera (multi-cam MJPEG) + LiDAR (binary WS) ──────────────────────────
try:
    from docs.dashboard.camera_stream import get_manager as _get_cam_mgr
except Exception:
    try:
        from camera_stream import get_manager as _get_cam_mgr
    except Exception:
        _get_cam_mgr = None  # type: ignore

try:
    from docs.dashboard.lidar_stream import get_lidar_streamer as _get_lidar
except Exception:
    try:
        from lidar_stream import get_lidar_streamer as _get_lidar
    except Exception:
        _get_lidar = None  # type: ignore


_PLACEHOLDER_JPEG = None


def _placeholder_jpeg() -> bytes:
    global _PLACEHOLDER_JPEG
    if _PLACEHOLDER_JPEG is not None:
        return _PLACEHOLDER_JPEG
    try:
        import numpy as np, cv2
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:] = (12, 8, 6)
        cv2.putText(img, "NO CAMERA SIGNAL", (140, 190),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (120, 132, 153), 2, cv2.LINE_AA)
        ok, buf = cv2.imencode(".jpg", img)
        _PLACEHOLDER_JPEG = buf.tobytes() if ok else b""
    except Exception:
        _PLACEHOLDER_JPEG = b""
    return _PLACEHOLDER_JPEG


@app.get("/api/cameras")
async def cameras_list():
    if _get_cam_mgr is None:
        return JSONResponse({"cameras": [], "error": "camera module unavailable"})
    return JSONResponse({"cameras": _get_cam_mgr().list()})


@app.get("/api/camera/{cam_id}/snapshot")
async def camera_snapshot(cam_id: str):
    if _get_cam_mgr is None:
        return Response(_placeholder_jpeg(), media_type="image/jpeg")
    cam = _get_cam_mgr().get(cam_id)
    jpg = (cam.latest() if cam else None) or _placeholder_jpeg()
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/camera/{cam_id}/stream")
async def camera_stream(cam_id: str):
    if _get_cam_mgr is None:
        return Response(_placeholder_jpeg(), media_type="image/jpeg")
    cam = _get_cam_mgr().get(cam_id)
    boundary = "neonframe"

    async def gen():
        idle = 0
        while True:
            jpg = cam.latest() if cam else None
            if jpg is None:
                jpg = _placeholder_jpeg()
                idle += 1
                if idle > 200:
                    await asyncio.sleep(0.5)
            else:
                idle = 0
            yield (b"--" + boundary.encode() + b"\r\n"
                   b"Content-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                   + jpg + b"\r\n")
            await asyncio.sleep(1.0 / max(cam.fps if cam else 15, 1))

    return StreamingResponse(
        gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
        headers={"Cache-Control": "no-store, no-cache", "Pragma": "no-cache"},
    )


# Back-compat: old single-camera endpoints → realsense_color
@app.get("/api/camera/status")
async def camera_status_legacy():
    if _get_cam_mgr is None:
        return JSONResponse({"running": False, "error": "unavailable"})
    cams = _get_cam_mgr().list()
    return JSONResponse(cams[0] if cams else {"running": False})


@app.get("/api/camera/snapshot")
async def camera_snapshot_legacy():
    return await camera_snapshot("realsense_color")


# ── LiDAR ──────────────────────────────────────────────────────────────
@app.get("/api/lidar/status")
async def lidar_status():
    if _get_lidar is None:
        return JSONResponse({"running": False, "error": "lidar module unavailable"})
    s = _get_lidar()
    s.start()
    return JSONResponse(s.status())


@app.websocket("/ws/lidar")
async def ws_lidar(ws: WebSocket):
    """Binary point-cloud stream: 7 bytes/point (x_i16,y_i16,z_i16,intensity_u8)."""
    if _auth.AUTH_ENABLED and _auth.require_ws_auth(ws) is None:
        await ws.close(code=4401); return
    await ws.accept()
    if _get_lidar is None:
        await ws.close()
        return
    s = _get_lidar()
    s.start()
    try:
        last = None
        while True:
            buf = s.latest()
            if buf is not None and buf is not last:
                await ws.send_bytes(buf)
                last = buf
            await asyncio.sleep(1.0 / max(s.hz, 1))
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as e:
        log.debug(f"ws_lidar: {e}")


# ── Agent chat ─────────────────────────────────────────────────────────
try:
    from docs.dashboard import chat_agent as _chat
except Exception:
    try:
        import chat_agent as _chat
    except Exception:
        _chat = None  # type: ignore


@app.get("/api/chat/status")
async def chat_status():
    if _chat is None:
        return JSONResponse({"ready": False, "error": "chat module unavailable"})
    return JSONResponse(await asyncio.to_thread(_chat.status))


@app.post("/api/chat")
async def chat_post(payload: dict):
    if _chat is None:
        return JSONResponse({"reply": None, "error": "chat unavailable"})
    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        return JSONResponse({"reply": None, "error": "empty prompt"})
    # agent calls are blocking → run in thread; generous timeout for tool use
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_chat.ask, prompt),
                                        timeout=180)
    except asyncio.TimeoutError:
        result = {"reply": None, "error": "agent timed out (180s)"}
    # mirror into the cross-persona log so other personas see dashboard chat
    try:
        alog = _TOOLS.get("agent_log")
        if alog:
            alog.record("shell", "user", prompt, {"src": "dashboard"})
            if result.get("reply"):
                alog.record("shell", "assistant", result["reply"][:2000], {"src": "dashboard"})
    except Exception:
        pass
    return JSONResponse(result)


@app.post("/api/chat/stream")
async def chat_stream(payload: dict):
    """Server-Sent Events stream of an agent turn (token-by-token + tool events).

    Emits ``data: {json}\n\n`` frames. Event JSON shapes:
      {"type":"text","data":"..."}         — assistant text delta
      {"type":"reasoning","data":"..."}    — extended-thinking delta
      {"type":"tool","name":"...","status":"running"}
      {"type":"done","reply":"..."}         — full text, turn complete
      {"type":"error","error":"..."}
    """
    if _chat is None or not hasattr(_chat, "ask_stream"):
        async def _err():
            yield "data: " + json.dumps({"type": "error", "error": "chat streaming unavailable"}) + "\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    prompt = (payload or {}).get("prompt", "").strip()
    if not prompt:
        async def _empty():
            yield "data: " + json.dumps({"type": "error", "error": "empty prompt"}) + "\n\n"
        return StreamingResponse(_empty(), media_type="text/event-stream")

    async def gen():
        final_reply = ""
        try:
            async for ev in _chat.ask_stream(prompt):
                if ev.get("type") == "done":
                    final_reply = ev.get("reply", "")
                yield "data: " + json.dumps(ev) + "\n\n"
        except Exception as e:
            yield "data: " + json.dumps({"type": "error", "error": str(e)}) + "\n\n"
        # mirror into cross-persona log (same as /api/chat)
        try:
            alog = _TOOLS.get("agent_log")
            if alog:
                alog.record("shell", "user", prompt, {"src": "dashboard"})
                if final_reply:
                    alog.record("shell", "assistant", final_reply[:2000], {"src": "dashboard"})
        except Exception:
            pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat/reset")
async def chat_reset():
    if _chat is None:
        return JSONResponse({"ok": False})
    return JSONResponse(await asyncio.to_thread(_chat.reset))


# ── WebSocket live stream ─────────────────────────────────────────────────
class Hub:
    def __init__(self):
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)

    async def broadcast(self, payload: dict):
        dead = []
        msg = json.dumps(payload)
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


HUB = Hub()
HZ = float(os.getenv("DASHBOARD_HZ", "1"))


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if _auth.AUTH_ENABLED and _auth.require_ws_auth(ws) is None:
        await ws.close(code=4401)
        return
    await HUB.connect(ws)
    try:
        # send an immediate snapshot on connect
        await ws.send_text(json.dumps({"type": "telemetry",
                                       "data": collect_telemetry()}))
        await ws.send_text(json.dumps({"type": "log",
                                       "data": collect_log(30),
                                       "stats": log_stats()}))
        while True:
            # allow client → server pings / commands (ignored for now)
            await asyncio.wait_for(ws.receive_text(), timeout=3600)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    except Exception as e:
        log.debug(f"ws error: {e}")
    finally:
        HUB.disconnect(ws)


async def _telemetry_loop():
    """Background broadcaster — reads the collector's cache (never blocks)."""
    n = 0
    while True:
        try:
            if HUB.clients:
                with _SNAP_LOCK:
                    snap = dict(_SNAPSHOT)
                if snap.get("ts"):
                    await HUB.broadcast({"type": "telemetry", "data": snap})
                if n % 5 == 0:
                    await HUB.broadcast({"type": "log",
                                         "data": collect_log(30),
                                         "stats": log_stats()})
            n += 1
        except Exception as e:
            log.debug(f"telemetry loop: {e}")
        await asyncio.sleep(1.0 / max(HZ, 0.1))


def _prewarm_sensors():
    """Prewarm cameras + lidar in a background thread so a blocking DDS/RealSense
    init can NEVER stall uvicorn from binding the port (fixes startup deadlock)."""
    try:
        if _get_cam_mgr is not None:
            _get_cam_mgr().start_all()
            log.info("📷 camera prewarm done")
    except Exception as e:
        log.debug(f"camera prewarm: {e}")
    try:
        if _get_lidar is not None:
            _get_lidar().start()
            log.info("📡 lidar prewarm done")
    except Exception as e:
        log.debug(f"lidar prewarm: {e}")


@app.on_event("startup")
async def _startup():
    _load_tools()
    # Background collector thread refreshes the DDS snapshot off the event loop.
    threading.Thread(target=_collector_loop, daemon=True, name="g1-collector").start()
    # Pre-warm cameras + lidar OFF the event loop (blocking DDS/RealSense init
    # must not prevent uvicorn from binding the port).
    threading.Thread(target=_prewarm_sensors, daemon=True, name="sensor-prewarm").start()
    asyncio.create_task(_telemetry_loop())
    log.info(f"🌐 Dashboard up. iface={IFACE} hz={HZ} dist={DIST} "
             f"(exists={DIST.exists()})")


# ── static frontend (mount LAST so /api + /ws win) ────────────────────────
@app.get("/")
async def index():
    idx = DIST / "index.html"
    if idx.exists():
        return FileResponse(str(idx))
    return JSONResponse({"status": "no-build",
                         "message": "Frontend not built. Run: npm run build"})


def _mount_static():
    if DIST.exists():
        app.mount("/", StaticFiles(directory=str(DIST), html=True), name="static")
        log.info(f"✓ serving frontend from {DIST}")
    else:
        log.warning(f"frontend dist not found at {DIST} — API only")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--hz", type=float, default=float(os.getenv("DASHBOARD_HZ", "1")))
    ap.add_argument("--ssl", action="store_true",
                    help="Serve HTTPS (self-signed) — match teleop HTTPS for iframe embedding")
    ap.add_argument("--cert", default=os.getenv("DASHBOARD_CERT",
                    os.path.expanduser("~/.config/xr_teleoperate/cert.pem")))
    ap.add_argument("--key", default=os.getenv("DASHBOARD_KEY",
                    os.path.expanduser("~/.config/xr_teleoperate/key.pem")))
    args = ap.parse_args()
    global HZ
    HZ = args.hz
    os.environ["DASHBOARD_HZ"] = str(HZ)
    _mount_static()

    use_ssl = args.ssl or os.getenv("DASHBOARD_SSL", "").lower() in ("1", "true", "yes")
    ssl_kw = {}
    if use_ssl and os.path.exists(args.cert) and os.path.exists(args.key):
        ssl_kw = {"ssl_certfile": args.cert, "ssl_keyfile": args.key}
        log.info(f"🔒 HTTPS enabled (cert={args.cert})")
    elif use_ssl:
        log.warning(f"SSL requested but cert/key missing ({args.cert}) — serving HTTP")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info", **ssl_kw)


if __name__ == "__main__":
    main()
