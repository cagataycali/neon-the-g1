# quickstart

<span class="read-badge">60s to first wave</span>

## 1 / clone & run

```bash
git clone https://github.com/cagataycali/neon-the-g1.git
cd neon-the-g1
make run
```

`make run` builds the Docker image (if needed), brings up the compose stack,
and attaches you to the `agent.py` REPL — auto-starting bidirectional voice
(`g1_speak`). For a bare-metal venv instead (DEV only), use `make run-bare`,
which clones the Unitree SDK (the pip wheel is broken) and exports
`CYCLONEDDS_URI` + `PYTHONPATH`.

## 2 / pick a model

NEON runs on a Bedrock model. Provide Bedrock credentials before `make run`;
the model id can be changed later from the dashboard (Configuration, model)
without editing files:

```bash
export AWS_BEARER_TOKEN_BEDROCK=...        # Bedrock bearer token (preferred)
export AWS_DEFAULT_REGION=us-west-2
export NEON_MODEL_ID={{facts:default_model}}   # the default; any Bedrock model id works
```

## 3 / talk to it

<div class="terminal" markdown>
<span class="p">&gt;</span> check state

<span class="ok">mode=ai · fsm=500 · arm_ready=True · soc=86%</span>

<span class="p">&gt;</span> wave hello

<span class="ok">g1_arm_action('high wave') rc=0 · released</span>
</div>

You want `arm_ready=True` and `fsm ∈ {500, 501, 801}`. If `arm_ready=False`,
say `set fsm 500` first — the arm won't move from Damp.

## enable control (arm the G1)

Before the robot can walk, switch it to **walk/control mode** in the Unitree
app. This hands motor control to neon over DDS.

<video controls muted playsinline loop width="100%" style="border-radius:8px;max-width:720px;">
  <source src="../../assets/how-to-arm-the-g1.mp4" type="video/mp4">
</video>

## safe first commands

!!! tip "Green zone"
    `check state` · `battery` · `wave` · `shake hand` · `heart` · `clap` ·
    `hug` · `stand up` · `sit down` · `damp` · `set stand height 0.75`

!!! danger "Walking is opt-in"
    `walk forward 50cm` needs FSM 501 **and** your explicit yes. Tools refuse
    otherwise. See [safety](../guide/safety.md).

## prerequisites

- Unitree G1+ reachable (`192.168.123.161` MCU · `192.168.123.164` Jetson)
- Interface `eth0` reaches the motor bus
- Python 3.10+ on the Jetson

## next

[docker](docker.md){ .md-button } [systemd](systemd.md){ .md-button } [tool catalog](../tools/catalog.md){ .md-button }
