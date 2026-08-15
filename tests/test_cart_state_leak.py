"""A cart's draw state must not outlive the cart.

`camera` / `clip` / `pal` / `palt` are per-cart state, and while a Lua cart runs
the C side owns them (libmoy's `moy_canvas`, plus the host raster binding's own
copy). When the run ends the shell paints the launcher through the SAME canvas.
If any of that state survives, the console draws offset, clipped, or recoloured
-- a whole-screen symptom with no obvious cause.

The moycore plan's lane ledger flagged exactly this and said the pin was
missing: *"camera/clip/pal Python-authoritative mirrors -> ownership flips to
moycore during a run; shell reads back at exit -- state-verb traces do NOT yet
observe the exit-time read-back (the trace resets state per frame)."* The
crossing shipped; the pin did not. This is it.

Why it is asserted BEHAVIOURALLY (draw a pixel, look at it) rather than by
reading `_cam_x` and friends: there are now up to three places the state can
live -- the Python mirrors, the native raster binding's canvas
(`Canvas._nr`, resynced by `reset_state`), and libmoy's own canvas inside a
running moycore VM. A field check pins whichever one the test author happened
to think of. A drawn pixel pins all of them at once, which is also what the kid
actually experiences.
"""

import pytest

from runtime.canvas import Canvas

import canvas_probe as probe  # pixel-width-agnostic "it drew" probes


def _ws(tmp_path):
    from runtime import host_app
    return host_app.build_workstation(str(tmp_path / "carts"))


def _open(ws, title):
    idx = {c["title"]: i for i, c in enumerate(ws.launcher.items)}
    assert title in idx, sorted(idx)
    ws.launcher.sel = idx[title]
    ws.open()
    assert ws.player.cart_error is None, ws.player.cart_error


DIRTY_PY = """\
def _init():
    camera(20, 10)
    clip(30, 30, 8, 8)
    pal(9, 3)
    palt(7, True)

def _update(dt):
    pass

def _draw():
    rect(0, 0, 4, 4, 9)
"""

DIRTY_LUA = """\
function _init()
  camera(20, 10)
  clip(30, 30, 8, 8)
  pal(9, 3)
  palt(7, true)
end

function _update(dt) end

function _draw()
  rect(0, 0, 4, 4, 9)
end
"""


def _assert_canvas_is_clean(cv):
    """Draw at the origin in colour 9 and require it to land at the origin, in
    colour 9, unclipped."""
    cv.cls(0)
    cv.rect(0, 0, 4, 4, 9)
    assert cv.pix(0, 0) == 9, "a cart's camera/clip/pal outlived it"
    assert cv.pix(3, 3) == 9
    assert cv.pix(5, 5) == 0, "the fill spread -- state is not identity"


def _run_and_exit(tmp_path, title, src, runtime=None, main=None):
    from runtime import moy_carts
    ws = _ws(tmp_path)
    kw = {"runtime": runtime, "main": main} if runtime else {}
    cart = moy_carts.create(title, str(tmp_path / "carts"), src=src, **kw)
    ws.launcher.items.append(cart)
    _open(ws, title)
    for _ in range(3):
        ws.frame(1 / 60)
        assert ws.player.cart_error is None, ws.player.cart_error
    ws._exit_to_caller()
    return ws


def test_a_python_carts_draw_state_does_not_outlive_it(tmp_path):
    ws = _run_and_exit(tmp_path, "Dirty Py", DIRTY_PY)
    _assert_canvas_is_clean(ws.canvas)


@pytest.mark.skipif(
    not __import__("runtime.lua_host", fromlist=["x"]).moycore_supports(""),
    reason="host lua binding not built")
def test_a_lua_carts_draw_state_does_not_outlive_it(tmp_path):
    """The one the ledger was actually worried about: here the state lived in
    libmoy's canvas inside the VM, not in the Python mirrors, so nothing the
    shell resets on its own side would have cleared it. (It happens to be safe
    by construction -- `moycore.close()` does `lua_close` and the run's canvas
    dies with it -- but "safe by construction" is a claim, and this is the
    thing that checks it.)"""
    ws = _run_and_exit(tmp_path, "Dirty Lua", DIRTY_LUA, "lua", "main.lua")
    _assert_canvas_is_clean(ws.canvas)


def test_reset_state_clears_the_native_raster_too_not_just_the_mirrors():
    """`Canvas.reset_state` ends with `_nr_sync()`, and that line is the whole
    reason a cart's camera does not leak into the shell on a host with the
    binding built. Dropping it would leave the Python mirrors identity and the
    C canvas offset -- the mirrors would say everything is fine.

    Skips itself when the binding is absent, so it tests the lane it names.
    """
    cv = Canvas(64, 48)
    if getattr(cv, "_nr", None) is None:
        pytest.skip("native raster binding not built")
    cv.camera(20, 10)
    cv.clip(30, 30, 8, 8)
    cv.pal(9, 3)
    cv.palt(7, True)
    cv.reset_state()
    _assert_canvas_is_clean(cv)


def test_the_shell_draws_clean_after_a_cart_that_never_reset(tmp_path):
    """End to end at the seam that matters: exit, then let the console paint a
    real frame. A leak here is what the kid sees -- a launcher drawn 20px left
    and clipped to a corner."""
    ws = _run_and_exit(tmp_path, "Dirty Py 2", DIRTY_PY)
    before = probe.painted_pixels(ws.canvas)
    ws.frame(1 / 60)
    after = probe.painted_pixels(ws.canvas)
    assert after > 0, "the shell painted nothing after the cart exited"
    del before
    _assert_canvas_is_clean(ws.canvas)
