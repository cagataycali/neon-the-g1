# AGENTS.md — Navigation Guide for `neon-the-g1`

> **Purpose**: fast orientation for any AI agent working in this directory.
> If you are an agent: read this first — it has the FSM chart, error codes,
> and safety rules inline. For deeper reference see `docs/reference/`.
> Human-facing usage → `README.md`.

Last updated: 2026-05-12 · Host: `ubuntu` (Jetson) · Robot: Unitree **G1+ pc4**

---

## 🗺️ Directory Map

```
g1_work/
├── agent.py              ← Strands agent entrypoint (interactive REPL)
├── env.sh                ← `source` this: sets CYCLONEDDS_URI + PYTHONPATH
├── requirements.txt      ← strands-agents, strands-agents-tools
├── README.md             ← human quick-start + tool catalog
├── AGENTS.md             ← you are here
│
├── tools/                ← all @tool wrappers (this is the agent's toolkit)
│   ├── __init__.py       ← exports G1_ALL_TOOLS / G1_SAFE_TOOLS / G1_LOOKOUT_TOOLS
│   ├── _g1_common.py     ← shared DDS init, singleton clients, FSM helpers, error decoder
│   ├── _dds_engine.py    ← low-level DDS subscribe/publish engine
│   │  # ── robot control (DDS) ──
│   ├── g1_state.py       ← 🟢 READ-ONLY: g1_get_state, g1_read_lowstate, g1_list_fsm_states
│   ├── g1_battery.py     ← 🟢 SOC / voltage / current / temperature
│   ├── g1_mainboard.py   ← 🟢 mainboard + foot-pressure telemetry
│   ├── g1_joints.py      ← 🟢 joint index/name reference (pure data)
│   ├── g1_posture.py     ← 🟡 MOTION: set_fsm / stand-height / balance-stand
│   ├── g1_safe_posture.py← 🟡 Damp-preamble transitions (incident-hardened)
│   ├── g1_arm.py         ← 🟡 ARM: g1_arm_action (any gesture), release, list
│   ├── g1_audio.py       ← 🟢 g1_play_wav + g1_asr (TTS/LED via use_unitree)
│   ├── g1_speak.py       ← 🎙 bidi voice control (start/stop/say)
│   ├── g1_locomotion.py  ← 🔴 WALKING — explicit user approval required
│   ├── g1_lidar.py       ← 🟢 Livox MID-360 state / snapshot / switch
│   ├── g1_slam.py        ← 🟢 kiss-icp SLAM (start/pose/save/load/…)
│   ├── g1_dds.py         ← 🟢 DDS escape hatches (list/subscribe/read/publish)
│   ├── use_unitree.py    ← 🔨 universal SDK wrapper (use_aws pattern)
│   ├── use_camera.py     ← V4L2/OpenCV/RealSense capture → Converse image blocks
│   │  # ── cross-persona stack (lookout-style) ──
│   ├── memory.py         ← SQLite kv/log + fs notes
│   ├── agent_log.py      ← unified cross-persona reasoning log
│   ├── voice_bridge.py   ← briefing queue → voice persona (voice_say)
│   ├── telegram.py       ← Telegram Bot API + history
│   ├── dispatch.py       ← spawn devduck sub-agents (cron / run_at)
│   ├── vision.py         ← take_photo (bidi image injection)
│   ├── prompts.py        ← per-persona prompt overrides (SQLite)
│   ├── manage_messages.py← trim/compact own history
│   ├── manage_tools.py   ← load/create tools at runtime
│   ├── use_spotify.py    ← Spotify playback control
│   └── voice_switch.py   ← swap voice profile/model/provider live
│
├── unitree_sdk2_python/  ← local SDK clone (pip wheel is BROKEN on G1 — see below)
│   └── example/g1/       ← audio / high_level / low_level reference scripts
│
├── scripts/
│   └── wifi_connect.sh   ← invoked by the systemd g1-telegram service on boot
│
├── telegram_events/      ← incoming telegram messages (poll by service)
├── whatsapp_events/      ← incoming whatsapp messages
├── realsense_color.jpg   ← sample RealSense capture
└── realsense_depth.jpg
```

---

## 🚀 Quick-Start Commands

```bash
# Load SDK env (sets CYCLONEDDS_URI + PYTHONPATH=./unitree_sdk2_python)
source env.sh

# Run the interactive Strands agent (uses agent.py)
python3 agent.py

# Health check without moving anything
python3 -c "
from tools import g1_get_state
print(g1_get_state())
"
```

Robot should reply with `mode={'name':'ai',...}`, `fsm_id` ∈ {500, 501, 801},
`arm_ready: True`. If `arm_ready: False`, call `g1_set_fsm(500)` first.

---

## 🎙️ Voice / Telegram / Memory (lookout-style stack)

neon uses **strands bidi voice architecture**, audio output goes to the G1 chest speaker via DDS (`AudioClient.PlayStream`). See `docs/voice-architecture.md` for the full diagram and tuning guide.

### Quick reference

| Capability       | Where                                            | Entry point                                        |
|---               |---                                               |---                                                 |
| Bidi voice agent | `g1.build_voice_agent()` in `g1.py`              | `make voice` → `g1_speech_listener.py`             |
| Telegram bot     | `g1.build_agent("telegram", ...)`                | `make tg` → `telegram_listener.py`                 |
| REPL agent       | `agent.py`                                       | `make run` (docker) / `make run-bare`              |
| Persistent memory| `tools/memory.py`  (sqlite + fs notes)           | `memory(action='kv_set'|'note_write'|'log_add')`   |
| Cross-persona log| `tools/agent_log.py` (sqlite)                    | injected into every persona's system prompt        |
| Briefing bus     | `tools/voice_bridge.py` (sqlite queue)           | `voice_say(text=..., importance=1|2)`              |
| Sub-agents       | `tools/dispatch.py` (devduck spawner)            | `dispatch(prompt=..., schedule=..., run_at=...)`   |
| Vision (bidi)    | `tools/vision.py` (BidiImageInputEvent)          | `take_photo(question="what do you see?")`          |

### Personas (sharing the same memory + tools)

```
   shell agent   ◄──── agent.py REPL (you, interactively)
   telegram agent ◄─── telegram_listener.py (incoming TG msg → spawn agent)
   voice agent   ◄──── g1_speech_listener.py (Brio mic + chest speaker)
   dispatch agent ◄─── dispatch tool (cron / run_at sub-agents)

   ALL share: .memory/mem.db (kv, log, agent_log, voice_bridge, tg_history,
              dispatches, dispatch_schedules, prompts)
```

When persona A does something, all OTHER personas SEE IT via the
"Unified Reasoning Log" block injected into their system prompts. This
means voice can pick up where telegram left off and vice versa.

### Inbound briefings → voice

Async messages flow:
```
telegram_listener.py:
  msg arrives → voice_bridge.push("telegram", "@user: hi") → SQLite
                                                                 │
                                                                 ▼
g1_bidi_audio.py (_BriefingInput):
  poll(2s) → pop_pending(5) → BidiTextInputEvent("[BRIEFING]...") → bidi model
```

Voice persona's prompt explains how to handle `[BRIEFING] [source/tag]`
markers — speak them as natural prose, not verbatim.

### Echo / VAD knobs (see `docs/voice-architecture.md`)

| Env var                    | Default | When to change                                    |
|---                         |---      |---                                                |
| `VOICE_PROVIDER`           | openai  | switch to nova_sonic / gemini                     |
| `VOICE_NAME`               | alloy   | per-provider voice                                |
| `VOICE_NO_AEC`             | (unset) | `1` to bypass AEC for diagnostic                  |
| `BRIO_DEVICE_INDEX`        | (auto)  | force a specific PyAudio input idx in Docker      |
| `G1_NETWORK_INTERFACE`     | eth0    | DDS interface                                     |

In code, also tunable: `stream_delay_ms`, `vad_threshold`,
`silence_duration_ms` (defaults 120 / 0.7 / 700).

### Slash commands (telegram_listener.py)

| Command    | Effect                                       |
|---         |---                                           |
| `/start`   | register chat_id, print help                 |
| `/clear`   | wipe THIS chat's tg_history                  |
| `/history` | dump last 30 turns                           |
| `/mute`    | set memory kv `voice.muted=true`             |
| `/unmute`  | set memory kv `voice.muted=false`            |
| `/voice`   | report current voice mute state              |
| `/state`   | dump live G1 state (FSM, IMU, balance)       |
| `/battery` | dump SOC / V / I / temperature               |

### Operational verbs

| Make target           | What                                              |
|---                    |---                                                |
| `make voice` / `voice-bg` | run voice listener (foreground / nohup)        |
| `make tg` / `tg-bg`   | run telegram listener                             |
| `make mute` / `unmute`| toggle voice mute                                 |
| `make voice-status`   | show mute state                                   |
| `make voice-push MSG=`| inject manual briefing                            |
| `make voice-bridge`   | pending briefing count                            |
| `make log-show`       | last 30 cross-persona turns                       |
| `make prompts-list`   | per-persona prompt overrides (defaults vs custom) |
| `make test-tg`        | telegram bot connectivity                         |
| `make test-voice`     | voice provider config check                       |
| `make ask Q='...'`    | one-shot query                                    |

---

## 🦾 The 53 Tools at a Glance

> **Count is verified.** "53" = the `G1_ALL_TOOLS` bundle (what `agent.py`
> loads). Category counts below sum to exactly 53 (46 safe + 7 walking).
> A raw `grep @tool tools/*.py` returns ~97 across 27 files — that extra
> ~44 are cross-persona infra (memory, telegram, dispatch, prompts,
> voice_*, vision, use_spotify, manage_*) and are NOT part of the G1
> robot toolset scoped by this table. Do NOT "bump" 53 to 97.

| Category | Safety | Count | Location | Exported list |
|---|---|---|---|---|
| State + battery + joints (read-only) | 🟢 safe | 9 | `g1_state.py`, `g1_battery.py`, `g1_mainboard.py`, `g1_joints.py` | `G1_STATE_TOOLS` |
| Posture / FSM (incl. safe-Damp) | 🟡 motion | 7 | `g1_posture.py`, `g1_safe_posture.py` | `G1_POSTURE_TOOLS` |
| Arm gestures | 🟡 motion | 4 | `g1_arm.py` | `G1_ARM_TOOLS` |
| Audio + LED | 🟢 safe | 3 | `g1_audio.py`, `g1_speak.py` | `G1_AUDIO_TOOLS` |
| Camera | 🟢 safe | 1 | `use_camera.py` | part of `G1_SENSING_TOOLS` |
| LiDAR | 🟢 safe | 4 | `g1_lidar.py` | `G1_LIDAR_TOOLS` |
| SLAM (kiss-icp) | 🟢 safe | 9 | `g1_slam.py` | `G1_SLAM_TOOLS` |
| DDS escape hatches | 🟢 safe | 8 | `g1_dds.py` | `G1_DDS_TOOLS` |
| Universal SDK wrapper | 🟡 varies | 1 | `use_unitree.py` | `G1_UNIVERSAL_TOOLS` |
| **Locomotion (walking)** | 🔴 **DANGER** | 7 | `g1_locomotion.py` | `G1_LOCOMOTION_TOOLS` |

**Bundles** (from `tools/__init__.py`):
- `G1_SAFE_TOOLS` = state + posture + arm + audio + sensing + use_unitree (46 tools, no walking)
- `G1_ALL_TOOLS` = everything (53 tools, includes walking)
- `G1_TOOLS` = alias for `G1_ALL_TOOLS`
- Plus the cross-persona `G1_LOOKOUT_TOOLS` (memory / voice_say / dispatch / telegram / …)

---

## 🚨 Safety Rules — READ BEFORE CALLING A TOOL

### 1. FSM gating (the #1 source of errors)
- **Arm actions** need `FSM ∈ {500, 501, 801}` → else rc=7404
- **Walking** needs `FSM = 501` (or 801) → else rc=7302
- Always call `g1_get_state()` first if unsure. `arm_ready: true` means go.

### 2. `rt/armsdk` is single-writer
- **Never parallelize** arm action calls → rc=7400 (topic occupied)
- **Always release** arm after action → else next call rc=7401 (arm holding)
- `g1_arm_action(auto_release=True)` handles this for you.

### 3. Dangerous ops (NEVER without explicit user OK)
| Op | Why dangerous |
|---|---|
| `g1_set_fsm(0)` (ZeroTorque) | Robot COLLAPSES if not on gantry |
| `g1_move_velocity / g1_walk_forward / g1_turn` | Robot WALKS — fall risk |
| `use_unitree(service_name="motion_switcher", operation_name="ReleaseMode")` | Leaves robot uncontrolled |
| `g1_set_fsm(0)` | Same as zero_torque |

### 4. Emergency stop
- `g1_stop_move()` is always safe (vx=vy=vyaw=0)
- `g1_damp()` → FSM 1, soft-holds current pose

---

## 🧠 Core Concepts (see `docs/reference/` for full depth)

### MotionSwitcher vs FSM
Two independent switches — you need BOTH correct:
1. **MotionSwitcher mode** = which controller owns the robot (`ai` is the only one installed)
2. **FSM id** = what pose/behavior inside that controller

`mode='ai'` alone is NOT enough. FSM must also be in the right state.

### Key FSM ids
| FSM | Name | Safe to enter? | Arm actions work? |
|---|---|---|---|
| 0 | ZeroTorque | 🔴 **only on gantry** | no |
| 1 | Damp | 🟢 soft limp, always safe | no |
| 3 | Sit | 🟡 from stand | no |
| 4 | StandUp | 🟡 rises from sit | no |
| **500** | Start (balance) | 🟢 default ready state | ✅ YES |
| **501** | Walk | 🟡 needed for walking | ✅ YES |
| **801** | BalanceExpert | 🟡 advanced | ✅ YES (fsm_mode 0/3) |
| 702 | Lie2StandUp | 🟡 from face-up | no |
| 706 | Squat2StandUp | 🟡 from squat | no |

### Error code cheat-sheet
| Code | Meaning | Fix |
|---|---|---|
| 0 | OK | — |
| 3104 | RPC timeout | check DDS, network_interface=eth0 |
| 7301 | LocoState not available | controller not running |
| 7400 | `rt/armsdk` occupied | another process is writing — don't parallelize |
| 7401 | Arm holding | call `g1_release_arm()` |
| 7402 | Invalid action id | see `g1_list_arm_actions()` |
| 7404 | Invalid FSM | call `g1_set_fsm(500)` to reach 500 |

### Arm action map (from `g1_list_arm_actions`)
```
11 two-hand kiss   12 left kiss     13 right kiss
15 hands up        17 clap          18 high five
19 hug             20 heart         21 right heart
22 reject          23 right hand up 24 x-ray
25 face wave       26 high wave     27 shake hand
99 release arm  ← ALWAYS follow actions with this
```

---

## 🔧 Extension Pattern (adding a new tool)

```python
# tools/g1_something.py
from strands import tool
from ._g1_common import (
    ensure_dds, get_loco_client, read_fsm_id, decode_code, HANDSHAKE_FSMS,
)

@tool
def g1_something(param: int = 0, network_interface: str = "eth0") -> dict:
    """One-line description the LLM will read to decide when to call this."""
    err = ensure_dds(network_interface)
    if err:
        return {"status": "error", "message": err}
    loco = get_loco_client()
    rc = loco.SomeMethod(param)
    return {
        "status": "success" if rc == 0 else "error",
        "rc": rc,
        "message": f"SomeMethod({param}) rc={decode_code(rc)}",
    }
```

Then add the import to `tools/__init__.py` and include it in the right
curated list (`G1_STATE_TOOLS`, etc.). `agent.py` auto-loads `G1_ALL_TOOLS`.

Use `_g1_common.py` helpers — DO NOT re-init DDS or re-create clients
(they are cached singletons; re-init causes crashes).

---

## 🌐 Network & SDK Facts (hard-learned)

- **Always** `network_interface="eth0"` — robot is on 192.168.123.0/24
- Main controller = `192.168.123.161` (Unitree embedded, no SSH)
- This Jetson = `192.168.123.164` (dev PC, runs everything we write)
- Use the **local SDK** at `./unitree_sdk2_python/` — pip wheel is broken
  (missing subpackages). `_g1_common.py` injects the path automatically.
- `ChannelFactoryInitialize(0, "eth0")` is idempotent — safe to call many times.

---

## 🤖 Agent Operating Style (baked into `agent.py` SYSTEM_PROMPT)

1. **Minimal words, batch parallel tool calls** whenever independent
2. **Check state before motion** — `g1_get_state()` is free
3. **Never auto-walk** — always ask the user before `g1_walk_forward` etc.
4. **Always release the arm** after gestures
5. **Verify outcomes** — call `g1_get_state()` after FSM transitions

---

## 🔗 Related Files to Consult

| Need | File |
|---|---|
| Deep SDK / FSM / DDS topic details | `docs/reference/{fsm,dds-topics,network}.md` |
| Human quickstart / tool catalog | `README.md` |
| Prior REPL sessions & experiments | `WORKLOG.md` |
| SDK reference examples | `unitree_sdk2_python/example/g1/` |
| SDK source (client classes) | `unitree_sdk2_python/unitree_sdk2py/g1/` |
| CycloneDDS config | `/home/unitree/cyclonedds_ws/cyclonedds.xml` |
| Persisted devduck service (telegram bot) | `systemctl --user status devduck-g1-telegram` |

---

## ✅ TL;DR for agents entering this directory

1. `source env.sh` first (for any script you run)
2. Import from `tools` (not from `unitree_sdk2py` directly, unless prototyping)
3. Call `g1_get_state()` before any motion — check `arm_ready`
4. Stick to `G1_SAFE_TOOLS` unless user explicitly approves walking
5. When in doubt → this file + `docs/reference/` have the error code, the FSM chart, the DDS topic

---

## 📚 Deep-dive & incident history

Hard-won lessons, incident reviews, and subsystem rewrite notes (Brio mic
tuning, the 2026-05-19 Damp-collapse incident, the speech.py→g1_speak voice
rewrite saga, docker canonicalization, bluetooth presence) live in
[`WORKLOG.md`](WORKLOG.md) so this file stays a fast navigation guide.
