"""🛠 `make` — let NEON drive its own Makefile.

The repo's Makefile is the operational control panel (run/voice/telegram/
thinker/dashboard/wifi/auth/logs/…). This tool lets any persona list and
invoke those targets so the agent can operate itself: start the voice
listener, tail logs, refresh the service token, check wifi, etc.

Targets that don't move the robot are safe. The tool refuses obviously
destructive host ops unless ``force=True``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from strands import tool

# Repo root = parent of this tools/ package.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Targets that could disrupt the host/session — require force=True.
_GUARDED = {"clean", "prune", "down", "docker-down", "uninstall-service", "stop"}


def _makefile_path() -> Optional[Path]:
    mk = _REPO_ROOT / "Makefile"
    return mk if mk.exists() else None


def _parse_targets(mk: Path) -> List[Dict[str, str]]:
    """Parse `target: ## description` lines from the Makefile."""
    out: List[Dict[str, str]] = []
    pat = re.compile(r"^([a-zA-Z0-9_-]+):.*?##\s*(.*)$")
    for line in mk.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = pat.match(line)
        if m:
            out.append({"target": m.group(1), "description": m.group(2).strip()})
    return out


@tool
def make(
    target: str = "",
    args: str = "",
    action: str = "run",
    force: bool = False,
    timeout: int = 300,
) -> Dict[str, Any]:
    """
    🛠 List or run Makefile targets (operate NEON's own control panel).

    Args:
        target: Makefile target to run (e.g. "voice-status", "logs", "tg-bg").
                Ignored when action="list".
        args:   Extra make args / variables, e.g. 'Q="stand up"' for `make ask`.
        action: "list" (show all targets + descriptions) or "run" (default).
        force:  Required True to run guarded targets (clean/prune/down/stop/…).
        timeout: Seconds before the make call is killed (default 300).

    Returns:
        dict with status + content. For "list", content is the target table.

    Examples:
        make(action="list")                         # see every target
        make(target="voice-status")                 # is voice running?
        make(target="ask", args='Q="wave at me"')   # one-shot query
        make(target="tg-bg")                         # start telegram in bg
        make(target="clean", force=True)             # guarded → needs force
    """
    mk = _makefile_path()
    if not mk:
        return {"status": "error",
                "content": [{"text": f"No Makefile found at {_REPO_ROOT}"}]}

    targets = _parse_targets(mk)
    known = {t["target"] for t in targets}

    if action == "list" or not target:
        lines = [f"Makefile targets ({len(targets)}) @ {_REPO_ROOT}:", ""]
        for t in sorted(targets, key=lambda x: x["target"]):
            guard = " ⚠️" if t["target"] in _GUARDED else ""
            lines.append(f"  {t['target']:<20} {t['description']}{guard}")
        lines.append("")
        lines.append("⚠️ = guarded (pass force=True). Run with target=\"<name>\".")
        return {"status": "success", "content": [{"text": "\n".join(lines)}]}

    if not shutil.which("make"):
        return {"status": "error",
                "content": [{"text": "`make` binary not found on PATH."}]}

    if target not in known:
        near = [t for t in known if target in t or t in target]
        hint = f" Did you mean: {', '.join(sorted(near))}?" if near else ""
        return {"status": "error",
                "content": [{"text": f"Unknown target '{target}'.{hint} "
                                     f"Use action='list' to see all."}]}

    if target in _GUARDED and not force:
        return {"status": "error",
                "content": [{"text": f"'{target}' is guarded (may disrupt host/"
                                     f"session). Re-call with force=True to run it."}]}

    cmd = ["make", target]
    if args:
        # allow VAR="value" style args; split respecting simple quoting
        import shlex
        cmd.extend(shlex.split(args))

    try:
        proc = subprocess.run(
            cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True,
            timeout=timeout, env={**os.environ},
        )
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(no output)"
        status = "success" if proc.returncode == 0 else "error"
        return {"status": status,
                "content": [{"text": f"$ make {target} {args}\nrc={proc.returncode}\n\n{out[:8000]}"}]}
    except subprocess.TimeoutExpired:
        return {"status": "error",
                "content": [{"text": f"`make {target}` timed out after {timeout}s. "
                                     f"For long-running targets use the *-bg variant "
                                     f"(e.g. voice-bg, tg-bg)."}]}
    except Exception as e:
        return {"status": "error", "content": [{"text": f"make failed: {e}"}]}
