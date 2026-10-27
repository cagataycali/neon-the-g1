"""scripts/neon_wifi.py: the pure parts (nmcli parsing, candidate order, portal guard)."""
import importlib.util
import pathlib

_p = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "neon_wifi.py"
_spec = importlib.util.spec_from_file_location("neon_wifi", _p)
nw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nw)


def test_split_terse_unescapes_colons():
    assert nw.split_terse(r"Cafe\:Guest:72:WPA2") == ["Cafe:Guest", "72", "WPA2"]
    assert nw.split_terse(r"a\\b::") == ["a\\b", "", ""]


def test_parse_scan_dedupes_strongest_and_hides_own_ap():
    text = "neon_net:40:WPA2\nneon_net:81:WPA2\n:90:WPA2\nNEON:99:WPA2\nOpen:30:\n"
    nets = nw.parse_scan(text, hide=("NEON",))
    assert [n["ssid"] for n in nets] == ["neon_net", "Open"]
    assert nets[0]["signal"] == 81 and nets[1]["security"] == "open"


def test_candidates_only_visible_priority_then_signal():
    known = [{"name": "office", "ssid": "office", "priority": 100},
             {"name": "neon_net", "ssid": "neon_net", "priority": -10},
             {"name": "cafe", "ssid": "cafe", "priority": 50},
             {"name": "hotel", "ssid": "hotel", "priority": 50}]
    scan = [{"ssid": "neon_net", "signal": 90}, {"ssid": "hotel", "signal": 70},
            {"ssid": "cafe", "signal": 40}]
    assert [k["name"] for k in nw.order_candidates(known, scan)] == ["hotel", "cafe", "neon_net"]


def test_station_count():
    dump = "Station aa:bb:cc:dd:ee:ff (on wlan0)\n\tsignal: -40\nStation 11:22:33:44:55:66 (on wlan0)\n"
    assert nw.count_stations(dump) == 2 and nw.count_stations("") == 0


def test_portal_only_answers_hotspot_clients():
    assert nw.client_allowed("10.42.0.37") and nw.client_allowed("127.0.0.1")
    assert not nw.client_allowed("192.168.0.195")
