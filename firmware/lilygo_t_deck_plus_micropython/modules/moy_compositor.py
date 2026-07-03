# Moybyte full-screen native compositor (Stage 3).
#
# Owns a full-screen RGB565 framebuffer (PSRAM) and flushes only the *dirty*
# region to the ST7789 over the lcd_bus DMA path -- the model the Stage 2 gate
# proved is required (full-frame redraw is bus-bound at ~21-28 FPS; a dirty band
# is 73-91 FPS). See docs/history/STAGE3_PLAN.md / docs/history/SPIKE_FINAL.md.
#
# Drawing and strip-packing go through the native `moy_gfx` C kernel when present
# (VM-neutral, fast); otherwise they fall back to `framebuf` / pure-Python so the
# module still works on an image built without moy_gfx. The actual DMA transfer
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
# sub-steps with ticks_us and logs ONE line via moybyte_diag (which echoes live
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
# WHY. Device FLUSHBRK measurement: the old serial flush was ~28 ms = a 10.5 ms
# `_frame[:]=_fb` PSRAM copy + ~17.4 ms tx (the SPI runs ~80 MHz -- the earlier
# "40 MHz floor" was wrong; see docs/history/spi_flush_80mhz.md). Double-buffering deletes
# the copy outright (A/B distinct buffers, no copy needed) -> measured flush drops
# to ~20 ms (copy=0), ~13->~16-19 fps. NOTE (measured): the tx is NOT fully hidden
# behind render -- lcd_bus exposes no async-completion callback, so the deferred
# final band drains synchronously (~17 ms shows up as `setup`/drain, not overlapped).
# So we bank the copy-removal win here; TRUE overlap (tx hidden) would need native
# esp_lcd access -- deferred (the per-object DRAW cost is the bigger wall: a native
# sprite-batch, #43). The flush can't be made faster, but the copy is gone.
#
# UPDATE (#43): "true overlap needs native esp_lcd" was WRONG -- the async-completion
# callback DOES exist at the MicroPython level (`bus.register_callback`), it was just
# unused, so every `last=False` band still busy-waited. The ASYNC_FLUSH path below
# registers it and finally hides the tx behind render. This block's "tx not hidden"
# caveat applies only when ASYNC_FLUSH is off.
#
# HOW (the buffer/swap design + how completion is tracked).
# Two DISTINCT PSRAM RGB565 framebuffers, A and B (2 x 153,600 B in PSRAM; we
# have ~7.7 MB free, so this is fine). At any moment one is the FRONT (being
# DMA'd to the panel) and the other is the BACK (the DeviceCanvas / moy_gfx draw
# into it). flush() never copies (the old `_frame[:] = _fb`): A and B are
# distinct, so the DMA can read the front while the CPU writes the back -- no
# race, which is the whole reason the dedicated-copy buffer existed.
#
# COMPLETION TRACKING -- the load-bearing detail. At the MicroPython
# lcd_bus.SPIBus level there is NO exposed on_color_trans_done callback and no
# "is the DMA busy" poll. The only completion signal the API gives us is that
# `tx_color(..., last=True)` BUSY-WAITS until the whole queued transfer chain
# has drained (see moy_canvas.py / moybyte_shell.py: "tx_color ... busy-waits for
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
DOUBLE_BUFFER = True   # device-confirmed stable + the copy-removal win (~13->16-19fps)


# --- async DMA completion: the REAL flush/render overlap (#43) ---------------
#
# THE BUG #40 BANKED BUT DIDN'T FIX. `lcd_bus.tx_color` BUSY-WAITS for the DMA to
# drain on EVERY call unless a Python callback is registered -- the `last` arg is
# IGNORED on the SPI path (ext_mod/lcd_bus/modlcd_bus.c:192 `while !trans_done`;
# lcd_types.c marks x/y/last LCD_UNUSED for esp_lcd_panel_io_tx_color). So the
# double-buffer "kick async, drain later" never overlapped: every band blocked and
# the frame cost render + flush, not max(render, flush). Device FLUSHBRK/PERF
# proved it: Beeper flush=21 draw=22 -> 22 fps (a real overlap would be ~45).
#
# THE FIX. Register a callback on the bus. Then tx_color QUEUES the band and
# returns immediately; esp_lcd fires on_color_trans_done -> bus_trans_done_cb ->
# our callback once per band as each DMA completes. _kick_front fires ALL bands
# async and returns, so the panel DMA runs while the CPU renders the NEXT frame;
# the next _drain_dma only waits for whatever render didn't already hide (FLUSHBRK
# `tx` -> ~0 when the overlap lands). Completion is tracked by counting callbacks
# (`_dma_done_n == _dma_target` == fully on the panel).
#
# ISR SAFETY (load-bearing). bus_trans_done_cb runs the callback IN ISR CONTEXT
# with the GC LOCKED (lcd_types.c cb_isr: gc_lock()), so the callback must not
# heap-allocate. `self._dma_done_n += 1` is heap-free: small ints are immediate
# (not heap objects), the STORE_ATTR targets a PRE-EXISTING slot (set in __init__,
# no dict resize), and the 0-arg call frame falls back to alloca under the lock
# (objfun.c VM_MAX_STATE_ON_STACK / objboundmeth.c n_total<=4). This is the same
# mechanism lvgl's own flush-ready callback relies on.
#
# Requires DOUBLE_BUFFER (distinct A/B buffers: DMA reads the front while the CPU
# writes the back). TO REVERT: ASYNC_FLUSH = False -> no callback is registered and
# the proven held-back-band path runs byte-for-byte (no overlap, but the
# copy-removal win remains). Registering the callback also makes the region paths
# (_flush_region via flush_dirty/flush_rect) async, so they wait per band (the
# shared strip buffer is reused) -- see _flush_region.
ASYNC_FLUSH = True


# --- PSRAM-direct single-transfer flush: the REAL overlap (#43) --------------
#
# The banded flush can't overlap render: esp_lcd calls spi_device_acquire_bus at the
# top of EVERY tx_color, and acquiring the bus lock waits for the device's in-flight
# DMA -- so each band's acquire blocks on the previous band's transfer. The escape is
# ONE tx_color for the whole frame (one acquire, all internal chunks queued async),
# which used to NO_MEM because spi_master BOUNCES PSRAM TX into the tiny internal
# MALLOC_CAP_DMA heap (153KB won't fit -> the reason it was banded to 30KB).
#
# Our esp-idf patch (spi_master.c setup_priv_desc, build patch #43) lets the S3 AHB
# GDMA read the PSRAM framebuffer DIRECTLY (no bounce) with a cache writeback, so a
# single full-frame tx_color now works AND queues async -> the panel DMA runs while
# the CPU renders the next frame (frame ~= max(render, flush)). The framebuffers are
# already PSRAM (the MEMORY_SPIRAM|MEMORY_DMA alloc), i.e. ext-DMA-capable by address.
#
# Requires ASYNC_FLUSH (completion via the registered callback) + the esp-idf patch.
# TO REVERT: PSRAM_DIRECT_FLUSH = False -> the proven banded path runs (still correct
# with the patch, just no overlap). If the panel garbles (cache-coherency/alignment),
# this flag is the first thing to flip.
PSRAM_DIRECT_FLUSH = True


# --- SRAM-bounce banded flush (#66): artifact-proof AND overlapped ------------
#
# WHY. The PSRAM-direct flush makes the panel DMA read 153KB straight from PSRAM,
# so ANY other heavy PSRAM traffic during the transfer (the GDMA layer copy; a
# fast cart's own churn through the dcache) can starve the SPI FIFO, which then
# clocks out garbage rows -- the 2026-07-03 horizontal-band artifacts. The
# structural fix: the panel DMA only ever reads INTERNAL SRAM, which cannot be
# starved by PSRAM contention. The frame is shipped as BOUNCE_ROWS-row bands:
# each band is memcpy'd front-PSRAM -> one of two internal DMA bounce buffers,
# then queued (tx_color). Two bands can be in flight, so the copy of band k
# overlaps the transfer of band k-1.
#
# WHO PUMPS. Queueing needs the esp_lcd tx_color no-acquire patch (#66,
# patches/esp_lcd_tx_color_noacquire.patch): continuation bands (cmd < 0) are
# queue-only and never block, so pump() costs ~80us per band (one 15KB memcpy +
# a queue). Bands are fed by a PUMP_TIMER_MS soft machine.Timer (esp32 Timer
# callbacks run via mp_sched between bytecodes -- they fire DURING the cart's
# _update, which has no other hook points and can run 20ms+), and by the next
# flush's drain as the always-correct fallback (a dead timer degrades to a
# serialized banded flush, never to corruption). The front buffer is immutable
# while it ships (ping-pong), so bands are tear-free by construction.
#
# COSTS. 2 x (320*BOUNCE_ROWS*2)B of internal DMA SRAM (24 rows -> 2x15360B),
# ~150KB/frame of PSRAM reads through the dcache (the pump memcpy; roughly half
# the pollution of the old CPU sync layer-copy since the SRAM writes are
# uncached). If internal RAM is tight (WiFi/web view NO_MEM, #38), this is the
# first alloc to shrink (12 rows halves it).
#
# TO REVERT: SRAM_BOUNCE_FLUSH = False -> the PSRAM-direct single-transfer path
# above runs unchanged (with its known contention-band risk).
SRAM_BOUNCE_FLUSH = True
BOUNCE_ROWS = 24       # rows per band: 24 -> 10 bands of 15360B on 320x240
PUMP_TIMER_MS = 2      # soft-timer pump period; 0 = drain-fallback only


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
        import moy_alloc

        self._bus = bus
        self._w = width
        self._h = height
        self._strip_h = strip_h
        self._row_bytes = width * 2
        # Full-screen RGB565 draw target (buffer A). Allocated via moy_alloc so it's the
        # SAME cache-line-aligned PSRAM as _fb_b/_frame: with PSRAM-direct DMA (#43) an
        # unaligned base glitched the DMA'd frame tail (the Beeper bottom artifact, only
        # visible on a flat-colour bottom). Falls back to a plain bytearray if the DMA
        # allocator / lcd_bus is unavailable (host bring-up).
        self._fb = None
        try:
            import lcd_bus
            self._fb = moy_alloc.malloc_dma(
                width * height * 2, lcd_bus.MEMORY_SPIRAM | lcd_bus.MEMORY_DMA
            )
        except Exception:
            self._fb = None
        if self._fb is None:
            self._fb = bytearray(width * height * 2)
        self._fb_mv = memoryview(self._fb)
        # DMA-capable strip buffer in INTERNAL SRAM, for small dirty-rect flushes.
        self._strip = moy_alloc.malloc_dma(width * strip_h * 2)
        # Full-frame DMA buffer in PSRAM, for whole-screen flushes in ONE transfer.
        # Multiple MicroPython-level tx_color calls glitch a few rows at each
        # boundary (the command->data transition); a single transfer lets esp_lcd
        # split the data internally with no re-issued command, so it's seamless
        # (the same reason the 128x128 moy_canvas blit is clean). The S3 can DMA
        # from PSRAM. Falls back to the strip path if the buffer can't be had.
        self._frame = None
        try:
            import lcd_bus
            self._frame = moy_alloc.malloc_dma(
                width * height * 2, lcd_bus.MEMORY_SPIRAM | lcd_bus.MEMORY_DMA
            )
        except Exception as exc:
            print("Moybyte compositor: full-frame buffer unavailable:", exc)
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
                self._fb_b = moy_alloc.malloc_dma(
                    width * height * 2, lcd_bus.MEMORY_SPIRAM | lcd_bus.MEMORY_DMA
                )
            except Exception as exc:
                print("Moybyte compositor: 2nd framebuffer unavailable, "
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
        # STREAM MODE (#41 web view): when True, flush() is a NO-OP -- the device goes
        # headless while a browser is playing (the cart still runs logic + the TeeCanvas
        # records draw commands, but neither rasterizes the panel nor pushes the SPI DMA),
        # lifting the web frame rate above the panel's render+flush ceiling. run_desktop's
        # WebView sets/clears this around ws.frame(); default False == today's behaviour.
        self.skip_flush = False
        # --- async DMA completion (real overlap, #43; see the ASYNC_FLUSH block) ---
        # `_dma_done_n` is bumped from the SPI completion ISR (GC locked) so it MUST
        # already exist as an attr slot here -- the ISR only STOREs into it, never
        # creates it (which could resize the dict = alloc = crash under the lock).
        # `_dma_target` is the band count of the in-flight front frame; the flush is
        # fully on the panel when `_dma_done_n == _dma_target`. `_async` gates every
        # async branch; it stays False (proven held-back-band path) unless ASYNC_FLUSH
        # is on, double-buffer is active, AND the bus accepts a callback.
        self._dma_done_n = 0
        self._dma_target = 0
        self._async = False
        if ASYNC_FLUSH and self.double_buffer:
            try:
                self._bus.register_callback(self._on_dma_done)
                self._async = True
            except Exception as exc:
                print("Moybyte compositor: async flush unavailable:", exc)
                self._async = False
        # PERF knob: rows per SPI transfer in the full-frame flush. Bigger = fewer
        # transfers = higher FPS, bounded by the per-band DMA bounce fitting internal
        # RAM. 48 -> 5 transfers for 240 rows (24 was 10). Tunable via _flush_rows.
        self._flush_rows = 48
        # FLUSHBRK instrumentation: count flushes so we sample 1-in-N (see the
        # FLUSH_INSTRUMENT block at module top). Lazily-bound diag/time handles so
        # the host (no moybyte_diag) and an instrument-off build pay nothing.
        self._flush_n = 0
        self._diag = None
        self._diag_tried = False
        self._diag_us = None
        self._dirty = DirtyTracker()
        # Native pixel kernel (fast, VM-neutral) when the image has it.
        try:
            import moy_gfx
            self._gfx = moy_gfx
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
        # --- SRAM-bounce banded flush state (#66; see the SRAM_BOUNCE_FLUSH block).
        # Two internal DMA bounce buffers + pre-sliced per-band views of BOTH
        # physical framebuffers (created once: pump() must not allocate on its
        # steady path, it runs from a soft-timer callback). `_bnc_total`/`_bnc_next`
        # are the in-flight frame's band bookkeeping (0/0 = idle); `_bnc_src`
        # points at the CURRENT front's band views for one flush's duration.
        # MUST init after _fb_a_mv/_fb_b_mv above (the band views slice them).
        self.bounce_flush = False
        self._bnc_total = 0
        self._bnc_next = 0
        self._bnc_src = None
        self._in_pump = False
        self._pump_timer = None
        if SRAM_BOUNCE_FLUSH and self._async:
            try:
                rows = BOUNCE_ROWS
                rb = self._row_bytes
                self._bnc_bufs = (moy_alloc.malloc_dma(width * rows * 2),
                                  moy_alloc.malloc_dma(width * rows * 2))
                self._bnc_mv = (memoryview(self._bnc_bufs[0]),
                                memoryview(self._bnc_bufs[1]))
                self._bnc_rows = rows
                nb = (height + rows - 1) // rows
                self._bnc_bands = nb

                def _band_views(mv):
                    out = []
                    for k in range(nb):
                        r = rows if (k + 1) * rows <= height else height - k * rows
                        out.append(mv[k * rows * rb:(k * rows + r) * rb])
                    return out

                self._bnc_src_a = _band_views(self._fb_a_mv)
                self._bnc_src_b = _band_views(self._fb_b_mv)
                self.bounce_flush = True
            except Exception as exc:
                print("Moybyte compositor: SRAM bounce flush unavailable:", exc)
                self.bounce_flush = False
        if self.bounce_flush and PUMP_TIMER_MS:
            try:
                from machine import Timer
                self._pump_timer = Timer(3)
                self._pump_timer.init(period=PUMP_TIMER_MS,
                                      mode=Timer.PERIODIC,
                                      callback=self._pump_cb)
            except Exception as exc:
                print("Moybyte compositor: pump timer unavailable "
                      "(drain-fallback only):", exc)
                self._pump_timer = None

    # -- introspection -------------------------------------------------------

    def framebuffer(self):
        # The current BACK buffer -- what the canvas / moy_gfx must draw into THIS
        # frame. In single-buffer mode this is always _fb (unchanged). In
        # double-buffer mode it ping-pongs, so a DeviceCanvas must re-fetch it each
        # frame (see DeviceCanvas.sync_back in moy_runtime) -- never cache it.
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
        """The native moy_gfx kernel (or None). Lets a canvas drawing into this
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
        # 8x8 framebuf font (moy_gfx native font is a Stage 3.1 follow-on).
        if self._fbuf is not None:
            self._fbuf.text(s, x, y, color)
        self._dirty.add(x, y, len(s) * 8, 8)

    def _blit_py(self, src, dx, dy, sw, sh, key):
        # Correct-but-slow fallback used only when moy_gfx is absent.
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
        # STREAM MODE (#41): headless while a browser plays -- skip the panel rasterize +
        # DMA entirely. The dirty box is cleared so the next REAL flush isn't fooled into a
        # stale region, but no SPI transfer happens (the ~20ms flush is the whole point of
        # going headless). Any DMA already in flight from the last real frame just completes.
        if self.skip_flush:
            self._dirty.clear()
            return
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
        """Queue `_front`'s bands for DMA and return so the CPU can render the next
        frame while they transfer. `_dma_front` marks which buffer is in flight so
        sync()/_drain_dma know what to wait on.

        ASYNC (a callback is registered): fire EVERY band -- tx_color queues + returns,
        no band blocks -- and record the band count in `_dma_target`. The completion
        ISR bumps `_dma_done_n`; the next _drain_dma waits until they match. `_dma_done_n`
        was zeroed by that _drain_dma before this call, so set `_dma_target` only AFTER
        firing (so a concurrent _await can't see a premature 0==0 match).

        CRITICAL (the actual overlap unlock, #43): only the FIRST band carries a command
        (RAMWR); bands 2..N are sent with cmd = -1 (NO command). esp_lcd's SPI tx_color
        QUEUES color data async, but any call that sends a command first BLOCKS until all
        previously-queued color DMA has drained (esp_lcd_panel_io_spi.c: "before issue a
        polling transaction, need to wait queued transactions finished"). Sending RAMWRC
        per band therefore serialized all 5 transfers into the kick (~17 ms, no overlap).
        With cmd = -1 the continuation bands skip that drain, so all bands queue async and
        the panel DMA runs while the CPU renders the next frame. The ST7789 keeps writing
        GRAM from data sent with D/C=data (CS framing between bands is irrelevant), so no
        RAMWRC is needed -- the CASET/RASET window + the single RAMWR set the write origin.

        NON-ASYNC fallback (no callback -> tx_color busy-waits): queue every band but
        the LAST as `last=False` and hold the final band in `_dma_pending` so the next
        flush's _drain_dma issues it `last=True` (the one busy-wait completion point).
        The window is armed once; the held-back band reuses it via RAMWRC."""
        self._set_window(0, 0, self._w - 1, self._h - 1)
        if self._async and self.bounce_flush:
            # SRAM-bounce bands (#66): arm the band bookkeeping for THIS front and
            # queue the first two bands (both bounce buffers full -> ~3ms of
            # transfer buffered). The soft pump timer feeds the rest during the
            # cart's _update/_draw; the next drain is the fallback feeder. Band 0
            # carries RAMWR (its tx_color acquires a drained bus -- guaranteed,
            # _drain_dma just ran); bands 1..N-1 go cmd=-1 = queue-only, which the
            # esp_lcd no-acquire patch makes non-blocking.
            self._bnc_src = (self._bnc_src_a if self._front is self._fb
                             else self._bnc_src_b)
            self._bnc_next = 0
            self._bnc_total = self._bnc_bands
            self.pump()
            self._dma_pending = None
            self._dma_front = self._front
            return
        mv = self._front_mv()
        rb = self._row_bytes
        rows_per = self._flush_rows
        cmd = RAMWR
        yy = 0
        h = self._h
        if self._async:
            if PSRAM_DIRECT_FLUSH:
                # ONE tx_color for the whole frame (see PSRAM_DIRECT_FLUSH block):
                # esp_lcd does a single acquire_bus (waits only for the prior frame's
                # DMA, already overlapped by render), then queues every internal chunk
                # async straight from the PSRAM buffer -- no bounce, no per-band acquire.
                # The panel DMA then overlaps the NEXT frame's render. on_color_trans_done
                # fires once at the end -> _dma_done_n reaches 1.
                self._bus.tx_color(RAMWR, mv, 0, 0, self._w - 1, self._h - 1, 0, True)
                self._dma_target = 1
            else:
                # Banded fallback: still async-queued, but esp_lcd serializes the bands
                # via per-tx_color acquire_bus (no overlap). First band RAMWR, rest cmd=-1.
                n = 0
                while yy < h:
                    rows = rows_per if yy + rows_per <= h else (h - yy)
                    last = (yy + rows >= h)
                    self._bus.tx_color(cmd, mv[yy * rb:(yy + rows) * rb],
                                       0, yy, self._w - 1, yy + rows - 1, 0, last)
                    cmd = -1
                    yy += rows
                    n += 1
                self._dma_target = n      # set AFTER firing
            self._dma_pending = None
            self._dma_front = self._front
            return
        # All bands but the last: async-queued; the last band's params are held back.
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

    # -- SRAM-bounce band pump (#66; see the SRAM_BOUNCE_FLUSH block) ---------

    def pump(self):
        """Feed the in-flight SRAM-bounce flush: copy the next band(s) of the FRONT
        into a free bounce buffer and queue them. Band k's bounce slot (k & 1) is
        free once band k-2 completed (`_dma_done_n >= k-1`), so at most two bands
        are ever in flight and the copy of one overlaps the transfer of the other.

        Called from _kick_front (first two bands), the soft pump timer (between
        the cart's bytecodes -- the only feeder during a long _update), and
        _drain_dma (the always-correct fallback). Reentrancy-guarded: a timer
        fire that lands inside a main-thread pump no-ops. Allocation-free on the
        steady path (band views are pre-sliced in __init__) EXCEPT the tx_color
        argument tuple machinery itself -- fine, soft-timer context allows alloc.
        Costs ~80us per band (15KB PSRAM->SRAM memcpy + an async queue; the
        queue never blocks thanks to the esp_lcd no-acquire patch)."""
        if self._in_pump or self._bnc_next >= self._bnc_total:
            return
        self._in_pump = True
        try:
            k = self._bnc_next
            total = self._bnc_total
            src = self._bnc_src
            rows = self._bnc_rows
            rb = self._row_bytes
            w1 = self._w - 1
            while k < total and self._dma_done_n >= k - 1:
                s = src[k]
                mv = self._bnc_mv[k & 1]
                n = len(s)
                if n == len(mv):
                    mv[:] = s          # C-level copy PSRAM -> internal SRAM
                    buf = mv
                else:                  # short final band (non-multiple heights)
                    buf = mv[:n]
                    buf[:] = s
                y = k * rows
                self._bus.tx_color(RAMWR if k == 0 else -1, buf,
                                   0, y, w1, y + (n // rb) - 1, 0, False)
                self._dma_target += 1
                k += 1
                self._bnc_next = k
        finally:
            self._in_pump = False

    def _pump_cb(self, _t):
        # Soft machine.Timer callback (esp32 Timers schedule via mp_sched -> runs
        # between bytecodes on the main thread, allocation allowed). ~2us no-op
        # when nothing is in flight.
        if self._bnc_next < self._bnc_total:
            self.pump()

    def _on_dma_done(self):
        # SPI completion ISR, GC LOCKED -- must NOT allocate. `+= 1` on a small int
        # into the pre-existing `_dma_done_n` slot is heap-free (see the ASYNC_FLUSH
        # block). Counts one finished band; the front is fully shipped when this
        # reaches `_dma_target`.
        self._dma_done_n += 1

    def _await_dma(self):
        """Spin until every fired band's completion ISR has run (`_dma_done_n` reaches
        `_dma_target`). The callback fires from the SPI ISR (not the MP scheduler), so
        a plain busy-loop sees the update. Bounded so a (never-observed) lost completion
        degrades to one possibly-torn frame instead of a permanent hang needing a
        physical reset -- the worst-case full-frame DMA is ~20 ms, far under this cap."""
        guard = 0
        while self._dma_done_n < self._dma_target:
            guard += 1
            if guard > 8000000:
                break

    def _drain_dma(self):
        """Finish the previous frame's in-flight DMA so the front buffer is fully on
        the panel and safe to reuse as the next back. No-op when nothing is in flight
        (first frame / after sync()).

        ASYNC: wait for the fired bands' completion ISRs, then reset the counters to a
        clean drained state (so the next _kick_front starts from `_dma_done_n == 0`).
        Most/all of the wait was already hidden behind the render that just ran -- this
        is only the residual (FLUSHBRK `tx`).

        NON-ASYNC: issue the held-back final band `last=True`, which busy-waits until the
        whole queued chain has drained."""
        if self._async:
            if self._bnc_total:
                # SRAM-bounce frame in flight: finish feeding it ourselves (the
                # correct fallback when the pump timer is dead or starved), then
                # wait out the tail. Most of this already happened behind render.
                guard = 0
                while (self._bnc_next < self._bnc_total
                       or self._dma_done_n < self._dma_target):
                    if self._bnc_next < self._bnc_total:
                        self.pump()
                    guard += 1
                    if guard > 1000000:   # heavier per-iter than _await_dma's spin;
                        break             # ~1s cap >> the 16ms worst-case transfer
                self._bnc_total = 0
                self._bnc_next = 0
                self._bnc_src = None
            elif self._dma_target:
                self._await_dma()
            self._dma_done_n = 0
            self._dma_target = 0
            self._dma_front = None
            return
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
        # internal headroom (see the "Moybyte mem:" boot readout).
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
        """Emit the FLUSHBRK line via moybyte_diag (live serial echo + boot dump).
        Falls back to a plain print if diag is unavailable. Fully guarded."""
        def _ms(v):
            return v / 1000.0
        msg = "copy=%.2f tx=%.2f setup=%.2f n=%d total=%.2f" % (
            _ms(copy_us), _ms(tx_us), _ms(setup_us), n, _ms(total_us))
        if not self._diag_tried:
            self._diag_tried = True
            try:
                import moybyte_diag
                self._diag = moybyte_diag
            except Exception:
                self._diag = None
        try:
            if self._diag is not None:
                self._diag.log("FLUSHBRK", msg)
                return
        except Exception:
            pass
        try:
            print("Moybyte FLUSHBRK", msg)
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
            if self._async:
                # A callback is registered, so tx_color no longer blocks -- but the
                # strip buffer is SHARED and repacked next iteration, so wait for this
                # band's DMA before reusing it. Arm the counter BEFORE firing so the
                # completion ISR can't bump it between the fire and the reset.
                self._dma_done_n = 0
                self._dma_target = 1
                self._bus.tx_color(cmd, data, x, yy, x + w - 1, yy + rows - 1, 0, last)
                self._await_dma()
            else:
                self._bus.tx_color(cmd, data, x, yy, x + w - 1, yy + rows - 1, 0, last)
            cmd = RAMWRC
            yy += rows
        if self._async:
            # Leave the counters drained so the next _flush_double's _drain_dma is a
            # no-op rather than waiting on this region's stale target.
            self._dma_done_n = 0
            self._dma_target = 0


def make_compositor(bus, width=320, height=240, strip_h=40):
    """Return a Compositor, or None when lcd_bus/hardware is unavailable."""
    if bus is None:
        return None
    try:
        return Compositor(bus, width, height, strip_h)
    except Exception as exc:
        print("Moybyte moy_compositor disabled:", exc)
        return None
