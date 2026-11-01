"""Moybyte M5Stack Core2 device backend -- the shared console on the classic
ESP32.

The T-Deck's `run_desktop` with this board's hardware under it: the same
320x240 glass shape over the same panel module (`moy_lcd`, the T-Deck's,
taken with this board's pins and its AXP2101 hooks), the same SD-on-the-panel-
bus store, and the shared boot spine (`device/desktop_spine.py`) over the
kernel's frame loop. What differs is input (touch, the Faces Gamepad3 and a
BLE keyboard, `core2_input.Core2Input`) and the chip: no GDMA, so no async
layer copy.
"""

from mem_census import mark as _census
from desktop_spine import build_desktop
# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_util import _ticks_ms, _ticks_diff, _diag_log
from core2_input import Core2Input
from device_canvas import SystemCanvas
from device_diag import (_diag_flush, _diag_drawbrk, _diag_draw2, _diag_i2cstat,
                         _diag_webhost, _diag_pump)

_census("imports")

# --- the panel's two revert flags -----------------------------------------------
#
# THE FLUSH OVERLAP: True ships each frame from the core-0 feeder while the
# next one renders (kick, then the drain at the next present). False is the
# serialized fallback -- every present ships and waits -- the one-flag test
# for a torn, glitched or hung panel, in one reflash.
ASYNC_FLUSH = True

# THE ASYNC LAYER COPY (#54 Stage 2 / #63): the classic ESP32 has no GDMA and
# no async-memcpy engine (moy_gfx latches every restore synchronous on it), so
# it is off and says so.
LAYER_COPY_ASYNC = False

# --- the serial dev channel ---------------------------------------------------
#
# ONE implementation for every board: `dev_channel.DevChannel` (staged from
# runtime/). Here stdin is UART0 behind the CH9102, with the 4 KB stdin ring
# (moybyte_patch_stdin_ring) between the ISR and the reader.
#
# Set False to remove the channel entirely (the loop is then byte-identical to
# one without it).
SERIAL_CMDS = True

# The idle ladder's blank rung (the kernel's, native/moy_kernel/moy_idle.c).
# Overridable before boot (`import moy_runtime; moy_runtime.POWER_SAVE_MS =
# ...`) and at runtime over the dev channel (`power <secs>`, `power off`). The
# same 5 minutes every console ships unless a board has a reason not to.
POWER_SAVE_MS = 300000          # 5 minutes; 0 disables
# The inputs as the hints know them (Workstation.use_inputs): touch, in use
# from boot; the Faces Gamepad3's "pad"; a paired BLE keyboard's keys.
INPUT_KINDS = (("touch", "touch"), ("pad", "pad"), ("ble", "keys"))

# #183: print a phase bracket around every SD session. This board has no REPL to
# interrogate once the desktop owns the loop, so the trace IS the diagnostic.
# It fires per store session -- a commit, a cover load, and every `read` a
# running compiled cart makes, which for a cart streaming its data file is
# several a second -- so it is PERF DIAG's like every other line that repeats
# (owner call 2026-09-30): kid mode prints none of it. Chasing a wedge, turn
# the diag on (`diag 1`) before the op.
SD_TRACE = True

# WHERE THE STORE LIVES WHEN THERE IS NO CARD: internal flash, the paths every
# console uses, so a board with an empty slot is a writable console. ONE store
# per boot, decided before anything is written (the T-Deck's moy_runtime has
# the history).
FLASH_CARTS_ROOT = "/moy/carts"
FLASH_UPDATE_DIR = "/moy/update"


class _Storage:
    """This boot's store -- the TF card when it takes the store, internal
    flash when it does not -- and the SESSION every store write goes through.

    Every SD session on this board is drained first and the panel SERIALIZED
    for the session's whole span (comp.sd_bracket -> moy_lcd's sd_guard). The
    bracket exists because the seed/scan PAINTS a progress frame per cart
    INSIDE the session, and since the core-0 feeder (2026-08-21) an
    unbracketed paint queues panel bands from core 0 while the VM sits inside
    an sdspi transaction on the same SPI host -- measured as a Cache/MMU panic
    at "loading cartridges 1/35". `comp.sync()` is load-bearing now that the
    flush overlaps: a frame's bands can still be in flight, and an SD op that
    overlaps a panel DMA on the shared host is the documented way to hang
    this board.

    The TRACE is the diagnostic (#183). An editor commit can wedge this board
    with nothing on serial, so each phase says its name and whichever line is
    LAST before the silence identifies the op:
      "SD > sync" -- the pre-op drain
      "SD > op"   -- the SD write itself
      "SD < op"   -- the NEXT PANEL FLUSH, i.e. the shared-bus corruption;
                     "SD = panel ok" (the frame tail) is what says it did not
                     happen.
    It prints only under PERF DIAG (`ws`, handed over once the console
    exists): a compiled cart streaming its data file opens several sessions a
    second.
    """

    def __init__(self, comp):
        self._comp = comp
        self.on_sd = False      # did the card take the store this boot
        self.traced = False     # a traced session awaits its "panel ok"
        self.ws = None          # the console, whose diag_live gates the trace

    def _bracketed(self, fn, trace=False):
        # Allocation-free on purpose: a compiled cart streaming its data file
        # comes through here several times a second, so the trace prints its
        # numbers as print() arguments rather than formatted strings, and the
        # bracket is called as a method rather than fetched as a bound one.
        if trace:
            print("SD > sync")
        t = _ticks_ms()
        comp = self._comp
        comp.sync()
        if trace:
            print("SD > op (sync ", _ticks_diff(_ticks_ms(), t), "ms)", sep="")
            t = _ticks_ms()
        bracket = hasattr(comp, "sd_bracket")
        if bracket:
            comp.sd_bracket(True)
        try:
            import moybyte_sd
            return moybyte_sd.with_sd_live(fn)
        finally:
            if bracket:
                comp.sd_bracket(False)
            if trace:
                print("SD < op ", _ticks_diff(_ticks_ms(), t), "ms", sep="")
                self.traced = True

    def load(self, boot, store):
        """The cart store: seed + scan on the card, bracketed (`with_sd_live`
        attaches once and keeps the card resident); internal flash when there
        is no card. The OTA directory goes wherever the store went -- an
        updater aimed at /sd/update on a card-less board would look on a card
        that is not there."""
        carts, root = boot.load_carts(store, CARTS, session=self._bracketed,
                                      media="SD",
                                      fallback_root=FLASH_CARTS_ROOT)
        self.on_sd = root is not None and root.startswith("/sd")
        return carts, root, (None if self.on_sd else FLASH_UPDATE_DIR)

    def session(self, fn):
        """Every store op: the bracket while the card is the store; a plain
        call while internal flash is -- it shares no bus with the panel, and
        routing it through the bracket would fail every write."""
        if not self.on_sd:
            return fn()
        return self._bracketed(fn, SD_TRACE and bool(
            getattr(self.ws, "diag_live", False)))


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts + keyboard + touch, carts on SD.

    The boot order and the service set are the shared spine's; what is here is
    the panel bring-up, the SD/panel bus gate, and the diag ticks this board's
    offline ring records; the inputs are `core2_input.Core2Input`'s, over the
    kernel's drivers.
    """
    import moy_glass
    import moy_lcd
    import moy_input

    # #54 St.2: the async layer copy's flag, set BEFORE the first canvas
    # exists (the canvas latches it in __init__); LAYER_COPY_ASYNC above says
    # why it is off on this chip.
    import device_canvas
    device_canvas.LAYER_COPY_ASYNC = LAYER_COPY_ASYNC

    # The kernel's banded compositor over moy_lcd's transport.
    comp = moy_glass.BandedCompositor(moy_lcd, nfbs=2, async_flush=ASYNC_FLUSH)
    set_backlight = comp.set_backlight
    # One surface is the game raster AND the glass here, and the shell draws
    # on it: it is a SystemCanvas, the class every other tier's shell surface
    # is and the host's 320x240 twin builds (host_app.build_workstation). At
    # font scale 1 it draws byte for byte what a plain DeviceCanvas draws; what
    # it adds is the system-surface verbs (`print_scaled`, `blit_cover`,
    # `set_font_scale`), and a shell change that calls one is green on the
    # host and on this glass alike.
    canvas = SystemCanvas(comp)
    # Its shift_last is a CPU row copy out of the buffer the feeder ships,
    # about half what drawing the rows it moves costs (#66, scroll-and-text
    # P1d's A/B on one image): a shift frame pays where its floor leaves at
    # least half of R undrawn.
    canvas.SHIFT_COST = 500
    _census("panel")
    try:
        import moybyte_diag as diag
    except Exception:  # noqa: BLE001
        diag = None

    inp = moy_input.kernel()
    tdin = Core2Input(inp)
    keyboard = tdin.keyboard

    store = _Storage(comp)

    def _before_slim(_ws):
        # Set BEFORE slim_carts so the store can reload what the diet drops.
        _ws._with_sd = store.session
        store.ws = _ws

    def _after_services(ws):
        _diag_log("boot", "desktop running touch=%d poller=%d"
                  % (1 if tdin.touch is not None and tdin.touch.available else 0,
                     1 if moy_input.task_stack_free() else 0),
                  diag)
        # #66/#67 SRAM diet: everything needing boot-time internal RAM has taken
        # it by here, so the Lua allocator's headroom floor drops 48->24KB. BOTH
        # runtimes -- moycore has its own allocator with its own floor, and a
        # cart left on the 48KB floor sits at ~97% PSRAM, the measured-2x-slower
        # regime, with nothing saying so.
        for _mod in ("moy_lua", "moycore"):
            try:
                _m = __import__(_mod)
                _fl = getattr(_m, "set_sram_floor", None)
                if _fl is not None:
                    _diag_log("boot", "%s sram floor=%dKB" % (_mod, _fl(24)), diag)
            except Exception:  # noqa: BLE001
                pass

    d = build_desktop("Moybyte", "core2", comp, canvas, set_backlight, inp,
                      inputs=lambda: tdin.build(canvas.w, canvas.h),
                      keyboard=keyboard, seed_carts=CARTS,
                      power_save_ms=POWER_SAVE_MS,
                      load_carts=store.load, with_sd=store.session,
                      before_slim=_before_slim, after_services=_after_services,
                      input_kinds=INPUT_KINDS,
                      serial=SERIAL_CMDS,
                      log=lambda tag, msg: _diag_log(tag, msg, diag),
                      fps_cap=fps_cap)
    ws = d.ws
    serial = d.serial

    # The offline diag ring's per-frame Python: this board's own, run inside
    # the frame upcall (never an upcall of its own). HITCH and LOOP are the
    # kernel's lines (native/moy_kernel/moy_loop.c), said on serial as they
    # happen; the diag tick takes the last of each into the ring too.
    import moy_loop
    _diag_at = [_ticks_ms() + 3000]
    _flush_at = [_ticks_ms() + 5000]
    _prev_cart_err = [None]
    _cart_prev = [False]

    def _after_frame(drew):
        # #183: close the SD bracket. A DRAWN frame here means the first panel
        # flush after the SD session completed, so the bus survived it.
        if store.traced and drew:
            store.traced = False
            print("SD = panel ok")
        if diag is not None:
            _ce = getattr(ws, "cart_error", None)
            if _ce is not None and _ce != _prev_cart_err[0]:
                _prev_cart_err[0] = _ce
                _diag_log("cart error", _ce, diag)
                _diag_flush(diag, ws)
            elif _ce is None:
                _prev_cart_err[0] = None
        _tnow = _ticks_ms()
        _live = bool(getattr(ws, "diag_live", False))
        if diag is not None and _ticks_diff(_tnow, _diag_at[0]) >= 0:
            _diag_at[0] = _tnow + 3000
            try:
                diag.ECHO_LIVE = _live
            except Exception:  # noqa: BLE001
                pass
            # Every line this tick writes is PERF DIAG's (owner call
            # 2026-09-30): in kid mode nothing periodic is formatted, printed
            # or ringed. The window still closes every tick.
            if _live:
                _diag_drawbrk(diag, ws)
                _diag_draw2(diag, ws)
                for _line in moy_loop.diag_take():
                    if _line is not None:
                        _tag, _sp, _msg = _line.partition(" ")
                        diag.ring_only(_tag, _msg)
                _diag_pump(diag, comp)
                _diag_i2cstat(diag, None, tdin.touch)
                _diag_webhost(diag, ws)
                if serial is not None:
                    serial.report(diag)
        # #68 kid mode: the periodic diag->SD write costs 80-120ms and IS a
        # felt stutter during play, so it needs PERF DIAG *and* DIAG SD LOG.
        # The cart-exit and crash flushes stay unconditional.
        _cart_now = ws.cart is not None
        if diag is not None and _cart_prev[0] and not _cart_now:
            _diag_flush(diag, ws)              # cart exited: persist the ring
        _cart_prev[0] = _cart_now
        if (diag is not None and _live and getattr(ws, "diag_sd", False)
                and _ticks_diff(_tnow, _flush_at[0]) >= 0):
            _flush_at[0] = _tnow + (20000 if ws.cart is not None else 5000)
            _diag_flush(diag, ws)

    # The input task reads the touch and the gamepad while the frame paces
    # (kicked at the tail, #69).
    return d.run(kick_at_tail=True, after_frame=_after_frame)
