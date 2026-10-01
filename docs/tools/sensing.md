# sensing

<span class="read-badge">60s</span>

Everything `neon` uses to **see and hear**. Vision via V4L2/RealSense; audio via
the G1's onboard AudioClient + the bidi voice agent.

## use_camera

```python
use_camera(action="capture", source="realsense")
# action: discover | capture | capture_depth | capture_both | save | info
# source: realsense | logitech | auto | <v4l2-index>
```

Returns a single frame as a Strands image block (the LLM sees it directly).
Color vs depth is chosen by **action**, not source.

- **RealSense D435i** — color + depth · 640×480 · rectified
- **Logitech Brio 4K** — chest-mounted · 1920×1080
- **any V4L2 device** — by path or index

## take_photo

Injects a frame straight into the **voice agent's** multimodal context (only
inside a running bidi voice agent — see `g1_speak`).

```python
take_photo(question="What do you see?")
take_photo(question="What's on my whiteboard?", device=1)  # 0=RealSense 1=Brio
```

## audio

```python
g1_asr(duration_s=3.0)                    # onboard mic → text (API 1002)
g1_play_wav(file_path="/path/clip.wav")   # 16 kHz mono PCM → chest speaker
```

## g1_speak — full bidi voice

Brio mic → AEC → bidi model → G1 chest speaker (DDS). Has the entire G1 toolset
wired in.

```python
g1_speak(action="start")                  # boot voice persona
g1_speak(action="say", text="hello")      # one-shot TTS (no bidi)
g1_speak(action="status")
g1_speak(action="stop")
# provider: openai | nova_sonic | gemini
```

!!! tip "Speak from anywhere, or not at all"
    Any persona can call `voice_say("...")` (or `importance=2` for urgent
    interrupts): picked up from a shared SQLite queue within ~2 s. The agent
    can also silence itself with `voice_control(action="snooze", minutes=60)`;
    muted gates the mic and the speaker (see [voice](../voice-architecture.md#mute-and-snooze)).

## LiDAR / 4

Livox MID-360 on the head, via SDK.

| tool | what |
|---|---|
| `g1_lidar_state` | spinning? points/sec? temp? |
| `g1_lidar_snapshot` | one point-cloud frame |
| `g1_lidar_switch(on=True)` | power on/off |
| `g1_lidar_stats` | averaged over N seconds |

## SLAM / 9

kiss-icp on the LiDAR stream (standalone from Unitree's onboard SLAM).

| tool | what |
|---|---|
| `g1_slam_start` · `g1_slam_stop` | toggle worker |
| `g1_slam_pose` | pose estimate (xyz + quat) |
| `g1_slam_reset` | clear map |
| `g1_slam_accumulate` | build global map for N s |
| `g1_slam_save(name)` · `g1_slam_load(name)` | persist |
| `g1_slam_list_maps` · `g1_slam_stats` | maps + drift |

!!! tip "Measure real travel"
    `g1_slam_start` → short `g1_walk_forward` → `g1_slam_pose` tells you how far
    the robot *actually* moved vs commanded.


---

[catalog](catalog.md){ .md-button } [perceive recipe](../recipes/perception.md){ .md-button } [voice architecture](../voice-architecture.md){ .md-button }
