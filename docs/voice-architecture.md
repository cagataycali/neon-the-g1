# voice architecture

<span class="read-badge">2min</span>

NEON's bidi voice loop is a realtime architecture — with one twist: audio out goes to the **G1 chest speaker via
DDS** (`AudioClient.PlayStream`), not a PortAudio device.

## signal chain

```
Brio mic (16k mono)                    → raw "near"
  → WebRTC AudioProcessor              AEC + NS + AGC · stream_delay_ms=120 ◄ KEY KNOB
  → ratecv 16k→24k                     (Realtime needs ≥24k in)
  → BidiAgent.run()                    ◄ briefings inject BidiTextInputEvent here
       ├ OpenAI / Nova / Gemini Live (websocket)
       └ tools (G1 + telegram + memory + …)
  → 24k assistant audio + transcripts
  → ratecv 24k→16k                     (PlayStream is 16k native)
  → G1SpeakerWriter._loop:
       PlayStream() rc=0 → slice 160-sample frames → feed ref_buf HERE
  → chest speaker emits ~120ms later (DDS roundtrip)
```

!!! warning "Critical timing"
    `ref_buf` is fed **post-PlayStream**, in the writer thread — not at enqueue
    time. The speaker is async-queued, so feeding the AEC reference on enqueue
    would misalign it by a variable amount (queue depth). Feeding it right after
    `PlayStream()` returns makes `stream_delay_ms=120` a *constant* delay. (The
    original port had the variable-delay bug — fixed in cycle 22.)

## why these values

| param | neon | lookout | reason |
|---|:-:|:-:|---|
| `stream_delay_ms` | 120 | 10 | DDS PlayStream adds ~100-150ms vs ~10ms for PortAudio |
| `vad_threshold` | 0.7 | 0.7 | higher = server-VAD less likely to fire on AEC residual |
| `silence_duration_ms` | 700 | 700 | longer turn-end → fewer self-interrupts |
| `ref_buf` maxsize | 500 | ∞ | caps ~5s; drops oldest on overflow (bounded memory) |

## echo troubleshooting

1. **Tune `stream_delay_ms` first.** Time `g1_speak(action="say", text="ping")`.
   Emits ~120ms later → keep 120. Almost instant → drop to 80/60 in 20ms steps.
2. **Verify Brio selected** — `g1_speak(action="debug")` → look for
   `device_index=N rate=16000`. `None` = wrong default mic.
3. **Verify ref_buf fed** — `g1_speak(action="status")`: if `g1_frames_sent`
   climbs but `ref_buf_qsize` stays 0, AEC has no signal to subtract → echo.
4. **Diagnostic bypass** — `VOICE_NO_AEC=1 make voice`. Echo gone → AEC tuning.
   Echo persists → server-VAD triggers on speaker bleed; raise `vad_threshold`
   to 0.9, `silence_duration_ms` to 1000+.
5. **Self-interrupting** (shorter and shorter utterances) → bump `vad_threshold`
   0.7 → 0.85.

## cross-persona briefings

The voice agent also receives async briefings from sibling personas:

```
telegram_listener → voice_bridge.push("telegram", "...")
                  → _BriefingInput poll(2s) → BidiTextInputEvent → model
                  → speaks "From telegram: …" naturally
```

Push manually: `make voice-push MSG="hi neon"` or `voice_say(text=..., importance=1)`.

## mute

`memory.kv_get('voice.muted')` polled at 1Hz. `true` → mic feeds silence (agent
stays quiet but can still speak briefings). Toggle: `make mute` / `make unmute`,
or Telegram `/mute` `/unmute`.

## two output sinks

1. **`_G1SpeakerOutput`** — frames → DDS PlayStream + AEC ref buffer
2. **`_LogOutput`** — final transcripts → `agent_log.record('voice', …)`

So voice transcripts are visible to **all other personas** via the unified log —
NEON stays conversationally coherent across surfaces.

## providers

| provider | env | default voice |
|---|---|---|
| `openai` | `OPENAI_API_KEY` | `alloy` (`VOICE_MODEL=gpt-realtime`) |
| `nova_sonic` | `AWS_BEARER_TOKEN_BEDROCK` / AWS creds | `tiffany` (us-east-1) |
| `gemini` | `GOOGLE_API_KEY` / `GEMINI_API_KEY` | `Kore` |

Switch via `VOICE_PROVIDER=...`. **OpenAI recommended** for G1: lowest-latency
websocket, best at AEC residuals, good chest-speaker quality (`alloy`/`cedar`).
