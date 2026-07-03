# stand → wave → sit

<span class="read-badge">⏱ 12s demo</span>

The canonical first demo. Robot starts in Damp, stands, waves, sits.

**Prereqs:** clear floor (one body-length around), battery ≥ 30%, floor-standing.

## say

```
> stand up, wave hello, then sit down
```

neon confirms the plan (~10 s, battery, arm_ready after stand), you reply `yes`.

## what neon does

```python
[ g1_get_state(), g1_battery() ]        # parallel state check
g1_set_fsm(3)                           # → Sit
g1_safe_squat_to_stand()                # → 500 (arm_ready)
[
  use_unitree("audio", "TtsMaker", {"text": "hello", "speaker_id": 0}),
  g1_arm_action(action="high wave"),    # auto-release
]
g1_safe_stand_to_squat()                # → Squat
g1_set_fsm(1)                           # → Damp
```

## variations

```
> stand, wave, sit — no talking          # omit TTS
> stand, look ahead, wave if you see a person, sit   # chains use_camera + reasoning
```

## if it refuses

- `rc=7404` → wrong FSM. Say `damp`, retry.
- arm stuck → say `release arm`.
- stands then wobbles → slippery floor / battery sag. Test on a gantry first.
