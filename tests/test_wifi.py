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


from ws_helpers import open_cart as _open_cart


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

    # Load the device moy_runtime under CPython (mirrors the spike test loader).
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    sys.path.insert(0, str(ROOT / "tools"))
    import gen_device_carts
    sys.modules["carts_data"] = gen_device_carts.as_module(str(SYSTEM_CARTS))
    fw = ROOT / "firmware" / "lilygo_t_deck_plus_mainline" / "modules" / "moy_runtime.py"
    spec = importlib.util.spec_from_file_location("moy_runtime", fw)
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
    from runtime import moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    w = host_app.FakeWifi(moy_carts, carts)

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
    from runtime import moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)

    w1 = host_app.FakeWifi(moy_carts, carts)
    w1.connect("Coffee Shop", "")           # open net
    w1.connect("Home WiFi", "secretpw")     # locked net (last -> front of store)

    # A fresh backend (e.g. next boot) sees the saved networks via the store.
    w2 = host_app.FakeWifi(moy_carts, carts)
    assert w2.known() == ["Home WiFi", "Coffee Shop"]   # most-recent first

    # The on-disk store round-trips the actual password too.
    nets = moy_carts.load_wifi(carts)
    by_ssid = {n["ssid"]: n["password"] for n in nets}
    assert by_ssid["Home WiFi"] == "secretpw"
    assert by_ssid["Coffee Shop"] == ""


def test_moy_carts_wifi_store_remember_forget(tmp_path):
    from runtime import moy_carts
    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)

    assert moy_carts.load_wifi(carts) == []          # nothing saved yet
    moy_carts.remember_wifi("Net A", "pa", carts)
    moy_carts.remember_wifi("Net B", "pb", carts)
    moy_carts.remember_wifi("Net A", "pa2", carts)   # re-remember moves to front, updates pw
    nets = moy_carts.load_wifi(carts)
    assert [n["ssid"] for n in nets] == ["Net A", "Net B"]
    assert nets[0]["password"] == "pa2"

    moy_carts.forget_wifi("Net A", carts)
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Net B"]
    # Forgetting an unknown ssid is a harmless no-op.
    moy_carts.forget_wifi("Nope", carts)
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Net B"]


# -- permission gate through the real Workstation -----------------------------

def test_network_cart_gets_wifi_non_network_cart_does_not(tmp_path):
    from runtime import host_app
    from runtime import moy_carts

    carts_dir = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts_dir)
    # A normal cart: no network permission.
    moy_carts.create("Plain", carts_dir, src="def _draw():\n    cls(0)\n")
    # A privileged cart: declares "network" in its manifest.
    priv = moy_carts.create("Net Cart", carts_dir, src="def _draw():\n    cls(0)\n")
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
    from runtime import moy_carts

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
    assert ssid in [n["ssid"] for n in moy_carts.load_wifi(carts_dir)]


def test_wifi_manager_cart_password_entry_for_locked_net(tmp_path):
    from runtime import host_app
    from runtime import moy_carts

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
    nets_saved = {n["ssid"]: n["password"] for n in moy_carts.load_wifi(carts_dir)}
    assert nets_saved.get(ssid) == "wonderland"


# -- reconnect to a SAVED network (the on-glass P4 bug, 2026-07-25) -----------
#
# The Settings wifi panel has no credential access: tapping an already-known
# network calls connect(ssid, "") and lets the SERVICE resolve the stored
# password. The old backends took the "" literally -- they associated with an
# empty password (which fails on a locked net) AND remembered the "", wiping
# the saved password so the network could never be rejoined without retyping.

def _device_wifi_class():
    """The device DeviceWifi loaded under CPython (like the make_api test)."""
    import importlib.util
    # The shared device tier at the repo root -- the board's modules/ dir only
    # holds gitignored build-staged copies, absent on a fresh checkout.
    fw = ROOT / "device" / "device_wifi.py"
    for name in ("device_util",):
        if name not in sys.modules:
            s = importlib.util.spec_from_file_location(
                name, fw.parent / (name + ".py"))
            m = importlib.util.module_from_spec(s)
            s.loader.exec_module(m)
            sys.modules[name] = m
    spec = importlib.util.spec_from_file_location("device_wifi", fw)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.DeviceWifi


def test_empty_password_reconnect_keeps_the_saved_one(tmp_path):
    """host == device: connect(ssid, "") on a KNOWN network must resolve the
    stored password, not overwrite it with ""."""
    from runtime import host_app, moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)

    w = host_app.FakeWifi(moy_carts, carts)
    w.connect("Home WiFi", "secretpw")
    w.disconnect()
    w.connect("Home WiFi", "")               # the panel's known-network reconnect
    saved = {n["ssid"]: n["password"] for n in moy_carts.load_wifi(carts)}
    assert saved["Home WiFi"] == "secretpw"

    # ... and the device backend resolves the stored password the same way
    # (no radio here: the store lookup is what this pins).
    dev = _device_wifi_class()(moy_carts, carts)
    seen = []
    dev._ensure_wlan = lambda: None          # no network module under CPython
    dev.connect("Home WiFi", "")
    saved = {n["ssid"]: n["password"] for n in moy_carts.load_wifi(carts)}
    assert saved["Home WiFi"] == "secretpw", seen


def test_device_status_survives_an_unreadable_detail(tmp_path):
    """A port where ifconfig()/essid raises must still report the link UP --
    the old status() swallowed the whole thing and said NOT CONNECTED."""
    from runtime import moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    dev = _device_wifi_class()(moy_carts, carts)

    class _Wlan:
        def isconnected(self):
            return True

        def ifconfig(self):
            raise OSError("no ip yet")

        def config(self, _k):
            raise OSError("essid unavailable")

    dev.wlan = _Wlan()
    dev._ssid = "Home WiFi"
    connected, ssid, ip = dev.status()
    assert connected is True
    assert ssid == "Home WiFi"       # falls back to the ssid we associated with
    assert ip is None

    class _Down(_Wlan):
        def isconnected(self):
            return False

    dev.wlan = _Down()
    assert dev.status() == (False, None, None)


# -- the blank-credential corruption ------------------------------------------
#
# The store is a read-modify-WRITE: every connect()/forget() loads the whole
# known-networks list, edits it and republishes it. That makes the LOADER
# load-bearing for durability -- a read that reports "nothing saved yet" for a
# store that is merely half-published turns the very next save into permanent
# data loss, because _write_atomic's crash safety lives entirely in the .bak it
# leaves behind and the save after that deletes it.
#
# These pin the three states that window can leave (missing / truncated / wrong
# shape), the mutations that must not rewrite the store at all, and the rule
# keeping an unverified blank password out of wifi.json in the first place.


def _wifi_files(carts):
    """The wifi.json family actually on disk (store + its .bak/.tmp siblings)."""
    import os
    from runtime import moy_carts
    d = os.path.dirname(moy_carts.wifi_store_path(carts))
    return sorted(f for f in os.listdir(d) if f.startswith(moy_carts.WIFI_STORE_NAME))


def _two_saved(tmp_path):
    from runtime import moy_carts
    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    moy_carts.remember_wifi("Home", "secretpw", carts)
    moy_carts.remember_wifi("Work", "workpw", carts)
    return carts


def test_a_store_lost_in_the_write_window_is_recovered_not_reported_empty(tmp_path):
    """_write_atomic rotates the good file to .bak and then publishes the new one.
    A crash between those two renames leaves NO store -- and the previous good copy
    right beside it. The loader must find it."""
    import os
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    p = moy_carts.wifi_store_path(carts)
    os.remove(p + ".bak")
    os.rename(p, p + ".bak")            # exactly the state the window leaves

    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Work", "Home"]
    # ... and the recovery is HEALED back onto disk, not re-derived every read:
    # the next save must not be the thing that finally deletes the only copy.
    assert os.path.exists(p)
    os.remove(p + ".bak")
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Work", "Home"]


def test_a_truncated_store_falls_back_to_the_backup(tmp_path):
    """The rename-unsupported _copy fallback publishes by truncating `path` and
    writing into it, so a crash there leaves a HALF file rather than none. Garbage
    must read as "use the backup", never as "no saved networks"."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    with open(moy_carts.wifi_store_path(carts), "w") as f:
        f.write('{"networks": [{"ssi')     # torn mid-write

    # .bak is the copy from before the second save, so Home is what survives.
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Home"]
    assert moy_carts.load_wifi(carts)[0]["password"] == "secretpw"


def test_a_wrong_shaped_store_falls_back_to_the_backup(tmp_path):
    """A document that parses but isn't a networks list is as unusable as garbage."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    with open(moy_carts.wifi_store_path(carts), "w") as f:
        f.write('{"networks": "oops"}')

    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Home"]


def test_a_deliberately_emptied_store_is_not_resurrected(tmp_path):
    """The flip side: forgetting the last network leaves a VALID empty store, and
    the .bak still holds the old one. An empty list is an answer, not a failure."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    moy_carts.forget_wifi("Work", carts)
    moy_carts.forget_wifi("Home", carts)
    assert moy_carts.load_wifi(carts) == []


def test_a_save_after_a_corrupt_read_does_not_eat_the_other_networks(tmp_path):
    """The blast radius, end to end: one unreadable read must not be laundered
    into a permanent one-entry store by the next remember."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    with open(moy_carts.wifi_store_path(carts), "w") as f:
        f.write("")                        # empty file: parses to nothing

    moy_carts.remember_wifi("Guest", "guestpw", carts)
    saved = {n["ssid"]: n["password"] for n in moy_carts.load_wifi(carts)}
    assert saved["Home"] == "secretpw"     # NOT wiped by the unrelated save
    assert saved["Guest"] == "guestpw"


def _arm_write_probe(carts):
    """Delete the store's .bak and hand back a "did anything republish the store?"
    check. _write_atomic ALWAYS rotates an existing store to .bak before
    publishing, so a reappeared .bak is proof of a rewrite -- an exact signal,
    where an mtime comparison depends on the filesystem's timestamp granularity."""
    import os
    from runtime import moy_carts
    bak = moy_carts.wifi_store_path(carts) + ".bak"
    if os.path.exists(bak):
        os.remove(bak)
    return lambda: os.path.exists(bak)


def test_forgetting_an_unknown_network_writes_nothing(tmp_path):
    """A no-op forget must not rewrite the whole store to prove the absence --
    that is a full atomic rewrite, and another crash window, for nothing."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    p = moy_carts.wifi_store_path(carts)
    before = open(p).read()
    rewrote = _arm_write_probe(carts)

    assert [n["ssid"] for n in moy_carts.forget_wifi("Never Seen", carts)] == ["Work", "Home"]
    assert open(p).read() == before
    assert not rewrote()


def test_a_blank_ssid_is_not_a_network(tmp_path):
    """save_wifi drops a blank ssid, so remembering one can only burn a rewrite
    and return a list whose first entry is not on disk."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    rewrote = _arm_write_probe(carts)

    got = moy_carts.remember_wifi("", "junk", carts)
    assert [n["ssid"] for n in got] == ["Work", "Home"]      # what is really stored
    assert not rewrote()
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Work", "Home"]


def test_re_remembering_the_front_network_writes_nothing(tmp_path):
    """Every panel reconnect and every boot autoconnect re-remembers what is
    already stored. Each rewrite is another window on the crash above."""
    from runtime import moy_carts

    carts = _two_saved(tmp_path)
    rewrote = _arm_write_probe(carts)

    moy_carts.remember_wifi("Work", "workpw", carts)         # already at the front
    assert not rewrote()
    moy_carts.remember_wifi("Home", "secretpw", carts)       # a real reorder
    assert rewrote()
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Home", "Work"]


def test_an_unverified_blank_password_is_never_remembered(tmp_path):
    """host == device: a blank password reaches connect() both for an OPEN network
    and for one the store could not tell us about. Only an association proves which
    -- so a blank the radio never accepted must not reach wifi.json, where it would
    sit at the FRONT of the boot autoconnect list forever."""
    from runtime import moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    moy_carts.remember_wifi("Home", "secretpw", carts)

    dev = _device_wifi_class()(moy_carts, carts)
    dev._ensure_wlan = lambda: None      # no radio under CPython -> connect fails

    # The kid taps CONNECT on a locked network with the password field empty.
    assert dev.connect("Cafe", "") is False
    assert [n["ssid"] for n in moy_carts.load_wifi(carts)] == ["Home"]

    # A typed password IS kept even though the link didn't come up inside the ~4s
    # poll -- that is the late-association case ensure_online() waits for.
    assert dev.connect("Cafe", "cafepw") is False
    saved = {n["ssid"]: n["password"] for n in moy_carts.load_wifi(carts)}
    assert saved == {"Cafe": "cafepw", "Home": "secretpw"}


def test_an_unreadable_store_does_not_blank_the_saved_password(tmp_path):
    """The reported corruption, end to end. The store goes unreadable for one
    read; the panel's known-network reconnect passes "" and the service resolves
    nothing. That "" must not be written back over a real password -- and the
    save must not take every OTHER network with it."""
    import os
    from runtime import host_app, moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    w = host_app.FakeWifi(moy_carts, carts)
    w.connect("Home", "secretpw")
    w.connect("Work", "workpw")

    p = moy_carts.wifi_store_path(carts)
    os.remove(p + ".bak")
    os.rename(p, p + ".bak")             # the write window, mid-session
    assert sorted(w.known()) == ["Home", "Work"]

    w.connect("Home", "")                # the panel's known-network reconnect
    saved = {n["ssid"]: n["password"] for n in moy_carts.load_wifi(carts)}
    assert saved == {"Home": "secretpw", "Work": "workpw"}


def test_the_sibling_system_stores_recover_the_same_way(tmp_path):
    """system.json and achievements.json are the same read-modify-write over the
    same _write_atomic. One loader, one rule."""
    import os
    from runtime import moy_carts

    carts = str(tmp_path / "carts")
    moy_carts.ensure_dirs(carts)
    moy_carts.save_system({"wallpaper": "moy_night"}, carts)
    moy_carts.save_system({"wallpaper": "moy_night", "theme": "outline"}, carts)
    moy_carts.save_achievements(["first_cart"], carts)
    moy_carts.save_achievements(["first_cart", "first_edit"], carts)

    for path in (moy_carts.system_store_path(carts),
                 moy_carts.achievements_store_path(carts)):
        os.remove(path)                  # the crash window, again

    assert moy_carts.load_system(carts) == {"wallpaper": "moy_night"}
    assert moy_carts.load_achievements(carts) == ["first_cart"]
