"""
📱 phone — control an ADB-connected Android device from neon.

Lets the robot drive a phone: wake, unlock (PIN), open URLs/apps, tap, swipe
(incl. dating-app left/right), capture the screen, and push what it sees to
Telegram so a human can watch remotely.

Requires:
    - `adb` on PATH, device authorized (`adb devices` shows `device`).
    - For telegram push: TELEGRAM_BOT_TOKEN (+ TELEGRAM_DEFAULT_CHAT_ID or pass chat_id).

Design notes:
    - This app is a WebGL/canvas app in Chrome — uiautomator dumps NO text, so
      we rely on screenshots, not view hierarchy.
    - A dating swipe is a horizontal `input swipe`. Duration ~300ms feels right.
      LEFT  = reject  (start right → end left).
      RIGHT = like    (start left  → end right).
    - screencap → /sdcard → adb pull → local /tmp. PNG ~1.5MB at 1080x2410.

Safety: phone control is non-destructive but DOES tap/swipe real UI. The PIN is
only sent on an explicitly locked screen.
"""
from __future__ import annotations

import os
import time
import shutil
import subprocess
import importlib.util
from pathlib import Path
from typing import Dict, Any, List, Optional

from strands import tool

# ── config ──────────────────────────────────────────────────────────────
ADB = os.environ.get("ADB_BINARY", "adb")
SHOT_DIR = Path(os.environ.get("PHONE_SHOT_DIR", "/tmp/robot_shots"))
SHOT_DIR.mkdir(parents=True, exist_ok=True)
DATING_APP_URL = "https://albertozhao.github.io/robot-dating-app/"


def _pin(explicit: str = "") -> str:
    """Unlock PIN: the explicit argument, else the PHONE_PIN env var (never in git)."""
    return (explicit or os.environ.get("PHONE_PIN", "")).strip()

_ROOT = Path(__file__).resolve().parent.parent


# ── low-level adb helpers ───────────────────────────────────────────────
def _adb(*args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)


def _shell(cmd: str, timeout: float = 30.0) -> str:
    return _adb("shell", cmd, timeout=timeout).stdout


def _device_ok() -> Optional[str]:
    if not shutil.which(ADB):
        return f"adb not found on PATH ('{ADB}')."
    out = _adb("devices").stdout
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip() and l.split()[-1] == "device"]
    if not devs:
        return f"No authorized adb device. `adb devices` says:\n{out}"
    return None


def _screen_size() -> tuple[int, int]:
    out = _shell("wm size")  # "Physical size: 1080x2410"
    try:
        wh = out.strip().split(":")[-1].strip().split("x")
        return int(wh[0]), int(wh[1])
    except Exception:
        return 1080, 2410


def _is_locked() -> bool:
    out = _shell("dumpsys deviceidle | grep mScreenLocked")
    if "mScreenLocked=" in out:
        return "mScreenLocked=true" in out
    # fallback: keyguard
    kg = _shell("dumpsys window | grep -i mDreamingLockscreen")
    return "mDreamingLockscreen=true" in kg


def _current_focus() -> str:
    out = _shell("dumpsys window | grep mCurrentFocus")
    return out.strip()


# ── telegram push (import telegram.py directly, no tools/__init__ DDS pull) ──
_TG = None
def _telegram_mod():
    global _TG
    if _TG is None:
        spec = importlib.util.spec_from_file_location(
            "neon_telegram_phone", str(_ROOT / "tools" / "telegram.py"))
        _TG = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TG)
    return _TG


def _push_telegram(path: str, caption: str, chat_id: Optional[str]) -> str:
    cid = chat_id or os.environ.get("TELEGRAM_DEFAULT_CHAT_ID")
    if not cid:
        return "no chat_id (set TELEGRAM_DEFAULT_CHAT_ID or pass chat_id)"
    try:
        return _telegram_mod().telegram(
            action="send_photo", chat_id=cid, file_path=path,
            caption=caption, parse_mode="")
    except Exception as e:
        return f"telegram push failed: {e}"


def _capture(tag: str) -> str:
    """screencap on device → pull to local /tmp → return local path."""
    ts = time.strftime("%H%M%S")
    remote = f"/sdcard/neon_{tag}_{ts}.png"
    local = str(SHOT_DIR / f"neon_{tag}_{ts}.png")
    _shell(f"screencap -p {remote}")
    _adb("pull", remote, local)
    _shell(f"rm -f {remote}")
    return local


# ── Chrome DevTools Protocol (CDP) — reliable button clicks on canvas/SPA apps ──
# The robot-dating-app is a Vite/React SPA; swipe gestures on the WebGL surface
# are flaky, but the like/nope buttons are real DOM (.act--like / .act--nope).
# Clicking them via JS .click() is 100% layout-independent. Requires Chrome
# remote debugging exposed over adb (we set it up on demand).
_CDP_PORT = 9222


def _cdp_setup() -> Optional[str]:
    """Forward Chrome's devtools socket to localhost:9222. Returns error or None."""
    try:
        _adb("forward", f"tcp:{_CDP_PORT}", "localabstract:chrome_devtools_remote")
        return None
    except Exception as e:
        return f"adb forward failed: {e}"


def _cdp_eval(expr: str) -> Dict[str, Any]:
    """Evaluate JS in the robot-dating-app page via CDP. Returns {ok, value|error}."""
    import json as _j, urllib.request as _u
    try:
        import websocket  # websocket-client
    except Exception:
        return {"ok": False, "error": "websocket-client not installed"}
    try:
        pages = _j.load(_u.urlopen(f"http://localhost:{_CDP_PORT}/json", timeout=5))
    except Exception as e:
        return {"ok": False, "error": f"CDP /json unreachable: {e}"}
    cand = [p for p in pages if "albertozhao" in p.get("url", "")] or \
           [p for p in pages if p.get("type") == "page"]
    if not cand:
        return {"ok": False, "error": "no matching page in CDP"}
    try:
        # suppress_origin=True dodges Chrome's 403 origin check
        ws = websocket.create_connection(
            cand[0]["webSocketDebuggerUrl"], suppress_origin=True, timeout=10)
    except Exception as e:
        return {"ok": False, "error": f"ws connect failed: {e}"}
    _id = {"n": 0}
    def cmd(m, prm=None):
        _id["n"] += 1
        ws.send(_j.dumps({"id": _id["n"], "method": m, "params": prm or {}}))
        while True:
            r = _j.loads(ws.recv())
            if r.get("id") == _id["n"]:
                return r
    try:
        cmd("Runtime.enable")
        r = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        val = r.get("result", {}).get("result", {}).get("value")
        return {"ok": True, "value": val}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        try: ws.close()
        except Exception: pass


def _click_dom(selector: str) -> Dict[str, Any]:
    """Click a DOM element by CSS selector via CDP JS .click()."""
    _cdp_setup()
    expr = ("(()=>{const e=document.querySelector('%s');"
            "if(!e)return 'no element: %s';e.click();return 'clicked %s';})()"
            % (selector, selector, selector))
    return _cdp_eval(expr)


# ── G1 arm gesture (raise arm during interaction) ──
def _arm(action_id: int, settle: float = 2.5) -> Dict[str, Any]:
    """Fire a G1 arm gesture (e.g. 15=hands up, 20=heart) and auto-release.

    rt/armsdk is single-writer: firing gestures back-to-back yields rc=7400
    (topic occupied). So we (a) release any prior hold first, (b) fire with
    auto_release, (c) sleep `settle`s to let the topic free before the next call.
    Imported lazily so phone control works even without the robot/DDS up."""
    try:
        from .g1_arm import g1_arm_action, g1_release_arm
        try:
            g1_release_arm()          # clear any prior hold
            time.sleep(0.4)
        except Exception:
            pass
        r = g1_arm_action(action_id=action_id, auto_release=True)
        time.sleep(settle)            # let rt/armsdk free up before next gesture
        return r
    except Exception as e:
        return {"status": "error", "message": f"arm gesture failed: {e}"}


# ═══════════════════════════════════════════════════════════════════════
@tool
def phone(
    action: str = "status",
    pin: str = "",
    url: str = "",
    text: str = "",
    direction: str = "left",
    x: int = 0,
    y: int = 0,
    duration_ms: int = 300,
    count: int = 1,
    caption: str = "",
    chat_id: str = "",
    to_telegram: bool = True,
    settle_s: float = 2.0,
    button: str = "like",
    arm_action_id: int = 15,
    do_arm: bool = True,
) -> Dict[str, Any]:
    """
    📱 Control an ADB-connected Android phone (wake / unlock / open / swipe / screenshot → Telegram).

    Actions:
      - "status"       : device + lock + screen-size + current focus.
      - "unlock"       : wake, swipe-up, and if locked enter `pin` (KEYCODE_ENTER).
                         No-op (just wake) if already unlocked.
      - "open"         : open `url` in browser (VIEW intent). Defaults to the
                         robot-dating-app if url is empty.
      - "screenshot"   : capture the screen; push to Telegram if to_telegram.
      - "click_button" : reliably click the like(heart)/nope button via Chrome
                         DevTools (works on the WebGL canvas where swipe fails).
      - "dating_arms"  : 🤖 raise G1 arm (hands up) + click heart + screenshot →
                         Telegram, repeated `count`× — watch neon "date".
      - "swipe"        : dating-style horizontal swipe. direction=left(reject)/
                         right(like)/up/down. Repeats `count` times, screenshots
                         each and pushes to Telegram.
      - "tap"          : tap at (x, y).
      - "type"         : type `text` into the focused field.
      - "key"          : send a keyevent named by `text` (e.g. "BACK", "HOME").
      - "dating_demo"  : full flow — unlock(pin) → open dating app →
                         screenshot → swipe left+right (count each) → all
                         screenshots to Telegram. The one-call "give it a try".

    Args:
      pin:          unlock PIN. Empty means the PHONE_PIN env var. Only sent if
                    the screen is locked; never echoed back.
      url:          URL for "open" (empty → robot-dating-app).
      text:         text for "type", or key name for "key".
      direction:    swipe direction: left|right|up|down.
      x, y:         tap coordinates.
      duration_ms:  swipe duration (ms). ~300 = natural dating swipe.
      count:        repeat count for swipe / per-direction in dating_demo.
      caption:      caption prefix for Telegram photos.
      chat_id:      Telegram chat (empty → TELEGRAM_DEFAULT_CHAT_ID).
      to_telegram:  push screenshots to Telegram (default True).
      settle_s:     seconds to wait for UI to settle after an action.
      button:       'like'/'heart' or 'nope' — which dating button to click.
      arm_action_id: G1 arm gesture id for dating_arms (15=hands up, 20=heart).
      do_arm:       raise the G1 arm each round in dating_arms (default True).
    """
    action = (action or "status").strip().lower()
    pin = _pin(pin)

    err = _device_ok()
    if err:
        return {"status": "error", "message": err}

    W, H = _screen_size()
    CY = H // 2
    results: List[str] = []

    def snap_and_push(tag: str, cap: str) -> Dict[str, str]:
        p = _capture(tag)
        entry = {"tag": tag, "path": p}
        if to_telegram:
            entry["telegram"] = _push_telegram(p, (caption + " " + cap).strip(), chat_id or None)
        return entry

    # ── status ──
    if action == "status":
        return {
            "status": "success",
            "locked": _is_locked(),
            "screen": f"{W}x{H}",
            "focus": _current_focus(),
            "message": "Device reachable.",
        }

    # ── unlock ──
    if action == "unlock":
        _shell("input keyevent KEYCODE_WAKEUP")
        time.sleep(0.3)
        if not _is_locked():
            _shell("input keyevent KEYCODE_HOME")
            return {"status": "success", "locked": False, "message": "Already unlocked (woke screen)."}
        _shell(f"input swipe {W//2} {int(H*0.75)} {W//2} {int(H*0.25)}")
        time.sleep(0.4)
        if pin:
            _shell(f"input text {pin}")
            time.sleep(0.2)
            _shell("input keyevent KEYCODE_ENTER")
            time.sleep(1.0)
        locked = _is_locked()
        _shell("input keyevent KEYCODE_HOME")
        return {"status": "success" if not locked else "error",
                "locked": locked,
                "message": "Unlocked." if not locked else "Still locked: wrong or missing PIN (PHONE_PIN)?"}

    # ── open ──
    if action == "open":
        target = url or DATING_APP_URL
        _shell(f'am start -a android.intent.action.VIEW -d "{target}"')
        time.sleep(max(settle_s, 3.0))
        out = {"status": "success", "url": target, "focus": _current_focus()}
        if to_telegram:
            out["shot"] = snap_and_push("open", f"opened {target}")
        return out

    # ── screenshot ──
    if action == "screenshot":
        shot = snap_and_push("shot", caption or "screenshot")
        return {"status": "success", "shot": shot}

    # ── tap ──
    if action == "tap":
        _shell(f"input tap {x} {y}")
        time.sleep(settle_s)
        return {"status": "success", "message": f"tapped ({x},{y})",
                "shot": snap_and_push("tap", f"tap {x},{y}") if to_telegram else None}

    # ── type ──
    if action == "type":
        if not text:
            return {"status": "error", "message": "type needs text="}
        _shell(f"input text {text.replace(' ', '%s')}")
        return {"status": "success", "message": f"typed: {text}"}

    # ── key ──
    if action == "key":
        if not text:
            return {"status": "error", "message": "key needs text=<KEYCODE name>"}
        kc = text if text.startswith("KEYCODE_") else f"KEYCODE_{text.upper()}"
        _shell(f"input keyevent {kc}")
        return {"status": "success", "message": f"key: {kc}"}

    # ── swipe ──
    if action == "swipe":
        shots = []
        for i in range(max(1, count)):
            if direction == "left":       x1, y1, x2, y2 = int(W*0.8), CY, int(W*0.2), CY
            elif direction == "right":    x1, y1, x2, y2 = int(W*0.2), CY, int(W*0.8), CY
            elif direction == "up":       x1, y1, x2, y2 = W//2, int(H*0.7), W//2, int(H*0.3)
            elif direction == "down":     x1, y1, x2, y2 = W//2, int(H*0.3), W//2, int(H*0.7)
            else: return {"status": "error", "message": f"bad direction '{direction}'"}
            _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
            time.sleep(settle_s)
            if to_telegram:
                shots.append(snap_and_push(f"swipe_{direction}_{i+1}", f"swipe {direction} #{i+1}"))
        return {"status": "success", "direction": direction, "count": count, "shots": shots}

    # ── dating_demo: the full "give it a try" flow ──
    if action == "dating_demo":
        steps: Dict[str, Any] = {}
        # 1. unlock
        _shell("input keyevent KEYCODE_WAKEUP"); time.sleep(0.3)
        if _is_locked():
            _shell(f"input swipe {W//2} {int(H*0.75)} {W//2} {int(H*0.25)}"); time.sleep(0.4)
            if pin:
                _shell(f"input text {pin}"); time.sleep(0.2)
                _shell("input keyevent KEYCODE_ENTER"); time.sleep(1.0)
        steps["locked_after_unlock"] = _is_locked()
        # 2. open app
        _shell(f'am start -a android.intent.action.VIEW -d "{DATING_APP_URL}"')
        time.sleep(max(settle_s, 4.0))
        steps["opened"] = snap_and_push("demo_open", "opened robot-dating-app")
        # 3. swipe left(reject) then right(like), count each
        demo_shots = []
        for direction in ("left", "right"):
            for i in range(max(1, count)):
                if direction == "left":  x1, y1, x2, y2 = int(W*0.8), CY, int(W*0.2), CY
                else:                     x1, y1, x2, y2 = int(W*0.2), CY, int(W*0.8), CY
                _shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
                time.sleep(settle_s)
                verb = "reject" if direction == "left" else "like"
                demo_shots.append(snap_and_push(f"demo_{direction}_{i+1}",
                                                 f"swipe {direction} ({verb}) #{i+1}"))
        steps["swipes"] = demo_shots
        return {"status": "success",
                "message": "Dating demo complete — unlock → open → swipe L/R, screenshots pushed to Telegram.",
                "steps": steps}

    # ── click_button: reliable DOM click on the dating-app like/nope buttons ──
    if action == "click_button":
        sel = ".act--like" if button in ("like", "heart") else ".act--nope"
        res = _click_dom(sel)
        time.sleep(settle_s)
        out = {"status": "success" if res.get("ok") else "error",
               "button": button, "selector": sel, "cdp": res}
        if to_telegram:
            out["shot"] = snap_and_push(f"click_{button}", f"clicked {button} button")
        return out

    # ── dating_arms: raise G1 arm + click heart + screenshot, repeated ──
    # This is the "watch the robot while it swipes" flow the user asked for:
    # for each round: raise the arm (hands up), then click the heart (like),
    # then screenshot → Telegram so you can watch what neon does.
    if action == "dating_arms":
        # make sure app is open
        foc = _current_focus()
        if "chrome" not in foc.lower():
            _shell(f'am start -a android.intent.action.VIEW -d "{DATING_APP_URL}"')
            time.sleep(max(settle_s, 4.0))
        _cdp_setup()
        rounds = []
        for i in range(max(1, count)):
            round_info = {"round": i + 1}
            # 1. raise the G1 arm so we can watch
            if do_arm:
                round_info["arm"] = _arm(arm_action_id)
            # 2. click the heart (like) — reliable via CDP
            sel = ".act--like" if button in ("like", "heart") else ".act--nope"
            round_info["click"] = _click_dom(sel)
            time.sleep(settle_s)
            # 3. screenshot → telegram
            if to_telegram:
                verb = "like ❤" if button in ("like", "heart") else "nope"
                round_info["shot"] = snap_and_push(
                    f"arms_{button}_{i+1}", f"round {i+1}: arm up + {verb}")
            rounds.append(round_info)
        return {"status": "success",
                "message": (f"dating_arms x{count}: raised arm (id={arm_action_id}) + "
                            f"clicked {button} each round, screenshots pushed to Telegram."),
                "rounds": rounds}

    return {"status": "error",
            "message": f"Unknown action '{action}'. "
                       "Use status|unlock|open|screenshot|swipe|click_button|tap|type|key|dating_demo|dating_arms."}
