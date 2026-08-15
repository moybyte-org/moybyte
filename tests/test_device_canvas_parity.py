"""The device canvas: its own invariants, its COMPOSITOR, and its fallback lane.

WHAT THIS FILE WAS, TWICE OVER. First, host == device pixel parity for the
TIC-80 draw verbs (#11 cluster 2): a scene through `runtime.canvas.Canvas` (the
host's indexed Python raster) against the same scene through
`device_canvas.DeviceCanvas` (RGB565 over `moy_gfx`). Two genuinely different
programs, held together by a pin. Then `runtime/canvas.py` was deleted and the
host began running `DeviceCanvas` too, so both sides became the same class and
the only difference left was the kernel each was handed: `gfx_binding` (vendored
libmoy, ctypes) on one side and `_FakeGfx` -- a ~650-line Python transcription
of `native/moy_gfx/modmoy_gfx.c` -- on the other.

That transcription was the RIGHT call when it was written and is not any more,
and the reason is worth keeping: at the time the host had no compiled kernel of
its own, so a hand-written second opinion was the only second opinion available.
It now has one. So the ~400 lines of it that re-implemented LIBMOY's nine verbs
(blit_map, blit_batch, tline, text, sspr, tri, circ, circb, line) are gone --
`_FakeGfx` forwards those to the binding -- and what replaced them is stronger:
`tests/test_gfx_binding.py::test_matches_the_native_moy_gfx` runs the same ops
through the binding AND through the REAL native module under a desktop
MicroPython, byte for byte, clamping regimes included. Two compiled kernels, no
transcription in between. (That check used to need a hand-built binary nothing
produced, so it silently skipped everywhere but one machine; `make
unix-micropython` builds it, CI runs it, and it FAILS rather than skips when the
binary is missing. Fixing that is what made this deletion safe.)

WHAT EACH COMPARISON HERE MEANS NOW. `_both(gfx)` builds a pair, and the two
arms are different questions:

  * `gfx=True` -- the compiled kernel against `_FakeGfx`, which still transcribes
    moy_gfx's OWN COMPOSITOR: `fill`, `fill_rect`, `fill_spans`, `blit565`,
    `blit_window`, `blit_indices`. libmoy has no counterpart for those, so this
    is still two implementations, and it is what `cls`, `rect`, `pix`, layers,
    strips and the span batch run through in nearly every test below.
  * `gfx=False` -- the compiled kernel against `DeviceCanvas`'s OWN Python
    fallback lanes (the no-`moy_gfx` build), plus `_FB`, a stand-in for
    MicroPython's `framebuf` that CPython does not have. Those lanes are shipped
    code and this file is the only thing that runs them; a `framebuf` stub is
    not a copy of libmoy, so it stays.

AND WHAT NEITHER ARM IS. Not a check of libmoy's raster -- read a difference in
one of the nine verbs as impossible here, because both sides call the same
function. That raster is pinned by the gfx-binding test above, by
`tests/test_spec_conformance.py` against the spec's goldens, and by
`tools/p4_conformance.py` on real glass. CLAUDE.md records why the last one
matters: the board once failed `provisional_tline` against the golden while this
suite was green.

The reason the file stays is the rest of it: the device canvas's own invariants
-- the PAL565 table, the auto-batch coalescing and its flush triggers, the map
auto-cache and its opaque lane, the pal-tint bake cache, the layer pool, the
spr_gate and lua-spr protocols, `pix()` as a read. Those have no other home.

`DeviceCanvas` runs under CPython here by injecting `_FakeGfx` and `_FB` into
`sys.modules`, plus `moy_font` (what build.sh stages runtime/font.py AS, so the
native-text path activates).
"""

import sys
import types
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime import font as _host_font  # noqa: E402
from runtime import gfx_binding  # noqa: E402
from runtime import host_canvas  # noqa: E402
from runtime import palette  # noqa: E402
from runtime.editors import SpriteSheet, TileMap  # noqa: E402
from runtime.moy_image import Image  # noqa: E402

import canvas_probe as probe  # noqa: E402  (pixel-width-agnostic "it drew" probes)

DEV = ROOT / "firmware" / "lilygo_t_deck_plus_micropython" / "modules"


def rgb565(rgb):
    r, g, b = rgb
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# --------------------------------------------------------------------------- #
# A stand-in moy_gfx: the COMPOSITOR transcribed, libmoy's verbs forwarded.   #
# --------------------------------------------------------------------------- #
class _FakeGfx:
    # THE NINE LIBMOY VERBS ARE NOT TRANSCRIBED HERE. They are the compiled
    # kernel, reached through the host binding -- one line each instead of the
    # ~400 that used to sit here.
    #
    # `blit_map`/`blit_batch`/`tline`/`text`/`sspr`/`tri`/`circ`/`circb`/`line`
    # are CALLS into vendored libmoy in modmoy_gfx.c (see
    # native/moy_gfx/libmoy/UPSTREAM.md for which crossed and why), so a Python
    # copy of them here was a THIRD implementation of a raster that is supposed
    # to have one -- and one that a re-vendor updated the C of and left behind.
    # git log shows the tax being paid by hand as each verb crossed (80904fa,
    # aa22c41, d6c405c, b9783f2).
    #
    # What replaced it is a STRONGER check, not a weaker one:
    # tests/test_gfx_binding.py::test_matches_the_native_moy_gfx drives 131 ops
    # through this same binding AND through the real native module under a
    # desktop MicroPython, byte for byte -- two independently compiled kernels,
    # including the clamping regimes (negative origins, oversize rects,
    # capacity overruns, off-sheet sources) that a clipped caller never reaches.
    # `make unix-micropython` builds the binary it needs; CI builds it on every
    # push, and the test FAILS rather than skips when it is missing.
    #
    # Forwarding is signature-safe by construction: gfx_binding's argument
    # order IS the native module's (that is the premise of the whole shim), and
    # `_FakeGfx` is only ever reached through the same `self._gfx` attribute a
    # board's kernel arrives on.
    #
    # NOT forwarded, and still transcribed below: `fill`/`fill_rect`/
    # `fill_spans`/`blit565`/`blit_window`/`blit_indices`. Those are moy_gfx's
    # OWN compositor -- viewport-aware fills, the RGB565 blits, the span batch --
    # which libmoy has no counterpart for, so a second opinion on them still has
    # somewhere to come from and nowhere else to live.
    blit_map = staticmethod(gfx_binding.blit_map)
    blit_batch = staticmethod(gfx_binding.blit_batch)
    tline = staticmethod(gfx_binding.tline)
    text = staticmethod(gfx_binding.text)
    sspr = staticmethod(gfx_binding.sspr)
    tri = staticmethod(gfx_binding.tri)
    circ = staticmethod(gfx_binding.circ)
    circb = staticmethod(gfx_binding.circb)
    line = staticmethod(gfx_binding.line)

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
    def fill_spans(buf, dw, dh, arr, n, ox, oy, cov, pal,
                   cam_x, cam_y, cx0, cy0, cx1, cy1):
        # Faithful transcription of modmoy_gfx.c moy_gfx_fill_spans (#167,
        # and since #163 the rect AUTO-GATE's flush kernel): n packed int16
        # quints (x, y, w, h, ci), camera subtracted, clip intersected, colour
        # from `cov` or the 64-entry resolved `pal` table.
        mv = memoryview(buf).cast("H")
        cap = len(mv)
        if dw <= 0 or (cov < 0 and pal is None):
            return
        nmax = len(arr) // 5
        if n < 0 or n > nmax:
            n = nmax
        max_rows = cap // dw
        if cx0 < 0:
            cx0 = 0
        if cy0 < 0:
            cy0 = 0
        if cx1 > dw:
            cx1 = dw
        if cy1 > max_rows:
            cy1 = max_rows
        for i in range(n):
            p = i * 5
            col = (cov if cov >= 0 else pal[arr[p + 4] & 63]) & 0xFFFF
            x0 = arr[p] + ox - cam_x
            y0 = arr[p + 1] + oy - cam_y
            x1 = x0 + arr[p + 2]
            y1 = y0 + arr[p + 3]
            if x0 < cx0:
                x0 = cx0
            if y0 < cy0:
                y0 = cy0
            if x1 > cx1:
                x1 = cx1
            if y1 > cy1:
                y1 = cy1
            if x1 <= x0 or y1 <= y0:
                continue
            for yy in range(y0, y1):
                base = yy * dw
                for xx in range(x0, x1):
                    mv[base + xx] = col

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
    """The host canvas's framebuffer as canonical little-endian RGB565 ints.

    Identical to `_dev_rgb565` now, and deliberately still a separate function:
    the two sides are the same CLASS but not the same kernel, and naming them
    apart is what keeps a failure message pointing at which one drew.

    The swap-back is not optional. Under CPython `moy_dsi` is absent, so
    device_canvas picks the T-Deck's byte-SWAPPED `PAL565_SW` as its wire table
    -- on both sides, which is why they compare equal either way, but comparing
    canonical words is what makes a printed `host=0x…` readable against
    `palette.MOY64`.
    """
    cv.flush_batch()
    return [((v << 8) | (v >> 8)) & 0xFFFF for v in memoryview(cv._buf).cast("H")]


def _dev_rgb565(dc):
    # The device writes pixels in PANEL byte order (moy_runtime.PAL565_SW, #43 -- the
    # CPU byte-swap is folded into the LUT so the lcd_bus per-flush swap can be off).
    # Swap back here so the comparison is against the canonical little-endian RGB565
    # the host produces. (PAL565 itself stays canonical -- test_pal565_matches_host.)
    # Present step first: the real loop drains the auto-batches (sprite runs +
    # the #163 rect span gate) via _flush_batches before comp.flush reads the
    # buffer -- reading without it would compare a half-queued frame.
    dc.flush_batch()
    return [((v << 8) | (v >> 8)) & 0xFFFF for v in memoryview(dc._buf).cast("H")]


def Canvas(w, h):
    """The C-kernel side: `DeviceCanvas` over libmoy compiled for the host.

    Named `Canvas` because that is what every test below calls it, and it is
    still the "reference" half of each pair -- only the reference stopped being
    a second raster and became the same raster over the real kernel.
    """
    return host_canvas.make_canvas(w, h)


def _both(use_gfx=True):
    """A fresh pair: the compiled kernel, and the stand-in beside it.

    `use_gfx=True` gives the SECOND canvas `_FakeGfx` -- the compositor verbs
    transcribed, libmoy's nine forwarded to the same binding the first one uses.
    `use_gfx=False` drops it to its `framebuf` lane instead (the no-moy_gfx
    fallback a board takes when the usermod is absent, and DeviceCanvas's own
    Python lanes for every verb that has one), which is the only reason that
    lane is exercised anywhere. See the module docstring for what each arm
    proves; they are not the same question.
    """
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
            "%s: the two rasters disagree in %d/%d px "
            "(first at %d,%d: kernel=%#06x stand-in=%#06x)"
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


def test_text_bytes_parity():
    """print() walks BYTES on both backends (moy SPEC.md 6).

    moy_lua hands the console a bytes object for a Lua string that is not valid
    UTF-8 -- a MicroPython str cannot hold one -- so a cart doing print("\\255")
    arrives here as b"\\xff". The device paths used to do str(s) on that, which
    renders the LITERAL "b'...'": eight visible characters where the cart asked
    for one blank cell.

    A str is UTF-8-encoded rather than read one byte per character, which is
    what makes "cafe-acute" five cells on every tier instead of four on one of
    them. Bytes with no glyph draw nothing and still advance, so the framebuf
    fallback (which cannot be handed a 0xFF at all) maps them to a space and
    lands the same pixels in the same places.
    """
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.print(b"G\xffH", 2, 3, 12)        # bytes, straight from moy_lua
            c.print("caf\u00e9", 2, 14, 7)      # a str: 5 bytes, so 5 cells
            c.print(b"\x00\x1fA", 2, 25, 9)     # control bytes still advance
            c.print(b"", 2, 36, 7)              # empty is not a crash
        _assert_same(host, dev, "text bytes gfx=%s" % gfx)


def test_text_bytes_do_not_shift_what_follows():
    """The cursor keeps step: a byte with no glyph costs exactly one cell, so
    the text after it lands where it would have anyway. A tier that dropped the
    byte instead would slide the rest left and still 'look fine' in isolation."""
    m, host, dev = _both(True)
    for c in (host, dev):
        c.cls(0)
        c.print(b"A\xffB", 2, 3, 12)
    m2, host2, dev2 = _both(True)
    for c in (host2, dev2):
        c.cls(0)
        c.print(b"A B", 2, 3, 12)              # a space is also a blank cell
    _assert_same(host, dev, "bytes advance")
    # Host against host, so compare index buffers directly (_assert_same is the
    # host-vs-device RGB565 comparison).
    assert bytes(host._buf) == bytes(host2._buf), \
        "0xff should occupy exactly one blank cell, like a space"

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
        # Build a sheet + tilemap (both sides share editors.py classes).
        # SPEC.md 3.2's 16 x 32 sheet, and NOT the smaller one this used to
        # build. libmoy refuses a sheet that is not exactly 128 x 256 and draws
        # NOTHING (`moy_gfx_is_moy_sheet` in modmoy_gfx.c, `hg_is_moy_sheet` in
        # the host shim, `_FakeGfx._is_moy_sheet` here) -- so every sheet verb
        # has to be handed the shape a real cart has, or this compares two blank
        # framebuffers and calls it agreement.
        sheet_h = SpriteSheet(16, 32)
        sheet_d = m.SpriteSheet(16, 32)
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
# Sheet fixtures for the auto-batch tests below.                              #
# --------------------------------------------------------------------------- #
def _batch_sheets(m):
    # Three asymmetric, non-blank tiles (so flip + z-order show) on the SPEC.md 3.2
    # sheet -- 16 x 32 tiles. libmoy's blit_batch refuses any other shape and draws
    # nothing at all; the tile ids used here address the same pixels either way.
    sheet_h = SpriteSheet(16, 32)
    sheet_d = m.SpriteSheet(16, 32)
    for sh in (sheet_h, sheet_d):
        # tile 1: an L of colours in a corner (asymmetric -> flip is visible)
        sh.tset(1, 0, 0, 8); sh.tset(1, 1, 0, 9); sh.tset(1, 0, 1, 10); sh.tset(1, 7, 7, 12)
        # tile 2: a centre block
        sh.tset(2, 3, 3, 11); sh.tset(2, 4, 3, 14); sh.tset(2, 3, 4, 15)
        # tile 3: a full-corner marker
        sh.tset(3, 0, 0, 6); sh.tset(3, 7, 0, 13)
    return sheet_h, sheet_d

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
    # pixel-for-pixel, through the compiled kernel.
    m, _, _ = _both(True)
    sheet_h, _ = _batch_sheets(m)
    a = Canvas(W, H)
    b = Canvas(W, H)
    _drive_sprite_scene(a, sheet_h, _stray_image(Image.from_ascii), use_batch=False)
    _drive_sprite_scene(b, sheet_h, _stray_image(Image.from_ascii), use_batch=True)
    assert bytes(a._buf) == bytes(b._buf), (
        "auto-batch differs from immediate on the C kernel in %d px"
        % sum(1 for x, y in zip(_host_rgb565(a), _host_rgb565(b)) if x != y))
    assert probe.drew_something(b)            # sanity: it actually drew something


def test_rect_sprite_overlap_order():
    """Interleaved OVERLAPPING rect/sprite/pix sequences keep painter's order
    through the sprite batch -- a rect between two sprites on the same
    pixels, a long rect run, a pix() READ mid-stream, and rectb over a
    sprite. Host == device, byte-for-byte. (Written for #163's rect
    auto-gate, which was REVERTED -- the pure-Python span append measured
    SLOWER than the direct fill it replaced, 65 -> 75-85us/op on glass; the
    scene stays as the interleaving pin either way.)"""
    m, host, dev = _both(True)
    sheet_h, sheet_d = _batch_sheets(m)
    for c, sheet in ((host, sheet_h), (dev, sheet_d)):
        c.cls(0)
        # sprite under rect under sprite, overlapping in one cell
        c.spr_tile(sheet, 1, 10, 10, -1, 1, 0)
        c.rect(12, 12, 4, 4, 9)
        c.spr_tile(sheet, 2, 14, 14, -1, 1, 0)
        # a long run of small fills
        for i in range(150):
            c.rect(i % 60, 30 + (i // 60) * 2, 2, 2, i % 16)
        # a read mid-stream must see the just-issued rect
        c.rect(50, 8, 3, 3, 12)
        assert c.pix(51, 9) == 12
        # outline over a sprite
        c.spr_tile(sheet, 3, 40, 40, -1, 1, 0)
        c.rectb(39, 39, 10, 10, 7)
    _assert_same(host, dev, "rect/sprite overlap order")


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


# --------------------------------------------------------------------------- #
# moy_lua l_spr (#67 Phase 4): the Lua cart runtime's hot spr is a SECOND C     #
# writer into the same _batch_arr, speaking the spr_gate protocol with its own  #
# token (0x7A11). _LuaSpr transcribes it; the tests pin array shape, clamps,    #
# run breaks, cross-writer interleaving, and the no-fallback error contract.    #
# --------------------------------------------------------------------------- #
_LUA_TOKEN = 0x7A11        # device_api._LUA_TOKEN: never 0 (the Python writer),
                           # outside the spr_gate sequence (1..0x4000)


class _LuaSprError(Exception):
    """Stands in for luaL_error: in the VM this becomes a Lua error -> a cart
    error panel, never a fallback (the gate's fallback delegation does not
    exist on the Lua path -- bad args are cart bugs)."""


class _LuaSpr:
    """Faithful Python transcription of moy_lua's l_spr (modmoy_lua.c, #67):
    the SAME array-header state machine as moy_gfx's spr_gate (_PyGate above)
    with the Lua writer's deltas: C-side defaults [0,0,0,-1,1,0], NO fallback
    (luaL_error on bad args), one sheet bound for the whole run (init), and the
    0x7A11 token (masked & 0x7FFF like moy_lua.init). Keep this line-for-line
    with the C; that is what makes the parity meaningful."""

    def __init__(self, canvas, sheet, token=_LUA_TOKEN):
        self._cv = canvas
        self._sheet = sheet
        self._q = canvas._batch_arr
        self._qlen = len(canvas._batch_arr)
        self._token = token & 0x7FFF

    def __call__(self, *args):
        n = len(args)
        if n < 3 or n > 6:
            raise _LuaSprError("spr(tile, x, y[, colorkey, scale, flip])")
        v = [0, 0, 0, -1, 1, 0]
        for i, o in enumerate(args):
            if isinstance(o, bool) or not isinstance(o, (int, float)):
                raise _LuaSprError("spr: arg %d must be a number" % (i + 1))
            v[i] = int(o)      # C: lua_tointeger / (lua_Integer) truncation
        q = self._q
        k = q[0]
        if k < 4:
            k = 4
        if (k == 4 or k + 4 > self._qlen
                or q[3] != self._token or q[1] != v[3] or q[2] != v[4]):
            # run break (first item / state change / foreign writer / full
            # queue): the SAME begin_batch upcall the C makes, nlr-protected there.
            self._cv.begin_batch(self._sheet, v[3], v[4], self._token)
            k = q[0]
            if k < 4 or k + 4 > self._qlen:
                return None    # defensive: queue unusable, drop
        tid = v[0]
        if tid < -32768 or tid > 32767:
            tid = -1           # invalid tile id -> skipped at draw
        x = min(32767, max(-32768, v[1]))
        y = min(32767, max(-32768, v[2]))
        q[k] = tid
        q[k + 1] = x
        q[k + 2] = y
        q[k + 3] = v[5] & 3
        q[0] = k + 4
        return None


def test_lua_spr_protocol_matches_python_path():
    # The Lua writer's scene must draw pixel-identically to the plain Python
    # spr_tile path: contiguous runs, colorkey/scale breaks, float truncation,
    # int16 clamps, invalid tile ids, and non-spr primitives breaking the run.
    m, _, _ = _both(True)
    _, sheet_d = _batch_sheets(m)

    def scene(cv, spr):
        cv.cls(1)
        for i in range(12):
            spr((i % 3) + 1, i * 9, 5, 0)             # one contiguous run
        for i in range(5):
            spr(2, i * 9, 30, 3)                      # colorkey change -> break
        spr(1, 10.6, 44.9, 0)                          # float coords truncate
        spr(1, -3.7, 20)                               # ...toward zero (-3, not -4)
        spr(1, 90000, 10, 0)                           # x clamps (off-screen)
        spr(90000, 20, 10, 0)                          # tile id -> invalid, skipped
        cv.rect(60, 60, 8, 8, 5)                       # primitive breaks the run
        spr(3, 70, 60, 0)
        spr(2, 80, 60, -1, 2)                          # scale change -> break
        spr(1, 4, 20, -1, 1, 1)                        # flip H
        cv.flush_batch()

    lua_cv = m.DeviceCanvas(_FakeComp(W, H))
    py_cv = m.DeviceCanvas(_FakeComp(W, H))

    def py_spr(n, x, y, colorkey=-1, scale=1, flip=0):
        py_cv.spr_tile(sheet_d, int(n), x, y, colorkey, scale, flip)

    scene(lua_cv, _LuaSpr(lua_cv, sheet_d))
    scene(py_cv, py_spr)
    a = _dev_rgb565(lua_cv)
    b = _dev_rgb565(py_cv)
    assert a == b, ("lua spr protocol differs from python path in %d px"
                    % sum(1 for x, y in zip(a, b) if x != y))


def test_lua_spr_array_shape_and_clamps():
    # The exact int16 layout the C kernel will read: header [next, colorkey,
    # scale, token] stamped by begin_batch, quads (tile, x, y, flip) after it.
    m, _, _ = _both(True)
    _, sheet_d = _batch_sheets(m)
    cv = m.DeviceCanvas(_FakeComp(W, H))
    spr = _LuaSpr(cv, sheet_d)
    spr(2, 10, 20)                       # defaults: colorkey -1, scale 1, flip 0
    spr(1, 33.9, -3.7, -1, 1, 5)         # truncation + flip & 3 masking
    spr(3, 90000, -90000, -1, 1, -1)     # int16 coord clamps; -1 & 3 == 3
    spr(70000, 5, 5)                     # out-of-int16 tile id -> -1
    q = cv._batch_arr
    assert q[0] == 4 + 4 * 4
    assert (q[1], q[2], q[3]) == (-1, 1, _LUA_TOKEN)
    assert list(q[4:8]) == [2, 10, 20, 0]
    assert list(q[8:12]) == [1, 33, -3, 1]
    assert list(q[12:16]) == [3, 32767, -32768, 3]
    assert list(q[16:20]) == [-1, 5, 5, 0]
    cv.flush_batch()
    assert q[0] == 4                     # drained back to the empty header


def test_lua_spr_run_breaks_and_cross_writer_interleave():
    # Coalescing counters: one contiguous Lua run is ONE flush; state changes
    # break; a FULL queue (512 quads) breaks mid-run; and interleaving with the
    # Python writer (token 0) breaks BOTH ways -- the foreign-token check is
    # what lets the console chrome and a Lua cart share one array safely.
    m, _, _ = _both(True)
    _, sheet_d = _batch_sheets(m)
    cv = m.DeviceCanvas(_FakeComp(W, H))
    spr = _LuaSpr(cv, sheet_d)
    # one run, one flush
    cv.batch_reset()
    for i in range(20):
        spr((i % 3) + 1, i * 3, 5)
    cv.flush_batch()
    assert (cv._batch_flushes, cv._batch_sprites, cv._batch_maxrun) == (1, 20, 20)
    # a 600-sprite frame overflows the 512-quad queue exactly once
    cv.batch_reset()
    for i in range(600):
        spr(1, i % 60, i // 60)
    cv.flush_batch()
    assert (cv._batch_flushes, cv._batch_sprites, cv._batch_maxrun) == (2, 600, 512)
    # Lua -> Python -> Lua: three runs (each writer breaks the other's)
    cv.batch_reset()
    spr(1, 0, 0)
    spr(2, 8, 0)
    cv.spr_tile(sheet_d, 1, 16, 0)       # the Python writer (token 0)
    spr(3, 24, 0)
    cv.flush_batch()
    assert (cv._batch_flushes, cv._batch_sprites) == (3, 4)


def test_lua_spr_bad_args_error_not_fallback():
    # Unlike the gate (which delegates Images/kwargs to the Python spr), the
    # Lua writer has NO fallback: wrong arity or a non-number arg is a Lua
    # error (-> the cart panel), and nothing lands in the queue.
    import pytest
    m, _, _ = _both(True)
    _, sheet_d = _batch_sheets(m)
    cv = m.DeviceCanvas(_FakeComp(W, H))
    spr = _LuaSpr(cv, sheet_d)
    for bad in ((1, 2), (1, 2, 3, 4, 5, 6, 7), ("x", 2, 3), (1, "y", 3),
                (1, 2, 3, None), (True, 2, 3)):
        with pytest.raises(_LuaSprError):
            spr(*bad)
    assert cv._batch_arr[0] == 4         # queue untouched


# --------------------------------------------------------------------------- #
# map auto-cache (#63 Fold 2): a naive camera()+map() re-uses a hidden cached    #
# raster on a camera-only change (window/keyed blit) and re-rasters only on a     #
# (tilemap.gen, sheet.gen, scale) bump. The cache must be BYTE-IDENTICAL to a     #
# direct raster across camera moves and after mset/pal/scale edits, host-index == #
# device-565, both gfx modes -- and it must actually KICK IN (counter proof).     #
# --------------------------------------------------------------------------- #
def _mapcache_world(m):
    # A sheet + a WIDER-THAN-SCREEN tilemap (a Hop-Quest-style scroller) with EMPTY cells
    # (transparency) AND a colorkey'd tile (index-0 holes under colorkey=0), so the cache's
    # KEYED composite is exercised, not just an opaque background copy.
    sheet_h = SpriteSheet(16, 32)                    # the SPEC sheet; see _batch_sheets
    sheet_d = m.SpriteSheet(16, 32)
    for sh in (sheet_h, sheet_d):
        for ly in range(8):                          # tile 1: a solid block (no holes)
            for lx in range(8):
                sh.tset(1, lx, ly, 6)
        sh.tset(1, 0, 0, 8); sh.tset(1, 7, 7, 12)    # ... with asymmetric markers
        sh.tset(2, 1, 1, 11); sh.tset(2, 2, 2, 14)   # tile 2: sparse -> index-0 = colorkey holes
        sh.tset(2, 5, 5, 9)
    tm_h = TileMap(12, 10)
    tm_d = TileMap(12, 10)
    for tm in (tm_h, tm_d):
        for cx in range(12):
            tm.mset(cx, 9, 1)                        # a full solid ground row
        tm.mset(2, 5, 2); tm.mset(7, 3, 1); tm.mset(9, 6, 2)
    return sheet_h, sheet_d, tm_h, tm_d


def _play_map(c, sheet, tm, cam, colorkey=0, scale=1, direct=False):
    # Draw the whole tilemap over `sheet` at camera `cam`. `direct=True` forces the UNCACHED
    # raster by first setting an identity pal(5, 5): that bumps _palgen (so map() rasters
    # straight to the buffer) WITHOUT changing any pixel -- an in-test direct-raster reference.
    c.cls(3)
    if direct:
        c.pal(5, 5)
    c.camera(cam[0], cam[1])
    c.map(tm, sheet, 0, 0, tm.w, tm.h, 0, 0, colorkey, scale)
    c.camera()
    if direct:
        c.pal()


def _map_cache_on(m, monkeypatch):
    # Fold 2 ships DEFAULT OFF on device (the hardware A/B verdict lives on the
    # MAP_AUTO_CACHE comment); the cache tests force it ON so the logic stays
    # pinned for a future native keyed-blit kernel / the P4 (#58).
    #
    # BOTH module objects, because there are two: `m` is the copy this file
    # execs per pair, and `device_canvas` is the one `runtime/host_canvas.py`
    # imported for the C-kernel side. Patching only `m` leaves that side with
    # the cache OFF, so a "the cache kicked in" counter reads 0 and a
    # cached-vs-direct comparison silently compares direct against direct.
    monkeypatch.setitem(m.DeviceCanvas.__init__.__globals__, "MAP_AUTO_CACHE", True)
    import device_canvas as _dc_host
    if _dc_host is not m:
        monkeypatch.setitem(_dc_host.DeviceCanvas.__init__.__globals__,
                            "MAP_AUTO_CACHE", True)


def test_map_autocache_host_equals_device_across_camera_and_edits(monkeypatch):
    # Cross-backend parity of the AUTO-CACHED map(): the host indexed rasterizer and the device
    # native path agree byte-for-byte across a camera sweep (cache hits), after an mset
    # (tilemap.gen bump -> re-raster) and under an active pal (both bypass to a direct raster).
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        _map_cache_on(m, monkeypatch)
        sh_h, sh_d, tm_h, tm_d = _mapcache_world(m)
        for cam in ((0, 0), (10, 4), (30, 20), (48, 32), (5, 0)):
            _play_map(host, sh_h, tm_h, cam)
            _play_map(dev, sh_d, tm_d, cam)
            _assert_same(host, dev, "autocache gfx=%s cam=%s" % (gfx, cam))
        tm_h.mset(4, 4, 2); tm_d.mset(4, 4, 2)       # edit the map -> both re-raster
        _play_map(host, sh_h, tm_h, (12, 6))
        _play_map(dev, sh_d, tm_d, (12, 6))
        _assert_same(host, dev, "autocache post-mset gfx=%s" % gfx)
        # An active palette on map() must still match (both backends bypass the cache).
        host.pal(6, 14); dev.pal(6, 14)
        _play_map(host, sh_h, tm_h, (8, 2))
        _play_map(dev, sh_d, tm_d, (8, 2))
        _assert_same(host, dev, "autocache pal-active gfx=%s" % gfx)


def test_map_autocache_equals_direct_raster(monkeypatch):
    # The cached path must be byte-identical to a DIRECT (uncached) raster of the SAME scene,
    # on EACH backend, across camera moves and after an mset. This is the Fold-2 acceptance:
    # auto-cached map() output == direct-raster map() output, byte-for-byte.
    for gfx in (True, False):
        m, _, _ = _both(gfx)
        _map_cache_on(m, monkeypatch)
        sh_h, sh_d, tm_h, tm_d = _mapcache_world(m)
        tm_h.mset(4, 4, 2); tm_d.mset(4, 4, 2)       # (also proves the post-edit raster matches)
        for cam in ((0, 0), (10, 4), (33, 18), (48, 30)):
            # Host (index space).
            cached = Canvas(W, H)
            direct = Canvas(W, H)
            _play_map(cached, sh_h, tm_h, cam)
            _play_map(direct, sh_h, tm_h, cam, direct=True)
            assert bytes(cached._buf) == bytes(direct._buf), (
                "C-kernel cache != direct gfx=%s cam=%s in %d px"
                % (gfx, cam, sum(1 for a, b in zip(_host_rgb565(cached),
                                                   _host_rgb565(direct)) if a != b)))
            # Device (RGB565).
            dc_cached = m.DeviceCanvas(_FakeComp(W, H))
            dc_direct = m.DeviceCanvas(_FakeComp(W, H))
            _play_map(dc_cached, sh_d, tm_d, cam)
            _play_map(dc_direct, sh_d, tm_d, cam, direct=True)
            a = _dev_rgb565(dc_cached)
            b = _dev_rgb565(dc_direct)
            assert a == b, ("device cache != direct gfx=%s cam=%s in %d px"
                            % (gfx, cam, sum(1 for x, y in zip(a, b) if x != y)))


def test_map_autocache_actually_caches(monkeypatch):
    # The cache must not just be pixel-correct -- it must KICK IN. The _map_raster_count /
    # _map_hits counters (#63) prove a camera-only change re-uses the cached region (one
    # blit565 / spr composite) instead of a full per-cell re-raster, which pixel-parity can't
    # see. The Fold-2 analogue of test_auto_batch_actually_coalesces (device gfx-only cache).
    m, _, _ = _both(True)
    _map_cache_on(m, monkeypatch)
    sh_h, sh_d, tm_h, tm_d = _mapcache_world(m)
    for c, sh, tm in ((Canvas(W, H), sh_h, tm_h),
                      (m.DeviceCanvas(_FakeComp(W, H)), sh_d, tm_d)):
        c.map_cache_reset()
        # First map() rasterizes; three camera-moved frames of the SAME region re-use it.
        for cam in ((0, 0), (8, 0), (16, 4), (24, 6)):
            c.cls(3)
            c.camera(cam[0], cam[1])
            c.map(tm, sh, 0, 0, 12, 10, 0, 0, 0, 1)
            c.camera()
        assert c._map_raster_count == 1, "camera move re-rastered (%d)" % c._map_raster_count
        assert c._map_hits == 3, "expected 3 cache hits, got %d" % c._map_hits
        # An mset (tilemap.gen bump) drops the cache -> the next map() re-rasters.
        tm.mset(1, 1, 2)
        c.cls(3)
        c.map(tm, sh, 0, 0, 12, 10, 0, 0, 0, 1)
        assert c._map_raster_count == 2, "mset didn't re-raster (%d)" % c._map_raster_count
        # A scale change (different pixel dims / key) re-rasters.
        c.cls(3)
        c.map(tm, sh, 0, 0, 12, 10, 0, 0, 0, 2)
        assert c._map_raster_count == 3, "scale change didn't re-raster (%d)" % c._map_raster_count
        c.cls(3)                                     # ... then the scale-2 region caches too
        c.map(tm, sh, 0, 0, 12, 10, 0, 0, 0, 2)
        assert c._map_raster_count == 3 and c._map_hits == 4
        # A sheet paint edit (sheet.gen bump) also drops the cache.
        sh.pset(0, 0, 5)
        c.cls(3)
        c.map(tm, sh, 0, 0, 12, 10, 0, 0, 0, 2)
        assert c._map_raster_count == 4, "sheet edit didn't re-raster (%d)" % c._map_raster_count
        # An active pal bypasses the cache entirely (direct raster, counters unchanged).
        # (A REAL remap: pal(3, 3) is identity CONTENT, and _palgen is a content id
        # now -- identity would legitimately keep using the cache.)
        c.pal(3, 5)
        c.cls(3)
        c.map(tm, sh, 0, 0, 12, 10, 0, 0, 0, 2)
        assert c._map_raster_count == 4, "pal-active path touched the cache (%d)" % c._map_raster_count
        c.pal()


def test_map_autocache_opaque_lane_full_coverage(monkeypatch):
    # A FULL-COVERAGE region with no colorkey has no transparent pixel, so the device
    # composite may take blit565's opaque row-memcpy lane (key=-1, the #66 chrome-trim
    # lane) instead of testing every pixel -- and it must stay byte-identical to the
    # direct raster. A sparse region (empty cells) must keep the keyed composite.
    m, _, _ = _both(True)
    _map_cache_on(m, monkeypatch)
    sh_h, sh_d, tm_h, tm_d = _mapcache_world(m)
    for tm in (tm_h, tm_d):
        for cy in range(10):                     # fill EVERY cell -> full coverage
            for cx in range(12):
                if tm.mget(cx, cy) < 0:
                    tm.mset(cx, cy, 1)
    for cam in ((0, 0), (12, 6)):
        cached = m.DeviceCanvas(_FakeComp(W, H))
        direct = m.DeviceCanvas(_FakeComp(W, H))
        for c, tm in ((cached, tm_d), (direct, tm_d)):
            c.cls(3)
            c.camera(cam[0], cam[1])
            if c is direct:
                c._nocache = True                # force the direct raster path
            c.map(tm, sh_d, 0, 0, 12, 10, 0, 0, -1, 1)   # colorkey=-1: opaque-eligible
            c.camera()
        assert cached._mapcache is not None and cached._mapcache[4] == -1, (
            "full-coverage colorkey=-1 region should composite via the opaque lane")
        assert _dev_rgb565(cached) == _dev_rgb565(direct), "opaque lane changed pixels"
    # Sparse (default world has empty cells): the keyed composite must be chosen.
    _, _, _ = _both(True)
    sh_h2, sh_d2, tm_h2, tm_d2 = _mapcache_world(m)
    c = m.DeviceCanvas(_FakeComp(W, H))
    c.cls(3)
    c.map(tm_d2, sh_d2, 0, 0, 12, 10, 0, 0, -1, 1)
    assert c._mapcache[4] != -1, "a sparse region must keep the keyed composite"


def test_pal_tint_sandwich_bakes_each_variant_once():
    # #63 fast-by-default (the #72 Letter Blitz disease, fixed ENGINE-side): a sprite
    # drawn through the pal()/spr()/pal() tint sandwich -- alternating tints across
    # frames -- must bake each (tint, scale) variant ONCE, then swap cached bakes.
    # _palgen is a content id (identity == 0, a re-seen remap gets its old id back),
    # and the per-Image variant dict keeps the bakes alive across tint switches.
    # Pixels must stay byte-identical to the always-rebake behaviour.
    m, _, _ = _both(True)
    cv = m.DeviceCanvas(_FakeComp(W, H))
    ref = m.DeviceCanvas(_FakeComp(W, H))
    img = m.Image(4, 4, [7, 7, 0, 0, 7, 7, 0, 0, 0, 0, 8, 8, 0, 0, 8, 8], -1)
    ref_img = m.Image(4, 4, [7, 7, 0, 0, 7, 7, 0, 0, 0, 0, 8, 8, 0, 0, 8, 8], -1)

    def frame(c, im):
        # two tinted draws + an untinted one -- the Letter Blitz shape
        c.cls(1)
        c.pal(7, 11)
        c.spr(im, 10, 10, 2)
        c.pal()
        c.pal(7, 3)
        c.spr(im, 40, 10, 2)
        c.pal()
        c.spr(im, 70, 10, 2)

    for _ in range(4):                    # 4 identical frames
        frame(cv, img)
    bakes = cv._rgb_bakes
    assert bakes == 3, "expected ONE bake per tint variant, got %d" % bakes
    for _ in range(4):                    # more frames -> zero new bakes
        frame(cv, img)
    assert cv._rgb_bakes == bakes, "a re-seen tint re-baked (variant cache miss)"
    # Pixels identical to a fresh canvas that never reused anything.
    frame(ref, ref_img)
    assert _dev_rgb565(cv) == _dev_rgb565(ref), "variant reuse changed pixels"


def test_layer_pool_reclaims_cart_buffers_across_runs(monkeypatch):
    # #63 leak fix: moy_alloc has no free(), so a dead cart's layer buffers must
    # return to the pool and the next same-dims new_layer must REUSE them (without
    # this, every cart re-run leaked its world from the heap_caps pool). Stub
    # moy_alloc/lcd_bus so the CPython-run device module takes the pooled path.
    import sys
    import types
    m, _, _ = _both(True)
    _map_cache_on(m, monkeypatch)
    fake_alloc = types.ModuleType("moy_alloc")
    fake_alloc.malloc_dma = lambda n, caps=0: bytearray(n)
    fake_bus = types.ModuleType("lcd_bus")
    fake_bus.MEMORY_SPIRAM = 1
    fake_bus.MEMORY_DMA = 2
    monkeypatch.setitem(sys.modules, "moy_alloc", fake_alloc)
    monkeypatch.setitem(sys.modules, "lcd_bus", fake_bus)
    g = m.DeviceCanvas.__init__.__globals__       # the device module's namespace
    monkeypatch.setitem(g, "_LAYER_POOL", {})
    cv = m.DeviceCanvas(_FakeComp(W, H))
    lay1 = cv.new_layer(64, 32, owner="cart")
    assert lay1._comp.pooled, "the stubbed allocator path must mark the buffer pooled"
    buf1 = lay1._comp._buf
    # An unowned (console) layer is never lent/reclaimed.
    lay_console = cv.new_layer(64, 32)
    cv.reclaim_layers("cart")                     # the cart died (Player.start)
    assert g["_LAYER_POOL"].get(64 * 32 * 2), "the cart buffer returns to the pool"
    lay2 = cv.new_layer(64, 32, owner="cart")     # next run, same dims
    assert lay2._comp._buf is buf1, "the next same-dims layer must REUSE the buffer"
    assert lay_console._comp._buf is not buf1, "console layers stay untouched"
    # The Fold-2 hidden map cache is program content: reclaim drops + pools it.
    sh_h, sh_d, tm_h, tm_d = _mapcache_world(m)
    cv.cls(3)
    cv.map(tm_d, sh_d, 0, 0, 12, 10, 0, 0, 0, 1)
    assert cv._mapcache is not None
    cv.reclaim_layers("cart")
    assert cv._mapcache is None, "reclaim must drop the map cache (its layer is pooled)"


# --------------------------------------------------------------------------- #
# pix() as a READ returns a palette INDEX on both tiers (moy SPEC.md 1/6).    #
# The device buffer holds RGB565, so it maps back through the wire LUT; before #
# that, `pix(x, y)` meant an index on the host and a raw RGB565 word on glass, #
# and a cart branching on it could never be pixel-conformant.                  #
# --------------------------------------------------------------------------- #
def test_pix_read_returns_index_on_both():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        host.cls(1)
        dev.cls(1)
        for idx in (0, 1, 8, 12, 63):
            host.pix(10, 10, idx)
            dev.pix(10, 10, idx)
            assert host.pix(10, 10) == idx, ("host gfx=%s" % gfx, idx)
            assert dev.pix(10, 10) == idx, ("device gfx=%s" % gfx, idx)
            assert dev.pix(10, 10) == host.pix(10, 10)


def test_pix_read_is_camera_relative_on_both():
    m, host, dev = _both(True)
    host.cls(0)
    dev.cls(0)
    host.pix(20, 12, 9)
    dev.pix(20, 12, 9)
    host.camera(4, 2)
    dev.camera(4, 2)
    # same world pixel, now addressed through the camera offset
    assert host.pix(24, 14) == 9
    assert dev.pix(24, 14) == 9


# --------------------------------------------------------------------------- #
# SPEC.md 6.1 verbs (#167 shape B): tri / sspr / tline. All three are libmoy  #
# calls now, so the gfx=True arm only re-checks the surrounding compositor;   #
# the gfx=False arm is the one with content here -- DeviceCanvas's own Python #
# fallbacks for these verbs, which nothing else in the tree runs. The kernel  #
# itself is pinned by test_gfx_binding (two compiled kernels) and by the      #
# conformance goldens (moy-spec provisional/_tline).                          #
# --------------------------------------------------------------------------- #
def _sheet_and_map():
    sh = SpriteSheet(16, 32)                          # the SPEC sheet; see _batch_sheets
    for y in range(8):
        for x in range(8):
            sh.tset(1, x, y, 8)                       # solid
            sh.tset(2, x, y, 12 if (x + y) & 1 else 7)  # checker
            sh.tset(3, x, y, 0 if (1 <= x <= 6 and 1 <= y <= 6) else 11)
    tm = TileMap(6, 4)
    for y in range(4):
        for x in range(6):
            v = (x + y) % 4                            # 0 -> empty hole
            tm.mset(x, y, v - 1 if v else -1)
    return sh, tm


def test_tri_parity():
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(1)
            c.tri(4, 4, 40, 10, 20, 40, 8)
            c.tri(30, 44, 60, 44, 45, 20, 11)          # flat bottom edge
            c.tri(5, 46, 25, 46, 15, 46, 14)           # degenerate: one row
            c.camera(6, 3)
            c.tri(16, 16, 50, 22, 30, 47, 12)
            c.camera()
            c.clip(10, 10, 30, 25)
            c.tri(0, 0, 63, 20, 20, 47, 10)
            c.clip()
        _assert_same(host, dev, "tri gfx=%s" % gfx)


def test_sspr_parity():
    sh, _tm = _sheet_and_map()
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(0)
            c.sspr(sh, 8, 0, 8, 8, 2, 2, 20, 20)       # stretch tile 1
            c.sspr(sh, 16, 0, 8, 8, 24, 2, 15, 30)     # non-uniform checker
            c.sspr(sh, 16, 0, 8, 8, 42, 2, 12, 12, 7)  # colorkey drops dark
            c.sspr(sh, 24, 0, 8, 8, 2, 26, 16, 16, -1, 3)  # border, flip both
            c.palt(12, 1)
            c.sspr(sh, 16, 0, 8, 8, 42, 26, 12, 12)    # palt drops light
            c.palt()
            c.camera(4, 2)
            c.sspr(sh, 8, 0, 4, 4, 30, 30, 9, 9)
            c.camera()
        _assert_same(host, dev, "sspr gfx=%s" % gfx)


def test_tline_parity():
    sh, tm = _sheet_and_map()
    F = 65536
    for gfx in (True, False):
        m, host, dev = _both(gfx)
        for c in (host, dev):
            c.cls(1)
            for i in range(10):                        # a small Mode 7 fan
                c.tline(tm, sh, 0, 2 + i, 60, 2 + i,
                        0, (i * 3 * F) // 2, F // 4 + i * (F // 64), 0)
            c.tline(tm, sh, 2, 14, 60, 40, 0, 0, F // 2, F // 3)  # diagonal
            c.tline(tm, sh, 0, 42, 60, 42, -tm.w * 8 * F, 4 * F, 2 * F, 0)  # wrap
            c.tline(tm, sh, 58, 2, 58, 44, 4 * F, 0, 0, F // 2)   # vertical
            c.tline(tm, sh, 2, 45, 40, 45, 0, 8 * F, F // 2, 0, 7)  # colorkey
            c.palt(12, 1)
            c.tline(tm, sh, 2, 47, 40, 47, 0, 8 * F, F // 2, 0)   # palt
            c.palt()
            c.camera(5, 2)
            c.tline(tm, sh, 10, 20, 50, 20, 0, 16 * F, F // 2, 0)
            c.camera()
            c.clip(8, 30, 20, 10)
            c.tline(tm, sh, 0, 34, 63, 34, 0, 0, F, 0)  # cursor walks under clip
            c.clip()
            c.tline(tm, sh, 33, 33, 33, 33, 0, 0, F, F)  # single pixel
        _assert_same(host, dev, "tline gfx=%s" % gfx)
