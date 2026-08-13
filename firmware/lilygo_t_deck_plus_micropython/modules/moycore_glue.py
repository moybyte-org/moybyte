"""The host half of moycore (stage 2): what the frame loop does around tick().

`LuaCartRun` next door registers ~40 Python closures as Lua globals and the
cart calls back into Python hundreds of times a frame. `MoycoreRun` registers
nothing: libmoy's own binding installs the whole verb table as C functions, and
this class does the three things that cannot live in C --

  * REFRESH the input snapshot before the tick. Buttons, time, the pointer and
    the last typed key are written into one `array("i")`; the cart's btn() is
    then an array read on the C side of the wall, however many times it asks.
  * DRAIN the audio queue after it, through the SAME `make_api` closures a
    Python cart uses. That is deliberate: sfx/music semantics (bank sync, the
    volume model the Settings surface reads, the diag triggers) stay in one
    place, and what the crossing deletes is the per-CALL trip, not the
    behaviour. Order is preserved because the queue is a queue.
  * PERSIST pmem at boundaries. The C side owns 256 int32 slots with a dirty
    flag, which is the shape the device already deferred to (#66) -- RAM during
    play, written at exit, crash capture, workspace swap and the periodic
    frame-boundary save.

Everything else the shell needs from a run -- `init`/`update`/`draw`/`close` --
has the same shape `LuaCartRun` exposes, so `Player` needs no branch.

EVERY Lua cart runs here. moybyte's superset verbs (scenes, tables, texts,
flags, the batch forms) are not in libmoy's table, so they are REGISTERED on
top of it as trampolines back to the same `make_api` closures they always had
-- `moycore.register()` between `run_begin` and `load`, which is the window a
cart needs because it captures its globals into locals as it executes.

The OBJECT-valued ones (make_layer/draw_layer/image) cannot be registered at
all: a trampoline marshals scalars and tuples, so a Layer comes back as
"unsupported value". They take the same route they take under moy_lua --
int-handle functions on this side, Lua wrappers on that side -- and the route
is literally the same code (`moy_lua_glue.install_handles` +
`PRELUDE_HANDLES`, run through `moycore.exec` before the cart loads), because
two copies of a wrapper is how the two runtimes would start drifting.

That is a correction, and worth stating plainly: the first version read "layers
stay Python-side" as "carts using layers keep the old runtime", which left TWO
Lua cart runtimes on the device, both implementing the spec verbs. A cart
needing a Python-backed make_layer does not need a second engine -- it needs
one engine that can hold a Python-backed verb. So `supports()` is gone with the
split it justified.
"""

from array import array

try:
    from lua_ext import PRELUDE_TABLE, PRELUDE_HANDLES, install_handles
except ImportError:                      # host tests importing the device module
    from runtime.lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES,
                                 install_handles)

try:
    import moycore as _moycore
except ImportError:                      # a build without the module
    _moycore = None

# The verbs libmoy's binding does not install, registered on top of it from the
# cart's own api namespace.
#
# NOT "moybyte's superset" -- that framing was wrong and it matters. SPEC.md 10
# defines `layers` (make_layer/draw_layer/background) and `viewport` (view) as
# STANDARD extensions, specified so two consoles implementing them implement
# the same thing; the rest are moybyte's, namespaced `vendor.feature` exactly
# as 10 requires. So this list is "optional spec features libmoy has not
# implemented yet, plus our own", and the first group is an upstream gap rather
# than a local invention. `Image` is excluded as always: it is a constructor
# returning an object, and objects do not cross this boundary -- layers and
# images travel as int handles (the prelude's wrappers).
# NOT view or background: SPEC.md 6 made both core, so libmoy installs them and
# registering ours would SHADOW the C with a trampoline -- the wrong direction.
# view costs nothing now (libmoy records it; read it back with moycore.view()),
# and background is a clear libmoy does itself.
#
# NOT make_layer/draw_layer/image either, though moybyte's DO shadow libmoy's:
# those three are object-valued, and objects do not cross this boundary any
# more than they cross moy_lua's. They ride the SHARED prelude instead --
# `install_handles` puts int-handle functions here and PRELUDE_HANDLES defines
# the Lua-side make_layer/draw_layer/image on top of them. That is why moybyte
# layers can take an Image (`lay:spr(image("bg"), ...)`, the moybyte.images
# vendor extension) where libmoy's sheet-tile pair cannot.
#
# NOT `table` either, for the #164 reason: registering that name would set the
# GLOBAL `table`, clobbering Lua's library, and celeste's p8 shim needs
# table.remove. It goes in as `moy_table_verb` and PRELUDE_TABLE grafts it onto
# the library as a metatable __call, exactly as under moy_lua.
SUPERSET = ("scene", "load_scene",
            "actors", "touching", "move_actor", "move_actor_to",
            "remove_actor", "draw_scene", "text",
            "spr_batch", "rect_batch", "spans", "mouse",
            "col", "on_net", "fget", "fset", "mouse_wheel")

# moy_button bit positions, SPEC.md 7.1 order. The snapshot packs them into one
# int; this is the only place the two orders meet, so it is a table rather than
# an assumption spread across the file.
BUTTONS = ("left", "right", "up", "down", "a", "b", "run", "home")


class MoycoreRun:
    """One cart run under moycore. Same shape as LuaCartRun."""

    # How much audio a single frame may queue before the rest is dropped. The
    # C side caps it too; this mirrors the constant so the array is the right
    # size rather than merely big.
    AUDIO_MAX = 32

    def __init__(self, ws, ns, src):
        if _moycore is None:
            raise RuntimeError("moycore is not in this build")
        self.ws = ws
        self.ns = ns
        self._dt = 0.0
        canvas = ws.canvas
        project = getattr(ws, "project", None)
        sheet = getattr(project, "sheet", None) if project is not None else None
        tilemap = getattr(project, "tilemap", None) if project is not None else None

        self.snap = array("i", bytearray(4 * _moycore.SNAP_LEN))
        self.aq = array("h", bytearray(2 * (1 + _moycore.AQ_SLOTS * self.AUDIO_MAX)))
        self.pmem_img = array("i", bytearray(4 * 256))

        pmem = getattr(ws, "pmem", None)
        if pmem is not None:
            cells = getattr(pmem, "cells", None)
            if cells is not None:
                for i in range(min(256, len(cells))):
                    self.pmem_img[i] = int(cells[i])

        # The wire table: what an index looks like in THIS canvas's buffer.
        # Read from the canvas rather than assumed, for the same reason
        # web_boot reports it to the page -- device_canvas picks canonical or
        # byte-swapped RGB565 from the panel it is talking to.
        wire = None
        try:
            import device_canvas
            wire = device_canvas._PAL565_WIRE_BUF
        except Exception:  # noqa: BLE001 -- no table: libmoy uses the spec palette
            wire = None

        cfg = ns.get("_moy_cfg") if hasattr(ns, "get") else None
        _moycore.run_begin(
            canvas._buf, canvas.w, canvas.h, wire,
            getattr(sheet, "pix", None),
            getattr(tilemap, "cells", None),
            getattr(tilemap, "w", 0) or 0, getattr(tilemap, "h", 0) or 0,
            self.snap, self.aq, self.pmem_img, cfg)
        # The superset, on top of libmoy's table and BEFORE the cart runs.
        # Anything callable in the namespace that libmoy did not already
        # install: registering a name libmoy owns would shadow the C verb with
        # a trampoline, which is the opposite of the point.
        try:
            for name in ns:
                if name in SUPERSET and callable(ns[name]):
                    _moycore.register(name, ns[name])
            tv = ns.get("table") if hasattr(ns, "get") else None
            if callable(tv):
                _moycore.register("moy_table_verb", tv)
            # The object-valued verbs and their Lua wrappers -- the same two
            # halves moy_lua uses, from the same source. Without this a cart
            # calling make_layer() gets "unsupported value" back from the
            # trampoline and the whole run falls to the old runtime, which is
            # what sakura_lua/brick_siege/ray did before this landed.
            self._layers, self._images = install_handles(ns, _moycore.register)
            err = _moycore.exec(PRELUDE_TABLE + PRELUDE_HANDLES, "prelude")
            if err:
                raise RuntimeError(err)
        except Exception:  # noqa: BLE001 -- a bad verb must not strand the VM
            _moycore.close()
            raise
        # "@cart" so a runtime error renders `cart:12:` -- what
        # player._lua_cart_line parses for the crash-to-code panel (#24).
        err = _moycore.load(src, "@cart")
        if err:
            try:
                _moycore.close()
            finally:
                raise RuntimeError(err)

        self.snap[_moycore.SNAP_PLAYERS] = 1
        self._view = None
        self._sync_view()          # a view declared in _init must land now
        # init already ran inside run_begin (libmoy's moy_lua_init), so the
        # Player's `lua.init()` step has nothing left to do.
        self.init = None
        self.update = self._update
        # Present but empty: tick() already drew. A None draw would change the
        # shape every other runtime presents, which the Player and its tests
        # both read.
        self.draw = self._draw_noop

    # -- the frame ----------------------------------------------------------

    def _refresh(self):
        s = self.snap
        ws = self.ws
        inp = ws.input
        held = pressed = 0
        for i in range(len(BUTTONS)):
            name = BUTTONS[i]
            try:
                if inp.held(name):
                    held |= 1 << i
                if inp.pressed(name):
                    pressed |= 1 << i
            except Exception:  # noqa: BLE001 -- a missing button is not held
                pass
        s[_moycore.SNAP_BTN] = held
        s[_moycore.SNAP_BTNP] = pressed
        try:
            from device_util import _ticks_ms, _ticks_diff
            s[_moycore.SNAP_TIME_MS] = _ticks_diff(_ticks_ms(), inp.cart_start_ms)
        except Exception:  # noqa: BLE001
            pass
        # The pointer, in the cart's own coordinates. touch() reads nil when
        # down is 0, which is what "no pointer" means in SPEC.md 7.3.
        t = getattr(inp, "touch_state", None)
        if t is not None:
            try:
                x, y, down, ms = t()
                s[_moycore.SNAP_TOUCH_X] = int(x)
                s[_moycore.SNAP_TOUCH_Y] = int(y)
                s[_moycore.SNAP_TOUCH_DOWN] = 1 if down else 0
                s[_moycore.SNAP_TOUCH_MS] = int(ms)
            except Exception:  # noqa: BLE001
                s[_moycore.SNAP_TOUCH_DOWN] = 0
        s[_moycore.SNAP_KEY] = int(getattr(inp, "last_key", 0) or 0)

    def _sync_view(self):
        """Apply the cart's view() to the console.

        libmoy owns the verb (SPEC.md 6 core) and records the declaration; the
        console still has to ACT on it -- ws.input.game_view is what the WM
        composites from. So this reads the recording instead of the cart
        crossing into Python to set it, which is the whole point of the verb
        moving into core. Checked per frame because the spec allows a cart to
        change its region at runtime, and skipped when unchanged so a cart that
        declares once pays one comparison.
        """
        v = _moycore.view()
        if v == self._view:
            return
        self._view = v
        try:
            self.ws.input.game_view = v
        except Exception:  # noqa: BLE001 -- a console without the field is fine
            pass

    def _drain_audio(self):
        n = self.aq[0]
        if n <= 0:
            return
        self.aq[0] = 0
        ns = self.ns
        slots = _moycore.AQ_SLOTS
        for i in range(n):
            p = 1 + i * slots
            op = self.aq[p]
            a, b = self.aq[p + 1], self.aq[p + 2]
            try:
                if op == _moycore.AQ_SFX:
                    ns["sfx"](a, None if b < 0 else b)
                elif op == _moycore.AQ_MUSIC:
                    ns["music"](a, bool(b))
                elif op == _moycore.AQ_BEEP:
                    ns["beep"](a, b / 1000.0)
                elif op == _moycore.AQ_MUSIC_STOP:
                    ns["music_stop"]()
                elif op == _moycore.AQ_SOUND_STOP:
                    ns["sound_stop"](None if a < 0 else a)
                elif op == _moycore.AQ_VOLUME:
                    ns["volume"](a)
            except Exception:  # noqa: BLE001 -- one bad command is not the frame
                pass

    def _draw_noop(self):
        return None

    def _update(self, dt):
        """The whole cart frame. `draw` is None because this already drew: the
        C loop runs _update and _draw back to back, which is the point."""
        self._refresh()
        # A tier that swaps framebuffers per frame (the P4's ping-pong, the
        # T-Deck's bounce) must re-point the canvas, exactly as
        # DeviceCanvas.sync_back does for the Python lanes.
        buf = self.ws.canvas._buf
        if buf is not self._last_buf():
            _moycore.retarget(buf)
            self._buf = buf
        err = _moycore.tick(dt)
        self._sync_view()
        self._drain_audio()
        if err:
            raise RuntimeError(err)

    _buf = None

    def _last_buf(self):
        return self._buf

    # -- exit ---------------------------------------------------------------

    def flush_pmem(self):
        """Write the C-side pmem image back into the console's Pmem, if it
        moved. Called at the same boundaries the deferred save already uses."""
        if _moycore is None or not _moycore.active():
            return False
        if not _moycore.pmem_image(self.pmem_img):
            return False
        pmem = getattr(self.ws, "pmem", None)
        if pmem is None:
            return False
        cell = getattr(pmem, "cell", None)
        if cell is None:
            return False
        for i in range(256):
            try:
                cell(i, int(self.pmem_img[i]))
            except Exception:  # noqa: BLE001
                break
        return True

    def close(self):
        try:
            self.flush_pmem()
        finally:
            # Drop the handle registries: they are what PIN the run's layers
            # and images, and a layer is a full-canvas allocation.
            self._layers = None
            self._images = None
            if _moycore is not None:
                _moycore.close()


def make_moycore_runtime(ws):
    """The `ws.lua_runtime`-shaped factory, or None when unavailable."""
    if _moycore is None:
        return None

    def _make(ns, src):
        return MoycoreRun(ws, ns, src)
    return _make
