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
        """Flush the whole screen. One DMA transfer from the PSRAM frame buffer
        when available (seamless, no per-strip boundary glitches); otherwise the
        strip path."""
        if self._frame is not None:
            self._frame[:] = self._fb     # native-order copy; tx_color swaps _frame in place
            self._set_window(0, 0, self._w - 1, self._h - 1)
            self._bus.tx_color(RAMWR, self._frame, 0, 0, self._w - 1, self._h - 1, 0, True)
        else:
            self._flush_region(0, 0, self._w, self._h)
        self._dirty.clear()

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
