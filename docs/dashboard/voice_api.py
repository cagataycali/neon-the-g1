"""Voice: mute / snooze / profile for the dashboard.

State lives in the shared memory kv through :mod:`tools.voice_state` (the
same keys ``voice.muted`` / ``voice.muted_until`` the voice listener's
MuteFlag polls and ``make voice-status`` prints). Muted means silent on both
sides: the mic input is dropped and model audio is not played.

The profile (VOICE_PROVIDER / VOICE_NAME / VOICE_MODEL) is read by
``g1_speech_listener.py`` at start, so a change writes .env and asks the
host's neon-ctl to ``restart-voice`` (a systemd unit, not a container).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

PROVIDERS = ("openai", "nova_sonic", "gemini")
SNOOZE_MINUTES = (15, 60, 180)


def _mod(name: str):
    import importlib
    try:
        return importlib.import_module(f"docs.dashboard.{name}")
    except Exception:
        return importlib.import_module(name)


def _state():
    """tools.voice_state; by file path when the robot toolset cannot import
    (a dev machine without the SDK). Same sqlite file either way."""
    try:
        from tools import voice_state
        return voice_state
    except Exception:
        import importlib.util
        import sys
        path = Path(__file__).resolve().parent.parent.parent / "tools" / "voice_state.py"
        mod = sys.modules.get("_neon_voice_state")
        if mod is None:
            spec = importlib.util.spec_from_file_location("_neon_voice_state", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            sys.modules["_neon_voice_state"] = mod
        return mod


def _catalog() -> Dict[str, Any]:
    try:
        from tools.voice_switch import NOVA_VOICES, OPENAI_MODELS, OPENAI_VOICES
        return {"openai": sorted(OPENAI_VOICES), "nova_sonic": sorted(NOVA_VOICES),
                "gemini": [], "openai_models": list(OPENAI_MODELS)}
    except Exception:  # robot toolset not importable here: the same lists, pinned
        return {"openai": ["alloy", "ash", "ballad", "cedar", "coral", "echo", "marin", "sage", "shimmer", "verse"],
                "nova_sonic": ["ambre", "amy", "beatrice", "carlos", "florian", "greta", "lennart", "lorenzo", "lupe", "matthew", "tiffany"],
                "gemini": [], "openai_models": ["gpt-realtime", "gpt-realtime-1.5", "gpt-realtime-2", "gpt-realtime-mini"]}


def profile() -> Dict[str, str]:
    cfg = _mod("config_api")
    env = cfg._parse(cfg._read_env_lines())
    pick = lambda k, d: env.get(k) or os.getenv(k) or d  # noqa: E731
    return {"provider": pick("VOICE_PROVIDER", "openai"), "voice": pick("VOICE_NAME", "alloy"),
            "model": pick("VOICE_MODEL", "gpt-realtime")}


def status() -> Dict[str, Any]:
    """{muted, muted_until, remaining_s, provider, voice, model, service_active, ctl_alive}."""
    st = _state().status()
    hb = _mod("ctl").heartbeat()
    return {**st, **profile(), "service_active": hb.get("voice"), "ctl_alive": hb.get("alive", False),
            "snooze_options": list(SNOOZE_MINUTES), "catalog": _catalog(),
            "note": "muted = mic dropped and speaker silent"}


def mute(minutes: Optional[float]) -> Dict[str, Any]:
    if minutes is not None:
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            return {"ok": False, "error": "minutes must be a number or null"}
        if minutes < 0 or minutes > 24 * 60:
            return {"ok": False, "error": "minutes must be between 0 and 1440"}
        if minutes == 0:
            minutes = None
    st = _state().mute(minutes)
    return {"ok": True, **st}


def unmute() -> Dict[str, Any]:
    return {"ok": True, **_state().unmute()}


def set_profile(provider: Optional[str], voice: Optional[str], model: Optional[str],
                restart: bool = True) -> Dict[str, Any]:
    """Write VOICE_* to .env and restart the voice listener through neon-ctl."""
    cur = profile()
    provider = (provider or cur["provider"]).strip().lower()
    voice = (voice or cur["voice"]).strip().lower()
    model = (model or cur["model"]).strip()
    if provider not in PROVIDERS:
        return {"ok": False, "error": f"provider must be one of {', '.join(PROVIDERS)}"}
    cat = _catalog()
    allowed: List[str] = cat.get(provider) or []
    if allowed and voice not in allowed:
        return {"ok": False, "error": f"unknown {provider} voice {voice}; valid: {', '.join(allowed)}"}
    if not model or any(c in model for c in " \n\"'"):
        return {"ok": False, "error": "model must be a single token"}
    cfg = _mod("config_api")
    cfg.set_env({"VOICE_PROVIDER": provider, "VOICE_NAME": voice, "VOICE_MODEL": model})
    out: Dict[str, Any] = {"ok": True, "provider": provider, "voice": voice, "model": model,
                           "changed": {k: (cur[k], v) for k, v in
                                       (("provider", provider), ("voice", voice), ("model", model)) if cur[k] != v}}
    if restart:
        out["restart"] = _mod("ctl").request_ctl("restart-voice", wait_s=60)
        out["ok"] = bool(out["restart"].get("ok"))
    return out
