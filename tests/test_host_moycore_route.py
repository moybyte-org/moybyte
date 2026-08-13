"""The host routes a spec-only Lua cart to the boards' Lua (rung 4 swap).

`build_workstation` now prefers `runtime/lua_binding` -- libmoy's binding over
the same vendored 5.4 the boards build, LUA_32BITS and all -- and keeps lupa
for carts that use moybyte's superset, which libmoy does not bind.

The gate is the part worth testing, because its first version was a silent
no-op: a plain substring scan for the superset names disqualified EVERY cart in
the tree (`table.insert`, a variable named `col`, the letters "net" inside an
identifier), so the new path existed and was never taken. It matches calls now,
and these assertions are what would have caught that.
"""

import pytest

from runtime import lua_host


def test_a_superset_cart_is_not_routed_away_any_more():
    """The correction: ONE runtime.

    A cart calling make_layer used to be sent to lupa, which meant two Lua
    engines coexisted, both implementing the spec verbs. The superset rides
    moycore now as registered trampolines, so every cart qualifies and the
    old source gate is gone."""
    for src in ("function _draw() cls(1) spr(2, 8, 8) end",
                "function _draw() draw_layer(l, 0, 0) end",
                "if view ~= nil then view(128, 120) end"):
        assert lua_host.moycore_supports(src) is True, src


@pytest.mark.skipif(not lua_host._moycore_available(),
                    reason="no C compiler for the host lua binding")
def test_every_lua_seed_cart_really_runs_on_moycore(tmp_path):
    """The net the route test above did not have: run the SHIPPED carts.

    `moycore_supports` answering True is not the same as the cart starting, and
    for a while it was not even close. Every Lua seed calls make_layer or
    image() somewhere in `_init`; those are object-valued, the trampoline
    cannot marshal a Layer, so the load raised and `_make_lua` handed the cart
    to lupa. The route test passed the whole time -- it asks the gate, and the
    gate was right. This asks the carts.

    Deliberately checks the run's TYPE rather than that it merely started: a
    fallback is a successful start.
    """
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    titles = [c["title"] for c in ws.launcher.items
              if c.get("runtime") == "lua"]
    assert len(titles) >= 3, "no lua seeds to check -- the roster moved"
    for title in titles:
        ws.launcher.sel = next(i for i, c in enumerate(ws.launcher.items)
                               if c["title"] == title)
        ws.open()                       # the real launch path, so the project
        assert ws.player.cart_error is None, title   # (and its sheet) is live
        assert type(ws.player._lua).__name__ == "MoycoreHostRun", \
            "%s fell back off moycore" % title
        for _ in range(5):
            ws.frame(1 / 60)
            assert ws.player.cart_error is None, title
        ws._exit_to_caller()


@pytest.mark.skipif(not lua_host._moycore_available(),
                    reason="no C compiler for the host lua binding")
def test_a_cart_with_no_sheet_or_map_does_not_take_the_process_down():
    """A brand-new project draws NOTHING, and that has to be survivable.

    moy_console holds its sheet and map by pointer and a fresh project has
    neither, so libmoy's binding was handing NULL to a raster that
    dereferences it: `function _draw() spr(0, 0, 0) end` in an empty cart
    segfaulted -- on a board, a reset with no message, from two lines a
    beginner types first. Fixed upstream (the verbs degrade to empty rather
    than crash) and vendored; this is the pin that keeps it fixed here, since
    every SHIPPED cart has a sheet and so proves nothing about this path.
    """
    from runtime.lua_binding import HostLuaRun
    buf = bytearray(64 * 64)
    run = HostLuaRun(buf, 64, 64)                    # no sheet, no map
    try:
        assert run.load("function _update(dt) end\n"
                        "function _draw()\n"
                        "  spr(1, 0, 0) sspr(0, 0, 8, 8, 0, 0)\n"
                        "  map(0, 0) tline(0, 0, 8, 8, 0, 0, 65536, 0)\n"
                        "  mset(1, 1, 3) X = mget(1, 1)\n"
                        "end\n", "@c") is None
        assert run.tick(1 / 60.0) is None
        assert run.get_global("X") == -1, "no map must read as empty, not junk"
        assert not any(buf), "an absent sheet drew something"
    finally:
        run.close()


@pytest.mark.skipif(not lua_host._moycore_available(),
                    reason="no C compiler for the host lua binding")
def test_a_spec_only_cart_actually_runs_on_the_new_path(tmp_path):
    """End to end through build_workstation: the run object must BE the
    moycore one, and a frame must reach the canvas."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    assert ws.lua_runtime is not None
    ns = ws.make_api(ws.canvas, ws.input, {}, ws.sheet, ws.audio,
                     ws.tilemap, ws.pmem, None, {})
    src = ("local n = 0\n"
           "function _update(dt) n = n + 1 end\n"
           "function _draw() cls(0) rect(0, 0, n * 3, 5, 8) end\n")
    run = ws.lua_runtime(ns, src)
    try:
        assert isinstance(run, lua_host.MoycoreHostRun), \
            "the cart did not run on the boards' Lua"
        before = sum(1 for b in ws.canvas.buf if b)
        run.update(1 / 30.0)
        run.update(1 / 30.0)
        after = sum(1 for b in ws.canvas.buf if b)
        assert after > before, "the C loop drew nothing into the host canvas"
    finally:
        run.close()


def test_button_masks_agree_with_the_per_name_walk():
    """The fast path and the slow path must produce identical bitmasks.

    moycore's snapshot refresh built these two integers with sixteen
    `held`/`pressed` calls -- ~100us of pure call overhead per frame on the S3,
    in the glue whose whole job is to stop a cart making calls like that. It
    asks `button_masks()` for both in one call now, and the failure mode of
    that swap is not a crash but SUBTLY WRONG INPUT, which would present as a
    cart that mostly works. So: every combination, both ways, must agree.
    """
    from runtime.input import InputState

    names = InputState.BUTTONS
    for combo in range(1 << len(names)):          # all 256 held-sets
        inp = InputState()
        inp.begin_frame()                          # frame 1: nothing held
        for i, n in enumerate(names):
            if combo & (1 << i):
                inp.set_held(n, True)
        inp.begin_frame()                          # frame 2: these are PRESSED
        want_h = want_p = 0
        for i, n in enumerate(names):
            if inp.held(n):
                want_h |= 1 << i
            if inp.pressed(n):
                want_p |= 1 << i
        assert inp.button_masks() == (want_h, want_p), combo
        inp.begin_frame()                          # frame 3: held, not pressed
        want_h2 = 0
        for i, n in enumerate(names):
            if inp.held(n):
                want_h2 |= 1 << i
        assert inp.button_masks() == (want_h2, 0), combo

    # A name outside BUTTONS must not corrupt the mask (the set can hold keys
    # the button table has never heard of).
    inp = InputState()
    inp.set_held("zorp", True)
    inp.set_held("a", True)
    inp.begin_frame()
    assert inp.button_masks() == (1 << names.index("a"), 1 << names.index("a"))
