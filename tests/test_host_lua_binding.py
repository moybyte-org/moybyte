"""The host runs the boards' Lua, not lupa's (moycore plan rung 4).

The host sim embeds Lua through **lupa**, which is a different program from the
one the boards run: 64-bit doubles where both boards build `LUA_32BITS`,
because their FPUs are single-precision and doubles would be soft-float. The
plan records the consequence -- golden-frame parity for float-heavy carts is
host-only, and device integers wrap at 2^31 where lupa's do not.

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
        assert r.load(CART, "@cart") is None
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
        assert r.load("function _update(dt) error('boom') end", "@cart") is None
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
        r.load("function _update(dt) end", "@cart")
        # coroutine left this list on 2026-09-02: SPEC.md 4.1 admits it.
        for name in ("io", "os", "debug", "package", "require",
                     "dofile", "loadstring", "collectgarbage"):
            probe = "function _update(dt) local x = %s.anything end" % name
            r2 = lb.HostLuaRun(bytearray(32 * 32), 32, 32)
            try:
                err = r2.load(probe, "@probe") or r2.tick(1 / 30.0)
                assert err is not None, "%s is reachable from a cart" % name
            finally:
                r2.close()
    finally:
        r.close()
