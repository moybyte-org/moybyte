"""Frameskip (#77): while a GAME plays, logic + input tick at the full loop rate
but the render side (cart _draw + composite + flush) runs every SECOND frame.
Settings -> FRAMESKIP toggles + persists it; default OFF. The gate lives in
Workstation.frame BEFORE the redraw gate; the logic-only tick is
Player.tick(dt, render=False)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _open_game(ws, title="Star Catcher"):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("no seed cart titled " + title)


def _count_player_calls(ws):
    """Wrap the running cart's _update/_draw with counters (via the forwarding
    properties), returning the dict they bump."""
    calls = {"upd": 0, "draw": 0}
    u0, d0 = ws.player._update, ws.player._draw

    def upd(dt):
        calls["upd"] += 1
        if u0:
            u0(dt)

    def draw():
        calls["draw"] += 1
        if d0:
            d0()

    ws.player._update = upd
    ws.player._draw = draw
    return calls


def test_frameskip_off_renders_every_frame(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_game(ws)
    assert ws.cart["type"] == "game" and ws.frameskip is False
    calls = _count_player_calls(ws)
    drawn0 = ws._frames_drawn
    for _ in range(20):
        ws.frame(1 / 60)
    assert calls["upd"] == 20
    assert calls["draw"] == 20
    assert ws._frames_drawn - drawn0 == 20


def test_frameskip_halves_render_keeps_logic_full_rate(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_game(ws)
    ws.set_frameskip(True, persist=False)
    calls = _count_player_calls(ws)
    drawn0 = ws._frames_drawn
    for _ in range(20):
        ws.frame(1 / 60)
    assert calls["upd"] == 20                    # logic never skips
    assert calls["draw"] == 10                   # render every 2nd frame
    assert ws._frames_drawn - drawn0 == 10       # no composite/flush on skips
    # First frame after the toggle RENDERED (the setter resets the phase bit).
    # 20 frames = R S R S ... so the last frame was a skip; one more renders.
    ws.frame(1 / 60)
    assert calls["draw"] == 11


def test_frameskip_leaves_the_launcher_and_tools_alone(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.set_frameskip(True, persist=False)
    ws._dirty = True
    drawn0 = ws._frames_drawn
    ws.frame(1 / 60)                             # launcher frame: gate must not fire
    assert ws._frames_drawn - drawn0 == 1
    assert ws._fs_phase is False                 # phase parked while no game runs


def test_frameskip_persists_via_settings_setter(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws.frameskip is False                 # kid default OFF
    ws.set_frameskip(True)
    assert ws.system.get("frameskip") is True    # lands in system.json state
    ws.set_frameskip(False)
    assert ws.system.get("frameskip") is False
    # The Settings surface exposes it as a row (the "diag"-kind ON/OFF gate).
    keys = [r[0] for r in ws.settings_layer._settings_rows()]
    assert "frameskip" in keys


def test_frameskip_crash_frame_still_renders_the_panel(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_game(ws)
    ws.set_frameskip(True, persist=False)

    def boom(dt):
        raise RuntimeError("kaboom")

    ws.player._update = boom
    for _ in range(4):                           # crash lands regardless of phase
        ws.frame(1 / 60)
    assert ws.cart_error is not None
    drawn0 = ws._frames_drawn
    ws._dirty = True
    ws.frame(1 / 60)                             # crashed cart: gate is OFF ->
    assert ws._frames_drawn - drawn0 == 1        # the error panel paints every frame


def test_release_world_on_exit_drops_the_cart_world(tmp_path):
    """#66 repeat-run fragmentation fix: exiting a run releases the WORLD at
    exit (ns cleared in place + _update/_draw dropped), so the next cart builds
    into a compact heap instead of around the previous world's corpse. go_home's
    old `ns = None` kept everything alive through _update's closure."""
    import gc
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_game(ws)
    for _ in range(10):
        ws.frame(1 / 60)
    assert ws.player._update is not None
    ns = ws.player.ns
    assert ns and "_update" in ns
    ws.go_home()
    # The run's world is gone: hooks nulled, the ns dict itself emptied (so any
    # lingering function object no longer pins its globals).
    assert ws.player._update is None and ws.player._draw is None
    assert ws.player.ns is None
    assert len(ns) == 0
    gc.collect()
    # And the hold-to-exit path funnels through the same release.
    _open_game(ws)
    for _ in range(5):
        ws.frame(1 / 60)
    ns2 = ws.player.ns
    assert ns2 and ws.player._update is not None
    ws._exit_to_caller()
    assert ws.player._update is None
    assert len(ns2) == 0


def test_go_home_drops_the_workspace_pins(tmp_path):
    """#66 pin-field fix: returning HOME re-slims the fat cart (its rehydrated
    src/sprites strings were permanent mid-heap pins when the same cart was
    reopened) and swaps in a fresh empty Project (the old sheet/tilemap/images
    were pins too). The editor path is untouched -- only go_home."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    _open_game(ws)
    cart = ws.cart
    proj = ws.project
    assert cart.get("lazy") is False          # rehydrated fat for the run
    assert proj.sheet is not None
    ws.go_home()
    assert cart.get("lazy") is True           # re-slimmed at exit
    assert "src" not in cart
    assert ws.project is not proj             # fresh empty workspace
    assert ws.project.sheet is None
    assert ws._fat_cart is None
    # And the cart still reopens fine (rehydrates from the store).
    _open_game(ws)
    assert ws.cart.get("lazy") is False
    for _ in range(3):
        ws.frame(1 / 60)
    assert ws.cart_error is None
