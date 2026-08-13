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
