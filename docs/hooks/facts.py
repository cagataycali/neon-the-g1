"""mkdocs hook: numbers the docs state are derived from the code, never typed.

Pages write ``{{facts:all_tools}}`` and the build substitutes the value read
from ``tools/__init__.py`` (bundle sizes, parsed with ``ast``, no import of the
package, so the build needs neither strands nor the robot) and ``g1.py`` (the
default model id). An unknown key or a README that states a different robot
tool count aborts the build, which under ``--strict`` fails the deploy.

Keys: see ``FACTS`` after ``derive()``; ``python docs/hooks/facts.py`` prints them.
Wired in mkdocs.yml under ``hooks:``. Content-only: nothing at runtime imports it.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS_INIT = ROOT / "tools" / "__init__.py"
G1_PY = ROOT / "g1.py"
README = ROOT / "README.md"

TOKEN = re.compile(r"\{\{facts:([a-z_]+)\}\}")

# bundle name in tools/__init__.py -> fact key
BUNDLES = {
    "G1_STATE_TOOLS": "state_tools",
    "G1_POSTURE_TOOLS": "posture_tools",
    "G1_ARM_TOOLS": "arm_tools",
    "G1_AUDIO_TOOLS": "audio_tools",
    "G1_LIDAR_TOOLS": "lidar_tools",
    "G1_SLAM_TOOLS": "slam_tools",
    "G1_DDS_TOOLS": "dds_tools",
    "G1_SENSING_TOOLS": "sensing_tools",
    "G1_UNIVERSAL_TOOLS": "universal_tools",
    "G1_LOCOMOTION_TOOLS": "locomotion_tools",
    "G1_MOTION_GEN_TOOLS": "motion_gen_tools",
    "G1_SAFE_TOOLS": "safe_tools",
    "G1_ALL_TOOLS": "all_tools",
    "G1_LOOKOUT_TOOLS": "lookout_tools",
}


def _names(node: ast.AST, env: dict[str, list[str]]) -> list[str]:
    """Flatten a list literal / name / ``a + b`` expression into tool names."""
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str] = []
        for elt in node.elts:
            out.extend(_names(elt, env))
        return out
    if isinstance(node, ast.Name):
        if node.id in env:
            return list(env[node.id])
        return [node.id]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _names(node.left, env) + _names(node.right, env)
    raise ValueError(f"facts: cannot derive a tool list from {ast.dump(node)[:80]}")


def derive() -> dict[str, str]:
    tree = ast.parse(TOOLS_INIT.read_text(encoding="utf-8"))
    env: dict[str, list[str]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in BUNDLES or name.startswith("G1_"):
                env[name] = _names(node.value, env)
    facts: dict[str, str] = {}
    for bundle, key in BUNDLES.items():
        if bundle not in env:
            raise ValueError(f"facts: {bundle} not found in {TOOLS_INIT}")
        facts[key] = str(len(env[bundle]))
    # sensing = cameras + lidar + slam + dds; the camera count is the remainder
    facts["camera_tools"] = str(
        int(facts["sensing_tools"]) - int(facts["lidar_tools"])
        - int(facts["slam_tools"]) - int(facts["dds_tools"]))
    m = re.search(r'MODEL_ID\s*=\s*os\.getenv\("NEON_MODEL_ID",\s*"([^"]+)"\)',
                  G1_PY.read_text(encoding="utf-8"))
    if not m:
        raise ValueError("facts: MODEL_ID default not found in g1.py")
    facts["default_model"] = m.group(1)
    return facts


FACTS = derive()


def check_readme() -> None:
    """The README is the one page outside the build; keep its number honest."""
    text = README.read_text(encoding="utf-8")
    stated = set(re.findall(r"\b(\d+) robot tools\b", text))
    stated |= set(re.findall(r"tools-(\d+)-", text))  # the shields.io badge
    if stated and stated != {FACTS["all_tools"]}:
        raise ValueError(
            f"facts: README.md says {sorted(stated)} robot tools, "
            f"tools/__init__.py has {FACTS['all_tools']}")


def on_config(config):
    check_readme()
    return config


def on_page_markdown(markdown, page, config, files):
    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in FACTS:
            raise ValueError(f"facts: unknown key {{{{facts:{key}}}}} on {page.file.src_path}")
        return FACTS[key]
    return TOKEN.sub(sub, markdown)


if __name__ == "__main__":
    for k, v in FACTS.items():
        print(f"{k}={v}")
    check_readme()
    print("README ok" if "--quiet" not in sys.argv else "", end="")
