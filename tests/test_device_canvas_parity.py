"""Host == device pixel parity for the TIC-80 draw verbs (#11 cluster 2).

clip / camera / spr-flip / pal / palt all change PIXELS, so the only honest proof
that the two backends agree is to render the SAME scene through both and compare
the resulting buffers byte-for-byte. The host `runtime.canvas.Canvas` works in
palette indices; the device `moy_runtime.DeviceCanvas` works in RGB565 over the
native `moy_gfx` C kernel + `framebuf`.

This module makes `DeviceCanvas` runnable under CPython by injecting:
  * a pure-Python `framebuf.FrameBuffer` stub (fill / fill_rect / pixel / line /
    text / rect over an RGB565 bytearray), and
  * a pure-Python `moy_gfx` stub that PORTS the C kernel logic (modmoy_gfx.c:
    fill / fill_rect / blit565 / blit_map, including the new optional clip args)
    line-for-line -- so this doubles as the cross-check of the C kernel against the
    host rasterizer that #11 asks for. (The real C kernel is compiled into the
    firmware and can't run here; keeping the Python port a faithful transcription is
    what makes the parity meaningful, and is flagged UNVERIFIED-ON-DEVICE.)

Host indices resolve to RGB565 via the SAME formula the device's PAL565 table was
generated with (asserted equal in test_pal565_matches_host_palette), so a host
buffer mapped through rgb565() is directly comparable to the device buffer.

Text (`print`) joined the parity suite with #62: the device's native moy_gfx.text
rasterizes the SAME petme128 glyph blob (runtime/font.py, staged as moy_font) the
host draws from, with camera + clip + pal honoured in C. The no-gfx fallback
(framebuf.text -- same glyphs, screen-bounds clip only) matches except under an
active clip rect, so the fallback tests avoid clip().
"""

import sys
import types
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import font as _host_font  # noqa: E402
from runtime import palette  # noqa: E402
from runtime.canvas import Canvas, Image  # noqa: E402
from runtime.editors import SpriteSheet, TileMap  # noqa: E402

DEV = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"


def rgb565(rgb):
    r, g, b = rgb
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# --------------------------------------------------------------------------- #
# Pure-Python moy_gfx -- a faithful transcription of native/moy_gfx/modmoy_gfx.c. #
# --------------------------------------------------------------------------- #
class _FakeGfx:
    @staticmethod
    def fill(buf, npix, color):
        mv = memoryview(buf).cast("H")
        cap = len(mv)
        n = npix
        if n < 0:
            n = 0
        if n > cap:
            n = cap
        c = color & 0xFFFF
        for i in range(n):
            mv[i] = c

    @staticmethod
    def fill_rect(buf, stride, x, y, w, h, color):
        mv = memoryview(buf).cast("H")
        cap = len(mv)
        c = color & 0xFFFF
        if stride <= 0:
            return
        if x < 0:
            w += x
            x = 0
        if y < 0:
            h += y
            y = 0
        if x >= stride:
            return
        if x + w > stride:
            w = stride - x
        if w <= 0 or h <= 0:
            return
        max_rows = cap // stride
        if y >= max_rows:
            return
        if y + h > max_rows:
            h = max_rows - y
        for row in range(h):
            base = (y + row) * stride + x
            for col in range(w):
                mv[base + col] = c

    @staticmethod
    def blit565(dst, dw, dh, dx, dy, src, sw, sh, key,
                cx0=None, cy0=None, cx1=None, cy1=None):
        d = memoryview(dst).cast("H")
        s = memoryview(src).cast("H")
        dcap = len(d)
        scap = len(s)
        if dw <= 0 or dh <= 0 or sw <= 0 or sh <= 0:
            return
        if dw * dh > dcap:
            dh = dcap // dw
        if sw * sh > scap:
            sh = scap // sw
        cx0 = 0 if cx0 is None else cx0
        cy0 = 0 if cy0 is None else cy0
        cx1 = dw if cx1 is None else cx1
        cy1 = dh if cy1 is None else cy1
        if cx0 < 0:
            cx0 = 0
        if cy0 < 0:
            cy0 = 0
        if cx1 > dw:
            cx1 = dw
        if cy1 > dh:
            cy1 = dh
        for row in range(sh):
            ty = dy + row
            if ty < cy0 or ty >= cy1:
                continue
            srow = row * sw
            drow = ty * dw
            for col in range(sw):
                tx = dx + col
                if tx < cx0 or tx >= cx1:
                    continue
                p = s[srow + col]
                if key >= 0 and p == (key & 0xFFFF):
                    continue
                d[drow + tx] = p

    @staticmethod
    def blit_map(dst, dw, dh, sx, sy, cells, map_w, map_h, mx, my, rw, rh,
                 atlas, ntiles, tile, scale, key,
                 cx0=None, cy0=None, cx1=None, cy1=None):
        d = memoryview(dst).cast("H")
        a = memoryview(atlas).cast("H")
        dcap = len(d)
        acap = len(a)
        ccap = len(cells)
        if dw <= 0 or dh <= 0 or map_w <= 0 or map_h <= 0 or tile <= 0 or ntiles <= 0:
            return
        if scale < 1:
            scale = 1
        cx0 = 0 if cx0 is None else cx0
        cy0 = 0 if cy0 is None else cy0
        cx1 = dw if cx1 is None else cx1
        cy1 = dh if cy1 is None else cy1
        if cx0 < 0:
            cx0 = 0
        if cy0 < 0:
            cy0 = 0
        if cx1 > dw:
            cx1 = dw
        if cy1 > dh:
            cy1 = dh
        tpx = tile * tile
        if ntiles * tpx > acap:
            return
        step = tile * scale
        for cy in range(rh):
            myy = my + cy
            if myy < 0 or myy >= map_h:
                continue
            dy0 = sy + cy * step
            for cx in range(rw):
                mxx = mx + cx
                if mxx < 0 or mxx >= map_w:
                    continue
                ci = myy * map_w + mxx
                if ci >= ccap:
                    continue
                v = cells[ci]
                if v == 0:
                    continue
                tid = v - 1
                if tid >= ntiles:
                    continue
                tsrc = tid * tpx
                dx0 = sx + cx * step
                for row in range(tile):
                    srow = tsrc + row * tile
                    for sub_y in range(scale):
                        ty = dy0 + row * scale + sub_y
                        if ty < cy0 or ty >= cy1:
                            continue
                        drow = ty * dw
                        for col in range(tile):
                            p = a[srow + col]
                            if key >= 0 and p == (key & 0xFFFF):
                                continue
                            bx = dx0 + col * scale
                            for sub_x in range(scale):
                                tx = bx + sub_x
                                if tx < cx0 or tx >= cx1:
                                    continue
                                d[drow + tx] = p

    @staticmethod
    def blit_batch(dst, dw, dh, items, atlas, ntiles, tile, scale, key,
                   cam_x, cam_y, cx0, cy0, cx1, cy1):
        # #43/#63: draw a run of sheet tiles from an RGB565 atlas in one call -- a
        # faithful transcription of moy_gfx_blit_batch in modmoy_gfx.c, so the
        # auto-batch path is cross-checked against the host rasterizer. Like the C,
        # `items` is EITHER a list of (tile, x, y[, flip]) tuples OR the canvas
        # batch array('h') [next, ck, scale, token, (tile x y flip)*N...] (#63
        # spr_gate array mode) -- detected the same way (buffer protocol).
        d = memoryview(dst).cast("H")
        a = memoryview(atlas).cast("H")
        acap = len(a)
        if dw <= 0 or dh <= 0 or tile <= 0 or ntiles <= 0:
            return
        if scale < 1:
            scale = 1
        if cx0 < 0:
            cx0 = 0
        if cy0 < 0:
            cy0 = 0
        if cx1 > dw:
            cx1 = dw
        if cy1 > dh:
            cy1 = dh
        tpx = tile * tile
        if ntiles * tpx > acap:
            return
        try:
            q = memoryview(items).cast("B").cast("h")   # array mode (int16 quads)
            nxt = max(4, min(q[0], len(q)))
            items = [(q[i], q[i + 1], q[i + 2], q[i + 3])
                     for i in range(4, nxt, 4)]
        except TypeError:
            pass                                        # classic list-of-tuples mode
        for it in items:
            if len(it) < 3:
                continue
            tid = int(it[0])
            if tid < 0 or tid >= ntiles:
                continue
            dx0 = int(it[1]) - cam_x
            dy0 = int(it[2]) - cam_y
            flip = int(it[3]) if len(it) > 3 else 0
            fx = flip & 1
            fy = (flip >> 1) & 1
            tsrc = tid * tpx
            for row in range(tile):
                ssy = (tile - 1 - row) if fy else row
                srow = tsrc + ssy * tile
                for sub_y in range(scale):
                    ty = dy0 + row * scale + sub_y
                    if ty < cy0 or ty >= cy1:
                        continue
                    drow = ty * dw
                    for col in range(tile):
                        ssx = (tile - 1 - col) if fx else col
                        p = a[srow + ssx]
                        if key >= 0 and p == (key & 0xFFFF):
                            continue
                        bx = dx0 + col * scale
                        for sub_x in range(scale):
                            tx = bx + sub_x
                            if tx < cx0 or tx >= cx1:
                                continue
                            d[drow + tx] = p

    @staticmethod
    def blit_window(dst, dw, dh, src, src_w, sx, sy):
        # #54 scroll engine: copy a dw x dh window of `src` (a wider pre-rendered
        # background, stride src_w) at (sx, sy) into `dst` (stride dw, contiguous) --
        # a faithful transcription of moy_gfx_blit_window in modmoy_gfx.c.
        d = memoryview(dst).cast("H")
        s = memoryview(src).cast("H")
        dcap = len(d)
        scap = len(s)
        if dw <= 0 or dh <= 0 or src_w <= 0:
            return
        if sx < 0:
            sx = 0
        if sy < 0:
            sy = 0
        if sx + dw > src_w:                       # clamp window to source width
            dw = src_w - sx
        if dw <= 0:
            return
        if dw * dh > dcap:                        # dst guard
            dh = dcap // dw
        src_rows = scap // src_w
        if sy + dh > src_rows:                    # src guard
            dh = src_rows - sy
        if dh <= 0:
            return
        for row in range(dh):
            d0 = row * dw
            s0 = (sy + row) * src_w + sx
            for col in range(dw):
                d[d0 + col] = s[s0 + col]

    @staticmethod
    def blit_indices(dst, dw, dh, dx, dy, indices, iw, ih, pal565):
        # #63 Fold 3: place an iw x ih palette-INDEX bitmap at (dx, dy), converting each
        # index -> RGB565 via pal565 -- a faithful transcription of moy_gfx_blit_indices
        # in modmoy_gfx.c (opaque; index past the palette is skipped; clamped to dst).
        # The C reads `indices` and `pal565` via the BUFFER PROTOCOL, so memoryview them
        # HERE too -- a tuple/list palette (the "object with buffer protocol required"
        # crash) then fails in this mirror exactly as it does on device, not silently.
        d = memoryview(dst).cast("H")
        iv = memoryview(indices)                  # uint8 index bytes -- buffer required
        # uint16 palette -- buffer required (the C reinterprets its bytes as uint16). Cast
        # via 'B' so it works whether pal565 is an array('H') (what the device passes) or
        # raw 565 bytes; a tuple/list has no buffer protocol and fails here, as on device.
        pv = memoryview(pal565).cast("B").cast("H")
        dcap = len(d)
        pcap = len(pv)
        icap = len(iv)
        if dw <= 0 or dh <= 0 or iw <= 0 or ih <= 0 or pcap == 0:
            return
        if dw * dh > dcap:
            dh = dcap // dw
        for row in range(ih):
            ty = dy + row
            if ty < 0 or ty >= dh:
                continue
            srow = row * iw
            drow = ty * dw
            for col in range(iw):
                tx = dx + col
                if tx < 0 or tx >= dw:
                    continue
                si = srow + col
                if si >= icap:
                    continue
                p = iv[si]
                if p >= pcap:
                    continue
                d[drow + tx] = pv[p]

    @staticmethod
    def _clip(dw, cap, cx0, cy0, cx1, cy1):
        max_rows = cap // dw
        if cx0 < 0:
            cx0 = 0
        if cy0 < 0:
            cy0 = 0
        if cx1 > dw:
            cx1 = dw
        if cy1 > max_rows:
            cy1 = max_rows
        return cx0, cy0, cx1, cy1

    @staticmethod
    def _put(d, dw, x, y, col, cam_x, cam_y, cx0, cy0, cx1, cy1):
        x -= cam_x
        y -= cam_y
        if x < cx0 or x >= cx1 or y < cy0 or y >= cy1:
            return
        d[y * dw + x] = col

    @staticmethod
    def circ(dst, dw, dh, cx, cy, r, color, cam_x, cam_y, cx0, cy0, cx1, cy1):
        d = memoryview(dst).cast("H")
        if dw <= 0 or r < 0:
            return
        cx0, cy0, cx1, cy1 = _FakeGfx._clip(dw, len(d), cx0, cy0, cx1, cy1)
        col = color & 0xFFFF
        for dy in range(-r, r + 1):
            span = int((r * r - dy * dy) ** 0.5)
            y = cy + dy - cam_y
            if y < cy0 or y >= cy1:
                continue
            x0 = cx - span - cam_x
            x1 = x0 + 2 * span + 1
            if x0 < cx0:
                x0 = cx0
            if x1 > cx1:
                x1 = cx1
            base = y * dw
            for x in range(x0, x1):
                d[base + x] = col

    @staticmethod
    def circb(dst, dw, dh, cx, cy, r, color, cam_x, cam_y, cx0, cy0, cx1, cy1):
        d = memoryview(dst).cast("H")
        if dw <= 0 or r < 0:
            return
        cx0, cy0, cx1, cy1 = _FakeGfx._clip(dw, len(d), cx0, cy0, cx1, cy1)
        col = color & 0xFFFF
        put = _FakeGfx._put
        x = r
        y = 0
        err = 0
        while x >= y:
            for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
                put(d, dw, cx + px, cy + py, col, cam_x, cam_y, cx0, cy0, cx1, cy1)
            y += 1
            if err <= 0:
                err += 2 * y + 1
            else:
                x -= 1
                err -= 2 * x + 1

    @staticmethod
    def line(dst, dw, dh, x0, y0, x1, y1, color, cam_x, cam_y, cx0, cy0, cx1, cy1):
        d = memoryview(dst).cast("H")
        if dw <= 0:
            return
        cx0, cy0, cx1, cy1 = _FakeGfx._clip(dw, len(d), cx0, cy0, cx1, cy1)
        col = color & 0xFFFF
        put = _FakeGfx._put
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            put(d, dw, x0, y0, col, cam_x, cam_y, cx0, cy0, cx1, cy1)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    @staticmethod
    def text(dst, dw, dh, s, x, y, color, font, first, scale,
             cam_x, cam_y, cx0, cy0, cx1, cy1):
        # Faithful transcription of moy_gfx_text in modmoy_gfx.c (#62): walk the
        # string as bytes, each glyph 8 column-bytes (LSB = top row), each set bit
        # a scale x scale block of `color`, camera-offset and clip-bounded.
        d = memoryview(dst).cast("H")
        fb = bytes(font) if not isinstance(font, (bytes, bytearray)) else font
        nglyphs = len(fb) // 8
        if dw <= 0 or nglyphs <= 0:
            return
        if scale < 1:
            scale = 1
        cx0, cy0, cx1, cy1 = _FakeGfx._clip(dw, len(d), cx0, cy0, cx1, cy1)
        col = color & 0xFFFF
        x -= cam_x
        y -= cam_y
        adv = 8 * scale
        if y >= cy1 or y + adv <= cy0:
            return
        for ch in s.encode("utf-8") if isinstance(s, str) else bytes(s):
            if x >= cx1:
                break
            if x + adv <= cx0:
                x += adv
                continue
            gi = ch - first
            if gi < 0 or gi >= nglyphs:
                gi = 0
            g = fb[gi * 8:gi * 8 + 8]
            for j in range(8):
                bits = g[j]
                if bits == 0:
                    continue
                bx = x + j * scale
                if bx >= cx1 or bx + scale <= cx0:
                    continue
                row = 0
                while bits:
                    if bits & 1:
                        by = y + row * scale
                        for sub_y in range(scale):
                            ty = by + sub_y
                            if ty < cy0 or ty >= cy1:
                                continue
                            base = ty * dw
                            for sub_x in range(scale):
                                tx = bx + sub_x
                                if tx < cx0 or tx >= cx1:
                                    continue
                                d[base + tx] = col
                    bits >>= 1
                    row += 1
            x += adv


# --------------------------------------------------------------------------- #
# Pure-Python framebuf.FrameBuffer stub (RGB565 over a bytearray).            #
# --------------------------------------------------------------------------- #
class _FB:
    RGB565 = 1

    def __init__(self, buf, w, h, fmt=1):
        self._mv = memoryview(buf).cast("H")
        self._w = w
        self._h = h

    def fill(self, col):
        c = col & 0xFFFF
        for i in range(len(self._mv)):
            self._mv[i] = c

    def fill_rect(self, x, y, w, h, col):
        c = col & 0xFFFF
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(self._w, x + w)
        y1 = min(self._h, y + h)
        for yy in range(y0, y1):
            base = yy * self._w
            for xx in range(x0, x1):
                self._mv[base + xx] = c

    def pixel(self, x, y, col=None):
        if not (0 <= x < self._w and 0 <= y < self._h):
            return 0
        if col is None:
            return self._mv[y * self._w + x]
        self._mv[y * self._w + x] = col & 0xFFFF

    def rect(self, x, y, w, h, col):
        self.fill_rect(x, y, w, 1, col)
        self.fill_rect(x, y + h - 1, w, 1, col)
        self.fill_rect(x, y, 1, h, col)
        self.fill_rect(x + w - 1, y, 1, h, col)

    def text(self, s, x, y, col):
        # Real framebuf.text: the SAME petme128 glyphs, clipped to the buffer
        # bounds only (no clip-rect awareness) -- the no-gfx fallback's behaviour.
        c = col & 0xFFFF
        mv = self._mv
        w = self._w
        h = self._h

        def put(px, py):
            if 0 <= px < w and 0 <= py < h:
                mv[py * w + px] = c

        _host_font.draw(put, s, x, y)


class _FakeFramebuf(types.ModuleType):
    RGB565 = 1
    FrameBuffer = _FB

    def __init__(self):
        super().__init__("framebuf")
        self.RGB565 = 1
        self.FrameBuffer = _FB


class _FakeComp:
    """Stands in for moy_compositor: an RGB565 buffer + the native gfx kernel."""

    def __init__(self, w, h):
        self._w = w
        self._h = h
        self._buf = bytearray(w * h * 2)
        self._gfx = _FakeGfx()

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        return self._gfx


def _load_device_canvas():
    # Inject the fake framebuf, then load moy_runtime's DeviceCanvas under CPython.
    sys.modules["framebuf"] = _FakeFramebuf()
    for name in ("editors", "audio", "console"):
        spec = importlib.util.spec_from_file_location(name, ROOT / "runtime" / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sys.modules[name] = mod
    # runtime/font.py is staged as the frozen `moy_font` by build.sh (#62); inject
    # it under that name so the native-text path activates here too.
    sys.modules["moy_font"] = _host_font
    # DeviceCanvas + PAL565 + Image + _USE_GFX now live in device_canvas.py
    # (extracted from moy_runtime.py); it imports the leaf device_util.
    du = importlib.util.spec_from_file_location("device_util", DEV / "device_util.py")
    dumod = importlib.util.module_from_spec(du)
    du.loader.exec_module(dumod)
    sys.modules["device_util"] = dumod
    spec = importlib.util.spec_from_file_location("device_canvas", DEV / "device_canvas.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # SpriteSheet lives in editors (the shared core); expose it for the parity
    # tests that build sheets against the device canvas.
    m.SpriteSheet = sys.modules["editors"].SpriteSheet
    return m


W, H = 64, 48


def _host_rgb565(cv):
    """Resolve a host indexed Canvas buffer to a flat list of RGB565 ints."""
    pal = [rgb565(c) for c in cv.palette]
    return [pal[i] for i in cv.buf]


def _dev_rgb565(dc):
    # The device writes pixels in PANEL byte order (moy_runtime.PAL565_SW, #43 -- the
    # CPU byte-swap is folded into the LUT so the lcd_bus per-flush swap can be off).
    # Swap back here so the comparison is against the canonical little-endian RGB565
    # the host produces. (PAL565 itself stays canonical -- test_pal565_matches_host.)
    return [((v << 8) | (v >> 8)) & 0xFFFF for v in memoryview(dc._buf).cast("H")]


def _both(use_gfx=True):
    """A fresh (host Canvas, device DeviceCanvas) pair of the same size."""
    m = _load_device_canvas()
    m._USE_GFX = use_gfx
    host = Canvas(W, H)
    dev = m.DeviceCanvas(_FakeComp(W, H))
    return m, host, dev


def _assert_same(host, dev, label=""):
    h = _host_rgb565(host)
    d = _dev_rgb565(dev)
    assert len(h) == len(d) == W * H
    if h != d:
        diff = sum(1 for x, y in zip(h, d) if x != y)
        first = next(i for i, (x, y) in enumerate(zip(h, d)) if x != y)
        raise AssertionError(
            "%s: host != device in %d/%d px (first at %d,%d: host=%#06x dev=%#06x)"
            % (label, diff, W * H, first % W, first // W, h[first], d[first]))


# --------------------------------------------------------------------------- #
# Sanity: the device PAL565 table equals rgb565(host MOY64).                  #
# --------------------------------------------------------------------------- #
def test_pal565_matches_host_palette():
    m = _load_device_canvas()
    assert len(m.PAL565) == 64
    for i, c in enumerate(palette.MOY64):
        assert m.PAL565[i] == rgb565(c), i


# --------------------------------------------------------------------------- #
# Baseline: plain primitives already match (regression guard for #11 edits).  #
# --------------------------------------------------------------------------- #
def _draw_baseline(api_or_canvas):
    c = api_or_canvas
    c.cls(1)
    c.rect(5, 5, 20, 10, 8)
    c.rectb(30, 6, 12, 12, 11)
    c.circ(40, 30, 7, 12)
    c.circb(15, 35, 6, 14)
    c.line(2, 2, 60, 44, 7)
    c.pix(50, 4, 10)


def test_baseline_primitives_match():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        _draw_baseline(host)
        _draw_baseline(dev)
        _assert_same(host, dev, "baseline gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# camera: a draw offset applied to every primitive.                          #
# --------------------------------------------------------------------------- #
def test_camera_offsets_all_primitives():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.camera(10, -5)
            c.rect(12, 12, 8, 8, 8)
            c.circ(40, 30, 5, 11)
            c.line(0, 0, 40, 40, 7)
            c.pix(20, 20, 10)
        _assert_same(host, dev, "camera gfx=%s" % gfx)


def test_camera_reset_restores_origin():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.camera(20, 20)
            c.rect(0, 0, 5, 5, 8)
            c.camera()                 # reset to (0,0)
            c.rect(0, 0, 5, 5, 11)
        _assert_same(host, dev, "camera-reset gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# clip: pixels outside the rect are suppressed; no-arg resets to full screen. #
# --------------------------------------------------------------------------- #
def test_clip_suppresses_out_of_rect_and_resets():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.clip(16, 16, 16, 16)
            c.rect(0, 0, W, H, 8)       # would fill everything; clipped to the rect
            c.circ(20, 20, 30, 11)      # huge circle, clipped
            c.line(0, 0, W, H, 7)
            c.clip()                    # reset
            c.rect(0, 0, 4, 4, 14)      # now draws (top-left, outside old clip)
        _assert_same(host, dev, "clip gfx=%s" % gfx)


def test_clip_with_camera_combines():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.camera(-8, -8)            # shift world right/down
            c.clip(10, 10, 20, 20)      # clip is screen space (post-camera)
            c.rect(0, 0, W, H, 9)
        _assert_same(host, dev, "clip+camera gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# print (#62): native moy_gfx.text vs host petme128 -- pixel parity at last.  #
# --------------------------------------------------------------------------- #
def test_text_parity_native_and_fallback():
    # Plain + edge-overhanging text matches through BOTH device paths: the native
    # moy_gfx.text kernel (gfx=True) and the framebuf.text fallback (gfx=False --
    # same glyphs, buffer-bounds clip). The legacy per-call scale arg is ignored
    # by both backends (pass 3 to prove it).
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.print("Hi moy!", 2, 3, 12)
            c.print("EDGE", 44, 20, 7, 3)     # runs off the right edge; scale ignored
            c.print("LOW", -5, H - 4, 9)      # off left + bottom
        _assert_same(host, dev, "text gfx=%s" % gfx)


def test_text_camera_pal_parity():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(1)
            c.pal(12, 9)                # text colour remaps through pal
            c.camera(6, -2)
            c.print("CAM", 10, 10, 12)
            c.camera()
            c.pal()
        _assert_same(host, dev, "text camera+pal gfx=%s" % gfx)


def test_text_clip_rect_native():
    # Arbitrary-rect clipping of text is the native kernel's NEW power (#62) --
    # framebuf.text can't do it, so this case is gfx=True only (the documented
    # fallback limitation).
    m, host, dev = _both(True)
    for c in (host, dev):
        c.cls(0)
        c.clip(12, 8, 24, 12)
        c.print("CLIPPED WIDE", 0, 6, 14)   # crosses the clip rect on all sides
        c.print("BELOW", 14, 30, 8)         # entirely outside -> nothing
        c.clip()
    _assert_same(host, dev, "text clip gfx=True")


# --------------------------------------------------------------------------- #
# spr flip: h / v / both mirror the sprite pixels (host == device).          #
# --------------------------------------------------------------------------- #
def _flip_image():
    # An asymmetric 4x4 sprite so every flip is distinguishable.
    rows = [
        "AB..",
        "C...",
        "....",
        "...D",
    ]
    return Image.from_ascii(rows, {"A": 8, "B": 9, "C": 10, "D": 11})


def test_spr_flip_h_v_both_match_host():
    for gfx in (True, False):
        for flip in (0, 1, 2, 3):
            m, host, dev = _both(gfx)
            img_h = _flip_image()
            img_d = m.Image.from_ascii(["AB..", "C...", "....", "...D"],
                                       {"A": 8, "B": 9, "C": 10, "D": 11})
            host.cls(0)
            dev.cls(0)
            host.spr(img_h, 20, 20, 1, flip)
            dev.spr(img_d, 20, 20, 1, flip)
            _assert_same(host, dev, "flip=%d gfx=%s" % (flip, gfx))


def test_spr_flip_scaled_matches_host():
    for gfx in (True, False):
        for flip in (1, 2, 3):
            m, host, dev = _both(gfx)
            img_h = _flip_image()
            img_d = m.Image.from_ascii(["AB..", "C...", "....", "...D"],
                                       {"A": 8, "B": 9, "C": 10, "D": 11})
            host.cls(0)
            dev.cls(0)
            host.spr(img_h, 8, 8, 3, flip)
            dev.spr(img_d, 8, 8, 3, flip)
            _assert_same(host, dev, "flip-scaled=%d gfx=%s" % (flip, gfx))


def test_spr_flip_clipped_matches_host():
    # Flip + clip + camera together through the native blit (clip args to the kernel).
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        img_h = _flip_image()
        img_d = m.Image.from_ascii(["AB..", "C...", "....", "...D"],
                                   {"A": 8, "B": 9, "C": 10, "D": 11})
        for c, img in ((host, img_h), (dev, img_d)):
            c.cls(0)
            c.camera(2, 2)
            c.clip(20, 20, 6, 6)
            c.spr(img, 18, 18, 2, 3)
        _assert_same(host, dev, "flip+clip gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# pal / palt: draw-time index remap + sprite transparency.                   #
# --------------------------------------------------------------------------- #
def test_pal_remap_matches_host_for_primitives():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.pal(8, 11)                # draw "8" as "11"
            c.rect(4, 4, 10, 10, 8)     # -> appears as 11
            c.circ(40, 30, 6, 8)        # -> 11
            c.pix(2, 2, 8)              # -> 11
            c.pal()                     # reset
            c.rect(20, 20, 6, 6, 8)     # -> stays 8
        _assert_same(host, dev, "pal gfx=%s" % gfx)


def test_pal_remap_matches_host_for_sprites():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        img_h = _flip_image()
        img_d = m.Image.from_ascii(["AB..", "C...", "....", "...D"],
                                   {"A": 8, "B": 9, "C": 10, "D": 11})
        for c, img in ((host, img_h), (dev, img_d)):
            c.cls(0)
            c.pal(8, 14)                # recolour the sprite's "8" pixels to 14
            c.spr(img, 16, 16, 2)
        _assert_same(host, dev, "pal-spr gfx=%s" % gfx)


def test_palt_transparency_matches_host():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        img_h = _flip_image()
        img_d = m.Image.from_ascii(["AB..", "C...", "....", "...D"],
                                   {"A": 8, "B": 9, "C": 10, "D": 11})
        for c, img in ((host, img_h), (dev, img_d)):
            c.cls(3)                    # background so transparency is visible
            c.palt(9, True)             # make index 9 ("B") transparent
            c.spr(img, 16, 16, 2)
        _assert_same(host, dev, "palt gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# map: camera + clip on the tilemap blit (one native call vs per-tile spr).   #
# --------------------------------------------------------------------------- #
def _tilemap_scene(c, sheet, tm):
    c.cls(0)
    c.camera(4, 4)
    c.clip(10, 10, 30, 24)
    c.map(tm, sheet, 0, 0, 4, 4, 0, 0, -1, 2)


def test_map_camera_clip_matches_host():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        # Build a small sheet + tilemap (host + device share editors.py classes).
        sheet_h = SpriteSheet(4, 4)
        sheet_d = m.SpriteSheet(4, 4)
        for sh in (sheet_h, sheet_d):
            sh.tset(1, 0, 0, 8)
            sh.tset(1, 3, 3, 11)
            sh.tset(2, 1, 1, 14)
        tm_h = TileMap(4, 4)
        tm_d = TileMap(4, 4)
        for tm in (tm_h, tm_d):
            tm.mset(0, 0, 1)
            tm.mset(2, 1, 2)
            tm.mset(3, 3, 1)
        _tilemap_scene(host, sheet_h, tm_h)
        _tilemap_scene(dev, sheet_d, tm_d)
        _assert_same(host, dev, "map gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# scroll engine (#54): pre-render a wider layer, window-copy it to the screen. #
# --------------------------------------------------------------------------- #
def _layer_scene(L):
    # A distinctive scene spanning the whole (wider-than-screen) layer, drawn with
    # verbs both Canvas and DeviceCanvas implement, so the window copy carries real
    # variety across the seam.
    L.cls(2)
    L.rect(4, 4, 30, 20, 8)
    L.circ(W, H // 2, 15, 11)
    L.circb(W // 2, 10, 8, 14)
    L.line(0, 0, W * 2, H, 7)
    L.pix(W + 5, 5, 10)


def test_scroll_layer_window_copy_matches_host():
    # new_layer + blit_window_from: pre-render the same scene into a wider layer on
    # both backends, then window-copy at a range of camera offsets (including the
    # right-edge clamp where dw is reduced) and assert the screens match pixel-for-
    # pixel. Host copies palette indices; device uses moy_gfx.blit_window (gfx=True) or
    # the memoryview fallback (gfx=False) over RGB565 -- all three must agree.
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        lh = host.new_layer(W * 2, H + 10)
        ld = dev.new_layer(W * 2, H + 10)
        assert (lh.w, lh.h) == (W * 2, H + 10)
        assert (ld.w, ld.h) == (W * 2, H + 10)
        _layer_scene(lh)
        _layer_scene(ld)
        for cam in ((0, 0), (W, 5), (W // 2, 8), (W + 17, 3), (W * 3, H * 3)):
            host.cls(0)
            dev.cls(0)
            host.blit_window_from(lh, cam[0], cam[1])
            dev.blit_window_from(ld, cam[0], cam[1])
            _assert_same(host, dev, "scroll gfx=%s cam=%s" % (gfx, cam))


# --------------------------------------------------------------------------- #
# blit_indices (#63 Fold 3): bake a palette-index bitmap into the framebuffer. #
# --------------------------------------------------------------------------- #
def test_blit_indices_matches_host():
    # Place an index bitmap (a paint-app image) at a range of offsets -- including
    # negative and past the right/bottom edge (clamped), plus an index past the palette
    # (skipped, leaves the background) -- on both backends. Host writes indices; device
    # converts index -> RGB565 via moy_gfx.blit_indices (gfx=True) or the memoryview
    # fallback (gfx=False). All three must agree pixel-for-pixel.
    iw, ih = 20, 12
    img = bytearray(iw * ih)
    for row in range(ih):
        for col in range(iw):
            img[row * iw + col] = (row * 3 + col) % 63      # valid MOY64 indices 0..62
    img[0] = 99                                             # past the palette -> skipped
    img[iw + 1] = 63
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for pos in ((10, 8), (0, 0), (-5, -4), (W - 6, H - 3), (-100, 50), (W + 5, 2)):
            host.cls(5)
            dev.cls(5)
            host.blit_indices(img, iw, ih, pos[0], pos[1])
            dev.blit_indices(img, iw, ih, pos[0], pos[1])
            _assert_same(host, dev, "blit_indices gfx=%s pos=%s" % (gfx, pos))


# --------------------------------------------------------------------------- #
# spr(paint image) (#63 Fold 3): a big MOY64 index bitmap placed 1:1. The device #
# bakes index->565 ONCE via blit_indices (not per-pixel), then blit565s -- and must #
# match the host index-space spr AND the raw blit_indices, pixel-for-pixel.        #
# --------------------------------------------------------------------------- #
def _paint_image(mk, iw, ih):
    idx = bytearray(iw * ih)
    for r in range(ih):
        for c in range(iw):
            idx[r * iw + c] = (r * 5 + c * 3) % 63       # valid MOY64 indices
    im = mk(iw, ih, idx, -1)
    im._paint = True                                     # tags the blit_indices fast path
    return im


def test_spr_paint_image_matches_host():
    iw, ih = 30, 20
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        hi = _paint_image(lambda w, h, p, t: Image(w, h, p, t), iw, ih)
        di = _paint_image(lambda w, h, p, t: m.Image(w, h, p, t), iw, ih)
        # Place it at a few offsets including partly off-screen (clamped) + inside a clip.
        for pos in ((5, 4), (0, 0), (W - 8, H - 6), (-6, -3)):
            host.cls(3)
            dev.cls(3)
            host.spr(hi, pos[0], pos[1])
            dev.spr(di, pos[0], pos[1])
            _assert_same(host, dev, "spr(paint) gfx=%s pos=%s" % (gfx, pos))
        # The device path baked the index->565 buffer ONCE via blit_indices (gfx only).
        if gfx:
            assert getattr(di, "_rgb_i", None) is not None, "no blit_indices bake cache"


def test_spr_paint_image_into_layer_matches_host():
    # The clean full-screen-background path: spr(bg, 0, 0) into a make_layer once, then
    # draw_layer per frame -- the device bakes the paint image into the layer buffer via
    # blit_indices. Host copies indices; both must agree after the window copy.
    iw, ih = W, H
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        hi = _paint_image(lambda w, h, p, t: Image(w, h, p, t), iw, ih)
        di = _paint_image(lambda w, h, p, t: m.Image(w, h, p, t), iw, ih)
        lh = host.new_layer(W, H)
        ld = dev.new_layer(W, H)
        lh.spr(hi, 0, 0)                                 # bake the paint bg into the layer
        ld.spr(di, 0, 0)
        host.cls(0)
        dev.cls(0)
        host.blit_window_from(lh, 0, 0)
        dev.blit_window_from(ld, 0, 0)
        _assert_same(host, dev, "paint-in-layer gfx=%s" % gfx)


# --------------------------------------------------------------------------- #
# blit_strip (#43 chrome cache): stamp a SMALLER layer at a fixed offset.      #
# --------------------------------------------------------------------------- #
def _strip_scene(L):
    # A distinctive scene filling a short, full-width strip (the top-bar shape).
    L.cls(1)
    L.rect(0, 0, L.w, 1, 8)
    L.rect(2, 2, 10, 6, 11)
    L.circ(L.w - 6, 4, 3, 14)
    L.line(0, 0, L.w - 1, L.h - 1, 7)
    L.pix(20, 3, 10)


def test_blit_strip_matches_host():
    # new_layer + blit_strip: render the same strip scene into a SHORT, full-width layer
    # on both backends, then stamp it at a range of offsets (including off-screen ones the
    # C kernel clamps) and assert the screens match pixel-for-pixel. Host copies palette
    # indices; device uses moy_gfx.blit565 with key=-1 (gfx=True) or the memoryview fallback
    # (gfx=False) over RGB565 -- all three agree. This is the cross-backend proof the
    # cached top-bar strip lands identically everywhere.
    STRIP_H = 8
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        lh = host.new_layer(W, STRIP_H)
        ld = dev.new_layer(W, STRIP_H)
        assert (lh.w, lh.h) == (W, STRIP_H)
        assert (ld.w, ld.h) == (W, STRIP_H)
        _strip_scene(lh)
        _strip_scene(ld)
        for pos in ((0, 0), (0, H - STRIP_H), (5, 10), (-4, 2), (W - 6, 3), (0, H + 4)):
            host.cls(0)
            dev.cls(0)
            host.blit_strip(lh, pos[0], pos[1])
            dev.blit_strip(ld, pos[0], pos[1])
            _assert_same(host, dev, "strip gfx=%s pos=%s" % (gfx, pos))


# --------------------------------------------------------------------------- #
# spr_batch: the native moy_gfx.blit_batch collapses N sheet tiles into one    #
# call. Prove it matches the host per-item reference (validates the stub).     #
# --------------------------------------------------------------------------- #
def _batch_sheets(m):
    # A small 4x4 sheet with three asymmetric, non-blank tiles (so flip + z-order show).
    sheet_h = SpriteSheet(4, 4)
    sheet_d = m.SpriteSheet(4, 4)
    for sh in (sheet_h, sheet_d):
        # tile 1: an L of colours in a corner (asymmetric -> flip is visible)
        sh.tset(1, 0, 0, 8); sh.tset(1, 1, 0, 9); sh.tset(1, 0, 1, 10); sh.tset(1, 7, 7, 12)
        # tile 2: a centre block
        sh.tset(2, 3, 3, 11); sh.tset(2, 4, 3, 14); sh.tset(2, 3, 4, 15)
        # tile 3: a full-corner marker
        sh.tset(3, 0, 0, 6); sh.tset(3, 7, 0, 13)
    return sheet_h, sheet_d


def test_spr_batch_matches_host_native_blit_batch():
    # DeviceCanvas.spr_batch -> moy_gfx.blit_batch vs the host per-item spr() loop, with
    # camera + clip + flip in play, byte-for-byte. This is the cross-backend proof for the
    # native batch kernel the auto-batcher (#63) flushes through.
    m, host, dev = _both(True)
    sheet_h, sheet_d = _batch_sheets(m)
    items = [(1, 4, 4), (2, 14, 4, 0), (1, 24, 6, 1), (3, 34, 8), (2, 44, 4, 3)]
    for c, sh in ((host, sheet_h), (dev, sheet_d)):
        c.cls(0)
        c.camera(2, 1)
        c.clip(6, 4, 50, 30)
        c.spr_batch(sh, items, colorkey=-1, scale=2)
    _assert_same(host, dev, "spr_batch native")


# --------------------------------------------------------------------------- #
# Fold 1 auto-batch (#63): a naive per-sprite spr_tile() loop must render       #
# byte-identically to the equivalent immediate spr() calls -- through every     #
# state-break that flushes the pending batch -- on BOTH backends.               #
# --------------------------------------------------------------------------- #
def _stray_image(mk):
    # A standalone Image (NOT a sheet tile) whose colours differ from the sheet, so an
    # interleaved Image blit is distinguishable and its flush point is exercised.
    return mk(["ZZ.", ".Z.", "..Z"], {"Z": 7})


def _drive_sprite_scene(c, sheet, img, use_batch):
    """One scene that touches every flush trigger from docs/fast_by_default_drawing.md
    2.2. `use_batch` picks the auto-batch path (spr_tile) or the immediate reference
    (resolve the tile + spr() now). Both MUST paint the same pixels."""
    def stile(tile, x, y, ck=-1, scale=1, flip=0):
        if use_batch:
            c.spr_tile(sheet, tile, x, y, ck, scale, flip)
        else:
            ti = sheet.tile_image(tile, ck)
            if ti is not None:
                c.spr(ti, x, y, scale, flip)

    c.cls(0)
    # (a) a run of sprites broken by a non-spr primitive (rect). flip does NOT break it.
    stile(1, 3, 3)
    stile(2, 12, 3)
    stile(1, 21, 3, flip=1)
    c.rect(1, 13, 40, 3, 5)
    # (b) a colorkey change mid-run.
    stile(1, 3, 18, ck=-1)
    stile(2, 12, 18, ck=8)
    stile(1, 21, 18, ck=8)
    # (c) a scale change mid-run.
    stile(2, 3, 28, ck=8, scale=1)
    stile(1, 13, 28, ck=8, scale=2)
    # (d) a camera change mid-run.
    stile(1, 3, 40)
    c.camera(3, 2)
    stile(2, 3, 40)
    # (e) a clip change mid-run.
    stile(1, 12, 40)
    c.clip(0, 0, W, H)
    stile(2, 12, 40)
    c.clip()
    c.camera()
    # (f) an Image-object sprite interleaved (flush pending, draw immediately).
    stile(3, 40, 20)
    c.spr(img, 44, 22, 1)
    stile(1, 40, 30)
    # (g) a multi-tile spr(w>1) interleaved (flush pending, draw immediately).
    stile(2, 2, 2)
    span = sheet.tile_span_image(1, 2, 1, -1)
    if span is not None:
        c.spr(span, 46, 2, 1)
    stile(3, 2, 9)
    # (h) a trailing run with NO following primitive: the end-of-frame flush must emit it.
    stile(1, 52, 40)
    stile(2, 58, 40)
    if use_batch:
        c.flush_batch()


def test_auto_batch_matches_immediate_on_host():
    # Batching invariance: the auto-batched scene == the same scene drawn immediately,
    # pixel-for-pixel, on the host reference rasterizer.
    m, _, _ = _both(True)
    sheet_h, _ = _batch_sheets(m)
    a = Canvas(W, H)
    b = Canvas(W, H)
    _drive_sprite_scene(a, sheet_h, _stray_image(Image.from_ascii), use_batch=False)
    _drive_sprite_scene(b, sheet_h, _stray_image(Image.from_ascii), use_batch=True)
    assert a.buf == b.buf, (
        "auto-batch differs from immediate on host in %d px"
        % sum(1 for x, y in zip(a.buf, b.buf) if x != y))
    assert len(set(b.buf)) > 1                # sanity: it actually drew something


def test_auto_batch_host_equals_device():
    # Cross-backend parity of the auto-batched scene: the host indexed rasterizer and the
    # device native path (spr_tile -> blit_batch / blit565) agree byte-for-byte.
    m, host, dev = _both(True)
    sheet_h, sheet_d = _batch_sheets(m)
    _drive_sprite_scene(host, sheet_h, _stray_image(Image.from_ascii), use_batch=True)
    _drive_sprite_scene(dev, sheet_d, _stray_image(m.Image.from_ascii), use_batch=True)
    _assert_same(host, dev, "auto-batch host==device")


def test_auto_batch_device_equals_immediate_device():
    # And on the DEVICE itself: the auto-batch path == drawing each sprite immediately,
    # so the native blit_batch / single-item blit565 fallback introduce no drift.
    m, _, _ = _both(True)
    _, sheet_d = _batch_sheets(m)
    imm = m.DeviceCanvas(_FakeComp(W, H))
    bat = m.DeviceCanvas(_FakeComp(W, H))
    _drive_sprite_scene(imm, sheet_d, _stray_image(m.Image.from_ascii), use_batch=False)
    _drive_sprite_scene(bat, sheet_d, _stray_image(m.Image.from_ascii), use_batch=True)
    a = _dev_rgb565(imm)
    b = _dev_rgb565(bat)
    assert a == b, ("device auto-batch differs from immediate in %d px"
                    % sum(1 for x, y in zip(a, b) if x != y))


class _PyGate:
    """Faithful Python transcription of moy_gfx spr_gate_call (modmoy_gfx.c, #63):
    the SAME array-header state machine (begin on empty/state-change/foreign-token/
    full, int16 clamps, int/float parse, fallback delegation) -- so the protocol the
    C relies on is cross-checked against the pure-Python spr path on the host.
    Keep this a line-for-line port; that is what makes the parity meaningful."""

    def __init__(self, canvas, sheet, fallback, token=7):
        self._cv = canvas
        self._sheet = sheet
        self._fb = fallback
        self._q = canvas._batch_arr
        self._qlen = len(canvas._batch_arr)
        self._token = token

    def __call__(self, *args, **kw):
        if kw or not (3 <= len(args) <= 6):
            return self._fb(*args, **kw)
        v = [0, 0, 0, -1, 1, 0]
        for i, o in enumerate(args):
            if isinstance(o, bool) or not isinstance(o, (int, float)):
                return self._fb(*args, **kw)
            v[i] = int(o)          # C: small-int value / float truncation
        q = self._q
        k = q[0]
        if k < 4:
            k = 4
        if (k == 4 or k + 4 > self._qlen
                or q[3] != self._token or q[1] != v[3] or q[2] != v[4]):
            self._cv.begin_batch(self._sheet, v[3], v[4], self._token)
            k = q[0]
            if k < 4 or k + 4 > self._qlen:
                return None
        tid = v[0]
        if tid < -32768 or tid > 32767:
            tid = -1
        x = min(32767, max(-32768, v[1]))
        y = min(32767, max(-32768, v[2]))
        q[k] = tid
        q[k + 1] = x
        q[k + 2] = y
        q[k + 3] = v[5] & 3
        q[0] = k + 4
        return None


def test_spr_gate_protocol_matches_python_path():
    # The C gate's array-header protocol (#63) must draw pixel-identically to the
    # plain Python spr_tile path for the SAME mixed scene: contiguous runs, a
    # colorkey change, a scale change, float coords, huge coords (int16 clamp),
    # an Image via the fallback, and interleaved non-spr primitives (run breaks).
    m, _, _ = _both(True)
    _, sheet_d = _batch_sheets(m)
    stray = _stray_image(m.Image.from_ascii)

    def scene(cv, spr):
        cv.cls(1)
        for i in range(12):
            spr((i % 3) + 1, i * 9, 5, 0)             # one contiguous run
        for i in range(5):
            spr(2, i * 9, 30, 3)                      # colorkey change -> break
        spr(1, 10.6, 44.9, 0)                          # float coords truncate
        spr(1, 90000, 10, 0)                           # x clamps (off-screen)
        spr(90000, 20, 10, 0)                          # tile id -> invalid, skipped
        cv.rect(60, 60, 8, 8, 5)                       # primitive breaks the run
        spr(3, 70, 60, 0)
        spr(2, 80, 60, -1, 2)                          # scale change -> break
        cv.spr(stray, 100, 60, 2)                      # Image path (gate: fallback)
        cv.flush_batch()

    gate_cv = m.DeviceCanvas(_FakeComp(W, H))
    py_cv = m.DeviceCanvas(_FakeComp(W, H))

    def py_spr(n, x, y, colorkey=-1, scale=1, flip=0):
        py_cv.spr_tile(sheet_d, int(n), x, y, colorkey, scale, flip)

    def gate_fallback(n, x, y, colorkey=-1, scale=1, flip=0):
        gate_cv.spr_tile(sheet_d, int(n), x, y, colorkey, scale, flip)

    gate = _PyGate(gate_cv, sheet_d, gate_fallback)
    scene(gate_cv, gate)
    scene(py_cv, py_spr)
    a = _dev_rgb565(gate_cv)
    b = _dev_rgb565(py_cv)
    assert a == b, ("spr_gate protocol differs from python path in %d px"
                    % sum(1 for x, y in zip(a, b) if x != y))


def test_auto_batch_actually_coalesces():
    # Batching must not just be pixel-correct -- it must COALESCE. The perf_batch counters
    # (#63) prove the run collapsed to ONE blit_batch, which the pixel-parity tests above
    # cannot see (N individual sprs draw the same pixels as one blit_batch). This is the
    # runtime guard that a cart's N-sprite loop (e.g. sakura's 120 petals) is one native
    # call, not N -- the whole point of Fold 1.
    m, _, _ = _both(True)
    sheet_h, sheet_d = _batch_sheets(m)
    for cv, sh in ((Canvas(W, H), sheet_h),
                   (m.DeviceCanvas(_FakeComp(W, H)), sheet_d)):
        # A contiguous run of same sheet/colorkey/scale coalesces into ONE flush.
        cv.batch_reset()
        for i in range(20):
            cv.spr_tile(sh, (i % 3) + 1, i * 3, 5, 0)
        cv.flush_batch()
        assert (cv._batch_flushes, cv._batch_sprites, cv._batch_maxrun) == (1, 20, 20)
        # A non-spr primitive splits the run.
        cv.batch_reset()
        for i in range(6):
            cv.spr_tile(sh, 1, i * 3, 5, 0)
        cv.rect(0, 0, 4, 4, 3)                       # breaks the batch
        for i in range(4):
            cv.spr_tile(sh, 1, i * 3, 30, 0)
        cv.flush_batch()
        assert (cv._batch_flushes, cv._batch_sprites, cv._batch_maxrun) == (2, 10, 6)
        # A scale change also breaks the run (blit_batch bakes one scale per call).
        cv.batch_reset()
        cv.spr_tile(sh, 1, 0, 0, -1, 1)
        cv.spr_tile(sh, 1, 8, 0, -1, 2)              # scale 1 -> 2 breaks
        cv.flush_batch()
        assert (cv._batch_flushes, cv._batch_maxrun) == (2, 1)
