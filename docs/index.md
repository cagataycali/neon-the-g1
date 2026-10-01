---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>

<img src="assets/neon.svg" alt="neon" class="logo"/>

<p class="tagline">a strands agent on the unitree g1+ · on the edge</p>

<div class="buttons" markdown>
[:material-rocket-launch: 3-minute tour](start/quickstart.md){ .md-button .md-button--primary }
[:material-github: GitHub](https://github.com/cagataycali/neon-the-g1){ .md-button }
</div>

<div class="stat-row" markdown>
<span class="stat-pill"><span class="dot"></span> <strong>&nbsp;live on G1</strong></span>
<span class="stat-pill"><strong>53</strong> tools</span>
<span class="stat-pill"><strong>500 Hz</strong> state</span>
<span class="stat-pill"><strong>29</strong> motors</span>
<span class="stat-pill"><strong>0</strong> cloud hops</span>
</div>

</div>

<div class="type-demo" markdown>
<span class="line"><span class="prompt">neon&nbsp;&gt;</span> wave hello and tell me what you see</span>
<span class="line"><span class="out">  → g1_arm_action('high wave')</span>     <span class="ok">rc=0 · released</span></span>
<span class="line"><span class="out">  → use_camera(source='realsense')</span> <span class="ok">rc=0 · 640×480</span></span>
<span class="line"><span class="prompt">neon&nbsp;&gt;</span> Waved 👋 — I see a person at a desk.<span class="cur"></span></span>
</div>

---

<div class="tour" markdown>

## the whole thing in 3 minutes

<div class="tour-step" markdown>
<span class="tour-num">1</span>
**What it is.** A [Strands](https://github.com/strands-agents) agent that drives a
**Unitree G1+** humanoid. It runs **on the robot's Jetson**, speaks DDS to the
motor bus, and turns every SDK call into a typed, safety-gated tool.
</div>

<div class="tour-step" markdown>
<span class="tour-num">2</span>
**How you talk to it.** One agent, four faces — REPL, voice, Telegram, dispatch.
Say `wave`, `walk forward 30cm`, `what do you see?`, `map this room`. It plans,
batches tool calls, and replies.
</div>

<div class="tour-step" markdown>
<span class="tour-num">3</span>
**Why it's safe.** Walking and posture are FSM-gated. The arm bus is mutex-locked.
Raw motor publishes need `unsafe=True`. The robot refuses what it shouldn't do.
</div>

[Start the tour →](start/quickstart.md){ .md-button .md-button--primary }

</div>

```mermaid
flowchart LR
  U(["🗣️ you"]) -->|"voice · telegram · REPL"| A

  subgraph G1["🤖 Unitree G1+ · Jetson Orin"]
    direction TB
    A["🧠 neon<br/>strands · 53 tools"]
    C["⚙️ MCU controllers"]
    M["🦾 motors · arms<br/>audio · LEDs"]
    S[("👁️ lidar · imu<br/>bms · camera")]
    A -->|"DDS"| C --> M
    A -.->|"read-only"| S
  end

  classDef brain stroke:#cc5a3a,stroke-width:1.5px
  classDef io stroke:#2e8b8b,stroke-width:1.5px
  class A brain
  class C,M,S io
```

## at a glance

| | |
|---|---|
| **robot** | Unitree G1+ · 29 motors · 2 arms · Livox MID-360 lidar |
| **compute** | Jetson Orin NX — agent runs *on the robot* |
| **transport** | CycloneDDS over `eth0` (no ROS) |
| **tools** | 53 — state · posture · arm · audio · lidar · SLAM · DDS · vision |
| **models** | Bedrock (default: Claude Opus) via `NEON_MODEL_ID` |
| **surfaces** | REPL · voice (chest speaker) · Telegram · dispatch |
| **safety** | FSM gating · arm mutex · velocity clamps · `unsafe=True` for raw publishes |

## the recommended path

<div class="grid cards" markdown>

-   :material-numeric-1-circle:{ .lg } **[Run it](start/quickstart.md)** — clone, pick a model, wave hello. 60s.

-   :material-numeric-2-circle:{ .lg } **[See it](showcase/gestures.md)** — gestures, locomotion, the full request→motion pipeline.

-   :material-numeric-3-circle:{ .lg } **[Do it](recipes/index.md)** — copy-paste recipes: wave, walk, perceive, map, telegram.

-   :material-numeric-4-circle:{ .lg } **[Dig in](tools/catalog.md)** — 53 tools, `use_unitree`, `use_dds`, safety, architecture.

</div>

??? abstract "everything else"
    **Start** · [docker](start/docker.md) · [systemd](start/systemd.md)<br>
    **Tools** · [catalog](tools/catalog.md) · [use_unitree](tools/use-unitree.md) · [use_dds](tools/use-dds.md) · [composed](tools/composed.md) · [sensing](tools/sensing.md)<br>
    **Guide** · [safety](guide/safety.md) · [architecture](guide/architecture.md) · [troubleshooting](guide/troubleshooting.md) · [cli](guide/cli.md) · [extending](guide/extending.md) · [strands-robots](guide/strands-robots.md) · [WebXR teleop](guide/webxr-teleop.md) · [voice](voice-architecture.md)<br>
    **Reference** · [FSM + errors](reference/fsm.md) · [DDS topics](reference/dds-topics.md) · [joints](reference/joints.md) · [network](reference/network.md)

---

<p style="text-align:center; color:var(--muted); font-size:0.85em; margin-top:2rem;">
  <span class="neon-dot"></span>
  built with <a href="https://github.com/strands-agents">strands-agents</a> ·
  <a href="https://github.com/cagataycali/devduck">devduck</a> ·
  <a href="https://github.com/unitreerobotics/unitree_sdk2_python">unitree_sdk2</a>
</p>
