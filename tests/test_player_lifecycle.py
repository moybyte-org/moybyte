"""Player lifecycle pins: the run's WORLD dies at exit, HOME drops the
workspace pins, and the text-mode flip survives a PLAY-then-exit round trip."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _open_game(ws, title="Star Catcher"):
    for i, c in enumerate(ws.launcher.items):
        if c["title"] == title:
            ws.launcher.sel = i
            ws.open()
            return
    raise AssertionError("no seed cart titled " + title)


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


def test_text_mode_restored_on_play_exit_to_code_tab(tmp_path):
    """#80: PLAY from the CODE tab -> exit returns to the SAME tab, which is not
    a tab CHANGE, so set_menu_view's text-mode flip never fired -- the keyboard
    stayed in the cart's raw/game mode and sym+digit typed nothing. The exit
    path now restores the returned-to tab's mode explicitly."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.open_picker()
    ws.pick_selected()
    ws.set_menu_view("code")
    assert ws.input.text_mode is True          # code tab = typing mode
    ws._leave_menu()                            # PLAY: cart wants game keys
    assert ws.input.text_mode is False
    ws._exit_to_caller()                        # hold-exit back to the code tab
    assert ws.wm.top_is("menu")
    assert ws.input.text_mode is True          # the #80 fix: mode restored
    # And returning to a NON-text tab stays in game-key mode.
    ws.set_menu_view("config")
    ws._leave_menu()
    ws._exit_to_caller()
    assert ws.input.text_mode is False
