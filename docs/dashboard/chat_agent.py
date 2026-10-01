"""
🧠 In-dashboard NEON agent — same persona + tools as agent.py, built safely.

We build straight from `g1.py` (the single source of truth for tools +
persona). We use the STATIC base prompt (`g1._BASE`) at construction — no
blocking DDS RPC — and add live-state per-turn from the dashboard's own
cached snapshot (`_live_block_from_cache`), never calling a DDS-blocking
prompt builder.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

log = logging.getLogger("neon-dashboard.chat")

_AGENT = None
_LOCK = threading.Lock()        # serializes ask() (single agent, shared history)
_BUILD_LOCK = threading.Lock()  # guards one-time build
_ERROR: Optional[str] = None
_BUILDING = False
_MODEL_ID: Optional[str] = None  # the model the live agent was built with
_static_prompt = ""


def _build():
    global _AGENT, _ERROR, _BUILDING
    if _AGENT is not None or _ERROR is not None:
        return
    with _BUILD_LOCK:
        if _AGENT is not None or _ERROR is not None or _BUILDING:
            return
        _BUILDING = True
    try:
        import os
        if not os.getenv("AWS_BEARER_TOKEN_BEDROCK") and not os.getenv("ANTHROPIC_API_KEY"):
            _ERROR = "no model credentials (AWS_BEARER_TOKEN_BEDROCK)"
            log.warning(_ERROR)
            return
        # Avoid speech auto-start side-effects.
        os.environ.setdefault("NEON_NO_SPEECH", "1")
        from strands import Agent
        import g1  # single source of truth for tools + persona (no blocking singleton)
        tools = g1.build_voice_tools()

        # Build a BedrockModel with EXTENDED THINKING enabled so the dashboard
        # can stream the model's reasoning deltas (reasoningText events). The
        # voice/shell personas use a bare model string for lowest latency —
        # this reasoning model is dashboard-only.
        #
        # Thinking requires: temperature=1, max_tokens > budget_tokens.
        # Disable gracefully (bare string model) if creds aren't Bedrock or
        # the thinking-capable model rejects the config.
        # The model id is re-read from the environment at every build so a
        # Configuration > model change applies on rebuild() without a restart;
        # g1.MODEL_ID is only the import-time default.
        model_id = os.getenv("NEON_MODEL_ID") or os.getenv("STRANDS_MODEL_ID") or g1.MODEL_ID
        global _MODEL_ID
        _MODEL_ID = model_id
        model = model_id
        if os.getenv("AWS_BEARER_TOKEN_BEDROCK") or os.getenv("AWS_ACCESS_KEY_ID"):
            try:
                from strands.models import BedrockModel
                max_tok = int(os.getenv("NEON_MAX_TOKENS", "8192"))
                # claude-opus-4-8 uses the ADAPTIVE thinking API:
                #   thinking.type = "adaptive" + output_config.effort
                # (older models used {"type":"enabled","budget_tokens":N}).
                effort = os.getenv("NEON_THINK_EFFORT", "medium")  # low|medium|high
                model = BedrockModel(
                    model_id=model_id,
                    max_tokens=max_tok,
                    temperature=1.0,
                    additional_request_fields={
                        "thinking": {"type": "adaptive"},
                        "output_config": {"effort": effort},
                    },
                )
                log.info(f"🧠 reasoning enabled (adaptive, effort={effort}, max_tokens={max_tok})")
            except Exception as e:
                log.warning(f"reasoning model build failed, using plain string: {e}")
                model = model_id

        # Build with the STATIC base prompt (no DDS at construction); the
        # dashboard adds live-state per-turn from its cached snapshot.
        a = Agent(
            model=model,
            tools=tools,
            system_prompt=g1._BASE,
        )
        global _static_prompt
        _static_prompt = g1._BASE
        _AGENT = a
        log.info(f"🧠 NEON dashboard agent built ({len(a.tool_names)} tools)")
    except Exception as e:
        _ERROR = f"agent build failed: {e}"
        log.warning(_ERROR, exc_info=True)
    finally:
        _BUILDING = False
_static_prompt = ""


def _live_block_from_cache() -> str:
    """Build a live-state block from the dashboard's cached snapshot (no DDS)."""
    try:
        from docs.dashboard import server as _srv
    except Exception:
        try:
            import server as _srv
        except Exception:
            return ""
    try:
        with _srv._SNAP_LOCK:
            snap = dict(_srv._SNAPSHOT)
    except Exception:
        return ""
    if not snap:
        return ""
    bat = snap.get("battery") or {}
    ls = snap.get("lowstate") or {}
    st = snap.get("state") or {}
    mode = (st.get("mode") or {}).get("name") if isinstance(st.get("mode"), dict) else "?"
    return (
        "\n\n## 🤖 NEON LIVE STATE (cached, ~1s fresh)\n"
        f"- Controller: mode={mode} FSM={st.get('fsm_id')} "
        f"arm_ready={st.get('arm_ready') or (ls.get('mode_machine') in (5,6))}\n"
        f"- Posture: {ls.get('posture')} avg_knee={(ls.get('legs') or {}).get('avg_knee')} "
        f"imu_rpy={ls.get('imu_rpy')}\n"
        f"- Battery: SOC={bat.get('soc_pct')}% V={bat.get('voltage_v')}V I={bat.get('current_a')}A\n"
        "*(trust this snapshot; only call g1_get_state if you must — loco RPC may be slow)*\n"
    )


def status() -> dict:
    # Never blocks: report building state without forcing a build here.
    if _AGENT is not None:
        return {"ready": True, "model": _MODEL_ID, "error": None,
                "tools": len(_AGENT.tool_names),
                "turns": len(_AGENT.messages) if hasattr(_AGENT, "messages") else 0}
    if _ERROR:
        return {"ready": False, "error": _ERROR, "tools": 0, "turns": 0}
    # kick off a background build the first time status is polled
    if not _BUILDING:
        threading.Thread(target=_build, daemon=True, name="neon-agent-build").start()
    return {"ready": False, "error": None, "tools": 0, "turns": 0, "building": True}


def ask(prompt: str) -> dict:
    _build()  # blocking build if not ready (runs in the /api/chat thread)
    if _AGENT is None:
        return {"reply": None, "error": _ERROR or "agent still building — retry shortly"}
    try:
        with _LOCK:
            # Inject the dashboard's CACHED telemetry (non-blocking) into the
            # prompt — never a DDS-blocking prompt builder (the wedged loco RPC
            # would hang the request for 5s+ per turn).
            try:
                _AGENT.system_prompt = _static_prompt + _live_block_from_cache()
            except Exception as e:
                log.debug(f"state inject failed: {e}")
            result = _AGENT(prompt)
        return {"reply": str(result), "error": None}
    except Exception as e:
        log.warning(f"chat ask failed: {e}")
        return {"reply": None, "error": str(e)}


async def ask_stream(prompt: str):
    """Async generator yielding streaming events for SSE.

    Mirrors devduck's callback_handler event shape. Yields dicts:
      {"type": "text",  "data": "<delta>"}        — assistant text token(s)
      {"type": "tool",  "name": "...", "status": "running"|"done"}
      {"type": "reasoning", "data": "..."}         — extended-thinking delta
      {"type": "done",  "reply": "<full text>"}    — turn complete
      {"type": "error", "error": "..."}            — failure

    The single shared agent + history is serialized via _LOCK so concurrent
    dashboard tabs don't interleave into the same conversation.
    """
    _build()
    if _AGENT is None:
        yield {"type": "error", "error": _ERROR or "agent still building — retry shortly"}
        return

    acquired = _LOCK.acquire(timeout=1.0)
    if not acquired:
        yield {"type": "error", "error": "agent busy with another request"}
        return

    try:
        try:
            _AGENT.system_prompt = _static_prompt + _live_block_from_cache()
        except Exception as e:
            log.debug(f"state inject failed: {e}")

        full_text: list[str] = []
        last_tool: Optional[str] = None
        try:
            async for event in _AGENT.stream_async(prompt):
                # text delta
                data = event.get("data")
                if data:
                    full_text.append(data)
                    yield {"type": "text", "data": data}

                # extended reasoning delta (adaptive thinking)
                # Strands surfaces reasoning as event["reasoning"]=True with the
                # text nested in delta.reasoningContent.text (older models used a
                # top-level "reasoningText"). Handle both; skip signature-only
                # frames.
                rt = event.get("reasoningText")
                if not rt and event.get("reasoning"):
                    d = event.get("delta") or {}
                    rc = d.get("reasoningContent") if isinstance(d, dict) else None
                    if isinstance(rc, dict):
                        rt = rc.get("text")
                if rt:
                    yield {"type": "reasoning", "data": rt}

                # tool invocation lifecycle
                tu = event.get("current_tool_use") or {}
                name = tu.get("name")
                if name and name != last_tool:
                    last_tool = name
                    yield {"type": "tool", "name": name, "status": "running"}
        except Exception as e:
            log.warning(f"chat stream failed: {e}")
            yield {"type": "error", "error": str(e)}
            return

        yield {"type": "done", "reply": "".join(full_text).strip()}
    finally:
        _LOCK.release()


def reset() -> dict:
    if _AGENT is not None and hasattr(_AGENT, "messages"):
        _AGENT.messages.clear()
    return {"ok": True}


def live_model() -> Optional[str]:
    """Model id of the agent currently answering, None while building/failed."""
    return _MODEL_ID if _AGENT is not None else None


def rebuild() -> dict:
    """Drop the cached agent so the next message builds a fresh one from the
    current environment (used after Configuration > model). Conversation
    history is lost on purpose: a new model should not inherit another
    model's reasoning blocks."""
    global _AGENT, _ERROR, _MODEL_ID
    with _BUILD_LOCK:
        _AGENT = None
        _ERROR = None
        _MODEL_ID = None
    threading.Thread(target=_build, daemon=True, name="neon-chat-rebuild").start()
    return {"ok": True, "rebuilding": True}
