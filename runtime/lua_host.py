"""Host Lua cart runtime: a "runtime": "lua" cart on the BOARDS' own Lua.

`MoycoreHostRun(ws, ns, src)` runs the cart through `runtime/lua_binding` --
libmoy's own binding over the same vendored Lua 5.4 the firmware compiles,
LUA_32BITS and all -- so the host is not a different program from the device.
Player._start_lua drives it; a Lua error surfaces as a normal Python exception,
so the Player's existing crash-to-code panel needs nothing special.

**lupa is GONE (2026-08-14), and with it `LuaCartRun` and the PRELUDE that
adapted its Python-object bridge.** It was a SECOND embedding with second
semantics -- 64-bit doubles where both boards build LUA_32BITS, so a float-heavy
cart could agree with the host goldens and disagree on glass, and an integer that
wraps at 2^31 on the board did not wrap here. It survived two justifications:
first as the runtime for carts using moybyte's superset (dead once lua_ext's
handle glue put layers/images ON moycore), then as the fallback for a host with
no C compiler. The second is not a trade this project makes -- the host already
REQUIRES a compiler for audio, where "no compiler" means SILENCE rather than a
second synth (plan 3.1). Two Lua engines to spare a compiler is that same trade,
refused there.

So: no compiler, no Lua carts on the host, and the Player says so through the
runtime-missing panel exactly as a device build without the module does.

Canonical home is runtime/; tests import it as runtime.lua_host.
"""

# The glue shared with the device runtimes (runtime/lua_ext.py): the object-verb
# handles (make_layer/draw_layer/image return OBJECTS, and the ctypes dispatch
# marshals ints and one string, so they ride int handles plus a Lua prelude),
# the moy_button bit order, and the two deny lists that decide what gets
# registered on top of libmoy's table.
from runtime.lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES, MOY_BUTTONS,
                             LIBMOY_VERBS, NOT_REGISTRABLE, install_handles)

# ---------------------------------------------------------------------------
# The moycore lane -- now the ONLY lane.
#
# `runtime/lua_binding.py` is the same C the boards run. This used to route only
# the carts libmoy's SPEC table could serve and hand the rest to lupa; the
# superset (layers/images, scenes, tables, texts, view) reaches moycore through
# lua_ext's handles now, so every cart qualifies and the source gate that used
# to decide is gone. What `moycore_supports` still answers is whether the module
# BUILT -- see below.
# ---------------------------------------------------------------------------

def _moycore_available():
    try:
        from runtime.lua_binding import HostLuaRun
    except ImportError:                     # pragma: no cover
        try:
            from lua_binding import HostLuaRun
        except ImportError:
            return None
    return HostLuaRun if HostLuaRun.available() else None


def canvas_target(canvas):
    """(buf, wire, indexed) -- how libmoy draws into whichever canvas this is.

    Every tier draws on `device_canvas.DeviceCanvas` now (two bytes of RGB565),
    including the host -- `runtime/canvas.py`, the indexed host raster this used
    to have to straddle, was deleted 2026-08-15. The probe stays because it is
    how the runtime asks a canvas what it is rather than assuming, and libmoy's
    binding still accepts either. The 565 side needs its WIRE table too: libmoy
    resolves colour at draw time there, and the table is per-canvas --
    byte-swapped on the T-Deck's panel, canonical elsewhere, and rewritten
    outright by a cart's SPEC.md 3.1 palette.

    Read off `_wire` rather than `device_canvas._PAL565_WIRE_BUF`, which is what
    the device glue reads: the module constant is the STOCK table, so a cart
    that shipped its own palette would draw through the wrong one.
    """
    buf = getattr(canvas, "_buf", None)          # DeviceCanvas: RGB565
    if buf is not None:
        return buf, getattr(canvas, "_wire", None), False
    return canvas.buf, None, True                # an indexed canvas: no tier ships one


class MoycoreHostRun:
    """A cart run under the boards' Lua -- the host's only Lua runtime."""

    def __init__(self, ws, ns, src):
        HostLuaRun = _moycore_available()
        if HostLuaRun is None:
            raise RuntimeError("no host lua binding")
        canvas = ws.canvas
        project = getattr(ws, "project", None)
        buf, wire, indexed = canvas_target(canvas)
        self._run = HostLuaRun(buf, canvas.w, canvas.h,
                               getattr(project, "sheet", None),
                               getattr(project, "tilemap", None),
                               wire=wire, indexed=indexed)
        # The superset, registered on top of libmoy's table before the cart
        # runs -- see the device glue for why this is registration and not a
        # second runtime.
        reg = getattr(self._run, "register", None)
        self._layers = self._images = None
        if reg is not None:
            for name in ns:
                if (name not in LIBMOY_VERBS and name not in NOT_REGISTRABLE
                        and callable(ns[name])):
                    reg(name, ns[name])
            tv = ns.get("table")
            if callable(tv):
                reg("moy_table_verb", tv)
            # The object-valued verbs, through the shared int-handle glue --
            # the same module and the same prelude the boards run. Without it
            # `make_layer` returns a Layer, the dispatch cannot marshal it, and
            # the cart gets nil back: sakura_lua died on `lay:spr(...)` rather
            # than falling back to lupa, which is a worse failure than the one
            # the fallback exists for.
            self._layers, self._images = install_handles(ns, reg)
            err = self._run.exec(PRELUDE_TABLE + PRELUDE_HANDLES, "prelude")
            if err:
                self._run.close()
                raise RuntimeError(err)
        err = self._run.load(src, "@cart")
        if err:
            self._run.close()
            raise RuntimeError(err)
        self._ns = ns
        self._ws = ws
        self._view = None
        self._sync_view()                   # a view declared in _init lands now
        self.init = None                    # load() already ran it
        self.update = self._update
        # The C loop runs _update and _draw back to back inside the tick, so
        # there is nothing left to do here -- but the hook must EXIST. The
        # Player calls update() then draw(), and a None draw would silently
        # change the shape every other runtime presents.
        self.draw = self._draw_noop

    def _update(self, dt):
        s = self._run.snap
        inp = self._ws.input
        from runtime.lua_binding import (SNAP_BTN, SNAP_BTNP, SNAP_BTN_P1,
                                         SNAP_BTNP_P1, SNAP_PLAYERS,
                                         AQ_SFX, AQ_MUSIC,
                                         AQ_BEEP, AQ_MUSIC_STOP,
                                         AQ_SOUND_STOP, AQ_VOLUME)
        # MOY_BUTTONS, not a fourth hand-written copy of the order. This loop
        # carried its own and was CORRECT, which is exactly what made the
        # boards' divergence invisible: the host played fine, so nothing here
        # ever pointed at the tier where it did not.
        masks = getattr(inp, "button_masks", None)
        if masks is not None:
            held, pressed = masks(MOY_BUTTONS)
        else:
            held = pressed = 0
            for i, name in enumerate(MOY_BUTTONS):
                try:
                    if inp.held(name):
                        held |= 1 << i
                    if inp.pressed(name):
                        pressed |= 1 << i
                except Exception:  # noqa: BLE001
                    pass
        s[SNAP_BTN], s[SNAP_BTNP] = held, pressed
        # PLAYER TWO (#65). These snapshot slots exist in the C ABI and nothing
        # filled them, so libmoy's `players()` answered 1 forever and a Lua cart
        # could not have a second player at all -- the Python twin of the same
        # cart fielded two tanks and the Lua one fielded one. The count is read
        # through the router because a transport slot (a radio peer) lives
        # there, not on the InputState; the fast path costs one dict test.
        n = 1
        pr = getattr(inp, "players", None)
        if pr is not None:
            n = pr.count()
            if n > 1:
                h1, p1 = pr.button_masks(MOY_BUTTONS, 1)
                s[SNAP_BTN_P1] = h1
                s[SNAP_BTNP_P1] = p1
        s[SNAP_PLAYERS] = n
        err = self._run.tick(dt)
        self._sync_view()
        # Audio drains through the SAME api closures a Python cart uses, so the
        # engine's behaviour lives in one place; only the per-call trip is gone.
        for op, a, b, _c in self._run.audio():
            try:
                if op == AQ_SFX:
                    self._ns["sfx"](a, None if b < 0 else b)
                elif op == AQ_MUSIC:
                    self._ns["music"](a, bool(b))
                elif op == AQ_BEEP:
                    self._ns["beep"](a, b / 1000.0)
                elif op == AQ_MUSIC_STOP:
                    self._ns["music_stop"]()
                elif op == AQ_SOUND_STOP:
                    self._ns["sound_stop"](None if a < 0 else a)
                elif op == AQ_VOLUME:
                    self._ns["volume"](a)
            except Exception:  # noqa: BLE001 -- one bad command is not the frame
                pass
        if err:
            raise RuntimeError(err)

    def _sync_view(self):
        """Apply the cart's view() to the console -- libmoy owns the verb and
        records it, the console still has to composite accordingly."""
        v = self._run.view()
        if v == self._view:
            return
        self._view = v
        try:
            self._ws.input.game_view = v
        except Exception:  # noqa: BLE001
            pass

    def get_global(self, name):
        """A cart global as a number, or None -- what the parity suites read."""
        return self._run.get_global(name)

    def get_global_len(self, name):
        """The length of a table global (Lua's #t), or None."""
        return self._run.get_global_len(name)

    def _draw_noop(self):
        return None

    def close(self):
        # Tear the hooks down with the state, as lupa's run does: the Player
        # and its tests read `update is None` as "this run is over".
        self.update = None
        self.draw = None
        self._run.close()


# (What to register on top of libmoy's table -- everything in the cart
# namespace libmoy does not already own -- is decided by LIBMOY_VERBS /
# NOT_REGISTRABLE, imported from runtime.lua_ext at the top of this file.
#
# They were written out HERE as well until 2026-08-15, defended by a comment
# saying this file is host-only while the other copy is staged into firmware
# trees. That was not the operative fact: the staged module already imports
# lua_ext, by the same name a board freezes it under -- there was no boundary
# between the two copies, only 46 hand-matched names and no test comparing
# them.)


def moycore_supports(src):
    """True when the boards' Lua can take this cart -- which is now every cart
    the binding is present for.

    It used to answer "does this cart avoid moybyte's superset", and the answer
    routed superset carts to lupa. That left TWO Lua runtimes, both
    implementing the spec verbs, which is the duplication this project exists
    to end. The superset rides moycore as registered trampolines now, so the
    only question left is whether the binding built at all. (The word-boundary
    source scan that backed the old answer went with it; the last thing that
    read it was this function.)
    """
    del src                     # every cart qualifies; the arg stays for callers
    return _moycore_available() is not None
