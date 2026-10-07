# sensing

<span class="read-badge">60s</span>

Everything `neon` uses to **see and hear**. Vision via V4L2/RealSense; audio via
the G1's onboard AudioClient + the bidi voice agent.

## one camera owner

V4L2 and RealSense devices are single-open, so the dashboard container is the
only process that opens them. Every other persona reads
`GET /api/camera/<realsense_color|brio>/snapshot` on the dashboard with a
service token (`NEON_CAMERA_PROXY`, `NEON_CAMERA_PROXY_TOKEN`); one camera,
many readers, the same frame the cockpit shows. A `401` means a stale token
(see [troubleshooting](../guide/troubleshooting.md#cameras)).

## use_camera

```python
use_camera(action="capture", source="realsense")
# action: discover | capture | capture_depth | capture_both | save | info
# source: realsense | logitech | auto | <v4l2-index>
```

Returns a single frame as a Strands image block (the LLM sees it directly).
Color vs depth is chosen by **action**, not source. With the proxy set it
fetches the dashboard snapshot; without it, it opens the device.

- **RealSense D435i**: color + depth, 640×480, rectified
- **Logitech Brio 4K**: chest-mounted, 1920×1080
- **any V4L2 device**: by path or index

## take_photo

The agent's own eyes, in every persona. In a voice session the JPEG goes into
the realtime stream and the model answers in audio; in the dashboard chat,
the REPL, Telegram and the thinker it comes back as a tool-result image block.
`NEON_DASHBOARD_CAM` picks the default camera.

```python
take_photo(question="What do you see?")
take_photo(question="What's on my whiteboard?", hires=True)
```

## audio

```python
g1_asr(duration_s=3.0)                    # onboard mic → text (API 1002)
g1_play_wav(file_path="/path/clip.wav")   # 16 kHz mono PCM → chest speaker
```

## g1_speak, full bidi voice

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

!!! tip "Measure real travel"
    `g1_walk_forward` reads `rt/odommodestate` before and after the command
    and returns `measured_m`, how far the robot *actually* moved vs commanded.
    The kiss-icp SLAM tools were retired on 2026-10-07.

---

[catalog](catalog.md){ .md-button } [perceive recipe](../recipes/perception.md){ .md-button } [voice architecture](../voice-architecture.md){ .md-button }
