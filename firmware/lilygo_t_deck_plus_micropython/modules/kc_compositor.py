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
        try:
            import framebuf
            self._fbuf = framebuf.FrameBuffer(self._fb, width, height, framebuf.RGB565)
        except Exception:
            self._fbuf = None

    # -- introspection -------------------------------------------------------

    def framebuffer(self):
        return self._fb

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
            self._gfx.fill(self._fb, self._w * self._h, color)
        elif self._fbuf is not None:
            self._fbuf.fill(color)
        self._dirty.add(0, 0, self._w, self._h)

    def fill_rect(self, x, y, w, h, color):
        if self._gfx is not None:
            self._gfx.fill_rect(self._fb, self._w, x, y, w, h, color)
        elif self._fbuf is not None:
            self._fbuf.fill_rect(x, y, w, h, color)
        self._dirty.add(x, y, w, h)

    def blit(self, src, dx, dy, sw, sh, key=-1):
        """Blit a sw*sh RGB565 source buffer at (dx, dy); key=-1 opaque."""
        if self._gfx is not None:
            self._gfx.blit565(self._fb, self._w, self._h, dx, dy, src, sw, sh, key)
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
        fb = self._fb_mv
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
        clock (tx) vs overhead (copy/setup). The untimed path below is unchanged."""
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
        x, y, w, h = clip_rect(x, y, w, h, self._w, self._h)
        if w > 0 and h > 0:
            self._flush_region(x, y, w, h)

    def flush_dirty(self):
        """Flush only the region drawn since the last flush (the desktop path)."""
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
                gfx.pack_strip(self._fb, self._w, x, yy, w, rows, strip)
            elif full_width:
                fbrb = self._row_bytes
                strip[:nbytes] = self._fb_mv[yy * fbrb:(yy + rows) * fbrb]
            else:
                mv = self._fb_mv
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
