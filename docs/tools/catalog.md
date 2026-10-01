# tool catalog

<span class="read-badge">90s · 53 tools</span>

Every `@tool` in `tools/`, grouped by safety class.

<div class="motion-legend" markdown>
<span><span class="dot safe"></span>safe — read-only / self-bounded</span>
<span><span class="dot motion"></span>motion — FSM/mutex-gated</span>
<span><span class="dot danger"></span>danger — can fall/collapse</span>
</div>

## state / 9 { .safe }

| tool | what |
|---|---|
| `g1_get_state` | FSM, mode, balance, height, `arm_ready`, SOC |
| `g1_read_lowstate` | IMU (rpy/gyro/accel), joint postures, leg torque |
| `g1_list_fsm_states` | static ref of all 10 FSM ids |
| `g1_battery` | SOC, SOH, voltage, current, cycles |
| `g1_mainboard` | CPU/GPU/MCU temp, RAM, fan |
| `g1_pressure` | foot pressure (4× per foot) |
| `g1_joint_reference` · `g1_joint_name` · `g1_joint_index` | joint name ⇄ index + gains |

## posture / 7 { .motion }

| tool | what |
|---|---|
| `g1_set_fsm` | any FSM id → rich `{before, after, rc, message}` |
| `g1_set_stand_height` | 0.65 – 0.85 m (clamped) |
| `g1_set_swing_height` | stride height for walking |
| `g1_balance_stand` | re-engage balance controller |
| `g1_safe_squat_to_stand` · `g1_safe_lie_to_stand` · `g1_safe_stand_to_squat` | Damp-preamble transitions |

## arm / 4 { .motion }

Auto-release by default. `rt/armsdk` is single-writer — never parallelize.

| tool | what |
|---|---|
| `g1_arm_action(action=...)` | any gesture by name, FSM auto-transition |
| `g1_release_arm` | back to neutral |
| `g1_list_arm_actions` | static list of 16 gestures |
| `g1_get_arm_action_list_from_robot` | live list from controller |

## audio / 3 { .safe }

| tool | what |
|---|---|
| `g1_speak` | bidi voice persona + one-shot TTS |
| `g1_play_wav` | stream 16 kHz mono PCM to speakers |
| `g1_asr` | speech-to-text from onboard mic |

TTS/volume/LED → `use_unitree("audio", …)`.

## sensing / camera 2 / lidar 4 / slam 9 { .safe }

| tool | what |
|---|---|
| `use_camera` | V4L2 / RealSense / UVC → image block |
| `take_photo` | inject frame into voice agent's context |
| `g1_lidar_state` · `_snapshot` · `_switch` · `_stats` | Livox MID-360 |
| `g1_slam_*` | kiss-icp: start/stop/pose/reset/accumulate/save/load/list_maps/stats |

## locomotion / 7 { .danger }

**Robot will fall if misused. Always ask the user first.**

| tool | what |
|---|---|
| `g1_move_velocity(vx,vy,vyaw,duration)` | direct velocity, clamped |
| `g1_walk_forward(distance,speed)` | high-level |
| `g1_turn(angle_rad,yaw_rate)` | turn in place (+CCW) |
| `g1_stop_move` | vx=vy=vyaw=0 (always safe) |
| `g1_wave_hand_loco` · `g1_shake_hand_loco` | walk + gesture |
| `g1_set_task_id` | switch walking controller |

## universal / 1 + DDS / 8

| tool | what |
|---|---|
| `use_unitree(service, operation, parameters)` | **any** SDK RPC, AST-verified |
| `g1_dds_list_topics` · `_discover` · `_snapshot` | inspect the bus |
| `g1_dds_subscribe` · `_read` · `_unsubscribe` · `_stats` | stateful subs |
| `g1_dds_publish(topic, payload, unsafe=True)` | raw publish ⚠ |

`use_unitree` services: `loco` · `arm` · `audio` · `motion_switcher` · `vui` · `robot_state`.

## bundles

From `tools/__init__.py`:

| name | count | includes |
|---|:---:|---|
| `G1_STATE_TOOLS` | 9 | read-only + battery + joints |
| `G1_POSTURE_TOOLS` | 7 | FSM + height + safe transitions |
| `G1_ARM_TOOLS` | 4 | gestures |
| `G1_AUDIO_TOOLS` | 3 | speak / wav / asr |
| `G1_SENSING_TOOLS` | 22 | camera + lidar + slam + dds |
| `G1_LOCOMOTION_TOOLS` | 7 | 🔴 walking |
| `G1_UNIVERSAL_TOOLS` | 1 | `use_unitree` |
| `G1_SAFE_TOOLS` | 46 | everything except walking |
| `G1_ALL_TOOLS` | 53 | everything |

Default: `G1_TOOLS == G1_ALL_TOOLS`.

## dig deeper

[use_unitree](use-unitree.md){ .md-button } [use_dds](use-dds.md){ .md-button } [composed](composed.md){ .md-button } [sensing](sensing.md){ .md-button }
