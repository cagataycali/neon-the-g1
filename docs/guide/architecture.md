# architecture

<span class="read-badge">90s</span>

How `neon` fits inside the G1's runtime.

## physical layout

```
Unitree G1+
├─ Jetson Orin NX  (192.168.123.164)  runs neon
│    docker compose: neon-agent, neon-dashboard (camera owner), neon-telegram, neon-thinker
│    host units: neon-voice (mic + chest speaker), neon-ctl (restarts for the dashboard)
│    unitree_sdk2_python/ local clone; eth0, CycloneDDS multicast
▼
└─ Main MCU        (192.168.123.161)  Unitree-owned, never touched
     master_service: sport_mode, loco_service, arm_action, vui/audio
     SPI to 29 motors, 2 arms, audio, LEDs, Livox lidar
```

neon and `master_service` do not know about each other: a persona crash leaves
motor control untouched, a `sport_mode` crash leaves neon running. Addresses and
ports on [network](../reference/network.md), the units on [systemd](../start/systemd.md).

## the agent loop

```mermaid
flowchart LR
  M(["message"]) --> I["inject live state<br/>(fsm, imu, battery, slam)"]
  I --> P["model plans tool calls"]
  P --> T["execute, parallel when independent<br/>every call logged"]
  T --> R["reply"]
  R --> U(["user"])
  classDef a stroke:#007a3d,stroke-width:1.5px
  class I,P,T,R a
```

Every turn **injects live robot state** into the system prompt: the model wakes
up knowing FSM, IMU, battery and SLAM pose from in-memory DDS buffers (under
50 ms). Every tool call is wrapped by `tools/tool_log.py` and lands in
`agent_log`, which the next turn of every persona reads.

## four tool families

```mermaid
flowchart LR
  A(["agent"]) --> C["composed<br/>safety-gated"]
  A --> U["use_unitree<br/>universal SDK"]
  A --> D["use_dds<br/>raw pub/sub"]
  A --> S["sensing<br/>cameras, mic, lidar, slam"]
  C --> SDK["SDK clients"] --> DDS["CycloneDDS"]
  U --> SDK
  D --> DDS
  DDS --> MCU["MCU"]
  S --> V["dashboard snapshot, ALSA, Livox"]
  classDef safe stroke:#007a3d,stroke-width:1.5px
  classDef raw stroke:#946e00,stroke-width:1.5px
  class C safe
  class D raw
```

Composed tools are hand-written and gated (FSM, mutex, clamps, measured
motion); `use_unitree` is AST-verified dispatch over the whole SDK; `use_dds`
is the raw escape hatch; sensing lives outside the SDK (the dashboard's camera
snapshot, the USB mic, kiss-icp on the lidar). Every tool on
[the catalog](../tools/catalog.md).

## one agent, several personas

```mermaid
flowchart TB
  MEM[("shared memory<br/>kv, agent_log, voice bridge")]
  VC["voice<br/>mic + chest speaker"] --- MEM
  TG["telegram<br/>listener"] --- MEM
  DB["dashboard<br/>cockpit chat"] --- MEM
  TH["thinker<br/>30 s heartbeat"] --- MEM
  SH["shell<br/>REPL"] --- MEM
  DP["dispatch<br/>sub-agents"] --- MEM
  classDef p stroke:#007a3d,stroke-width:1.5px
  class VC,TG,DB,TH,SH,DP p
```

All of them share the tools **and the memory**: what one persona did (and every
tool it called) is in the log the others read on their next turn, so voice
picks up where Telegram left off. Who carries which tools is on the
[catalog](../tools/catalog.md#who-sees-what); the voice loop on
[voice architecture](../voice-architecture.md).

## why DDS not ROS

The MCU speaks CycloneDDS natively. ROS would add a bridge subprocess, an extra
serialization hop, and middleware we don't control. Raw CycloneDDS + the SDK's
typed wrappers is simpler, faster, and matches Unitree's own tools.

[catalog](../tools/catalog.md){ .md-button } [systemd](../start/systemd.md){ .md-button }
