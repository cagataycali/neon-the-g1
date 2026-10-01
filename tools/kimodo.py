"""
🎬 kimodo — text-to-motion for G1 *neon* via NVIDIA Kimodo (nv-tlabs/kimodo).

Pipeline:
    text prompt ──▶ Kimodo-G1-RP-v1 diffusion model ──▶ MuJoCo qpos CSV (T×36)
                                                          │
    CSV cols 7:36 (29 joint dofs, radians) ──────────────┘
                                                          ▼
    rt/lowcmd LowCmd_ stream @ fps (ramp-in, per-motor q/kp/kd, CRC32)

Two phases, decoupled on purpose:

  1. GENERATE  (needs a GPU + `pip install kimodo`; ~17GB VRAM, or 3GB with
     TEXT_ENCODER_DEVICE=cpu). This Jetson has NO CUDA, so generation runs
     off-box: either on a discovered zenoh peer with a GPU, or you run it
     manually on a workstation and copy the CSV into ./motions/.

  2. PLAY      (runs HERE on the robot). Streams the CSV as low-level joint
     targets over DDS `rt/lowcmd`, exactly like the SDK
     g1_ankle_swing_example.cpp: 500Hz-ish command writer, Kp/Kd PD gains,
     mode=1 enable, CRC32 footer.

🚨 SAFETY — PLAYING A MOTION IS DANGEROUS 🚨
  `play` BYPASSES the onboard `ai` balance controller. It commands raw joint
  positions. The robot WILL fall if it is not suspended on a gantry, or if the
  motion is not statically stable. Therefore:
    - play(confirm=False) is a DRY RUN by default (parses + reports, no motion)
    - play requires confirm=True to actually move
    - play requires on_gantry=True acknowledgement for full-body / leg motion
    - a gentle ramp-in from the CURRENT measured pose is always applied
    - Ctrl-safe: any exception → we stop writing (robot holds last cmd; you
      should immediately damp via g1_damp()).

Kimodo qpos CSV layout (36 cols), from kimodo.exports.mujoco.MujocoQposConverter:
    [0:3]  root translation (x,y,z)   — IGNORED for playback (we don't move base)
    [3:7]  root quaternion (w,x,y,z)  — IGNORED for playback
    [7:36] 29 joint dofs (radians)    — MAPPED 1:1 to G1 SDK motor index 0..28

The kimodo G1Skeleton34 bone order (legs → waist → left arm → right arm) matches
the Unitree SDK G1JointIndex order exactly, so CSV col (7+i) == motor i.
"""
from __future__ import annotations

import os
import sys
import csv
import glob
import math
import time
import threading
from typing import Dict, Any, List, Optional

from strands import tool

from ._g1_common import (
    ensure_dds, decode_code, read_fsm_id, _read_mode_machine_from_lowstate,
    _normalize, _DDS_INIT_LOCK,
)
from .g1_joints import G1_JOINT_NAMES, KP_RECOMMENDED, KD_RECOMMENDED

# ---------------------------------------------------------------------------
MOTIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "motions")
MOTIONS_DIR = os.path.abspath(MOTIONS_DIR)
os.makedirs(MOTIONS_DIR, exist_ok=True)

G1_NUM_MOTOR = 29
LOWCMD_TOPIC = "rt/lowcmd"
DEFAULT_MODEL = "Kimodo-G1-RP-v1"
# HTTP inference endpoint (SSH tunnel to EC2 L40S GPU running kimodo_server.py).
# Override with KIMODO_ENDPOINT env var. No SCP: POST prompt -> get CSV.
KIMODO_ENDPOINT = os.environ.get("KIMODO_ENDPOINT", "http://127.0.0.1:18000")

# rt/lowcmd is a single-writer control topic. Never stream from 2 threads.
_LOWCMD_LOCK = threading.Lock()
# active playback stop flag (allows kimodo(action="stop"))
_PLAY_STATE = {"running": False, "stop": False, "frame": 0, "total": 0}


# --- CRC32 (matches SDK Crc32Core) -----------------------------------------
def _crc32_core(data_u32: List[int]) -> int:
    crc = 0xFFFFFFFF
    poly = 0x04C11DB7
    for word in data_u32:
        xbit = 1 << 31
        for _ in range(32):
            if crc & 0x80000000:
                crc = ((crc << 1) & 0xFFFFFFFF) ^ poly
            else:
                crc = (crc << 1) & 0xFFFFFFFF
            if word & xbit:
                crc ^= poly
            xbit >>= 1
    return crc & 0xFFFFFFFF


# --- CSV parsing ------------------------------------------------------------
def _load_qpos_csv(path: str) -> Dict[str, Any]:
    """Load a kimodo qpos CSV → list of 29-dof joint frames (radians)."""
    if not os.path.isabs(path):
        cand = os.path.join(MOTIONS_DIR, path)
        if os.path.exists(cand):
            path = cand
    if not os.path.exists(path):
        return {"ok": False, "message": f"CSV not found: {path}"}

    frames: List[List[float]] = []
    ncols = None
    with open(path, "r") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            try:
                vals = [float(x) for x in row]
            except ValueError:
                continue  # skip header-ish lines
            if ncols is None:
                ncols = len(vals)
            frames.append(vals)

    if not frames:
        return {"ok": False, "message": f"CSV {path} had no numeric rows"}

    if ncols not in (36, 29):
        return {"ok": False, "message": (
            f"Unexpected CSV width {ncols}; expected 36 (root7+29dof) or 29 (dof-only)."
        )}

    joints = []
    for v in frames:
        if ncols == 36:
            joints.append(v[7:36])       # skip root translation+quat
        else:
            joints.append(v[:29])
    return {"ok": True, "path": path, "ncols": ncols, "frames": joints, "n": len(joints)}


def _joint_stats(frames: List[List[float]]) -> Dict[str, Any]:
    per = []
    for i in range(G1_NUM_MOTOR):
        col = [fr[i] for fr in frames]
        per.append({
            "index": i, "name": G1_JOINT_NAMES[i],
            "min_rad": round(min(col), 3), "max_rad": round(max(col), 3),
            "span_rad": round(max(col) - min(col), 3),
        })
    return {"per_joint": per}


# --- low-level publisher ----------------------------------------------------
# The pip wheel lacks the CRC .so; g1-work clone has crc_aarch64.so. Ensure a
# working SDK (with native CRC lib) is importable.
for _extra_sdk in (
    "/home/unitree/g1-work/unitree_sdk2_python",
    "/home/unitree/unitree_sdk2_python",
):
    if os.path.isdir(os.path.join(_extra_sdk, "unitree_sdk2py")) and _extra_sdk not in sys.path:
        sys.path.insert(0, _extra_sdk)


def _get_lowcmd_publisher():
    from unitree_sdk2py.core.channel import ChannelPublisher
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_
    with _DDS_INIT_LOCK:
        pub = ChannelPublisher(LOWCMD_TOPIC, LowCmd_)
        pub.Init()
    return pub, LowCmd_


def _read_current_q(timeout: float = 2.0) -> Optional[List[float]]:
    """Read current 29 motor positions from rt/lowstate."""
    try:
        from ._dds_engine import get_dds
        snap = get_dds().snapshot("rt/lowstate", timeout=timeout)
        if not snap.get("ok"):
            return None
        ms = snap["data"].get("motor_state") or snap["data"].get("motor_state_")
        if not ms:
            return None
        q = []
        for i in range(G1_NUM_MOTOR):
            m = ms[i]
            q.append(float(m.get("q", 0.0)) if isinstance(m, dict) else float(m))
        return q
    except Exception:
        return None


# Cached SDK CRC helper (native .so). Required — the robot rejects LowCmd with
# a wrong CRC, so we MUST use the SDK's struct-packed CRC (crc_aarch64.so),
# not a naive word CRC.
_CRC = None


def _default_lowcmd():
    """Return a properly-initialised HG LowCmd_ (idl.default gives 35 motor slots)."""
    from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
    return unitree_hg_msg_dds__LowCmd_()


def _build_lowcmd(cmd, q_target: List[float], kp: List[float], kd: List[float],
                  mode_machine: int):
    """Populate a LowCmd_ msg in-place (canonical g1_low_level_example.py pattern)."""
    global _CRC
    cmd.mode_pr = 0                    # PR: series control for pitch/roll
    cmd.mode_machine = int(mode_machine)
    for i in range(G1_NUM_MOTOR):      # only the 29 real DoF; leave 29..34 zeroed
        mc = cmd.motor_cmd[i]
        mc.mode = 1                    # 1: enable
        mc.q = float(q_target[i])
        mc.dq = 0.0
        mc.kp = float(kp[i])
        mc.kd = float(kd[i])
        mc.tau = 0.0
    if _CRC is None:
        from unitree_sdk2py.utils.crc import CRC
        _CRC = CRC()
    cmd.crc = _CRC.Crc(cmd)
    return cmd


# ===========================================================================

def _generate_via_endpoint(prompt: str, out_csv: str, model: str,
                           duration: float, diffusion_steps: int = 100,
                           seed: Optional[int] = None,
                           timeout: float = 900.0) -> Dict[str, Any]:
    """POST a prompt to the Kimodo HTTP endpoint and write the CSV to out_csv.

    Returns a dict with ok/status/message. No SCP involved — the motion CSV
    comes back inline in the HTTP response body.
    """
    import json as _json
    import urllib.request as _rq
    import urllib.error as _er

    url = KIMODO_ENDPOINT.rstrip("/") + "/generate"
    payload = {
        "prompt": prompt,
        "model": model,
        "duration": float(duration),
        "diffusion_steps": int(diffusion_steps),
        "num_samples": 1,
    }
    if seed is not None:
        payload["seed"] = int(seed)
    data = _json.dumps(payload).encode()
    req = _rq.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with _rq.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            frames = resp.headers.get("X-Kimodo-Frames", "?")
            gen_s = resp.headers.get("X-Kimodo-Gen-Seconds", "?")
            job = resp.headers.get("X-Kimodo-Job", "?")
    except _er.HTTPError as e:
        detail = e.read().decode()[:1500]
        return {"ok": False, "message": f"endpoint HTTP {e.code}: {detail}"}
    except Exception as e:
        return {"ok": False, "message": f"endpoint unreachable ({url}): {e}. "
                "Is the tunnel up? systemctl --user status kimodo-tunnel"}

    if not body or "," not in body.splitlines()[0]:
        return {"ok": False, "message": f"endpoint returned no CSV data: {body[:300]}"}

    with open(out_csv, "w") as f:
        f.write(body)
    return {"ok": True, "csv": out_csv, "frames": frames, "gen_seconds": gen_s, "job": job}


@tool
def kimodo(
    action: str = "list",
    prompt: str = "",
    csv: str = "",
    name: str = "",
    model: str = DEFAULT_MODEL,
    duration: float = 5.0,
    fps: int = 30,
    confirm: bool = False,
    on_gantry: bool = False,
    ramp_seconds: float = 2.0,
    kp_scale: float = 1.0,
    kd_scale: float = 1.0,
    max_frames: int = 0,
    peer_id: str = "",
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """
    🎬 Kimodo text-to-motion for G1 *neon*.

    Actions:
      - "list"     : list generated motion CSVs in ./motions/
      - "preview"  : parse a CSV and report frame count + per-joint angle ranges
                     (NO robot motion). Use this FIRST to sanity-check a motion.
      - "generate" : text → motion CSV. Needs a CUDA GPU + `pip install kimodo`.
                     This Jetson has no GPU, so this will (a) use a local kimodo
                     if importable, else (b) dispatch to a GPU zenoh peer if
                     peer_id given, else (c) return exact instructions to run
                     off-box and drop the CSV into ./motions/.
      - "play"     : stream a CSV to the robot over rt/lowcmd.
                     🚨 DANGEROUS. Bypasses balance controller.
                     confirm=False (default) = DRY RUN (validates + reports).
                     confirm=True + on_gantry=True required to actually move.
      - "stop"     : abort an in-progress playback.

    Args:
      prompt: text description of desired motion (for generate). Multiple
              motions can be separated by periods, e.g. "wave hello. then clap."
      csv: CSV filename (in ./motions/) or absolute path (for preview/play).
      name: output stem for generate (default derived from prompt).
      model: Kimodo model (default Kimodo-G1-RP-v1 — the only G1 real-robot one).
      duration: seconds of motion to generate.
      fps: playback / generation frame rate (Kimodo G1 model is 30fps).
      confirm: must be True for play to actually move the robot.
      on_gantry: acknowledge robot is suspended/safe (required for play).
      ramp_seconds: gentle blend from current measured pose into frame 0.
      kp_scale/kd_scale: scale the default PD gains (softer = safer).
      max_frames: cap frames played (0 = all). Useful for testing a slice.
      peer_id: zenoh peer to run generation on (must have GPU + kimodo).
      network_interface: DDS interface (default eth0).
    """
    action = (action or "list").strip().lower()

    # ---- list ----
    if action == "list":
        files = sorted(glob.glob(os.path.join(MOTIONS_DIR, "*.csv")))
        out = []
        for p in files:
            try:
                n = sum(1 for _ in open(p)) 
            except Exception:
                n = "?"
            out.append({"file": os.path.basename(p), "rows": n,
                        "size_kb": round(os.path.getsize(p) / 1024, 1)})
        return _normalize({
            "status": "success", "dir": MOTIONS_DIR, "count": len(out),
            "motions": out,
            "message": (f"{len(out)} motion CSV(s) in {MOTIONS_DIR}. "
                        "Use action='preview' to inspect, action='play' to run."),
        })

    # ---- preview ----
    if action == "preview":
        if not csv:
            return _normalize({"status": "error", "message": "preview needs csv=<file>"})
        loaded = _load_qpos_csv(csv)
        if not loaded["ok"]:
            return _normalize({"status": "error", "message": loaded["message"]})
        frames = loaded["frames"]
        stats = _joint_stats(frames)
        dur = len(frames) / float(fps or 30)
        return _normalize({
            "status": "success", "csv": loaded["path"],
            "frames": loaded["n"], "cols": loaded["ncols"],
            "duration_s_at_fps": round(dur, 2), "fps": fps,
            "joint_ranges": stats["per_joint"],
            "message": (f"{loaded['n']} frames ({dur:.1f}s @ {fps}fps). "
                        "Check joint spans look sane, then play with confirm=True, on_gantry=True."),
        })

    # ---- generate ----
    if action == "generate":
        if not prompt:
            return _normalize({"status": "error", "message": "generate needs prompt=<text>"})
        stem = name or ("kimodo_" + "_".join(prompt.lower().split()[:5]).replace(".", ""))
        out_csv = os.path.join(MOTIONS_DIR, stem + ".csv")

        # (0) HTTP endpoint FIRST — the EC2 GPU inference server (no SCP).
        ep = _generate_via_endpoint(prompt, out_csv, model, duration)
        if ep["ok"]:
            return _normalize({
                "status": "success", "csv": ep["csv"], "engine": "endpoint",
                "endpoint": KIMODO_ENDPOINT, "frames": ep.get("frames"),
                "gen_seconds": ep.get("gen_seconds"), "job": ep.get("job"),
                "message": (f"Generated {ep['csv']} via HTTP endpoint "
                            f"({ep.get('frames')} frames in {ep.get('gen_seconds')}s). "
                            "Preview then play."),
            })
        _endpoint_err = ep["message"]

        # (a) local kimodo?
        local_ok = False
        try:
            import kimodo  # noqa: F401
            import torch
            local_ok = torch.cuda.is_available()
        except Exception:
            local_ok = False

        if local_ok:
            import subprocess
            cmd = [
                "python3", "-m", "kimodo.scripts.generate", prompt,
                "--model", model, "--duration", str(duration),
                "--output", out_csv,
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                                   env={**os.environ})
                if r.returncode == 0 and os.path.exists(out_csv):
                    return _normalize({
                        "status": "success", "csv": out_csv, "engine": "local",
                        "message": f"Generated {out_csv}. Preview then play.",
                        "stdout_tail": r.stdout[-500:],
                    })
                return _normalize({"status": "error", "engine": "local",
                                   "message": f"kimodo_gen failed rc={r.returncode}",
                                   "stderr_tail": r.stderr[-800:]})
            except Exception as e:
                return _normalize({"status": "error", "message": f"local generate raised: {e}"})

        # (b) dispatch to GPU peer
        if peer_id:
            try:
                from .dispatch import dispatch as _dispatch  # local dispatcher if present
            except Exception:
                _dispatch = None
            gen_cmd = (
                f"cd /tmp && python3 -m kimodo.scripts.generate '{prompt}' "
                f"--model {model} --duration {duration} --output /tmp/{stem}.csv "
                f"&& base64 /tmp/{stem}.csv"
            )
            return _normalize({
                "status": "pending", "engine": "peer", "peer_id": peer_id,
                "message": (
                    "This box has no GPU. Send this to the GPU peer, then save the "
                    f"decoded CSV to {out_csv}:\n\n"
                    f"zenoh_peer(action='send', peer_id='{peer_id}', message=\"!{gen_cmd}\")\n\n"
                    "Or scp the resulting /tmp/{stem}.csv back here."
                ),
                "remote_cmd": gen_cmd, "target_csv": out_csv,
            })

        # (c) manual instructions
        return _normalize({
            "status": "error", "engine": "none",
            "message": (
                "No local CUDA GPU and no peer_id given. Kimodo generation needs a GPU.\n"
                "Run on a workstation with a GPU:\n"
                "  pip install kimodo\n"
                f"  TEXT_ENCODER_DEVICE=cpu python3 -m kimodo.scripts.generate '{prompt}' "
                f"--model {model} --duration {duration} --output {stem}.csv\n"
                f"Then copy {stem}.csv into {MOTIONS_DIR}/ and run "
                f"kimodo(action='preview', csv='{stem}.csv').\n"
                "Or pass peer_id=<gpu zenoh peer> to dispatch remotely."
            ),
            "target_csv": out_csv,
        })

    # ---- stop ----
    if action == "stop":
        _PLAY_STATE["stop"] = True
        return _normalize({"status": "success",
                           "message": "Stop requested. Playback will halt after current frame. "
                                      "Call g1_damp() to soft-hold the robot."})

    # ---- play ----
    if action == "play":
        if not csv:
            return _normalize({"status": "error", "message": "play needs csv=<file>"})
        loaded = _load_qpos_csv(csv)
        if not loaded["ok"]:
            return _normalize({"status": "error", "message": loaded["message"]})
        frames = loaded["frames"]
        if max_frames and max_frames > 0:
            frames = frames[:max_frames]
        n = len(frames)
        dur = n / float(fps or 30)

        # gains
        kp = [KP_RECOMMENDED[i] * kp_scale for i in range(G1_NUM_MOTOR)]
        kd = [KD_RECOMMENDED[i] * kd_scale for i in range(G1_NUM_MOTOR)]

        # DRY RUN (default)
        if not confirm:
            stats = _joint_stats(frames)
            return _normalize({
                "status": "dry_run", "csv": loaded["path"], "frames": n,
                "duration_s": round(dur, 2), "fps": fps,
                "kp_scale": kp_scale, "kd_scale": kd_scale,
                "joint_ranges": stats["per_joint"],
                "message": (
                    "🚨 DRY RUN — no motion sent. This motion would stream "
                    f"{n} frames ({dur:.1f}s @ {fps}fps) to rt/lowcmd, bypassing the "
                    "balance controller.\n\n"
                    "To ACTUALLY move the robot:\n"
                    "  1. Suspend neon on the gantry (feet off ground or spotter ready).\n"
                    "  2. kimodo(action='play', csv='...', confirm=True, on_gantry=True)\n"
                    "  3. Keep g1_damp() ready as e-stop.\n\n"
                    "TIP: test a small slice first with max_frames=30, kp_scale=0.5."
                ),
            })

        if not on_gantry:
            return _normalize({"status": "error", "message": (
                "Refusing to play: confirm=True but on_gantry=False. "
                "Low-level playback WILL drop the robot if it's standing free. "
                "Set on_gantry=True only when neon is suspended or spotted.")})

        # --- real playback ---
        err = ensure_dds(network_interface)
        if err:
            return _normalize({"status": "error", "message": err})

        mm = _read_mode_machine_from_lowstate()
        if mm is None:
            mm = 0  # best-effort; some builds accept 0

        cur = _read_current_q()
        if cur is None:
            cur = list(frames[0])  # fallback: start at frame0 (less safe)

        try:
            pub, _LowCmd = _get_lowcmd_publisher()
            cmd = _default_lowcmd()
        except Exception as e:
            return _normalize({"status": "error", "message": f"lowcmd publisher init failed: {e}"})

        _PLAY_STATE.update({"running": True, "stop": False, "frame": 0, "total": n})
        dt = 1.0 / float(fps or 30)
        ramp_n = max(1, int(ramp_seconds / dt))

        try:
            with _LOWCMD_LOCK:
                # Phase A: ramp from current pose → frame0
                target0 = frames[0]
                for k in range(ramp_n):
                    if _PLAY_STATE["stop"]:
                        break
                    a = (k + 1) / ramp_n
                    q = [(1 - a) * cur[i] + a * target0[i] for i in range(G1_NUM_MOTOR)]
                    _build_lowcmd(cmd, q, kp, kd, mm)
                    pub.Write(cmd)
                    time.sleep(dt)

                # Phase B: stream frames
                for fi, fr in enumerate(frames):
                    if _PLAY_STATE["stop"]:
                        break
                    _build_lowcmd(cmd, fr, kp, kd, mm)
                    pub.Write(cmd)
                    _PLAY_STATE["frame"] = fi
                    time.sleep(dt)
        except Exception as e:
            _PLAY_STATE["running"] = False
            return _normalize({"status": "error", "message": (
                f"Playback aborted mid-stream at frame {_PLAY_STATE['frame']}: {e}. "
                "CALL g1_damp() NOW.")})
        finally:
            _PLAY_STATE["running"] = False

        stopped = _PLAY_STATE["stop"]
        return _normalize({
            "status": "success", "csv": loaded["path"],
            "frames_played": _PLAY_STATE["frame"] + 1, "total_frames": n,
            "stopped_early": stopped,
            "message": (
                f"{'Stopped early' if stopped else 'Completed'} — played "
                f"{_PLAY_STATE['frame'] + 1}/{n} frames. "
                "Robot is holding last commanded pose. Call g1_damp() to relax, "
                "or g1_set_fsm(500) to return to balance-stand.")
        })

    return _normalize({"status": "error",
                       "message": f"Unknown action '{action}'. "
                                  "Use list|preview|generate|play|stop."})
