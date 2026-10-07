# Map (grep -n a name to jump there):
#   P4SystemCanvas                   the P4 system canvas: PPA fill, scale and present
#   P4SystemCanvas.enable_ppa        probe and register the PPA
#   P4SystemCanvas.set_crisp_scale   CRISP PIXELS
#   P4SystemCanvas.present_frame     a compiled cart's frame on the PPA
#   P4SystemCanvas.blit_strip_async  the drag stamp-defer hook
#   run_ppa_smoke                    A/B the PPA against the CPU blit
"""The ESP32-P4 SYSTEM canvas (the P4 silicon tier's Python half): the shared
system-surface contract over the DSI framebuffer, plus the hardware PPA
composite hooks the shared WM probes for.

Born inside the Waveshare 7B's `moy_runtime.py` (#58/#73) and promoted here
on 2026-09-06, the day the Guition JC8012P4A1C became its second consumer
(Phase C, docs/board_ports_2026-08.md). Nothing in it is a board's: the PPA is
the P4's, `moy_ppa` is the silicon tier's (native/p4), and the composite
arithmetic reads the canvas sizes it is handed. What IS a board's -- the
compositor's backlight, the touch driver, the canvas sizes -- stays in each
board's `moy_runtime.py`, which constructs this class.

The two-domain seam (#39) as it runs on both P4 boards:

  * `P4SystemCanvas` -- the SYSTEM canvas: a DeviceCanvas (RGB565 + native
    moy_gfx) drawing DIRECTLY into the DSI scan-out framebuffer, plus what a
    system surface must add over a game canvas: a settings-chosen font_scale
    (native text kernel's scale arg), font-scale-carrying layers (window
    buffers, the bar cache), and the two native composite hooks the shared
    presentation code probes for -- blit_game (the windowed WM's game->window
    viewport, wm_windowed._blit_game) and blit_cover (the wallpaper's
    cover-crop desktop backdrop, wallpaper._backdrop_blit) -- each ONE
    moy_gfx.blit565_scale call over the game canvas's RGB565 buffer, or the
    PPA's DMA where it wins (upscale composites only; the README of either
    board carries the measurements).
  * the GAME canvas stays a plain 320x240 DeviceCanvas over an off-screen
    buffer; carts + make_api are byte-identical to the T-Deck's.
"""

try:
    from device_canvas import (DeviceCanvas, SystemCanvas, _LayerComp,
                               _ST_N_FILL, _ST_N_TEXT, _room)
    from device_util import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host package lane
    from device.device_canvas import (DeviceCanvas, SystemCanvas, _LayerComp,
                                      _ST_N_FILL, _ST_N_TEXT, _room)
    from device.device_util import _ticks_ms, _ticks_diff


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
    # the console's crisp-pixels setter -> set_crisp_scale below): the game
    # canvas's composite goes nearest-neighbour through moy_ppa.blit_crisp's
    # SRAM-bounce band pipeline, falling back to the CPU kernel. A compiled
    # cart's direct-colour frame is scaled from the cart's memory
    # (present_frame) and keeps the bilinear scale. A class attribute like
    # _ppa: the one system canvas and its layers share the mode.
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
        # A compositor whose paint target PERSISTS (the rotated one: the
        # console paints one landscape buffer and the scan buffers are its
        # rotated copies) says so; otherwise the horizon is the ping-pong.
        n = getattr(comp, "retained_frames", None)
        if n is None:
            n = len(getattr(comp, "_fbs", ()) or ())
        if n:
            self.RETAINED_FRAMES = n
        # A compositor that presents DESCRIBED frames (the rotated one) takes
        # the WM's damage rects straight; the root's coordinates are the paint
        # buffer's. Absent otherwise, so the WM's probe finds nothing to feed.
        nd = getattr(comp, "note_damage", None)
        if nd is not None:
            self.note_damage = nd
        # The quiet-frame snapshot (sync_back / _gates_unchanged): the two
        # native gate counters and this surface's clears, as three ints so a
        # play frame builds no tuple. -1 = no frame yet, never "unchanged".
        self._clears = 0
        # The cart-view crop scratch, pooled across frames. Its OWN name: the
        # base class's `_view_scratch` on the same object is a _LayerComp
        # (`._w`/`.framebuffer()`), this one a DeviceCanvas (`.w`/`._buf`), and
        # one name for two shapes is a crash waiting for the day blit_game
        # stops being fully overridden here.
        self._view_crop = None
        self._q_fill = -1
        self._q_text = -1
        self._q_clears = -1
        # A compiled cart's frame (present_frame): the scratch's patch
        # picture, viewed once per scratch rather than sliced every frame, the
        # frame's patches, and its composite for the rotated compositor's
        # paint route, bound once.
        self._pic = None
        self._pic_for = None
        self._fr = None
        self._fr_patches = [0] * 24       # six numbers a patch, four at most
        self._paint_frame_fn = self._paint_frame
        _room(self)                       # device_canvas._MAP_SIZES says why

    def cls(self, c=0):
        # Counted, because a clear is the one whole-surface write the native
        # gates cannot see: on the PPA it is moy_ppa.fill, on the CPU a direct
        # moy_gfx.fill -- neither is a gated verb. The PLAY world's letterbox
        # is a cls (composite_game, twice per game open), and with it
        # invisible the frame read as quiet, so only the game rect reached the
        # scan buffers and the two of them kept different Library pixels in
        # the bezel: the flicker behind a fullscreen game (owner, Guition P4,
        # 2026-09-09).
        self._clears += 1
        SystemCanvas.cls(self, c)

    def set_crisp_scale(self, on):
        """Settings -> CRISP PIXELS (probed by the console's crisp-pixels setter): route
        the game canvas's composite nearest-neighbour instead of the PPA's
        fixed bilinear -- the palette-based frames, pixel art's. A compiled
        cart's direct-colour frame (present_frame) keeps the bilinear scale
        either way. Turning crisp OFF returns blit_crisp's SRAM bounce bands
        to the internal heap -- that pool is the Lua allocator's first choice,
        so a mode nobody has on must not tax it."""
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
            scr = self._view_crop
            if scr is None or scr.w != vw or scr.h != vh:
                if scr is not None:
                    # Off-heap, no collector: give it back once no PPA op of
                    # the last frame still reads it.
                    if self._ppa is not None:
                        self._ppa.sync()
                    scr.release()
                scr = self._view_crop = self.new_layer(vw, vh)
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
        # A ROTATED compositor (a landscape desk on portrait glass) presents by
        # rotating the paint buffer, so the composite lands in the paint buffer
        # synchronously -- there is no scan-out switch to defer -- and a quiet
        # frame instead tells the compositor which rect it may rotate alone.
        if getattr(self._comp, "rotated", False):
            # Register the composite and let the compositor decide at flush:
            # a quiet frame (nothing else drew -- the gates below) goes to the
            # scan buffer directly, anything else takes the paint route. The
            # PPA only wins bilinear; crisp pixels take the paint route too.
            comp = self._comp
            direct = (ppa is not None and P4SystemCanvas._smooth
                      and ox >= 0 and oy >= 0
                      and ox + gc.w * scale <= self.w
                      and oy + gc.h * scale <= self.h)
            comp.mark_game(gc._buf, gc.w, gc.h, ox, oy, scale,
                           lambda: self._composite_paint(gc, ox, oy, scale),
                           self._gates_unchanged, direct)
            return
        self._composite_paint(gc, ox, oy, scale, defer)

    def sync_back(self):
        SystemCanvas.sync_back(self)
        # The frame's gate snapshot (rotated compositors): rect/rectb/print/
        # pix are the native gates; a play frame that draws nothing but the
        # game and the (ungated) bar strip leaves them untouched -- measured
        # on the Guition P4, 34 frames, not one count moved.
        if getattr(self._comp, "rotated", False):
            st = self._gate_state
            if st is not None:
                self._q_fill = st[_ST_N_FILL]
                self._q_text = st[_ST_N_TEXT]
            self._q_clears = self._clears

    def _gates_unchanged(self):
        st = self._gate_state
        if st is None:
            return False              # no gates installed: never claim quiet
        return (st[_ST_N_FILL] == self._q_fill
                and st[_ST_N_TEXT] == self._q_text
                and self._clears == self._q_clears)

    def _composite_paint(self, gc, ox, oy, scale, defer=False):
        """The composite into THIS canvas's buffer: PPA (crisp or bilinear)
        with the CPU kernel as the fallback. `defer` is the Waveshare's async
        kick (never on a rotated compositor)."""
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

    # -- a compiled cart's frame, from its own memory -------------------------
    #
    # A compiled cart's blit565 frame is little-endian RGB565, this panel's
    # own order, so the PPA scales it to the glass from where the cart made
    # it, in its linear memory, and nothing writes it into the game canvas.
    # Beside the scale the GDMA copies it into the run's scratch
    # (moy_ppa.snap): the copy shown again when the cart does not replace the
    # frame, and written into the canvas (CartFrame.settle) when something
    # needs it there, by which time the cart's next hook may have changed its
    # memory. sync_back fences that copy before the hook (`_snap_live`, as on
    # the S3 boards), and the next frame's copy first waits out every PPA op
    # still reading the last one. A palette frame stays the blit's: ESP-IDF
    # disables the PPA's palette mode.
    #
    # The opaque rects the console paints over a frame on the game canvas --
    # the FPS chip, the perf HUD line, declared by their painter (`cf.rects`)
    # -- are scaled over the frame by ops of their own: from the game canvas
    # itself where the frame's ops are fenced before the cart's next hook (the
    # Waveshare), and where they may outlive it (the rotated compositor) from
    # a PATCH PICTURE at the scratch's tail, the canvas's width and
    # PATCH_ROWS tall, the rects copied in packed down it. Each patch is six
    # numbers in `_fr_patches`: its source block and where its scaled image
    # lands.

    PATCH_ROWS = 32

    @property
    def presents_frames(self):
        return getattr(self._ppa, "snap", None) is not None

    presents_palette_frames = False

    def present_frame(self, cf, view, gc, ox, oy, scale, src=None,
                      defer=False):
        """Show a compiled cart's blit565 frame where blit_game would have
        composited the game canvas: `view` is the frame the CartFrame `cf`
        owes or keeps (the game canvas `gc`'s size), `src` the cart's view
        rect, `defer` blit_game's -- the composite may be the frame's last
        write. The Waveshare scales `view` itself: its deferred composite
        is a "game" show, fenced before the cart's next hook as the canvas's
        is. The rotated compositor's quiet-frame op outlives that fence, so
        it scales the snapshot. CRISP PIXELS does not apply: the bilinear
        scale is the frame's either way. False when it cannot take the
        frame -- a palette frame, a geometry the PPA cannot place, more patch
        rows than the picture holds, no scratch: the caller settles it into
        the canvas and composites as ever."""
        ppa = self._ppa
        gw = gc.w
        gh = gc.h
        n = 2 * gw * gh
        if ppa is None or len(view) != n:
            return False
        if src is not None:
            sx, sy, vw, vh = src
        else:
            sx = 0
            sy = 0
            vw = gw
            vh = gh
        ox = int(ox)
        oy = int(oy)
        scale = int(scale)
        if (ox < 0 or oy < 0 or ox + vw * scale > self.w
                or oy + vh * scale > self.h):
            return False
        base = (n + 191) & ~63            # past any span the snapshot takes
        pic_n = 2 * gw * self.PATCH_ROWS
        scr = cf.scratch(base + pic_n)
        if scr is None:
            return False
        if scr is not self._pic_for:
            self._pic_for = scr
            self._pic = memoryview(scr)[base:base + pic_n]
        comp = self._comp
        rotated = getattr(comp, "rotated", False)
        ppa.wait(0)                       # nothing reads the last copy now
        npatch = self._patch(cf, gc, sx, sy, vw, vh, ox, oy, scale, rotated)
        if npatch < 0:
            return False
        off = ppa.snap(scr, view)
        self._snap_live = True
        try:
            if rotated:
                self._fr = (cf.kept_view(scr, off, n), gw, gh, sx, sy, vw, vh,
                            ox, oy, scale, npatch)
                comp.mark_game(self._fr[0], gw, gh, ox, oy, scale,
                               self._paint_frame_fn, self._gates_unchanged,
                               True, frame=(sx, sy, vw, vh, self._pic,
                                            self.PATCH_ROWS,
                                            self._fr_patches, npatch))
            else:
                blit = ppa.blit_async if defer else ppa.blit_scale
                blit(self._buf, self.w, self.h, ox, oy, view, gw, gh, scale,
                     sx, sy, vw, vh)
                self._blit_patches(blit, gc._buf, gw, gh, scale, npatch)
                if defer:
                    comp._composite_pending = True
        except (OSError, ValueError) as exc:
            print("Moybyte P4 frame composite refused -> canvas:", exc)
            ppa.sync()
            return False
        cf.presented(scr, off)
        return True

    def _patch(self, cf, gc, sx, sy, vw, vh, ox, oy, scale, copy):
        """Record each rect the console painted over the frame, clipped to
        the cart's view: its block and where its scaled image lands. With
        `copy` the rects are copied out of the game canvas into the patch
        picture first and the blocks are the picture's; without, they are
        the canvas's own. Returns how many, or -1 when they need more rows
        than the picture has."""
        n = cf.nrects
        if not n:
            return 0
        r = cf.rects
        pr = self._fr_patches
        vx1 = sx + vw
        vy1 = sy + vh
        rows = self.PATCH_ROWS
        row = 0
        k = 0
        for i in range(n):
            j = 4 * i
            x0 = r[j]
            y0 = r[j + 1]
            x1 = x0 + r[j + 2]
            y1 = y0 + r[j + 3]
            if x0 < sx:
                x0 = sx
            if y0 < sy:
                y0 = sy
            if x1 > vx1:
                x1 = vx1
            if y1 > vy1:
                y1 = vy1
            if x1 <= x0 or y1 <= y0:
                continue
            h = y1 - y0
            by = y0
            if copy:
                if row + h > rows or self._gfx is None:
                    return -1
                self._gfx.blit565(self._pic, gc.w, rows, 0, row - y0, gc._buf,
                                  gc.w, gc.h, -1, x0, row, x1, row + h)
                by = row
                row += h
            j = 6 * k
            pr[j] = x0
            pr[j + 1] = by
            pr[j + 2] = x1 - x0
            pr[j + 3] = h
            pr[j + 4] = ox + (x0 - sx) * scale
            pr[j + 5] = oy + (y0 - sy) * scale
            k += 1
        return k

    def _blit_patches(self, blit, src, sw, sh, scale, npatch):
        pr = self._fr_patches
        for k in range(npatch):
            j = 6 * k
            blit(self._buf, self.w, self.h, pr[j + 4], pr[j + 5], src, sw, sh,
                 scale, pr[j], pr[j + 1], pr[j + 2], pr[j + 3])

    def _paint_frame(self):
        """The rotated compositor's paint route for a frame present_frame
        registered: its view and patches scaled into the paint buffer from
        the snapshot, blocking."""
        kept, gw, gh, sx, sy, vw, vh, ox, oy, scale, npatch = self._fr
        ppa = self._ppa
        ppa.snap_wait()
        ppa.blit_scale(self._buf, self.w, self.h, ox, oy, kept, gw, gh, scale,
                       sx, sy, vw, vh)
        self._blit_patches(ppa.blit_scale, self._pic, gw, self.PATCH_ROWS,
                           scale, npatch)

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
        comp = self._comp
        if (ppa is None or x < 0 or y < 0
                or x + layer.w > self.w or y + layer.h > self.h
                or (getattr(comp, "rotated", False)
                    and not getattr(comp, "_async", False))):
            return False              # no queue to ride: the sync CPU stamp
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


_room(P4SystemCanvas)


def run_ppa_smoke(comp, set_backlight, scale=2, iters=60, game_w=320, game_h=240):
    """A/B the P4 hardware PPA vs the CPU moy_gfx blit for the game->window
    composite (#58 perf). Ctrl-C the desktop to the REPL first, then each
    board's `moy_runtime.run_ppa_smoke()` (which hands in its compositor and
    backlight -- the only two things the smoke does not own).

    Draws a game-sized test pattern (colored quadrants + label), then times
    `iters` integer-upscale composites into the DSI framebuffer TWO ways --
    moy_gfx.blit565_scale (CPU) and moy_ppa.blit_scale (PPA DMA) -- showing each
    result so correctness (colors/scale/position) is eyeballable over serial +
    glass, and printing per-blit timings + the speedup. The composite is the
    exact op wm_windowed._blit_game runs every game frame, so the speedup here is
    the headline lever for both game play and window drags.

    moy_dsi.init() is idempotent, so a fresh compositor reuses the live panel
    the interrupted desktop left up (no re-init, no reflash)."""
    gfx = comp.gfx()
    W, H = comp.size()
    sw, sh = game_w, game_h
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
