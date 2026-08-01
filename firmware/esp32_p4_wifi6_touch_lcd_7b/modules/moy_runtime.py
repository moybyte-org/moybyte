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
from carts_data import CARTS   # build-time generated from system_carts/
from device_util import _ticks_ms, _ticks_diff
from device_api import make_api
from device_canvas import (DeviceCanvas, _LayerComp, _FONT8, _FONT8_FIRST,
                           _ST_FONT_SCALE, _COMPACT_MIN_PX)
from device_wifi import make_wifi

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


class P4SystemCanvas(DeviceCanvas):
    """The P4 SYSTEM canvas: DeviceCanvas over the DSI framebuffer + the system-
    surface contract (#39/#73) -- font_scale text, font-scale layers, and the
    native game/wallpaper composite hooks the shared WM/wallpaper probe for."""

    # Hardware PPA (pixel accelerator) module, set by enable_ppa() once at boot;
    # a class attribute so the one system canvas AND its layers share it, and a
    # PPA error demotes to the CPU kernel globally. None = CPU-only (blit565).
    _ppa = None

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
        # BEFORE the base __init__: its _install_draw_gates (#155) seeds the C
        # gate's state array from font_scale, and the native print gate renders
        # at that scale. Setting it afterwards would gate every system surface
        # at 1x until the next set_font_scale.
        self.font_scale = max(1, int(font_scale))
        DeviceCanvas.__init__(self, comp)
        # The root's staleness horizon = the panel's ACTUAL buffer rotation
        # (3 with the #58 render-overlap triple buffer, 2 on an older moy_dsi
        # build, 1 in the single-buffer degrade). Every partial-paint streak
        # (_retained_n in wm_windowed/launcher_layer) reads this.
        n = len(getattr(comp, "_fbs", ()) or ())
        if n:
            self.RETAINED_FRAMES = n

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))
        st = self._gate_state
        if st is not None:
            st[_ST_FONT_SCALE] = self.font_scale

    def print(self, s, x, y, c, scale=1):
        # SystemCanvas.print (#39): petme128 at font_scale via the native text
        # kernel's scale arg. The legacy per-call `scale` arg stays ignored,
        # exactly like the host. framebuf fallback can't scale -- but a P4 build
        # always carries moy_gfx, so that path only ever renders 1x.
        fs = self.font_scale
        if fs <= 1 or self._gfx_text is None:
            DeviceCanvas.print(self, s, x, y, c)
            return
        self.flush_batch()
        self._gfx_text(self._buf, self.w, self.h, str(s), int(x), int(y),
                       self._col(c), _FONT8, _FONT8_FIRST, fs,
                       self._cam_x, self._cam_y,
                       self._clip_x0, self._clip_y0,
                       self._clip_x1, self._clip_y1)

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

    def new_layer(self, w, h, owner=None):
        # Font-scale-carrying layers (mirrors host SystemCanvas.new_layer): the
        # windowed WM's window buffers and the bar cache print through these, so
        # they must scale like the surface they composite onto. Same body as
        # DeviceCanvas.new_layer with the subclass constructed instead -- including
        # its size gate on the COMPACT FIRST pre-collect, which matters MORE here:
        # a collect on the P4 desk is ~55ms (mark scales with the live set), and
        # this override is the one the bar's 1024x18 strip cache reaches, twice per
        # gesture. See DeviceCanvas.new_layer for the full note.
        if int(w) * int(h) >= _COMPACT_MIN_PX:
            try:
                import gc
                gc.collect()
            except Exception:  # noqa: BLE001
                pass
        lay = P4SystemCanvas(_LayerComp(int(w), int(h), self._gfx),
                             font_scale=self.font_scale)
        lay._nocache = True
        # #113: ONE persistent buffer, so a blit-scrolling surface must measure
        # against the LAST paint. The class default 2 describes the ROOT DPI
        # ping-pong only. This line exists in DeviceCanvas.new_layer and was
        # LOST when this subclass copied its body -- with the ring armed, a
        # picker drag then shifted by ~twice the real delta and ghosted a second
        # copy of every card (owner-reported on glass 2026-07-25).
        lay.RETAINED_FRAMES = 1
        comp = lay._comp
        if owner is not None and comp.pooled:
            lent = self._lent_layers
            if lent is None:
                lent = self._lent_layers = {}
            lent.setdefault(owner, []).append((comp._buf, comp._nbytes))
        return lay

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
        moy_gfx CPU kernel. Both write the same RGB565 bytes (glass-verified
        pixel-identical), so the fallback is graceful.

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
        if ppa is not None and ox >= 0 and oy >= 0 \
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

    def blit_cover(self, gc):
        """wallpaper._backdrop_blit's device path (#58): the smallest integer
        upscale of the 320x240 wallpaper frame that COVERS the whole desktop,
        centered + cropped (dx/dy <= 0). The crop overflows the picture, which
        the PPA can't do (it has no clip), so this stays on the CPU kernel --
        it's a launcher-only backdrop (drag frames restore from the cache via
        blit_copy, which IS the PPA), not a per-frame hot path."""
        gw, gh = gc.w, gc.h
        sw, sh = self.w, self.h
        scale = max(1, (sw + gw - 1) // gw, (sh + gh - 1) // gh)
        ox = (sw - gw * scale) // 2
        oy = (sh - gh * scale) // 2
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        self.flush_batch()
        g = self._gfx
        if g is None:
            return
        g.blit565_scale(self._buf, self.w, self.h, int(ox), int(oy),
                        gc._buf, gc.w, gc.h, int(scale))


def _load_carts():
    """Load carts from the internal-flash store (seeding built-ins on first
    boot); fall back to the embedded CARTS on any store failure."""
    try:
        import moy_carts
        moy_carts.ensure_dirs(CARTS_ROOT)
        moy_carts.seed_builtins(CARTS, CARTS_ROOT)
        carts = moy_carts.scan(CARTS_ROOT)
        if carts:
            print("Moybyte P4 loaded %d carts from flash" % len(carts))
            return carts, CARTS_ROOT
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4 flash carts unavailable:", exc)
    print("Moybyte P4 using built-in carts")
    return [dict(c) for c in CARTS], None


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


def _remote_state(ws):
    """One-line JSON snapshot for the serial `state` command -- the on-glass
    test harness's introspection half (tools/p4_autotest.py parses it). Every
    field is best-effort: a broken subsystem reads as null, never a crash."""
    st = {}
    try:
        st["screen"] = ws.screen
        st["frames"] = getattr(ws, "_frames_drawn", None)
        # Idle screen blank (#58): the harness has to be able to tell a blanked
        # panel from a hung one -- they look identical from the host end.
        st["psave"] = [bool(getattr(ws, "_psave_asleep", False)),
                       getattr(ws, "_psave_ms", POWER_SAVE_MS) // 1000]
        # Expensive-event counters (ws.note_cost): cache builds + storage reads.
        # A cache that is silently missing shows up here as a count that tracks the
        # frame count -- which is how the 2026-07-26 bar-strip thrash would have
        # announced itself instead of taking a day to corner.
        st["costs"] = dict(getattr(ws, "costs", {}) or {})
        wm = ws.wm
        desk = getattr(wm, "desk_open", None)
        st["desk"] = bool(desk()) if desk is not None else None
        st["order"] = list(getattr(wm, "_order", ()) or ())
        st["focus"] = getattr(wm, "_focus", None)
        wins = {}
        for k in st["order"]:
            win = wm._wins.get(k)
            if win is not None:
                wins[k] = [win.x, win.y, win.w, win.h, win.title_h,
                           win.kind, bool(win.minimized)]
        st["wins"] = wins
        # #113: a window buffer is ONE retained surface -- 2 here means a
        # blit-scroll would measure against the wrong paint and ghost.
        st["win_retained"] = dict(
            (k, getattr(wm._wins[k].buf, "RETAINED_FRAMES", None))
            for k in st["order"]
            if wm._wins.get(k) is not None and wm._wins[k].buf is not None)
    except Exception as exc:  # noqa: BLE001
        st["wm_err"] = str(exc)
    try:
        sl = ws.settings_layer
        sr = sl.scroll
        st["settings"] = {
            "set_top": sl.set_top, "sel": sl.set_msel,
            "rows": len(sl._settings_rows()),
            "offset": None if sr is None else sr.offset,
            "view": None if sr is None else list(sr.view),
            "content": None if sr is None else sr.content,
            "wifi_view": bool(sl.wifi_view), "bt_view": bool(sl.bt_view),
        }
        win = ws.wm._wins.get("settings") if hasattr(ws.wm, "_wins") else None
        if win is not None and win.ctx is not None:
            lay = win.ctx.layout
            st["settings"]["lay"] = [lay.set_x, lay.set_row_y0,
                                     lay.set_w, lay.set_row_h]
    except Exception as exc:  # noqa: BLE001
        st["settings_err"] = str(exc)
    try:
        if ws.wifi is not None:
            st["wifi"] = list(ws.wifi.status())
        else:
            st["wifi"] = None
    except Exception as exc:  # noqa: BLE001
        st["wifi_err"] = str(exc)
    try:
        # Look the app cart up by TITLE, never by folder name: the device seeds
        # from the title slug (appearance.moy) while the host store copies the
        # source folder (theme_picker.moy) -- assuming either name is what broke
        # is_app in the first place (on-glass 2026-07-25).
        app = getattr(ws, "appearance_app", None)
        cart = None
        for c in ws._all_carts:
            if c.get("title") == "Appearance":
                cart = c
                break
        if cart is None:
            st["appearance_cart"] = None
        else:
            st["appearance_cart"] = {
                "title": cart.get("title"), "version": cart.get("version"),
                "path": cart.get("path"),
                "perms": list(cart.get("permissions") or ()),
                "is_app": bool(app.is_app(cart)) if app is not None else None,
            }
        # Every registered system app's claim, so a harness run catches the
        # whole class rather than just Appearance.
        claims = {}
        for _app, _text in getattr(ws, "_apps", ()):
            claims[_app.id] = sum(1 for c in ws._all_carts if _app.is_app(c))
        st["app_claims"] = claims
    except Exception as exc:  # noqa: BLE001
        st["appearance_err"] = str(exc)
    return st


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
    from p4_ble_keyboard import BleHidKeyboard
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

    carts, carts_root = _load_carts()
    # P4 keyboard (#26): the C6_WIFI MicroPython variant already exposes NimBLE
    # central/GATT-client bindings over ESP-Hosted SDIO. Keep construction lazy
    # until /moy exists (the bond store lives beside the carts), and start the
    # radio after the Workstation has finished its boot allocations below.
    keyboard = BleHidKeyboard(inp, store_path="/moy/ble_keyboard.json",
                              auto_start=False)
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=FONT_SCALE)
    # #67 Phase 1: the Lua cart runtime (shared glue in device_api.py) -- wired
    # only when the moy_lua native module is in this build; without it a
    # "runtime": "lua" cart opens the Player's runtime-missing panel.
    lua_runtime = None
    try:
        import moy_lua as _moy_lua_probe  # noqa: F401 -- availability probe
        from device_api import make_lua_runtime
        lua_runtime = make_lua_runtime(ws)
        print("Moybyte P4: lua runtime ON")
    except ImportError:
        pass
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
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: reboot hook unavailable:", exc)
    # The P4 presentation tier (#73/#58, two worlds #105): the DESK is home
    # (make world, windows); the PLAY icon drops to the fullscreen Library.
    # Installed AFTER load_system (same order as host build_workstation) so the
    # persisted font scale is applied before the root layout context is captured.
    ws.wm = WindowedWM(ws)
    ws.open_desk()
    keyboard.start()               # failure is touch-only, never a boot failure

    # Remote input over serial (#58 dev affordance): the CH343 REPL stays alive
    # under the desktop (no USB starvation on this board), so complete LINES piped
    # into the port drive the UI while the glass is watched:
    #   tap <x> <y>   synthetic tap at system-canvas coords
    #   tap sysmenu   tap a named bar button (any ws.layout.<name>_btn rect:
    #                 sysmenu / wifi / batt / context_x)
    #   bt status|scan|forget|trace [0|1]  BLE keyboard diagnostics
    #   quit          leave the desktop for the REPL
    # Ctrl-C still interrupts as before (handled below the stdin read).
    try:
        import sys
        import select
        _sin = select.poll()
        _sin.register(sys.stdin, select.POLLIN)
    except Exception:  # noqa: BLE001 -- remote input is optional sugar
        _sin = None

    import gc
    gc.collect()
    print("Moybyte P4 desktop running (Ctrl-C for REPL)")
    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    _backlight_on = False          # dark until the first composed frame (#45)
    _ps_ms = POWER_SAVE_MS         # idle blank timeout (serial `power` retunes)
    _asleep = False                # panel blanked by the idle timer
    _ps_force = False              # serial `power off`: blank on the next frame
    _idle_at = _ticks_ms()         # last frame that saw real input
    _drag_script = None            # remote `drag` playback state (see below)
    _swipe_script = None           # remote `swipe` playback state (see below)
    # Perf sampler (#58 fps-ledger groundwork): serial is free on this board, so
    # print a PERF line every ~2s -- drawn-fps, average busy loop ms, and the
    # console's own draw/flush/logic/render/chrome EMAs (filled because
    # perf_capture is on). Costs two tick reads per frame.
    ws.perf_capture = True
    _pf_at = _ticks_ms() + 2000
    _pf_n = 0
    _pf_busy = 0
    _pf_drawn = 0
    ws.arm_splash()
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        # BLE notifications arrive asynchronously; applying their latest level
        # state before begin_frame gives InputState clean press/release edges.
        # poll() also advances scan/reconnect and flushes a newly-created bond
        # once, outside the NimBLE IRQ.
        try:
            keyboard.poll()
        except Exception as exc:  # noqa: BLE001 -- keyboard must fail touch-only
            print("Moybyte P4 BLE keyboard poll failed:", exc)
        inp.begin_frame()
        click = False
        _serial_cmd = False        # a dev command counts as activity (wakes it)
        tp = touch.poll()
        pointer.down = tp is not None
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:
                click = True
        if _sin is not None and _sin.poll(0):
            line = ""
            try:
                line = sys.stdin.readline().strip()
            except Exception:  # noqa: BLE001
                pass
            parts = line.split() if line else []
            _serial_cmd = bool(parts)
            if parts and parts[0] == "quit":
                print("REMOTE quit -> REPL")
                return
            if parts and parts[0] == "bt":
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
            if parts and parts[0] == "tap":
                r = None
                if len(parts) == 3:
                    try:
                        r = (int(parts[1]), int(parts[2]))
                    except ValueError:
                        r = None
                elif len(parts) == 2:
                    rect = getattr(ws.layout, parts[1] + "_btn", None)
                    if rect:
                        r = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
                if r is not None:
                    pointer.place(r[0], r[1])
                    pointer.down = True   # released next frame (touch reads None)
                    click = True
                    print("REMOTE tap %d %d" % r)
                else:
                    print("REMOTE ? %s" % line)
            if parts and parts[0] == "run":
                # `run <name>`: select the first cart whose title matches and RUN
                # it (the launcher tap path) -- deterministic game launch without
                # tile-hunting, for measuring the play + game-window-drag paths.
                name = (" ".join(parts[1:])).lower() if len(parts) > 1 else ""
                items = getattr(ws.launcher, "items", [])
                idx = None
                for i in range(len(items)):
                    it = items[i]
                    if not it.get("path"):
                        continue
                    t = str(it.get("title") or "").lower()
                    if not name:
                        idx = i
                        break
                    if name in t:
                        idx = i
                        break
                if idx is not None:
                    ws.launcher.sel = idx
                    ws.launch_selected()
                    print("REMOTE run %s" % items[idx].get("title"))
                else:
                    print("REMOTE run: no cart match")
            if parts and parts[0] == "diag":
                # `diag 0|1`: toggle the diagnostic frame-eaters (perf_capture +
                # the on-screen FPS chip) to measure the TRUE shipping fps. The
                # fps= field of the PERF line reads _frames_drawn either way, so
                # it stays valid with perf_capture off (only the ms EMAs go
                # stale). Default here is ON (a measurement build).
                on = not (len(parts) == 2 and parts[1] == "0")
                ws.perf_capture = on
                ws.show_fps = on
                ws._dirty = True
                print("REMOTE diag %s" % ("on" if on else "off"))
            if parts and parts[0] == "skip":
                # `skip 0|1`: A/B the #77 frameskip gate (logic full-rate, render
                # halved) without walking to Settings. persist=False: a serial A/B
                # must not rewrite the kid's system.json.
                on = not (len(parts) == 2 and parts[1] == "0")
                ws.set_frameskip(on, persist=False)
                print("REMOTE skip %s" % ("on" if on else "off"))
            if parts and parts[0] == "gov":
                # `gov 0|1`: A/B the frame governor (#63 SNES rule) on glass --
                # a GAME locks to its manifest fps (default 30) with the freed
                # headroom absorbing GC/render spikes. frame_cap_fps() re-reads
                # the module global every loop, so this takes effect immediately;
                # the shipped default stays OFF (owner measurement mode).
                on = not (len(parts) == 2 and parts[1] == "0")
                import console as _console_mod
                _console_mod.FPS_GOVERNOR = on
                print("REMOTE gov %s" % ("on" if on else "off"))
            if parts and parts[0] == "union":
                # `union 0|1`: A/B the dirty-union gesture restore (#58; 1=on,
                # 0=full-screen restore) -- pairs with `drag [frames]`.
                on = not (len(parts) == 2 and parts[1] == "0")
                ws.wm._union_disabled = not on
                print("REMOTE union %s" % ("on" if on else "off"))
            if parts and parts[0] == "cache":
                # `cache 0|1`: A/B the drag backdrop cache on glass (1=on).
                on = not (len(parts) == 2 and parts[1] == "0")
                ws.wm._backdrop_disabled = not on
                print("REMOTE cache %s" % ("on" if on else "off"))
            if parts and parts[0] == "power":
                # `power`            -- report
                # `power <seconds>`  -- retune the idle blank (0 disables)
                # `power off`        -- blank NOW, without waiting out the timer
                #                      (the board README wants exactly this
                #                      reading to split SoC vs backlight draw)
                # `power on`         -- wake now
                _arg = parts[1] if len(parts) > 1 else ""
                if _arg == "off":
                    _ps_force = True
                elif _arg == "on":
                    _idle_at = now
                    if _asleep:
                        _asleep = False
                        set_backlight(True)
                        _backlight_on = True
                        ws._psave_asleep = False
                        ws._dirty = True
                elif _arg:
                    try:
                        _ps_ms = max(0, int(_arg)) * 1000
                        ws._psave_ms = _ps_ms
                        _idle_at = now
                    except ValueError:
                        print("REMOTE power ? %s" % line)
                print("REMOTE power timeout=%ds asleep=%s idle=%ds"
                      % (_ps_ms // 1000, _asleep,
                         _ticks_diff(now, _idle_at) // 1000))
            if parts and parts[0] == "open":
                # `open settings|picker`: pop an app window deterministically (no
                # tile-hunting) so a drag can be measured against a known window.
                # `open appearance` reports open_app's claim result (a False is
                # the silent no-op the on-glass harness needs to SEE); `open
                # wifi` deep-links Settings -> the wifi panel.
                what = parts[1] if len(parts) > 1 else ""
                if what == "appearance":
                    ok = ws.open_app(ws.appearance_app)
                    print("REMOTE open appearance -> %s" % ok)
                elif what == "wifi":
                    ws.open_settings()
                    ws.settings_layer.open_wifi()
                    print("REMOTE open wifi")
                else:
                    fn = {"settings": getattr(ws, "open_settings", None),
                          "picker": getattr(ws, "open_picker", None)}.get(what)
                    if fn is not None:
                        fn()
                        print("REMOTE open %s" % what)
                    else:
                        print("REMOTE open ? %s" % line)
            if parts and parts[0] == "py" and len(parts) > 1:
                # `py <code>`: eval/exec one line against the LIVE console (ws /
                # wm / pointer in scope) between frames -- the harness's probe
                # hook (attach counters, read gesture state, monkeypatch a
                # surface under test). Dev-board serial only, like every
                # command here.
                _code = line.split(None, 1)[1]
                _env = {"ws": ws, "wm": ws.wm, "pointer": pointer,
                        "comp": comp, "game": game}
                try:
                    try:
                        print("PY %r" % (eval(_code, _env),))
                    except SyntaxError:
                        exec(_code, _env)
                        print("PY ok")
                except Exception as exc:  # noqa: BLE001
                    print("PY ERR %s: %s" % (type(exc).__name__, exc))
            if parts and parts[0] == "state":
                # `state`: one-line JSON snapshot (world/windows/settings scroll/
                # wifi/cart claims) -- the harness's assertion source.
                try:
                    import json as _json
                    print("STATE %s" % _json.dumps(_remote_state(ws)))
                except Exception as exc:  # noqa: BLE001
                    print("STATE {\"err\": \"%s\"}" % exc)
            if parts and parts[0] == "swipe" and len(parts) >= 5:
                # `swipe x0 y0 x1 y1 [frames]`: a synthetic touch gesture fed
                # through the SAME pointer path as the glass (press edge at the
                # start point, interpolated held move, release at the end), so
                # the harness can exercise scroll/drag/fling on any surface.
                try:
                    _swipe_script = {"i": 0,
                                     "x0": int(parts[1]), "y0": int(parts[2]),
                                     "x1": int(parts[3]), "y1": int(parts[4]),
                                     "n": max(2, int(parts[5]))
                                     if len(parts) > 5 else 20}
                    print("REMOTE swipe %d,%d -> %d,%d frames=%d"
                          % (_swipe_script["x0"], _swipe_script["y0"],
                             _swipe_script["x1"], _swipe_script["y1"],
                             _swipe_script["n"]))
                except ValueError:
                    _swipe_script = None
                    print("REMOTE swipe ? %s" % line)
            if parts and parts[0] == "drag":
                # `drag [frames]`: grab the TOP window's title strip and oscillate
                # it for `frames` frames (default 120), so the PERF sampler reports
                # DRAG-time fps -- the backdrop-cache lever's target. No window open
                # -> a no-op note.
                order = getattr(ws.wm, "_order", None) or []
                if order:
                    win = ws.wm._wins[order[-1]]
                    n = 120
                    step = 6
                    if len(parts) >= 2:
                        try:
                            n = max(8, int(parts[1]))
                        except ValueError:
                            pass
                    if len(parts) >= 3:
                        try:
                            step = max(1, int(parts[2]))  # px/frame amplitude scale
                        except ValueError:
                            pass
                    _drag_script = {"i": 0, "n": n, "step": step,
                                    "cx": win.x + 30,
                                    "cy": win.y + max(6, win.title_h // 2)}
                    print("REMOTE drag win=%s cx=%d cy=%d frames=%d step=%d"
                          % (order[-1], _drag_script["cx"], _drag_script["cy"],
                             n, step))
                else:
                    print("REMOTE drag: no window open")
        if _drag_script is not None:
            s = _drag_script
            i = s["i"]
            if i >= s["n"]:
                pointer.down = False
                _drag_script = None
                print("REMOTE drag done")
            else:
                # Triangle wave around the grab point: continuous movement so the
                # drag stays engaged and every frame is dirty (the drag path).
                t = i % 40
                tri = t if t < 20 else 40 - t          # 0..20..0
                off = 0 if i == 0 else (tri - 10) * s["step"]  # amplitude = step*10
                pointer.place(s["cx"] + off, s["cy"])
                pointer.down = True
                click = (i == 0)                        # frame 0 arms the drag
                s["i"] = i + 1
        if _swipe_script is not None:
            s = _swipe_script
            i = s["i"]
            n = s["n"]
            if i > n:
                _swipe_script = None
                print("REMOTE swipe done")
            else:
                # i==0 press edge at the start, i in 1..n-1 held interpolation,
                # i==n the release sample at the end point (down=False so the
                # gesture machines see a real release, fling velocity intact).
                f = min(i, n - 1) / (n - 1)
                x = s["x0"] + int((s["x1"] - s["x0"]) * f)
                y = s["y0"] + int((s["y1"] - s["y0"]) * f)
                pointer.place(x, y)
                pointer.down = i < n
                click = (i == 0)
                s["i"] = i + 1
        # -- idle screen blank (#58) -------------------------------------------
        # Placed after EVERY input source has been read (touch, BLE keys, the
        # serial dev channel) and before the pointer is handed to the console,
        # which is what lets the waking touch be swallowed below.
        _active = (tp is not None or bool(inp._held) or inp.last_key
                   or _serial_cmd)
        if _ps_force:
            # `power off` arrives ON the serial channel, which is itself
            # activity -- so an explicit blank has to outrank _active or it
            # would wake again in the very same iteration.
            _ps_force = False
            _asleep = True
            _idle_at = now
            set_backlight(False)
            _backlight_on = False
            ws._psave_asleep = True
        elif _active:
            _idle_at = now
            if _asleep:
                _asleep = False
                set_backlight(True)
                _backlight_on = True
                ws._psave_asleep = False
                # The panel may hold a frame from before the blank, and the
                # partial-paint machinery would happily leave it there.
                ws._dirty = True
                # The touch that WAKES the screen must not also press whatever
                # it landed on -- otherwise a wake tap launches a cart.
                pointer.down = False
                click = False
        elif (_ps_ms and not _asleep
                and _ticks_diff(now, _idle_at) >= _ps_ms):
            _asleep = True
            set_backlight(False)
            _backlight_on = False
            ws._psave_asleep = True
            print("Moybyte P4 power save: screen off (idle %ds)" % (_ps_ms // 1000))
        pointer.click = click
        pointer.tick(now)
        # Present the PREVIOUS quiet game frame now (its async composite has been
        # DMAing through the input poll above): wait the DMA, switch scan-out to
        # it, free the other buffer. No-op unless the last frame deferred (#58
        # composite-overlap). Must precede sync_back, which re-points at the freed
        # buffer.
        comp.present_pending()
        game.sync_back()           # off-screen: contract no-op
        sys_canvas.sync_back()     # double-buffer: re-point at the new BACK fb
        try:
            ws.handle_input()
            ws.handle_pointer()
            ws.frame(dt)           # draw + composite + flush (cache msync)
        except KeyboardInterrupt:
            raise                  # Ctrl-C -> moybyte_shell -> REPL
        except Exception as exc:   # noqa: BLE001 -- one bad frame must not brick the boot
            print("Moybyte P4 frame error:", exc)
            gc.collect()
        # First composed frame lights the panel (#45). `not _asleep` is load
        # bearing: without it this fires the frame after the idle blank -- the
        # blank clears _backlight_on and frames keep being drawn -- and the
        # screen comes straight back on.
        if not _backlight_on and not _asleep \
                and getattr(ws, "_frames_drawn", 0) > 0:
            set_backlight(True)
            _backlight_on = True
        elapsed = _ticks_diff(_ticks_ms(), now)
        _pf_n += 1
        _pf_busy += elapsed
        if _ticks_diff(_ticks_ms(), _pf_at) >= 0:
            _drawn = getattr(ws, "_frames_drawn", 0)
            # home(wp/grid/bar): the LAUNCHER frame's section split (stashed by
            # the shared launcher_layer under perf_capture) -- names where a
            # slow desktop repaint goes; empty when the last frame wasn't the
            # home screen.
            _home = getattr(ws, "_pf_home", None)
            print("PERF fps=%d/%d busy=%dms draw=%.0f flush=%.0f logic=%.0f "
                  "render=%.0f chrome=%.0f wmr=%d wmw=%d wms=%d cart=%s%s"
                  % ((_drawn - _pf_drawn) // 2, _pf_n // 2,
                     _pf_busy // (_pf_n or 1),
                     ws._draw_ms, ws._flush_ms, ws._upd_ms, ws._cart_ms,
                     ws._chrome_ms,
                     getattr(ws, "_pf_wm_restore", 0),   # drag backdrop restore ms
                     getattr(ws, "_pf_wm_windows", 0),   # window-stack pass ms
                     getattr(ws, "_pf_wm_stamp", 0),     # window content stamp ms
                     (ws.cart or {}).get("title", "-"),
                     (" home(wp=%d grid=%d bar=%d)" % _home) if _home else ""))
            _pf_at = _ticks_ms() + 2000
            _pf_n = 0
            _pf_busy = 0
            _pf_drawn = _drawn
        try:
            _fms = 1000 // ws.frame_cap_fps()
        except Exception:  # noqa: BLE001 -- pacing must never kill the loop
            _fms = frame_ms
        if _fms < frame_ms:
            _fms = frame_ms
        if elapsed < _fms:
            time.sleep_ms(_fms - elapsed)
