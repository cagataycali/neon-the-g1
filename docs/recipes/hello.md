# stand → wave → sit

<span class="read-badge">12s demo</span>

The canonical first demo. Robot starts in Damp, stands, waves, sits.

**Prereqs:** clear floor (one body-length around), battery ≥ 30%, floor-standing.

## say

```
> stand up, wave hello, then sit down
```

neon says what it is about to do in one sentence while it starts (about 10 s
end to end); posture changes and gestures need no confirmation round-trip.

## what neon does

```python
[ g1_get_state(), g1_battery() ]        # parallel state check
use_unitree("loco", "SetFsmId", {"fsm_id": 3})   # → Sit
use_unitree("loco", "SetFsmId", {"fsm_id": 500}) # → 500 (arm_ready)
[
  use_unitree("audio", "TtsMaker", {"text": "hello", "speaker_id": 0}),
  g1_arm_action(action="high wave"),    # auto-release
]
use_unitree("loco", "SetFsmId", {"fsm_id": 2})   # → Squat
use_unitree("loco", "SetFsmId", {"fsm_id": 1})   # → Damp
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
