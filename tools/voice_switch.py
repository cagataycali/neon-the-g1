"""voice_switch — let the voice agent change its own voice/model at runtime.

Voice profile is set at bidi session start (BidiOpenAIRealtimeModel reads
`provider_config.audio.voice` once during start()). To swap voices, we
update VOICE_NAME / VOICE_MODEL in .env and bounce the launchd service.
The KeepAlive=true plist immediately respawns it with the new config.

Within ~3 seconds the new voice/model is live and the briefing queue
keeps any pending messages it should speak after the swap.
"""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
from typing import Optional
from strands import tool

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
LAUNCHD_LABEL = "com.cagatay.gh-watcher.voice"

# Canonical voice catalog per provider. Keep in sync with Makefile voice-list.
OPENAI_VOICES = {
    "alloy":   "balanced neutral (legacy default)",
    "ash":     "clear, articulate",
    "ballad":  "warm, narrative",
    "coral":   "bright feminine",
    "echo":    "smooth masculine",
    "sage":    "calm, contemplative",
    "shimmer": "light feminine",
    "verse":   "expressive masculine",
    "marin":   "NEW gpt-realtime-2: natural feminine",
    "cedar":   "NEW gpt-realtime-2: natural masculine",
}

NOVA_VOICES = {
    "tiffany":  "English (US) feminine",
    "matthew":  "English (US) masculine",
    "amy":      "English (GB) feminine",
    "ambre":    "French feminine",
    "florian":  "French masculine",
    "beatrice": "Italian feminine",
    "lorenzo":  "Italian masculine",
    "greta":    "German feminine",
    "lennart":  "German masculine",
    "lupe":     "Spanish feminine",
    "carlos":   "Spanish masculine",
}

OPENAI_MODELS = [
    "gpt-realtime",
    "gpt-realtime-1.5",
    "gpt-realtime-2",
    "gpt-realtime-mini",
    "gpt-realtime-translate",
    "gpt-realtime-whisper",
]


def _read_env() -> dict:
    if not ENV_FILE.exists():
        return {}
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _write_env_kv(updates: dict[str, str]) -> None:
    """Update .env, replacing matching keys, preserving comments + order."""
    keys_to_set = set(updates)
    out_lines = []
    seen = set()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.split("=", 1)[0].strip()
                if k in keys_to_set:
                    out_lines.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            out_lines.append(line)
    # Append new keys not already present
    for k in keys_to_set - seen:
        out_lines.append(f"{k}={updates[k]}")
    ENV_FILE.write_text("\n".join(out_lines) + "\n")
    os.chmod(ENV_FILE, 0o600)


def _bounce_voice_service() -> tuple[bool, str]:
    """launchctl kickstart -k forces a restart even if it was running.
    Returns (ok, message)."""
    try:
        uid = os.getuid()
        target = f"gui/{uid}/{LAUNCHD_LABEL}"
        r = subprocess.run(
            ["launchctl", "kickstart", "-k", target],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            return True, "service kicked"
        return False, f"launchctl rc={r.returncode}: {r.stderr.strip() or r.stdout.strip()}"
    except Exception as e:
        return False, f"launchctl error: {e}"


@tool
def switch_voice(
    voice: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    list_options: bool = False,
) -> dict:
    """Switch the voice agent's voice profile, model, or provider — live.

    The voice agent reloads itself within ~3 seconds with the new settings.
    Pending briefings in the queue are preserved across the swap.

    Args:
        voice: Voice profile name. OpenAI: alloy, ash, ballad, coral, echo,
               sage, shimmer, verse, marin, cedar. Nova Sonic: tiffany,
               matthew, amy, ambre, florian, beatrice, lorenzo, greta,
               lennart, lupe, carlos.
        model: OpenAI model id (e.g. 'gpt-realtime-2', 'gpt-realtime-mini').
               Ignored when provider is nova_sonic.
        provider: 'openai' or 'nova_sonic'. Switches the whole backend.
        list_options: If True, return the catalog of options without changing
                      anything. Useful when the user says "what voices can you use?".

    Returns:
        dict with status + applied changes + restart confirmation.

    Examples:
        switch_voice(voice='cedar')                           # try a new voice
        switch_voice(voice='matthew', provider='nova_sonic')  # switch to Bedrock
        switch_voice(model='gpt-realtime-mini')               # go cheaper/faster
        switch_voice(list_options=True)                        # browse options
    """
    if list_options:
        return {
            "status": "success",
            "providers": ["openai", "nova_sonic"],
            "openai_voices": OPENAI_VOICES,
            "openai_models": OPENAI_MODELS,
            "nova_sonic_voices": NOVA_VOICES,
            "current": _read_env_summary(),
        }

    env = _read_env()
    cur_provider = env.get("VOICE_PROVIDER", "nova_sonic").lower()
    target_provider = (provider or cur_provider).lower()

    if target_provider not in ("openai", "openai_realtime", "nova_sonic", "nova", "novasonic"):
        return {
            "status": "error",
            "message": f"unknown provider '{provider}'. Use 'openai' or 'nova_sonic'.",
        }
    if target_provider in ("nova", "novasonic"):
        target_provider = "nova_sonic"
    if target_provider == "openai_realtime":
        target_provider = "openai"

    # Validate voice against target provider
    if voice:
        v = voice.lower().strip()
        if target_provider == "openai" and v not in OPENAI_VOICES:
            return {
                "status": "error",
                "message": f"'{voice}' is not a valid OpenAI voice. "
                           f"Valid: {', '.join(OPENAI_VOICES.keys())}",
                "hint": "Run with list_options=True to see all options",
            }
        if target_provider == "nova_sonic" and v not in NOVA_VOICES:
            return {
                "status": "error",
                "message": f"'{voice}' is not a valid Nova Sonic voice. "
                           f"Valid: {', '.join(NOVA_VOICES.keys())}",
                "hint": "Run with list_options=True to see all options",
            }
        voice = v

    # Validate model (only relevant for openai)
    if model and target_provider != "openai":
        return {
            "status": "error",
            "message": "model parameter only applies to provider=openai",
        }

    # Build updates
    updates: dict[str, str] = {}
    if provider:
        updates["VOICE_PROVIDER"] = target_provider
    if voice is not None:
        updates["VOICE_NAME"] = voice
    if model is not None:
        updates["VOICE_MODEL"] = model

    if not updates:
        return {
            "status": "noop",
            "message": "nothing to change. Pass voice, model, or provider.",
            "current": _read_env_summary(),
        }

    # Pre-flight: if switching to openai, ensure key is present
    if target_provider == "openai" and not env.get("OPENAI_API_KEY", "").startswith("sk-"):
        return {
            "status": "error",
            "message": "OPENAI_API_KEY not set in .env (or not 'sk-...' prefix)",
        }
    if target_provider == "nova_sonic" and not env.get("AWS_BEARER_TOKEN_BEDROCK"):
        return {
            "status": "error",
            "message": "AWS_BEARER_TOKEN_BEDROCK not set in .env",
        }

    _write_env_kv(updates)

    ok, msg = _bounce_voice_service()
    return {
        "status": "success" if ok else "partial",
        "applied": updates,
        "restart": msg,
        "note": "Voice will reload in ~3s. Any briefing you queue now will be "
                "spoken with the new voice.",
        "current": _read_env_summary(),
    }


def _read_env_summary() -> dict:
    e = _read_env()
    return {
        "VOICE_PROVIDER": e.get("VOICE_PROVIDER", "nova_sonic (default)"),
        "VOICE_NAME":     e.get("VOICE_NAME", "(provider default)"),
        "VOICE_MODEL":    e.get("VOICE_MODEL", "(provider default)"),
    }
