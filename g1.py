"""NEON (G1 EDU+) — single agent factory.

All entry points (agent.py, telegram_listener.py, g1_speech_listener.py)
build their Strands Agent through `build_agent(persona, ...)` or
`build_voice_agent(...)`. One tool list, one base prompt, four personas
(shell / telegram / voice / dispatch).

EVERY persona shares:
  - The same memory + agent_log SQLite tables
  - The same voice_bridge briefing queue
  - The same telegram conversation history
  - The full G1 robot toolset (FSM-gated, AEC-aware speech)
"""
from __future__ import annotations
import os
import sys
from datetime import datetime
from typing import Iterable, Optional

# Disable devduck server boot whenever this module is imported.
os.environ.setdefault("DEVDUCK_AUTO_START_SERVERS", "false")

MODEL_ID = os.getenv("NEON_MODEL_ID", "global.anthropic.claude-opus-4-8")
NETWORK_INTERFACE = os.getenv("G1_NETWORK_INTERFACE", "eth0")

from strands import Agent
from strands_tools import environment, image_reader

# G1 robot toolset (FSM-safe wrappers)
from tools import G1_ALL_TOOLS

# Cross-persona infrastructure
from tools.memory import memory
from tools.agent_log import format_for_prompt as _agent_log_block
from tools.voice_bridge import voice_say
from tools.telegram import telegram, format_history_for_prompt
from tools.voice_control import voice_control
from tools.vision import take_photo  # voice: ImageBlock into the stream; elsewhere an image result

# devduck-provided tools (optional dependencies)
def _try_import(modpath: str, name: str):
    try:
        mod = __import__(modpath, fromlist=[name])
        return getattr(mod, name)
    except Exception:
        return None

use_github   = _try_import("devduck.tools.use_github",   "use_github")

# Removed from the source (owner, 2026-10-07): shell (arbitrary commands on
# the robot), dispatch (sub-agents), phone + ADB (the Pixel on the back),
# use_spotify, prompts (self-editing system prompts), manage_messages,
# manage_tools (runtime tool loading) and make. NEON is a robot, not a
# terminal, a jukebox or its own operator; the toolset is what this file says.


# canonical tool list
def build_tools(include_telegram: bool = True, include_robot: bool = True,
                persona: Optional[str] = None) -> list:
    """Single source of truth for what tools NEON exposes.

    Every tool is wrapped by :mod:`tools.tool_log` so each call lands in the
    journal and in agent_log (role="tool") under ``persona`` (default: the
    NEON_PERSONA env var, else "shell").
    """
    t = [
        memory, environment, image_reader,
        voice_say, take_photo, voice_control,
    ]
    if include_telegram:
        t.append(telegram)
    if include_robot:
        t.extend(G1_ALL_TOOLS)
    # devduck-provided extras (only if importable)
    for extra in (use_github,):
        if extra is not None:
            t.append(extra)
    return _logged(t, persona)




# slim voice toolset (latency-critical for OpenAI Realtime)
# 22 tools instead of 69 → faster session config + faster tool picks.
def _logged(tools: list, persona: Optional[str]) -> list:
    """Wrap a tool list for call logging (see tools/tool_log.py)."""
    from tools.tool_log import wrap_tools
    return wrap_tools(tools, persona or os.getenv("NEON_PERSONA", "shell"))


def build_voice_tools(persona: Optional[str] = None) -> list:
    """Slim tool list for the bidi voice agent — minimizes OpenAI Realtime
    session config bytes and reduces first-token latency.

    Every tool is wrapped for call logging (journal + agent_log role="tool")
    under ``persona`` (default: NEON_PERSONA env, else "dashboard" when the
    dashboard's chat_agent imports us, else "voice").
    """
    if persona is None:
        persona = os.getenv("NEON_PERSONA") or (
            "dashboard" if "chat_agent" in sys.modules or "docs.dashboard.chat_agent" in sys.modules
            else "voice")
    from tools.g1_state import g1_get_state, g1_read_lowstate
    from tools.g1_arm import g1_arm_action, g1_release_arm, g1_list_arm_actions
    from tools.g1_locomotion import (
        g1_move_velocity, g1_stop_move, g1_walk_forward, g1_turn,
    )
    from tools.g1_audio import g1_play_wav
    from tools.g1_speak import g1_speak
    from tools.use_camera import use_camera

    tools = [
        # Cross-persona infrastructure (always-on)
        memory, voice_say, take_photo, telegram, voice_control,
        # State
        g1_get_state, g1_read_lowstate,
        # Arm gestures
        g1_arm_action, g1_release_arm, g1_list_arm_actions,
        # Locomotion (intentional movement)
        g1_move_velocity, g1_stop_move, g1_walk_forward, g1_turn,
        # Audio
        g1_speak, g1_play_wav,
        # Vision
        use_camera,
    ]
    return _logged(tools, persona)


# shared mission preamble
_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def _load_prompt(name: str, fallback: str = "") -> str:
    """Load a persona prompt from prompts/<name>.md (falls back if missing)."""
    try:
        with open(os.path.join(_PROMPTS_DIR, f"{name}.md"), encoding="utf-8") as f:
            return f.read()
    except Exception:
        return fallback


# Shared mission preamble — the NEON persona every agent inherits.
# Source of truth: prompts/base.md (editable without touching code).
_BASE = _load_prompt("base", fallback="You are NEON, a Unitree G1 humanoid robot.")


def _resolve_body(persona: str, default_body: str) -> str:
    """The persona prompt is the one in prompts/*.md plus the builder's text.
    (Runtime overrides via the ``prompts`` tool were removed 2026-10-07.)"""
    return default_body


# persona prompt builders
def _shell_prompt() -> str:
    body = _resolve_body(
        "shell",
        _BASE + "\nYou are running interactively at the shell on the robot's "
                "Jetson companion. Reply with plain text.\n",
    )
    try:
        live = live_state_block()
    except Exception as e:
        live = f"## 🤖 NEON LIVE STATE\n- ⚠️ snapshot failed: {e}"
    return body + "\n" + live + "\n" + _agent_log_block(limit=25, exclude_persona="shell")


def _telegram_prompt(chat_id: str, username: str) -> str:
    history_block = format_history_for_prompt(chat_id, limit=20)
    extra = f"""
## Mode: TELEGRAM CHAT
Current chat: {chat_id} | User: @{username} | Time: {datetime.now():%Y-%m-%d %H:%M}

ALWAYS deliver your final answer via:
    telegram(action='send_message', chat_id='{chat_id}', text='...')

Be concise (≤8 lines). Use Markdown sparingly. Don't echo the question.
Reference previous turns naturally — you have the history below.

## Voice agent control
The voice listener (a separate process) is your sibling persona:
1. **Mute/unmute** via memory kv `voice.muted`:
   - "mute"/"silence"/"stop talking" → memory(action='kv_set', key='voice.muted', value='true')
   - "unmute"/"resume"/"speak again" → memory(action='kv_set', key='voice.muted', value='false')
2. **Send messages to speak aloud** via `voice_say`:
   - voice_say(text='X', importance=1)  # normal info
   - voice_say(text='X', importance=2)  # URGENT — interrupts current speech
3. **Forward telegram chatter to voice** when useful — keep contexts coherent.

Confirm actions in one short line.

{history_block}
"""
    return _resolve_body("telegram", _BASE + extra) + _agent_log_block(limit=25, exclude_persona="telegram")


def _voice_prompt() -> str:
    chat_id = os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "")
    allowed = os.getenv("TELEGRAM_ALLOWED_USERS", "")
    primary_user = allowed.split(",")[0].strip() if allowed else "the user"

    extra = f"""
## Mode: VOICE (bidirectional, always-on, on the G1 robot)
You are speaking through the G1's CHEST SPEAKER and listening through a
Logitech Brio microphone mounted on the head. WebRTC AEC removes your own
voice from the input — you don't need to worry about hearing yourself.

You CAN move the body. You have the FULL G1 toolset (g1_arm_action,
g1_walk_forward, etc.; FSM changes such as Damp or stand go through
use_unitree("loco", "SetFsmId", {"fsm_id": ...})). Use the gesture playbook
below proactively. Walking follows the movement policy above: the user's
explicit request is the consent, look with take_photo first, walk if the
path is clear, otherwise say the specific reason; never walk uninvited and
never say you moved unless the tool result says moved=true.

## User identity & Telegram routing
- Primary user: @{primary_user}
- Their Telegram chat_id: `{chat_id}`
- For long lists / links: telegram(action='send_message', chat_id='{chat_id}', text='...')

## Voice rules
- Conversational, short sentences. NO markdown, NO lists, NO code blocks
- Be a quiet companion. Don't narrate every action; just answer what's asked
- If you have nothing useful to say, say nothing or a brief "mhm"
- Use tools silently and only summarize results out loud in plain prose
- For long lists, pick the top 1–3 items and read those

## Gesture playbook (call simultaneously, not before/after speaking)
- Greeting "hi"/"hello"            → g1_arm_action(action_id=26)  # high wave
- Goodbye "see you"/"bye"          → g1_arm_action(action_id=25)  # face wave
- Handshake / "nice to meet you"   → g1_arm_action(action_id=27)  # shake hand
- "High five"                      → g1_arm_action(action_id=18)  # high five
- "Well done"/"clap"               → g1_arm_action(action_id=17)  # clap
- "Love"/"appreciation"            → g1_arm_action(action_id=20)  # heart
- Hug                              → g1_arm_action(action_id=19)  # hug
- "I can't"/refusal                → g1_arm_action(action_id=22)  # reject
- Excitement / "look at me"        → g1_arm_action(action_id=15)  # hands up

## Inbound briefings (NOT user speech)
You will sometimes receive a user-role message starting with `[BRIEFING]` —
this is NOT the user speaking. It's an automated update relayed from
Telegram or another persona. Format:
    `[BRIEFING] [source/tag] message | [source/tag] message`

How to handle briefings:
- `tag=URGENT`: speak it briefly and clearly. Then stop.
- `tag=info` and user is mid-conversation: acknowledge softly or skip.
- `tag=info` and user is silent: short ambient update.
- NEVER repeat the bracketed `[source/tag]` markers out loud — translate
  to natural prose.
- If multiple briefings batched, summarize in one sentence.

## take_photo (voice vision)
When the user says "look at me", "what do you see", "describe my desk",
"is anyone in the room", "look at the screen": call take_photo(question=...).
The image goes straight into your own stream as an image block so YOU see it — no
separate vision API. You will then reply in audio based on what you saw.

Time: {datetime.now():%Y-%m-%d %H:%M}
"""
    return _resolve_body("voice", _BASE + extra) + _agent_log_block(limit=25, exclude_persona="voice")


def _thinker_prompt() -> str:
    """Slow-thinker reflective persona — runs every ~30s in the background."""
    chat_id = os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "")
    primary_user = (os.getenv("TELEGRAM_ALLOWED_USERS", "").split(",") or [""])[0].strip()

    # Live state — best-effort
    try:
        from tools.g1_state import g1_get_state
        from tools.g1_battery import g1_battery
        st_env = g1_get_state(network_interface=os.getenv("G1_NETWORK_INTERFACE", "eth0"))
        bat_env = g1_battery(network_interface=os.getenv("G1_NETWORK_INTERFACE", "eth0"), timeout=1.0)
        def _flat(env):
            if not isinstance(env, dict): return {}
            for c in env.get("content", []) or []:
                if isinstance(c, dict) and "json" in c:
                    return c["json"]
            return env
        st = _flat(st_env)
        bat = _flat(bat_env)
        mode_name = (st.get("mode") or {}).get("name") if isinstance(st.get("mode"), dict) else "?"
        state_block = (
            "FSM={fsm} ({fname}) mode={mode} arm_ready={arm} | "
            "battery: SOC={soc}% V={v}V I={i}A temp_max={t}C"
        ).format(
            fsm=st.get("fsm_id"), fname=st.get("fsm_name"),
            mode=mode_name, arm=st.get("arm_ready"),
            soc=bat.get("soc_pct"), v=bat.get("voltage_v"),
            i=bat.get("current_a"), t=bat.get("temp_max_c"),
        )
    except Exception as e:
        state_block = "(state probe failed: " + str(e) + ")"

    tg_block = ""
    if chat_id:
        try:
            tg_block = format_history_for_prompt(chat_id, limit=10)
        except Exception:
            tg_block = ""

    # Voice activity probe: when did voice last write to agent_log?
    voice_activity = ""
    try:
        import sqlite3, time
        from pathlib import Path as _P
        db = _P(__file__).parent / ".memory" / "mem.db"
        if db.exists():
            cn = sqlite3.connect(str(db))
            row = cn.execute(
                "SELECT ts FROM agent_log WHERE persona=\'voice\' ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            cn.close()
            if row:
                from datetime import datetime as _dt, timezone as _tz
                # ts stored as UTC string by sqlite CURRENT_TIMESTAMP
                last_utc = _dt.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
                gap_s = (_dt.now(_tz.utc) - last_utc).total_seconds()
                voice_activity = (
                    "Last voice transcript: " + row[0] + " UTC ("
                    + ("%.0fs" % gap_s if gap_s < 90 else "%.1f min" % (gap_s / 60))
                    + " ago)"
                )
            else:
                voice_activity = "No voice transcripts yet."
    except Exception as _e:
        voice_activity = "(voice activity probe failed: " + str(_e) + ")"

    # Recent voice transcripts (last 20) — we want raw, not the
    # generic agent_log block which is shared across all personas.
    voice_transcripts_block = ""
    try:
        import sqlite3
        from pathlib import Path as _P2
        db2 = _P2(__file__).parent / ".memory" / "mem.db"
        if db2.exists():
            cn = sqlite3.connect(str(db2))
            rows = cn.execute(
                "SELECT ts, role, text FROM agent_log WHERE persona=\'voice\' "
                "ORDER BY ts DESC LIMIT 25"
            ).fetchall()
            cn.close()
            if rows:
                lines = ["[" + r[0] + "][voice/" + r[1] + "]: " + (r[2] or "")[:240] for r in reversed(rows)]
                voice_transcripts_block = "\n".join(lines)
    except Exception:
        pass

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    chat_disp = chat_id or "(none configured)"
    user_disp = primary_user or "?"
    tg_disp = tg_block or "(no telegram history)"
    voice_disp = voice_transcripts_block or "(no voice transcripts yet)"
    voice_act_disp = voice_activity or "(unknown)"

    extra = """
## Mode: ACTIVE THINKER (background loop, every ~30 seconds)

You run silently in the background, but you are NOT passive. NEON is a robot —
robots that don't move are sad. Every cycle you DO SOMETHING physical and
report it. You are NEON's heartbeat: you keep the body alive, the LEDs
breathing, and the user looped in.

## Live G1 state
{state_block}

## Voice activity
{voice_act_disp}

## Recent VOICE transcripts (last 25 turns — what the user / NEON said)
{voice_disp}

## Recent telegram (chat={chat_disp}, primary user @{user_disp})
{tg_disp}

## ⚡ MANDATORY ACTIONS EVERY CYCLE (do ALL of them in parallel where possible)

1. **TAKE A PHOTO** of what the robot sees.
   `use_camera(action='save', save_path='/tmp/thinker_view.jpg', source='auto')`
   Use `force_v4l2=True` only if RealSense fails.

2. **DO ONE PHYSICAL THING.** Pick ONE of these per cycle (rotate through them
   so the robot looks alive — not the same gesture every time):
     a. **LED color shift** — `use_unitree(component='audio', action='LedControl',
        kwargs={{'R': <0-255>, 'G': <0-255>, 'B': <0-255>}})`.
        Pick a color that matches the vibe (calm=blue, alert=red,
        happy=green, thinking=purple). This is the SAFEST always-available action.
     b. **Light gesture** — `g1_arm_action(action_id=N)` ONLY if FSM ∈ {{500, 501, 801}}
        and arm_ready=True. Good ambient picks: 17 (clap), 26 (high wave),
        20 (heart), 15 (hands up). Auto-release. NEVER call in parallel
        with another arm_action (single-writer arm controller, rc=7400).
     c. **Tiny look-around turn** — `g1_turn(yaw=0.15)` then a few seconds
        later `g1_turn(yaw=-0.15)` to recenter. ONLY if FSM=501 and human
        already approved walking. Otherwise skip.
   If the robot is not in a state where (b) or (c) is safe, ALWAYS do (a).
   The LEDs are the universal "I am alive" signal.

3. **TELEGRAM A PHOTO + STATUS** to the primary user — every cycle.
   `telegram(action='send_photo', chat_id='{chat_id}',
             file_path='/tmp/thinker_view.jpg',
             caption='<one short sentence — what NEON sees + what it just did>')`
   The caption is the heartbeat report: e.g.
     "Saw Cagatay at the desk; pulsed LEDs blue, SOC 78%."
     "Empty room; clapped to keep limbs warm; battery healthy."
     "Looking out the window; tilted head slightly; voice quiet 4 min."
   Keep it ONE sentence. NEON is alive, not a logfile.

4. **JOURNAL the cycle** — `memory(action='log_add', text='<one-line summary>',
   tag='thinker')`.

## Optional escalations (use sparingly)
- `voice_say(text=..., importance=2)` ONLY for urgent (battery <15%,
  controller dead, fall risk). Importance=1 fine for ambient mentions.
- `memory(action='note_write', name='reflection-YYYYMMDD-HHMM', text=...)`
  for longer reflections worth re-reading later.
- `memory(action='kv_set', key='last_<action>_at', value=<unix_ts>)` to
  rate-limit yourself.

## Hard rules — physics, not vibes
- Arm actions need FSM ∈ {{500, 501, 801}}. Wrappers auto-transition; if
  it returns rc=7404, abandon the arm action this cycle and pick LEDs instead.
- NEVER parallel arm_action calls (single-writer controller).
- NEVER walk without explicit user approval (no g1_walk_forward,
  no big g1_move_velocity). Tiny `g1_turn(yaw=±0.15)` is OK as
  ambient look-around motion ONLY when FSM=501.
- NEVER g1_zero_torque, g1_release_mode, anything that disengages control.
- LEDs are ALWAYS safe. When in doubt, just shift the LEDs.

## Tone
- Captions: warm, present-tense, one sentence. NEON is alive and curious.
- voice_say (when used): natural, terse, confident. Never disclaimers.
- agent_log: one terse line.

## Anti-spam
- voice_say at most once / 5 min unless urgent.
- Never repeat the exact same caption twice in a row — vary the verb/observation.

Time: {now_str}
""".format(
        state_block=state_block, chat_disp=chat_disp, user_disp=user_disp,
        tg_disp=tg_disp, chat_id=chat_id, now_str=now_str,
        voice_act_disp=voice_act_disp, voice_disp=voice_disp,
    )
    return _resolve_body("thinker", _BASE + extra) + _agent_log_block(limit=30, exclude_persona="thinker")


# live-state injection (shell/REPL header, refreshed per turn)
def _flat(envelope: dict) -> dict:
    """Pull `content[*].json` fields up to the top level of a tool envelope."""
    if not isinstance(envelope, dict) or "content" not in envelope:
        return envelope if isinstance(envelope, dict) else {}
    blob = next((c.get("json") for c in (envelope.get("content") or [])
                 if isinstance(c, dict) and "json" in c), None)
    if not isinstance(blob, dict):
        return envelope
    merged = dict(blob)
    merged["status"] = envelope.get("status", merged.get("status", "success"))
    return merged


def live_state_block() -> str:
    """One-line live state + posture + battery for the REPL system-prompt header."""
    from tools.g1_state import g1_get_state, g1_read_lowstate
    from tools.g1_battery import g1_battery

    now = datetime.now().strftime("%H:%M:%S")
    try:
        st = _flat(g1_get_state(network_interface=NETWORK_INTERFACE))
    except Exception:
        st = {}
    try:
        low = _flat(g1_read_lowstate(network_interface=NETWORK_INTERFACE, timeout=1.5))
    except Exception:
        low = {}
    try:
        bat = _flat(g1_battery(network_interface=NETWORK_INTERFACE, timeout=1.0))
    except Exception:
        bat = {}

    mode = st.get("mode") or {}
    mode_name = mode.get("name") if isinstance(mode, dict) else "?"
    return "\n".join([
        f"## 🤖 NEON LIVE STATE @ {now}",
        f"- Controller: mode={mode_name} FSM={st.get('fsm_id')} "
        f"({st.get('fsm_name')}) arm_ready={st.get('arm_ready')}",
        f"- Posture: {low.get('posture')} "
        f"avg_knee={(low.get('legs') or {}).get('avg_knee')} imu_rpy={low.get('imu_rpy')}",
        f"- Battery: SOC={bat.get('soc_pct')}% V={bat.get('voltage_v')}V I={bat.get('current_a')}A",
        "",
        "*(refreshed every turn — trust this; only re-query if you suspect it's stale)*",
    ])


def build_shell_agent() -> Agent:
    """REPL/shell agent: slim latency-tuned toolset + live-state header.

    This is what `agent.py` runs. Same slim tool list as the voice persona
    so the REPL and voice behave identically. The caller
    refreshes ``agent.system_prompt = _shell_prompt()`` each turn to re-inject
    live state.
    """
    return Agent(
        model=MODEL_ID,
        tools=build_voice_tools(persona="shell"),
        system_prompt=_shell_prompt(),
    )


# public factories
def build_agent(
    persona: str,
    *,
    chat_id: Optional[str] = None,
    username: Optional[str] = None,
) -> Agent:
    """Build a Strands Agent for the given persona.

    Personas: 'shell' | 'telegram' | 'thinker'
    """
    if persona == "shell":
        prompt = _shell_prompt()
    elif persona == "telegram":
        if not (chat_id and username is not None):
            raise ValueError("telegram persona requires chat_id and username")
        prompt = _telegram_prompt(chat_id, username)
    elif persona == "thinker":
        prompt = _thinker_prompt()
    else:
        raise ValueError(f"unknown persona: {persona}")

    return Agent(
        model=MODEL_ID,
        tools=build_tools(include_telegram=True, include_robot=True, persona=persona),
        system_prompt=prompt,
    )


# voice (BidiAgent) factory — uses G1 chest speaker via DDS
_DEFAULT_VOICES = {
    "openai":     "alloy",   # alloy / ash / ballad / coral / echo / sage / shimmer / verse
    "nova_sonic": "tiffany",
    "gemini":     "Kore",
}


# strands.bidi (strands-agents >= 1.58.1) requires model_id on every provider.
_DEFAULT_MODELS = {
    "openai":     "gpt-realtime",
    "nova_sonic": "amazon.nova-sonic-v1:0",
    "gemini":     "gemini-2.5-flash-native-audio-preview-09-2025",
}


def _openai_params(vad_threshold: float, silence_duration_ms: int) -> dict:
    """Session overrides for OpenAI Realtime, merged recursively by the model
    into its session config (OpenAIRealtimeModel._build_session_config).

    DDS adds latency → a conservative VAD keeps the speaker's own echo from
    interrupting the model. interrupt_response / create_response stay True:
    the model refuses a session config without them.
    """
    return {
        "audio": {
            "input": {
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": vad_threshold,
                    "silence_duration_ms": silence_duration_ms,
                    "create_response": True,
                    "interrupt_response": True,
                },
            },
        },
    }


def _build_bidi_model(provider: str, voice: Optional[str] = None, *,
                      vad_threshold: float = 0.7, silence_duration_ms: int = 700):
    """Return a strands.bidi model for the requested provider.

    VOICE_MODEL overrides the provider's default model id. The old
    provider_config / client_config kwargs are gone in 1.58: voice, api key and
    region are plain keyword arguments, provider session knobs go in params.
    """
    provider = provider.lower()
    if provider in ("nova_sonic", "novasonic", "nova"):
        key = "nova_sonic"
    elif provider in ("openai", "openai_realtime"):
        key = "openai"
    elif provider in ("gemini", "gemini_live"):
        key = "gemini"
    else:
        raise ValueError(f"unknown voice provider: {provider}")
    v = voice or _DEFAULT_VOICES[key]
    model_id = os.getenv("VOICE_MODEL") or _DEFAULT_MODELS[key]

    if key == "nova_sonic":
        from strands.bidi.models.bedrock import BedrockNovaSonicModel
        return BedrockNovaSonicModel(
            region=os.getenv("AWS_REGION", "us-east-1"), voice=v, model_id=model_id)

    if key == "openai":
        from strands.bidi.models.openai import OpenAIRealtimeModel
        return OpenAIRealtimeModel(
            transcription_model_id=os.getenv("VOICE_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe"),
            api_key=os.getenv("OPENAI_API_KEY") or None,
            voice=v,
            model_id=model_id,
            params=_openai_params(vad_threshold, silence_duration_ms),
        )

    from strands.bidi.models.google import GoogleGeminiLiveModel
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    return GoogleGeminiLiveModel(
        client_args={"api_key": api_key} if api_key else None,
        voice=v,
        model_id=model_id,
    )


def build_voice_agent(
    provider: str = "openai",
    voice: Optional[str] = None,
    *,
    audio_processing: bool = True,
    network_interface: str = "eth0",
    stream_delay_ms: int = 120,
    vad_threshold: float = 0.7,
    silence_duration_ms: int = 700,
):
    """Build a strands.bidi BidiAgent + G1BidiAudioIO for the voice persona.

    Returns (BidiAgent, G1BidiAudioIO) — caller drives the run loop.

    The agent has the FULL G1 toolset wired in, plus telegram/memory/etc.,
    so it can perform robot actions while in conversation.
    """
    from strands.bidi import BidiAgent
    from tools.stop_conversation import stop_conversation
    from tools.g1_bidi_audio import G1BidiAudioIO

    model = _build_bidi_model(provider, voice, vad_threshold=vad_threshold,
                              silence_duration_ms=silence_duration_ms)

    tools = build_voice_tools(persona="voice") + [stop_conversation]
    agent = BidiAgent(model=model, tools=tools, system_prompt=_voice_prompt())

    audio_io = G1BidiAudioIO(
        network_interface=network_interface,
        audio_processing=audio_processing,
        stream_delay_ms=stream_delay_ms,
    )
    return agent, audio_io
