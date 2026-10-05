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
    try:
        pipe = rs.pipeline()
        cfg = rs.config()
        cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        cfg.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
        pipe.start(cfg)
    except Exception as e:
        _log(f"pipeline start failed: {e}")
        return 3
    _log(f"pipeline started {width}x{height}@{fps}")
    out = sys.stdout.buffer
    enc = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    misses = 0
    try:
        while True:
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
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        try:
            pipe.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
