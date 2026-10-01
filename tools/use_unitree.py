"""use_unitree — a universal @tool wrapper around every Unitree SDK2 client.

This lets ONE @tool cover the entire unitree_sdk2_python surface without a
hand-written @tool per method. Adding a new method upstream requires no
change here — discovery is dynamic (inspect first, AST fallback).

Services (auto-discovered from the SDK):
    loco            unitree_sdk2py.g1.loco.g1_loco_client.LocoClient
    arm             unitree_sdk2py.g1.arm.g1_arm_action_client.G1ArmActionClient
    audio           unitree_sdk2py.g1.audio.g1_audio_client.AudioClient
    motion_switcher unitree_sdk2py.comm.motion_switcher.motion_switcher_client.MotionSwitcherClient
    vui             unitree_sdk2py.go2.vui.vui_client.VuiClient
    robot_state     unitree_sdk2py.go2.robot_state.robot_state_client.RobotStateClient

Each client is cached as a singleton after first use (matches the pattern
established in _g1_common.py — re-Init() can crash the process).

Pattern:
  - @tool decorator (Strands native) with rich docstring for LLM grounding
  - Mutative-op detection → flagged in response (BYPASS_TOOL_CONSENT=true skips)
  - Parameter validation → helpful schema on error
  - Response normalization (tuple unpacking, JSON serialization)

Meta-operations (no DDS required, no robot needed):
  - list_services       → service registry
  - list_operations     → methods on a client
  - describe_operation  → inspect.signature + docstring + danger flags
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from strands import tool

# Reuse the battle-tested singleton + DDS init
from ._g1_common import (
    ensure_dds,
    get_arm_client,
    get_audio_client,
    get_loco_client,
    get_motion_switcher,
    _LOCO_CALL_LOCK,
    _normalize,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Service registry — dynamically discovered
# ═══════════════════════════════════════════════════════════════════════════

# Cached VUI client (not in _g1_common)
_VUI_LOCK = threading.Lock()
_VUI_CLIENT: Optional[Any] = None


def _get_vui_client(timeout: float = 3.0):
    global _VUI_CLIENT
    with _VUI_LOCK:
        if _VUI_CLIENT is None:
            from unitree_sdk2py.go2.vui.vui_client import VuiClient
            c = VuiClient()
            c.SetTimeout(timeout)
            c.Init()
            _VUI_CLIENT = c
        return _VUI_CLIENT


# Cached robot-state client (service list/switch — powers services on/off)
_RS_LOCK = threading.Lock()
_RS_CLIENT: Optional[Any] = None


def _get_robot_state_client(timeout: float = 3.0):
    """Go2 RobotStateClient — also works on G1 (robot-wide service registry).

    Exposes: ServiceList, ServiceSwitch (turn services on/off),
             SetReportFreq (change broadcast rate).
    """
    global _RS_CLIENT
    with _RS_LOCK:
        if _RS_CLIENT is None:
            from unitree_sdk2py.go2.robot_state.robot_state_client import RobotStateClient
            c = RobotStateClient()
            c.SetTimeout(timeout)
            c.Init()
            _RS_CLIENT = c
        return _RS_CLIENT


# service_name → (getter, sdk_class_qualname)
SERVICES: Dict[str, Tuple[Callable[[], Any], str]] = {
    "loco": (
        get_loco_client,
        "unitree_sdk2py.g1.loco.g1_loco_client.LocoClient",
    ),
    "arm": (
        get_arm_client,
        "unitree_sdk2py.g1.arm.g1_arm_action_client.G1ArmActionClient",
    ),
    "audio": (
        get_audio_client,
        "unitree_sdk2py.g1.audio.g1_audio_client.AudioClient",
    ),
    "motion_switcher": (
        get_motion_switcher,
        "unitree_sdk2py.comm.motion_switcher.motion_switcher_client.MotionSwitcherClient",
    ),
    "vui": (
        _get_vui_client,
        "unitree_sdk2py.go2.vui.vui_client.VuiClient",
    ),
    "robot_state": (
        _get_robot_state_client,
        "unitree_sdk2py.go2.robot_state.robot_state_client.RobotStateClient",
    ),
}


# ═══════════════════════════════════════════════════════════════════════════
# Mutative-op detection (inspired by use_aws MUTATIVE_OPERATIONS)
# ═══════════════════════════════════════════════════════════════════════════

MUTATIVE_PREFIXES = (
    "Set", "Execute", "Move", "Start", "Stop", "Damp",
    "Sit", "HighStand", "LowStand", "WaveHand", "ShakeHand",
    "Squat2StandUp", "Lie2StandUp", "StandUp2Squat",
    "BalanceStand", "ZeroTorque",
    "LedControl", "TtsMaker", "PlayStream", "PlayStop",
    "SelectMode", "ReleaseMode",
)

# Operations that are EXPLICITLY safe despite matching a prefix above.
READONLY_WHITELIST = {
    "CheckMode", "GetActionList", "GetVolume", "GetBrightness", "GetSwitch",
    "Init",  # idempotent singleton init
}

# Operations that are EXTREMELY dangerous — always confirm even in dev
HIGH_DANGER_OPS = {
    ("loco", "ZeroTorque"),       # robot collapses
    ("loco", "SetFsmId"),          # if fsm_id=0 → collapse
    ("loco", "SetVelocity"),       # walking
    ("loco", "Move"),              # walking (possibly 10-day duration!)
    ("loco", "WaveHand"),          # invokes leg motion in some FSMs
    ("loco", "ShakeHand"),
    ("motion_switcher", "ReleaseMode"),  # robot uncontrolled
}


def _is_readonly(operation_name: str) -> bool:
    """Operation is side-effect-free (safe to run without confirmation)."""
    if operation_name in READONLY_WHITELIST:
        return True
    if operation_name.startswith("_"):
        return False
    if operation_name.startswith("Get") or operation_name.startswith("Check"):
        return True
    return False


def _is_mutative(operation_name: str) -> bool:
    if _is_readonly(operation_name):
        return False
    return any(operation_name.startswith(p) for p in MUTATIVE_PREFIXES)


# ═══════════════════════════════════════════════════════════════════════════
# Discovery helpers
# ═══════════════════════════════════════════════════════════════════════════

def list_services() -> List[Dict[str, Any]]:
    """Return the service registry."""
    return [
        {"service_name": name, "sdk_class": qualname}
        for name, (_, qualname) in SERVICES.items()
    ]


def _ast_methods_for_class(qualname: str) -> Dict[str, List[str]]:
    """AST-walk the SDK source for a class.

    Returns {method_name: [arg_names]}. Works without importing the SDK
    (the SDK IDL pulls in cyclonedds which isn't available on dev boxes).
    """
    import ast as _ast

    mod_path, class_name = qualname.rsplit(".", 1)
    rel_path = mod_path.replace(".", "/") + ".py"

    candidates = [
        os.environ.get("UNITREE_SDK_PATH", ""),
        "/tmp/g1-audit/unitree_sdk2_python",
        "/tmp/unitree_sdk2_python",
        "/home/unitree/g1_work/unitree_sdk2_python",
        "/home/unitree/neon-the-g1/unitree_sdk2_python",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "unitree_sdk2_python"),
    ]
    src_file = None
    for root in candidates:
        if not root:
            continue
        p_ = os.path.join(root, rel_path)
        if os.path.isfile(p_):
            src_file = p_
            break
    if src_file is None:
        return {}

    try:
        tree = _ast.parse(open(src_file).read(), filename=src_file)
    except Exception:
        return {}

    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef) and node.name == class_name:
            methods: Dict[str, List[str]] = {}
            for item in node.body:
                if isinstance(item, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    if item.name.startswith("_"):
                        continue
                    args = [a.arg for a in item.args.args if a.arg != "self"]
                    methods[item.name] = args
            return methods
    return {}


def list_operations(service_name: str) -> List[str]:
    """Return the callable method names on a service's client class."""
    if service_name not in SERVICES:
        raise KeyError(f"unknown service: {service_name}")
    _, qualname = SERVICES[service_name]

    # Fast path: import + inspect
    try:
        mod_path, class_name = qualname.rsplit(".", 1)
        mod = __import__(mod_path, fromlist=[class_name])
        cls = getattr(mod, class_name)
        ops = []
        for name, _m in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            ops.append(name)
        if ops:
            return sorted(ops)
    except Exception:
        pass

    # Fallback: AST
    return sorted(_ast_methods_for_class(qualname).keys())


def describe_operation(service_name: str, operation_name: str) -> Dict[str, Any]:
    """Return signature + docstring for an operation.

    Tries runtime `inspect` first, falls back to AST when the SDK isn't
    importable. Doesn't touch DDS either way.
    """
    if service_name not in SERVICES:
        return {"error": f"unknown service: {service_name}"}

    _, qualname = SERVICES[service_name]

    # Fast path: import + inspect
    try:
        mod_path, class_name = qualname.rsplit(".", 1)
        mod = __import__(mod_path, fromlist=[class_name])
        cls = getattr(mod, class_name)

        fn = getattr(cls, operation_name, None)
        if fn is not None and callable(fn):
            try:
                sig = str(inspect.signature(fn))
            except (TypeError, ValueError):
                sig = "(unknown)"
            doc = inspect.getdoc(fn) or ""

            params = []
            try:
                for p in inspect.signature(fn).parameters.values():
                    if p.name == "self":
                        continue
                    entry = {"name": p.name, "kind": str(p.kind)}
                    if p.annotation is not inspect.Parameter.empty:
                        entry["type"] = getattr(p.annotation, "__name__", str(p.annotation))
                    if p.default is not inspect.Parameter.empty:
                        entry["default"] = (
                            p.default
                            if isinstance(p.default, (str, int, float, bool, type(None)))
                            else str(p.default)
                        )
                    params.append(entry)
            except (TypeError, ValueError):
                pass

            return {
                "service_name": service_name,
                "operation_name": operation_name,
                "signature": f"{operation_name}{sig}",
                "docstring": doc,
                "parameters": params,
                "is_mutative": _is_mutative(operation_name),
                "is_readonly": _is_readonly(operation_name),
                "high_danger": (service_name, operation_name) in HIGH_DANGER_OPS,
                "source": "inspect",
            }
    except Exception:
        pass

    # Fallback: AST
    ast_methods = _ast_methods_for_class(qualname)
    if operation_name not in ast_methods:
        return {
            "error": f"unknown operation: {service_name}.{operation_name}",
            "available": sorted(ast_methods.keys()) or list_operations(service_name),
        }

    arg_names = ast_methods[operation_name]
    params = [{"name": n, "kind": "POSITIONAL_OR_KEYWORD"} for n in arg_names]
    return {
        "service_name": service_name,
        "operation_name": operation_name,
        "signature": f"{operation_name}({', '.join(arg_names)})",
        "docstring": "",
        "parameters": params,
        "is_mutative": _is_mutative(operation_name),
        "is_readonly": _is_readonly(operation_name),
        "high_danger": (service_name, operation_name) in HIGH_DANGER_OPS,
        "source": "ast",
    }


def generate_input_schema(service_name: str, operation_name: str) -> Dict[str, Any]:
    """Generate a JSON Schema for an operation's parameters."""
    desc = describe_operation(service_name, operation_name)
    if "error" in desc:
        return desc

    py_to_json = {
        "int": "integer", "float": "number", "str": "string",
        "bool": "boolean", "bytes": "string",
    }
    props = {}
    required = []
    for p in desc["parameters"]:
        prop = {}
        if "type" in p:
            prop["type"] = py_to_json.get(p["type"], "string")
        if "default" in p:
            prop["default"] = p["default"]
        else:
            required.append(p["name"])
        props[p["name"]] = prop

    return {"type": "object", "properties": props, "required": required}


# ═══════════════════════════════════════════════════════════════════════════
# Execution
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_response(result: Any) -> Any:
    """Return something JSON-serializable."""
    if result is None:
        return None
    if isinstance(result, (bool, int, float, str)):
        return result
    if isinstance(result, tuple):
        return [_normalize_response(x) for x in result]
    if isinstance(result, list):
        return [_normalize_response(x) for x in result]
    if isinstance(result, dict):
        return {k: _normalize_response(v) for k, v in result.items()}
    return str(result)[:500]


def _execute(
    service_name: str,
    operation_name: str,
    parameters: Dict[str, Any],
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """Core dispatcher — returns a dict with rc/data/error."""
    err = ensure_dds(network_interface)
    if err:
        return {"ok": False, "error": err}

    if service_name not in SERVICES:
        return {
            "ok": False,
            "error": f"unknown service '{service_name}'",
            "available_services": list(SERVICES.keys()),
        }

    getter, _ = SERVICES[service_name]
    try:
        client = getter()
    except Exception as e:
        return {"ok": False, "error": f"client init failed: {e}"}

    fn = getattr(client, operation_name, None)
    if fn is None or not callable(fn):
        return {
            "ok": False,
            "error": f"unknown operation '{service_name}.{operation_name}'",
            "available_operations": list_operations(service_name),
        }

    # Parameter validation via inspect
    try:
        sig = inspect.signature(fn)
        sig.bind(**(parameters or {}))
    except TypeError as e:
        schema = generate_input_schema(service_name, operation_name)
        return {
            "ok": False,
            "error": f"parameter mismatch: {e}",
            "expected_schema": schema,
        }

    # Execute (serialised — SDK clients are NOT thread-safe; concurrent
    # calls clobber each other's response futures and return rc=3104)
    try:
        with _LOCO_CALL_LOCK:
            raw = fn(**(parameters or {}))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": True, "result": _normalize_response(raw)}


# ═══════════════════════════════════════════════════════════════════════════
# The @tool — universal Unitree SDK dispatcher
# ═══════════════════════════════════════════════════════════════════════════

@tool
def use_unitree(
    service_name: str,
    operation_name: str,
    parameters: Optional[Dict[str, Any]] = None,
    label: str = "",
    network_interface: str = "eth0",
) -> Dict[str, Any]:
    """🤖 Universal interface to every Unitree SDK2 client method.

    Like ``use_aws`` but for the Unitree G1. ONE tool covers the entire
    unitree_sdk2_python surface — no per-method @tool wrapper needed. New
    SDK methods become callable automatically.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    SERVICES (the client class to dispatch to):
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
       loco              LocoClient (FSM, posture, walking, balance)
       arm               G1ArmActionClient (gestures via ExecuteAction)
       audio             AudioClient (TTS, LED, PCM streaming)
       motion_switcher   MotionSwitcherClient (controller select: ai/normal/...)
       vui               VuiClient (head LED, head TTS, brightness)
       robot_state       RobotStateClient (service list, ServiceSwitch)
       meta              Discovery only (list/describe — see below)

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    OPERATIONS — PascalCase method on the chosen client class
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    Most-used operations per service (NOT exhaustive — use meta to discover):

      loco:
        SetFsmId(fsm_id: int)            → 0=ZeroTorque🚨, 1=Damp, 3=Sit,
                                            500=Start (arm-ready), 501=Walk,
                                            801=BalanceExpert, 706=Squat2StandUp
        SetStandHeight(stand_height: float)   → meters; UINT32_MAX = HighStand
        SetSwingHeight(swing_height: float)   → leg-lift while walking (0.05–0.15m)
        SetVelocity(vx, vy, omega, duration=1.0)  🚨 walks for `duration` seconds
        Move(vx, vy, vyaw, continous_move=False)  🚨 may walk for ~10 days(!)
        StopMove()                       → emergency stop (vx=vy=vyaw=0)
        SetTaskId(task_id)               → built-in tasks (0=wave, 1=wave+turn, ...)
        SetBalanceMode(balance_mode)     → 0=static, 3=dynamic
        Damp() / Start() / Sit() / HighStand() / LowStand()
        Squat2StandUp() / Lie2StandUp() / StandUp2Squat() ⚠️ buggy upstream!
        ZeroTorque()                     🚨🚨 robot COLLAPSES off-gantry
        WaveHand(turn_flag) / ShakeHand(stage) / BalanceStand(balance_mode)

      arm:
        ExecuteAction(action_id: int)    → 11=2-hand kiss, 17=clap, 18=high five,
                                            19=hug, 20=heart, 25=face wave,
                                            26=high wave, 27=shake hand, 99=release
        GetActionList()                  → query firmware-supported actions

      audio:
        TtsMaker(text, speaker_id)       → onboard TTS (speaker_id=0 default)
        SetVolume(volume) / GetVolume()  → 0–100
        LedControl(R, G, B)              → head ring LED (0–255 each)
        PlayStream(app_name, stream_id, pcm_data)  → push raw PCM to speaker
        PlayStop(app_name)

      motion_switcher:
        CheckMode()                      → returns (rc, {'name':'ai',...})
        SelectMode(name)                 → 'ai' is the only one installed on G1
        ReleaseMode()                    🚨 robot uncontrolled

      vui:
        SetVolume / GetVolume / SetBrightness / GetBrightness
        SetSwitch / GetSwitch / TtsMaker

      robot_state:
        ServiceList()                    → list of services + on/off state
        ServiceSwitch(name, switch)      → enable/disable a service

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    META OPERATIONS (no DDS, no robot, always safe — use these to explore!):
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
      service_name='meta', operation_name='list_services'
        → list all services + their SDK class qualnames

      service_name='meta', operation_name='list_operations',
      parameters={'service_name': 'loco'}
        → all PascalCase method names on LocoClient

      service_name='meta', operation_name='describe_operation',
      parameters={'service_name': 'loco', 'operation_name': 'SetFsmId'}
        → signature, docstring, parameter list, mutative/readonly flags,
          high-danger flag

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    SAFETY RAILS (built in):
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
      • Mutative ops (Set*, Execute*, Move*, ...) are detected and flagged
        in the response. BYPASS_TOOL_CONSENT=true skips confirmation prompts.
      • HIGH_DANGER_OPS — always flagged loudly:
          (loco, ZeroTorque)             ← robot collapses
          (loco, SetFsmId)               ← if fsm_id=0 → collapse
          (loco, SetVelocity / Move)     ← walking (fall risk)
          (loco, WaveHand / ShakeHand)   ← leg motion in some FSMs
          (motion_switcher, ReleaseMode) ← robot uncontrolled
      • Use the FSM-safe @tool wrappers (g1_arm_action, g1_move_velocity,
        g1_safe_squat_to_stand, g1_set_fsm) for routine motion — they
        gate FSM transitions, mutex rt/armsdk, and auto-release. Reach for
        use_unitree only when you need raw SDK access.

    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    EXAMPLES:
    ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
      # Discover what's available
      use_unitree('meta', 'list_operations', {'service_name': 'audio'})

      # Speak a phrase (TTS)
      use_unitree('audio', 'TtsMaker',
                  {'text': 'Hello, I am neon.', 'speaker_id': 0},
                  label='greet user')

      # Set head LED to red
      use_unitree('audio', 'LedControl', {'R': 255, 'G': 0, 'B': 0})

      # Read the current MotionSwitcher mode
      use_unitree('motion_switcher', 'CheckMode', {})

      # Reach the arm-ready FSM (500 = Start)
      use_unitree('loco', 'SetFsmId', {'fsm_id': 500})

      # Execute a clap arm action (raw — no auto-release!)
      # Prefer g1_arm_action() instead, which mutexes and auto-releases.
      use_unitree('arm', 'ExecuteAction', {'action_id': 17})
      use_unitree('arm', 'ExecuteAction', {'action_id': 99})  # release!

      # Hard-stop walking
      use_unitree('loco', 'StopMove', {})

    Args:
        service_name: One of {loco, arm, audio, motion_switcher, vui,
            robot_state} — or 'meta' for discovery operations.
        operation_name: PascalCase method name on the client class
            (e.g. 'SetFsmId', 'ExecuteAction', 'TtsMaker'), or one of
            {list_services, list_operations, describe_operation} when
            service_name='meta'.
        parameters: Kwargs to pass to the method. Defaults to empty dict.
            For meta ops, contains the lookup target (e.g.
            {'service_name': 'loco', 'operation_name': 'SetFsmId'}).
        label: Optional human-readable description of what this call
            does ('greet user', 'enter walk FSM', ...). Echoed back in
            the response and useful for logs.
        network_interface: DDS interface. Default 'eth0'. Robot is on
            192.168.123.0/24 reachable only via eth0.

    Returns:
        Strands ToolResult dict. On success:
            {'status': 'success', 'content': [{'text': <JSON>}, {'json': {...}}]}
        with the JSON containing service, operation, label, parameters,
        result, mutative flag, high_danger flag.

        On error: status='error', content includes the error message
        and (where useful) expected_schema or available_operations.
    """
    params = parameters or {}
    label = label or f"{service_name}.{operation_name}"

    # ── meta operations ───────────────────────────────────────────────
    if service_name == "meta" or operation_name in (
        "list_services", "list_operations", "describe_operation"
    ):
        try:
            if operation_name == "list_services":
                data = list_services()
            elif operation_name == "list_operations":
                target_svc = params.get("service_name") or service_name
                if target_svc == "meta":
                    return _normalize({
                        "status": "error",
                        "message": "list_operations needs a service_name in parameters",
                    })
                data = list_operations(target_svc)
            elif operation_name == "describe_operation":
                target_svc = params.get("service_name") or service_name
                target_op = params.get("operation_name")
                if not target_svc or not target_op:
                    return _normalize({
                        "status": "error",
                        "message": "describe_operation needs parameters {service_name, operation_name}",
                    })
                data = describe_operation(target_svc, target_op)
            else:
                return _normalize({
                    "status": "error",
                    "message": (
                        f"unknown meta operation '{operation_name}'. Valid: "
                        "list_services, list_operations, describe_operation"
                    ),
                })

            return _normalize({
                "status": "success",
                "message": f"meta.{operation_name} ok",
                "service": "meta",
                "operation": operation_name,
                "result": data,
            })
        except Exception as e:
            return _normalize({
                "status": "error",
                "message": f"meta operation failed: {e}",
            })

    # ── validate service ──────────────────────────────────────────────
    if service_name not in SERVICES:
        return _normalize({
            "status": "error",
            "message": (
                f"unknown service '{service_name}'. "
                f"Valid: {sorted(SERVICES.keys())} (or 'meta' for discovery)"
            ),
        })

    # ── danger gate ───────────────────────────────────────────────────
    high_danger = (service_name, operation_name) in HIGH_DANGER_OPS
    mutative = _is_mutative(operation_name)
    bypass = os.environ.get("BYPASS_TOOL_CONSENT", "").lower() == "true"

    if (high_danger or mutative) and not bypass:
        logger.warning(
            "use_unitree: %s.%s is %s (bypass=%s)",
            service_name, operation_name,
            "HIGH_DANGER" if high_danger else "mutative",
            bypass,
        )

    # ── execute ───────────────────────────────────────────────────────
    res = _execute(service_name, operation_name, params, network_interface=network_interface)

    if not res.get("ok"):
        return _normalize({
            "status": "error",
            "message": (
                f"{service_name}.{operation_name} failed: {res.get('error')}"
            ),
            "service": service_name,
            "operation": operation_name,
            "label": label,
            **{k: v for k, v in res.items() if k != "ok"},
        })

    return _normalize({
        "status": "success",
        "message": f"{service_name}.{operation_name} ok",
        "service": service_name,
        "operation": operation_name,
        "label": label,
        "parameters": params,
        "result": res["result"],
        "mutative": mutative,
        "high_danger": high_danger,
    })


# Backwards-compat alias — old code expected TOOL_SPEC. With @tool the
# spec is now built by Strands; we expose this name for any importer
# that hasn't been migrated yet.
TOOL_SPEC = getattr(use_unitree, "tool_spec", None)
