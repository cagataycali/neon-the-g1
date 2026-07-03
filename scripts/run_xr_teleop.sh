#!/usr/bin/env bash
# NEON WebXR live teleop launcher (conda env: teleop)
set -euo pipefail
export MAMBA_ROOT_PREFIX=$HOME/micromamba
export PYTHONPATH=$HOME/neon-the-g1:$HOME/xr_teleoperate
export CYCLONEDDS_URI=${CYCLONEDDS_URI:-/home/unitree/cyclonedds_ws/cyclonedds.xml}
export CYCLONEDDS_HOME=/usr/local
export LD_LIBRARY_PATH=/usr/local/lib:${LD_LIBRARY_PATH:-}
# IK loads URDF/cache via CWD-relative ../assets — run from teleop dir
cd "$HOME/xr_teleoperate/teleop"
ARM=${ARM:-G1_29}
IFACE=${IFACE:-eth0}
INPUT=${INPUT:-hand}
HZ=${HZ:-30}
echo "🥽 NEON XR teleop LIVE → arm=$ARM iface=$IFACE input=$INPUT hz=$HZ"
exec "$HOME/bin/micromamba" run -n teleop python -m neon.teleop.xr_bridge \
  --arm "$ARM" --network-interface "$IFACE" --input-mode "$INPUT" --frequency "$HZ"
