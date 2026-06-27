# KidCode full-screen native compositor (Stage 3).
#
# Owns a full-screen RGB565 framebuffer (PSRAM) and flushes only the *dirty*
# region to the ST7789 over the lcd_bus DMA path -- the model the Stage 2 gate
# proved is required (full-frame redraw is bus-bound at ~21-28 FPS; a dirty band
# is 73-91 FPS). See docs/history/STAGE3_PLAN.md / docs/history/SPIKE_FINAL.md.
#
# Drawing and strip-packing go through the native `kc_gfx` C kernel when present
# (VM-neutral, fast); otherwise they fall back to `framebuf` / pure-Python so the
# module still works on an image built without kc_gfx. The actual DMA transfer
# uses the same lcd_bus tx_param/tx_color calls proven in Stage 1/2.
#
# On the host (no bus) make_compositor(None) returns None, so the simulator/tests
# never import device-only modules.

CASET = 0x2A   # column address set
RASET = 0x2B   # row address set
RAMWR = 0x2C   # memory write
RAMWRC = 0x3C  # memory write continue (stream more pixels into the same window)


# --- flush-breakdown instrumentation (perf #33/#12) -------------------------
#
# THE QUESTION. The desktop loop times the whole comp.flush() at a CONSTANT
# ~28 ms/frame for a 320x240 RGB565 frame (153,600 bytes). At a *true* 80 MHz
# SPI that transfer alone should be ~15.4 ms, so 28 ms is ~2x the ideal. Two
# very different causes have the same total:
#   (a) the SPI clock isn't actually 80 MHz -- the T-Deck display pins
#       (MOSI=41, SCK=40, MISO=38) are NOT the ESP32-S3 IOMUX-native FSPI pins
#       (11/12/13), so they route through the GPIO matrix, which caps a
#       write-only LCD output at ~40 MHz -> a 153 KB push is ~30 ms. If that's
#       it, the 28 ms is almost ALL `tx` and 80 MHz is unreachable on this fixed
#       wiring (a real finding, not a fixable bug).
#   (b) a big slice of the 28 ms is NON-transfer overhead inside flush() -- the
#       `self._frame[:] = self._fb` 153 KB PSRAM->PSRAM copy (the doc estimates
#       ~3 ms) plus the per-band window/tx_color setup -- in which case `tx` is
#       ~15 ms and the copy/overhead is the lever to cut.
#
# To tell these apart ON HARDWARE (we can't profile here), flush() times its
# sub-steps with ticks_us and logs ONE line via kidcode_diag (which echoes live
# to serial AND persists to the boot dump):
#
#   FLUSHBRK copy=<ms> tx=<ms> setup=<ms> n=<bands> total=<ms>
#
#   copy  = the `_frame[:] = _fb` full-frame copy (0 when the strip path is used)
#   tx    = the band push: the tx_color DMA transfers + the one tiny _set_window
#           window arm (the actual SPI work -- the window arm is a negligible
#           tx_param vs the 153 KB push)
#   setup = total - copy - tx: residual Python/bookkeeping not in copy or tx
#           (normally ~0; surfaces any unattributed per-flush overhead)
#   n     = number of bands / tx_color calls
#   total = the whole flush (should track the console's `flush=` HUD number)
#
# READING IT: if tx ~= 25-28 ms -> the SPI clock is the wall (case a). If
# tx ~= 15 ms and copy+setup ~= 13 ms -> overhead is the wall (case b).
#
# Gated by FLUSH_INSTRUMENT and sampled every FLUSH_SAMPLE_EVERY-th flush so it
# can't flood the diag ring / serial. To REVERT: set FLUSH_INSTRUMENT = False --
# the timing path then never runs and flush() is byte-for-byte the original hot
# loop (the timed branch is a self-contained copy of the untimed one).
FLUSH_INSTRUMENT = True
FLUSH_SAMPLE_EVERY = 30   # log one FLUSHBRK per N flushes (~ every 0.5-1.5 s)


# --- DMA double-buffering / flush overlap (#40) -----------------------------
#
# DEFAULT OFF. This is the fragile #40 DMA/SPI path, so it is opt-in: with
# DOUBLE_BUFFER = False the proven single-buffer banded flush below runs
# byte-for-byte unchanged (the instant revert -- see "TO ENABLE / TO REVERT").
#
# WHY. The panel flush is hardware-floored at ~28 ms (153,600 B @ ~40 MHz on
# this board's GPIO-matrix wiring -- see docs/spi_flush_80mhz.md; it can't go
# faster). Today the flush is SERIAL with render: frame = render(~50) +
# flush(28) ~= 78 ms -> ~13 fps. With double-buffering the flush DMA runs WHILE
# the CPU renders the next frame, so frame = max(render, flush) ~= 50 ms ->
# ~20 fps. The flush can't be made faster, but it can be HIDDEN.
#
# HOW (the buffer/swap design + how completion is tracked).
# Two DISTINCT PSRAM RGB565 framebuffers, A and B (2 x 153,600 B in PSRAM; we
# have ~7.7 MB free, so this is fine). At any moment one is the FRONT (being
# DMA'd to the panel) and the other is the BACK (the DeviceCanvas / kc_gfx draw
# into it). flush() never copies (the old `_frame[:] = _fb`): A and B are
# distinct, so the DMA can read the front while the CPU writes the back -- no
# race, which is the whole reason the dedicated-copy buffer existed.
#
# COMPLETION TRACKING -- the load-bearing detail. At the MicroPython
# lcd_bus.SPIBus level there is NO exposed on_color_trans_done callback and no
# "is the DMA busy" poll. The only completion signal the API gives us is that
# `tx_color(..., last=True)` BUSY-WAITS until the whole queued transfer chain
# has drained (see kc_canvas.py / kidcode_shell.py: "tx_color ... busy-waits for
# the SPI transfer"); the continuation bands (`last=False`) queue async into
# esp_lcd's trans_queue (depth 10) and return immediately. So we get overlap by
# DEFERRING the one blocking band: flush() kicks every band of the front buffer
# `last=False` (all async) and holds back the FINAL band; the blocking
# `last=True` band is issued at the START of the NEXT flush (_drain_dma), i.e.
# AFTER the CPU has spent the frame rendering into the back buffer. The loop only
# stalls if render finished faster than the front's DMA -- exactly the
# max(render, flush) behaviour. `_dma_pending` (the held-back final band's
# cmd/y/rows) + `_dma_front` (which buffer is in flight) are the done-flag: a
# non-None `_dma_pending` means "a frame's DMA is still in flight; drain it
# before reusing that buffer or touching the shared SPI bus".
#
# SD vs panel-DMA mutual exclusion: the SD card shares this SPI host, so an SD
# access can NOT overlap an in-flight panel DMA. sync() drains any pending DMA;
# run_desktop calls comp.sync() before every with_sd_live op (see #40 notes in
# CLAUDE.md -- never flush/DMA the panel inside an SD session).
#
# TO ENABLE: set DOUBLE_BUFFER = True (or flip comp.double_buffer at runtime),
# rebuild, flash. TO REVERT (the one-flag fallback if it tears/glitches/hangs --
# the #40 failure mode): set DOUBLE_BUFFER = False -> flush() is the original
# single-buffer banded path, byte-for-byte. FLUSHBRK instrumentation works in
# BOTH paths so the overlap is measurable either way.
DOUBLE_BUFFER = False


def plan_strips(height, strip_h):
    """Row bands [(y, rows), ...] covering `height`, each <= strip_h rows.

    Pure helper (no hardware) so the strip math is host-testable.
    """
    if strip_h <= 0:
        raise ValueError("strip_h must be positive")
    bands = []
    y = 0
    while y < height:
        rows = strip_h if y + strip_h <= height else height - y
        bands.append((y, rows))
        y += rows
    return bands


def clip_rect(x, y, w, h, max_w, max_h):
    """Clip (x, y, w, h) to the (max_w, max_h) screen. Pure / host-testable."""
    if x < 0:
        w += x
        x = 0
    if y < 0:
        h += y
        y = 0
    if x + w > max_w:
        w = max_w - x
    if y + h > max_h:
        h = max_h - y
    if w < 0:
        w = 0
    if h < 0:
        h = 0
    return (x, y, w, h)


def union_rect(a, b):
    """Bounding box union of two (x, y, w, h) rects (either may be None)."""
    if a is None:
        return b
    if b is None:
        return a
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x = ax if ax < bx else bx
    y = ay if ay < by else by
    x2 = (ax + aw) if (ax + aw) > (bx + bw) else (bx + bw)
    y2 = (ay + ah) if (ay + ah) > (by + bh) else (by + bh)
    return (x, y, x2 - x, y2 - y)


class DirtyTracker:
    """Single union bounding box of everything drawn since the last flush."""

    def __init__(self):
        self._box = None

    def add(self, x, y, w, h):
        if w <= 0 or h <= 0:
            return
        self._box = union_rect(self._box, (x, y, w, h))

    def clear(self):
        self._box = None

    def is_empty(self):
        return self._box is None

    def take(self, max_w, max_h):
        """Return the clamped dirty box (x, y, w, h) and reset, or None."""
        if self._box is None:
            return None
        bx, by, bw, bh = self._box
        x, y, w, h = clip_rect(bx, by, bw, bh, max_w, max_h)
        self._box = None
        if w <= 0 or h <= 0:
            return None
        return (x, y, w, h)


class Compositor:
    """Full-screen RGB565 framebuffer with dirty-rect flush over lcd_bus DMA."""

    def __init__(self, bus, width=320, height=240, strip_h=40):
        import kc_alloc

        self._bus = bus
        self._w = width
        self._h = height
        self._strip_h = strip_h
        self._row_bytes = width * 2
        # Full-screen RGB565 draw target. On SPIRAM_OCT this lives in PSRAM.
        self._fb = bytearray(width * height * 2)
        self._fb_mv = memoryview(self._fb)
        # DMA-capable strip buffer in INTERNAL SRAM, for small dirty-rect flushes.
        self._strip = kc_alloc.malloc_dma(width * strip_h * 2)
        # Full-frame DMA buffer in PSRAM, for whole-screen flushes in ONE transfer.
        # Multiple MicroPython-level tx_color calls glitch a few rows at each
        # boundary (the command->data transition); a single transfer lets esp_lcd
        # split the data internally with no re-issued command, so it's seamless
        # (the same reason the 128x128 kc_canvas blit is clean). The S3 can DMA
        # from PSRAM. Falls back to the strip path if the buffer can't be had.
        self._frame = None
        try:
            import lcd_bus
            self._frame = kc_alloc.malloc_dma(
                width * height * 2, lcd_bus.MEMORY_SPIRAM | lcd_bus.MEMORY_DMA
            )
        except Exception as exc:
            print("KidCode compositor: full-frame buffer unavailable:", exc)
            self._frame = None
        self._frame_mv = memoryview(self._frame) if self._frame is not None else None
        # --- DMA double-buffer ping-pong (#40; default OFF, see DOUBLE_BUFFER) ---
        # Per-instance copy of the module flag so a runtime toggle (comp.double_buffer
        # = True) is possible without touching globals. Only honoured when a SECOND
        # PSRAM frame buffer could be allocated; otherwise we fall back to the proven
        # single-buffer path (and never tear).
        self.double_buffer = DOUBLE_BUFFER
        # `_fb` above is buffer A (the always-present draw target). `_fb_b` is the
        # second ping-pong buffer in PSRAM DMA memory; with both present, one is the
        # FRONT (DMAing to the panel) and the other the BACK (drawn into). `_back` is
        # the buffer the canvas currently draws into; `_dma_front` is the buffer whose
        # bands are queued/in-flight; `_dma_pending` is the held-back final band
        # (cmd, y, rows) -- non-None == "a frame's DMA is still in flight, drain it
        # before reusing the front or touching the shared SPI bus". See the module
        # DMA-double-buffer block for the full design + completion-tracking rationale.
        self._fb_b = None
        if self.double_buffer:
            try:
                import lcd_bus
                self._fb_b = kc_alloc.malloc_dma(
                    width * height * 2, lcd_bus.MEMORY_SPIRAM | lcd_bus.MEMORY_DMA
                )
            except Exception as exc:
                print("KidCode compositor: 2nd framebuffer unavailable, "
                      "double-buffer OFF:", exc)
                self._fb_b = None
        if self._fb_b is None:
            self.double_buffer = False     # no 2nd buffer -> single-buffer path
        # ping-pong state: A is _fb, B is _fb_b. `_back` is the bytearray the canvas
        # draws into THIS frame; `_dma_front` is the buffer being DMA'd; `_dma_pending`
        # the (cmd, y, rows) final band held back from the last flush() (None = idle).
        self._back = self._fb
        self._front = self._fb
        self._dma_front = None
        self._dma_pending = None
        # PERF knob: rows per SPI transfer in the full-frame flush. Bigger = fewer
        # transfers = higher FPS, bounded by the per-band DMA bounce fitting internal
        # RAM. 48 -> 5 transfers for 240 rows (24 was 10). Tunable via _flush_rows.
        self._flush_rows = 48
        # FLUSHBRK instrumentation: count flushes so we sample 1-in-N (see the
        # FLUSH_INSTRUMENT block at module top). Lazily-bound diag/time handles so
        # the host (no kidcode_diag) and an instrument-off build pay nothing.
        self._flush_n = 0
        self._diag = None
        self._diag_tried = False
        self._diag_us = None
        self._dirty = DirtyTracker()
        # Native pixel kernel (fast, VM-neutral) when the image has it.
        try:
            import kc_gfx
            self._gfx = kc_gfx
        except ImportError:
            self._gfx = None
        # framebuf fallback for drawing (and the only path for text()).
        # `_fbuf` follows the current BACK buffer: in single-buffer mode that is
        # always _fb (identical to before); in double-buffer mode it is rebound to
        # the new back buffer on each swap (_swap_buffers). framebuf can't retarget
        # its backing store in place, so we keep one framebuf per physical buffer and
        # pick the right one -- no per-frame allocation.
        self._fbuf = None
        self._fbuf_a = None
        self._fbuf_b = None
        try:
            import framebuf
            self._fbuf_a = framebuf.FrameBuffer(self._fb, width, height, framebuf.RGB565)
            if self._fb_b is not None:
                self._fbuf_b = framebuf.FrameBuffer(self._fb_b, width, height, framebuf.RGB565)
        except Exception:
            self._fbuf_a = None
            self._fbuf_b = None
        # memoryview of each physical buffer (for the _blit_py fallback / strip pack).
        self._fb_a_mv = self._fb_mv
        self._fb_b_mv = memoryview(self._fb_b) if self._fb_b is not None else None
        # Point the BACK-buffer aliases at A initially (single-buffer => never moves).
        self._back_mv = self._fb_a_mv
        self._fbuf = self._fbuf_a

    # -- introspection -------------------------------------------------------

    def framebuffer(self):
        # The current BACK buffer -- what the canvas / kc_gfx must draw into THIS
        # frame. In single-buffer mode this is always _fb (unchanged). In
        # double-buffer mode it ping-pongs, so a DeviceCanvas must re-fetch it each
        # frame (see DeviceCanvas.sync_back in kid_runtime) -- never cache it.
        return self._back

    def back_buffer(self):
        """Alias of framebuffer(): the buffer the canvas should draw into this frame.
        Named so the double-buffer wiring reads clearly at the call site."""
        return self._back

    def size(self):
        return (self._w, self._h)

    def has_gfx(self):
        return self._gfx is not None

    def gfx(self):
        """The native kc_gfx kernel (or None). Lets a canvas drawing into this
        compositor's framebuffer() call the C fill/blit kernels directly."""
        return self._gfx

    def dirty(self):
        return self._dirty

    # -- drawing (marks dirty) ----------------------------------------------

    def clear(self, color):
        if self._gfx is not None:
            self._gfx.fill(self._back, self._w * self._h, color)
        elif self._fbuf is not None:
            self._fbuf.fill(color)
        self._dirty.add(0, 0, self._w, self._h)

    def fill_rect(self, x, y, w, h, color):
        if self._gfx is not None:
            self._gfx.fill_rect(self._back, self._w, x, y, w, h, color)
        elif self._fbuf is not None:
            self._fbuf.fill_rect(x, y, w, h, color)
        self._dirty.add(x, y, w, h)

    def blit(self, src, dx, dy, sw, sh, key=-1):
        """Blit a sw*sh RGB565 source buffer at (dx, dy); key=-1 opaque."""
        if self._gfx is not None:
            self._gfx.blit565(self._back, self._w, self._h, dx, dy, src, sw, sh, key)
        else:
            self._blit_py(src, dx, dy, sw, sh, key)
        self._dirty.add(dx, dy, sw, sh)

    def text(self, s, x, y, color):
        # 8x8 framebuf font (kc_gfx native font is a Stage 3.1 follow-on).
        if self._fbuf is not None:
            self._fbuf.text(s, x, y, color)
        self._dirty.add(x, y, len(s) * 8, 8)

    def _blit_py(self, src, dx, dy, sw, sh, key):
        # Correct-but-slow fallback used only when kc_gfx is absent.
        smv = memoryview(src)
        fb = self._back_mv
        w = self._w
        for row in range(sh):
            ty = dy + row
            if ty < 0 or ty >= self._h:
                continue
            for col in range(sw):
                tx = dx + col
                if tx < 0 or tx >= w:
                    continue
                si = (row * sw + col) * 2
                lo = smv[si]
                hi = smv[si + 1]
                if key >= 0 and (lo | (hi << 8)) == key:
                    continue
                di = (ty * w + tx) * 2
                fb[di] = lo
                fb[di + 1] = hi

    # -- flush ---------------------------------------------------------------

    def flush(self):
        """Flush the whole screen from the dedicated PSRAM frame buffer in row-bands.

        Each band is a DISTINCT, stable slice of `_frame` (copied once up front and
        never overwritten during the flush), so the async esp_lcd DMA (trans_queue
        depth 10) can never read a buffer that the next band has already clobbered --
        that reuse-race on the single shared strip buffer caused the one-band vertical
        offset/duplication. Banding into <=strip_h rows also keeps each transfer's DMA
        bounce small enough to allocate (a single 320x240 tx_color NO_MEMs on the S3's
        fragmented internal heap). Window armed once; RAMWR then RAMWRC streams in.

        When FLUSH_INSTRUMENT is on, one flush in FLUSH_SAMPLE_EVERY is timed and a
        `FLUSHBRK copy=.. tx=.. setup=.. n=.. total=..` line is logged (see the
        instrumentation block at the top of this module) to localize the ~28 ms --
        clock (tx) vs overhead (copy/setup). The untimed path below is unchanged.

        DOUBLE_BUFFER (default OFF, #40): when enabled, flush() routes to the
        ping-pong path (_flush_double) -- drain the previous frame's in-flight DMA,
        swap, then kick the just-rendered buffer's DMA and RETURN so the CPU can
        render the next frame while it transfers (frame = max(render, flush)). When
        OFF, the proven single-buffer banded path below runs byte-for-byte."""
        if self.double_buffer:
            self._flush_double()
            return
        if FLUSH_INSTRUMENT:
            self._flush_n += 1
            if self._flush_n >= FLUSH_SAMPLE_EVERY:
                self._flush_n = 0
                if self._flush_instrumented():
                    self._dirty.clear()
                    return
        if self._frame is not None:
            self._frame[:] = self._fb     # one stable full copy in PSRAM
            self._flush_full_frame()
        else:
            self._flush_region(0, 0, self._w, self._h)
        self._dirty.clear()

    # -- DMA double-buffer / flush overlap (#40; default OFF) ----------------

    def _flush_double(self):
        """Ping-pong flush: hide the panel DMA behind the next frame's render.

        Sequence each call (see the DOUBLE_BUFFER block at module top for the full
        design + completion-tracking rationale):
          1. DRAIN the previous frame's in-flight DMA -- issue the held-back final
             band (`last=True`, which busy-waits until the whole chain has drained).
             This runs AFTER the CPU spent the frame rendering into `_back`, so the
             wait is only the slice of flush that render didn't already hide.
          2. SWAP: the just-rendered `_back` becomes the new FRONT (to DMA); the old
             front (now drained, safe to overwrite) becomes the new `_back`.
          3. KICK the new front's bands `last=False` (async, queue + return) and HOLD
             the final band in `_dma_pending` for the next call's drain. The CPU now
             renders the next frame into `_back` while the front DMAs.

        No `_frame[:] = _fb` copy: A and B are distinct buffers, so the DMA reads the
        front while the CPU writes the back -- the invariant that makes this race-free
        (NEVER render into a buffer whose DMA hasn't drained; step 1 guarantees it).

        FLUSHBRK still samples here (1-in-N), timing the DRAIN as `tx` (the residual
        flush the overlap didn't hide) + the KICK as `setup`, so the overlap is
        measurable: a well-hidden flush shows tx -> ~0."""
        instrument = False
        if FLUSH_INSTRUMENT:
            self._flush_n += 1
            if self._flush_n >= FLUSH_SAMPLE_EVERY:
                self._flush_n = 0
                instrument = True
        us = self._ticks_us() if instrument else None
        if us is not None:
            try:
                import time
                t0 = us()
                self._drain_dma()            # step 1: finish the prior frame's DMA
                t1 = us()
                self._swap_buffers()         # step 2
                self._kick_front()           # step 3: queue bands, hold the last
                t2 = us()
                drain_us = time.ticks_diff(t1, t0)
                kick_us = time.ticks_diff(t2, t1)
                # tx = the residual (un-hidden) flush we still had to wait on;
                # setup = the kick (window arm + queueing the async bands); copy = 0
                # (double-buffer removes the PSRAM->PSRAM copy entirely). n = the total
                # band count (the _flush_rows split), for parity with the single-buffer
                # FLUSHBRK line. A well-hidden flush shows tx -> ~0.
                n = (self._h + self._flush_rows - 1) // self._flush_rows
                self._log_flushbrk(0, drain_us, kick_us, n, drain_us + kick_us)
            except Exception:
                # Instrumentation must never crash the loop: fall back to the plain
                # (untimed) sequence so the frame still ships.
                self._drain_dma()
                self._swap_buffers()
                self._kick_front()
        else:
            self._drain_dma()
            self._swap_buffers()
            self._kick_front()
        self._dirty.clear()

    def _swap_buffers(self):
        """Make the just-rendered `_back` the FRONT, and the (now drained) old front
        the new `_back`. Repoints the back-buffer aliases (_back / _back_mv / _fbuf)
        so subsequent draws + framebuffer() target the new back. Caller must have
        drained any in-flight DMA first (the old front must be safe to overwrite)."""
        front = self._back                     # the frame we just rendered -> DMA it
        back = self._fb_b if front is self._fb else self._fb
        self._front = front
        self._back = back
        if back is self._fb:
            self._back_mv = self._fb_a_mv
            self._fbuf = self._fbuf_a
        else:
            self._back_mv = self._fb_b_mv
            self._fbuf = self._fbuf_b

    def _kick_front(self):
        """Queue every band of `_front` EXCEPT the last as async tx_color (`last=False`,
        returns immediately), recording the final band in `_dma_pending` so the NEXT
        flush's _drain_dma issues it `last=True` (the busy-wait completion point). The
        window is armed once here; the held-back final band reuses the same armed
        window (RAMWRC continues into it). `_dma_front` marks which buffer is in
        flight so sync()/_drain_dma know what to wait on."""
        self._set_window(0, 0, self._w - 1, self._h - 1)
        mv = self._front_mv()
        rb = self._row_bytes
        rows_per = self._flush_rows
        cmd = RAMWR
        yy = 0
        h = self._h
        # All bands but the last: async. The last band's params are held back.
        while yy < h:
            rows = rows_per if yy + rows_per <= h else (h - yy)
            is_last = (yy + rows >= h)
            if is_last:
                # Hold the final band: do NOT issue it now (its last=True would
                # busy-wait here and serialize). Record it for the next drain.
                self._dma_pending = (cmd, yy, rows)
                break
            self._bus.tx_color(cmd, mv[yy * rb:(yy + rows) * rb],
                               0, yy, self._w - 1, yy + rows - 1, 0, False)
            cmd = RAMWRC
            yy += rows
        self._dma_front = self._front

    def _front_mv(self):
        """memoryview of the in-flight FRONT buffer (for the held-back final band)."""
        return self._fb_a_mv if self._front is self._fb else self._fb_b_mv

    def _drain_dma(self):
        """Finish the previous frame's in-flight DMA: issue the held-back final band
        with `last=True`, which busy-waits until the WHOLE queued chain has drained.
        After this returns the front buffer is fully on the panel and safe to reuse
        as the next back. No-op when nothing is pending (first frame / after sync())."""
        pending = self._dma_pending
        if pending is None:
            return
        self._dma_pending = None
        cmd, yy, rows = pending
        mv = self._fb_a_mv if self._dma_front is self._fb else self._fb_b_mv
        rb = self._row_bytes
        self._bus.tx_color(cmd, mv[yy * rb:(yy + rows) * rb],
                           0, yy, self._w - 1, yy + rows - 1, 0, True)
        self._dma_front = None

    def sync(self):
        """Block until any in-flight panel DMA has fully drained, leaving NO transfer
        on the shared SPI bus. The SD card shares this SPI host, so the desktop loop
        MUST call this before any with_sd_live op (a panel DMA and an SD access can't
        run at once -- see CLAUDE.md #40). A no-op in single-buffer mode (the flush
        already fully blocked) and when nothing is pending."""
        self._drain_dma()

    def _flush_full_frame(self):
        """Band the already-copied `_frame` out to the panel in one armed window.

        Returns the number of bands (tx_color calls). Pure transfer -- the caller
        owns the `_frame[:] = _fb` copy and the dirty clear. Split out of flush()
        so the instrumented path can time exactly this (the `tx`) in isolation."""
        self._set_window(0, 0, self._w - 1, self._h - 1)
        mv = self._frame_mv
        rb = self._row_bytes
        # PERF: each band is one SPI transfer; fewer/bigger bands = higher FPS
        # (the old single-transfer full flush was ~2x faster than the 24-row
        # banding). Bounded by the per-band DMA bounce fitting internal RAM.
        # 48 rows -> 5 transfers (was 24 rows / 10). Tune up to the measured
        # internal headroom (see the "KidCode mem:" boot readout).
        rows_per = self._flush_rows
        cmd = RAMWR
        yy = 0
        n = 0
        while yy < self._h:
            rows = rows_per if yy + rows_per <= self._h else (self._h - yy)
            last = (yy + rows >= self._h)
            self._bus.tx_color(cmd, mv[yy * rb:(yy + rows) * rb],
                               0, yy, self._w - 1, yy + rows - 1, 0, last)
            cmd = RAMWRC
            yy += rows
            n += 1
        return n

    def _ticks_us(self):
        """Bound time.ticks_us once (device-only; None on the host/no-time build)."""
        if self._diag_us is not None:
            return self._diag_us
        try:
            import time
            self._diag_us = time.ticks_us
        except (ImportError, AttributeError):
            self._diag_us = None
        return self._diag_us

    def _flush_instrumented(self):
        """Timed full-frame flush that logs ONE FLUSHBRK line. Returns True if it
        performed the flush (so flush() doesn't double it), False to fall through to
        the normal path (no timer / no full-frame buffer / any failure).

        Splits the ~28 ms into copy (PSRAM->PSRAM) + tx (SPI/DMA) + setup (window
        arm + per-band bookkeeping = total - copy - tx), the exact breakdown that
        decides clock-bound vs overhead-bound. All guarded -- instrumentation must
        never crash the render loop."""
        us = self._ticks_us()
        if us is None or self._frame is None:
            return False
        try:
            import time
            t0 = us()
            self._frame[:] = self._fb
            t1 = us()
            n = self._flush_full_frame()
            t2 = us()
            copy_us = time.ticks_diff(t1, t0)
            tx_us = time.ticks_diff(t2, t1)
            total_us = time.ticks_diff(t2, t0)
            # setup = total - copy - tx: the window arm (_set_window) + per-band
            # slicing/bookkeeping that isn't the copy or the DMA push itself. The
            # band loop's _set_window/tx_color setup is folded into tx_us (it's
            # inside _flush_full_frame), so setup_us is normally ~0; it exists to
            # surface any per-band Python overhead the split didn't attribute.
            setup_us = total_us - tx_us - copy_us
            if setup_us < 0:
                setup_us = 0
            self._log_flushbrk(copy_us, tx_us, setup_us, n, total_us)
            return True
        except Exception:
            return False

    def _log_flushbrk(self, copy_us, tx_us, setup_us, n, total_us):
        """Emit the FLUSHBRK line via kidcode_diag (live serial echo + boot dump).
        Falls back to a plain print if diag is unavailable. Fully guarded."""
        def _ms(v):
            return v / 1000.0
        msg = "copy=%.2f tx=%.2f setup=%.2f n=%d total=%.2f" % (
            _ms(copy_us), _ms(tx_us), _ms(setup_us), n, _ms(total_us))
        if not self._diag_tried:
            self._diag_tried = True
            try:
                import kidcode_diag
                self._diag = kidcode_diag
            except Exception:
                self._diag = None
        try:
            if self._diag is not None:
                self._diag.log("FLUSHBRK", msg)
                return
        except Exception:
            pass
        try:
            print("KidCode FLUSHBRK", msg)
        except Exception:
            pass

    def flush_rect(self, x, y, w, h):
        """Flush an explicit region."""
        # Region flushes pack from the BACK buffer through the internal-SRAM strip and
        # block (last=True), so any in-flight full-frame DMA must finish first or two
        # windows would collide on the bus. No-op drain unless double-buffer is mid-
        # flight (these region paths are bench/smoke only; the desktop uses flush()).
        if self.double_buffer:
            self._drain_dma()
        x, y, w, h = clip_rect(x, y, w, h, self._w, self._h)
        if w > 0 and h > 0:
            self._flush_region(x, y, w, h)

    def flush_dirty(self):
        """Flush only the region drawn since the last flush (the desktop path)."""
        if self.double_buffer:
            self._drain_dma()
        box = self._dirty.take(self._w, self._h)
        if box is None:
            return False
        self._flush_region(box[0], box[1], box[2], box[3])
        return True

    def _set_window(self, x1, y1, x2, y2):
        bus = self._bus
        bus.tx_param(CASET, bytes(((x1 >> 8) & 0xFF, x1 & 0xFF, (x2 >> 8) & 0xFF, x2 & 0xFF)))
        bus.tx_param(RASET, bytes(((y1 >> 8) & 0xFF, y1 & 0xFF, (y2 >> 8) & 0xFF, y2 & 0xFF)))

    def _flush_region(self, x, y, w, h):
        strip = self._strip
        gfx = self._gfx
        full_width = (x == 0 and w == self._w)
        cap_rows = len(strip) // (w * 2)
        if cap_rows < 1:
            cap_rows = 1
        rows_per = self._strip_h if self._strip_h < cap_rows else cap_rows
        rb = w * 2
        # Arm the CASET/RASET window ONCE for the whole region, then stream the
        # bands into it: RAMWR for the first, RAMWR-continue (0x3C) for the rest.
        # Re-arming the window before every band caused non-deterministic banding
        # on rapid multi-strip full flushes (the bug the static test exposed).
        self._set_window(x, y, x + w - 1, y + h - 1)
        cmd = RAMWR
        yy = y
        while yy < y + h:
            rows = rows_per if yy + rows_per <= y + h else (y + h - yy)
            nbytes = rows * rb
            if gfx is not None:
                gfx.pack_strip(self._back, self._w, x, yy, w, rows, strip)
            elif full_width:
                fbrb = self._row_bytes
                strip[:nbytes] = self._back_mv[yy * fbrb:(yy + rows) * fbrb]
            else:
                mv = self._back_mv
                off = 0
                for r in range(rows):
                    si = ((yy + r) * self._w + x) * 2
                    strip[off:off + rb] = mv[si:si + rb]
                    off += rb
            data = strip if nbytes == len(strip) else strip[:nbytes]
            last = (yy + rows >= y + h)
            self._bus.tx_color(cmd, data, x, yy, x + w - 1, yy + rows - 1, 0, last)
            cmd = RAMWRC
            yy += rows


def make_compositor(bus, width=320, height=240, strip_h=40):
    """Return a Compositor, or None when lcd_bus/hardware is unavailable."""
    if bus is None:
        return None
    try:
        return Compositor(bus, width, height, strip_h)
    except Exception as exc:
        print("KidCode kc_compositor disabled:", exc)
        return None
