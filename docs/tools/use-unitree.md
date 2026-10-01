# use_unitree

<span class="read-badge">60s · universal</span>

One tool. The whole SDK.

```python
use_unitree(service_name="loco", operation_name="StandUp", parameters={})
```

Inspired by `use_aws`. **AST-verified** against the local `unitree_sdk2_python/`
clone at import time — hallucinated calls are caught before they touch DDS.

## services

| `service_name=` | client | example ops |
|---|---|---|
| `loco` | `LocoClient` | `StandUp` `Damp` `Squat` `SetFsmId` `Move` `HighStand` |
| `arm` | `G1ArmActionClient` | `ExecuteAction` `GetActionList` |
| `audio` | `AudioClient` | `TtsMaker` `SetVolume` `LedControl` `PlayStream` |
| `motion_switcher` | `MotionSwitcherClient` | `CheckMode` `SelectMode` `ReleaseMode` |
| `vui` | `VuiClient` | `SetBrightness` `SetVolume` `TtsMaker` |
| `robot_state` | `RobotStateClient` | `ServiceList` `ServiceSwitch` |
| `meta` | *(discovery)* | `list_services` `list_operations` `describe_operation` |

## examples

```python
# stand height
use_unitree("loco", "SetStandHeight", {"stand_height": 0.75})
# speaker volume
use_unitree("audio", "SetVolume", {"volume": 60})
# TTS
use_unitree("audio", "TtsMaker", {"text": "hello from neon", "speaker_id": 0})
# eye LED
use_unitree("audio", "LedControl", {"R": 0, "G": 240, "B": 255})
# motion mode
use_unitree("motion_switcher", "CheckMode", {})
```

## discover before you dispatch (always safe, no DDS)

```python
use_unitree("meta", "list_services", {})
use_unitree("meta", "list_operations", {"service_name": "audio"})
use_unitree("meta", "describe_operation",
            {"service_name": "loco", "operation_name": "SetFsmId"})
```

## how AST verification works

At boot, `use_unitree.py` parses every client class and records each method's
params. Every call is checked:

1. `service_name` known
2. `operation_name` exists on that client
3. `parameters` keys ⊆ method signature
4. FSM preconditions (for `loco`)

Bad calls return a helpful error, not a crashed DDS writer.

Seven operations are flagged `high_danger` in the reply whatever the
environment: `loco.ZeroTorque`, `loco.SetFsmId`, `loco.SetVelocity`,
`loco.Move`, `loco.WaveHand`, `loco.ShakeHand` and
`motion_switcher.ReleaseMode`. Mutative operations are flagged too unless
`BYPASS_TOOL_CONSENT=true` (the compose stack sets it); `Get*` and `Check*`
calls are read-only and never flagged.

## composed vs universal

| situation | use |
|---|---|
| arm gesture (FSM + mutex + release) | `g1_arm_action` |
| walking (clamps + user OK) | `g1_walk_forward` / `g1_move_velocity` |
| squat/lie transition | `g1_safe_*` |
| **anything else in the SDK** | `use_unitree` |

**Composed for safety, universal for coverage.**


---

[use_dds](use-dds.md){ .md-button } [composed](composed.md){ .md-button } [catalog](catalog.md){ .md-button }
