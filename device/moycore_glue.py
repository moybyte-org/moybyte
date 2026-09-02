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
    from lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES, MOY_BUTTONS,
                         LIBMOY_VERBS, NOT_REGISTRABLE, install_handles)
except ImportError:                      # host tests importing the device module
    from runtime.lua_ext import (PRELUDE_TABLE, PRELUDE_HANDLES, MOY_BUTTONS,
                                 LIBMOY_VERBS, NOT_REGISTRABLE,
                                 install_handles)

try:
    import moycore as _moycore
except ImportError:                      # a build without the module
    _moycore = None

# Hoisted out of _refresh, where it was an `import` statement executed once per
# frame. Device-only, so it stays optional: the host and the web runner have no
# device_util and simply skip the time slot (libmoy adds the intra-tick elapsed
# term itself -- see modmoycore.c's h_time_ms).
try:
    from device_util import _ticks_ms, _ticks_diff
except ImportError:
    _ticks_ms = _ticks_diff = None

# What NOT to register on top of libmoy's table -- LIBMOY_VERBS (the names
# libmoy's own binding installs) and NOT_REGISTRABLE (ours, each excluded for
# its own reason) -- is imported above from lua_ext, with the full rationale
# beside the declaration there. So is the MOY_BUTTONS bit order.
#
# Read lua_ext's note (the d-pad incident) before duplicating any of them
# back here.


_P8_BUFFERS = []


def _p8_buffers():
    """The PICO-8 machine's 64KB memory and 0x4300 ROM snapshot, once."""
    if not _P8_BUFFERS:
        _P8_BUFFERS.append(bytearray(65536))
        _P8_BUFFERS.append(bytearray(0x4300))
    return _P8_BUFFERS


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
        # SPEC.md 3.5 tile flags. Unlike the sheet and the map this crosses as a
        # COPY (run_begin memcpys 512 bytes into the console's own table), so a
        # cart's fset writes are the C table's, not this bytearray's -- which is
        # right: they are run state, and nothing persists them.
        flags = getattr(project, "flags", None) if project is not None else None

        # Slot numbers bound ONCE: every one of these was a module attribute
        # lookup per frame in _refresh.
        self._I_BTN = _moycore.SNAP_BTN
        self._I_BTNP = _moycore.SNAP_BTNP
        self._I_BTN_P1 = _moycore.SNAP_BTN_P1
        self._I_BTNP_P1 = _moycore.SNAP_BTNP_P1
        self._I_PLAYERS = _moycore.SNAP_PLAYERS
        self._I_TIME = _moycore.SNAP_TIME_MS
        self._I_TX = _moycore.SNAP_TOUCH_X
        self._I_TY = _moycore.SNAP_TOUCH_Y
        self._I_TD = _moycore.SNAP_TOUCH_DOWN
        self._I_TMS = _moycore.SNAP_TOUCH_MS
        self._I_KEY = _moycore.SNAP_KEY
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
        # THIS CANVAS's table, not the module constant. They are the same object
        # until a cart ships its own palette (SPEC.md 3.1), at which point the
        # canvas holds a private one -- and reading the constant here would draw
        # every Lua verb in stock MOY64 while the Python verbs on the same canvas
        # honoured the cart's. Cart palettes only started working on device at
        # all in 096c492; this is the half of it the Lua path needs.
        wire = getattr(canvas, "_wire", None)
        if wire is None:
            try:
                import device_canvas
                wire = device_canvas._PAL565_WIRE_BUF
            except Exception:  # noqa: BLE001 -- no table: libmoy uses the spec palette
                wire = None

        cfg = ns.get("_moy_cfg") if hasattr(ns, "get") else None
        # The PICO-8 machine's memory (moy-spec libmoy moy_p8.c), from THIS
        # heap and handed over like the framebuffer -- not from the ESP heap,
        # which the S3 boards' MicroPython heap leaves 1.5KB of. Allocated
        # once; moycore reseeds it per run.
        if hasattr(_moycore, "p8_memory"):
            _moycore.p8_memory(*_p8_buffers())
        _moycore.run_begin(
            canvas._buf, canvas.w, canvas.h, wire,
            getattr(sheet, "pix", None),
            getattr(tilemap, "cells", None),
            getattr(tilemap, "w", 0) or 0, getattr(tilemap, "h", 0) or 0,
            self.snap, self.aq, self.pmem_img, cfg, flags)
        # The superset, on top of libmoy's table and BEFORE the cart runs.
        # Anything callable in the namespace that libmoy did not already
        # install: registering a name libmoy owns would shadow the C verb with
        # a trampoline, which is the opposite of the point.
        try:
            for name in ns:
                if (name not in LIBMOY_VERBS and name not in NOT_REGISTRABLE
                        and callable(ns[name])):
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

        self.snap[_moycore.SNAP_PLAYERS] = 1   # refreshed every frame by _refresh
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
        """Fill the snapshot the tick will read. Runs before EVERY frame, so
        what it costs is what every Lua cart pays before its own code starts.

        It cost ~1ms on the S3 and that was visible on glass: the Bench twins'
        FLOOR phase (the console's own frame, cart doing nothing) read 18ms for
        Python and 19ms for Lua, and the whole game-scene gap was this, not the
        scene. Which is the wrong way round for the glue whose entire job is to
        stop a cart making per-frame Python calls -- moycore deleted hundreds of
        crossings from the cart and this function put thirty back.

        What went: sixteen held/pressed calls (~6.35us each on that board --
        `CALIB call4=635us/100`) became ONE `button_masks()`; an `import`
        statement that ran every frame moved to module scope; and the SNAP_*
        slot numbers are bound once at construction instead of being looked up
        on the module object a dozen times a frame."""
        s = self.snap
        inp = self.ws.input
        masks = getattr(inp, "button_masks", None)
        if masks is None:
            # The guard is BACK, and the reason is worth keeping: it was removed
            # on the argument that every tier builds the real InputState, which
            # was wrong -- there are TWO InputState classes (runtime/input.py
            # and modules/moybyte/input.py), the boards use the second, and
            # removing this dropped a Lua cart into the crash-to-code editor
            # with `no attribute button_masks`. A per-frame getattr is cheap
            # insurance against an input object this file has never heard of.
            #
            # The fallback walks MOY_BUTTONS too. It used to carry its own copy
            # of the order, which made it the fourth in the tree and -- because
            # it was the copy that happened to be RIGHT -- meant the slow path
            # and the fast path disagreed about which button the kid pressed.
            held = pressed = 0
            for i, name in enumerate(MOY_BUTTONS):
                if inp.held(name):
                    held |= 1 << i
                if inp.pressed(name):
                    pressed |= 1 << i
        else:
            held, pressed = masks(MOY_BUTTONS)
        s[self._I_BTN] = held
        s[self._I_BTNP] = pressed
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
                s[self._I_BTN_P1] = h1
                s[self._I_BTNP_P1] = p1
        s[self._I_PLAYERS] = n
        if _ticks_ms is not None:
            try:
                s[self._I_TIME] = _ticks_diff(_ticks_ms(), inp.cart_start_ms)
            except Exception:  # noqa: BLE001
                pass
        # The pointer, in the cart's own coordinates. touch() reads nil when
        # down is 0, which is what "no pointer" means in SPEC.md 7.3.
        t = getattr(inp, "touch_state", None)
        if t is not None:
            try:
                x, y, down, ms = t()
                s[self._I_TX] = int(x)
                s[self._I_TY] = int(y)
                s[self._I_TD] = 1 if down else 0
                s[self._I_TMS] = int(ms)
            except Exception:  # noqa: BLE001
                s[self._I_TD] = 0
        s[self._I_KEY] = int(getattr(inp, "last_key", 0) or 0)

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

    def frame_split(self):
        """(update_ms, draw_ms) for the last tick, or None.

        The loop times `update()` and `draw()` to get its logic/render split,
        and both of those happen inside our update() -- so without this the
        diag reads `logic = the whole cart frame, render = 0`. Every per-cart
        number recorded since #67 is a logic/render pair, so the lump does not
        merely lose detail: compared against them it reads as a doubling of
        logic that never happened. The C side still has the halves; this is how
        they get back. A build whose module predates tick_split reports None
        and the loop keeps its own timing, which is wrong in the old way rather
        than crashing.
        """
        f = getattr(_moycore, "tick_split", None)
        if f is None:
            return None
        upd, drw = f()
        return (upd / 1000.0, drw / 1000.0)

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
