"""The host runs the boards' Lua (moycore plan rung 4).

A second embedding would be a different program from the one the boards run:
64-bit doubles where both boards build `LUA_32BITS` (their FPUs are
single-precision, so doubles would be soft-float), integers that do not wrap at
2^31 -- and golden-frame parity for float-heavy carts would be host-only.

`runtime/lua_binding.py` closes that by giving CPython libmoy's own binding
over the same vendored Lua 5.4, compiled the same way. This pins that it works
and that the seams the boards depend on behave identically: a cart loads and
`_init` runs, frames tick, the canvas changes with cart state, an input
snapshot edge reaches the cart, the audio queue carries what it played in
order, pmem is C-side with a dirty flag, and an error comes back as text with
its line number rather than as an exception.

Skipped without a C compiler, like the audio and raster bindings.
"""

import pytest

from runtime import lua_binding as lb

CART = """
local n = 0
function _init() pmem(0, 41) end
function _update(dt)
  n = n + 1
  pmem(0, pmem(0) + 1)
  if btnp("left") then sfx(3) end
  if btn("a") then sfx(5, 2) end
end
function _draw()
  cls(0)
  rect(0, 0, n * 4, 6, 8)
  print("x" .. n, 2, 30, 7)
end
"""


@pytest.mark.skipif(not lb.HostLuaRun.available(),
                    reason="no C compiler for the host lua binding")
def test_a_cart_runs_in_the_same_c_the_boards_run():
    buf = bytearray(96 * 64)
    r = lb.HostLuaRun(buf, 96, 64)
    try:
        assert r.load([(CART, "@cart")]) is None
        counts, audio = [], []
        for f in range(4):
            r.snap[lb.SNAP_BTNP] = (1 << 0) if f == 1 else 0    # left
            r.snap[lb.SNAP_BTN] = (1 << 4) if f == 2 else 0     # a
            assert r.tick(1 / 30.0) is None
            counts.append(sum(1 for b in buf if b))
            audio.append(r.audio())
        # The rect grows with the cart's own counter: a canvas wired elsewhere,
        # or an _update that never ran, gives a flat sequence.
        assert counts == sorted(counts) and counts[0] < counts[-1], counts
        assert audio[0] == []
        assert audio[1] == [(lb.AQ_SFX, 3, -1, 0)], audio
        assert audio[2] == [(lb.AQ_SFX, 5, 2, 0)], audio
        dirty, img = r.pmem()
        assert dirty and img[0] == 45, img[0]
    finally:
        r.close()


@pytest.mark.skipif(not lb.HostLuaRun.available(),
                    reason="no C compiler for the host lua binding")
def test_a_cart_error_is_text_with_its_line():
    """What crash-to-code needs: the chunkname and line, not a traceback the
    Player would have to parse out of an exception."""
    r = lb.HostLuaRun(bytearray(32 * 32), 32, 32)
    try:
        assert r.load([("function _update(dt) error('boom') end", "@cart")]) is None
        err = r.tick(1 / 30.0)
        assert err and "boom" in err and "cart:1" in err, err
    finally:
        r.close()


@pytest.mark.skipif(not lb.HostLuaRun.available(),
                    reason="no C compiler for the host lua binding")
def test_the_sandbox_is_the_same_ceiling_the_boards_have():
    """SPEC.md 4.1 is a ceiling, not a suggestion, and the reason it holds is
    that the excluded stdlibs are not compiled in at all -- so this is checking
    the build, not a registration list."""
    r = lb.HostLuaRun(bytearray(32 * 32), 32, 32)
    try:
        r.load([("function _update(dt) end", "@cart")])
        # coroutine is NOT on this list: SPEC.md 4.1 admits it.
        for name in ("io", "os", "debug", "package", "require",
                     "dofile", "loadstring", "collectgarbage"):
            probe = "function _update(dt) local x = %s.anything end" % name
            r2 = lb.HostLuaRun(bytearray(32 * 32), 32, 32)
            try:
                err = r2.load([(probe, "@probe")]) or r2.tick(1 / 30.0)
                assert err is not None, "%s is reachable from a cart" % name
            finally:
                r2.close()
    finally:
        r.close()


TOUCH_CART = """
px, py, pt, ph = -1, -1, -1, -1
function _update(dt)
  local x, y, tapped, held = touch()
  if x == nil then px, py, pt, ph = -1, -1, -1, -1
  else px, py, pt, ph = x, y, (tapped and 1 or 0), (held and 1 or 0) end
end
function _draw() end
"""


@pytest.mark.skipif(not lb.HostLuaRun.available(),
                    reason="no C compiler for the host lua binding")
def test_touch_reaches_a_lua_cart_and_decodes_its_flags():
    """`touch()` on the Lua tier, which answered nil for every cart everywhere.

    The snapshot slot was in the C ABI and libmoy read it; NOTHING ever wrote
    it -- not this binding and not the boards' -- so a Lua cart had no pointer
    at all while the Python twin of the same cart did. The slot is FLAGS now
    (widgets.P_LIVE/P_HELD/P_CLICK) because h_touch has one slot and three
    questions, and `click` is not nested inside `down`: a scripted tap raises
    the edge with the finger already lifted, which is what the last state here
    pins and what `letter blitz` scores with.
    """
    buf = bytearray(96 * 64)
    r = lb.HostLuaRun(buf, 96, 64)
    try:
        assert r.load([(TOUCH_CART, "@cart")]) is None
        want = {
            0: (-1, -1, -1, -1),            # no pointer at all -> touch() is nil
            1: (40, 22, 0, 0),              # live, nothing held
            3: (40, 22, 0, 1),              # held (a drag)
            7: (40, 22, 1, 1),              # the press edge of a hold
            5: (40, 22, 1, 0),              # a tap whose finger already lifted
        }
        for state, expect in want.items():
            r.snap[lb.SNAP_TOUCH_X] = 40
            r.snap[lb.SNAP_TOUCH_Y] = 22
            r.snap[lb.SNAP_TOUCH_DOWN] = state
            assert r.tick(1 / 30.0) is None
            got = tuple(r.get_global(n) for n in ("px", "py", "pt", "ph"))
            assert got == expect, (state, got, expect)
    finally:
        r.close()


QUIT_CART = """
function _update(dt) if TIME_TO_GO then quit() end end
function _draw() end
"""


@pytest.mark.skipif(not lb.HostLuaRun.available(),
                    reason="no C compiler for the host lua binding")
def test_a_lua_cart_can_end_itself():
    """`quit()` from Lua reaches the flag the Player honours.

    libmoy's quit() is a host callback that sets SNAP_QUIT, and NOTHING read it
    on either tier -- so a Lua cart calling quit() ran on forever, including a
    textmode(True) cart, which the cart API says must provide its own exit
    because hold-BACKSPACE cannot reach one. Found on glass: a probe cart
    called quit() 3,940 times and kept running.
    """
    buf = bytearray(32 * 32)
    r = lb.HostLuaRun(buf, 32, 32)
    try:
        assert r.load([(QUIT_CART, "@cart")]) is None
        assert r.tick(1 / 30.0) is None
        assert r.snap[lb.SNAP_QUIT] == 0, "quit fired without the cart asking"
        r.exec("TIME_TO_GO = true")
        assert r.tick(1 / 30.0) is None
        assert r.snap[lb.SNAP_QUIT] == 1, "quit() did not reach the snapshot"
    finally:
        r.close()
