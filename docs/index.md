---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>

<img src="assets/mark.svg" alt="" class="logo"/>

<p class="hero-title">neon</p>

<p class="tagline">a strands agent on the unitree g1+, running on the robot</p>

<div class="buttons" markdown>
[:material-rocket-launch: 3-minute tour](start/quickstart.md){ .md-button .md-button--primary }
[:material-github: GitHub](https://github.com/cagataycali/neon-the-g1){ .md-button }
</div>

<div class="stat-row" markdown>
<span class="stat-pill"><span class="dot"></span> <strong>&nbsp;live on G1</strong></span>
<span class="stat-pill"><strong>{{facts:all_tools}}</strong> robot tools</span>
<span class="stat-pill"><strong>500 Hz</strong> state</span>
<span class="stat-pill"><strong>29</strong> motors</span>
<span class="stat-pill"><strong>8</strong> safety gates</span>
</div>

</div>

<div class="type-demo" markdown>
<span class="line"><span class="prompt">neon&nbsp;&gt;</span> wave hello and tell me what you see</span>
<span class="line"><span class="out">  → g1_arm_action('high wave')</span>     <span class="ok">rc=0, released</span></span>
<span class="line"><span class="out">  → take_photo('what do you see?')</span> <span class="ok">640x480 via the dashboard</span></span>
<span class="line"><span class="prompt">neon&nbsp;&gt;</span> Waved. A person at a desk, laptop open.<span class="cur"></span></span>
</div>

<figure class="shot sr-wide" markdown>
<a href="guide/dashboard/"><img src="assets/dashboard/cockpit.png" alt="The neon cockpit: live RealSense colour feed in the centre, controller, posture and telemetry cards around it, the agent composer along the bottom" loading="lazy"></a>
<figcaption>the cockpit at neon.cagatay.my, live from the G1 (camera frame blurred for the people in the office). <a href="guide/dashboard/">more views</a></figcaption>
</figure>

---

<div class="tour" markdown>

## the whole thing in 3 minutes

<div class="tour-step" markdown>
<span class="tour-num">1</span>
**What it is.** A [Strands](https://github.com/strands-agents) agent that drives a
**Unitree G1+** humanoid. It runs **on the robot's Jetson**, speaks DDS to the
motor bus, and turns the SDK into {{facts:all_tools}} typed, safety-gated tools.
The model is remote (Bedrock or OpenAI Realtime); everything that touches a
motor is local.
</div>

<div class="tour-step" markdown>
<span class="tour-num">2</span>
**How you talk to it.** One agent, many faces: the chest speaker, Telegram, the
[cockpit](guide/dashboard.md) in a browser or on your phone, the REPL over SSH,
and a thinker that reflects every 30 s. Say `wave`, `walk forward 30cm`,
`what do you see?`, `map this room`. Every persona shares one memory and one log.
</div>

<div class="tour-step" markdown>
<span class="tour-num">3</span>
**Why it's safe.** Walking is FSM-gated and only on your explicit request; the
arm bus is mutex-locked; raw motor publishes need `unsafe=True`. After a walk
the robot measures its own displacement and says "done" only when it moved.
</div>

[Start the tour →](start/quickstart.md){ .md-button .md-button--primary }

</div>

```mermaid
flowchart LR
  U(["you"]) -->|"voice, telegram, cockpit, REPL"| A

  subgraph G1["Unitree G1+, Jetson Orin"]
    direction TB
    A["neon<br/>strands, {{facts:all_tools}} robot tools"]
    C["MCU controllers"]
    M["motors, arms<br/>audio, LEDs"]
    S[("lidar, imu<br/>bms, cameras")]
    A -->|"DDS"| C --> M
    A -.->|"read-only"| S
  end

  classDef brain stroke:#007a3d,stroke-width:1.5px
  classDef io stroke:#666464,stroke-width:1.5px
  class A brain
  class C,M,S io
```

## at a glance

| | |
|---|---|
| **robot** | Unitree G1+, 29 motors, 2 arms, Livox MID-360 lidar, RealSense + Brio cameras |
| **compute** | Jetson Orin NX, the agent runs on the robot |
| **transport** | CycloneDDS over `eth0` (no ROS) |
| **tools** | {{facts:all_tools}} robot tools + {{facts:lookout_tools}} cross-persona tools, counted from the code at build time |
| **model** | any Bedrock model id (`NEON_MODEL_ID`, default `{{facts:default_model}}`), changed live from the cockpit; voice on OpenAI Realtime, Nova Sonic or Gemini Live |
| **surfaces** | voice (chest speaker), Telegram, cockpit (browser, phone, tiny app), REPL, thinker |
| **safety** | [eight gates](guide/safety.md): allowlist, prompt, tool class, FSM, arm mutex, clamps, request + look, measured motion |

## the recommended path

<div class="grid cards" markdown>

-   :material-numeric-1-circle:{ .lg } **[Run it](start/quickstart.md)**: clone, add a Bedrock key, wave hello. 60 s.

-   :material-numeric-2-circle:{ .lg } **[See it](showcase/gestures.md)**: gestures, a measured walk, a Telegram message end to end.

-   :material-numeric-3-circle:{ .lg } **[Do it](recipes/index.md)**: recipes you can say: wave, walk, look, map, text.

-   :material-numeric-4-circle:{ .lg } **[Operate it](guide/dashboard.md)**: the cockpit, voice snooze, model switch, passkeys.

-   :material-numeric-5-circle:{ .lg } **[Dig in](tools/catalog.md)**: {{facts:all_tools}} tools, `use_unitree`, `use_dds`, the eight gates, the runtime.

</div>

---

<p style="text-align:center; color:var(--muted); font-size:0.85em; margin-top:2rem;">
  <span class="neon-dot"></span>
  built with <a href="https://github.com/strands-agents">strands-agents</a>,
  <a href="https://github.com/cagataycali/devduck">devduck</a>,
  <a href="https://github.com/unitreerobotics/unitree_sdk2_python">unitree_sdk2</a>
</p>
