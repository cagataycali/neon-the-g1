"""G1 @tool wrappers for the Strands agent.

Organizing principle (post-2026-05-13 cleanup):
    Anything that's a 1:1 SDK call is reachable via ``use_unitree``.
    This package only keeps tools that do REAL WORK beyond the SDK:
      * FSM/safety gating        (g1_arm, g1_locomotion, g1_safe_posture)
      * Rich round-trip returns  (g1_posture.g1_set_fsm)
      * DDS subscription parsing (g1_state, g1_battery, g1_mainboard, g1_lidar)
      * Out-of-SDK subsystems    (kiss-icp SLAM, cameras, mic, YOLO)
      * Reference data           (g1_joints)
      * Escape hatches           (g1_dds, use_unitree)

    from tools import G1_ALL_TOOLS
    agent = Agent(tools=[*G1_ALL_TOOLS, ...])
"""
# Composed / FSM-safe robot control tools
from .g1_state import g1_get_state, g1_read_lowstate, g1_list_fsm_states
from .g1_posture import (
    g1_set_fsm,
    g1_set_stand_height,
    g1_set_swing_height,
    g1_balance_stand,
)
from .g1_safe_posture import (
    g1_safe_squat_to_stand, g1_safe_lie_to_stand, g1_safe_stand_to_squat,
)
from .g1_locomotion import (
    g1_move_velocity, g1_stop_move, g1_walk_forward, g1_turn,
    g1_wave_hand_loco, g1_shake_hand_loco, g1_set_task_id,
)
from .g1_arm import (
    g1_arm_action, g1_release_arm, g1_list_arm_actions,
    g1_get_arm_action_list_from_robot,
)

# Audio helpers that do real work (not SDK 1:1)
from .g1_audio import g1_play_wav, g1_asr

# Read-only DDS subscribers + derived state
from .g1_battery import g1_battery
from .g1_mainboard import g1_mainboard, g1_pressure
from .g1_lidar import (
    g1_lidar_state, g1_lidar_snapshot, g1_lidar_switch, g1_lidar_stats,
)
from .g1_slam import (
    g1_slam_start, g1_slam_stop, g1_slam_pose, g1_slam_reset,
    g1_slam_accumulate, g1_slam_save, g1_slam_load, g1_slam_list_maps,
    g1_slam_stats,
)

# Reference data (pure Python, no robot needed)
from .g1_joints import g1_joint_reference, g1_joint_name, g1_joint_index

# Sensing / perception
from .use_camera import use_camera
from .vision import capture_camera

# DDS escape hatches
from .g1_dds import (
    g1_dds_list_topics, g1_dds_discover, g1_dds_snapshot,
    g1_dds_subscribe, g1_dds_read, g1_dds_unsubscribe,
    g1_dds_stats, g1_dds_publish,
)

# Universal SDK wrapper (use_aws pattern)
from .use_unitree import use_unitree, TOOL_SPEC as USE_UNITREE_SPEC


# ═══════════════════════════════════════════════════════════════════════════
# Curated bundles
# ═══════════════════════════════════════════════════════════════════════════

G1_STATE_TOOLS = [
    g1_get_state, g1_read_lowstate, g1_list_fsm_states,
    g1_battery, g1_mainboard, g1_pressure,
    g1_joint_reference, g1_joint_name, g1_joint_index,
]

G1_POSTURE_TOOLS = [
    g1_set_fsm, g1_set_stand_height, g1_set_swing_height, g1_balance_stand,
    g1_safe_squat_to_stand, g1_safe_lie_to_stand, g1_safe_stand_to_squat,
]

# 🚨 walking
G1_LOCOMOTION_TOOLS = [
    g1_move_velocity, g1_stop_move, g1_walk_forward, g1_turn,
    g1_wave_hand_loco, g1_shake_hand_loco, g1_set_task_id,
]

G1_ARM_TOOLS = [
    g1_arm_action, g1_release_arm, g1_list_arm_actions,
    g1_get_arm_action_list_from_robot,
]

from .g1_speak import g1_speak
G1_AUDIO_TOOLS = [g1_speak, g1_play_wav, g1_asr]
G1_LIDAR_TOOLS = [
    g1_lidar_state, g1_lidar_snapshot, g1_lidar_switch, g1_lidar_stats,
]

G1_SLAM_TOOLS = [
    g1_slam_start, g1_slam_stop, g1_slam_pose, g1_slam_reset,
    g1_slam_accumulate, g1_slam_save, g1_slam_load, g1_slam_list_maps,
    g1_slam_stats,
]

G1_DDS_TOOLS = [
    g1_dds_list_topics, g1_dds_discover, g1_dds_snapshot,
    g1_dds_subscribe, g1_dds_read, g1_dds_unsubscribe,
    g1_dds_stats, g1_dds_publish,
]

from .kimodo import kimodo

G1_SENSING_TOOLS = (
    [use_camera, capture_camera]
    + G1_LIDAR_TOOLS + G1_SLAM_TOOLS + G1_DDS_TOOLS
)

# The one big hammer. Covers every SDK RPC via use_aws-style dispatch.
G1_UNIVERSAL_TOOLS = [use_unitree]

# Safe set (everything except walking) — still full coverage via use_unitree
G1_SAFE_TOOLS = (
    G1_STATE_TOOLS
    + G1_POSTURE_TOOLS
    + G1_ARM_TOOLS
    + G1_AUDIO_TOOLS
    + G1_SENSING_TOOLS
    + G1_UNIVERSAL_TOOLS
)

# Kimodo text-to-motion (🚨 low-level playback; safety-gated inside the tool)
G1_MOTION_GEN_TOOLS = [kimodo]

# Everything
G1_ALL_TOOLS = G1_SAFE_TOOLS + G1_LOCOMOTION_TOOLS + G1_MOTION_GEN_TOOLS

# Default export
G1_TOOLS = G1_ALL_TOOLS


# cross-persona stack (memory/voice/telegram)
from .memory import memory
from .agent_log import (
    record as agent_log_record,
    recent as agent_log_recent,
    format_for_prompt as agent_log_format_for_prompt,
    stats as agent_log_stats,
    clear as agent_log_clear,
)
from .voice_bridge import (
    voice_say,
    push as voice_bridge_push,
    pop_pending as voice_bridge_pop_pending,
    flush_stale as voice_bridge_flush_stale,
    stats as voice_bridge_stats,
)
from .telegram import (
    telegram,
    record_message as telegram_record_message,
    format_history_for_prompt as telegram_format_history,
    download_file as telegram_download_file,
    listen as telegram_listen,
)
from .vision import take_photo
from .voice_control import voice_control

# Curated bundle for callers. Cut to what NEON uses (owner, 2026-10-07):
# dispatch, phone/ADB, use_spotify, prompts, manage_messages, manage_tools
# and make are gone from the source.
G1_LOOKOUT_TOOLS = [
    memory, voice_say, telegram, take_photo, kimodo, voice_control,
]

