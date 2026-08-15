# Moybyte v0.4 workstation -- DEVICE side.
#
# Boots the fantasy workstation on the T-Deck: a cartridge launcher + the carts,
# navigated with the keyboard/trackball, each cart drawn through the native
# moy_compositor. The drawing API (cls/pix/rect/rectb/circ/circb/spr/print/btn/...,
# TIC-80 style: rect/circ are filled, rectb/circb are outlines) matches the host
# `runtime/` reference, so cartridges are portable; only the
# canvas backend differs (framebuf over the compositor buffer + palette->RGB565).
#
# v1 embeds the cart sources; loading real .moy files from SD is the follow-on.

import time
from array import array

# Editor cores (CodeEditor / SpriteSheet / PaintEditor) are backend-agnostic and
# shared verbatim with the host (canonical: runtime/editors.py; build.sh stages a
# copy into modules/ so it freezes here as the top-level module `editors`).
from editors import CodeEditor, PaintEditor, SpriteSheet
from console import NAMES, Pointer, Workstation, _cursor_delta, draw_splash, wire_workstation_core
from carts_data import CARTS  # build-time generated from system_carts/ (tools/gen_device_carts.py)
# Leaf tick + diag helpers (extracted to device_util.py so every device cluster can
# import them without a moy_runtime cycle -- see device_util.py's module docstring).
from device_util import (
    _ticks_ms, _ticks_diff, _ticks_us, _diag_note, _diag_log, sram_census,
)
# The device WiFi service (#38, extracted to device_wifi.py). run_desktop calls
# make_wifi()/autoconnect_wifi(); DeviceWifi is the injected `wifi` backend.
from device_wifi import DeviceWifi, make_wifi, autoconnect_wifi
# The pointer input drivers (extracted to device_input.py): the trackball + GT911
# touch. run_desktop constructs TrackBall()/Touch(...) and feeds them to Pointer.
from device_input import TrackBall, Touch
# Serial diagnostics (extracted to device_diag.py): the between-frames logging
# functions run_desktop calls when perf capture is on. HITCH_MS + _CALIB_DONE are
# the loop's hitch threshold + one-shot calib flag (mutated in place).
from device_diag import (
    _diag_flush, _diag_perf_sample, _diag_hitch, _diag_drawbrk, _diag_draw2,
    _diag_draw3, _diag_loop, _diag_chromebrk, _diag_layerbrk,
    _diag_homebrk, _diag_pump,
    _diag_luamem,
    _diag_i2cstat, _diag_calib, _diag_gc, HITCH_MS, _CALIB_DONE,
)
# The device AUDIO backend (#16, extracted to device_audio.py). run_desktop wires
# ws.make_audio = make_audio; DeviceAudio is the injected I2S backend.
from device_audio import DeviceAudio, make_audio
# The device DRAWING backend (extracted to device_canvas.py): the RGB565 canvas +
# scroll layers + sprite images. run_desktop constructs DeviceCanvas(comp);
# make_api (device_api.py) imports Image / _decode_moyimg / _Layer from it directly.
from device_canvas import DeviceCanvas


# --- make_api (the cart NAMESPACE builder) now lives in device_api.py so the
# P4 target (#58) stages the identical kid API instead of duplicating it;
# imported here so moy_runtime.make_api keeps resolving for run_desktop /
# run_perf_bench and any external references.
from device_api import make_api  # noqa: E402


# --- Audio backend (#16) now lives in device_audio.py (DeviceAudio + make_audio +
# the MOY_AUDIO_CORE1 / I2S_* / AUDIO_* consts), imported at the top of this module.
# run_desktop wires ws.make_audio = make_audio.


# --- WiFi service (#38) now lives in device_wifi.py (DeviceWifi + make_wifi +
# autoconnect_wifi), imported at the top of this module. run_desktop still calls
# make_wifi()/autoconnect_wifi() to build + boot-connect the injected wifi service.

# --- pointer input drivers now live in device_input.py (TrackBall + the GT911
# Touch + the TOUCH_* calibration constants), imported at the top of this module.
# run_desktop constructs them and (in poller mode) pokes touch._source.

# --- #69 the input-poller thread knob ----------------------------------------
#
# THE keyboard/touch stall fix: moybyte.input.InputPoller (see its docstring for
# the full story) owns every I2C0 transaction on a dedicated Python thread; the
# frame loop only consumes staged state, so a C3 clock-stretch stall (40-60ms,
# I2CSTAT-sized) blocks the poller thread instead of a frame. Needs the build's
# I2C GIL-release patch (esp32_i2c_gil_release.patch) to actually isolate the
# stall -- without it the stall holds the GIL and freezes the loop from any
# thread (the poller is then harmless, just useless). Set False -- or lose
# _thread, or let the thread die -- and run_desktop stays on / falls back to
# the synchronous keyboard.poll()/touch path, exactly the pre-poller behavior
# (revert with NO rebuild, same pattern as MOY_AUDIO_CORE1).
MOY_INPUT_POLLER = True

# #183: print a phase bracket around every SD session (see _with_sd_synced). ON while
# the "editing code hard-hangs the T-Deck, nothing on serial" bug is open -- this board
# has no REPL to interrogate, so the trace IS the diagnostic, and it only fires on
# commits (never per frame). Turn OFF when #183 closes.
SD_TRACE = True


# _ticks_ms/_ticks_diff/_ticks_us + _diag_note/_diag_log now live in the leaf
# device_util.py (imported at the top of this module), so extracted device
# clusters can share them without a moy_runtime import cycle.

# --- serial diagnostics now live in device_diag.py (_diag_flush / _diag_perf_sample
# / _diag_hitch / _diag_drawbrk / _diag_draw2 / _diag_chromebrk / _diag_pump /
# _diag_i2cstat / _diag_calib / _diag_gc), imported at the top of this module.
# run_desktop calls them between frames when perf capture is on.


def _load_carts(session=None, progress=None):
    """Load cartridges from SD (seeding the built-ins on first boot). Returns
    (carts, carts_root); carts_root is None (management disabled) on fallback to
    the embedded carts if the SD card is missing/unreadable.

    `session` is the SD lifecycle wrapper to mount under. Default is the
    pre-display machine.SDCard path (used by the boot prefetch); pass
    moybyte_sd.with_sd_live for the post-display native path.

    `progress(done, total, title)` feeds the boot splash's bar. Seeding is the
    long pole of a first boot -- 17.5s of the P4's 25s, and this board writes
    the same cartridges to SD rather than internal flash."""
    try:
        import moybyte_sd
        import moy_carts

        if session is None:
            session = moybyte_sd.with_sd

        def _seed_and_scan():
            moy_carts.ensure_dirs()
            moy_carts.seed_builtins(CARTS, progress=progress)
            return moy_carts.scan()

        # Mount only for the seed+scan, then unmount: the render loop must own
        # the shared SPI bus with no SDCard device attached, or flushes hang.
        carts = session(_seed_and_scan)
        if carts:
            print("Moybyte loaded %d carts from SD" % len(carts))
            return carts, moy_carts.CARTS_DIR
    except Exception as exc:  # noqa: BLE001
        print("Moybyte SD carts unavailable:", exc)
    print("Moybyte using built-in carts")
    return [dict(c) for c in CARTS], None


# --- the device WEB VIEW (the streaming browser mirror) was DELETED in the
# 2026-08 streaming sunset (docs/moycore_plan_2026-08.md 3.2): device_webview.py,
# moy_webserver's frame push and the TeeCanvas lane are gone. moy_webserver.py
# survives as the bare socket/HTTP/WS transport core for the 3.4 sync RPC.


# Kid-side bench source (#63 run_perf_bench): sakura's exact _update/_draw shape,
# compiled AT RUNTIME with exec() like a real SD cart -- so the kid side runs RAM
# bytecode against the frozen engine, the same split as production. 120 petals of
# float physics + the naive per-petal spr() loop.
_BENCH_KID_CODE = """
import math
SIN = [math.sin(i / 256.0 * 6.2831853) for i in range(256)]
petals = []
t = 0.0

def _sin(turn):
    return SIN[int(turn * 256.0) & 255]

def _init():
    global petals
    petals = []
    for i in range(120):
        shade = i % 3
        petals.append([(i * 37) % 320 * 1.0, (i * 53) % 240 * 1.0,
                       30.0 * (1.0 - 0.18 * shade), 0.3 + i * 0.01,
                       4.0 + (i % 9), shade])

def _update(dt):
    global t
    t += dt
    breeze = 18.0
    cx = -999.0
    cy = -999.0
    R = 52.0
    for p in petals:
        p[3] += dt * (0.32 + 0.06 * p[5])
        sway = _sin(p[3]) * p[4]
        p[0] += (breeze * (1.0 - 0.15 * p[5]) + sway) * dt
        p[1] += p[2] * dt
        dx = p[0] - cx
        dy = p[1] - cy
        if -R < dx < R and -R < dy < R:
            far = dx if dx >= 0 else -dx
            ady = dy if dy >= 0 else -dy
            if ady > far:
                far = ady
            k = (R - far) / R * 130.0
            inv = 1.0 / (far + 4.0)
            p[0] += dx * inv * k * dt
            p[1] += dy * inv * k * dt
        if p[1] > H + 4.0:
            p[1] = 0.0
        elif p[0] < -8.0:
            p[0] += W + 16.0
        elif p[0] > W + 8.0:
            p[0] -= W + 16.0

def _draw():
    draw_layer(lay, 0, 0)
    for p in petals:
        spr(p[5], int(p[0]), int(p[1]), 0)
"""


def run_perf_bench(handler):
    """Self-terminating perf bench (#63): boots the REAL device pipeline (compositor,
    DeviceCanvas, frozen engine, runtime-exec'd kid code, real flush DMA) and measures
    the sakura-shaped frame under every combination that matters:
      - Python spr path vs the native spr_gate
      - cold heap vs warm/fragmented heap (the frame-spill pathology trigger)
      - flush on vs off (PSRAM DMA cache-contention probe)
    plus the CALIB cost-model line on the frozen interpreter. Prints BENCH lines and
    RETURNS (no takeover loop): the board drops back to the REPL, so a headless bench
    board (XIAO, no buttons) stays reflashable. Never enabled in user images -- boots
    only via the _moy_bench build stamp (MOYBYTE_BENCH=1)."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:
            print("Moybyte bench: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState
        from editors import SpriteSheet
    except Exception as exc:
        print("Moybyte bench unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("Moybyte bench: no compositor")
        return
    import gc

    canvas = DeviceCanvas(comp)
    sheet = SpriteSheet()
    px = sheet.pix
    for i in range(len(px)):
        px[i] = (i * 7) % 15 + 1       # non-transparent noise tiles
    inp = InputState()

    class _Diag:
        def log(self, tag, msg):
            print("BENCH %s %s" % (tag, msg))

    diag = _Diag()

    def build_ns(use_gate):
        # Fresh kid namespace, exec'd at runtime like a real cart. use_gate=False
        # shadows make_spr_gate so make_api keeps the Python spr closure.
        if not use_gate:
            canvas.make_spr_gate = lambda s, f: None       # instance shadow
        elif "make_spr_gate" in canvas.__dict__:
            del canvas.make_spr_gate                       # restore the C gate
        ns = make_api(canvas, inp, {}, sheet=sheet)
        exec(_BENCH_KID_CODE, ns)                           # noqa: S102 -- bench-only
        ns["lay"] = ns["make_layer"](canvas.w, canvas.h)
        ns["lay"].cls(3)
        ns["_init"]()
        return ns

    def run_cfg(label, use_gate, frames=60, flush=True):
        ns = build_ns(use_gate)
        upd = ns["_update"]
        drw = ns["_draw"]
        gc.collect()
        tu = 0
        td = 0
        tf = 0
        for i in range(frames):
            canvas.sync_back()
            canvas.batch_reset()
            t0 = _ticks_us()
            upd(0.033)
            t1 = _ticks_us()
            drw()
            canvas.flush_batch()
            t2 = _ticks_us()
            if flush:
                comp.flush()
            t3 = _ticks_us()
            tu += _ticks_diff(t1, t0)
            td += _ticks_diff(t2, t1)
            tf += _ticks_diff(t3, t2)
        print("BENCH %s: update=%.2fms draw=%.2fms flush=%.2fms (batch=%d/%d)"
              % (label, tu / frames / 1000.0, td / frames / 1000.0,
                 tf / frames / 1000.0, canvas._batch_flushes, canvas._batch_sprites))

    print("BENCH start (frozen engine, runtime-exec kid code)")
    _diag_calib(diag)                       # cost model, cold-ish heap
    run_cfg("cold pyspr ", False)
    run_cfg("cold gate  ", True)
    # warm/fragmented heap: the production trigger (live buffers + churn holes)
    ballast = [bytearray(150 * 1024)]
    for i in range(6000):
        ballast.append([i, i, i, i, i, i, i, i])
    frag = [(i, i, i, i) for i in range(20000)]
    keep = []
    for i in range(0, 20000, 2):
        keep.append(frag[i])
    frag = keep
    gc.collect()
    print("BENCH warm heap live=%dk" % (gc.mem_alloc() >> 10))
    _CALIB_DONE[0] = False
    _diag_calib(diag)                       # cost model again, warm heap
    run_cfg("warm pyspr ", False)
    run_cfg("warm gate  ", True)
    run_cfg("warm gate noflush", True, flush=False)
    print("BENCH done -- returning to REPL")


def run_desktop(handler, prefetched=None, fps_cap=60):
    """Boot the workstation on the device: launcher + carts + keyboard.

    `prefetched` is an optional pre-read (carts, carts_root) tuple; the shipped
    boot passes None (#56: nothing touches SD before the panel is up) and carts
    load below through the bus-safe moy_sd/with_sd_live attach -- machine.SDCard
    against the live panel bus is what hard-hangs the board, not SD access."""
    if handler is not None:
        try:
            handler.deinit()  # stop the LVGL TaskHandler; the compositor owns the bus
        except Exception as exc:
            print("Moybyte desktop: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState, TDeckKeyboard, InputPoller
    except Exception as exc:
        print("Moybyte desktop unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("Moybyte desktop: no compositor")
        return
    # The compositor flushes the dedicated _frame buffer in strip_h-row bands: each a
    # distinct, stable slice (the async esp_lcd DMA can't race a reused buffer -> no
    # offset/duplication) and small enough that the per-band DMA bounce fits the S3's
    # fragmented internal heap (a single 320x240 tx_color NO_MEMs). strip_h=24 = band.

    canvas = DeviceCanvas(comp)

    # -- boot splash (#45/#58) --------------------------------------------
    # The panel is dark until the first frame ships (the backlight gate in the
    # loop below), which is right -- it keeps the ST7789's power-on GRAM noise
    # off the glass. The cost is that a slow boot looks like a dead board, and
    # a FIRST boot is slow: every built-in cartridge is written out before
    # anything composes. Measured on the P4, whose store is internal flash:
    # 17.5s of a 25s boot. This board seeds to SD.
    #
    # So show the SHIPPED boot logo early (console.draw_splash -- the same
    # picture arm_splash holds, so the machine never appears to start twice)
    # with the seeding bar under it, and say the same on serial.
    _splash = {"lit": False}

    def _boot_note(msg, frac=None):
        if _splash.get("done"):
            return                       # the desktop owns the glass now
        if frac is None:
            print("Moybyte boot:", msg)  # a stage; the bar stays quiet
        try:
            # The canvas caches its framebuffer pointer, and flush() may rotate
            # the back buffer -- a cheap no-op in single-buffer mode, and
            # load-bearing otherwise (on the P4 its absence strobed two frames
            # in every three).
            canvas.sync_back()
            draw_splash(canvas, frac=frac, status=msg)
            comp.flush()
            if not _splash["lit"]:
                import tdeck_display
                tdeck_display.set_backlight(True)
                _splash["lit"] = True
        except Exception as exc:  # noqa: BLE001 -- a splash must never fail a boot
            print("Moybyte splash unavailable:", exc)

    def _seed_progress(done, total, title):
        if done % 8 == 0:                # the wire, without one line per cart
            print("Moybyte boot: loading cartridges %d/%d" % (done + 1, total))
        _boot_note("loading cartridges  %d/%d" % (done + 1, total),
                   frac=float(done) / total if total else 1.0)

    _boot_note("starting")

    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    ball = TrackBall()
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    pointer = Pointer(canvas.w, canvas.h)
    inp.pointer = pointer         # touch-driven carts read it via the api touch()
    # #69 input-poller thread: move EVERY I2C0 transaction (kbd + GT911 + mode
    # switches) off the frame loop so a C3 clock-stretch stall can't freeze a
    # frame (needs the build's GIL-release patch to bite; see the class comment).
    # Any failure leaves the synchronous path untouched.
    poller = None
    if MOY_INPUT_POLLER:
        try:
            _p = InputPoller(keyboard, touch)
            if _p.start():
                poller = _p
                keyboard._poller_owned = True
                touch._source = poller.consume_touch
                _diag_note("input", "poller thread running (#69, %dms cadence)"
                           % poller.period)
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            _diag_note("input", "poller setup failed: %s" % (exc,))
            poller = None
    import moybyte_sd
    # Carts are read from SD before display init; only fall back to a post-display
    # mount (now safe via the native moy_sd path) if the shell didn't prefetch.
    sram_census("rd-entry")         # compositor/canvas/input are up by here
    _boot_note("loading cartridges")
    carts, carts_root = (prefetched if prefetched is not None
                         else _load_carts(moybyte_sd.with_sd_live,
                                          progress=_seed_progress))
    import moy_carts
    sram_census("carts")
    _boot_note("building the desktop")
    ws = Workstation(comp, canvas, inp, carts)
    sram_census("console")
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster (celeste's 128x128) plays on its own off-screen _LayerComp-backed
    # DeviceCanvas; bind_run_canvas promotes the boot canvas (the glass) to
    # SYSTEM canvas for the run and wm.composite_game upscales through
    # DeviceCanvas.blit_game (blit565_scale). No native kernel -> None, so the
    # Player refuses the cart cleanly instead of crawling per-pixel.
    def _mk_game_canvas(w, h):
        if getattr(canvas, "_gfx", None) is None:
            return None
        from device_canvas import _LayerComp
        return DeviceCanvas(_LayerComp(int(w), int(h), canvas._gfx))
    ws.make_game_canvas = _mk_game_canvas
    # cover_diet stays OFF (owner call 2026-08-03): thumbs remain warm across a
    # game run -- the trade of a longer GC pause (~243ms vs ~150ms at the dieted
    # live set, roughly one mid-play collect per 10s) was judged worse than any
    # cover rebuild. The real fix is GC-INVISIBLE warm storage: moy_alloc-backed
    # payloads the collector never scans (the moy_buf issue). The release
    # mechanism (Workstation._release_cover_caches, keep-newest-6) stays built
    # for that work to reuse.
    # #67 spike: say ONCE whether the auto-native cart loader engaged (the emitter
    # probe in player.py), so a serial capture can attribute logic-ms deltas.
    try:
        import player as _player_mod
        _diag_note("carts", "auto-native %s"
                   % ("ON" if _player_mod.NATIVE_CARTS else "OFF"))
    except Exception:  # noqa: BLE001 -- diagnostic only
        pass
    # #67 Phase 1: the Lua cart runtime -- wired only when the moy_lua native
    # module is in this build; without it a "runtime": "lua" cart opens the
    # Player's runtime-missing panel (the Phase 2 graceful floor).
    # ONE Lua runtime. moycore runs the cart's whole frame inside libmoy --
    # `_update` and `_draw` back to back in C, one upcall per frame instead of
    # hundreds -- and moybyte's superset verbs ride it as registered
    # trampolines, with the object-valued ones (layers, images) on the shared
    # int-handle glue. There is no second engine and no chooser: a build
    # without the module opens the Player's runtime-missing panel, which is the
    # same graceful floor a build without a Lua VM always had.
    #
    # The S3's presentation is unchanged by this: moycore renders the cart
    # canvas and the #190 flush-bounce fold synthesizes its bands from that
    # buffer exactly as before, because the buffer is the same one.
    lua_runtime = None
    try:
        from moycore_glue import make_moycore_runtime
        lua_runtime = make_moycore_runtime(ws)
    except ImportError:
        pass
    # Say whether moycore is actually in this image. The S3's USB-CDC RX is
    # dead under the desktop, so serial is READ-only here -- a status that is
    # not printed cannot be asked for.
    _diag_note("carts", "lua runtime %s"
               % ("ON (moycore)" if lua_runtime is not None else "ABSENT"))

    # Set by the SD-session trace below, cleared by the first frame that flushes after
    # one -- the "the panel survived the SD session" half of the #183 bracket.
    _sd_traced = [False]

    # SD vs panel-DMA mutual exclusion (#40 double-buffer): SD shares the panel's SPI
    # host, so an SD op can NOT overlap an in-flight panel DMA. Wrap with_sd_live so it
    # drains any pending panel DMA (comp.sync()) BEFORE touching the SD card -- the
    # desktop loop is single-threaded so SD ops run between frames, but with double-
    # buffer a frame's flush DMA may still be in flight when the op starts. sync() is a
    # no-op in single-buffer mode (the flush already blocked), so this is safe either
    # way and the wrapper is transparent to the shared console code.
    def _with_sd_synced(fn):
        if not SD_TRACE:
            comp.sync()
            return moybyte_sd.with_sd_live(fn)
        # #183 BRACKETING TRACE. An editor commit can hard-hang this board with
        # NOTHING on serial, and its USB-CDC RX is dead -- there is no REPL to ask
        # what it was doing. TX streams fine, so name each phase of the SD session:
        # whichever line is LAST before the silence identifies the op that wedged.
        #   "> sync"  hung here -> the pre-op DMA drain (comp.sync -> _drain_dma; the
        #             non-async branch ends in lcd_bus tx_color(last=True), a C
        #             busy-wait with no bound of its own)
        #   "> op"    hung here -> the SD write itself (moy_sd / _write_atomic)
        #   "< op"    hung after -> the NEXT panel flush, i.e. the documented
        #             shared-bus corruption ("the write lands on SD, then resume
        #             freezes") -- the predicted one; "= panel ok" never follows
        # Costs nothing when quiet: SD sessions happen on COMMITS, not per frame.
        # Flip SD_TRACE off once #183 is closed.
        print("SD > sync")
        _t = _ticks_ms()
        comp.sync()
        print("SD > op (sync %dms)" % _ticks_diff(_ticks_ms(), _t))
        _t = _ticks_ms()
        try:
            return moybyte_sd.with_sd_live(fn)
        finally:
            print("SD < op %dms" % _ticks_diff(_ticks_ms(), _t))
            _sd_traced[0] = True

    def _before_slim(ws):
        # Writes are enabled on-device via moy_sd: it attaches the SD card to the SPI
        # host esp_lcd already initialized (instead of machine.SDCard re-initializing
        # it, which hangs the live bus). with_sd_live mounts the card once and keeps
        # it resident -- tearing it down per op silent-hangs the next panel flush.
        # Set BEFORE slim_carts so the store can reload what the diet drops.
        ws._with_sd = _with_sd_synced
        # OTA firmware update (#53): the shared console's Settings -> UPDATE FW row
        # flashes a new app image from /sd/update into the inactive OTA slot
        # (esp32.Partition) and reboots. SD shares the panel SPI host, so the updater
        # reads through the SAME _with_sd_synced wrapper as cart saves. Available only
        # on an --ota build; on a legacy single-factory image available() is False.
        try:
            import moy_ota
            ws.updater = moy_ota.OtaUpdater(_with_sd_synced)
        except Exception as exc:
            print("Moybyte: OTA updater unavailable:", exc)

    # The shared service wiring (console.wire_workstation_core -- one canonical
    # order for host + both boards): api/audio/lua + store/root/can_manage + the
    # WiFi (#38) SYSTEM service (network.WLAN STA, lazy -- injected into a cart's
    # namespace only when its manifest grants "network"; the WLAN stack
    # NEEDS ON-DEVICE VERIFICATION) + the #66 slim_carts diet + pointer/keyboard
    # + the boot loads.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          make_wifi(moy_carts, carts_root),
                          make_audio=make_audio,   # device I2S audio backend (#16)
                          lua_runtime=lua_runtime, before_slim=_before_slim,
                          pointer=pointer, inp=inp, keyboard=keyboard)
    # OTA online update (#53, Phase 3): hand the updater the wifi service so Settings ->
    # UPDATE ONLINE can fetch a manifest + stream a new image to SD. go_online reuses the
    # saved-credential autoconnect (autoconnect_wifi) so the kid needn't re-enter wifi to
    # update -- it only connects to a network they already joined via the WiFi cart.
    if getattr(ws, "updater", None) is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:
            print("Moybyte: OTA wifi wiring failed:", exc)
    # System menu (#52): the ≡ dropdown's "Reboot" row. On device a real reboot is
    # machine.reset(); the shared console calls this injected hook (None on host -> a
    # safe go_home stub). Additive -- it never touches the render/flush path.
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:
        print("Moybyte: reboot hook unavailable:", exc)
    # WEB CONSOLE (moycore plan 3.4 pull half): serve the wasm console from this
    # board. Constructed, NOT started -- injecting it only makes the Settings row
    # appear, and the row is what brings the radio up.
    #
    # This board differs from the P4 in two ways that are both arguments, not
    # code: the bundle lives on the SD card (/sd/web), and every read of it must
    # go through the _with_sd_synced gate -- SD shares the panel's SPI host, so
    # an op may not overlap an in-flight flush DMA (see the hard constraints).
    # Streaming a ~1MB wasm therefore reads between frames, the same way the OTA
    # download to SD already does.
    #
    # THE RISK THAT IS THIS BOARD'S ALONE, and the reason this is off until asked
    # for: the WLAN stack reserves internal RAM the LCD DMA flush needs, which is
    # why boot deliberately does not autoconnect (see the note below -- OSError
    # 257 / ESP_ERR_NO_MEM froze the desktop). Turning this row on brings the
    # radio up and takes that risk knowingly, exactly as UPDATE ONLINE does. The
    # P4 has no such coexistence problem (separate C6 radio over SDIO), so this
    # hazard must not be assumed to travel with the feature.
    try:
        from moy_webhost import make_webhost, TDECK_WEB_DIR

        ws.webhost = make_webhost(ws, carts_root, TDECK_WEB_DIR,
                                  autoconnect=autoconnect_wifi,
                                  with_sd=_with_sd_synced)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte: web console unavailable:", exc)
    # WiFi is deliberately NOT brought up at boot: the WLAN stack reserves internal RAM
    # the LCD DMA flush needs, so autoconnecting here starved the panel flush (OSError
    # 257 / ESP_ERR_NO_MEM) and froze the desktop. DeviceWifi is lazy now -- the radio
    # only spins up when the WiFi-manager cart scans/connects. WiFi<->display coexistence
    # on this RAM budget is an open #38 item. (autoconnect_wifi left defined, not called.)
    # (The #28 system.json / icon theme / achievements boot loads ran inside
    # wire_workstation_core above -- same store + with_sd_live path.)
    # Offline diagnostics (moybyte_diag): RAM ring now, flushed to SD every ~5s and
    # on a crash (read back via moybyte_diag.dump_previous_to_serial from the
    # REPL, or by pulling the card). perf_capture makes ws.frame() record
    # the flush/draw split each frame WITHOUT drawing the on-screen HUD, so the perf
    # sampler below can read steady numbers. Guarded import: no diag -> plain loop.
    try:
        import moybyte_diag as diag
    except Exception:
        diag = None
    if diag is not None:
        try:
            # 2026-08-03: capture follows Settings -> PERF DIAG, no longer
            # unconditionally True -- the deep per-frame meters it arms
            # (per-layer walk timing, per-op canvas timers, the EMA tail)
            # measured ~1-1.5ms of EVERY kid frame. The 3s diag tick below
            # re-syncs it live when the toggle flips.
            ws.perf_capture = bool(getattr(ws, "diag_live", False))
        except Exception:
            pass
    _diag_log("boot", "desktop running kb=%d ball=%d touch=%d"
              % (1 if keyboard.available else 0, 1 if ball.available else 0,
                 1 if touch.available else 0), diag)
    sram_census("desktop-up")

    # #66/#67 SRAM diet: everything that needs boot-time internal RAM has taken
    # it by here (flush bounce, poller; audio reserves at first cart start), so
    # the Lua allocator's headroom floor drops 48->24KB and the SRAM-first
    # grant grows by the difference. The accepted edge: a FIRST wifi start
    # mid-cart can fail for RAM -- games are fullscreen so the wifi/OTA flows
    # normally run with no Lua cart loaded (a closed cart frees its whole heap).
    # BOTH runtimes: moycore has its own allocator with its own floor, and for
    # a while only moy_lua's was lowered here -- so a cart on moycore kept the
    # 48KB floor, which on this board's 269KB internal heap is the ~97%-PSRAM
    # case the whole SRAM-first policy exists to avoid. Nothing said so; the
    # cart was just slower.
    for _mod in ("moy_lua", "moycore"):
        try:
            _m = __import__(_mod)
            _fl = getattr(_m, "set_sram_floor", None)
            if _fl is not None:
                _diag_log("boot", "%s sram floor=%dKB" % (_mod, _fl(24)), diag)
        except Exception:
            pass

    # #66/#67 indexed-SRAM-canvas pricing: one MEMBENCH line at boot when PERF
    # DIAG is persisted on (the T-Deck has no serial RX to ask for it later; the
    # P4 can also run `py __import__('moy_gfx').membench()` any time). Runs
    # BEFORE any cart so internal SRAM is at its largest -- ~50ms, diag-gated.
    if ws.perf_capture:
        try:
            import moy_gfx as _mg
            _mb = getattr(_mg, "membench", None)
            if _mb is not None:
                _r = _mb()
                _diag_log("MEMBENCH",
                          "us/frame fill16 s=%d p=%d fill8 s=%d p=%d "
                          "blit16 s=%d p=%d blit8 s=%d p=%d resolve=%d bounce=%d"
                          % tuple(_r), diag)
        except Exception as _e:
            _diag_log("MEMBENCH", "failed: %r" % _e, diag)

    # Say what became of the last update, before anything can overwrite the evidence
    # (#53). Reaching here means the panel, SD, keyboard and desktop all came up --
    # but NOT that a single frame reached the glass, and a board that boots to a
    # black screen has shipped here before (#56). So the rollback CONFIRM is not
    # made here; it is made from the frame loop below, once frames are really going
    # out (ws.updater.confirm_when_healthy).
    _ota = getattr(ws, "updater", None)          # cleared once the confirm has fired
    if _ota is not None:
        try:
            _verdict = _ota.boot_check()
            if _verdict:
                _diag_log("ota", "last update %s (%s)" % _verdict, diag)
                ws.announce_update()      # and say so on the desktop, not just here
        except Exception as exc:
            _diag_log("ota", "boot_check failed: %s" % exc, diag)

    import gc
    gc.collect()                                # defrag after the heavy boot so the LCD
                                                # DMA flush has the internal RAM it needs
    try:                                        # one-shot heap snapshot (diagnostic):
        import esp32                            # each region = (total, free, max_contiguous, min_free);
        # the small regions are internal SRAM, the huge one is PSRAM. The LCD DMA
        # bounce needs a contiguous INTERNAL block, so watch the small regions' max.
        _diag_log("mem", "gc_free=%d heap=%s"
                  % (gc.mem_free(), esp32.idf_heap_info(esp32.HEAP_DATA)), diag)
    except Exception as _e:                     # noqa: BLE001 -- diagnostic only
        _diag_log("mem", "gc_free=%d (esp32 n/a: %s)" % (gc.mem_free(), _e), diag)
    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    # #45: the panel stays dark until the first frame ships -- unless the boot
    # splash already composed one, in which case it is lit and stays lit.
    _backlight_on = _splash["lit"]
    _first_at = _ticks_ms()
    # Diag timers: flush the RAM ring to SD every ~5s (between frames, never during a
    # panel flush -- with_sd_live mounts on the native single-bus path), and sample
    # the perf HUD numbers into a PERF line every ~3s while a cart runs.
    _diag_flush_at = _ticks_ms() + 5000
    _diag_perf_at = _ticks_ms() + 3000
    _diag_prev_cart_err = None    # last ws.cart_error we logged, so we log each crash once
    _diag_cart_prev = False       # #68: cart-running edge -> flush the ring on cart EXIT
    # LOOP-line accumulator: [n, frame, kbd, inp, sb, ws, web, diag, sd, sleep] ms
    # summed per frame, averaged + zeroed every diag tick (_diag_loop). HITCH only
    # fires on SPIKES, so a steady per-frame cost that never crosses HITCH_MS was
    # invisible until this line existed (2026-07-29 fps hunt).
    _loop_acc = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    # Pacing debt (#77, 2026-08-10): ms the loop is BEHIND its cadence. The old
    # per-frame clamp couldn't pace a frameskip pair whose FULL frame overruns
    # the 33ms budget (celeste: 50ms full + 33ms padded skip = 83ms pairs, the
    # game 20% slow and 12fps -- worse on both axes). An over-budget frame now
    # borrows from the next frames' sleeps so the PAIR lands on cadence
    # (50 + 16 = 66ms = two 30fps slots). Capped at one pair so a real hitch
    # (a 200ms GC) doesn't eat the sleeps for a second afterwards.
    _pace_debt = 0
    _boot_note("drawing the first frame")
    # NOT a second logo: the boot splash has held this exact picture for the
    # whole boot, so arming it again would replay the splash and delay the
    # launcher. Armed only if the splash never came up (its draw failed) --
    # the one case where the logo would otherwise go unseen.
    if not _splash["lit"]:
        ws.arm_splash()           # boot logo: the moybyte mascot before the launcher
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        # #69: with the poller thread live, the frame loop only APPLIES staged
        # input (no I2C -> no stall can land here). If the thread ever dies,
        # detach and fall back to the synchronous poll -- input never goes dark.
        if poller is not None and not poller.alive:
            _diag_note("input", "poller thread died -> synchronous fallback")
            keyboard._poller_owned = False
            touch._source = None
            poller = None
        try:
            if poller is not None:
                poller.consume()
            else:
                keyboard.poll()
        except Exception:
            pass
        # HITCH v2 (#66): the first hardware pass showed ~188ms hitches every
        # ~1.3s with every MEASURED phase normal -- the pause lives in the input
        # polls (I2C keyboard/touch), the one loop stage that wasn't timed. Time
        # kbd / (ball+touch) / ws.frame separately so a HITCH line names it.
        _t_kbd = _ticks_diff(_ticks_ms(), now)
        _t0 = _ticks_ms()
        inp.begin_frame()                       # keyboard edges (still a fallback)
        counts, click = ball.poll()             # trackball
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        if ws.screen == "menu" and ws.menu_view == "code":
            ws.nav(nx, ny)                      # in the code editor the trackball moves the caret
        else:
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)            # elsewhere it moves the cursor
        tp = touch.poll()                       # touch -> absolute position + tap
        pointer.down = tp is not None           # held finger drives drag-scroll
        # Touch.poll holds a held finger's last point across the frames the GT911
        # has no fresh sample for (#74's 20-45ms finger-down stalls), so `down`
        # above is a real LEVEL and a drag survives them; `fresh` marks the
        # repeats so kinetic scrolling (#113) doesn't measure finger speed
        # against a sample the hardware never took.
        pointer.fresh = getattr(touch, "fresh", True)
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:                           # press edge = tap = click
                click = True
        pointer.click = click
        pointer.tick(now)                       # auto-hide the idle trackball cursor
        _t_inp = _ticks_diff(_ticks_ms(), _t0)  # trackball + touch + pointer (HITCH v2)
        # DMA double-buffer (#40, DEFAULT ON): point the canvas at the compositor's
        # current BACK buffer before drawing. The previous flush() swapped it, so this
        # frame's cls/rect/spr/map must target the new back, never the buffer that's
        # mid-DMA. No-op (buffer unchanged) in single-buffer mode or on a skipped frame.
        _frames_before = getattr(ws, "_frames_drawn", 0)
        _t0 = _ticks_ms()
        canvas.sync_back()                      # buffer repoint + GDMA layer kick
        _t_sb = _ticks_diff(_ticks_ms(), _t0)   # (was an unmeasured stage; HITCH v3)
        _t0 = _ticks_ms()
        _t_hi = 0                               # (defined before the try: a crash
        _t_hp = 0                               #  mid-frame still logs a LOOP line)
        try:
            # Split so the LOOP line can tell ROUTING from DRAWING (2026-07-29):
            # _t_ws lumped all three, and the fps hunt found ~4.8ms/frame inside
            # it that DRAWBRK + flush don't account for -- identical on two very
            # different carts, which is the shape of a fixed per-frame routing
            # cost, not cart draw work.
            ws.handle_input()                   # keyboard W/A/S/D etc.
            _t_hi = _ticks_diff(_ticks_ms(), _t0)
            ws.handle_pointer()                 # cursor hover + click
            _t_hp = _ticks_diff(_ticks_ms(), _t0) - _t_hi
            ws.frame(dt)                        # draw + composite + flush
        except Exception as exc:                # never let one bad frame brick the device:
            # Capture the crash in diag AND print it live: a crash we can't see live
            # (the takeover loop has starved USB) is the whole reason diag exists, so
            # flush it to SD immediately so next boot's dump has it.
            _diag_log("frame error", exc, diag)
            print("Moybyte frame error:", exc)  # print the traceback's reason to serial
            _diag_flush(diag, ws)
            gc.collect()                        # a NO_MEM flush may recover after a collect
        _t_ws = _ticks_diff(_ticks_ms(), _t0)   # handle_input/pointer + ws.frame (HITCH v2)
        # #183: close the SD bracket. Reaching here with a DRAWN frame means the first
        # panel flush after the SD session completed -- so the bus survived it and the
        # hang (if any) is elsewhere. Silence after "SD < op" is the shared-bus one.
        if _sd_traced[0] and getattr(ws, "_frames_drawn", 0) != _frames_before:
            _sd_traced[0] = False
            print("SD = panel ok")
        # DMA double-buffer (#40): finish the displayed frame when the UI goes IDLE.
        # flush() holds back the final band (the busy-wait completion point) for the
        # NEXT flush's drain so render overlaps the DMA -- but the redraw-on-change gate
        # (#44) may skip flush() for many idle frames, which would leave that final band
        # un-issued and the panel showing an incomplete frame. So when THIS frame did
        # not draw (no flush happened), drain any pending band: the panel is then fully
        # painted and stays idle (pending -> None, so this fires once, not every idle
        # frame). No-op in single-buffer mode (sync() is a no-op there).
        if getattr(ws, "_frames_drawn", 0) == _frames_before:
            try:
                comp.sync()
            except Exception:
                pass
        # A cart that raises inside its own _update/_draw is caught INSIDE ws.frame()
        # (so it never reaches the except above) -- it sets ws.cart_error. Mirror any
        # NEW cart_error into diag + flush it, so an in-cart crash is captured offline
        # for the next-boot dump (the takeover loop has starved live serial by now).
        if diag is not None:
            _ce = getattr(ws, "cart_error", None)
            if _ce is not None and _ce != _diag_prev_cart_err:
                _diag_prev_cart_err = _ce
                _diag_log("cart error", _ce, diag)
                _diag_flush(diag, ws)
            elif _ce is None:
                _diag_prev_cart_err = None
        # Boot "CRT" flash fix (#45): the backlight booted OFF (tdeck_board/tdeck_display)
        # so the ST7789 power-on GRAM noise is never lit. Turn it on the instant the
        # first real frame has been composed+flushed -- ws._frames_drawn ticks past 0
        # only inside frame() after comp.flush() -- so the user's first sight is the
        # desktop, not garbage. One-shot; guarded so a no-op redraw frame won't re-light.
        if not _backlight_on and getattr(ws, "_frames_drawn", 0) > 0:
            try:
                import tdeck_display
                tdeck_display.set_backlight(True)
            except Exception as _bl:            # display-less host / bring-up: ignore
                print("Moybyte backlight on failed:", _bl)
            _backlight_on = True
        # How long the desktop took to reach the glass -- reported once, then
        # the splash hands over and stops painting.
        if not _splash.get("done") and getattr(ws, "_frames_drawn", 0) > 0:
            _splash["done"] = True
            print("Moybyte first frame in %dms"
                  % _ticks_diff(_ticks_ms(), _first_at))
        # The OTA rollback confirm (#53), now that frames are really going out.
        # Cheap (an int compare) and self-disarming after it fires once.
        if _ota is not None:
            try:
                if _ota.confirm_when_healthy(getattr(ws, "_frames_drawn", 0)):
                    _diag_log("ota", "marked app valid (slot %s)" % _ota.slot(), diag)
                if _ota.confirmed:
                    _ota = None       # fired (or a non-OTA build): stop asking
            except Exception as exc:  # never break a frame over this
                _diag_log("ota", "confirm failed: %s" % exc, diag)
                _ota = None
        # Diag perf sample (~3s): a structured "PERF cart=<name> fps=<n> flush=<ms>
        # draw=<ms>" line while a cart runs -- the payload that makes "play -> reboot
        # -> paste the serial" yield per-cart frame timings offline. No SD touch here
        # (just the RAM ring); the 5s flush below is what writes it out.
        _tnow = _ticks_ms()
        _t_diag = 0
        # #68 "kid mode" gate: Settings -> PERF DIAG (ws.diag_live, persisted,
        # default OFF). OFF skips the two diag costs a player can FEEL -- the 30s
        # forced GC sample (~130-230ms) and the periodic diag->SD write (~115ms) --
        # and hushes the live echo. The RAM ring still collects every line
        # (us-cheap) and still reaches SD on crash / cart exit below, so
        # "play -> crash -> read diag.log" works in kid mode too.
        _live = bool(getattr(ws, "diag_live", False))
        if diag is not None and _ticks_diff(_tnow, _diag_perf_at) >= 0:
            _diag_perf_at = _tnow + 3000
            if ws.perf_capture != _live:
                ws.perf_capture = _live   # capture follows the PERF DIAG toggle
                if _live:
                    # #66/#67: the MEMBENCH pricing line also fires on the
                    # OFF->ON edge, so a measurement session needs no reboot
                    # to get it (it otherwise prints only at a diag-on boot).
                    # ~50ms once, and only in a diag-armed session.
                    try:
                        import moy_gfx as _mg
                        _mb = getattr(_mg, "membench", None)
                        if _mb is not None:
                            _diag_log("MEMBENCH",
                                      "us/frame fill16 s=%d p=%d fill8 s=%d "
                                      "p=%d blit16 s=%d p=%d blit8 s=%d p=%d "
                                      "resolve=%d bounce=%d" % tuple(_mb()),
                                      diag)
                    except Exception as _e:
                        _diag_log("MEMBENCH", "failed: %r" % _e, diag)
            try:
                diag.ECHO_LIVE = _live   # echo follows the toggle (boot lines echoed
            except Exception:            # before the first 3s tick either way)
                pass
            _diag_perf_sample(diag, ws)
            _diag_drawbrk(diag, ws)
            _diag_draw2(diag, ws)       # #63: split render into layer-copy vs sprite-batch us
            _diag_draw3(diag, ws)       # the REST of render (spr/circ/line) + the
                                        # measured dispatch residual
                                        # (dead RX: serial TX is this board's only proof)
            _diag_loop(diag, ws, _loop_acc)     # the average frame by loop stage --
            for _i in range(12):                # the steady-state HITCH never sees
                _loop_acc[_i] = 0
            _diag_chromebrk(diag, ws)   # #66 lever 5: bar/composite/cursor chrome sub-split
            _diag_layerbrk(diag, ws)    # #172: chrome's `other` IS the stack walk --
                                        # this names the layer inside it
            _diag_luamem(diag, ws)      # #67: the lua heap's SRAM/PSRAM split --
                                        # prices any structural SRAM proposal
            _diag_homebrk(diag, ws)     # launcher wallpaper/grid/bar split (cart-gated
                                        # DRAWBRK never fires on the home screen)
            _diag_pump(diag, comp)      # #66 lever 4: bounce-feed pacing (SPI idle gaps)
            _diag_i2cstat(diag, keyboard, touch)  # #69: kbd/touch I2C session latency
            _diag_calib(diag)           # #63: one-shot interpreter cost model (spill probe)
            if _live:
                _diag_gc(diag)          # #63/#68: the FORCED collect sample -- diag-only,
                                        # never during kid play (it costs a felt frame)
            _t_diag = _ticks_diff(_ticks_ms(), _tnow)
        # Diag SD flush: overwrite /sd/moybyte/diag.log with the current ring.
        # Runs between frames on the native single-bus path (with_sd_live), never
        # during a panel flush. Guarded -> a flush failure degrades to a no-op.
        # CADENCE (#66, hardware-measured): the write costs 80-120ms -- at the old
        # flat 5s that was a visible stutter DURING PLAY (HITCH sdflush=82-118
        # confirmed it). #68: the timer flush now runs ONLY with PERF DIAG on
        # (20s in-cart / 5s otherwise); in kid mode the ring is persisted at cart
        # EXIT instead (one write, off the play path) + the crash paths.
        _cart_now = ws.cart is not None
        _t_sd = 0
        if diag is not None and _diag_cart_prev and not _cart_now:
            # #66 repeat-run accumulation instrumentation (sakura fresh 53fps vs
            # warm 37-42): the host CPython console AND the bare unix-MP engine
            # path both measured NO accumulation (2026-07-10) -- whatever grows
            # lives only in the on-device console/cache interplay. So log the
            # live set at every cart EXIT (a timed collect is fine here -- off
            # the play path, same place the diag ring flushes): the MEMX curve
            # across a play-several-carts session names the growth transition.
            if _live:
                _t0 = _ticks_ms()
                gc.collect()
                _c_ms = _ticks_diff(_ticks_ms(), _t0)
                try:
                    import moy_alloc as _ma_stats
                    _ob = _ma_stats.stats()[1] // 1024   # #186 off-heap KB
                except (ImportError, AttributeError):
                    _ob = -1
                print("Moybyte %d MEMX live=%dk free=%dk collect=%dms offheap=%dk"
                      % (_ticks_ms(), gc.mem_alloc() // 1024,
                         gc.mem_free() // 1024, _c_ms, _ob))
            _t_sd = _diag_flush(diag, ws)  # #68: cart exited -> persist the session's ring
        _diag_cart_prev = _cart_now
        # The periodic flush now ALSO needs Settings -> DIAG SD LOG (ws.diag_sd,
        # owner call 2026-07-08): PERF DIAG alone streams serial samples with no
        # 20s SD-write stutter; crash/cart-exit flushes below stay unconditional.
        if (diag is not None and _live and getattr(ws, "diag_sd", False)
                and _ticks_diff(_tnow, _diag_flush_at) >= 0):
            _diag_flush_at = _tnow + (20000 if ws.cart is not None else 5000)
            _t_sd = _diag_flush(diag, ws)
        # The web bucket stays in the diag/LOOP format (parsers key on it) but
        # reads 0 since the streaming web view died (2026-08 sunset).
        _t_web = 0
        elapsed = _ticks_diff(_ticks_ms(), now)
        # Hitch logger (#66): any frame past HITCH_MS gets a HITCH line naming the
        # measured stages -- kbd (I2C keyboard poll), inp (trackball+touch+pointer),
        # ws (input handlers + frame: logic/render/chrome/flush), the 3s diag
        # sample, the diag->SD write -- the tool for catching the
        # "micro-stutter every couple of seconds" class of bug. A spike with all
        # the named parts small = the pause was between stages (e.g. an implicit
        # GC collect inside an alloc), which is itself the answer.
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, _t_kbd, _t_inp, _t_sb, _t_ws,
                        _t_diag, _t_sd, _t_web, _t_hi, _t_hp)
        # Frame pacing (#63): a running GAME locks to a steady cadence (30fps
        # default, manifest "fps": 60 for carts that sustain it) -- a LOCKED 30
        # feels smoother than a 38-55 swing, and the freed headroom absorbs
        # GC/SD hitches. Console screens/tools keep the loop's fps_cap (pointer
        # responsiveness). Re-read per iteration: it changes on cart open/exit.
        try:
            _fms = 1000 // ws.frame_cap_fps()
        except Exception:  # noqa: BLE001 -- pacing must never kill the loop
            _fms = frame_ms
        if _fms < frame_ms:
            _fms = frame_ms                     # never pace FASTER than the loop cap
        if elapsed < _fms:
            _t_sleep = _fms - elapsed
            if _pace_debt:                      # pay the debt out of this sleep
                _take = _t_sleep if _t_sleep < _pace_debt else _pace_debt
                _t_sleep -= _take
                _pace_debt -= _take
        else:
            _t_sleep = 0
            _pace_debt += elapsed - _fms
            if _pace_debt > 2 * _fms:
                _pace_debt = 2 * _fms           # unpayable: just run flat out
        # LOOP accumulation (see _loop_acc): plain int adds, one per stage per
        # frame. Done BEFORE the sleep so `frame` is work, and `sleep` is carried
        # separately -- a paced loop must not read as a slow one.
        _loop_acc[0] += 1
        _loop_acc[1] += elapsed
        _loop_acc[2] += _t_kbd
        _loop_acc[3] += _t_inp
        _loop_acc[4] += _t_sb
        _loop_acc[5] += _t_ws
        _loop_acc[6] += _t_web
        _loop_acc[7] += _t_diag
        _loop_acc[8] += _t_sd
        _loop_acc[9] += _t_sleep
        _loop_acc[10] += _t_hi       # ws.handle_input   (routing, not drawing)
        _loop_acc[11] += _t_hp       # ws.handle_pointer (routing, not drawing)
        if _t_sleep:
            time.sleep_ms(_t_sleep)


def run_touch_calibrate(handler):
    """Touch bring-up aid (moybyte_shell.RUN_TOUCH_CALIBRATE). Draws corner
    targets and prints each GT911 sample (raw + current mapping) over serial.

    It flushes the panel only ONCE up front and then just polls + prints, so USB
    serial keeps draining -- the normal desktop loop's continuous flush starves
    USB and you'd see nothing. Touch each yellow corner, read the raw coords over
    serial, then set TOUCH_SWAP / TOUCH_FLIP_X / TOUCH_FLIP_Y / TOUCH_RAW_* in device_input.py
    so the mapped value lands on that corner, and rebuild."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte touch-cal: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState, TDeckKeyboard
    except Exception as exc:  # noqa: BLE001
        print("Moybyte touch-cal unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("Moybyte touch-cal: no compositor")
        return
    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    canvas.cls(NAMES["black"])
    for (cx, cy) in ((8, 8), (canvas.w - 9, 8), (8, canvas.h - 9),
                     (canvas.w - 9, canvas.h - 9), (canvas.w // 2, canvas.h // 2)):
        canvas.rectb(cx - 6, cy - 6, 12, 12, NAMES["yellow"])
    canvas.print("TOUCH CORNERS", 100, canvas.h // 2 - 24, NAMES["white"], 2)
    canvas.print("watch serial", 108, canvas.h // 2 + 8, NAMES["light_grey"], 1)
    comp.flush()
    print("Moybyte touch-cal start avail=%d addr=%s"
          % (1 if touch.available else 0, hex(touch.addr) if touch.addr else "?"))
    while True:
        r = touch.debug_read()
        if r and r[1]:  # (status, 8 raw bytes) on a real touch
            status, d = r
            print("Moybyte touch-cal status=0x%02x bytes=%s"
                  % (status, " ".join("%02x" % b for b in d)))
        time.sleep_ms(50)


def run_keyboard_probe(handler):
    """Keyboard bring-up aid (moybyte_shell.RUN_KEYBOARD_PROBE): read the T-Deck
    keyboard over I2C0 and print the byte each key returns -- the code-editor's
    1-byte ASCII read path. No panel takeover/flush, so USB serial stays alive
    (the desktop loop's continuous flush would starve it).

    Tap each key left->right, top->bottom; each new key prints one `KEY ...` line.
    We deliberately do NOT send the raw-matrix command (0x03) so this shows the
    keyboard's plain ASCII protocol -- exactly what the editor should consume."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte kb-probe: takeover failed:", exc)
    try:
        from machine import I2C, Pin
    except Exception as exc:  # noqa: BLE001
        print("Moybyte kb-probe unavailable:", exc)
        return
    addr = 0x55
    try:
        i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte kb-probe i2c failed:", exc)
        return
    found = []
    try:
        found = i2c.scan()
    except Exception:  # noqa: BLE001
        pass
    print("Moybyte keyboard probe start; i2c scan=%s addr=0x%02x"
          % ([hex(a) for a in found], addr))
    print("Moybyte kb-probe: tap keys L->R, T->B. lines = KEY <n> 0x<hex> <dec> '<char>'")
    prev = 0
    n = 0
    beat = 0
    while True:
        try:
            d = i2c.readfrom(addr, 1)
            k = d[0] if d else 0
        except Exception as exc:  # noqa: BLE001
            print("Moybyte kb-probe read err:", exc)
            time.sleep_ms(300)
            continue
        if k and k != prev:
            n += 1
            ch = chr(k) if 0x20 <= k <= 0x7E else "."
            print("KEY %d 0x%02x %d '%s'" % (n, k, k, ch))
        prev = k
        beat += 1
        if beat % 250 == 0:        # ~5s heartbeat so you know it's alive
            print("Moybyte kb-probe alive (keys so far: %d)" % n)
        time.sleep_ms(20)
