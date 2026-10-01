# SLAM-guided walk

<span class="read-badge">⏱ ~8s</span>

Walk 1 m, read SLAM pose before/after to **measure real travel**.

**Prereqs:** standing (FSM 500/501), clear 1 m path, lidar on.

## say

```
> start SLAM, walk forward 1 meter, then tell me how far you actually moved
```

## what neon does

```python
[ g1_lidar_switch(on=True), g1_slam_start() ]   # wait ~2s to stabilize
before = g1_slam_pose()
g1_set_fsm(501)                                  # Walk
g1_walk_forward(distance=1.0, speed=0.25)
g1_stop_move()
after = g1_slam_pose()
# travelled = hypot(after-before)
```

> Commanded 1.0 m · measured 0.94 m via SLAM · 6 cm under (slippage).

## variations

```
> measure how accurately you can turn 90 degrees   # reads slam yaw
> walk to a point 2 m ahead using SLAM feedback     # closed-loop correction
```

## notes

- First pose after start can be zero for 1-2 s; the tool flags freshness and the agent waits.
- `speed ≤ 0.3 m/s` is well-calibrated; above that expect 5-10% overshoot.
