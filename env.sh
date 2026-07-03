# source this: `source env.sh`
# Auto-detects the right CYCLONEDDS_URI and local SDK path.
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# CycloneDDS URI — check the common locations
for _c in \
  "$CYCLONEDDS_URI" \
  "/home/unitree/cyclonedds_ws/cyclonedds.xml" \
  "$_HERE/cyclonedds.xml" \
; do
  if [ -f "$_c" ]; then
    export CYCLONEDDS_URI="$_c"
    break
  fi
done

# Local SDK — pip wheel is broken, prefer clone
for _sdk in \
  "$_HERE/unitree_sdk2_python" \
  "/tmp/unitree_sdk2_python" \
  "/home/unitree/unitree_sdk2_python" \
; do
  if [ -d "$_sdk" ]; then
    export PYTHONPATH="$_sdk${PYTHONPATH:+:$PYTHONPATH}"
    break
  fi
done

# Default DDS interface
export G1_IFACE="${G1_IFACE:-eth0}"

echo "🤖 G1 env ready"
echo "  CYCLONEDDS_URI=${CYCLONEDDS_URI:-<not set>}"
echo "  PYTHONPATH=${PYTHONPATH:-<not set>}"
echo "  G1_IFACE=$G1_IFACE"
