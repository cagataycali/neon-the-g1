# map a space

<span class="read-badge">~5 min record</span>

Walk a room, save a kiss-icp map, reload + localize next session.

**Prereqs:** lidar spinning, ~5 min clear floor, operator walks alongside (0.2 m/s).

## 1 / record

```
> start SLAM, I'll guide you around. Save it as 'living-room' when done.
```

```python
[ g1_lidar_switch(on=True), g1_slam_start() ]   # wait ~3s
g1_slam_reset()                                  # fresh map
```

Then walk it through the space (`walk forward 2m`, `turn 90 right`, …), then:

```
> save the map as 'living-room'      →  g1_slam_save(name="living-room")
```

## 2 / localize later

```
> load the 'living-room' map and tell me where you are
```

```python
[ g1_lidar_switch(on=True), g1_slam_start(), g1_slam_load(name="living-room") ]
pose = g1_slam_pose()   # after ~5s ICP converge
```

> Localized in 'living-room', x=1.23 y=0.47 yaw=12°, near the door.

## list maps

```python
g1_slam_list_maps()   # → [{name, size_kb, points, saved}, ...]
```

## tips

- Rooms need visual diversity for ICP (bare walls drift, add a few obstacles).
- ICP uses ~15-20% of one Jetson core. Maps are pointclouds, not meshes.
