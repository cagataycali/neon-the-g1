# gestures

<span class="read-badge">45s</span>

Every arm gesture, dispatched through `g1_arm_action(action=...)`. Fifteen
gestures plus `release arm` (id 99), the SDK `action_map` as shipped.

<div class="motion-legend" markdown>
<span><span class="dot safe"></span>auto-release</span>
<span><span class="dot motion"></span>needs FSM 500/501/801</span>
</div>

| action | key | · | action | key |
|---|---|---|---|---|
| face wave | `'face wave'` | · | hug | `'hug'` |
| high wave | `'high wave'` | · | clap | `'clap'` |
| shake hand | `'shake hand'` | · | hands up | `'hands up'` |
| high five | `'high five'` | · | right hand up | `'right hand up'` |
| heart | `'heart'` | · | reject | `'reject'` |
| right heart | `'right heart'` | · | x-ray | `'x-ray'` |
| kiss | `'two-hand kiss'` | · | left/right kiss | `'left kiss'` / `'right kiss'` |

```python
from tools import g1_arm_action
g1_arm_action(action="high wave")   # auto_release=True by default
```

## wave, live

Triggered from the dashboard — `g1_arm_action('high wave')` on the real robot.

<video controls muted playsinline loop width="100%" style="border-radius:8px;max-width:720px;">
  <source src="../../assets/neon-wave.mp4" type="video/mp4">
</video>

## what happens under the hood

1. **FSM gate** — not in `{500,501,801}`? auto-transition or refuse (`rc=7404`).
2. **Arm mutex** — locks `rt/armsdk` (single-writer). Never parallelize arm calls.
3. **Dispatch** — `G1ArmActionClient.ExecuteAction(id)`.
4. **Auto-release** — action id 99 back to neutral.

See [FSM + errors](../reference/fsm.md) for the state machine.
