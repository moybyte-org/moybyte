"""The glyph lane (docs/look_2026-10.md step 4, native/moy_gfx/moy_text.h's
paint): a version 2 font's 1, 2 and 4 bpp coverage blended into RGB565.

* A Python reference blends every glyph over what is under it, channel by
  channel in canonical RGB565, a glyph at a time, the shadow pass first. The C
  matches it at every bpp, at scales 1 and 2, clipped and camera-shifted,
  over noise and over a known background (whole or a box), with a shadow and
  with an ellipsis cut, in both wire orders.
* The known-background ramp never reads the target: told a background the
  buffer does not hold, a glyph's pixels show the ramp's colours.
* The toolkit passes the field it fills: a row and a button drawn whole take
  the ramp and draw what their frame and content phases drawn apart (which
  read) draw; a label's `bg` and `shadow` land as the reference says.
* The boards' module (the desktop MicroPython's `moy_ui` and `moy_gfx`) draws
  a fuzzed run of prints and labels to the host's bytes.
"""

import ctypes
import os
import random
import subprocess
import sys
import zlib

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import glass_binding, ui
from runtime.host_canvas import make_system_canvas

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from on_glass import GLYPH_CASES  # noqa: E402
from test_font_lane import FONTS, _Font2, _read, _Target, _v2_files  # noqa: E402
from unix_mp import require_unix_mp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 160, 40
ELLIPSIS = 0x85


def sw(v):
    return ((v << 8) | (v >> 8)) & 0xFFFF


def blend(f, b, wt):
    iw = 256 - wt
    r = (((f >> 11) & 31) * wt + ((b >> 11) & 31) * iw + 128) >> 8
    g = (((f >> 5) & 63) * wt + ((b >> 5) & 63) * iw + 128) >> 8
    bl = ((f & 31) * wt + (b & 31) * iw + 128) >> 8
    return (r << 11) | (g << 5) | bl


class _Ref(_Font2):
    """The lane as the plan says it: each pixel of a glyph blends its ink at
    its coverage over what is under it, canonical words."""

    def weights(self, alpha):
        top = (1 << self.bpp) - 1
        return [(v * alpha + (top >> 1)) // top for v in range(top + 1)]

    def one_pass(self, buf, bs, x, y, col, alpha, scale, clip, cam):
        cx0, cy0, cx1, cy1 = clip
        wts = self.weights(alpha)
        top = (1 << self.bpp) - 1
        pens, _w = self.pens(bs)
        for b, pen in zip(bs, pens):
            off, gw, gh, gx, gy, _adv = self.rec(b)
            ox = x + (pen + gx) * scale - cam[0]
            oy = y + (self.ascent - gy) * scale - cam[1]
            for r in range(gh):
                for c in range(gw):
                    bit = (r * gw + c) * self.bpp
                    v = self.cov[off + (bit >> 3)] >> (8 - self.bpp - (bit & 7)) & top
                    wt = wts[v]
                    if not wt:
                        continue
                    for sy in range(scale):
                        for sx in range(scale):
                            tx, ty = ox + c * scale + sx, oy + r * scale + sy
                            if cx0 <= tx < cx1 and cy0 <= ty < cy1:
                                i = ty * W + tx
                                buf[i] = col if wt == 256 else blend(col, buf[i], wt)

    def paint(self, buf, bs, x, y, col, scale, clip=(0, 0, W, H), cam=(0, 0), shadow=None):
        if shadow is not None:
            sc, a, dx, dy = shadow
            self.one_pass(buf, bs, x + dx * scale, y + dy * scale, sc, a + (a >> 7), scale,
                          clip, cam)
        self.one_pass(buf, bs, x, y, col, 256, scale, clip, cam)


class _Paint(ctypes.Structure):
    _fields_ = [("bg", ctypes.c_int32)] + [(n, ctypes.c_int) for n in ("bx", "by", "bw", "bh")] + [
        ("shadow", ctypes.c_int32)] + [(n, ctypes.c_int) for n in ("a", "dx", "dy")]


def _lib():
    lib = glass_binding._lib()
    lib.moy_text_paint.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8,
                                   ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_uint16, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
    lib.moy_text_cut_px.restype = ctypes.c_size_t
    lib.moy_text_cut_px.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                    ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    return lib


@pytest.fixture(params=[True, False], ids=["swapped", "canonical"])
def wire(request):
    was = moy_ui.text_wire(request.param)
    yield request.param
    moy_ui.text_wire(was)


@pytest.fixture(params=_v2_files(), ids=os.path.basename)
def v2(request):
    data = _read(request.param)
    rc, fid = moy_ui.font_load(data)
    assert rc == 0
    yield fid, _Ref(data)
    moy_ui.font_free(fid)


def _c_draw(lib, fid, s, x, y, col, scale, buf, swapped, clip=(0, 0, W, H), cam=(0, 0),
            bg=None, box=None, shadow=None, maxw=-1, align=0):
    """moy_text_paint into `buf` (canonical words, converted to and from the
    wire order); answers the drawn width and the buffer."""
    to = sw if swapped else (lambda v: v)
    arr = (ctypes.c_uint16 * (W * H))(*[to(v) for v in buf])
    t = _Target(ctypes.addressof(arr), W * H, W, cam[0], cam[1], *clip)
    p = None
    if bg is not None or shadow is not None:
        bx, by, bw, bh = box or (cam[0], cam[1], W, H)
        sc, a, dx, dy = shadow or (-1, 0, 0, 0)
        p = _Paint(-1 if bg is None else to(bg), bx, by, bw, bh,
                   -1 if shadow is None else to(sc), a, dx, dy)
    drawn = lib.moy_text_paint(ctypes.byref(t), fid, 1, s, len(s), x, y, to(col), scale, align,
                               maxw, None if p is None else ctypes.byref(p))
    return drawn, [to(v) for v in arr]


PROBE = "AVTa yo., 0123 Hi é−…x"
INK, BG, SHADE = 0xF7BE, 0x2945, 0x0841


def _noise(seed):
    rng = random.Random(seed)
    return [rng.randrange(0x10000) for _ in range(W * H)]


@pytest.mark.parametrize("scale", [1, 2])
def test_the_lane_blends_as_the_reference(v2, scale, wire):
    lib = _lib()
    fid, ref = v2
    s = PROBE.encode()
    bs = ref.bytes_of(PROBE)
    solid = [BG] * (W * H)
    boxed = _noise(3)
    for y in range(H):
        for x in range(20, 80):
            boxed[y * W + x] = BG
    cases = (
        ("noise", _noise(1), dict(at=(3, 2))),
        ("clip+camera", _noise(2), dict(at=(-5, 4), clip=(2, 3, 150, 30), cam=(4, -3))),
        ("known", solid, dict(at=(3, 2), bg=BG)),
        ("known+camera", solid, dict(at=(-5, 4), clip=(2, 3, 150, 30), cam=(4, -3), bg=BG)),
        ("known box", boxed, dict(at=(-30, 2), bg=BG, box=(20, 0, 60, H))),
        ("shadow", _noise(4), dict(at=(3, 2), shadow=(SHADE, 140, 1, 1))),
        ("shadow+known", solid, dict(at=(3, 2), bg=BG, shadow=(SHADE, 255, 2, 1))),
    )
    for name, under, kw in cases:
        at = kw.pop("at")
        drawn, got = _c_draw(lib, fid, s, at[0], at[1], INK, scale, list(under), wire, **kw)
        want = list(under)
        ref.paint(want, bs, at[0], at[1], INK, scale, kw.get("clip", (0, 0, W, H)),
                  kw.get("cam", (0, 0)), kw.get("shadow"))
        assert drawn == ref.width(PROBE) * scale, name
        assert got == want, name
        assert got != under, name


def test_a_cut_string_blends_its_ellipsis(wire):
    lib = _lib()
    data = _read(os.path.join(FONTS, "inter-15-500-4.fnt"))
    ref = _Ref(data)
    rc, fid = moy_ui.font_load(data)
    try:
        s = b"A long window title"
        ell = ctypes.c_int()
        for maxw in (90, 60):
            k = lib.moy_text_cut_px(fid, s, len(s), maxw, 1, ctypes.byref(ell))
            assert ell.value == 1
            drawn, got = _c_draw(lib, fid, s, 0, 2, INK, 1, [BG] * (W * H), wire, bg=BG,
                                 maxw=maxw, align=1)
            want = [BG] * (W * H)
            d = maxw - drawn
            ref.paint(want, ref.bytes_of(s[:k]) + [ELLIPSIS],
                      d // 2 if d >= 0 else -((1 - d) // 2), 2, INK, 1)
            assert got == want, maxw
    finally:
        moy_ui.font_free(fid)


def _disjoint(ref, bs):
    pens, _w = ref.pens(bs)
    boxes = sorted((pen + ref.rec(b)[3], pen + ref.rec(b)[3] + ref.rec(b)[1])
                   for b, pen in zip(bs, pens) if ref.rec(b)[1])
    return all(a[1] <= b[0] for a, b in zip(boxes, boxes[1:]))


@pytest.mark.parametrize("name", ["sample_v2_2.fnt", "sample_v2_4.fnt"])
def test_the_ramp_never_reads_the_target(name, wire):
    """Told the background is BG over a buffer of another colour, the glyphs
    store the ramp of INK over BG: what the lane would blend over BG, and
    every pixel they leave keeps the buffer's own colour."""
    lib = _lib()
    data = _read(os.path.join(ROOT, "tests", "fixtures", "fonts", name))
    ref = _Ref(data)
    s = next(t for t in ("HI", "Hl", "II") if _disjoint(ref, ref.bytes_of(t)))
    rc, fid = moy_ui.font_load(data)
    try:
        other = 0xF800
        _drawn, got = _c_draw(lib, fid, s.encode(), 4, 4, INK, 2, [other] * (W * H), wire,
                              bg=BG)
        over_bg = [BG] * (W * H)
        ref.paint(over_bg, ref.bytes_of(s), 4, 4, INK, 2)
        want = [v if v != BG else other for v in over_bg]
        assert got == want
        assert set(over_bg) - {BG, INK}, "no partial coverage drawn"
    finally:
        moy_ui.font_free(fid)


# --- the toolkit passes the field it fills -------------------------------------------

@pytest.fixture
def ui_font():
    data = _read(os.path.join(FONTS, "inter-12-400-2.fnt"))
    rc, fid = moy_ui.font_load(data)
    assert rc == 0
    was = moy_ui.font("ui")[0]
    assert ui.use_font("ui", fid)
    yield fid, _Ref(data)
    ui.use_font("ui", was)
    moy_ui.font_free(fid)


class _Split:
    """ui's C with every whole-phase row and button drawn as its frame, then
    its content: the content phase does not know the field, so it reads."""

    def __init__(self, c):
        self._c = c

    def __getattr__(self, name):
        return getattr(self._c, name)

    def row(self, *a):
        self._c.row(*a[:-1], ui._FRAME)
        self._c.row(*a[:-1], ui._CONTENT)

    def button(self, *a):                  # (..., phase, app)
        self._c.button(*a[:-2], ui._FRAME, a[-1])
        self._c.button(*a[:-2], ui._CONTENT, a[-1])


def _words(cv):
    return bytes(cv._buf)


@pytest.mark.parametrize("fs", [1, 2])
def test_a_row_and_a_button_ramp_over_their_field(ui_font, fs, monkeypatch):
    def frame():
        cv = make_system_canvas(240, 60 * fs, font_scale=fs)
        cv.cls(1)
        ui.row(cv, ui._TH, (2, 2, 200, 18 * fs), "Wi-Fi network", value="Moy AV")
        ui.button(cv, ui._TH, (2, 24 * fs, 120, 18 * fs), "Tray")
        return _words(cv)

    n0 = moy_ui.text_lanes()
    whole = frame()
    n1 = moy_ui.text_lanes()
    monkeypatch.setattr(ui, "_C", _Split(ui._C))
    apart = frame()
    n2 = moy_ui.text_lanes()
    assert whole == apart
    assert n1[0] - n0[0] > 10                     # the whole draw took the ramp
    assert n2[0] == n1[0] and n2[1] - n1[1] > 10  # the content phase alone reads


def test_a_labels_bg_and_shadow(ui_font):
    from device_canvas import PAL565
    fid, ref = ui_font
    line = moy_ui.font_info(fid)[2]
    rect, text = (4, 3, 150, 20), "Sky Run − Tabs"
    for bg, shadow in ((None, None), (9, None), (None, (0, 200, 1, 1)), (9, (0, 255, 0, 1))):
        cv = make_system_canvas(W, H)
        cv.cls(9)
        n0 = moy_ui.text_lanes()
        drawn = ui.label(cv, rect, text, 7, bg=bg, shadow=shadow)
        n1 = moy_ui.text_lanes()
        got = [sw(v) for v in memoryview(cv._buf).cast("H")]
        want = [PAL565[9]] * (W * H)
        sh = None if shadow is None else (PAL565[shadow[0]],) + shadow[1:]
        ref.paint(want, ref.bytes_of(text), rect[0], rect[1] + (rect[3] - line) // 2,
                  PAL565[7], 1, shadow=sh)
        assert drawn == ref.width(text)
        assert got == want, (bg, shadow)
        took_ramp = n1[0] > n0[0]
        assert took_ramp == (bg is not None), (bg, shadow)


# --- the boards' module ---------------------------------------------------------------

_MP_DRIVER = r'''import sys
sys.path.insert(0, @RUNTIME@)
sys.path.insert(0, @DEVICE@)
sys.path.insert(0, @STAGE@)
import moy_ui
import device_canvas as dc
SOURCES = []
for _p in @FONTS@:
    with open(_p, "rb") as _f:
        SOURCES.append(_f.read())
'''

_MP_TAIL = r'''
import binascii


class Comp:
    def __init__(self, w, h):
        self._w, self._h = w, h
        self._buf = bytearray(w * h * 2)

    def size(self):
        return (self._w, self._h)

    def framebuffer(self):
        return self._buf

    def gfx(self):
        import moy_gfx
        return moy_gfx


def make(w, h, fs):
    return dc.SystemCanvas(Comp(w, h), font_scale=fs)


glyph_cases(make, lambda i, b: print(i, binascii.crc32(b)), SOURCES)
'''


def test_the_boards_module_draws_the_hosts_bytes(tmp_path):
    exe = require_unix_mp(
        "moy_ui", "moy_gfx", "moy_glass",
        why="Without it the glyph lane's MicroPython face, the one every board "
            "imports, never draws on the host.")
    import shutil
    stage = tmp_path / "stage"
    stage.mkdir()
    shutil.copy(os.path.join(ROOT, "runtime", "font.py"), stage / "moy_font.py")
    shutil.copy(os.path.join(ROOT, "runtime", "moy_image.py"), stage / "moy_image.py")
    fonts = _v2_files()
    cases = GLYPH_CASES.replace("@SEED@", "11").replace("@N@", "60")
    script = tmp_path / "driver.py"
    script.write_text(_MP_DRIVER.replace("@DEVICE@", repr(os.path.join(ROOT, "device")))
                      .replace("@RUNTIME@", repr(os.path.join(ROOT, "runtime")))
                      .replace("@STAGE@", repr(str(stage))).replace("@FONTS@", repr(fonts))
                      + cases + _MP_TAIL)
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:] or out.stdout[-2000:]
    got = [tuple(int(v) for v in ln.split()) for ln in out.stdout.splitlines()]

    want = []
    g = {"moy_ui": moy_ui}
    exec(cases, g)
    g["glyph_cases"](lambda w, h, fs: make_system_canvas(w, h, font_scale=fs),
             lambda i, b: want.append((i, zlib.crc32(b))), [_read(p) for p in fonts])
    assert len(got) == len(want) == 60, out.stdout[-500:]
    assert got == want
