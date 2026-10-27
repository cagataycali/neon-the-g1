#!/usr/bin/env python3
"""📶 neon-wifi — keep NEON online, and open a setup hotspot when it can't be.

Runs as root under systemd (scripts/systemd/neon-wifi.service). Python 3.8,
stdlib only (it runs on the Jetson host, not in the compose stack).

  client mode   wlan0 joins the known networks NetworkManager has saved
                (/etc/NetworkManager/system-connections — that IS the store).
                `neon_net` / cagatay4321 is always ensured, so a phone hotspot
                with that name brings the robot online with no setup.
  offline       no internet on wlan0 for GRACE_S → try every visible saved
                network once → still offline → AP mode.
  AP mode       hotspot `NEON` (WPA2, cagatay4321) at 10.42.0.1. Join it and
                the phone's captive sheet opens the picker (any http:// URL
                works too, e.g. http://10.42.0.1). Pick a network, type its
                password: the profile is saved and the robot switches. On
                failure the hotspot comes back with the error on the page.
                Every RETRY_S with no phone attached, NEON drops the hotspot
                and retries the saved networks (the radio can't do both).

Never touches eth0 (robot DDS, 192.168.123.0/24).
"""
import html
import json
import os
import socket
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

IFACE = os.environ.get("NEON_WIFI_IFACE", "wlan0")
AP_SSID = os.environ.get("NEON_AP_SSID", "NEON")
AP_PSK = os.environ.get("NEON_AP_PSK", "cagatay4321")
AP_CON = os.environ.get("NEON_AP_CON", "neon-ap")
HOME_SSID = os.environ.get("NEON_HOME_SSID", "neon_net")
HOME_PSK = os.environ.get("NEON_HOME_PSK", "cagatay4321")
PORT = int(os.environ.get("NEON_PORTAL_PORT", "8099"))
CHECK_S = float(os.environ.get("NEON_CHECK_S", "10"))
GRACE_S = float(os.environ.get("NEON_GRACE_S", "60"))
RETRY_S = float(os.environ.get("NEON_RETRY_S", "180"))
AP_NET = "10.42.0."  # NetworkManager ipv4.method=shared default
SAVED_PRIORITY = 50  # networks added from the portal
DNSMASQ_CONF = "/etc/NetworkManager/dnsmasq-shared.d/neon-captive.conf"
STATE_DIR = "/run/neon-wifi"
PROBES = (("1.1.1.1", 443), ("8.8.8.8", 53), ("9.9.9.9", 443))


def log(msg):
    print("neon-wifi: %s" % msg, flush=True)


# ── pure helpers (tested in tests/test_neon_wifi.py) ──────────────────────
def split_terse(line):
    """Split one `nmcli -t` line: ':' separates fields, '\\:' and '\\\\' are escapes."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def parse_scan(text, hide=()):
    """`nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list` → strongest-first, deduped."""
    nets = {}
    for ln in text.splitlines():
        p = split_terse(ln)
        if len(p) < 3:
            continue
        ssid = p[0].strip()
        if not ssid or ssid in hide:
            continue
        sig = int(p[1]) if p[1].isdigit() else 0
        if ssid not in nets or sig > nets[ssid]["signal"]:
            nets[ssid] = {"ssid": ssid, "signal": sig, "security": p[2].strip() or "open"}
    return sorted(nets.values(), key=lambda n: -n["signal"])


def order_candidates(known, scan):
    """Saved profiles whose SSID is on the air: priority first, then signal."""
    sig = {n["ssid"]: n["signal"] for n in scan}
    cands = [k for k in known if k["ssid"] in sig]
    return sorted(cands, key=lambda k: (-k["priority"], -sig[k["ssid"]]))


def count_stations(text):
    return sum(1 for ln in text.splitlines() if ln.startswith("Station "))


def client_allowed(ip):
    return ip.startswith(AP_NET) or ip in ("127.0.0.1", "::1")


# ── NetworkManager ────────────────────────────────────────────────────────
def run(*args, timeout=30):
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as e:
        return 127, "", str(e)


def nmcli(*args, timeout=30):
    return run("nmcli", *args, timeout=timeout)


def online():
    """A TCP connect pinned to wlan0 succeeds (eth0's default route can't fake it)."""
    for host, port in PROBES:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, 25, IFACE.encode())  # SO_BINDTODEVICE
            s.settimeout(3)
            s.connect((host, port))
            return True
        except OSError:
            pass
        finally:
            s.close()
    return False


def active_con():
    _, out, _ = nmcli("-t", "-f", "DEVICE,STATE,CONNECTION", "dev", "status")
    for ln in out.splitlines():
        p = split_terse(ln)
        if len(p) >= 3 and p[0] == IFACE and p[1] == "connected":
            return p[2]
    return ""


def known_networks():
    _, out, _ = nmcli("-t", "-f", "NAME,TYPE,AUTOCONNECT-PRIORITY", "con", "show")
    nets = []
    for ln in out.splitlines():
        p = split_terse(ln)
        if len(p) < 3 or p[1] != "802-11-wireless" or p[0] == AP_CON:
            continue
        _, g, _ = nmcli("-g", "802-11-wireless.ssid,802-11-wireless.mode", "con", "show", p[0])
        f = g.splitlines() + ["", ""]
        if f[1].strip() == "ap":
            continue
        try:
            prio = int(p[2])
        except ValueError:
            prio = 0
        nets.append({"name": p[0], "ssid": f[0].strip() or p[0], "priority": prio})
    return nets


def scan(rescan=True):
    _, out, _ = nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                      "--rescan", "yes" if rescan else "no", timeout=40)
    return parse_scan(out, hide=(AP_SSID,))


def save_network(ssid, psk):
    """Create/refresh a client profile named after the SSID. Returns the profile name."""
    name = ssid
    existing = {k["name"] for k in known_networks()}
    if name not in existing:
        rc, _, err = nmcli("con", "add", "type", "wifi", "ifname", IFACE,
                           "con-name", name, "ssid", ssid)
        if rc != 0:
            raise RuntimeError(err.strip() or "could not create the profile")
    mods = ["connection.autoconnect", "yes", "802-11-wireless.ssid", ssid,
            "802-11-wireless.mode", "infrastructure"]
    if psk:
        mods += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
    rc, _, err = nmcli("con", "modify", name, *mods)
    if rc != 0:
        raise RuntimeError(err.strip() or "could not save the profile")
    if not psk:  # open network: drop any old security block
        nmcli("con", "modify", name, "remove", "802-11-wireless-security")
    return name


def ensure_profiles():
    names = {k["name"]: k for k in known_networks()}
    if HOME_SSID not in names:
        log("creating %s profile" % HOME_SSID)
        nmcli("con", "add", "type", "wifi", "ifname", IFACE, "con-name", HOME_SSID,
              "ssid", HOME_SSID)
    nmcli("con", "modify", HOME_SSID, "connection.autoconnect", "yes",
          "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", HOME_PSK)
    rc, _, _ = nmcli("-t", "-f", "NAME", "con", "show", AP_CON)
    if rc != 0:
        log("creating %s profile (ssid %s)" % (AP_CON, AP_SSID))
        nmcli("con", "add", "type", "wifi", "ifname", IFACE, "con-name", AP_CON,
              "ssid", AP_SSID, "autoconnect", "no")
    nmcli("con", "modify", AP_CON, "connection.autoconnect", "no",
          "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
          "802-11-wireless.channel", "6", "ipv4.method", "shared",
          "ipv6.method", "ignore", "wifi-sec.key-mgmt", "wpa-psk",
          "wifi-sec.proto", "rsn", "wifi-sec.pairwise", "ccmp",
          "wifi-sec.group", "ccmp", "wifi-sec.psk", AP_PSK)
    # captive DNS: every name → the robot, so phones pop the picker sheet
    want = "# neon-wifi: AP clients resolve everything to the portal\naddress=/#/%s1\n" % AP_NET
    try:
        os.makedirs(os.path.dirname(DNSMASQ_CONF), exist_ok=True)
        cur = open(DNSMASQ_CONF).read() if os.path.exists(DNSMASQ_CONF) else ""
        if cur != want:
            with open(DNSMASQ_CONF, "w") as f:
                f.write(want)
    except OSError as e:
        log("captive dns conf: %s" % e)


REDIRECT = ["-t", "nat", "PREROUTING", "-i", IFACE, "-s", AP_NET + "0/24", "-p", "tcp",
            "--dport", "80", "-j", "REDIRECT", "--to-ports", str(PORT)]


def redirect(on):
    t, chain, rest = REDIRECT[:2], REDIRECT[2], REDIRECT[3:]
    while run("iptables", *t, "-C", chain, *rest)[0] == 0:  # drop any copies
        run("iptables", *t, "-D", chain, *rest)
    if on:
        run("iptables", *t, "-I", chain, "1", *rest)


# ── the daemon ────────────────────────────────────────────────────────────
class Wifi:
    def __init__(self):
        self.lock = threading.RLock()
        self.mode = "client"
        self.networks = []  # last scan
        self.scanned_at = 0.0
        self.message = ""  # last outcome shown on the portal
        self.last_online = time.time()  # boot: give NM GRACE_S to autoconnect
        self.ap_since = 0.0
        self.busy = ""

    def write_state(self):
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(STATE_DIR + "/state.json", "w") as f:
                json.dump(self.snapshot(), f)
        except OSError:
            pass

    def snapshot(self):
        return {"mode": self.mode, "active": active_con(), "message": self.message,
                "busy": self.busy, "networks": self.networks,
                "scanned_at": self.scanned_at, "ap_ssid": AP_SSID}

    def refresh_scan(self):
        nets = scan()
        if nets:
            self.networks, self.scanned_at = nets, time.time()

    def wait_online(self, s):
        end = time.time() + s
        while time.time() < end:
            if online():
                return True
            time.sleep(2)
        return False

    def try_known(self):
        """Every visible saved network once. True when wlan0 reaches the internet."""
        self.refresh_scan()
        for k in order_candidates(known_networks(), self.networks):
            log("trying %s" % k["name"])
            rc, _, err = nmcli("--wait", "30", "con", "up", "id", k["name"], timeout=45)
            if rc == 0 and self.wait_online(15):
                log("online via %s" % k["name"])
                return True
            log("%s: %s" % (k["name"], (err.strip() or "no internet")[:160]))
        return False

    def start_ap(self):
        log("starting hotspot %s" % AP_SSID)
        rc, _, err = nmcli("--wait", "30", "con", "up", "id", AP_CON, timeout=45)
        if rc != 0:
            log("hotspot failed: %s" % err.strip()[:200])
            return False
        redirect(True)
        self.mode, self.ap_since = "ap", time.time()
        return True

    def stop_ap(self):
        redirect(False)
        nmcli("--wait", "15", "con", "down", "id", AP_CON, timeout=25)
        self.mode = "client"

    def go_offline_path(self):
        if self.try_known():
            self.last_online = time.time()
            return
        if not self.start_ap():
            self.last_online = time.time()  # back off a full GRACE before retrying

    def tick(self):
        with self.lock:
            if self.busy:
                return
            if self.mode == "client":
                if online():
                    self.last_online = time.time()
                elif time.time() - self.last_online > GRACE_S:
                    log("no internet on %s for %ds" % (IFACE, GRACE_S))
                    self.go_offline_path()
            else:
                if active_con() != AP_CON:  # someone/something tore the hotspot down
                    self.mode = "client"
                    self.last_online = time.time()
                    return
                if time.time() - self.ap_since < RETRY_S:
                    return
                _, out, _ = run("iw", "dev", IFACE, "station", "dump")
                if count_stations(out):
                    self.ap_since = time.time()  # a phone is on the picker — leave it
                    return
                log("hotspot idle — retrying saved networks")
                self.stop_ap()
                self.go_offline_path()
            self.write_state()

    def connect(self, ssid, psk):
        """Portal action, runs in its own thread after the page has been sent."""
        with self.lock:
            self.busy = "connecting to %s" % ssid
            try:
                name = save_network(ssid, psk)
                nmcli("con", "modify", name, "connection.autoconnect-priority",
                      str(SAVED_PRIORITY))
                time.sleep(2)  # let the reply reach the phone
                self.stop_ap()
                rc, _, err = nmcli("--wait", "40", "con", "up", "id", name, timeout=55)
                if rc == 0:
                    ok = self.wait_online(15)
                    self.message = "Joined %s%s." % (ssid, "" if ok else " (no internet yet)")
                    log(self.message)
                    self.last_online = time.time()
                    return
                self.message = "Could not join %s: %s" % (ssid, (err.strip() or "failed")[:200])
                log(self.message)
                if not self.try_known():
                    self.start_ap()
                else:
                    self.last_online = time.time()
            except Exception as e:  # never leave the robot with no way back
                self.message = "Could not save %s: %s" % (ssid, e)
                log(self.message)
                if self.mode == "client" and not online():
                    self.start_ap()
            finally:
                self.busy = ""
                self.write_state()

    def rescan_from_ap(self):
        with self.lock:
            self.busy = "scanning"
            try:
                time.sleep(2)
                was_ap = self.mode == "ap"
                if was_ap:
                    self.stop_ap()
                    time.sleep(2)
                self.refresh_scan()
                self.message = "Found %d networks." % len(self.networks)
                if was_ap:
                    self.start_ap()
            finally:
                self.busy = ""
                self.write_state()

    def forget(self, name):
        with self.lock:
            if name in (HOME_SSID, AP_CON):
                self.message = "%s is built in and stays." % name
                return
            rc, _, err = nmcli("con", "delete", "id", name)
            self.message = "Forgot %s." % name if rc == 0 else err.strip()[:200]

    def loop(self):
        ensure_profiles()
        if active_con() == AP_CON:  # restarted while the hotspot was up
            self.mode, self.ap_since = "ap", time.time()
            redirect(True)
        else:
            redirect(False)
        threading.Thread(target=self.refresh_scan, daemon=True).start()
        log("up: iface=%s home=%s ap=%s portal=:%d" % (IFACE, HOME_SSID, AP_SSID, PORT))
        while True:
            try:
                self.tick()
            except Exception as e:
                log("tick: %r" % e)
            time.sleep(CHECK_S)


# ── portal ────────────────────────────────────────────────────────────────
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEON Wi-Fi</title><style>
body{font:16px -apple-system,system-ui,sans-serif;margin:0;padding:18px;background:#0b0b0c;color:#eee}
h1{font-size:20px;margin:0 0 4px}.m{color:#9a9a9a;font-size:14px;margin:0 0 14px}
.note{background:#1d2a1d;border:1px solid #2f5a2f;padding:10px;border-radius:10px;margin:0 0 14px}
button,input{font:inherit;width:100%%;box-sizing:border-box;border-radius:10px;padding:12px;margin:4px 0}
.net{display:flex;justify-content:space-between;background:#18181a;border:1px solid #2a2a2e;color:#eee;text-align:left}
input{background:#18181a;border:1px solid #2a2a2e;color:#eee;font-size:16px}
.go{background:#3b82f6;border:0;color:#fff;font-weight:600}.sub{background:#222;border:0;color:#ccc}
h2{font-size:15px;margin:20px 0 6px;color:#bbb}.row{display:flex;gap:8px;align-items:center}
.row span{flex:1}.x{width:auto;background:#2a1414;border:0;color:#f88;padding:8px 12px}
</style></head><body>
<h1>📶 NEON Wi-Fi</h1><p class="m">%(status)s</p>%(note)s
<form method="post" action="/connect">
<input name="ssid" id="ssid" placeholder="Network name" value="" autocapitalize="off" autocorrect="off" required>
<input name="psk" type="password" placeholder="Password (blank if open)" autocomplete="off">
<button class="go">Join</button></form>
<h2>Nearby %(age)s</h2>%(nets)s
<form method="post" action="/rescan"><button class="sub">Scan again (hotspot drops ~15 s, rejoin NEON)</button></form>
<h2>Saved — NEON tries these by itself</h2>%(saved)s
<script>document.querySelectorAll('.net').forEach(b=>b.onclick=e=>{e.preventDefault();
document.getElementById('ssid').value=b.dataset.s;document.querySelector('[name=psk]').focus()})</script>
</body></html>"""

DONE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>NEON</title>
<style>body{font:16px -apple-system,system-ui,sans-serif;padding:18px;background:#0b0b0c;color:#eee}</style>
</head><body><h1>%s</h1><p>%s</p></body></html>"""


def render(w):
    s = w.snapshot()
    a = s["active"]
    status = ("Hotspot <b>%s</b> is up — NEON has no internet." % AP_SSID if s["mode"] == "ap"
              else "Connected to <b>%s</b>." % html.escape(a) if a else "Not connected.")
    if s["busy"]:
        status += " Now: %s…" % html.escape(s["busy"])
    note = '<p class="note">%s</p>' % html.escape(s["message"]) if s["message"] else ""
    nets = "".join(
        '<button class="net" data-s="%s"><span>%s %s</span><span>%d%%</span></button>'
        % (html.escape(n["ssid"], True), "🔓" if n["security"] == "open" else "🔒",
           html.escape(n["ssid"]), n["signal"]) for n in s["networks"]) or '<p class="m">none yet</p>'
    age = ""
    if s["scanned_at"]:
        age = "(%d min ago)" % max(0, int((time.time() - s["scanned_at"]) // 60))
    saved = "".join(
        '<form method="post" action="/forget" class="row"><span>%s%s</span>'
        '<input type="hidden" name="name" value="%s">%s</form>'
        % (html.escape(k["name"]), " · built in" if k["name"] == HOME_SSID else "",
           html.escape(k["name"], True),
           "" if k["name"] == HOME_SSID else '<button class="x">forget</button>')
        for k in known_networks())
    return PAGE % {"status": status, "note": note, "nets": nets, "age": age, "saved": saved}


class Portal(BaseHTTPRequestHandler):
    wifi = None  # set in main()

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8"):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def guard(self):
        if client_allowed(self.client_address[0]):
            return True
        self.send(403, "join the %s hotspot to use this page\n" % AP_SSID, "text/plain")
        return False

    def do_GET(self):
        if not self.guard():
            return
        if self.path.startswith("/api/state"):
            return self.send(200, json.dumps(self.wifi.snapshot()), "application/json")
        self.send(200, render(self.wifi))  # every path: captive probes land here

    def do_POST(self):
        if not self.guard():
            return
        n = int(self.headers.get("Content-Length") or 0)
        f = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(n).decode(),
                                                       keep_blank_values=True).items()}
        w = self.wifi
        if self.path == "/connect" and f.get("ssid", "").strip():
            ssid = f["ssid"].strip()
            self.send(200, DONE % ("Joining %s…" % html.escape(ssid),
                      "NEON saved it and is switching now, so this hotspot will drop. "
                      "If it can't join, the <b>%s</b> hotspot comes back within a minute "
                      "with the reason." % AP_SSID))
            threading.Thread(target=w.connect, args=(ssid, f.get("psk", "")), daemon=True).start()
        elif self.path == "/rescan":
            self.send(200, DONE % ("Scanning…", "The hotspot drops for ~15 s. Rejoin "
                      "<b>%s</b> and open any web page." % AP_SSID))
            threading.Thread(target=w.rescan_from_ap, daemon=True).start()
        elif self.path == "/forget" and f.get("name"):
            w.forget(f["name"])
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
        else:
            self.send(400, "bad request\n", "text/plain")


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    w = Wifi()
    Portal.wifi = w
    srv = Server(("0.0.0.0", PORT), Portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    w.loop()


if __name__ == "__main__":
    main()
