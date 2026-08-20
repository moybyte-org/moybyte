"""Unified multiplayer cart API (#65): the transport-neutral player-input router
(btn(name, player) / players()) and the capability-gated net.* message seam
(net.send / on_net), plus a loopback fake transport for host testing.

The design mirrors the wifi gate (#38): player 0 is the local console's existing
controls (byte-for-byte as today -- zero regression); extra player slots stay empty
until a transport registers one; net.* is injected into a cart's namespace ONLY when
its manifest permissions include "multiplayer". Actual transports (USB gamepads,
ESP-NOW) are hardware and out of scope -- these tests pin the API layer over the
in-process LoopbackNet + a fake extra-controller source.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from runtime import players as players_mod  # noqa: E402


# -- stubs (mirroring test_wifi's) -----------------------------------------

class _StubInput:
    """A bare InputState-shaped stub: held/pressed, no PlayerRouter attached (the
    make_api probe / layer path)."""

    def __init__(self):
        self._held = set()
        self._pressed = set()

    def held(self, n):
        return n in self._held

    def pressed(self, n):
        return n in self._pressed


class _Stub:
    w = 320
    h = 240

    def __getattr__(self, name):
        return lambda *a, **k: 0


# -- PlayerRouter (input routing) ------------------------------------------

def test_router_single_player_delegates_to_local():
    inp = _StubInput()
    r = players_mod.PlayerRouter(inp)
    inp._held.add("left")
    inp._pressed.add("a")
    # Player 0 reads the local console directly -- byte-for-byte the InputState.
    assert r.held("left", 0) is True
    assert r.held("right", 0) is False
    assert r.pressed("a", 0) is True
    assert r.pressed("b", 0) is False
    # No transport registered -> exactly one player, and slot >0 is always empty.
    assert r.count() == 1
    assert r.held("left", 1) is False
    assert r.pressed("a", 1) is False


def test_router_second_source_counts_and_routes():
    inp = _StubInput()
    r = players_mod.PlayerRouter(inp)
    slot = r.add_player(1)              # a transport registers player 2 (index 1)
    assert r.count() == 2
    slot.set_held("right", True)
    r.begin_frame()                    # aligns the extra slot's press-edge
    assert r.held("right", 1) is True
    assert r.pressed("right", 1) is True     # 0->1 edge this frame
    assert r.held("right", 0) is False       # player 0 is independent
    r.begin_frame()                          # a second frame: still held, no new edge
    assert r.held("right", 1) is True
    assert r.pressed("right", 1) is False
    # Disconnect drops the slot + the count.
    r.remove_player(1)
    assert r.count() == 1
    assert r.held("right", 1) is False


def test_router_add_player_is_idempotent_and_reconnects():
    r = players_mod.PlayerRouter(_StubInput())
    a = r.add_player(2)
    a.set_held("up", True)
    b = r.add_player(2)                # same index -> same slot (state preserved)
    assert a is b
    assert r.held("up", 2) is True


# -- NetService / LoopbackNet (the net.* seam) -----------------------------

def test_loopback_send_delivers_to_peer_and_pumps_to_handler():
    a = players_mod.LoopbackNet()
    b = players_mod.LoopbackNet()
    a.link(b)
    assert a.peers() == 1 and b.peers() == 1

    got = []
    b.on_message(got.append)
    a.send({"x": 5})
    assert got == []                   # queued on b's inbox, NOT delivered mid-send
    b.pump()                           # the Player drains it once per frame
    assert got == [{"x": 5}]
    b.pump()                           # nothing left
    assert got == [{"x": 5}]


def test_loopback_unlinked_send_drops():
    solo = players_mod.LoopbackNet()   # a solo desktop sim: no second console
    assert solo.peers() == 0
    solo.send("into the void")         # must not raise
    solo.pump()                        # no handler, no inbox


def test_net_reset_clears_handler_and_inbox():
    a = players_mod.LoopbackNet()
    b = players_mod.LoopbackNet()
    a.link(b)
    seen = []
    b.on_message(seen.append)
    a.send("one")
    b.reset()                          # a fresh run: drop handler + queued packets
    b.pump()
    assert seen == []
    a.send("two")                      # the old handler is gone
    b.pump()
    assert seen == []


# -- make_api surface: players always present, net.* capability-gated --------

def _keys(**kw):
    from runtime import host_app
    return set(host_app.make_api(_Stub(), _StubInput(), {}, **kw).keys())


def test_make_api_exposes_btn_btnp_players_always():
    base = _keys()
    assert {"btn", "btnp", "players"} <= base
    assert "net" not in base and "on_net" not in base   # no permission -> no net


def test_make_api_net_is_capability_gated():
    base = _keys()
    with_net = _keys(net=players_mod.LoopbackNet())
    # The ONLY difference is the two gated names -- everything else is identical, so
    # a normal kid cart's namespace is unchanged.
    assert with_net - base == {"net", "on_net"}
    assert base - with_net == set()


def test_make_api_host_and_device_players_net_surface_match():
    # host == device: the base keysets already match (test_wifi pins it); here we
    # pin that BOTH added `players` and BOTH gate net/on_net the same way. Load the
    # DEVICE make_api straight from device_api.py (its bottom-of-DAG deps device_util/
    # device_canvas resolve once the firmware modules dir is on the path; conftest
    # routes bare `console` to runtime/) -- no fragile moy_runtime/carts_data chain.
    import importlib
    from runtime import host_app

    modules_dir = ROOT / "device"
    sys.path.insert(0, str(modules_dir))
    try:
        for _stale in ("device_util", "device_canvas", "device_api"):
            sys.modules.pop(_stale, None)
        dev = importlib.import_module("device_api")
    finally:
        sys.path.remove(str(modules_dir))

    net = players_mod.LoopbackNet()
    host_base = set(host_app.make_api(_Stub(), _StubInput(), {}).keys())
    dev_base = set(dev.make_api(_Stub(), _StubInput(), {}).keys())
    assert "players" in host_base and "players" in dev_base
    assert host_base == dev_base                      # the whole base surface matches
    host_net = set(host_app.make_api(_Stub(), _StubInput(), {}, net=net).keys())
    dev_net = set(dev.make_api(_Stub(), _StubInput(), {}, net=net).keys())
    assert host_net - host_base == {"net", "on_net"}
    assert dev_net - dev_base == {"net", "on_net"}


# -- make_api btn(name, player) wired to a real router ----------------------

def test_make_api_btn_reads_router_slots_and_is_regression_free():
    from runtime import host_app
    from runtime.input import InputState

    inp = InputState()
    inp.players = players_mod.PlayerRouter(inp)   # what wire_workstation_core attaches
    ns = host_app.make_api(_Stub(), inp, {})

    # Player 0 is byte-for-byte the local InputState (zero regression): drive some
    # buttons and assert btn/btnp mirror input.held/pressed for EVERY button.
    inp.set_held("left", True)
    inp.set_held("a", True)
    inp.begin_frame()
    for name in InputState.BUTTONS:
        assert ns["btn"](name) == inp.held(name)
        assert ns["btnp"](name) == inp.pressed(name)
    assert ns["btn"]("left") is True and ns["btn"]("right") is False
    assert ns["players"]() == 1                    # no second controller yet

    # A transport joins player 2 and feeds it; btn(name, 1) reads that slot while
    # player 0 stays independent.
    slot = inp.players.add_player(1)
    slot.set_held("right", True)
    inp.players.begin_frame()
    assert ns["players"]() == 2
    assert ns["btn"]("right", 1) is True
    assert ns["btn"]("right", 0) is False
    assert ns["btn"]("left", 0) is True            # player 0 still holds its own


# -- end to end through the shared console ----------------------------------

def _mp_cart(carts_dir, title, src):
    """Create a cart with the "multiplayer" manifest permission, then rescan so
    load() carries the permission onto the cart (create() has no perms arg)."""
    from runtime import moy_carts
    cart = moy_carts.create(title, carts_dir, src=src, type="game",
                            edit=[])
    man_path = Path(cart["path"]) / "manifest.json"
    man = json.loads(man_path.read_text())
    man["permissions"] = ["multiplayer"]
    man_path.write_text(json.dumps(man))
    return moy_carts.scan(carts_dir)


def _run_cart(ws, title):
    ws.launcher.sel = next(i for i, it in enumerate(ws.launcher.items)
                           if it["title"] == title)
    ws.open()


def test_end_to_end_net_delivery_gated_by_permission(tmp_path):
    from runtime import host_app
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    assert ws.net is not None                       # host injects a LoopbackNet

    # A multiplayer cart registers an on_net handler that stashes messages where the
    # test can read them (ws.ns is the running cart's namespace).
    src = (
        "_got = []\n"
        "def _catch(m):\n"
        "    _got.append(m)\n"
        "on_net(_catch)\n"
        "def _update(dt):\n"
        "    pass\n"
        "def _draw():\n"
        "    cls(0)\n"
    )
    ws.launcher.set_items(_mp_cart(carts_dir, "NetGame", src))
    drv = host_app.ConsoleDriver(ws)
    _run_cart(ws, "NetGame")
    assert ws.screen == "desktop"
    assert "net" in ws.ns and "on_net" in ws.ns      # gated names present

    # The peer console sends a message; it lands after the next frame's pump.
    peer = players_mod.LoopbackNet()
    peer.link(ws.net)
    peer.send({"hello": 1})
    assert ws.ns["_got"] == []                        # not yet delivered
    drv.frame(1 / 30)
    assert ws.ns["_got"] == [{"hello": 1}]            # pumped before _update

    # players() reports the local player; a joined controller bumps it.
    assert ws.ns["players"]() == 1
    ws.input.players.add_player(1).set_held("a", True)
    ws.input.players.begin_frame()
    assert ws.ns["players"]() == 2
    assert ws.ns["btn"]("a", 1) is True


def test_normal_cart_has_no_net_names(tmp_path):
    """A cart WITHOUT the multiplayer permission gets no net/on_net (sandbox
    preserved), and its btn/players still work single-player."""
    from runtime import host_app, moy_carts
    carts_dir = str(tmp_path / "carts")
    ws = host_app.build_workstation(carts_dir)
    moy_carts.create("Plain", carts_dir, type="game",
                     src="def _update(dt):\n    pass\ndef _draw():\n    cls(0)\n")
    ws.launcher.set_items(moy_carts.scan(carts_dir))
    _run_cart(ws, "Plain")
    assert ws.screen == "desktop"
    assert "net" not in ws.ns and "on_net" not in ws.ns
    assert ws.ns["players"]() == 1
    assert ws.ns["btn"]("left") is False
