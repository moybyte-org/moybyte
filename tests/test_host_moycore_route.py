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


def test_the_gate_discriminates_and_names_why():
    """A gate that never opens, and a gate that always opens, are both bugs."""
    spec_only = "function _draw() cls(1) rect(0,0,4,4,7) spr(2, 8, 8) end"
    assert lua_host._uses(spec_only, lua_host.SUPERSET) is None

    # Superset CALLS are caught...
    assert lua_host._uses("function _draw() draw_layer(l, 0, 0) end",
                          lua_host.SUPERSET) == "draw_layer"
    assert lua_host._uses("if view ~= nil then view(128, 120) end",
                          lua_host.SUPERSET) == "view"
    # ...while the words that made the first version useless are not.
    for benign in ("local t = {} table.insert(t, 1)",
                   "local col = 7 rect(0,0,1,1,col)",
                   "local network_ok = true",
                   "-- draws the text of the image",
                   "obj:image()",
                   "moy.table(1)"):
        assert lua_host._uses(benign, lua_host.SUPERSET) is None, benign


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
            "a spec-only cart still went to lupa"
        before = sum(1 for b in ws.canvas.buf if b)
        run.update(1 / 30.0)
        run.update(1 / 30.0)
        after = sum(1 for b in ws.canvas.buf if b)
        assert after > before, "the C loop drew nothing into the host canvas"
    finally:
        run.close()
