# tool catalog

<span class="read-badge">90s, {{facts:all_tools}} robot tools</span>

Every robot `@tool` exported by `tools/__init__.py`, grouped by safety class.
The counts on this page are read from that file at build time, so they match
the code on `main`. The {{facts:lookout_tools}} cross-persona tools (memory,
voice, Telegram, dispatch, phone, ...) are listed at the end.

<div class="motion-legend" markdown>
<span><span class="dot safe"></span>safe, read-only / self-bounded</span>
<span><span class="dot motion"></span>motion, FSM/mutex-gated</span>
<span><span class="dot danger"></span>danger, can fall/collapse</span>
</div>

## state / {{facts:state_tools}} { .safe }

| tool | what |
|---|---|
| `g1_get_state` | FSM, mode, balance, height, `arm_ready`, SOC |
| `g1_read_lowstate` | IMU (rpy/gyro/accel), joint postures, leg torque |
| `g1_list_fsm_states` | static ref of all 10 FSM ids |
| `g1_battery` | SOC, SOH, voltage, current, cycles |
| `g1_mainboard` | CPU/GPU/MCU temp, RAM, fan |
| `g1_pressure` | foot pressure (4× per foot) |
| `g1_joint_reference`, `g1_joint_name`, `g1_joint_index` | joint name ⇄ index + gains |

## posture / {{facts:posture_tools}} { .motion }

| tool | what |
|---|---|
| `g1_set_fsm` | any FSM id → rich `{before, after, rc, message}` |
| `g1_set_stand_height` | 0.65-0.85 m (clamped) |
| `g1_set_swing_height` | stride height for walking |
| `g1_balance_stand` | re-engage balance controller |
| `g1_safe_squat_to_stand`, `g1_safe_lie_to_stand`, `g1_safe_stand_to_squat` | Damp-preamble transitions |

## arm / {{facts:arm_tools}} { .motion }

Auto-release by default. `rt/armsdk` is single-writer, never parallelize.

| tool | what |
|---|---|
| `g1_arm_action(action=...)` | any gesture by name, FSM auto-transition |
| `g1_release_arm` | back to neutral |
| `g1_list_arm_actions` | the SDK `action_map`: 15 gestures + `release arm` |
| `g1_get_arm_action_list_from_robot` | live list from controller |

## audio / {{facts:audio_tools}} { .safe }

| tool | what |
|---|---|
| `g1_speak` | bidi voice persona + one-shot TTS |
| `g1_play_wav` | stream 16 kHz mono PCM to speakers |
| `g1_asr` | speech-to-text from onboard mic |

TTS/volume/LED → `use_unitree("audio", …)`.

## sensing / camera {{facts:camera_tools}} / lidar {{facts:lidar_tools}} / slam {{facts:slam_tools}} / dds {{facts:dds_tools}} { .safe }

| tool | what |
|---|---|
| `use_camera` | RealSense / Brio / any V4L2 device → image block; pulls the shared frame from the dashboard when `NEON_CAMERA_PROXY` is set |
| `capture_camera` | one JPEG returned as data (base64, optional save), no agent context needed: for dashboards, fleet planes, scripts |
| `g1_lidar_state`, `_snapshot`, `_switch`, `_stats` | Livox MID-360 |
| `g1_slam_*` | kiss-icp: start/stop/pose/reset/accumulate/save/load/list_maps/stats |
| `g1_dds_list_topics`, `_discover`, `_snapshot` | inspect the bus |
| `g1_dds_subscribe`, `_read`, `_unsubscribe`, `_stats` | stateful subscriptions |
| `g1_dds_publish(topic, payload, unsafe=True)` | raw publish; `unsafe=True` required on the five motor/BMS/hand topics |

## locomotion / {{facts:locomotion_tools}} { .danger }

**Robot will fall if misused.** An explicit request to walk is the consent;
the agent looks first (`take_photo`), walks only in FSM 501, and every walk
and turn measures its own displacement on `rt/odommodestate`: the result says
`moved=true` with the metres, or `moved=false` and why. "Done" is only said
when it moved. See [safety](../guide/safety.md).

| tool | what |
|---|---|
| `g1_move_velocity(vx,vy,vyaw,duration)` | direct velocity, clamped |
| `g1_walk_forward(distance,speed)` | distance clamped to 0.1 - 1.0 m, speed 0.05 - 0.5 m/s (at least 0.15 under 0.3 m); returns `moved`, `requested_m`, `measured_m` |
| `g1_turn(angle_rad,yaw_rate)` | turn in place (+CCW), yaw rate clamped 0.1 - 0.6 rad/s; returns `measured_rad` |
| `g1_stop_move` | vx=vy=vyaw=0 (always safe) |
| `g1_wave_hand_loco`, `g1_shake_hand_loco` | walk + gesture |
| `g1_set_task_id` | switch walking controller |

## motion generation / {{facts:motion_gen_tools}} { .danger }

| tool | what |
|---|---|
| `kimodo(action, prompt, csv, confirm, on_gantry)` | text-to-motion clips played on `rt/lowcmd`; dry run unless `confirm=True` **and** `on_gantry=True`. [Page](motion-gen.md). |

## universal / {{facts:universal_tools}}

| tool | what |
|---|---|
| `use_unitree(service, operation, parameters)` | **any** SDK RPC, AST-verified |

`use_unitree` services: `loco`, `arm`, `audio`, `motion_switcher`, `vui`, `robot_state`.

## bundles

From `tools/__init__.py`:

| name | count | includes |
|---|:---:|---|
| `G1_STATE_TOOLS` | {{facts:state_tools}} | read-only + battery + joints |
| `G1_POSTURE_TOOLS` | {{facts:posture_tools}} | FSM + height + safe transitions |
| `G1_ARM_TOOLS` | {{facts:arm_tools}} | gestures |
| `G1_AUDIO_TOOLS` | {{facts:audio_tools}} | speak / wav / asr |
| `G1_SENSING_TOOLS` | {{facts:sensing_tools}} | cameras + lidar + slam + dds |
| `G1_UNIVERSAL_TOOLS` | {{facts:universal_tools}} | `use_unitree` |
| `G1_SAFE_TOOLS` | {{facts:safe_tools}} | everything above: no walking, no motion generation |
| `G1_LOCOMOTION_TOOLS` | {{facts:locomotion_tools}} | walking (danger) |
| `G1_MOTION_GEN_TOOLS` | {{facts:motion_gen_tools}} | `kimodo` (danger) |
| `G1_ALL_TOOLS` | {{facts:all_tools}} | everything |
| `G1_LOOKOUT_TOOLS` | {{facts:lookout_tools}} | cross-persona: memory, voice_say, dispatch, telegram, take_photo, prompts, manage_messages, manage_tools, make, kimodo, phone, voice_control |

Default: `G1_TOOLS == G1_ALL_TOOLS`.

## cross-persona tools / {{facts:lookout_tools}}

The tools every persona carries besides the robot ones (`G1_LOOKOUT_TOOLS`).

| tool | what |
|---|---|
| `memory` | kv + notes in `.memory/mem.db`, shared by every persona |
| `voice_say(text, importance)` | queue a sentence for the chest speaker from any persona |
| `voice_control(action, minutes, level)` | the agent on its own voice: mute, snooze, unmute, status, speaker volume |
| `take_photo(question, hires)` | a frame from the dashboard camera into the model (audio reply in voice, image block elsewhere) |
| `telegram(action, chat_id, text)` | send messages and photos to the owner's chat |
| `dispatch(prompt, mode, tools)` | a background sub-agent that reports back through `voice_say` |
| `phone(action, ...)` | an ADB-connected Android phone on the robot's back: status, unlock, open, screenshot, tap, swipe; the unlock PIN comes from `PHONE_PIN` and is never spoken or written |
| `prompts` | read, override and reset a persona's own system prompt |
| `manage_messages` / `manage_tools` | compact own history / load extra tools at runtime |
| `make` | run a Makefile target from inside the agent |
| `kimodo` | [motion generation](motion-gen.md) |

## who sees what

`g1.py` builds three lists; every tool in them is wrapped by `tools/tool_log.py`
so each call lands as a `tool` row in `agent_log`.

| persona | list | contents |
|---|---|---|
| voice, REPL, dashboard chat | `build_voice_tools` | 12 cross-persona + 14 robot tools (state 2, posture 2, arm 3, locomotion 4, audio 2, `use_camera`) + Spotify, plus ADB when installed; small on purpose for realtime latency |
| telegram, thinker | `build_tools` | 13 cross-persona + `telegram` + all {{facts:all_tools}} robot tools + GitHub / Spotify / ADB when installed |
| `neon-mcp --safe` | `G1_SAFE_TOOLS` | {{facts:safe_tools}} robot tools, no walking, no `kimodo` |

Need lidar, SLAM or DDS by voice? The prompt says so: load them on demand with
`manage_tools`.

## dig deeper

[use_unitree](use-unitree.md){ .md-button } [use_dds](use-dds.md){ .md-button } [composed](composed.md){ .md-button } [sensing](sensing.md){ .md-button }
