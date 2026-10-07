# measured walk

<span class="read-badge">~8s</span>

Walk, and let the robot tell you how far it really went.

**Prereqs:** standing in FSM 501 (Walk), a clear path one body-length longer
than the request, you within reach.

## say

```
> walk forward one metre
```

That sentence is the consent; NEON does not ask again.

## what neon does

```python
take_photo(question="Is the path ahead clear for 1 metre? ...")   # look first
g1_walk_forward(distance=1.0, speed=0.25)   # 1.0 m is the per-request cap
# -> {"moved": true, "requested_m": 1.0, "measured_m": 0.94,
#     "message": "moved 0.94 m (rc=0 ...)"}
```

> Moved ninety-four centimetres.

The number is the robot's own odometry (`rt/odommodestate` before and after),
not the request echoed back. When nothing moved the result says so:

```python
# -> {"moved": false, "measured_m": 0.001,
#     "message": "SDK accepted (rc=0) but no displacement measured ... did NOT move"}
```

> The robot did not move: it is not in walk mode. Stand me up first.

## variations

```
> turn ninety degrees left        # g1_turn(angle_rad=1.57), reports measured_rad
> walk 30 cm, then tell me what you see
```

## notes

- Odometry drifts over a room; `measured_m` is per request, not a map frame.
- Under 0.1 m is rounded up, over 1.0 m is capped: ask twice for two metres.
- Every walk is a `tool` row in the activity log with `moved` and `measured_m`.
