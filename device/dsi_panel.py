"""The ESP32-P4 DSI compositor (the P4 silicon tier's Python half): the
compositor shim over the shared `native/p4/moy_dsi`.

Born as the Waveshare 7B's `p4_display.py` (#58) and promoted here on
2026-09-06, the day the Guition JC8012P4A1C became its second consumer -- Phase
C of docs/board_ports_2026-08.md: a driver moves to `device/` the day a second
board carries the hardware, parameterized by what differs. What differs is ONE
thing, the backlight (GPIO32 active-low on the Waveshare, GPIO23 active-high on
the Guition), so it is a constructor argument and each board's display module
keeps its own eight-line `set_backlight` beside a two-line subclass.

The panel runs MIPI-DSI DPI mode on both boards: the DSI peripheral
CONTINUOUSLY scans a PSRAM framebuffer, so there is no per-frame flush transfer
at all (the T-Deck's ~28ms tx_color ceiling is structurally gone). `P4Compositor`
adapts that to the compositor interface DeviceCanvas + the shared Workstation
expect (size/framebuffer/back_buffer/gfx/flush/sync).

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

try:
    from ticks import _ticks_us, _ticks_diff
except ImportError:  # pragma: no cover - host package lane
    from runtime.ticks import _ticks_us, _ticks_diff


class P4Compositor:
    def __init__(self, set_backlight=None):
        import moy_dsi
        # Dark until the first composed frame (#45): the fresh DPI framebuffers
        # are uninitialized PSRAM -- scan-out garbage the user must never see lit.
        # The backlight is the BOARD's (its GPIO and polarity differ between the
        # two P4 boards), so the board's display module hands its function in.
        if set_backlight is not None:
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
        # The overlap's meters -- see overlap_stats() for what each one says.
        self._deferred = 0
        self._obsolete = 0
        self._fences = 0
        self._fence_us = 0
        self._game_n = 0
        self._game_us = 0
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
        game_pending = self._composite_pending
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
                # A frame carrying BOTH is a "game": the stamp kick sets the
                # same flag, and the weaker done()-gated fence would let the
                # cart's next _draw overwrite the composite's source canvas.
                self._pend3.append(
                    (self._back,
                     "stamp" if stamp_kicked and not game_pending else "game"))
                self._busy3.append(self._back)
                self._composite_pending = False
                self._deferred += 1
            else:
                if self._pend3:
                    # This full opaque frame REPLACES the queued deferred
                    # ones: drop their shows (an older frame must never flash
                    # after a newer). Their DMA may still fly -- _busy3 keeps
                    # the reuse fence armed.
                    self._obsolete += len(self._pend3)
                    self._pend3 = []
                self._dsi.show(self._back)   # msync + zero-copy switch
            self._back = (self._back + 1) % n
            # The next paint target still has DMA in flight (two deferred
            # frames without a present between them, or a dropped obsolete
            # op): one blocking fence before handing the buffer out. Rare --
            # a present runs every loop.
            if self._back in self._busy3:
                t0 = _ticks_us()
                self._drain_pending()
                self._fences += 1
                self._fence_us += _ticks_diff(_ticks_us(), t0)
            return
        if self._composite_pending:
            # A quiet game frame kicked the composite async: hold the show for the
            # NEXT present_pending (after the following input poll), so the DMA
            # runs concurrently. The buffer stays _back until then (nothing else
            # draws it -- blit_game was this frame's last framebuffer op).
            self._pending = self._back
            self._composite_pending = False
            self._deferred += 1
            return
        self._dsi.show(self._back)       # msync + zero-copy scan-out switch
        self._back ^= 1                  # next frame draws the other buffer

    def _queued_game(self):
        """Whether ANY queued show reads the game canvas -- not just the head:
        a "stamp" head sits in front of a "game" whenever a slow DMA held a
        present back for a loop."""
        for _fb, kind in self._pend3:
            if kind == "game":
                return True
        return False

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
        "stamp"-only queue (the source is frozen; if the DMA is still flying
        the show just waits for the next loop -- painting continues into a
        third buffer meanwhile), blocking once any queued show is a "game" (the
        cart's next _draw overwrites the composite's SOURCE canvas, so the
        fence must land before the tick). n == 2: wait for
        the PPA DMA, then switch scan-out and free the other buffer -- called
        by the desktop loop AFTER the input poll, so the poll overlapped the
        DMA. No-op when nothing was deferred."""
        if len(self._fbs) >= 3:
            if not self._pend3:
                return
            import moy_ppa
            if self._queued_game():
                t0 = _ticks_us()
                self._drain_pending()
                self._game_n += 1
                self._game_us += _ticks_diff(_ticks_us(), t0)
                return
            done = getattr(moy_ppa, "done", None)
            if done is None or done():
                self._drain_pending()    # sync is ~free once done() is True
            return
        if self._pending is None:
            return
        import moy_ppa
        t0 = _ticks_us()
        moy_ppa.sync()                   # fence the async composite
        self._game_n += 1
        self._game_us += _ticks_diff(_ticks_us(), t0)
        self._dsi.show(self._pending)
        self._back ^= 1                  # the other buffer is now free to draw
        self._pending = None

    def sync(self):
        """Leave NO DMA in flight and no deferred show unpresented.

        The panel SCANS, so there is no transfer to drain -- but a deferred
        composite or drag stamp leaves a PPA op flying and its frame queued.

        Both arms are needed: `_busy3` non-empty with `_pend3` empty is the
        obsolete-drop state, and the 2-buffer lane tracks its pending in
        `_pending`, which a `_busy3`-only fence would miss.
        """
        if self._busy3:
            self._drain_pending()
        elif self._pending is not None:
            self.present_pending()

    def overlap_stats(self):
        """The async-overlap meters, cumulative since boot:

            (deferred, obsolete, fences, fence_us, game_n, game_us, timeouts)

        deferred  composites kicked async whose scan-out switch was held one
                  loop -- the denominator for the rest.
        obsolete  queued shows a full opaque paint replaced: composited, unseen.
        fences    blocking reuse fences in flush(), and fence_us their cost.
                  One per deferred frame means the third framebuffer buys
                  nothing.
        game_n    the blocking "game" fence in present_pending(), and game_us
                  its cost. It runs inside FrameLoop's UNTIMED present() hook,
                  so this is the only place it is visible.
        timeouts  moy_ppa fences that gave up. Must stay 0, and a fence cannot
                  RAISE (it runs where a throw would take the desktop down), so
                  this is the only sign of a wedge.
        """
        try:
            import moy_ppa
            timeouts = moy_ppa.stats()[2]
        except Exception:  # noqa: BLE001 -- no PPA (or an older build)
            timeouts = 0
        return (self._deferred, self._obsolete, self._fences, self._fence_us,
                self._game_n, self._game_us, timeouts)

    def underruns(self):
        try:
            return self._dsi.underruns()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# A LANDSCAPE console on a PORTRAIT DSI panel (the Guition JC8012P4A1C, owner
# call 2026-09-06: "we want it landscape").
#
# The DSI peripheral scans a portrait framebuffer (800 wide, 1280 tall) from
# PSRAM continuously; there is no per-frame flush to fold a rotation into. So
# the console paints a persistent LANDSCAPE buffer (1280x800, `framebuffer()`)
# and flush() ROTATES it into a portrait scan buffer on the PPA, then switches
# scan-out to that buffer. Two costs, and the design is about paying the small
# one as often as possible:
#
#   * a FULL frame -- anything the WM painted that it did not describe -- is a
#     whole-buffer rotate, 2MB in and 2MB out over the same PSRAM the DSI is
#     reading at ~123MB/s. Tens of milliseconds. Every painted chrome frame
#     pays it; an idle desk paints nothing and pays nothing.
#   * a QUIET game frame -- the game composite was the frame's only write
#     (the canvas's draw gates did not move; both the windowed WM's quiet
#     stack and a fullscreen play frame look like this) -- is ONE PPA op: the
#     game canvas scaled AND rotated straight into the scan buffer, plus the
#     top bar's strip rotated from the paint buffer. A few ms, not tens.
#   * a DAMAGE frame -- the WM painted, and said WHAT it painted
#     (`note_damage`: a drag's gesture union, the window a keystroke or a
#     scroll re-rendered) -- rotates those landscape rects from the paint
#     buffer, plus the bar strip and the game rect if a game drew. The paint
#     buffer is ONE persistent picture, so "what this frame changed" is
#     exactly what the WM wrote, and the WM already reasons about that set
#     for its own backdrop restore. A frame the WM cannot describe -- a
#     window opening, a theme change, a toast -- notes nothing and pays the
#     full rotate; a description that would cost more than the full rotate
#     is declined for one.
#
# The catch is ping-pong: a rect-only frame lands in a scan buffer that was
# last shown two frames ago and may lack what the frame between painted. So
# every scan buffer carries a STALE list -- the portrait rects it has missed
# since it was last fully current -- and before a rect frame is rotated into a
# buffer, the stale rects it does not cover are copied 1:1 from the buffer on
# glass (angle 0). For a game at a fixed rect that list is the same rect every
# frame, already covered, and costs nothing. A buffer whose stale list has
# grown past a handful, or that missed a full frame, is brought current by a
# full rotate instead: correctness by construction, and the bound on the
# bookkeeping. Two scan buffers, not three: the third bought the Waveshare an
# async-overlap lever this path does not use (every rotate is blocking), and a
# third buffer to keep current would be a third full rotate after every change.
#
# EVERY FRAME IS ASYNC (2026-09-08), and the paint buffer PING-PONGS to make
# it so. A rotate is PPA time the CPU used to spend waiting -- ~11ms for a
# game frame, ~17ms for a drag's rect frame, ~48ms for a full one. Instead a
# frame's ops are QUEUED and the show waits for the next present, and the
# CPU paints the NEXT frame into the OTHER paint buffer meanwhile. Two paint
# buffers, so RETAINED_FRAMES on the root is 2 -- exactly the horizon the WM
# already runs at (its `_retained_n` floors to 2), so it paints no more than
# it did with one. The present fences with wait(keep): everything older than
# the last flush's ops has landed, so the buffer about to be painted has no
# reader left. The quiet game frame keeps its tighter fence (keep=1): its
# copy of the game canvas into the scratch must land before the cart's tick
# writes the canvas, and the strip rotate before the WM re-stamps the bar.
# A flush that finds its predecessor still flying fences and shows it first.
#
# THE DRAG STAMP rides the same queue. The WM's stamp-defer
# (P4SystemCanvas.blit_strip_async) hands the moving window's content copy
# to the compositor instead of doing it on the CPU -- 17ms of a 32ms drag
# frame through the write-allocate cache -- and flush() queues it as the
# frame's FIRST op, into the paint buffer, so the rect rotates behind it
# read the stamped pixels (the SRM engine completes in submit order: one
# tail-inserted list per engine, one transaction on the 2D-DMA at a time).
# ---------------------------------------------------------------------------


def rotate_rect(x, y, w, h, angle, lw, lh):
    """The portrait (x, y, w, h) a landscape rect lands on after a
    counter-clockwise rotation of the lw x lh landscape picture by `angle`
    (90 or 270 -- the PPA's convention). Pure arithmetic, pinned by
    tests/test_p4_display.py."""
    if angle == 90:
        # landscape (x, y) -> portrait (y, lw - 1 - x)
        return (y, lw - x - w, h, w)
    if angle == 270:
        # landscape (x, y) -> portrait (lh - 1 - y, x)
        return (lh - y - h, x, h, w)
    raise ValueError("angle 90 or 270")


def _covered(r, rects):
    """Whether rect r lies entirely inside one of `rects`."""
    x, y, w, h = r
    for (qx, qy, qw, qh) in rects:
        if qx <= x and qy <= y and x + w <= qx + qw and y + h <= qy + qh:
            return True
    return False


def unrotate_rect(px, py, pw, ph, angle, lw, lh):
    """The landscape rect a portrait (px, py, pw, ph) came from: rotate_rect's
    inverse, pinned by the same test."""
    if angle == 90:
        return (lw - py - ph, px, ph, pw)
    if angle == 270:
        return (py, lh - px - pw, ph, pw)
    raise ValueError("angle 90 or 270")


def _bbox(a, b):
    x0 = min(a[0], b[0])
    y0 = min(a[1], b[1])
    x1 = max(a[0] + a[2], b[0] + b[2])
    y1 = max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


class RotatedCompositor:
    """The landscape compositor over a portrait moy_dsi panel -- see the block
    comment above. The compositor interface (size/framebuffer/back_buffer/
    gfx/flush/sync + the P4 extras the canvas and the PERF sampler read)."""

    STALE_LIMIT = 6         # more distinct stale rects than this -> full rotate

    def __init__(self, set_backlight=None, angle=90):
        import moy_dsi
        if set_backlight is not None:
            set_backlight(False)
        moy_dsi.init()
        self._dsi = moy_dsi
        self._pw = moy_dsi.WIDTH          # the panel's scan geometry (portrait)
        self._ph = moy_dsi.HEIGHT
        self._w = self._ph                # the console's (landscape)
        self._h = self._pw
        self.angle = angle
        try:
            import moy_gfx
            self._gfx = moy_gfx
        except ImportError:
            self._gfx = None
        import moy_ppa
        if not moy_ppa.init():
            raise OSError("moy_ppa init failed: a portrait panel needs the rotate")
        self._ppa = moy_ppa
        # Two scan buffers of the panel's (the third exists; unused here).
        self._fbs = [moy_dsi.fb(0), moy_dsi.fb(1)]
        # Two paint buffers, ping-ponged at flush: the PPA reads one while
        # the console paints the other (see the block comment).
        self._paints = [self._alloc(self._w * self._h * 2),
                        self._alloc(self._w * self._h * 2)]
        self._pi = 0
        if self._gfx is not None:
            for f in self._fbs:
                self._gfx.fill(f, self._pw * self._ph, 0)
            for f in self._paints:
                self._gfx.fill(f, self._w * self._h, 0)
        moy_dsi.show(0)
        self._front = 0
        self._back = 1
        # None = "missed a full frame": the next frame into it is a full rotate.
        self._stale = [[], None]
        # This frame's game composite, registered by the canvas (mark_game),
        # or None: decided at flush -- a quiet frame goes straight to the scan
        # buffer as one scale+rotate, anything else composites into the paint
        # buffer and rotates the whole frame.
        self._game = None
        # The chrome strip a quiet frame carries besides the game rect: the
        # top bar, stamped by an UNGATED blit every play frame (so the gates
        # cannot see it change). Landscape rows; run_desktop sets the height
        # from the bar once the console exists. 0 = none.
        self.strip_h = 18
        # This frame's damage, in landscape rects, as the WM described it
        # (note_damage); None = nothing described. Consumed at flush.
        self._damage = None
        # Meters (overlap_stats keeps the PERF line's 7-slot ppa= shape; a
        # damage frame counts as a rect frame there and separately below).
        self._full_n = 0
        self._full_us = 0
        self._rect_n = 0
        self._rect_us = 0
        self._copies = 0
        self._grown = 0               # stale rects swallowed by a grown rect
        self._dmg_n = 0
        self._dmg_rects = 0
        self._dmg_declined = 0
        # The async quiet frame: a moy_ppa that can fence a queue tail
        # (`wait`) runs it; an older one keeps every rotate blocking.
        self._async = hasattr(moy_ppa, "wait")
        # The SRAM-bounce rotate (see _rotate); None once the bands failed.
        self._bounce = getattr(moy_ppa, "rotate_bounce", None)
        self._bounced = 0             # bounce transactions submitted
        self._scratch = None          # the game canvas's copy the rotate reads
        self._scratch_n = 0
        self._pending = None          # the scan buffer whose show is deferred
        self._keep = 0                # ops the present may leave in flight
        self._stamp_n = 0             # drag stamps the PPA performed
        self._refused = 0             # submits the full queue refused (retried blocking)
        self._def_n = 0               # deferred frames
        self._pres_n = 0              # ...shown at a present
        self._late_n = 0              # ...shown by the following flush
        self._wait_us = 0             # time the present spent fencing
        # The WM's deferred window stamp (P4SystemCanvas.blit_strip_async):
        # (dst, dw, dh, x, y, src, sw, sh), the frame's first queued op.
        self._stamp_pending = None
        self._composite_pending = False   # the Waveshare's flag; inert here
        self.retained_frames = 2
        self.rotated = True

    @staticmethod
    def _alloc(nbytes):
        try:
            import moy_alloc
            buf = moy_alloc.malloc_dma(
                nbytes, moy_alloc.MEMORY_SPIRAM | moy_alloc.MEMORY_DMA)
            if buf is not None:
                return buf
        except Exception:  # noqa: BLE001 -- host / no allocator
            pass
        return bytearray(nbytes)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._paints[self._pi]

    def back_buffer(self):
        return self._paints[self._pi]

    def gfx(self):
        return self._gfx

    def set_angle(self, angle):
        """Flip the desk the other way up, live: the next frame is a full
        rotate into every buffer."""
        rotate_rect(0, 0, 1, 1, angle, self._w, self._h)   # validates
        self.angle = angle
        self._stale = [None, None]

    def mark_game(self, src, sw, sh, ox, oy, scale, paint, quiet, direct):
        """The canvas's word about THIS frame's game composite (one per frame):
        `src` the game canvas's RGB565 buffer (sw x sh) to land at landscape
        (ox, oy) scaled by `scale`; `paint()` composites it into the paint
        buffer the ordinary way (the full-frame path, and crisp mode);
        `quiet()` answers at flush time whether anything ELSE drew this
        frame (the canvas's draw gates); `direct` allows the one-op
        scale+rotate straight into the scan buffer (bilinear -- crisp mode
        says no and takes the paint route)."""
        self._game = (src, int(sw), int(sh), int(ox), int(oy), int(scale),
                      paint, quiet, bool(direct))

    def note_damage(self, x, y, w, h):
        """The WM's word that THIS frame changed the paint buffer inside this
        landscape rect (and, over the frame, only inside the noted rects plus
        the bar strip and the game rect). Clipped to the frame; an empty rect
        is nothing. A frame that notes nothing rotates whole."""
        x0 = max(0, int(x))
        y0 = max(0, int(y))
        x1 = min(self._w, int(x) + int(w))
        y1 = min(self._h, int(y) + int(h))
        if x1 <= x0 or y1 <= y0:
            return
        if self._damage is None:
            self._damage = []
        self._damage.append((x0, y0, x1 - x0, y1 - y0))

    # More distinct rects than this in one frame -> one bounding box (each
    # rotate op carries a fixed cost: the driver writes back and invalidates
    # the whole out picture per submit).
    DAMAGE_RECTS = 3
    # A description whose area exceeds this share of the frame is declined
    # for the full rotate (same bytes moved, one op instead of several).
    DAMAGE_SHARE = 0.6

    def _damage_rects(self, rects):
        """Coalesce noted rects: drop rects covered by another, and above
        DAMAGE_RECTS distinct ones take their bounding box. Returns None when
        rotating the description would not beat the full rotate."""
        out = []
        for r in rects:
            if _covered(r, out):
                continue
            out = [q for q in out if not _covered(q, [r])]
            out.append(r)
        if len(out) > self.DAMAGE_RECTS:
            x0 = min(r[0] for r in out)
            y0 = min(r[1] for r in out)
            x1 = max(r[0] + r[2] for r in out)
            y1 = max(r[1] + r[3] for r in out)
            out = [(x0, y0, x1 - x0, y1 - y0)]
        area = 0
        for (_x, _y, w, h) in out:
            area += w * h
        if area > self._w * self._h * self.DAMAGE_SHARE:
            return None
        return out

    def damage_stats(self):
        """(damage frames, rects rotated on them, descriptions declined,
        stale rects swallowed by growing a rect instead of copying)."""
        return (self._dmg_n, self._dmg_rects, self._dmg_declined, self._grown)

    def _strip(self):
        h = self.strip_h
        if h <= 0:
            return None
        return (0, 0, self._w, min(h, self._h))

    # WHO WRITES THE DESTINATION decides `wb`, the op's cache writeback.
    #
    # moy_ppa writes the destination rows' CPU cache back before every submit,
    # because the driver invalidates exactly those rows and an invalidate
    # DISCARDS a dirty line. That walk is the op's fixed cost, and it buys
    # nothing for a destination the CPU never writes -- which is every
    # destination on this path but one:
    #
    #   scan buffers  the CPU fills them ONCE at init, and moy_dsi.show()
    #                 msyncs the whole buffer at every present (including the
    #                 init one), so no dirty line survives to a rotate.
    #                 Afterwards only the PPA writes them.
    #   scratch       the game canvas's copy, written by the PPA alone. The
    #                 rotate covers the whole buffer, and the driver's
    #                 invalidate drops whatever a previous owner of that
    #                 memory left dirty, so nothing can evict over the pixels.
    #   paint buffer  CPU-PAINTED -- the drag stamp lands beside this frame's
    #                 chrome. It keeps the writeback, or the chrome is
    #                 discarded (the 2026-07-10 desktop trails).
    def _rot(self, nb, wb, *args):
        """moy_ppa.rotate, queued when `nb`, writing the destination rows back
        when `wb` (see above). A full submit queue is a refused submit (the
        driver does not wait): fence everything and resubmit blocking, and
        count it -- a drag that overruns the queue is slower for a frame,
        never wrong."""
        try:
            self._ppa.rotate(*(args + (nb, wb)))
        except OSError:
            if not nb:
                raise
            self._ppa.sync()
            self._refused += 1
            self._ppa.rotate(*(args + (False, wb)))

    def _rot_scale(self, nb, wb, *args):
        try:
            self._ppa.rotate_scale(*(args + (nb, wb)))
        except OSError:
            if not nb:
                raise
            self._ppa.sync()
            self._refused += 1
            self._ppa.rotate_scale(*(args + (False, wb)))

    # A paint-buffer block of at least this many pixels is rotated through the
    # SRAM bounce (moy_ppa.rotate_bounce -- the AXI GDMA copies its rows into
    # SRAM bands off the CPU and the engine rotates from there): the engine
    # reads PSRAM at ~40MB/s and internal SRAM at ~4x that. The threshold is
    # where a plain rotate stops hiding behind the next frame's draw: below
    # it the engine's read finishes inside the ~18ms the desk spends drawing
    # anyway, and the bounce -- full rows, a cache writeback of them, the
    # bands -- only adds. Measured on the Guition P4 (2026-09-09): a 512x480
    # Settings window scroll 18ms a frame plain, 25 bounced; the 1120x720
    # picker 44ms plain, 21 bounced; the full frame 47 -> 25.
    BOUNCE_MIN_PX = 384 * 1024

    def _rotate(self, fb, paint, x, y, w, h, nb=False):
        """Rotate the paint buffer's landscape block onto scan buffer `fb`.
        Returns the number of engine transactions it submitted (the frame's
        op count is what the present fences by)."""
        # Destination is always a scan buffer -> no writeback.
        px, py, pw, ph = rotate_rect(x, y, w, h, self.angle, self._w, self._h)
        rb = self._bounce
        if rb is not None and w * h >= self.BOUNCE_MIN_PX:
            # FULL ROWS, whatever the block's width: the engine loses
            # transactions on a bounced block narrower than its band (the
            # desk scroll wedged the driver within ten frames, while full
            # frames ran clean for thousands of bands, 2026-09-09), and the
            # rows OUTSIDE the block are current too -- the WM repaints every
            # change into both paint buffers before partial frames resume,
            # which is what lets a full rotate read this buffer at all. The
            # sibling's debt stays the described rect (`changed`); the extra
            # columns it already holds.
            fx, fy, fw, fh = rotate_rect(0, y, self._w, h, self.angle,
                                         self._w, self._h)
            # n bands submitted; 0 = the queue is full this once (rotate it
            # plainly, ask again next frame); -1 = never for this picture.
            n = rb(fb, self._pw, self._ph, fx, fy, paint, self._w, self._h,
                   0, y, self._w, h, self.angle, nb)
            if n > 0:
                self._bounced += n
                return n
            if n < 0:
                self._bounce = None
            else:
                self._refused += 1
        self._rot(nb, False, fb, self._pw, self._ph, px, py,
                  paint, self._w, self._h, x, y, w, h, self.angle)
        return 1

    def _scratch_for(self, n):
        if self._scratch is None or self._scratch_n < n:
            self._scratch = self._alloc(n)
            self._scratch_n = n
        return self._scratch

    def _present(self, late):
        """Show the deferred buffer (every op landed) and swap."""
        back = self._pending
        self._pending = None
        self._dsi.show(back)
        self._front = back
        self._back = 1 - back
        if late:
            self._late_n += 1
        else:
            self._pres_n += 1

    def flush(self):
        back = self._back
        fb = self._fbs[back]
        stale = self._stale[back]
        game = self._game
        self._game = None
        damage = self._damage
        self._damage = None
        stamp = self._stamp_pending
        self._stamp_pending = None
        paint_buf = self._paints[self._pi]
        nb = self._async
        if self._pending is not None:
            # The last frame's ops outlived a whole loop: fence and show it
            # before this frame's ops go to its sibling buffer.
            self._ppa.sync()
            self._present(True)
            back = self._back
            fb = self._fbs[back]
            stale = self._stale[back]
        t0 = _ticks_us()
        ops = 0
        if stamp is not None:
            # The moving window's content, into THIS paint buffer, ahead of
            # every rotate that reads it. The rows' CPU chrome is written
            # back at submit (rotate's msync -- the one destination on this
            # path that needs it); nothing CPU-writes them again before the
            # present fence.
            dst, dw, dh, sx, sy, sbuf, sw, sh = stamp
            self._rot(nb, True, dst, dw, dh, sx, sy, sbuf, sw, sh,
                      0, 0, sw, sh, 0)
            ops += 1
            self._stamp_n += 1
        # Two separate questions. What did THIS FRAME change (relative to the
        # frame on glass)? -- everything, or the game rect (+ the chrome
        # strip), or the rects the WM described (+ strip, + game rect). And
        # what does the target buffer need to become current? -- a full
        # rotate if the frame was full OR the buffer missed one, else its
        # missed rects copied plus this frame's rects rotated. Conflating
        # them made every buffer "behind" forever and the ping-pong never
        # converged.
        rects = None            # this frame's landscape rects, game first
        direct_game = False     # rects[0] is the game, straight from its canvas
        painted = False         # the game composite reached the paint buffer
        grect = None
        if game is not None:
            src, sw, sh, ox, oy, scale, paint, quiet, direct = game
            grect = (ox, oy, sw * scale, sh * scale)
            # Noted damage (or a deferred stamp) means the WM drew, whatever
            # the gates say (a blit-only window render moves none of them).
            if damage is None and stamp is None and quiet():
                rects = [grect]
                direct_game = direct
            else:
                paint()                       # the paint buffer needs it too
                painted = True
        if rects is None and damage:
            dr = self._damage_rects(damage)
            if dr is None:
                self._dmg_declined += 1
            else:
                rects = ([grect] if grect is not None else []) + dr
                self._dmg_n += 1
                self._dmg_rects += len(dr)
        if rects is not None:
            strip = self._strip()
            if strip is not None:
                rects.append(strip)
        changed = None if rects is None else [
            rotate_rect(x, y, w, h, self.angle, self._w, self._h)
            for (x, y, w, h) in rects]
        if changed is None or stale is None or len(stale) > self.STALE_LIMIT:
            if game is not None and not painted:
                paint()                       # a full rotate reads the paint buffer
            ops += self._rotate(fb, paint_buf, 0, 0, self._w, self._h, nb)
            self._full_n += 1
            self._full_us += _ticks_diff(_ticks_us(), t0)
            # `changed` stays what the FRAME changed: a full rotate made THIS
            # buffer current, the other one still lacks only the rects.
        else:
            front = self._fbs[self._front]
            # A stale rect this frame's rects do not cover is either COPIED
            # 1:1 from the buffer on glass or, when growing one of this
            # frame's paint-buffer rects to swallow it moves fewer pixels
            # than the copy would, ROTATED as part of that rect (a drag's
            # union barely moves between frames, so the grown rect is a
            # few px larger and the copy -- as large as the rect itself --
            # is gone). Never the game rect: its source is the game canvas.
            # `changed` stays the frame's OWN damage (what the sibling buffer
            # will owe); the grown rects live in `cover`/`rects` only, or a
            # drag's trail would compound frame over frame.
            cover = list(changed)
            for r in stale:
                if _covered(r, cover):
                    continue
                best = None
                for i in range(1 if direct_game else 0, len(rects)):
                    grown = _bbox(rects[i], unrotate_rect(
                        r[0], r[1], r[2], r[3], self.angle, self._w, self._h))
                    cost = grown[2] * grown[3] - rects[i][2] * rects[i][3]
                    if cost < r[2] * r[3] and (best is None or cost < best[0]):
                        best = (cost, i, grown)
                if best is not None:
                    rects[best[1]] = best[2]
                    cover[best[1]] = rotate_rect(
                        best[2][0], best[2][1], best[2][2], best[2][3],
                        self.angle, self._w, self._h)
                    self._grown += 1
                    continue
                self._rot(nb, False, fb, self._pw, self._ph, r[0], r[1],
                          front, self._pw, self._ph, r[0], r[1], r[2], r[3], 0)
                ops += 1
                self._copies += 1
            if direct_game:
                # The strip and the copies go FIRST: wait(keep=1) at the
                # present then covers everything but the scale+rotate.
                for (x, y, w, h) in rects[1:]:
                    ops += self._rotate(fb, paint_buf, x, y, w, h, nb)
                px, py, _pw, _ph = changed[0]
                if nb:
                    n = sw * sh * 2
                    scr = self._scratch_for(n)
                    self._rot(True, False, scr, sw, sh, 0, 0, src, sw, sh,
                              0, 0, sw, sh, 0)
                    self._rot_scale(True, False, fb, self._pw, self._ph,
                                    px, py, scr, sw, sh, scale, self.angle)
                    ops += 2
                else:
                    self._rot_scale(False, False, fb, self._pw, self._ph,
                                    px, py, src, sw, sh, scale, self.angle)
            else:
                if game is not None and not painted:
                    paint()                   # crisp quiet: composite, then rotate
                # The plain rotates (the strip, small rects) go FIRST and the
                # bounced blocks LAST: the bounce is a worker on the other
                # core submitting bands as it copies them, and the engine
                # loses transactions when the console submits alongside it
                # (the desk scroll wedged the driver within ten frames,
                # 2026-09-09). After this loop the console submits nothing
                # until the next flush, which fences everything first.
                big = None
                for (x, y, w, h) in rects:
                    if self._bounce is not None and w * h >= self.BOUNCE_MIN_PX:
                        if big is None:
                            big = []
                        big.append((x, y, w, h))
                        continue
                    ops += self._rotate(fb, paint_buf, x, y, w, h, nb)
                if big is not None:
                    for (x, y, w, h) in big:
                        ops += self._rotate(fb, paint_buf, x, y, w, h, nb)
            self._rect_n += 1
            self._rect_us += _ticks_diff(_ticks_us(), t0)
        self._stale[back] = []
        other = self._front
        if changed is None:
            self._stale[other] = None
        elif self._stale[other] is not None:
            lst = self._stale[other]
            for r in changed:
                if r not in lst:
                    lst.append(r)
        # The next frame paints the other buffer while this one's ops fly.
        self._pi = 1 - self._pi
        if nb:
            # Everything older than this frame's ops must have landed before
            # the buffer painted next is touched; a direct game frame's copy
            # of the game canvas must have landed before the cart's tick.
            self._keep = 1 if direct_game else ops
            self._pending = back
            self._def_n += 1
            return                            # shown at the next present
        self._dsi.show(back)
        self._front = back
        self._back = 1 - back

    def present_pending(self):
        """The loop's pre-frame hook, BEFORE the canvas re-points at the
        next paint buffer and the cart's tick: fence every op older than the
        last flush's (they read the buffer about to be painted, or the game
        canvas), and show the deferred frame if its own ops landed too."""
        if self._pending is None:
            return
        t0 = _ticks_us()
        self._ppa.wait(self._keep)
        self._wait_us += _ticks_diff(_ticks_us(), t0)
        if self._ppa.done():
            self._present(False)

    def sync(self):
        """Leave no op in flight and no deferred show unpresented."""
        if self._pending is not None:
            self._ppa.sync()
            self._present(True)

    def async_stats(self):
        """(deferred frames, shown at a present, shown by the next flush,
        present fence us, drag stamps the PPA performed, refused submits,
        bounce transactions)."""
        return (self._def_n, self._pres_n, self._late_n, self._wait_us,
                self._stamp_n, self._refused, self._bounced)

    def overlap_stats(self):
        """The PERF line's ppa= slots, re-purposed for this path:
        (rect frames, copies, full frames, full_us, rect frames, rect_us,
        ppa timeouts) -- fence_ms is the full-rotate cost, gfence_ms the
        rect-rotate cost."""
        try:
            timeouts = self._ppa.stats()[2]
        except Exception:  # noqa: BLE001
            timeouts = 0
        return (self._rect_n, self._copies, self._full_n, self._full_us,
                self._rect_n, self._rect_us, timeouts)

    def underruns(self):
        try:
            return self._dsi.underruns()
        except Exception:  # noqa: BLE001
            return None
