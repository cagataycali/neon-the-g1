# use_dds

<span class="read-badge">60s, escape hatch</span>

Raw DDS on any topic, for monitoring, debugging, or publishing what the SDK
doesn't wrap.

| tool | what |
|---|---|
| `g1_dds_list_topics` | curated G1 topic catalog |
| `g1_dds_discover(timeout=5.0)` | full live topic set (no arg) |
| `g1_dds_snapshot(topic)` | read ONE sample |
| `g1_dds_subscribe(topic, max_buffer=20)` | buffered subscription |
| `g1_dds_read(topic, n=10)` | pop latest N |
| `g1_dds_unsubscribe(topic)` | stop |
| `g1_dds_stats()` | active subs/pubs + counts |
| `g1_dds_publish(topic, payload, unsafe=True)` | raw publish |

## read flow

<div class="terminal" markdown>
<span class="p">&gt;</span> list all DDS topics
<span class="ok">rt/lowstate, rt/bmsstate, rt/armsdk, rt/lowcmd, ...</span>
<span class="p">&gt;</span> snapshot rt/lowstate
<span class="ok">{ timestamp, imu_state, motor_state[29] }</span>
<span class="p">&gt;</span> subscribe rt/bmsstate, read last 3
<span class="ok">[{soc:86, current:-2.1}, {...}, {...}]</span>
</div>

## publishing, the dangerous part

`rt/*cmd` topics are single-writer, owned by the motor controller. Publishing
without coordination WILL cause motor failures.

```python
# safe topic — no flag:
g1_dds_publish(topic="rt/utlidar/switch", payload={"data": "ON"})
# dangerous topic — REQUIRES unsafe=True:
g1_dds_publish(topic="rt/armsdk", payload={...}, unsafe=True)
```

`unsafe=True` required for: `rt/lowcmd`, `rt/user_lowcmd`, `rt/armsdk` ·
`rt/bmscmd`, `rt/inspire/cmd`. The flag forces explicit intent, the LLM can't
publish to these "by accident".

## when to reach for this

**For:** inspecting a topic the SDK does not surface, prototyping before a
composed tool, debugging why an RPC is not reaching the MCU.

**Not for:** bypassing safety, publishing motor commands by hand, anything
`use_unitree` already covers.

See [DDS topics](../reference/dds-topics.md) for the full schema list.
