# describe the room

<span class="read-badge">⏱ ~3s</span>

Camera → multimodal model → one sentence back. No YOLO — the model sees the
frame directly.

**Prereqs:** RealSense D435i (or Brio 4K), a multimodal model.

## say

```
> what do you see?
```

## what neon does

```python
use_camera(action="capture", source="realsense")   # inline image block → model
```

> A person at a desk with a laptop, a plant to the left, a window behind. Bright room.

## counting & distance

```
> how many people? any chairs?
> how far is the nearest person?
```

```python
# inside the voice agent — model counts natively:
take_photo(question="How many people? Any chairs?")
# depth-aware:
use_camera(action="capture_both", source="realsense")   # color + depth heatmap
```

## variations

```
> watch for 30 seconds and tell me if anything changes   # captures every 5s, diffs
```

## tips

- RealSense at eye level → better social framing. Brio (1080p) is chest-mounted.
- Debug: `use_camera(action="capture", source="realsense", save_path="/tmp/frame.jpg")`.
