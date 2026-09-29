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

EVERY Lua cart runs here. moybyte's superset verbs (scenes,
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
    from lua_ext import (PRELUDE_HANDLES, MOY_BUTTONS, cart_chunks,
                         LIBMOY_VERBS, NOT_REGISTRABLE, install_handles,
                         snap_slots, audio_ops, snap_shared, sync_view,
                         drain_audio)
except ImportError:                      # host tests importing the device module
    from runtime.lua_ext import (PRELUDE_HANDLES, MOY_BUTTONS, cart_chunks,
                                 LIBMOY_VERBS, NOT_REGISTRABLE,
                                 install_handles, snap_slots, audio_ops,
                                 snap_shared, sync_view, drain_audio)

try:
    from widgets import pointer_state
except ImportError:                      # host tests importing the device module
    from runtime.widgets import pointer_state

try:
    import moycore as _moycore
except ImportError:                      # a build without the module
    _moycore = None

try:
    import moy_wasm as _moy_wasm         # the compiled cart's engine
except ImportError:                      # a build without it: no wasm runtime
    _moy_wasm = None

# The cart's clock: ms since the Player's stamp, which snap_shared writes into
# the snapshot's time slot -- the base libmoy's time() adds the milliseconds
# inside the tick to (modmoycore.c's h_time_ms).
try:
    from ticks import _since_ms
except ImportError:                      # host tests importing the device module
    from runtime.ticks import _since_ms

# What NOT to register on top of libmoy's table -- LIBMOY_VERBS (the names
# libmoy's own binding installs) and NOT_REGISTRABLE (ours, each excluded for
# its own reason) -- is imported above from lua_ext, with the full rationale
# beside the declaration there. So is the MOY_BUTTONS bit order.
#
# Read lua_ext's note (the d-pad incident) before duplicating any of them
# back here.


_P8_BUFFERS = []


def _p8_buffers():
    """The PICO-8 machine's 64KB memory and 0x4300 ROM snapshot, once -- or
    an empty list when the heap cannot give them, which the caller passes on
    as "no machine".

    The machine is OPTIONAL (moy.h: a host that does not open it offers no
    __moy_p8 globals, and the port shim keeps its Lua for exactly that), so
    failing to allocate it must not fail the cart. It did: on the Guition,
    with the desk and its store up, the S3's MicroPython heap has hundreds of
    KB free and no 64KB run of it, and every Lua cart -- conformance scenes
    that never touch the machine included -- died with "MemoryError:
    allocating 65536 bytes" before drawing a pixel (on glass, 2026-09-03).
    A collect first is what usually finds the run; a run that still is not
    there is a slower p8 port, not a dead one. Retried per run rather than
    remembered, because fragmentation changes."""
    if not _P8_BUFFERS:
        import gc
        gc.collect()
        try:
            mem = bytearray(65536)
            rom = bytearray(0x4300)
        except MemoryError:
            return []
        _P8_BUFFERS.append(mem)
        _P8_BUFFERS.append(rom)
    return _P8_BUFFERS


def reserve_p8_memory():
    """Take the PICO-8 machine's buffers NOW, while the heap is still whole.

    Called from device_boot ahead of the cart-store scan, because that scan is
    what fragments the heap: after it an S3 has hundreds of KB free and no
    64KB run of it, and _p8_buffers' first-run allocation fails -- measured on
    the Guition, where every p8 port then ran without its machine. 81KB held
    for the whole session is the price of a port running at C speed on every
    board rather than only on the ones whose heap happened to have room. True
    when the machine is provisioned; False on a build with no moycore or no
    room even at boot, in which case first-run allocation stays the fallback."""
    if _moycore is None or not hasattr(_moycore, "p8_memory"):
        return False
    return bool(_p8_buffers())


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
        # The slots and op codes lua_ext's shared bodies take, resolved once --
        # the same reason the SNAP_* lookups above are bound at construction.
        self._I_SNAP = snap_slots(_moycore)
        self._aq_ops = audio_ops(_moycore)
        self._touch_out = [0, 0, 0, 0]   # reused; see widgets.pointer_state
        self._I_QUIT = _moycore.SNAP_QUIT
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
            bufs = _p8_buffers()
            if bufs:
                _moycore.p8_memory(*bufs)
            else:
                _moycore.p8_memory(None, None)      # no machine this run

        _moycore.run_begin(
            canvas._buf, canvas.w, canvas.h, wire,
            getattr(sheet, "pix", None),
            getattr(tilemap, "cells", None),
            getattr(tilemap, "w", 0) or 0, getattr(tilemap, "h", 0) or 0,
            self.snap, self.aq, self.pmem_img, cfg, flags, True)
        # The superset, on top of libmoy's table and BEFORE the cart runs.
        # Anything callable in the namespace that libmoy did not already
        # install: registering a name libmoy owns would shadow the C verb with
        # a trampoline, which is the opposite of the point.
        try:
            for name in ns:
                if (name not in LIBMOY_VERBS and name not in NOT_REGISTRABLE
                        and callable(ns[name])):
                    _moycore.register(name, ns[name])
            # The object-valued verbs and their Lua wrappers -- the same two
            # halves moy_lua uses, from the same source. Without this a cart
            # calling make_layer() gets "unsupported value" back from the
            # trampoline and the whole run falls to the old runtime, which is
            # what sakura_lua/brick_siege/ray did before this landed.
            self._layers, self._images = install_handles(ns, _moycore.register)
            err = _moycore.exec(PRELUDE_HANDLES, "prelude")
            if err:
                raise RuntimeError(err)
            # The namespace's OWN prelude, if it brought one (the text console's
            # `print`/`input` binding -- see runtime/lua_host.py's twin of this).
            # A string, so the registration loop above skipped it.
            extra = ns.get("_moy_prelude")
            if extra:
                err = _moycore.exec(extra, "prelude")
                if err:
                    raise RuntimeError(err)
        except Exception:  # noqa: BLE001 -- a bad verb must not strand the VM
            _moycore.close()
            raise
        # The cart's scripts in one call (SPEC.md 4, runtime/lua_ext.py): main
        # keeps the "@cart" name, so a runtime error in it renders `cart:12:`
        # -- what player._lua_cart_line parses for the crash-to-code panel
        # (#24) -- and load() is where the verb profiler arms, ahead of the
        # first chunk rather than after a shim that already captured its verbs.
        err = _moycore.load(cart_chunks(ns, src))
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
        snap_shared(s, inp, self._I_SNAP, pointer_state, self._touch_out, _since_ms)
        s[self._I_KEY] = int(getattr(inp, "last_key", 0) or 0)

    def _sync_view(self):
        self._view = sync_view(self.ws, _moycore.view(), self._view)

    def _drain_audio(self):
        n = self.aq[0]
        if n <= 0:
            return
        self.aq[0] = 0
        aq = self.aq
        slots = _moycore.AQ_SLOTS
        drain_audio(self.ns, self._aq_ops,
                    ((aq[1 + i * slots], aq[2 + i * slots], aq[3 + i * slots])
                     for i in range(n)))

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

    # The Player's scheduler (#217) clears this for a logic-only tick. A module
    # built before `tick` took the flag draws every tick, which is the fused
    # frame it always ran -- the divisor then saves the composite and flush
    # on those frames, and not the cart's own drawing.
    draw_next = True
    _tick_draw = None

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
        td = self._tick_draw
        if td is None:
            td = MoycoreRun._tick_draw = bool(getattr(_moycore, "TICK_DRAW", 0))
        err = _moycore.tick(dt, self.draw_next) if td else _moycore.tick(dt)
        # A LUA cart ends itself the same way a Python one does. libmoy's quit()
        # is a host callback that sets SNAP_QUIT (h_quit), and nothing read it:
        # the flag was written on every tier and translated on none, so `quit()`
        # was a no-op for every Lua cart -- including the textmode(True) carts
        # the cart API says MUST provide their own exit, because hold-BACKSPACE
        # cannot reach one. Route it into the flag the Player already honours
        # after _update (player.tick), and clear the slot so one quit is one
        # exit.
        if self.snap[self._I_QUIT]:
            self.snap[self._I_QUIT] = 0
            inp = getattr(self.ws, "input", None)
            if inp is not None:
                inp.cart_quit = True
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


# -- the compiled cart (docs/wasm_tier_plan_2026-09.md, phase 3) ------------

def aot_path(cart_dir, main, chip):
    """Where a cart's compiled module for `chip` sits: its `main` with `.wasm`
    replaced by `.<chip>.aot`, in the cart's folder. How a board finds the
    module is host policy (the plan); tools/wasm_cart.py's `aot_name` states
    the same rule and tests/test_wasm_cart.py holds the two equal."""
    stem = main[:-5] if main.endswith(".wasm") else main
    return "%s/%s.%s.aot" % (cart_dir, stem, chip)


def wasm_head(path):
    """The canonical module's bytes up to the end of its memory section --
    what moy_wasm_check reads the declared memory from. Sections run in id
    order and memory (5) precedes code and data, so this is the module's
    small head, never its body. Everything read, when the file ends first."""
    with open(path, "rb") as f:
        data = f.read(8)
        if data[:4] != b"\0asm":
            return data
        while True:
            sid = f.read(1)
            if not sid:
                return data
            data += sid
            n = shift = 0
            while True:
                b = f.read(1)
                if not b:
                    return data
                data += b
                n |= (b[0] & 0x7F) << shift
                shift += 7
                if not b[0] & 0x80:
                    break
            if sid[0] > 5:
                return data
            body = f.read(n)
            data += body
            if sid[0] == 5 or len(body) < n:
                return data


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(4096)
            if not b:
                break
            h.update(b)
    return "".join("%02x" % c for c in h.digest())


class CartFrame:
    """A compiled cart's frame on its way to the glass straight from the
    cart's memory (libmoy's frame hand-off, moy_fold.h's frame fold).

    While a run holds one, every blit leaves its frame where the cart made it
    and the game canvas is not written: the console's composite point hands
    the frame to the system canvas (`present_frame`), whose flush snapshots it
    and resolves it band by band. Anything that must draw over the frame first
    `settle`s it into the canvas, the same bytes the blit would have written;
    the binding does the same itself before the cart's next hook. The frame's
    snapshot scratch is this run's, taken at the first present and freed when
    the run closes, once nothing still reads it."""

    # What moy_fold copies of the game canvas over a frame: MOY_FOLD_MAX_PATCHES
    # rects, MOY_FOLD_PATCH_BYTES of them (native/moy_flush/moy_fold.h).
    MAX_PATCHES = 4
    PATCH_BYTES = 8192

    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.lut = array("H", bytearray(512))    # a palette frame's colours
        self.rects = array("h", bytearray(2 * 4 * self.MAX_PATCHES))
        self.nrects = 0
        self.kept_off = 0                # where the last frame shown sits in the scratch
        self.fmt = 0                     # its layout: moy_fold's LE565 1 / IDX8 2
        self._scratch = None

    def take(self):
        """The frame to show, or None: the owed one, as a view into the
        cart's memory (w*h bytes are indices whose colours are now in `lut`,
        2*w*h bytes little-endian RGB565) -- or, on a frame the cart did not
        replace it, the last one shown, still in the scratch while the canvas
        lacks it."""
        view = _moycore.frame(self.lut)
        if view is not None:
            self.fmt = 2 if len(view) == self.w * self.h else 1
            return view
        s = self._scratch
        if s is None or not self.fmt or not _moycore.frame_kept():
            return None
        n = self.w * self.h * (1 if self.fmt == 2 else 2)
        return memoryview(s)[self.kept_off:self.kept_off + n]

    def settle(self, canvas=None):
        """Write the owed frame into the game canvas. Given the canvas, the
        opaque rects already painted over the frame keep their pixels."""
        n = self.nrects
        self.nrects = 0
        if not n or canvas is None:
            _moycore.frame_settle()
            return
        buf = canvas._buf
        stride = getattr(canvas, "_stride", canvas.w)
        r = self.rects
        kept = []
        for i in range(n):
            x = max(0, r[4 * i])
            y = max(0, r[4 * i + 1])
            x1 = min(canvas.w, r[4 * i] + r[4 * i + 2])
            y1 = min(canvas.h, r[4 * i + 1] + r[4 * i + 3])
            for row in range(y, y1):
                a = 2 * (row * stride + x)
                b = a + 2 * (x1 - x)
                if b > a:
                    kept.append((a, bytes(buf[a:b])))
        _moycore.frame_settle()
        for a, px in kept:
            buf[a:a + len(px)] = px

    def patch(self, x, y, w, h):
        """The console is about to paint an opaque rect over the frame on the
        game canvas: the flush shows the canvas there and the frame elsewhere.
        False when the fold holds no more rects -- the painter settles."""
        n = self.nrects
        if n >= self.MAX_PATCHES:
            return False
        r = self.rects
        r[4 * n] = x
        r[4 * n + 1] = y
        r[4 * n + 2] = w
        r[4 * n + 3] = h
        self.nrects = n + 1
        return True

    def presented(self, kept, off):
        """The frame is on its way to the glass; `kept[off:]` is the copy
        the binding writes the canvas from if the cart draws before its next
        blit."""
        _moycore.frame_presented(kept, off)
        self.kept_off = off
        self.nrects = 0

    def scratch(self, n):
        """At least `n` bytes of DMA-reachable PSRAM for the frame's
        snapshot, or None when there are none to be had."""
        s = self._scratch
        if s is not None and len(s) >= n:
            return s
        self._free()
        try:
            import moy_alloc
            s = moy_alloc.alloc(n, moy_alloc.MEMORY_SPIRAM | moy_alloc.MEMORY_DMA)
        except (ImportError, AttributeError, MemoryError):
            s = None
        self._scratch = s
        return s

    def close(self, comp):
        """The run is over: wait out whatever still reads the scratch -- an
        in-flight flush's synthesis, the snapshot's copy, an arm no flush has
        taken yet -- and hand it back."""
        if self._scratch is None:
            return
        for name in ("fold_fence", "snap_fence", "disarm_scale_fold"):
            fn = getattr(comp, name, None)
            if fn is not None:
                fn()
        self._free()

    def _free(self):
        s = self._scratch
        self._scratch = None
        if s is not None:
            try:
                import moy_alloc
                moy_alloc.free(s)
            except (ImportError, AttributeError, ValueError):
                pass


class WasmRun(MoycoreRun):
    """One compiled cart run: moycore's console with libmoy's wasm import
    table on it and the engine (moy_wasm) running the cart's module on its own
    thread. The same shape as MoycoreRun -- the snapshot, the tick, quit, view,
    the audio queue and pmem are that class's -- so only construction is
    written here."""

    def __init__(self, ws, ns, src):
        del src                          # a compiled cart has no source text
        if (_moycore is None or not getattr(_moycore, "WASM", 0)
                or _moy_wasm is None):
            raise RuntimeError("moycore has no wasm engine in this build")
        project = getattr(ws, "project", None)
        cart = getattr(project, "cart", None) or ws.cart or {}
        path = cart["path"]
        main = cart.get("main", "main.wasm")
        pages = cart.get("memory")
        if not pages:
            raise RuntimeError('refused: the manifest declares no "memory"')
        module = aot_path(path, main, _moy_wasm.CHIP)
        try:
            open(module, "rb").close()
        except OSError:
            raise RuntimeError("no module compiled for this board (%s)"
                               % module[len(path) + 1:])
        head = wasm_head(path + "/" + main)
        sha = _sha256_file(path + "/" + main)
        self.ws = ws
        self.ns = ns
        self._dt = 0.0
        canvas = ws.canvas
        sheet = getattr(project, "sheet", None) if project is not None else None
        tilemap = getattr(project, "tilemap", None) if project is not None else None
        flags = getattr(project, "flags", None) if project is not None else None
        self._I_BTN = _moycore.SNAP_BTN
        self._I_BTNP = _moycore.SNAP_BTNP
        self._I_SNAP = snap_slots(_moycore)
        self._aq_ops = audio_ops(_moycore)
        self._touch_out = [0, 0, 0, 0]
        self._I_QUIT = _moycore.SNAP_QUIT
        self._I_KEY = _moycore.SNAP_KEY
        self.snap = array("i", bytearray(4 * _moycore.SNAP_LEN))
        self.aq = array("h", bytearray(2 * (1 + _moycore.AQ_SLOTS * self.AUDIO_MAX)))
        self.pmem_img = array("i", bytearray(4 * 256))
        pmem = getattr(ws, "pmem", None)
        cells = getattr(pmem, "cells", None) if pmem is not None else None
        if cells is not None:
            for i in range(min(256, len(cells))):
                self.pmem_img[i] = int(cells[i])
        wire = getattr(canvas, "_wire", None)
        import device_canvas
        if wire is None:
            wire = device_canvas._PAL565_WIRE_BUF
        swapped = device_canvas.PAL565_WIRE is not device_canvas.PAL565
        cfg = ns.get("_moy_cfg") if hasattr(ns, "get") else None
        self._layers = self._images = None
        _moycore.run_begin(
            canvas._buf, canvas.w, canvas.h, wire,
            getattr(sheet, "pix", None),
            getattr(tilemap, "cells", None),
            getattr(tilemap, "w", 0) or 0, getattr(tilemap, "h", 0) or 0,
            self.snap, self.aq, self.pmem_img, cfg, flags, False)
        self.snap[_moycore.SNAP_PLAYERS] = 1
        # Every `read` the cart makes runs inside the store's gate, as every
        # other store access does: on the T-Deck it drains the panel's flush
        # first, because the card shares the panel's SPI bus. The owner's
        # Unknown sources setting, as it stands at this load, decides whether
        # a module with no signature may load; the engine checks everything
        # else either way. The engine raises MemoryError when it cannot hold
        # the module file, and a run left open here would refuse every later
        # cart's run_begin.
        try:
            err = _moycore.wasm_open(module, head, int(pages), sha, path,
                                     swapped, getattr(ws, "_with_sd", None),
                                     bool(getattr(ws, "unknown_sources", False)))
        except BaseException:
            _moycore.close()
            raise
        if err:
            try:
                _moycore.close()
            finally:
                raise RuntimeError(err)
        self._view = None
        self._sync_view()
        self.init = None                 # _init ran inside wasm_open
        self.update = self._update
        self.draw = self._draw_noop
        # Frames go to the glass from the cart's memory where the system
        # canvas can show them that way; everywhere else the blit writes the
        # canvas as it always has.
        self.frame = None
        if getattr(getattr(ws, "sys_canvas", None), "presents_frames", False):
            self.frame = CartFrame(canvas.w, canvas.h)
            ws.cart_frame = self.frame
            _moycore.take_frames(True)

    def close(self):
        f = self.frame
        if f is not None:
            self.frame = None
            if getattr(self.ws, "cart_frame", None) is f:
                self.ws.cart_frame = None
            f.close(getattr(self.ws, "comp", None))
        MoycoreRun.close(self)


def make_moycore_runtime(ws):
    """The Lua runtime factory, or None when unavailable."""
    if _moycore is None:
        return None

    def _make(ns, src):
        return MoycoreRun(ws, ns, src)
    return _make


# The most linear memory a footprint is asked about: 1 GiB, past any board's
# PSRAM, so a larger declaration is simply too big and never overflows the
# engine's machine word.
_MAX_PAGES = 16384


class WasmRuntime:
    """`ws.runtimes["wasm"]`: called with `(ns, src)` it starts a WasmRun,
    and before it does, the Player asks it what the cart's load needs
    (`footprint`) and what this board can give (`memory`)."""

    def __init__(self, ws):
        self.ws = ws

    def __call__(self, ns, src):
        return WasmRun(self.ws, ns, src)

    def footprint(self, cart):
        """(total, block) the cart's load takes from PSRAM, by the engine's
        own sizing (moy_wasm.footprint): its declared memory, this chip's
        module file and the pool that module gets. None when there is
        nothing to measure -- no "memory", no module for this chip -- and the
        load's own refusal says why."""
        pages = cart.get("memory")
        path = cart.get("path")
        if not pages or not path:
            return None
        module = aot_path(path, cart.get("main", "main.wasm"), _moy_wasm.CHIP)
        import os
        gate = getattr(self.ws, "_with_sd", None)
        try:
            size = (gate(lambda: os.stat(module)[6]) if gate is not None
                    else os.stat(module)[6])
        except OSError:
            return None
        if not size:
            return None
        return _moy_wasm.footprint(min(int(pages), _MAX_PAGES) * 65536, size)

    def memory(self):
        """(free, largest block) of PSRAM, the engine's own report."""
        m = _moy_wasm.mem()
        return m[3], m[4]


def make_wasm_runtime(ws):
    """The compiled-cart runtime, or None when this build has no engine
    (moycore's WASM flag is the build's own answer)."""
    if (_moycore is None or not getattr(_moycore, "WASM", 0)
            or _moy_wasm is None):
        return None
    return WasmRuntime(ws)


def make_runtimes(ws):
    """`ws.runtimes` for this build: every runtime the image carries, by the
    manifest name a cart gives it. An absent key is a runtime this build lacks,
    and the Player's runtime-missing panel is what a cart naming it gets."""
    out = {}
    for name, make in (("lua", make_moycore_runtime), ("wasm", make_wasm_runtime)):
        rt = make(ws)
        if rt is not None:
            out[name] = rt
    return out
