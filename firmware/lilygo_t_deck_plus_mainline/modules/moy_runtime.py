"""Moybyte T-Deck device backend -- the shared console on the S3.

The `run_desktop` that had the least to invent: the shared boot spine
(`device/desktop_spine.py` over `runtime/device_boot.py`) owns the splash,
the cart seed/scan, the Lua probe, the service wiring and the OTA verdict,
the kernel owns the frame (native/moy_kernel/moy_loop.c), and the shared
`console.Workstation` owns every pixel. What is
left here is the part that is genuinely this board's hardware.

This port replaced the lvgl_micropython fork build of the same glass (deleted
2026-08-17). What structurally changed with it: the panel machine is
`native/moy_lcd`, one C module that also owns the SPI host, with the
kernel's `moy_glass.BandedCompositor` the ping-pong over its kick/drain
split (the fork's Python `moy_compositor` + `lcd_bus` banding died with it,
strategy retained -- `flush()` queues the first bands and returns, the rest
are fed while this loop renders the next frame, `comp.sync()` is a real
fence); there is no LVGL; and the SERIAL DEV CHANNEL works, which on the fork
it never did.

Everything the console is built from -- input drivers, SD lifecycle, cart API,
audio backend, WiFi service, OTA updater -- is staged from the shared
`runtime/` and `device/` trees by `board.toml`: one console, per-board hardware
underneath.
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
from tdeck_input import TDeckInput
from device_canvas import DeviceCanvas
from device_diag import (_diag_flush, _diag_hitch,
                         _diag_drawbrk, _diag_draw2, _diag_loop, _diag_i2cstat, _diag_webhost,
                         _diag_pump, HITCH_MS)

_census("imports")

# --- the panel's two revert flags -----------------------------------------------
#
# THE FLUSH OVERLAP: True ships each frame from the core-0 feeder while the
# next one renders (kick, then the drain at the next present). False is the
# serialized fallback -- every present ships and waits -- the one-flag test
# for a torn, glitched or hung panel, in one reflash.
ASYNC_FLUSH = True

# THE ASYNC LAYER COPY (#54 Stage 2 / #63), applied to device_canvas by
# run_desktop. The copy is a GDMA PSRAM->PSRAM blit, which starved the SPI
# FIFO into garbage bands while the panel DMA read PSRAM (2026-07-03); since
# the SRAM-bounce flush (#66) moy_lcd's DMA reads only the internal bounce
# slots, so the copy is safe here. It arms only for a screen-wide layer
# restored at (0, 0) -- sakura, letter_blitz and hop_quest on the shipped
# roster. TO REVERT: False, one reflash; independent of ASYNC_FLUSH, so a
# torn or stale frame can be pinned on one by flipping it alone.
LAYER_COPY_ASYNC = True

# --- the serial dev channel ---------------------------------------------------
#
# ONE implementation for every board: `dev_channel.DevChannel` (staged from
# runtime/), which reads stdin one byte at a time after poll(0) -- NEVER
# readline, which blocks per character -- and disarms itself, out loud, if
# kilobytes arrive without a single complete command. Its module docstring
# carries the reader's design; what is THIS board's alone is mechanism 2 of the
# original stall: `MICROPY_HW_ENABLE_UART_REPL` would put UART0's ISR on the
# same stdin ring buffer, so noise on the floating U0RXD (GPIO44, exposed on
# the expansion header) reads exactly like typed input. The board header keeps
# UART_REPL off (#201); if `SERIAL rx=` ever climbs on an idle board, that is
# the mechanism to suspect. (The full history of why RX was thought impossible
# here -- and why the fork's never worked -- is in this board's README and
# git history at 4faf07a/24ccb0b.)
#
# Set False to remove the channel entirely (the loop is then byte-identical to
# one without it).
SERIAL_CMDS = True

# The idle ladder's blank rung (the kernel's, native/moy_kernel/moy_idle.c).
# Overridable before boot (`import moy_runtime; moy_runtime.POWER_SAVE_MS =
# ...`) and at runtime over the dev channel (`power <secs>`, `power off`). The
# same 5 minutes every console ships unless a board has a reason not to.
POWER_SAVE_MS = 300000          # 5 minutes; 0 disables

# #183: print a phase bracket around every SD session. This board has no REPL to
# interrogate once the desktop owns the loop, so the trace IS the diagnostic.
# It fires per store session -- a commit, a cover load, and every `read` a
# running compiled cart makes, which for a cart streaming its data file is
# several a second -- so it is PERF DIAG's like every other line that repeats
# (owner call 2026-09-30): kid mode prints none of it. Chasing a wedge, turn
# the diag on (`diag 1`) before the op.
SD_TRACE = True

# WHERE THE STORE LIVES WHEN THERE IS NO CARD. This board's carts normally live
# on the TF card (moy_carts.CARTS_DIR, /sd/moybyte/carts) -- but a T-Deck with
# an empty slot used to boot to the EMBEDDED carts with a None root, which
# `wire_workstation_core` turns into can_manage=False: a read-only console, no
# saving, no editing, no importing, and the only explanation one serial line on
# a board whose USB-CDC RX is dead under the desktop. These are the same paths
# the P4 has always used for the same reason, and a card put in later is picked
# up on the next boot -- ONE store per boot, decided before anything is written,
# because two live stores is the arrangement where a kid's save goes to the one
# they are not looking at.
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
    offline ring records; the input trio is `tdeck_input.TDeckInput`'s, over
    the kernel's drivers.
    """
    import moy_glass
    import moy_lcd
    import moy_input

    # #54 St.2: arm the async layer copy BEFORE the first canvas exists.
    # `DeviceCanvas` latches `_async_ok` in __init__, so this has to precede the
    # construction below or it reaches nothing. It is an assignment rather than
    # an edit because `device_canvas.py` is staged from the shared `device/`
    # tree and is not this board's to change; LAYER_COPY_ASYNC above says why
    # this panel can take it.
    import device_canvas
    device_canvas.LAYER_COPY_ASYNC = LAYER_COPY_ASYNC

    # The kernel's banded compositor over moy_lcd's transport.
    comp = moy_glass.BandedCompositor(moy_lcd, nfbs=2, async_flush=ASYNC_FLUSH)
    set_backlight = comp.set_backlight
    canvas = DeviceCanvas(comp)
    _census("panel")
    try:
        import moybyte_diag as diag
    except Exception:  # noqa: BLE001
        diag = None

    inp = moy_input.kernel()
    tdin = TDeckInput(inp)
    keyboard = tdin.keyboard

    store = _Storage(comp)

    def _before_slim(_ws):
        # Set BEFORE slim_carts so the store can reload what the diet drops.
        _ws._with_sd = store.session
        store.ws = _ws

    def _after_services(ws):
        _diag_log("boot", "desktop running kb=%d ball=%d touch=%d poller=%d"
                  % (1 if keyboard is not None and keyboard.available else 0,
                     1 if tdin.ball is not None else 0,
                     1 if tdin.touch is not None and tdin.touch.available else 0,
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

    d = build_desktop("Moybyte", "tdeck", comp, canvas, set_backlight, inp,
                      inputs=lambda: tdin.build(canvas.w, canvas.h),
                      keyboard=keyboard, seed_carts=CARTS,
                      power_save_ms=POWER_SAVE_MS,
                      load_carts=store.load, with_sd=store.session,
                      before_slim=_before_slim, after_services=_after_services,
                      ble_keyboard=tdin.ble_keyboard,
                      serial=SERIAL_CMDS,
                      log=lambda tag, msg: _diag_log(tag, msg, diag),
                      fps_cap=fps_cap)
    ws = d.ws
    serial = d.serial

    # The offline diag ring's per-frame Python: this board's own, run inside
    # the frame upcall (never an upcall of its own). The frame's numbers are
    # the kernel's -- the last frame's work and sleep (`moy_loop.last()`),
    # the stages in its meters -- so LOOP splits the frame into its work, the
    # console's share and the sleep, and HITCH names the frame that spiked.
    import moy_loop
    _diag_at = [_ticks_ms() + 3000]
    _flush_at = [_ticks_ms() + 5000]
    _prev_cart_err = [None]
    _cart_prev = [False]
    # [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep, hi, hp] ms per frame,
    # averaged and zeroed every diag tick. The stages the kernel runs in C
    # (kbd, inp, sb, web) are its meters' now and read None here.
    _acc = [0] * 12
    _t = {"diag": 0, "sd": 0}

    def _after_frame(drew):
        last = moy_loop.last()
        elapsed = last >> 16
        sleep_ms = last & 0xFFFF
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, -1, -1, -1, -1,
                        _t["diag"], _t["sd"], -1)
        _acc[0] += 1
        _acc[1] += elapsed
        _acc[7] += _t["diag"]
        _acc[8] += _t["sd"]
        _acc[9] += sleep_ms
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
        _t["diag"] = 0
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
                _diag_loop(diag, ws, _acc)
                _diag_pump(diag, comp)
                _diag_i2cstat(diag, keyboard, tdin.touch)
                _diag_webhost(diag, ws)
                if serial is not None:
                    serial.report(diag)
            for _i in range(12):
                _acc[_i] = 0
            _t["diag"] = _ticks_diff(_ticks_ms(), _tnow)
        # #68 kid mode: the periodic diag->SD write costs 80-120ms and IS a
        # felt stutter during play, so it needs PERF DIAG *and* DIAG SD LOG.
        # The cart-exit and crash flushes stay unconditional.
        _cart_now = ws.cart is not None
        _t["sd"] = 0
        if diag is not None and _cart_prev[0] and not _cart_now:
            _t["sd"] = _diag_flush(diag, ws)   # cart exited: persist the ring
        _cart_prev[0] = _cart_now
        if (diag is not None and _live and getattr(ws, "diag_sd", False)
                and _ticks_diff(_tnow, _flush_at[0]) >= 0):
            _flush_at[0] = _tnow + (20000 if ws.cart is not None else 5000)
            _t["sd"] = _diag_flush(diag, ws)

    # The trackball is this board's arrow keys; its input task reads the
    # keyboard and the GT911 while the frame paces (kicked at the tail, #69).
    return d.run(ball=tdin.ball, kick_at_tail=True, after_frame=_after_frame)
