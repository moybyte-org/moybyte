"""WiFi manager (#38): the capability-permission-gated `wifi` API, the host fake
backend, the credential store, and the WiFi-manager system cart running headlessly
through the shared console -- the same console the device runs.

The injection is permission-gated: a cart whose manifest grants "network" gets a
`wifi` name in its namespace; a normal cart does NOT (sandbox preserved). These
tests pin that gate, the FakeWifi behavior, persistence + reload, and that the
manager cart loads + runs a few frames without error.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYSTEM_CARTS = ROOT / "system_carts"


class _StubInput:
    def held(self, n):
        return False

    def pressed(self, n):
        return False


class _Stub:
    w = 320
    h = 240

    def __getattr__(self, name):
        return lambda *a, **k: 0


def _open_cart(ws, title):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            break
    ws.open()


# -- the base key-set is identical; `wifi` is the only conditional name --------

def test_make_api_base_keyset_identical_and_wifi_is_conditional():
    from runtime import host_app
    base = set(host_app.make_api(_Stub(), _StubInput(), {}).keys())
    assert "wifi" not in base                       # no permission -> no wifi name

    with_wifi = set(host_app.make_api(_Stub(), _StubInput(), {},
                                      wifi=host_app.FakeWifi()).keys())
    # The ONLY difference between the two namespaces is the gated `wifi` name.
    assert with_wifi - base == {"wifi"}
    assert base - with_wifi == set()


def test_host_and_device_make_api_keysets_match_except_wifi():
    # host == device contract: identical base names, and `wifi` injected the same
    # way (only when a non-None backend is passed). Compare the key-sets directly.
    import importlib.util
    from runtime import host_app

    # Load the device kid_runtime under CPython (mirrors the spike test loader).
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module(str(SYSTEM_CARTS))
    fw = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules" / "kid_runtime.py"
    spec = importlib.util.spec_from_file_location("kid_runtime", fw)
    dev = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dev)

    host_base = set(host_app.make_api(_Stub(), _StubInput(), {}).keys())
    dev_base = set(dev.make_api(_Stub(), _StubInput(), {}).keys())
    assert host_base == dev_base

    host_w = set(host_app.make_api(_Stub(), _StubInput(), {}, wifi=object()).keys())
    dev_w = set(dev.make_api(_Stub(), _StubInput(), {}, wifi=object()).keys())
    assert host_w == dev_w
    assert host_w - host_base == {"wifi"} == dev_w - dev_base


# -- FakeWifi backend behavior + persistence ----------------------------------

def test_fakewifi_scan_connect_status_forget(tmp_path):
    from runtime import host_app
    from runtime import kid_carts

    carts = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts)
    w = host_app.FakeWifi(kid_carts, carts)

    # scan() returns the canned list of (ssid, signal, locked) tuples.
    aps = w.scan()
    assert aps and all(len(a) == 3 for a in aps)
    ssids = [a[0] for a in aps]
    assert "Home WiFi" in ssids

    # Not connected yet.
    assert w.status() == (False, None, None)
    assert w.known() == []

    # connect() -> status() reports connected + the fake IP; creds persist.
    assert w.connect("Home WiFi", "hunter2") is True
    connected, ssid, ip = w.status()
    assert connected and ssid == "Home WiFi" and ip == host_app.FakeWifi.FAKE_IP
    assert "Home WiFi" in w.known()

    # forget() drops it and disconnects when it's the active link.
    assert w.forget("Home WiFi") is True
    assert w.status() == (False, None, None)
    assert "Home WiFi" not in w.known()


def test_wifi_credentials_persist_and_reload(tmp_path):
    from runtime import host_app
    from runtime import kid_carts

    carts = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts)

    w1 = host_app.FakeWifi(kid_carts, carts)
    w1.connect("Coffee Shop", "")           # open net
    w1.connect("Home WiFi", "secretpw")     # locked net (last -> front of store)

    # A fresh backend (e.g. next boot) sees the saved networks via the store.
    w2 = host_app.FakeWifi(kid_carts, carts)
    assert w2.known() == ["Home WiFi", "Coffee Shop"]   # most-recent first

    # The on-disk store round-trips the actual password too.
    nets = kid_carts.load_wifi(carts)
    by_ssid = {n["ssid"]: n["password"] for n in nets}
    assert by_ssid["Home WiFi"] == "secretpw"
    assert by_ssid["Coffee Shop"] == ""


def test_kid_carts_wifi_store_remember_forget(tmp_path):
    from runtime import kid_carts
    carts = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts)

    assert kid_carts.load_wifi(carts) == []          # nothing saved yet
    kid_carts.remember_wifi("Net A", "pa", carts)
    kid_carts.remember_wifi("Net B", "pb", carts)
    kid_carts.remember_wifi("Net A", "pa2", carts)   # re-remember moves to front, updates pw
    nets = kid_carts.load_wifi(carts)
    assert [n["ssid"] for n in nets] == ["Net A", "Net B"]
    assert nets[0]["password"] == "pa2"

    kid_carts.forget_wifi("Net A", carts)
    assert [n["ssid"] for n in kid_carts.load_wifi(carts)] == ["Net B"]
    # Forgetting an unknown ssid is a harmless no-op.
    kid_carts.forget_wifi("Nope", carts)
    assert [n["ssid"] for n in kid_carts.load_wifi(carts)] == ["Net B"]


# -- permission gate through the real Workstation -----------------------------

def test_network_cart_gets_wifi_non_network_cart_does_not(tmp_path):
    from runtime import host_app
    from runtime import kid_carts

    carts_dir = str(tmp_path / "carts")
    kid_carts.ensure_dirs(carts_dir)
    # A normal cart: no network permission.
    kid_carts.create("Plain", carts_dir, src="def _draw():\n    cls(0)\n")
    # A privileged cart: declares "network" in its manifest.
    priv = kid_carts.create("Net Cart", carts_dir, src="def _draw():\n    cls(0)\n")
    import json
    man_path = priv["path"] + "/manifest.json"
    man = json.loads(Path(man_path).read_text())
    man["permissions"] = ["graphics", "input", "network"]
    Path(man_path).write_text(json.dumps(man))

    ws = host_app.build_workstation(carts_dir)

    _open_cart(ws, "Plain")
    assert ws.cart_error is None
    assert "wifi" not in ws.ns                     # sandbox preserved

    _open_cart(ws, "Net Cart")
    assert ws.cart_error is None
    assert "wifi" in ws.ns                          # capability granted
    assert ws.ns["wifi"] is ws.wifi                 # the shared system service


# -- the WiFi-manager system cart loads + runs headlessly ----------------------

def test_wifi_manager_cart_loads_and_runs(tmp_path):
    from runtime import host_app

    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_cart(ws, "WiFi")
    assert ws.screen == "desktop" and ws.cart_error is None
    assert "wifi" in ws.ns                          # it got the gated API

    # It scanned on _init -> the cart sees networks in its module namespace.
    assert ws.ns.get("nets")                        # non-empty scan list
    # Run a handful of frames; a crash would set cart_error.
    for _ in range(5):
        ws.frame(1.0 / 30)
    assert ws.cart_error is None


def test_wifi_manager_cart_connects_via_touch_and_persists(tmp_path):
    from runtime import host_app
    from runtime import kid_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    _open_cart(ws, "WiFi")
    assert ws.cart_error is None

    # Pick the first OPEN network (no password screen) and tap CONNECT.
    nets = ws.ns["nets"]
    open_idx = next(i for i, n in enumerate(nets) if not n[2])
    ws.ns["sel"] = open_idx
    ssid = nets[open_idx][0]

    # Drive a tap on the CONNECT button through the pointer (mouse == touch).
    bx, by, bw, bh = ws.ns["CONNECT_BTN"]
    ws.pointer.place(bx + bw // 2, by + bh // 2)
    ws.pointer.click = True
    ws.frame(1.0 / 30)
    ws.pointer.click = False

    # The shared system service now reports connected to that SSID + an IP.
    connected, got_ssid, ip = ws.wifi.status()
    assert connected and got_ssid == ssid and ip

    # Persisted: a reload of the store sees the joined network.
    assert ssid in [n["ssid"] for n in kid_carts.load_wifi(carts_dir)]


def test_wifi_manager_cart_password_entry_for_locked_net(tmp_path):
    from runtime import host_app
    from runtime import kid_carts

    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    _open_cart(ws, "WiFi")

    # Pick a LOCKED network and tap CONNECT -> the cart switches to the password
    # screen (it does NOT connect yet, since it needs a password).
    nets = ws.ns["nets"]
    locked_idx = next(i for i, n in enumerate(nets) if n[2])
    ws.ns["sel"] = locked_idx
    ssid = nets[locked_idx][0]
    bx, by, bw, bh = ws.ns["CONNECT_BTN"]
    ws.pointer.place(bx + bw // 2, by + bh // 2)
    ws.pointer.click = True
    ws.frame(1.0 / 30)
    ws.pointer.click = False
    assert ws.ns["mode"] == "pass" and ws.ns["pick"] == ssid
    assert ws.wifi.status() == (False, None, None)   # not connected on the password screen

    # Type a password byte-by-byte via the keyboard (key()), then ENTER to connect.
    for ch in "wonderland":
        ws.input.last_key = ord(ch)
        ws.frame(1.0 / 30)
        ws.input.last_key = 0
        ws.frame(1.0 / 30)                  # release edge so the next char registers
    ws.input.last_key = 10                  # ENTER
    ws.frame(1.0 / 30)
    ws.input.last_key = 0

    connected, got_ssid, _ip = ws.wifi.status()
    assert connected and got_ssid == ssid
    nets_saved = {n["ssid"]: n["password"] for n in kid_carts.load_wifi(carts_dir)}
    assert nets_saved.get(ssid) == "wonderland"
