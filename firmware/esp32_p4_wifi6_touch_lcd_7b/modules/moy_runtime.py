"""Moybyte P4 device backend (#58): the shared console on the Waveshare 7B.

This is the P4 sibling of the T-Deck's moy_runtime, an order of magnitude
smaller because the board removed the walls the T-Deck backend exists to fight:
no flush ceiling (DPI scan-out), no SD<->display bus war (separate buses), no
keyboard mode-flipping (BLE HID has real make/break reports), no input-poller
thread (nothing stalls the loop).

The two-domain seam (#39) runs for real here for the first time on hardware:

  * `P4SystemCanvas` -- the SYSTEM canvas: a DeviceCanvas (RGB565 + native
    moy_gfx) drawing DIRECTLY into the 1024x600 DSI scan-out framebuffer, plus
    what a system surface must add over a game canvas: a settings-chosen
    font_scale (native text kernel's scale arg), font-scale-carrying layers
    (window buffers, the bar cache), and the two native composite hooks the
    shared presentation code probes for -- blit_game (the windowed WM's
    game->window viewport, wm_windowed._blit_game) and blit_cover (the
    wallpaper's cover-crop desktop backdrop, wallpaper._backdrop_blit) -- each
    ONE moy_gfx.blit565_scale call over the game canvas's RGB565 buffer.
  * the GAME canvas stays a plain 320x240 DeviceCanvas over an off-screen
    buffer; carts + make_api (device_api, staged from the T-Deck tree) are
    byte-identical to the T-Deck's.
  * `run_desktop` constructs the shared Workstation with BOTH canvases and
    installs `WindowedWM` -- the launcher is the desktop, every app a floating
    window (#73's presentation tier, finally on its intended hardware).

Carts live on the INTERNAL flash VFS (31.5MB -- SD is optional on this board;
the SDIO slot + LDO4 power fix are a follow-up for removable-cart workflows).
"""

import time

from console import Pointer, Workstation, wire_workstation_core
# The boot spine + frame pump, shared with the T-Deck (#161 Phase 4/5,
# canonical: runtime/device_boot.py; board.toml stages it like every other
# shared module). The steps that used to be written twice -- boot splash, cart
# seed+scan, the Lua runtime probe, the OTA verdict + rollback confirm, the
# frame cadence -- live there now, as do the idle screen blank (IdleBlank) and
# the serial dev channel (runtime/dev_channel.py, one vocabulary for every
# board -- this file adds only the P4-only extras: bt/union/cache). What stays
# here is hardware: the DPI scan-out, the PPA composite, BLE HID.
from device_boot import (DeviceBoot, FrameLoop, FramePump, IdleBlank,
                         OtaHealth, apply_touch, poll_webhost)
from carts_data import CARTS   # build-time generated from system_carts/
from device_util import _ticks_ms, _ticks_diff
from device_api import make_api
from device_canvas import DeviceCanvas, SystemCanvas, _LayerComp
from device_wifi import autoconnect_wifi, make_wifi

GAME_W, GAME_H = 320, 240
FONT_SCALE = 1                     # 1x everywhere (owner call, 2026-07-12): the 7"
                                   # 1024x600 fits CONTENT, not magnification --
                                   # geometry is resolution-driven; persisted
                                   # system.json still overrides (Settings FONT SIZE)
# Internal-flash store root. NOT "/moybyte/..." -- a root-level dir named like an
# importable module SHADOWS the frozen module of that name ('' precedes '.frozen'
# on sys.path), and the first boot's seeded /moybyte dir broke the next boot's
# `from moybyte.input import ...` (hardware-learned 2026-07-08).
CARTS_ROOT = "/moy/carts"
# Where an OTA image stages (#53). This board has no SD -- the T-Deck's
# /sd/update has no meaning here -- so it lands on the internal VFS, which
# has ~23MB free against a ~3MB image. NOT under /moy/carts: the store
# scans that directory.
OTA_UPDATE_DIR = "/moy/update"


class P4SystemCanvas(SystemCanvas):
    """The P4 SYSTEM canvas: the shared system-surface contract (#39/#73 --
    font_scale text, font-scale layers, blit_cover: ONE body in device_canvas'
    SystemCanvas) over the DSI framebuffer, plus what only this board has:
    the PPA hardware-composite hooks the shared WM probes for."""

    # Hardware PPA (pixel accelerator) module, set by enable_ppa() once at boot;
    # a class attribute so the one system canvas AND its layers share it, and a
    # PPA error demotes to the CPU kernel globally. None = CPU-only (blit565).
    _ppa = None

    # Game-composite filtering: the PPA's SRM scaler is fixed BILINEAR in
    # silicon (no nearest mode, no flag -- 2026-08-20), which smears pixel-art
    # carts. False = CRISP PIXELS (Settings row, persisted via
    # console.set_crisp_pixels -> set_crisp_scale below): the composite goes
    # nearest-neighbour through moy_ppa.blit_crisp's SRAM-bounce band pipeline,
    # falling back to the CPU kernel. A class attribute like _ppa: the one
    # system canvas and its layers share the mode.
    _smooth = True

    @classmethod
    def enable_ppa(cls):
        """Probe + register the P4 PPA once (run_desktop calls this after the
        panel is up). Returns True if hardware compositing is live."""
        try:
            import moy_ppa
            if moy_ppa.init():
                cls._ppa = moy_ppa
                return True
        except Exception as exc:  # noqa: BLE001 -- any failure -> CPU kernel
            print("Moybyte P4 PPA unavailable:", exc)
        cls._ppa = None
        return False

    def __init__(self, comp, font_scale=1):
        SystemCanvas.__init__(self, comp, font_scale=font_scale)
        # The root's staleness horizon = the panel's ACTUAL buffer rotation
        # (3 with the #58 render-overlap triple buffer, 2 on an older moy_dsi
        # build, 1 in the single-buffer degrade). Every partial-paint streak
        # (_retained_n in wm_windowed/launcher_layer) reads this.
        n = len(getattr(comp, "_fbs", ()) or ())
        if n:
            self.RETAINED_FRAMES = n

    def set_crisp_scale(self, on):
        """Settings -> CRISP PIXELS (probed by console.set_crisp_pixels): route
        the game composite nearest-neighbour instead of the PPA's fixed
        bilinear. Turning crisp OFF returns blit_crisp's SRAM bounce bands to
        the internal heap -- that pool is the Lua allocator's first choice, so
        a mode nobody has on must not tax it."""
        on = bool(on)
        P4SystemCanvas._smooth = not on
        ppa = self._ppa
        if not on and ppa is not None:
            rel = getattr(ppa, "crisp_release", None)
            if rel is not None:
                try:
                    rel()
                except Exception:  # noqa: BLE001 -- freeing is best-effort
                    pass

    # Below this many pixels a CPU fill wins outright: the rect is cache-resident
    # and the PPA's ~60us submit cost dominates. Measured on glass 2026-07-26 --
    # 128x64 CPU 0.07ms vs PPA 0.15ms; 200x100 CPU 0.72ms vs PPA 0.19ms.
    PPA_FILL_MIN_PX = 16384

    def ppa_fill(self, x, y, w, h, col):
        """Clear a block on the PPA DMA engine. True if it took it (#155).

        Worth having even though a 1:1 PPA COPY measured a wash against the CPU:
        a copy moves the same bytes either way, but a CPU fill pays an extra
        cache-line READ per line (write-allocate on PSRAM) that the DMA does not.
        So a fill is the one op where DMA does strictly LESS memory traffic --
        measured 18.0ms -> 3.6ms for the full screen."""
        ppa = self._ppa
        if ppa is None or w <= 0 or h <= 0 or w * h < self.PPA_FILL_MIN_PX:
            return False
        fill = getattr(ppa, "fill", None)
        if fill is None:
            return False              # older firmware: CPU path
        try:
            return bool(fill(self._buf, self._stride, self._bh,
                             x, y, w, h, col))
        except Exception as exc:  # noqa: BLE001 -- a real error demotes for good
            print("Moybyte P4 PPA fill failed -> CPU:", exc)
            P4SystemCanvas._ppa = None
            return False

    # new_layer is DeviceCanvas.new_layer's one body (the SystemCanvas
    # _make_layer hook constructs this class); its COMPACT-FIRST pre-collect
    # matters MORE here -- a collect on the P4 desk is ~55ms and the bar's
    # 1024x18 strip cache builds layers twice per gesture -- and the copy this
    # class used to carry had lost the cart-palette rider AND (2026-07-25, on
    # glass) once the RETAINED_FRAMES = 1 pin: a picker drag shifted by ~twice
    # the real delta and ghosted every card. That is why there is no copy.

    # -- the native composite hooks (probed via getattr by the shared code) ----

    def blit_game(self, gc, ox, oy, scale, defer=False, src=None):
        # A cart-declared VIEW (`view(w, h)`, e.g. celeste's 128x128 p8 screen):
        # crop the source rect into a scratch layer via one dest-clipped blit565
        # with a NEGATIVE dest offset (no crop kernel needed), then scale the
        # scratch through the normal path below -- the PPA's scaled-fit check
        # passes and the composite reads a SMALLER source than the full canvas.
        if src is not None and self._gfx is not None:
            fb0 = getattr(gc, "flush_batch", None)
            if fb0 is not None:
                fb0()
            sx, sy, vw, vh = src
            scr = getattr(self, "_view_scratch", None)
            if scr is None or scr.w != vw or scr.h != vh:
                scr = self._view_scratch = self.new_layer(vw, vh)
            self._gfx.blit565(scr._buf, vw, vh, -sx, -sy,
                              gc._buf, gc.w, gc.h, -1)
            gc = scr
        return self._blit_game_full(gc, ox, oy, scale, defer)

    def _blit_game_full(self, gc, ox, oy, scale, defer=False):
        """wm_windowed._blit_game's device path (#58/#73): integer-scale the
        320x240 game canvas into this surface at (ox, oy). Hardware PPA (DMA,
        ~2.6x faster than the CPU blit -- measured) when available, else the
        moy_gfx CPU kernel. NOTE the two do NOT write the same bytes: the PPA
        scaler is fixed bilinear (smeared pixel art), the CPU kernel nearest --
        which is what the CRISP PIXELS toggle (_smooth above) trades on. In
        crisp mode moy_ppa.blit_crisp keeps nearest pixels at ~60% of the CPU
        kernel's cost (SRAM bounce bands + 1:1 DMA ship, byte-exact vs the CPU
        kernel -- glass-verified); any refusal falls back to the CPU kernel.

        defer=True (a QUIET game frame, where this is the frame's LAST framebuffer
        write) kicks the PPA async and hands the show to P4Compositor.flush ->
        present_pending, so the DMA overlaps the next frame's input poll (#58
        composite-overlap budget lever). full-paint frames pass defer=False so
        the following chrome never races the DMA."""
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        self.flush_batch()
        ox = int(ox)
        oy = int(oy)
        scale = int(scale)
        ppa = self._ppa
        # The PPA needs the scaled block to fit INSIDE the output picture (it
        # can't clip like the CPU kernel). The game->window composite always
        # fits (scale is derived from the window rect); only the cover-crop
        # backdrop overflows, and that takes the CPU path below. A non-fit is a
        # normal per-call condition, NOT a PPA failure -- don't demote for it.
        if ppa is not None and not P4SystemCanvas._smooth:
            # CRISP PIXELS: nearest-neighbour banded composite. blit_crisp
            # applies the same fit gate itself and returns False on any
            # refusal (no SRAM bands / non-fit / hardware error), which falls
            # through to the CPU kernel below -- identical pixels, slower.
            # It never demotes _ppa: the bilinear path stays healthy.
            bc = getattr(ppa, "blit_crisp", None)
            if bc is not None:
                try:
                    if bc(self._buf, self.w, self.h, ox, oy,
                          gc._buf, gc.w, gc.h, scale, 1 if defer else 0):
                        if defer:
                            self._comp._composite_pending = True
                        return
                except Exception as exc:  # noqa: BLE001 -- fall to the CPU
                    print("Moybyte P4 crisp blit failed -> CPU:", exc)
        elif ppa is not None and ox >= 0 and oy >= 0 \
                and ox + gc.w * scale <= self.w and oy + gc.h * scale <= self.h:
            try:
                if defer:
                    # NOTE the DOUBLE GAME CANVAS (copy-on-swap so this pending
                    # could go fence-free) was built and REVERTED 2026-07-28
                    # with a measured verdict: windowed Brick Siege 56 -> 41fps.
                    # The blocking "game" fence this defer pays at
                    # present_pending measured ~FREE at this composite size
                    # (the DMA finishes within the input poll), while the
                    # swap's ~150KB retention memcpy cost 4-5ms EVERY quiet
                    # frame and the fence-free show backlogged into
                    # _drain_pending collisions. See #58 for the numbers; the
                    # design is in git history if a bigger composite ever
                    # changes the arithmetic.
                    ppa.blit_async(self._buf, self.w, self.h, ox, oy,
                                   gc._buf, gc.w, gc.h, scale)
                    self._comp._composite_pending = True   # flush() will defer
                else:
                    ppa.blit_scale(self._buf, self.w, self.h, ox, oy,
                                   gc._buf, gc.w, gc.h, scale)
                return
            except Exception as exc:  # noqa: BLE001 -- real error -> CPU forever
                print("Moybyte P4 PPA blit failed -> CPU:", exc)
                P4SystemCanvas._ppa = None
        g = self._gfx
        if g is None:
            return
        g.blit565_scale(self._buf, self.w, self.h, ox, oy,
                        gc._buf, gc.w, gc.h, scale)

    # NOTE: no PPA path for the full-screen backdrop restore -- a 1:1 copy is
    # PSRAM-bandwidth-bound (measured ~26ms both ways: the DSI scan-out shares
    # the bus), so the accelerator only wins on UPSCALE composites (small source
    # read) -- exactly blit_game above / blit_cover below.

    def blit_strip_async(self, layer, x, y):
        """The #58 drag stamp-defer hook (probed by wm_windowed._draw_app_window):
        kick `layer`'s 1:1 stamp at (x, y) on the PPA NON-BLOCKING and defer the
        scan-out switch (the same composite-pending/present_pending machinery as
        the quiet-game-frame composite). A 1:1 PPA copy is a wall-time WASH vs
        the CPU (PSRAM-bound both ways) -- but async it runs on the DMA engine
        while the loop does input/logic, hiding the drag frame's dominant cost
        (the ~24ms window-content stamp, measured 2026-07-10). Returns False (no
        PPA / non-fit / hardware refusal) so the caller falls back to the sync
        CPU stamp. MUST be the frame's LAST framebuffer write (the caller draws
        the chrome FIRST; regions are disjoint, and the PPA driver's dest cache
        writeback at submit covers the shared edge cache lines)."""
        ppa = self._ppa
        x = int(x)
        y = int(y)
        if (ppa is None or x < 0 or y < 0
                or x + layer.w > self.w or y + layer.h > self.h):
            return False
        fb = getattr(layer, "flush_batch", None)
        if fb is not None:
            fb()
        self.flush_batch()
        # DON'T kick the DMA here: the bar / chips / cursor layers still CPU-draw
        # AFTER the window stack, and any of their writes near the in-flight DMA
        # region get clobbered by stale cache-line evictions (glass-confirmed
        # 2026-07-10: persistent desktop droppings during drags, cleaned by the
        # release repaint). REGISTER the stamp instead; P4Compositor.flush()
        # kicks it after the WHOLE frame has drawn -- the true last write.
        self._comp._stamp_pending = (self._buf, self.w, self.h, x, y,
                                     layer._buf, layer.w, layer.h)
        return True

    # blit_cover is SystemCanvas's shared body -- CPU kernel by design: the
    # cover-crop overflows the picture, which the PPA can't do (no clip), and
    # it's a launcher-only backdrop, not a per-frame hot path.


# Loading the carts is DeviceBoot.load_carts now (#161 Phase 4): the seed +
# scan + built-in fallback is the same on both boards, and what differs here is
# arguments -- the internal-flash root above, no storage SESSION at all (this
# console has no SD card and the store races nobody), and the word "flash" in
# the serial lines. On a full-erase boot that call is 17.5 of the 25 seconds
# before anything composes, and every second of it is seeding -- which is what
# the splash's progress bar is for.


def run_touch_calibrate():
    """Touch calibration aid (#58): corner + center targets on the glass, every
    GT911 sample printed to serial as raw + mapped coords + the live knob state.

    Run it from the REPL (Ctrl-C out of the desktop first):

        import moy_runtime; moy_runtime.run_touch_calibrate()

    Tap each numbered box; read which box the MAPPED coords land in. The knobs
    are live module globals -- Ctrl-C, `import p4_input; p4_input.FLIP_X = True`
    (etc.), re-run, and once mapped == tapped everywhere, bake the winners into
    p4_input.py. Ctrl-C exits (the REPL stays alive on this board)."""
    from p4_display import P4Compositor, set_backlight
    from p4_input import Touch
    import p4_input

    comp = P4Compositor()
    canvas = P4SystemCanvas(comp, font_scale=2)
    touch = Touch(canvas.w, canvas.h)
    w, h = canvas.w, canvas.h
    targets = ((60, 60, "1 TOP-LEFT"), (w - 61, 60, "2 TOP-RIGHT"),
               (60, h - 61, "3 BOT-LEFT"), (w - 61, h - 61, "4 BOT-RIGHT"),
               (w // 2, h // 2, "5 CENTER"))

    def _draw(msg, mx=-1, my=-1):
        canvas.cls(0)
        for (cx, cy, label) in targets:
            canvas.rectb(cx - 20, cy - 20, 40, 40, 10)          # yellow box
            canvas.print(label, max(4, min(w - 180, cx - 40)), cy + 26, 7)
        canvas.print("TOUCH CALIBRATE - tap the boxes, watch serial", w // 2 - 340, h // 2 - 60, 7)
        canvas.print(msg, w // 2 - 340, h // 2 + 40, 6)
        if mx >= 0:
            canvas.rect(mx - 4, my - 4, 9, 9, 8)                # red: mapped landing
        comp.flush()

    _draw("swap=%s flip_x=%s flip_y=%s" % (p4_input.SWAP_XY, p4_input.FLIP_X, p4_input.FLIP_Y))
    set_backlight(True)
    print("Moybyte P4 touch calibrate: swap=%s flip_x=%s flip_y=%s (Ctrl-C to exit)"
          % (p4_input.SWAP_XY, p4_input.FLIP_X, p4_input.FLIP_Y))
    last_print = 0
    while True:
        tp = touch.poll()
        now = _ticks_ms()
        if tp is not None and (tp[2] or _ticks_diff(now, last_print) > 250):
            last_print = now
            raw = touch.raw or (-1, -1)
            print("TAP%s mapped=(%d,%d) raw=(%d,%d) swap=%s flip_x=%s flip_y=%s"
                  % ("*" if tp[2] else " ", tp[0], tp[1], raw[0], raw[1],
                     p4_input.SWAP_XY, p4_input.FLIP_X, p4_input.FLIP_Y))
            if tp[2]:
                _draw("last mapped=(%d,%d) raw=(%d,%d)"
                      % (tp[0], tp[1], raw[0], raw[1]), tp[0], tp[1])
        time.sleep_ms(20)


def run_ppa_smoke(scale=2, iters=60):
    """A/B the P4 hardware PPA vs the CPU moy_gfx blit for the game->window
    composite (#58 perf). Ctrl-C the desktop to the REPL first, then:

        import moy_runtime; moy_runtime.run_ppa_smoke()

    Draws a 320x240 test pattern (colored quadrants + label), then times `iters`
    integer-upscale composites into the 1024x600 framebuffer TWO ways --
    moy_gfx.blit565_scale (CPU) and moy_ppa.blit_scale (PPA DMA) -- showing each
    result so correctness (colors/scale/position) is eyeballable over serial +
    glass, and printing per-blit timings + the speedup. The composite is the
    exact op wm_windowed._blit_game runs every game frame, so the speedup here is
    the headline lever for both game play and window drags.

    moy_dsi.init() is idempotent, so a fresh P4Compositor reuses the live panel
    the interrupted desktop left up (no re-init, no reflash)."""
    from p4_display import P4Compositor, set_backlight

    comp = P4Compositor()
    gfx = comp.gfx()
    W, H = comp.size()
    sw, sh = GAME_W, GAME_H
    game = DeviceCanvas(_LayerComp(sw, sh, gfx))
    # A pattern whose colors + orientation make a wrong byte-order / mirror
    # instantly obvious: red TL, green TR, blue BL, yellow BR, white label.
    game.cls(0)
    game.rect(0, 0, sw // 2, sh // 2, 8)            # red
    game.rect(sw // 2, 0, sw // 2, sh // 2, 11)     # green
    game.rect(0, sh // 2, sw // 2, sh // 2, 12)     # blue
    game.rect(sw // 2, sh // 2, sw // 2, sh // 2, 10)  # yellow
    game.rectb(0, 0, sw, sh, 7)
    game.print("PPA", sw // 2 - 12, sh // 2 - 4, 7)
    game.flush_batch()

    ox = (W - sw * scale) // 2
    oy = (H - sh * scale) // 2
    set_backlight(True)

    # Clear BOTH ping-pong buffers to a dark bg so the letterbox is clean.
    for _ in range(2):
        gfx.fill(comp.framebuffer(), W * H, 1)
        comp.flush()

    def _time(label, blit):
        fb = comp.framebuffer()      # write the SAME back buffer each iter (no
        gfx.fill(fb, W * H, 1)       # flush inside the loop) to isolate the blit
        try:
            import gc
            gc.collect()
        except Exception:  # noqa: BLE001
            pass
        t0 = _ticks_ms()
        for _ in range(iters):
            blit(fb)
        ms = _ticks_diff(_ticks_ms(), t0)
        comp.flush()                 # show the composited result for eyeballing
        per = ms / (iters or 1)
        print("PPA SMOKE %s: %.2f ms/blit (%d iters, %d ms total)"
              % (label, per, iters, ms))
        return per

    cpu = _time("CPU  blit565_scale",
                lambda fb: gfx.blit565_scale(fb, W, H, ox, oy,
                                             game._buf, sw, sh, scale))
    import time
    time.sleep(2)                    # a beat to see the CPU frame on glass

    ppa_per = None
    try:
        import moy_ppa
        ok = moy_ppa.init()
        print("PPA SMOKE moy_ppa.init() ->", ok)
        if ok:
            ppa_per = _time("PPA  blit_scale",
                            lambda fb: moy_ppa.blit_scale(fb, W, H, ox, oy,
                                                          game._buf, sw, sh, scale))
    except Exception as exc:  # noqa: BLE001
        print("PPA SMOKE moy_ppa unavailable:", exc)

    if ppa_per is not None and ppa_per > 0:
        print("PPA SMOKE RESULT scale=%d: cpu=%.2fms ppa=%.2fms speedup=%.1fx"
              % (scale, cpu, ppa_per, cpu / ppa_per))
    print("PPA SMOKE done -> REPL")


# Idle screen blank (#58): milliseconds of NO INPUT before the panel goes dark,
# so the board can sit plugged in for days without a lit screen. 0 disables it.
# Overridable before boot (`import moy_runtime; moy_runtime.POWER_SAVE_MS = ...`)
# and at runtime over the serial `power` command.
#
# This is a DARK SCREEN, not a suspend, and deliberately so: the loop keeps
# running, a cart mid-run keeps ticking, and the serial dev channel stays live,
# because the on-glass harness (#156) has to reach a board that has been idle
# for hours. The backlight is also the one power lever the board README calls
# out as unmeasured -- its 2.85W draw has never been split between the SoC and
# the panel, and "one reading with the backlight blanked settles it".
POWER_SAVE_MS = 300000          # 5 minutes


def run_desktop(fps_cap=60):
    """Boot the shared console on the P4: launcher-as-desktop under WindowedWM,
    GT911 touch as the pointer, a BLE HID keyboard over the companion C6, and
    carts on internal flash. Ctrl-C over the CH343 REPL interrupts the loop (no
    USB starvation on this board)."""
    from p4_display import P4Compositor, set_backlight
    from p4_input import Touch
    from ble_keyboard import BleHidKeyboard
    from moybyte.input import InputState
    from wm_windowed import WindowedWM
    import moy_carts

    comp = P4Compositor()
    gfx = comp.gfx()
    print("Moybyte P4 display up (%dx%d, gfx=%s)"
          % (comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = P4SystemCanvas(comp, font_scale=FONT_SCALE)
    # Hardware compositing (#58): the P4 PPA offloads the game->window scale
    # blit + the drag backdrop-cache copy from the CPU (DMA, ~2.6x). CPU kernel
    # if it fails to register.
    print("Moybyte P4 PPA:", "enabled" if P4SystemCanvas.enable_ppa() else "CPU-only")

    # -- the shared boot spine (#45/#58/#161) ------------------------------
    # DeviceBoot owns the boot splash + its progress bar, the cart seed/scan,
    # the Lua runtime probe and the "first frame in Nms" report -- one
    # implementation, both boards. What differs here is its arguments: the
    # serial prefix and this board's panel-light function.
    #
    # The panel stays dark until a frame has composed (#45), which is right:
    # an uninitialised framebuffer is worse than black. But it makes a slow
    # boot indistinguishable from a dead board. Owner-reported after a full
    # erase: "screen is black", serial silent after its last boot line, on a
    # board that was in fact working and did light up eventually.
    # The splash makes the wait legible on the glass and on the wire; the
    # timing line at the end of this function names where a slow boot went.
    boot = DeviceBoot(sys_canvas, comp, set_backlight, "Moybyte P4")
    boot.note("starting")
    # The fixed 320x240 GAME canvas (#39): off-screen RGB565 sharing the same
    # native kernel; the windowed WM composites it into the player window.
    # (#77: -O3 on moy_gfx and an internal-SRAM game canvas were A/B'd here --
    # individually AND combined -- and all measured render-slice no-ops: the
    # slice is MicroPython per-draw-call dispatch, not C compute or framebuffer
    # bandwidth. So the canvas stays in PSRAM (internal SRAM is wanted for
    # WiFi/audio DMA). See docs/perf_native_gap_v1.md.)
    game = DeviceCanvas(_LayerComp(GAME_W, GAME_H, gfx))
    inp = InputState()
    touch = Touch(sys_canvas.w, sys_canvas.h)
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer          # touch-driven carts read it via the api touch()

    boot.note("loading cartridges")
    carts, carts_root = boot.load_carts(moy_carts, CARTS, root=CARTS_ROOT,
                                        media="flash")
    # P4 keyboard (#26): the C6_WIFI MicroPython variant already exposes NimBLE
    # central/GATT-client bindings over ESP-Hosted SDIO. Keep construction lazy
    # until /moy exists (the bond store lives beside the carts), and start the
    # radio after the Workstation has finished its boot allocations below.
    keyboard = BleHidKeyboard(inp, store_path="/moy/ble_keyboard.json",
                              auto_start=False)
    boot.note("building the desktop")
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=FONT_SCALE)
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster plays on its own off-screen canvas -- the exact constructor the
    # boot `game` canvas uses -- and P4SystemCanvas.blit_game (PPA) upscales it
    # like any game composite (a 128x120 view fills the 600px height at 5x).
    ws.make_game_canvas = lambda w, h: DeviceCanvas(
        _LayerComp(int(w), int(h), gfx))
    # The #67 Lua cart runtime (DeviceBoot.lua_runtime -- one probe, both
    # boards; see its docstring for why there is no chooser and what a build
    # without the module does instead).
    lua_runtime = boot.lua_runtime(ws)
    # The shared service wiring (console.wire_workstation_core -- one canonical
    # order for host + both boards; this used to be a hand-kept "same order as
    # host build_workstation" copy). P4 notes: can_manage's carts_root default is
    # the internal VFS -- no bus gymnastics, _with_sd stays the direct-call
    # default; the C6-hosted WLAN is transparent to network.WLAN
    # (bring-up-confirmed); no I2S audio backend yet.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          make_wifi(moy_carts, carts_root),
                          lua_runtime=lua_runtime,
                          pointer=pointer, inp=inp, keyboard=keyboard)
    # OTA firmware update (#53 on this board). The partition table has been
    # OTA-shaped since bring-up (ota_0/ota_1, 4MB each) and update_ui has been
    # frozen in all along; this is the piece that was missing.
    #
    # Two things differ from the T-Deck. There is no SD card in this console, so
    # the image stages on the internal VFS (~23MB free, against a ~3MB image) and
    # with_sd is a plain call-through -- no bus to drain, no card to mount. And
    # the board identity matters: an OTA payload is an app-partition image, so
    # the manifest is per board and this one must never be handed an S3 build.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(lambda fn: fn(),
                                        update_dir=OTA_UPDATE_DIR)
        ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: OTA updater unavailable:", exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: reboot hook unavailable:", exc)
    # WEB CONSOLE (moycore plan 3.4 pull half): serve the wasm console from this
    # board. Constructed, NOT started -- __init__ binds no socket, so injecting
    # it only makes the Settings row appear. `ensure_online` returns the STA IP,
    # which is what the row displays: 0.0.0.0 is the one address nobody can type
    # into a browser.
    try:
        from moy_webhost import make_webhost, P4_WEB_DIR

        # The link wait that used to be a closure here is moy_webhost.ensure_online
        # now -- it was the same 25 lines the T-Deck needed, and writing it per
        # board is how that board went without the feature entirely.
        ws.webhost = make_webhost(ws, carts_root, P4_WEB_DIR,
                                  autoconnect=autoconnect_wifi)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: web console unavailable:", exc)
    # The P4 presentation tier (#73/#58, two worlds #105): the DESK is home
    # (make world, windows); the PLAY icon drops to the fullscreen Library.
    # Installed AFTER load_system (same order as host build_workstation) so the
    # persisted font scale is applied before the root layout context is captured.
    ws.wm = WindowedWM(ws)
    ws.open_desk()
    keyboard.start()               # failure is touch-only, never a boot failure

    # The serial dev channel (#58/#156): ONE implementation for every board --
    # `dev_channel.DevChannel`, staged from runtime/ (state / tap / run / open /
    # swipe / drag / diag / skip / gov / power / py / web / quit; the CH343 REPL
    # stays alive under the desktop, so complete lines piped into the port
    # drive the UI while the glass is watched). Ctrl-C still interrupts as
    # before. This board's EXTRAS -- commands only its hardware or tier has:
    #   bt status|scan|forget|trace [0|1]  BLE keyboard diagnostics
    #   union 0|1   A/B the dirty-union gesture restore (pairs with `drag`)
    #   cache 0|1   A/B the drag backdrop cache
    #   crisp 0|1   A/B the CRISP PIXELS composite (non-persisting)
    idle = IdleBlank(set_backlight, POWER_SAVE_MS)
    ws._psave_ms = POWER_SAVE_MS   # `state` reports the LIVE timeout

    def _bt_cmd(ws, parts, line):
        action = parts[1] if len(parts) > 1 else "status"
        if action == "scan":
            print("REMOTE bt scan ->", keyboard.scan())
        elif action == "forget":
            keyboard.forget()
            print("REMOTE bt forgot keyboard + local bonds")
        elif action == "status":
            print("REMOTE bt status state=%s name=%s passkey=%s "
                  "protocol=%s interval_ms=%s notify=%s fast=%s "
                  "dsi_underruns=%s error=%s"
                  % (keyboard.status()[0], keyboard.status()[1],
                     keyboard.status()[2], keyboard.protocol,
                     keyboard._conn_interval_ms, keyboard._notify_count,
                     keyboard.fast_status(), comp.underruns(),
                     keyboard.error))
        elif action == "trace":
            on = not (len(parts) > 2 and parts[2] == "0")
            print("REMOTE bt trace ->", keyboard.trace(on))
        else:
            print("REMOTE bt ? %s" % line)

    def _union_cmd(ws, parts, line):
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.wm._union_disabled = not on
        print("REMOTE union %s" % ("on" if on else "off"))

    def _cache_cmd(ws, parts, line):
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.wm._backdrop_disabled = not on
        print("REMOTE cache %s" % ("on" if on else "off"))

    def _crisp_cmd(ws, parts, line):
        # A/B the CRISP PIXELS composite from serial without persisting, so a
        # measurement session never leaves the board on a non-default mode.
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.set_crisp_pixels(on, persist=False)
        print("REMOTE crisp %s" % ("on" if on else "off"))

    try:
        from dev_channel import DevChannel
        # env: what the `py` probe hook can reach beyond ws/wm/pointer --
        # `pump.debt` and `boot.lit`/`boot.done` are the shared spine's only
        # on-glass witnesses (pump joins the env right after it is created).
        serial = DevChannel(ws, pointer, set_backlight=set_backlight, idle=idle,
                            extra={"bt": _bt_cmd, "union": _union_cmd,
                                   "cache": _cache_cmd, "crisp": _crisp_cmd},
                            env={"comp": comp, "game": game, "boot": boot})
    except Exception as exc:  # noqa: BLE001 -- remote input is optional sugar
        print("Moybyte P4 serial channel unavailable:", exc)
        serial = None

    import gc
    gc.collect()
    # Say what became of the last update before anything else can overwrite the
    # evidence (#53). The rollback CONFIRM does not happen here -- reaching this
    # line only proves the desktop was built, and an image that never paints has
    # already shipped once (#56). It is fired from the frame loop below
    # (FramePump.tail), after the console has actually drawn.
    _ota = OtaHealth(ws, log=lambda m: print("Moybyte P4 OTA: %s" % m))
    _ota.boot_check()
    print("Moybyte P4 desktop running (Ctrl-C for REPL)")
    # The last thing before the loop, and the stage the silent wait was in:
    # everything above had already printed when the screen was reported black.
    # start_frames also arms the boot logo, but ONLY if the splash never came up
    # (arming it otherwise replays the splash and delays the desktop).
    boot.start_frames(ws)
    # The shared frame pump (#161 Phase 5): the dt clock, the once-only
    # first-frame/OTA housekeeping, and the cadence + pacing debt. Everything
    # BETWEEN its head and its tail is this board's own hardware.
    pump = FramePump(boot, _ota, fps_cap)
    if serial is not None:
        serial.env["pump"] = pump   # created just above; see the env note
    # Perf sampler (#58 fps-ledger groundwork): serial is free on this board, so
    # print a PERF line every ~2s -- drawn-fps, average busy loop ms, and the
    # console's own draw/flush/logic/render/chrome EMAs. Costs two tick reads
    # per frame; the LINE is unconditional (its fps= field reads _frames_drawn,
    # so it is valid with the meters off, and tools/p4_perf.py parses it).
    #
    # The METERS follow Settings -> PERF DIAG, exactly as on the T-Deck
    # (#68 kid mode: perf_capture arms per-layer walk timing, per-op canvas
    # timers and the EMA tail -- ~1-1.5ms of every frame there). This line read
    # an unconditional True until 2026-08-15, so the toggle gated nothing at
    # boot on this board and the shipping fps could not be measured without
    # first issuing `diag 0` -- which tools/p4_perf.py already did, its
    # docstring already claiming "DIAG IS OFF BY DEFAULT".
    ws.perf_capture = bool(getattr(ws, "diag_live", False))
    _pf = {"at": _ticks_ms() + 2000, "n": 0, "busy": 0, "drawn": 0}

    def _poll_inputs(now):
        """This board's input sources: the BLE keyboard's async notifications
        (applied before begin_frame so InputState gets clean press/release
        edges; poll() also advances scan/reconnect and flushes a new bond once,
        outside the NimBLE IRQ) and the GT911 pointer. The dev channel and the
        idle blank run in the SHARED loop, in the one order that lets the
        waking touch be swallowed."""
        try:
            keyboard.poll()
        except Exception as exc:  # noqa: BLE001 -- keyboard must fail touch-only
            print("Moybyte P4 BLE keyboard poll failed:", exc)
        inp.begin_frame()
        touched, click = apply_touch(touch, pointer)
        return click, (touched or bool(inp._held) or bool(inp.last_key))

    def _present():
        # Present the PREVIOUS quiet game frame now (its async composite has
        # been DMAing through the input poll above): wait the DMA, switch
        # scan-out to it, free the other buffer. No-op unless the last frame
        # deferred (#58 composite-overlap). Must precede sync_back, which
        # re-points at the freed buffer.
        comp.present_pending()
        game.sync_back()           # off-screen: contract no-op
        sys_canvas.sync_back()     # double-buffer: re-point at the new BACK fb

    def _frame_error(exc):
        print("Moybyte P4 frame error:", exc)
        gc.collect()

    def _tail(now):
        poll_webhost(ws)               # see the helper for why the frame TAIL

    def _account(now, elapsed, sleep_ms):
        _pf["n"] += 1
        _pf["busy"] += elapsed
        if _ticks_diff(_ticks_ms(), _pf["at"]) >= 0:
            _drawn = getattr(ws, "_frames_drawn", 0)
            # GUARDED, like every diag helper on the T-Deck. This block reads a
            # dozen Workstation internals owned by the SHARED runtime/console.py
            # and sits OUTSIDE the frame try, so while the reads were bare,
            # renaming one of them there dropped this board to the REPL about
            # two seconds after boot -- a measurement killing the loop it
            # measures. PRINTED rather than swallowed (2s cadence, live serial)
            # so a stale sampler says so; the timer resets either way, so a
            # broken sample cannot become a per-frame retry.
            try:
                # The meters follow Settings -> PERF DIAG live, so flipping it
                # needs no reboot (T-Deck twin: the 3s diag tick in its tail).
                _live = bool(getattr(ws, "diag_live", False))
                if ws.perf_capture != _live:
                    ws.perf_capture = _live
                # home(wp/grid/bar): the LAUNCHER frame's section split (stashed
                # by the shared launcher_layer under perf_capture) -- names
                # where a slow desktop repaint goes; empty when the last frame
                # wasn't the home screen.
                _home = getattr(ws, "_pf_home", None)
                print("PERF fps=%d/%d busy=%dms draw=%.0f flush=%.0f logic=%.0f "
                      "render=%.0f chrome=%.0f wmr=%d wmw=%d wms=%d cart=%s%s"
                      % ((_drawn - _pf["drawn"]) // 2, _pf["n"] // 2,
                         _pf["busy"] // (_pf["n"] or 1),
                         getattr(ws, "_draw_ms", 0), getattr(ws, "_flush_ms", 0),
                         getattr(ws, "_upd_ms", 0), getattr(ws, "_cart_ms", 0),
                         getattr(ws, "_chrome_ms", 0),
                         getattr(ws, "_pf_wm_restore", 0),  # drag backdrop restore ms
                         getattr(ws, "_pf_wm_windows", 0),  # window-stack pass ms
                         getattr(ws, "_pf_wm_stamp", 0),    # window content stamp ms
                         (getattr(ws, "cart", None) or {}).get("title", "-"),
                         (" home(wp=%d grid=%d bar=%d)" % _home) if _home else ""))
            except Exception as _pf_exc:   # noqa: BLE001 -- a diag never kills the loop
                print("PERF sample failed: %s: %s"
                      % (type(_pf_exc).__name__, _pf_exc))
            _pf["at"] = _ticks_ms() + 2000
            _pf["n"] = 0
            _pf["busy"] = 0
            _pf["drawn"] = _drawn

    # The shared frame loop (#202 Phase B): the invariant order lives ONCE, in
    # device_boot.FrameLoop -- including the #77/#161 pacing debt via
    # pump.pace and the first-frame backlight gate (dark until the first
    # composed frame, #45, unless the splash already lit it). Every hook above
    # is this board's own hardware.
    loop = FrameLoop(ws, pump, pointer, _poll_inputs, idle=idle, serial=serial,
                     present=_present, tail=_tail, account=_account,
                     frame_error=_frame_error,
                     set_backlight=set_backlight, lit=boot.lit)
    loop.run()
