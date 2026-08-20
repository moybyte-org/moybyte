"""A cart's view() must reach the console, not just libmoy.

SPEC.md 6 made `view` core, so libmoy owns the verb and RECORDS what the cart
declared -- which is what lets the host read it instead of the cart crossing
into Python to set it. But recording is only half: `ws.input.game_view` is what
the WM composites from, and a console that never reads the recording shows a
128x128 cart small in the corner of a 320x240 screen while every test passes.

That is exactly the gap this pins. It existed for one commit: moving `view` off
its trampoline removed the only thing that had been setting the field.
"""

import pytest

from runtime import lua_host


@pytest.mark.skipif(not lua_host._moycore_available(),
                    reason="no C compiler for the host lua binding")
def test_a_declared_view_reaches_the_console(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ns = ws.make_api(ws.canvas, ws.input, {}, ws.sheet, ws.audio,
                     ws.tilemap, ws.pmem, None, {})
    run = ws.lua_runtime(ns, "function _init() view(128, 120) end\n"
                             "function _update(dt) end\n"
                             "function _draw() cls(0) end\n")
    try:
        assert isinstance(run, lua_host.MoycoreHostRun)
        # Declared in _init, so it must be applied before the first frame --
        # the WM reads this on the frame the cart starts.
        assert getattr(ws.input, "game_view", None) == (128, 120)
        # ...and the WM's own view of it, which is the property that clamps the
        # declaration to the canvas and hands back a source rect.
        assert ws.game_view is not None
        run.update(1 / 30.0)
        assert getattr(ws.input, "game_view", None) == (128, 120)
    finally:
        run.close()


@pytest.mark.skipif(not lua_host._moycore_available(),
                    reason="no C compiler for the host lua binding")
def test_a_view_changed_mid_run_follows(tmp_path):
    """SPEC.md 6: view chooses the region at runtime, so a cart may change it."""
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ns = ws.make_api(ws.canvas, ws.input, {}, ws.sheet, ws.audio,
                     ws.tilemap, ws.pmem, None, {})
    run = ws.lua_runtime(ns, "n = 0\n"
                             "function _update(dt)\n"
                             "  n = n + 1\n"
                             "  if n == 2 then view(160, 120) end\n"
                             "end\n"
                             "function _draw() cls(0) end\n")
    try:
        assert ws.game_view is None             # nothing declared yet
        run.update(1 / 30.0)
        assert ws.game_view is None
        run.update(1 / 30.0)
        assert getattr(ws.input, "game_view", None) == (160, 120)
        assert ws.game_view is not None
    finally:
        run.close()
