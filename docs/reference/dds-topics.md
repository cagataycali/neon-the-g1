# DDS topics

<span class="read-badge">⏱ 60s · ref</span>

All `rt/*` topics currently discoverable on the G1 CycloneDDS domain.

## subscribe (read-only from our side)

| Topic | Rate | Type | Content |
|---|:---:|---|---|
| `rt/lowstate` | 500 Hz | `LowState_` | IMU + 29 motor states (q, dq, tau, temp, V) |
| `rt/bmsstate` / `rt/lf/bmsstate` | 1 Hz | `BmsState_` | SOC, SOH, voltage, current, cycles |
| `rt/multiplestate` | 50 Hz | `LowState_` | Combined state (FSM id, mode, balance, stand height) |
| `rt/inspire/state` | 50 Hz | `HandState_` | Inspire hand finger positions |
| `rt/head/cam/image_color` (?) | — | — | head cam RGB — NOT in codebase; verify live via `g1_dds_discover()` before use |
| `rt/utlidar/cloud_livox_mid360` | 10 Hz | `PointCloud2_` | Livox Mid-360 point cloud |
| `rt/lf/lowstate` | low-freq | `LowState_` | Low-freq LowState variant |
| `rt/lf/secondary_imu` | low-freq | `LowState_` | Secondary IMU |
| `rt/lf/sportmodestate` | low-freq | `SportModeState_` | Motion state (low-freq) |
| `rt/mainboardstate` | 1 Hz | `MainBoardState_` | Fan / board temps |
| `rt/pressuresensorstate` | 50 Hz | `PressSensorState_` | Foot pressure sensors |
| `rt/wirelesscontroller` | on-event | `WirelessController_` | Remote joystick (silent if unpaired) |
| `rt/odom` | — | `Odometry_` | Robot odometry (nav_msgs) |
| `rt/unitree_slam/odom` | — | `Odometry_` | Unitree SLAM odometry |
| `rt/unitree_slam/global_map` | — | `PointCloud2_` | Unitree SLAM global map |
| `rt/utlidar/lidar_state` | — | `LidarState_` | LiDAR sensor state |

## RPC request/response

Every `Client` class in the SDK maps to a pair:

| Service | Request | Response |
|---|---|---|
| `LocoClient` | `rt/api/loco/request` | `rt/api/loco/response` |
| `G1ArmActionClient` | `rt/api/arm/request` | `rt/api/arm/response` |
| `AudioClient` | `rt/api/audio/request` | `rt/api/audio/response` |
| `MotionSwitcherClient` | `rt/api/motion_switcher/request` | `rt/api/motion_switcher/response` |
| `VuiClient` | `rt/api/vui/request` | `rt/api/vui/response` |
| `RobotStateClient` | `rt/api/robot_state/request` | `rt/api/robot_state/response` |

Payload: `{"api_id": int, "parameter": str_json, "priority": int}` → response `{"code": int, "data": str_json}`.

## publish (dangerous — need `unsafe=True` via `g1_dds_publish`)

| Topic | Type | What |
|---|---|---|
| `rt/lowcmd` | `LowCmd_` | ⚠ direct motor torques — will collapse the robot if misused |
| `rt/armsdk` | `LowCmd_` | arm gesture id (single-writer, use `g1_arm_action`) |
| `rt/bmscmd` | `BmsCmd_` | battery commands |
| `rt/inspire/cmd` | `HandCmd_` | Inspire hand commands |
| `rt/user_lowcmd` | `LowCmd_` | ⚠ user low-level cmd |
| `rt/utlidar/switch` | `String_` | LiDAR ON/OFF switch (use `g1_lidar_switch`) |

## discover live

```python
g1_dds_list_topics()
# → ["rt/lowstate", "rt/bmsstate", "rt/armsdk", "rt/api/loco/request", ...]

g1_dds_discover()      # no args — scans the whole bus via `cyclonedds ls`
# → {"ok": True, "count": 12,
#    "live_topics": ["rt/lowstate", "rt/bmsstate", "rt/armsdk", ...]}

g1_dds_snapshot("rt/lowstate")   # read ONE actual sample from a topic
# → {"ok": True, "topic": "rt/lowstate", "sample": {...decoded IDL fields...}}
```

## DDS config

- CycloneDDS config: `/home/unitree/cyclonedds_ws/cyclonedds.xml`
- Interface: **eth0** (not eth1, not WiFi — those don't see the motor bus)
- Domain: 0
- Env: `CYCLONEDDS_URI=file:///home/unitree/cyclonedds_ws/cyclonedds.xml`

`env.sh` in the repo sets this automatically.

## SDK type reference

The DDS types live under `unitree_sdk2_python/unitree_sdk2py/idl/`. Useful when
writing a new subscriber:

```
unitree_hg::msg::dds_::LowState_   → 29 motors + IMU (also rt/multiplestate "combined state")
unitree_hg::msg::dds_::BmsState_   → battery
unitree_hg::msg::dds_::LowCmd_     → motor command (⚠) (also rt/armsdk, rt/user_lowcmd)
unitree_hg::msg::dds_::HandState_  → Inspire hand state
```
