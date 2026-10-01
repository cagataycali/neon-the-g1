"""Tool-call observability for every NEON persona.

Wraps each Strands ``AgentTool`` so that EVERY invocation (voice, telegram,
thinker, shell, dashboard chat) is recorded with its name, arguments,
duration, status, rc and message:

  (a) one line on stderr  -> ``journalctl -u neon-voice`` / ``docker logs``
  (b) one ``agent_log`` row with role="tool" and
      meta={tool, tool_name, rc, status, duration_ms, args, moved?}
      -> the dashboard Activity Log and the cross-persona prompt block.

Why a wrapper and not hooks: the bidi agent (strands 1.44 on the Jetson)
runs tools through its own executor from a worker thread; a wrapper around
``AgentTool.stream`` is the one point every executor must pass through, so
it provably fires in the voice path (see tests/test_voice_tool_logging.py).

The wrapper keeps the inner tool's ``tool_name``/``tool_spec``/``tool_type``
so the model-facing schema is byte-identical; a tool that already is a
``LoggedTool`` is returned as-is (idempotent).
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Iterable, List, Optional

from strands.types.tools import AgentTool

from . import agent_log

MAX_FIELD = 300               # chars kept per logged field (args, message)
_SECRET_KEYS = ("token", "password", "pin", "secret", "api_key", "apikey")


def _short(value: Any, limit: int = MAX_FIELD) -> str:
    try:
        s = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=True)
    except Exception:
        s = str(value)
    s = s.replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _mask(args: Any) -> Any:
    """Hide obvious secrets in tool arguments before they reach a log."""
    if isinstance(args, dict):
        out = {}
        for k, v in args.items():
            if any(s in str(k).lower() for s in _SECRET_KEYS) and v not in (None, ""):
                out[k] = "***"
            else:
                out[k] = _mask(v)
        return out
    if isinstance(args, list):
        return [_mask(v) for v in args]
    return args


def _is_result(event: Any) -> bool:
    """Is this stream event the final ToolResult (plain dict or ToolResultEvent)?"""
    if getattr(event, "tool_result", None) is not None:
        return True
    if isinstance(event, dict):
        if isinstance(event.get("tool_result"), dict):
            return True
        return "status" in event and "content" in event
    return False


def summarize_result(result: Any) -> Dict[str, Any]:
    """Pull status / rc / message / moved out of a Strands ToolResult.

    Works on the ``{status, content:[{text},{json}]}`` shape every NEON tool
    returns (see ``tools._g1_common._normalize``) and degrades gracefully on
    anything else.
    """
    out: Dict[str, Any] = {"status": None, "rc": None, "message": "", "moved": None}
    # strands >= 1.4x yields a ToolResultEvent {type: tool_result, tool_result: {...}} last
    tr = getattr(result, "tool_result", None)
    if tr is not None:
        result = tr
    elif isinstance(result, dict) and isinstance(result.get("tool_result"), dict):
        result = result["tool_result"]
    if not isinstance(result, dict):
        out["message"] = _short(result)
        return out
    out["status"] = result.get("status")
    texts: List[str] = []
    blob: Dict[str, Any] = {}
    for c in result.get("content") or []:
        if not isinstance(c, dict):
            continue
        if "text" in c:
            texts.append(str(c["text"]))
        elif "json" in c and isinstance(c["json"], dict):
            blob.update(c["json"])
        elif "image" in c:
            texts.append("<image>")
    if "rc" in blob:
        out["rc"] = blob.get("rc")
    if "moved" in blob:
        out["moved"] = bool(blob.get("moved"))
    if "measured_m" in blob:
        out["measured_m"] = blob.get("measured_m")
    if "measured_rad" in blob:
        out["measured_rad"] = blob.get("measured_rad")
    out["message"] = _short(" | ".join(t for t in texts if t) or blob.get("message") or "")
    return out


class LoggedTool(AgentTool):
    """An ``AgentTool`` that logs every call of the tool it wraps."""

    def __init__(self, inner: AgentTool, persona: str = "shell"):
        super().__init__()
        self._inner = inner
        self._persona = persona
        try:
            self._is_dynamic = bool(inner.is_dynamic)
        except Exception:
            pass

    # -- identity: byte-identical to the wrapped tool -----------------------
    @property
    def tool_name(self) -> str:
        return self._inner.tool_name

    @property
    def tool_spec(self):
        return self._inner.tool_spec

    @property
    def tool_type(self) -> str:
        return self._inner.tool_type

    @property
    def supports_hot_reload(self) -> bool:
        return self._inner.supports_hot_reload

    @property
    def inner(self) -> AgentTool:
        return self._inner

    def get_display_properties(self) -> Dict[str, str]:
        props = dict(self._inner.get_display_properties())
        props["Logged"] = self._persona
        return props

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Direct calls (``g1_get_state(network_interface=...)``) still work."""
        return self._inner(*args, **kwargs)  # type: ignore[operator]

    def __getattr__(self, name: str) -> Any:
        # Anything else (original_function, _metadata, ...) comes from the inner tool.
        return getattr(self._inner, name)

    # -- the one point every executor passes through ------------------------
    async def stream(self, tool_use, invocation_state, **kwargs):
        name = self.tool_name
        args = _mask((tool_use or {}).get("input"))
        tid = (tool_use or {}).get("toolUseId")
        t0 = time.monotonic()
        last: Any = None
        logged = False
        try:
            async for event in self._inner.stream(tool_use, invocation_state, **kwargs):
                last = event
                # The strands executor stops consuming at the first ToolResultEvent, so
                # this generator is never resumed after yielding it: log BEFORE the yield.
                if not logged and _is_result(event):
                    log_call(self._persona, name, args, time.monotonic() - t0,
                             summarize_result(event), tool_use_id=tid)
                    logged = True
                yield event
        except Exception as e:  # log, then let the executor handle it
            if not logged:
                log_call(self._persona, name, args, time.monotonic() - t0,
                         {"status": "exception", "rc": None, "message": _short(repr(e)), "moved": None},
                         tool_use_id=tid)
                logged = True
            raise
        finally:
            # Consumer closed us early (GeneratorExit) or the inner stream never yielded a result.
            if not logged:
                log_call(self._persona, name, args, time.monotonic() - t0,
                         summarize_result(last), tool_use_id=tid)


def log_call(persona: str, name: str, args: Any, duration_s: float,
             summary: Dict[str, Any], tool_use_id: Optional[str] = None) -> int:
    """Write the stderr line and the agent_log row. Never raises."""
    status = summary.get("status") or "unknown"
    rc = summary.get("rc")
    moved = summary.get("moved")
    msg = _short(summary.get("message") or "")
    args_s = _short(args)
    moved_s = "" if moved is None else f" moved={str(moved).lower()}"
    line = (f"[tool {persona}] {name}({args_s}) status={status} rc={rc}"
            f"{moved_s} {duration_s:.2f}s: {msg}")
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
    meta: Dict[str, Any] = {
        "tool": name, "tool_name": name, "status": status, "rc": rc,
        "duration_ms": int(duration_s * 1000), "args": args,
    }
    if moved is not None:
        meta["moved"] = moved
    for k in ("measured_m", "measured_rad"):
        if k in summary:
            meta[k] = summary[k]
    if tool_use_id:
        meta["tool_use_id"] = tool_use_id
    try:
        return agent_log.record(persona, "tool", line[:2000], meta=meta)
    except Exception as e:  # the DB must never take a tool down
        try:
            print(f"[tool {persona}] agent_log write failed: {e}", file=sys.stderr, flush=True)
        except Exception:
            pass
        return 0


def _module_tools(module: Any) -> List[AgentTool]:
    """Resolve a TOOL_SPEC-style module (strands_tools.shell, ...) the way the
    strands registry does, so it can be wrapped too."""
    try:
        from strands.tools.loader import load_tools_from_module
        return list(load_tools_from_module(module, module.__name__.split(".")[-1]))
    except Exception as e:
        print(f"[tool_log] cannot wrap module tool {getattr(module, '__name__', module)}: {e}",
              file=sys.stderr)
        return []


def wrap_tools(tools: Iterable[Any], persona: str) -> list:
    """Wrap every tool in ``tools`` (AgentTool instances and TOOL_SPEC modules);
    anything the registry would not understand either is passed through."""
    import inspect
    out = []
    for t in tools:
        if isinstance(t, LoggedTool):
            out.append(t)
        elif isinstance(t, AgentTool):
            out.append(LoggedTool(t, persona))
        elif inspect.ismodule(t) and hasattr(t, "__file__"):
            resolved = _module_tools(t)
            out.extend(LoggedTool(x, persona) for x in resolved) if resolved else out.append(t)
        else:
            out.append(t)
    return out
