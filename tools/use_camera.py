"""
📷 G1 camera tool — unified capture across multiple sources.

Supported cameras:
  1. **RealSense D435i** (default source="realsense")
     - Backend: pyrealsense2 (librealsense direct USB)
     - Color (BGR) + Depth (uint16 Z16) + IR streams
     - Native up to 1920x1080 color, 1280x720 depth
     - Bypasses videohub_pc4 — works even with system service holding V4L2 nodes
     - Serial 347622072138, depth scale 0.001 m/unit (1 mm)

  2. **Logitech Brio 4K** (source="logitech")
     - Backend: OpenCV V4L2 (MJPG), /dev/video6
     - Color only (no depth)
     - Native up to 3840x2160, default 1920x1080 @ 30fps

  3. **V4L2 generic fallback** — used only for RealSense color if librealsense
     fails. Never touches /dev/video0..4 which videohub_pc4 holds.

Default resolution: **1920x1080** (both RS and Brio handle it fine).

Actions:
  discover       → enumerate all connected cameras (RS + Logi + V4L2)
  capture        → one color frame → inline Converse image
  capture_depth  → colorized depth heatmap → inline (RealSense only)
  capture_both   → color + depth both inline (RealSense only)
  save           → write color JPEG to disk, return path
  info           → list RealSense stream profiles (RS only)

Args of interest:
  source : "realsense" (default) | "logitech" | "auto"
           - "auto" picks realsense if present else logitech
  width/height : default 1920x1080
"""
import glob
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strands import tool

logger = logging.getLogger(__name__)

# ── Backend probes ──────────────────────────────────────────────────────────
try:
    import cv2
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

try:
    import numpy as np
    _NP_OK = True
except ImportError:
    _NP_OK = False

try:
    import pyrealsense2 as rs
    _RS_OK = True
except ImportError:
    _RS_OK = False

# Vendor fingerprints from udev
LOGITECH_VID = "046d"
REALSENSE_VENDOR_TAG = "RealSense"

# V4L2 nodes held by another process (e.g. videohub_pc4) — runtime-detected
# instead of hardcoded so we work on any Jetson topology.
# dashboard snapshot proxy
# A V4L2 / RealSense device can be opened by only ONE process. On the robot
# the dashboard holds the cameras open and shares frames over HTTP. When THIS
# process (e.g. the thinker container) can't open the device, fall back to
# pulling a frame from the dashboard's snapshot endpoint.
#
#   NEON_CAMERA_PROXY        e.g. https://localhost:8080  (dashboard base URL)
#   NEON_CAMERA_PROXY_TOKEN  bearer JWT (auth.service_token) — optional
def _mint_proxy_token() -> str:
    """Mint a fresh service token from the dashboard auth store (the containers
    bind-mount .memory, so the store is reachable). Same path as
    vision._prime_camera_proxy. Returns '' when the store or clock is not usable."""
    try:
        import sys as _sys
        from pathlib import Path as _P
        dash = str(_P(__file__).resolve().parent.parent / "docs" / "dashboard")
        if dash not in _sys.path:
            _sys.path.insert(0, dash)
        from auth import service_token  # type: ignore
        return service_token("mhs") or ""
    except Exception as e:  # clock not synced, store missing, pyjwt absent
        logger.warning(f"camera proxy: could not mint a fresh token: {e}")
        return ""


def _proxy_snapshot(source: str = "auto"):
    """Fetch a JPEG frame from the dashboard snapshot API. Returns bytes|None.

    A 401/403 means the bearer in NEON_CAMERA_PROXY_TOKEN is stale (typically a
    token minted at a 1970 boot clock, exp 1980): drop it, mint a fresh one from
    the auth store and retry ONCE. One warning line, never a loop."""
    base = os.getenv("NEON_CAMERA_PROXY", "").strip().rstrip("/")
    if not base:
        return None
    # map our 'source' -> dashboard cam id
    cam_id = "realsense_color"
    if source == "logitech":
        cam_id = "brio"
    try:
        import requests
        tok = os.getenv("NEON_CAMERA_PROXY_TOKEN", "").strip()
        reminted = False
        # try the requested cam, then fall back to any available color cam
        for cid in (cam_id, "brio", "realsense_color"):
            url = f"{base}/api/camera/{cid}/snapshot"
            headers = {"Authorization": f"Bearer {tok}"} if tok else {}
            r = requests.get(url, headers=headers, timeout=6, verify=False)
            if r.status_code in (401, 403) and not reminted:
                reminted = True
                os.environ.pop("NEON_CAMERA_PROXY_TOKEN", None)
                fresh = _mint_proxy_token()
                if not fresh:
                    return None
                logger.warning("camera proxy: token refused (%s), re-minted from the auth store", r.status_code)
                os.environ["NEON_CAMERA_PROXY_TOKEN"] = tok = fresh
                r = requests.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=6, verify=False)
            if r.status_code == 200 and r.content and len(r.content) > 1000:
                return r.content
    except Exception as e:
        logger.debug(f"camera proxy snapshot failed: {e}")
    return None


def _is_node_busy(idx: int) -> bool:
    """Return True if /dev/videoN is held open by another process."""
    try:
        with open(f"/proc/self/fd", "r"):
            pass
    except Exception:
        pass
    # Try opening O_RDWR | O_NONBLOCK; if EBUSY, it's held.
    import os, errno
    try:
        fd = os.open(f"/dev/video{idx}", os.O_RDWR | os.O_NONBLOCK)
        os.close(fd)
        return False
    except OSError as e:
        return e.errno == errno.EBUSY
    except Exception:
        return False

DEFAULT_SAVE_DIR = Path(
    os.getenv("NEON_CAPTURE_DIR")
    or (Path(__file__).resolve().parent.parent / "captures")
)
DEFAULT_W = 1920
DEFAULT_H = 1080
DEFAULT_FPS = 30


# ─── udev helpers ───────────────────────────────────────────────────────────

def _udev_info(dev_path: str) -> Dict[str, str]:
    """Best-effort udev property lookup for a /dev/videoX node."""
    info: Dict[str, str] = {}
    try:
        import subprocess
        out = subprocess.run(
            ["udevadm", "info", "--query=property", f"--name={dev_path}"],
            capture_output=True, text=True, timeout=2,
        ).stdout
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v
    except Exception:
        pass
    return info


def _v4l2_list() -> List[int]:
    return sorted(
        int(p.replace("/dev/video", ""))
        for p in glob.glob("/dev/video*")
        if p.replace("/dev/video", "").isdigit()
    )


def _v4l2_name(index: int) -> str:
    try:
        with open(f"/sys/class/video4linux/video{index}/name") as f:
            return f.read().strip()
    except Exception:
        return "unknown"


def _classify_v4l2(index: int) -> str:
    """Return 'realsense' | 'logitech' | 'other'."""
    info = _udev_info(f"/dev/video{index}")
    vendor = info.get("ID_VENDOR", "")
    product = info.get("ID_V4L_PRODUCT", "")
    if LOGITECH_VID == info.get("ID_VENDOR_ID", "") or vendor.startswith("046d") or vendor == LOGITECH_VID:
        return "logitech"
    if REALSENSE_VENDOR_TAG in vendor or REALSENSE_VENDOR_TAG in product:
        return "realsense"
    # Fallback by name
    n = _v4l2_name(index).lower()
    if "logi" in n:
        return "logitech"
    if "realsense" in n:
        return "realsense"
    return "other"


def _resolve_by_id(prefix: str) -> List[int]:
    """Return v4l2 indices whose /dev/v4l/by-id symlink starts with ``prefix``.

    by-id symlinks survive enumeration order changes — the most reliable
    way to find a specific camera regardless of which /dev/videoN it
    landed on this boot.
    """
    import os, glob
    out = []
    for link in sorted(glob.glob("/dev/v4l/by-id/" + prefix + "*")):
        try:
            target = os.path.realpath(link)
            if "/video" in target:
                idx = int(target.rsplit("video", 1)[-1])
                out.append(idx)
        except Exception:
            continue
    return out


def _probe_capturable(idx: int, w: int = 640, h: int = 480) -> bool:
    """Quick sanity probe: can we open AND read a frame from this node?

    A Brio's main stream (index0 on its USB interface) yields real frames;
    metadata-only nodes (index1, index3) open but can't capture.
    """
    if not _CV2_OK:
        return False
    try:
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if not cap.isOpened():
            return False
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        for _ in range(2):
            cap.read()
        ok, _ = cap.read()
        cap.release()
        return bool(ok)
    except Exception:
        return False


def _find_logitech_main() -> Optional[int]:
    """Find the Logitech Brio's MAIN color stream V4L2 index.

    Strategy (in order):
      1. Look for /dev/v4l/by-id/usb-046d_Logi*-video-index0 (the main stream).
      2. Fall back to udev classification + capture probe across all nodes.
      3. Last resort: the first /dev/videoN whose name contains "logi".
    """
    # 1. Stable by-id resolution — Logi Brio's index0 is always the main stream
    by_id = _resolve_by_id("usb-046d_Logi")
    for idx in by_id:
        # by-id list is sorted by index suffix, so first entry is index0
        if not _is_node_busy(idx) and _probe_capturable(idx):
            return idx

    # 2. Classify-and-probe every video node
    for idx in _v4l2_list():
        if _classify_v4l2(idx) == "logitech" and not _is_node_busy(idx):
            if _probe_capturable(idx):
                return idx

    # 3. Sysfs name fallback
    for idx in _v4l2_list():
        name = _v4l2_name(idx).lower()
        if "logi" in name and not _is_node_busy(idx) and _probe_capturable(idx):
            return idx

    return None


def _find_realsense_v4l2_main() -> Optional[int]:
    """Find the RealSense's V4L2 color stream index for fallback when
    pyrealsense2 isn't available. Uses by-id symlinks for stability.
    """
    by_id = _resolve_by_id("usb-Intel_R__RealSense")
    for idx in by_id:
        if not _is_node_busy(idx) and _probe_capturable(idx):
            return idx

    for idx in _v4l2_list():
        if _classify_v4l2(idx) == "realsense" and not _is_node_busy(idx):
            if _probe_capturable(idx):
                return idx
    return None


# ─── pyrealsense2 helpers ───────────────────────────────────────────────────

def _rs_list_devices() -> List[Dict[str, Any]]:
    if not _RS_OK:
        return []
    try:
        ctx = rs.context()
        out = []
        for d in ctx.query_devices():
            out.append({
                "name": d.get_info(rs.camera_info.name),
                "serial": d.get_info(rs.camera_info.serial_number),
                "firmware": d.get_info(rs.camera_info.firmware_version) if
                    d.supports(rs.camera_info.firmware_version) else "?",
                "product_line": d.get_info(rs.camera_info.product_line) if
                    d.supports(rs.camera_info.product_line) else "?",
            })
        return out
    except Exception as e:
        logger.debug(f"rs context failed: {e}")
        return []


# D435i color sensor supports 1920x1080, 1280x720, 848x480, 640x480, 640x360 @ BGR8
# Depth is capped at 1280x720. We negotiate intelligently.
_RS_COLOR_OPTIONS = [(1920,1080), (1280,720), (848,480), (640,480), (640,360)]
_RS_DEPTH_OPTIONS = [(1280,720), (848,480), (640,480), (640,360), (480,270)]


def _rs_best_profile(requested: Tuple[int,int], options: List[Tuple[int,int]]) -> Tuple[int,int]:
    """Pick an exact match if possible, else the closest >= requested, else largest."""
    rw, rh = requested
    if (rw, rh) in options:
        return (rw, rh)
    # Prefer same-or-larger
    geq = [o for o in options if o[0] >= rw and o[1] >= rh]
    if geq:
        return min(geq, key=lambda o: o[0]*o[1])
    return max(options, key=lambda o: o[0]*o[1])


def _rs_capture(
    want_color: bool = True,
    want_depth: bool = False,
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    fps: int = DEFAULT_FPS,
    warmup: int = 5,
    serial: Optional[str] = None,
    timeout_ms: int = 5000,
) -> Tuple[Optional["np.ndarray"], Optional["np.ndarray"], Dict[str, Any]]:
    meta: Dict[str, Any] = {"backend": "pyrealsense2", "source": "realsense"}
    if not _RS_OK:
        meta["error"] = "pyrealsense2 not installed in this interpreter"
        return None, None, meta

    p = rs.pipeline()
    cfg = rs.config()
    if serial:
        cfg.enable_device(serial)

    cw, ch = _rs_best_profile((width, height), _RS_COLOR_OPTIONS)
    dw, dh = _rs_best_profile((width, height), _RS_DEPTH_OPTIONS)
    meta["negotiated_color"] = [cw, ch]
    meta["negotiated_depth"] = [dw, dh]

    if want_color:
        cfg.enable_stream(rs.stream.color, cw, ch, rs.format.bgr8, fps)
    if want_depth:
        cfg.enable_stream(rs.stream.depth, dw, dh, rs.format.z16, fps)

    try:
        profile = p.start(cfg)
    except Exception as e:
        meta["error"] = f"pipeline.start failed: {e}"
        return None, None, meta

    try:
        for _ in range(max(0, warmup)):
            try:
                p.wait_for_frames(timeout_ms=timeout_ms)
            except Exception:
                break
        frames = p.wait_for_frames(timeout_ms=timeout_ms)

        color = None
        depth = None
        if want_color:
            cf = frames.get_color_frame()
            if cf:
                color = np.asanyarray(cf.get_data())
                meta["color_size"] = [int(cf.get_width()), int(cf.get_height())]
        if want_depth:
            df = frames.get_depth_frame()
            if df:
                depth = np.asanyarray(df.get_data())
                meta["depth_size"] = [int(df.get_width()), int(df.get_height())]
                dev = profile.get_device()
                ds = dev.first_depth_sensor()
                meta["depth_scale_m"] = float(ds.get_depth_scale())

        dev = profile.get_device()
        meta["device_name"] = dev.get_info(rs.camera_info.name)
        meta["device_serial"] = dev.get_info(rs.camera_info.serial_number)
        return color, depth, meta
    finally:
        try: p.stop()
        except Exception: pass


def _colorize_depth(depth_u16, alpha: float = 0.03) -> Optional["np.ndarray"]:
    if depth_u16 is None or not _CV2_OK or not _NP_OK:
        return None
    try:
        depth8 = cv2.convertScaleAbs(depth_u16, alpha=alpha)
        return cv2.applyColorMap(depth8, cv2.COLORMAP_JET)
    except Exception as e:
        logger.debug(f"colorize depth failed: {e}")
        return None


def _encode_jpeg(frame_bgr, quality: int = 90) -> Optional[bytes]:
    if frame_bgr is None or not _CV2_OK:
        return None
    try:
        ok, buf = cv2.imencode(".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        return buf.tobytes() if ok else None
    except Exception as e:
        logger.debug(f"jpeg encode failed: {e}")
        return None


# ─── V4L2 capture (Brio + RS fallback) ──────────────────────────────────────

def _v4l2_open(index: int, w: int, h: int, fourcc: Optional[str] = None, fps: int = DEFAULT_FPS):
    if not _CV2_OK:
        return None
    if _is_node_busy(index):
        logger.debug(f"v4l2 node {index} busy, skipping")
        return None
    cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
    if not cap.isOpened():
        try: cap.release()
        except Exception: pass
        return None
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    try: cap.set(cv2.CAP_PROP_FPS, fps)
    except Exception: pass
    try: cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception: pass
    return cap


def _v4l2_capture(
    index: int, w: int, h: int,
    warmup: int = 3, fourcc: Optional[str] = None, fps: int = DEFAULT_FPS,
) -> Optional["np.ndarray"]:
    cap = _v4l2_open(index, w, h, fourcc=fourcc, fps=fps)
    if cap is None:
        return None
    try:
        for _ in range(warmup):
            cap.read()
            time.sleep(0.03)
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        try: cap.release()
        except Exception: pass


def _logitech_capture(
    width: int = DEFAULT_W, height: int = DEFAULT_H,
    fps: int = DEFAULT_FPS, warmup: int = 5,
    device_index: Optional[int] = None,
) -> Tuple[Optional["np.ndarray"], Dict[str, Any]]:
    meta: Dict[str, Any] = {"backend": "v4l2", "source": "logitech"}
    idx = device_index if device_index is not None else _find_logitech_main()
    if idx is None:
        meta["error"] = "no Logitech camera found on any /dev/videoX"
        return None, meta
    meta["v4l2_index"] = idx
    meta["device_name"] = _v4l2_name(idx)
    # Brio loves MJPG at 1080p
    frame = _v4l2_capture(idx, width, height, warmup=warmup, fourcc="MJPG", fps=fps)
    if frame is None:
        # Retry without fourcc forcing
        frame = _v4l2_capture(idx, width, height, warmup=warmup, fps=fps)
        meta["fourcc_fallback"] = True
    if frame is not None:
        meta["color_size"] = [frame.shape[1], frame.shape[0]]
    else:
        meta["error"] = "v4l2 read failed"
    return frame, meta


# ─── @tool ──────────────────────────────────────────────────────────────────

@tool
def use_camera(
    action: str = "discover",
    source: str = "auto",
    width: int = DEFAULT_W,
    height: int = DEFAULT_H,
    fps: int = DEFAULT_FPS,
    quality: int = 90,
    depth_colorize_alpha: float = 0.03,
    save_path: Optional[str] = None,
    warmup: int = 5,
    serial: Optional[str] = None,
    force_v4l2: bool = False,
    v4l2_device: Optional[int] = None,
) -> Dict[str, Any]:
    """
    📷 Capture frames from any connected camera (RealSense D435i or Logitech Brio 4K).

    Actions:
      discover       → list all cameras (RealSense + Logitech + V4L2 nodes)
      capture        → one color frame → inline image block
      capture_depth  → colorized depth heatmap → inline (RealSense only)
      capture_both   → color + colorized depth both inline (RealSense only)
      save           → write color JPEG to disk, returns path
      info           → RealSense stream profile dump

    Args:
      action       : see above (default "discover")
      source       : "realsense" | "logitech" | "auto" (default "auto")
                     "auto" picks realsense if present, else logitech
      width/height : default 1920x1080. Brio: up to 3840x2160.
                     RealSense color: 1920x1080 / 1280x720 / 848x480 / 640x480.
                     Request gets negotiated to the nearest supported profile.
      fps          : default 30
      quality      : JPEG quality 1..100 (default 90)
      depth_colorize_alpha : heatmap scale (default 0.03)
      save_path    : custom output path for action=save
      warmup       : frames to discard before capturing (default 5)
      serial       : target a specific RealSense by serial (RS only)
      force_v4l2   : bypass pyrealsense2 (test fallback)
      v4l2_device  : explicit V4L2 index override (e.g. 6 for Logitech)
    """
    result: Dict[str, Any] = {"status": "error", "message": "", "action": action, "source": source}

    if not _NP_OK or not _CV2_OK:
        result["message"] = "numpy/opencv not installed — cannot process frames"
        return result

    # ── discover ──
    if action == "discover":
        rs_devs = _rs_list_devices() if _RS_OK else []
        v4l2_info = []
        logitech_idx = _find_logitech_main()
        for idx in _v4l2_list():
            busy = _is_node_busy(idx)
            cls = _classify_v4l2(idx)
            entry = {
                "index": idx,
                "path": f"/dev/video{idx}",
                "name": _v4l2_name(idx),
                "classification": cls,
                "busy": busy,
                "is_logitech_main": (idx == logitech_idx),
            }
            # Only probe non-busy, non-realsense nodes (RS is owned by librealsense)
            if not busy and cls != "realsense":
                frame = _v4l2_capture(idx, 640, 480, warmup=1)
                entry["usable_via_v4l2"] = frame is not None
                if frame is not None:
                    entry["frame_shape"] = list(frame.shape)
            v4l2_info.append(entry)

        result["status"] = "success"
        result["realsense_available"] = _RS_OK
        result["realsense_devices"] = rs_devs
        result["logitech_main_v4l2_index"] = logitech_idx
        result["v4l2_devices"] = v4l2_info
        available_sources = []
        if rs_devs: available_sources.append("realsense")
        if logitech_idx is not None: available_sources.append("logitech")
        result["available_sources"] = available_sources
        result["default_source"] = available_sources[0] if available_sources else None
        result["message"] = (
            f"{len(rs_devs)} RealSense + "
            f"{'Logitech @ /dev/video' + str(logitech_idx) if logitech_idx is not None else 'no Logitech'} "
            f"→ sources: {available_sources}"
        )
        return result

    # Resolve source
    resolved_source = source
    if source == "auto":
        if _RS_OK and _rs_list_devices():
            resolved_source = "realsense"
        elif _find_logitech_main() is not None:
            resolved_source = "logitech"
        elif os.getenv("NEON_CAMERA_PROXY", "").strip():
            # No local device, but a dashboard camera proxy is configured —
            # resolve to realsense so the proxy-first fallback below serves
            # the shared frame (dashboard owns the single-owner RealSense).
            resolved_source = "realsense"
        else:
            result["message"] = "no cameras available (no RealSense, no Logitech)"
            return result
    result["source"] = resolved_source

    # ── info (RealSense only) ──
    if action == "info":
        if resolved_source != "realsense":
            result["message"] = f"info only supported for realsense, got source={resolved_source}"
            return result
        if not _RS_OK:
            result["message"] = "pyrealsense2 not installed — no info available"
            return result
        ctx = rs.context()
        devs = list(ctx.query_devices())
        if not devs:
            result["message"] = "no RealSense devices found via librealsense"
            return result
        profiles_out = []
        for d in devs:
            serial_n = d.get_info(rs.camera_info.serial_number)
            for sensor in d.query_sensors():
                sensor_name = sensor.get_info(rs.camera_info.name)
                for p in sensor.get_stream_profiles():
                    try:
                        vp = p.as_video_stream_profile()
                        profiles_out.append({
                            "device_serial": serial_n,
                            "sensor": sensor_name,
                            "stream": str(p.stream_type()).rsplit(".", 1)[-1],
                            "format": str(p.format()).rsplit(".", 1)[-1],
                            "width": vp.width(),
                            "height": vp.height(),
                            "fps": p.fps(),
                        })
                    except Exception:
                        pass
        result["status"] = "success"
        result["profile_count"] = len(profiles_out)
        result["profiles_sample"] = profiles_out[:40]
        result["message"] = f"{len(profiles_out)} stream profiles across {len(devs)} device(s)"
        return result

    # ── capture / capture_depth / capture_both / save ──
    if action not in ("capture", "capture_depth", "capture_both", "save"):
        result["message"] = (
            f"unknown action: {action}. "
            f"Use discover|capture|capture_depth|capture_both|save|info"
        )
        return result

    want_color = action in ("capture", "capture_both", "save")
    want_depth = action in ("capture_depth", "capture_both")

    # Proxy-first: if a dashboard camera proxy is configured and we only need
    # color (capture/save, no depth), pull the shared frame instead of fighting
    # the single-owner device. Falls through to direct capture if proxy fails.
    if want_color and not want_depth and os.getenv("NEON_CAMERA_PROXY", "").strip():
        _pj = _proxy_snapshot(resolved_source)
        if _pj:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if action == "save":
                sp = save_path
                if sp is None:
                    DEFAULT_SAVE_DIR.mkdir(parents=True, exist_ok=True)
                    sp = str(DEFAULT_SAVE_DIR / f"{ts}_proxy_color.jpg")
                else:
                    Path(sp).parent.mkdir(parents=True, exist_ok=True)
                with open(sp, "wb") as f:
                    f.write(_pj)
                return {"status": "success", "path": sp, "backend": "dashboard-proxy",
                        "source": resolved_source, "bytes": len(_pj), "timestamp": ts,
                        "message": f"saved color JPEG via dashboard proxy to {sp}"}
            # capture → inline image block
            return {"status": "success",
                    "content": [{"text": f"📷 color (via dashboard proxy) @ {ts}"},
                                {"image": {"format": "jpeg", "source": {"bytes": _pj}}}],
                    "backend": "dashboard-proxy", "source": resolved_source,
                    "bytes": len(_pj)}

    # Depth only works with RealSense
    if want_depth and resolved_source != "realsense":
        result["message"] = f"depth capture not supported on source={resolved_source} (realsense only)"
        return result

    color = depth = None
    meta: Dict[str, Any] = {}

    if resolved_source == "realsense":
        use_rs = _RS_OK and not force_v4l2
        if use_rs:
            color, depth, meta = _rs_capture(
                want_color=want_color, want_depth=want_depth,
                width=width, height=height, fps=fps,
                warmup=warmup, serial=serial,
            )
            if meta.get("error") and color is None and depth is None:
                if want_color and not force_v4l2:
                    logger.info(f"pyrealsense2 failed ({meta['error']}), trying V4L2 fallback")
                    use_rs = False
        if not use_rs and want_color:
            idx = v4l2_device if v4l2_device is not None else _find_realsense_v4l2_main()
            if idx is None:
                result["message"] = "no usable V4L2 device for realsense fallback"
                return result
            color = _v4l2_capture(idx, width, height, warmup=warmup)
            meta = {"backend": "v4l2", "source": "realsense", "v4l2_index": idx,
                    "device_name": _v4l2_name(idx)}
            if color is not None:
                meta["color_size"] = [color.shape[1], color.shape[0]]

    elif resolved_source == "logitech":
        color, meta = _logitech_capture(
            width=width, height=height, fps=fps, warmup=warmup,
            device_index=v4l2_device,
        )

    else:
        result["message"] = f"unknown source: {resolved_source} (use realsense|logitech|auto)"
        return result

    # Validate — with dashboard-proxy fallback when the device is busy/owned.
    proxy_jpeg = None
    if want_color and color is None:
        proxy_jpeg = _proxy_snapshot(resolved_source)
        if proxy_jpeg is None:
            result["message"] = f"color capture failed: {meta.get('error', 'unknown')}"
            result["meta"] = meta
            return result
        meta["backend"] = "dashboard-proxy"
        meta.setdefault("source", resolved_source)
    if want_depth and depth is None:
        result["message"] = f"depth capture failed: {meta.get('error', 'unknown')}"
        result["meta"] = meta
        return result

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    content: List[Dict[str, Any]] = []

    # Color
    color_jpeg = None
    if proxy_jpeg is not None:
        color_jpeg = proxy_jpeg
        content.append({"text": f"📷 color (via dashboard proxy) @ {ts}"})
        content.append({"image": {"format": "jpeg", "source": {"bytes": color_jpeg}}})
        meta["color_bytes"] = len(color_jpeg)
        meta.setdefault("color_size", [None, None])
    elif color is not None:
        color_jpeg = _encode_jpeg(color, quality=quality)
        if color_jpeg:
            w, h = meta.get("color_size") or [color.shape[1], color.shape[0]]
            meta["color_bytes"] = len(color_jpeg)
            content.append({"text": f"📷 color {w}x{h} @ {ts} "
                                    f"(source={meta.get('source')}, backend={meta.get('backend')})"})
            content.append({"image": {"format": "jpeg", "source": {"bytes": color_jpeg}}})

    # Depth
    depth_jpeg = None
    if depth is not None:
        depth_viz = _colorize_depth(depth, alpha=depth_colorize_alpha)
        depth_jpeg = _encode_jpeg(depth_viz, quality=quality)
        if depth_jpeg:
            w, h = meta.get("depth_size") or [depth.shape[1], depth.shape[0]]
            meta["depth_bytes"] = len(depth_jpeg)
            content.append({"text": f"🌈 depth {w}x{h} (colorized, alpha={depth_colorize_alpha})"})
            content.append({"image": {"format": "jpeg", "source": {"bytes": depth_jpeg}}})

    # ── save ──
    if action == "save":
        if save_path is None:
            DEFAULT_SAVE_DIR.mkdir(parents=True, exist_ok=True)
            save_path = str(DEFAULT_SAVE_DIR / f"{ts}_{meta.get('source','cam')}_color.jpg")
        else:
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(color_jpeg)
        result["status"] = "success"
        result["path"] = save_path
        result["backend"] = meta.get("backend")
        result["source"] = meta.get("source")
        result["width"] = (meta.get("color_size") or [None, None])[0]
        result["height"] = (meta.get("color_size") or [None, None])[1]
        result["bytes"] = len(color_jpeg)
        result["timestamp"] = ts
        result["message"] = (
            f"saved {result['width']}x{result['height']} "
            f"{result['source']} color JPEG to {save_path}"
        )
        return result

    # ── capture family ──
    result["status"] = "success"
    result["timestamp"] = ts
    result["meta"] = meta
    result["content"] = content
    parts = []
    if color is not None: parts.append(f"color {meta.get('color_size')}")
    if depth is not None: parts.append(f"depth {meta.get('depth_size')}")
    result["message"] = (
        f"captured from {meta.get('source')} via {meta.get('backend')}: "
        f"{', '.join(parts)}"
    )
    return result
