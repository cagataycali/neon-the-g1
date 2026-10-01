# locomotion

<span class="read-badge">45s</span>

Walking, turning, posture — **the dangerous stuff**. The user's explicit
request is the consent: NEON looks first (`take_photo`), walks if the path is
clear, otherwise says the specific reason. Every walk and turn measures its
own displacement on `rt/odommodestate` and reports `moved=true/false`; the
model may only say "done" when the tool says it moved.

!!! danger "Robot can fall"
    Clear floor, operator within reach, ~500 ms stop latency. See [safety](../guide/safety.md).

| intent | call |
|---|---|
| walk 30 cm | `g1_walk_forward(distance=0.3, speed=0.2)` |
| turn 90° | `g1_turn(angle_rad=1.57, yaw_rate=0.4)` |
| strafe right | `g1_move_velocity(vx=0, vy=0.2, vyaw=0, duration=2)` |
| **stop** (safe) | `g1_stop_move()` |
| squat → stand | `g1_safe_squat_to_stand()` |
| lie → stand | `g1_safe_lie_to_stand()` |
| damp (safe) | `g1_set_fsm(1)` |

## voice-driven walking, live

Strands **bidirectional speech-to-speech** → `g1_move_velocity` → the G1 steps.
Spoken command in, motion out, in real time.

<div style="display:flex;gap:12px;flex-wrap:wrap;">
<figure style="flex:1;min-width:240px;margin:0;">
<video controls muted playsinline loop width="100%" style="border-radius:8px;">
  <source src="../../assets/move-forward.mp4" type="video/mp4">
</video>
<figcaption style="text-align:center;color:var(--muted);font-size:.85em;">walk forward</figcaption>
</figure>
<figure style="flex:1;min-width:240px;margin:0;">
<video controls muted playsinline loop width="100%" style="border-radius:8px;">
  <source src="../../assets/move-backwards.mp4" type="video/mp4">
</video>
<figcaption style="text-align:center;color:var(--muted);font-size:.85em;">walk backward</figcaption>
</figure>
</div>

## clamps

| parameter | clamp |
|---|---|
| `duration` (`g1_move_velocity`) | 0 to 10 s |
| `distance` (`g1_walk_forward`) | 0.1 to 1.0 m per request |
| `speed` (`g1_walk_forward`) | 0.05 to 0.5 m/s, at least 0.15 under 0.3 m |
| `yaw_rate` (`g1_turn`) | 0.1 to 0.6 rad/s |

Velocities passed to `g1_move_velocity` are not clamped; the prompt keeps them
at 0.2 m/s and 0.3 rad/s. All three tools read `rt/odommodestate` before and
0.5 s after the command; `moved=false` means `rc=0` and no displacement.

The full exchange, including a refusal, is the [measured walk](../recipes/walk.md) recipe.

---

[safety model](../guide/safety.md){ .md-button } [gestures](gestures.md){ .md-button } [walk recipe](../recipes/walk.md){ .md-button }
