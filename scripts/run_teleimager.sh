#!/usr/bin/env bash
# NEON teleimager — WebRTC stereo/mono camera downlink for WebXR teleop.
# Streams the Logitech Brio (video_id 2) over WebRTC :60001 to the Quest 3.
# (Dashboard uses RealSense /dev/video6, so no device conflict with this.)
set -euo pipefail
export MAMBA_ROOT_PREFIX=$HOME/micromamba
export CYCLONEDDS_URI=${CYCLONEDDS_URI:-/home/unitree/cyclonedds_ws/cyclonedds.xml}
export CYCLONEDDS_HOME=/usr/local
export LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH:-}
export XR_TELEOP_CERT=${XR_TELEOP_CERT:-$HOME/.config/xr_teleoperate/cert.pem}
export XR_TELEOP_KEY=${XR_TELEOP_KEY:-$HOME/.config/xr_teleoperate/key.pem}
SRC="$HOME/xr_teleoperate/teleop/teleimager/src"
cd "$SRC"
echo "📸 NEON teleimager → WebRTC :60001 (Brio video_id 2)"
exec "$HOME/bin/micromamba" run -n teleop \
  env PYTHONPATH="$SRC" \
      CYCLONEDDS_URI="$CYCLONEDDS_URI" CYCLONEDDS_HOME=/usr/local \
      LD_LIBRARY_PATH=/usr/local/lib \
      XR_TELEOP_CERT="$XR_TELEOP_CERT" XR_TELEOP_KEY="$XR_TELEOP_KEY" \
  python -m teleimager.image_server --no-affinity
