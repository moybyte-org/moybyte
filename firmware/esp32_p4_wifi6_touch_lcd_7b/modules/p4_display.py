"""P4 display glue (#58): the compositor shim over moy_dsi + the backlight.

The EK79007 panel runs MIPI-DSI DPI mode: the DSI peripheral CONTINUOUSLY scans
a PSRAM framebuffer, so there is no per-frame flush transfer at all (the
T-Deck's ~28ms tx_color ceiling is structurally gone). `P4Compositor` adapts
that to the compositor interface DeviceCanvas + the shared Workstation expect
(size/framebuffer/back_buffer/gfx/flush/sync).

MULTI-BUFFERED (double 2026-07-08, TRIPLE 2026-07-27 -- the #58 render
overlap): drawing straight into the scanned framebuffer made every repaint
visibly flash (the scan-out raced the painter), so draws land in a BACK
buffer and flush() switches scan-out to it zero-copy (moy_dsi.show -- the DPI
driver recognizes its own fb pointer, no pixel copy). With THREE buffers a
deferred async PPA op (the drag stamp / game composite) never blocks the next
paint: one buffer scans, one holds the in-flight DMA, one takes the paint --
the ~15ms moy_ppa.sync fence leaves the per-frame path for "stamp" pendings
(a "game" pending still fences before the cart tick: the composite reads the
game canvas the tick would overwrite). DeviceCanvas.sync_back() re-points the
draw target each frame (the #40 machinery). This requires every DRAWN frame
to fully repaint -- which the console's draw stack does; the stale-by-N
partial machinery (wm_windowed/launcher_layer/_retained_n, RETAINED_FRAMES=3
on the root) carries the horizon. Degrades to the 2-buffer then
single-buffer paths on older moy_dsi builds.
"""

BACKLIGHT_GPIO = 32     # active-LOW (board fact, hardware-confirmed)

_bl_pin = None


def set_backlight(on):
    global _bl_pin
    from machine import Pin
    if _bl_pin is None:
        _bl_pin = Pin(BACKLIGHT_GPIO, Pin.OUT, value=0 if on else 1)
    else:
        _bl_pin.value(0 if on else 1)


class P4Compositor:
    def __init__(self):
        import moy_dsi
        # Dark until the first composed frame (#45): the fresh DPI framebuffers
        # are uninitialized PSRAM -- scan-out garbage the user must never see lit.
        set_backlight(False)
        moy_dsi.init()
        self._dsi = moy_dsi
        self._w = moy_dsi.WIDTH
        self._h = moy_dsi.HEIGHT
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None
        try:
            n = moy_dsi.nfbs()
        except AttributeError:
            n = 1               # older moy_dsi build: single-buffer degrade
        self._fbs = [moy_dsi.fb(i) for i in range(n)] if n > 1 else [moy_dsi.fb()]
        # First paint: black out the garbage in BOTH buffers so neither swap can
        # ever reveal it, then present a clean field before the backlight lights.
        if self._gfx is not None:
            for f in self._fbs:
                self._gfx.fill(f, self._w * self._h, 0)
        if n > 1:
            moy_dsi.show(0)     # scan 0; draw into 1
            self._back = 1
        else:
            moy_dsi.flush()
            self._back = 0
        # Deferred present (#58 composite-overlap): a quiet game frame kicks the
        # game->window composite on the PPA async and DEFERS its show one step, so
        # the DMA overlaps the next frame's input poll. _pending holds the fb index
        # of a composited-but-not-yet-shown frame; _composite_pending is set by
        # blit_game for the current frame.
        self._pending = None
        self._composite_pending = False
        # Triple-buffer rotation (#58 render overlap, n >= 3): deferred frames
        # queue here as (fb_index, kind) -- kind "stamp" (the drag content
        # stamp; its SOURCE win.buf is frozen for the gesture, so the show can
        # wait non-blocking on moy_ppa.done()) or "game" (the composite READS
        # the game canvas, which the cart's NEXT _draw overwrites -- the fence
        # must stay blocking until the double game canvas retires it).
        # _busy3 tracks fb indices with in-flight DMA separately: a full
        # opaque paint OBSOLETES a queued show (never flash an older frame
        # after a newer one), but its buffer stays unpaintable until a fence.
        self._pend3 = []
        self._busy3 = []
        # Deferred drag stamp (#58 stamp-defer): the WM registers the dragged
        # window's content stamp here (P4SystemCanvas.blit_strip_async) instead
        # of drawing it mid-stack; flush() kicks it on the PPA as the frame's
        # TRUE last write -- after the bar/chips/cursor -- so no CPU draw can
        # race the DMA (the mid-stack kick left cache-eviction droppings on the
        # desktop, glass-confirmed). Tuple: (dst, dw, dh, x, y, src, sw, sh).
        self._stamp_pending = None

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._fbs[self._back]

    def back_buffer(self):
        return self._fbs[self._back]

    def gfx(self):
        return self._gfx

    def flush(self):
        stamp_kicked = False
        if self._stamp_pending is not None:
            # Kick the registered drag stamp NOW -- every layer (incl. cursor)
            # has drawn, so the DMA can't race any CPU write. Falls back to the
            # deferred-present machinery below exactly like the game composite.
            args = self._stamp_pending
            self._stamp_pending = None
            try:
                import moy_ppa
                moy_ppa.blit_async(args[0], args[1], args[2], args[3], args[4],
                                   args[5], args[6], args[7], 1)
                self._composite_pending = True
                stamp_kicked = True
            except Exception as exc:  # noqa: BLE001 -- refusal -> draw it sync
                print("Moybyte P4 stamp kick failed -> CPU:", exc)
                try:
                    import moy_gfx
                    moy_gfx.blit565(args[0], args[1], args[2], args[3], args[4],
                                    args[5], args[6], args[7], -1)
                except Exception:  # noqa: BLE001 -- worst case: one stale frame
                    pass
        if len(self._fbs) <= 1:
            self._dsi.flush()            # single-buffer: CPU-cache msync only
            return
        n = len(self._fbs)
        if n >= 3:
            # Triple-buffer rotation (#58 render overlap): the back buffer
            # ALWAYS advances at flush, because the next paint target is
            # neither the scanned buffer nor the DMA-pending one -- the paint
            # never waits on the fence. Deferred frames queue; present_pending
            # shows them when their DMA lands.
            if self._composite_pending:
                self._pend3.append((self._back,
                                    "stamp" if stamp_kicked else "game"))
                self._busy3.append(self._back)
                self._composite_pending = False
            else:
                if self._pend3:
                    # This full opaque frame REPLACES the queued deferred
                    # ones: drop their shows (an older frame must never flash
                    # after a newer). Their DMA may still fly -- _busy3 keeps
                    # the reuse fence armed.
                    self._pend3 = []
                self._dsi.show(self._back)   # msync + zero-copy switch
            self._back = (self._back + 1) % n
            # The next paint target still has DMA in flight (two deferred
            # frames without a present between them, or a dropped obsolete
            # op): one blocking fence before handing the buffer out. Rare --
            # a present runs every loop.
            if self._back in self._busy3:
                self._drain_pending()
            return
        if self._composite_pending:
            # A quiet game frame kicked the composite async: hold the show for the
            # NEXT present_pending (after the following input poll), so the DMA
            # runs concurrently. The buffer stays _back until then (nothing else
            # draws it -- blit_game was this frame's last framebuffer op).
            self._pending = self._back
            self._composite_pending = False
            return
        self._dsi.show(self._back)       # msync + zero-copy scan-out switch
        self._back ^= 1                  # next frame draws the other buffer

    def _drain_pending(self):
        """Fence every in-flight PPA op, then show the queued frames in order
        (the last show wins the next VSYNC; earlier ones were sequential)."""
        import moy_ppa
        moy_ppa.sync()
        self._busy3 = []
        while self._pend3:
            self._dsi.show(self._pend3.pop(0)[0])

    def present_pending(self):
        """Show a deferred (async-composited) frame. n >= 3: non-blocking for a
        "stamp" pending (its source is frozen; if the DMA is still flying the
        show just waits for the next loop -- painting continues into a third
        buffer meanwhile), blocking for a "game" pending (the cart's next _draw
        overwrites the composite's SOURCE canvas, so the fence must land before
        the tick -- the double game canvas will retire this). n == 2: wait for
        the PPA DMA, then switch scan-out and free the other buffer -- called
        by the desktop loop AFTER the input poll, so the poll overlapped the
        DMA. No-op when nothing was deferred."""
        if len(self._fbs) >= 3:
            if not self._pend3:
                return
            import moy_ppa
            if self._pend3[0][1] == "game":
                self._drain_pending()
                return
            done = getattr(moy_ppa, "done", None)
            if done is None or done():
                self._drain_pending()    # sync is ~free once done() is True
            return
        if self._pending is None:
            return
        import moy_ppa
        moy_ppa.sync()                   # fence the async composite
        self._dsi.show(self._pending)
        self._back ^= 1                  # the other buffer is now free to draw
        self._pending = None

    def sync(self):
        pass                             # no in-flight DMA to drain

    def underruns(self):
        try:
            return self._dsi.underruns()
        except Exception:
            return None
