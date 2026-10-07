"""RealSense worker: owns the pyrealsense2 pipeline in its OWN process.

Why a process and not a thread: pybind11 keeps the GIL while ``rs.pipeline()``
/ ``rs.context()`` enumerate USB devices through libusb. When any USB port on
the Jetson is flapping (dmesg ``usb usb2-portN: Cannot enable``), that
enumeration blocks inside the kernel's hub lock for seconds at a time, and the
thread holding the GIL takes every other Python thread down with it: the
dashboard listened on :8080 but never answered a request (2026-10-05, after a
hard power-off). A thread timeout cannot help because the timing thread needs
the GIL too. In a child process the stall is contained; the parent reads with
a timeout and restarts the child when it stops producing.

Protocol (stdout, binary): ``NEON`` + kind (1 byte, ``C`` colour / ``D`` depth
colour-mapped) + u32 big-endian JPEG length + JPEG bytes. One line per event
on stderr. Exit code 3 = pipeline could not start (the parent backs off).
"""
from __future__ import annotations

import os
import struct
import sys
import time

MAGIC = b"NEON"
DEPTH_MAX_MM = 4000


def _log(msg: str) -> None:
    sys.stderr.write(f"[rs-worker] {msg}\n")
    sys.stderr.flush()


def hardware_reset(rs, why: str, settle_s: float = 20.0) -> bool:
    """Power-cycle the D435i over USB and wait until it re-enumerates.

    Measured on the G1 Jetson (2026-10-07): re-enumeration 2 s, first frame
    3-4 s after that, both colour-only and colour+depth."""
    _log(f"hardware_reset ({why})")
    try:
        devs = rs.context().query_devices()
        if len(devs) == 0:
            _log("hardware_reset: no device to reset")
            return False
        devs[0].hardware_reset()
    except Exception as e:
        _log(f"hardware_reset failed: {e}")
        return False
    time.sleep(1.0)
    deadline = time.time() + settle_s
    while time.time() < deadline:
        try:
            if len(rs.context().query_devices()):
                time.sleep(1.5)   # let the UVC nodes settle before opening
                _log("hardware_reset: device is back")
                return True
        except Exception:
            pass
        time.sleep(1.0)
    _log(f"hardware_reset: device did not come back in {settle_s:.0f}s")
    return False


def main() -> int:
    width = int(os.getenv("RS_WIDTH", "640"))
    height = int(os.getenv("RS_HEIGHT", "480"))
    fps = int(os.getenv("RS_FPS", "15"))
    quality = int(os.getenv("RS_JPEG_Q", "70"))
    try:
        import cv2
        import numpy as np
        import pyrealsense2 as rs
    except Exception as e:  # pragma: no cover - the parent already checked find_spec
        _log(f"imports failed: {e}")
        return 3
    first_s = float(os.getenv("RS_FIRST_FRAME_S", "10"))
    reset_first = os.getenv("RS_RESET", "") in ("1", "true", "yes")

    def _start():
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        cfg.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        pipe.start(cfg)
        return pipe

    def _first_frame(pipe):
        """The D435i on a cold Jetson boot starts in <1 s and then takes 3-8 s
        for the first frame, or never delivers one at all (measured 2026-10-07:
        try_wait_for_frames stayed False for 8 s, 57 frames/2 s right after a
        hardware_reset). Wait a bounded time for the first frame."""
        deadline = time.time() + first_s
        while time.time() < deadline:
            ok, frames = pipe.try_wait_for_frames(1000)
            if ok:
                return frames
        return None

    try:
        if reset_first:
            hardware_reset(rs, "parent asked for a reset")
        pipe = _start()
        frames = _first_frame(pipe)
        if frames is None:
            _log(f"pipeline started but no frame in {first_s:.0f}s: hardware reset")
            try:
                pipe.stop()
            except Exception:
                pass
            hardware_reset(rs, "no first frame")
            pipe = _start()
            frames = _first_frame(pipe)
            if frames is None:
                _log(f"no frames: still nothing {first_s:.0f}s after a hardware reset")
                return 4
    except Exception as e:
        _log(f"pipeline start failed: {e}")
        return 3
    _log(f"pipeline started {width}x{height}@{fps}, first frame in hand")
    out = sys.stdout.buffer
    enc = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    misses = 0
    try:
        while True:
            if frames is None:
                try:
                    frames = pipe.wait_for_frames(timeout_ms=2000)
                except Exception as e:
                    misses += 1
                    if misses >= 5:
                        _log(f"no frames: {e}")
                        return 4
                    continue
            misses = 0
            cf = frames.get_color_frame()
            df = frames.get_depth_frame()
            if cf:
                ok, buf = cv2.imencode(".jpg", np.asanyarray(cf.get_data()), enc)
                if ok:
                    out.write(MAGIC + b"C" + struct.pack(">I", len(buf)) + buf.tobytes())
            if df:
                depth = np.asanyarray(df.get_data())
                d8 = (np.clip(depth, 0, DEPTH_MAX_MM).astype(np.float32) / DEPTH_MAX_MM * 255).astype(np.uint8)
                ok, buf = cv2.imencode(".jpg", cv2.applyColorMap(d8, cv2.COLORMAP_TURBO), enc)
                if ok:
                    out.write(MAGIC + b"D" + struct.pack(">I", len(buf)) + buf.tobytes())
            out.flush()
            frames = None
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        try:
            pipe.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
