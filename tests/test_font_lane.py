"""The font lane (docs/theming_2026-09.md §4.7, native/moy_gfx/moy_text.h): a
theme font is a row behind moy_text's font argument, petme128 is row 0, and a
font file is untrusted data a kid may trade.

* Petme128 through the row draws what the cell rasterizer drew: a reference
  raster of runtime/font.py's glyphs at every scale, clipped and camera-shifted.
* A second row draws: the sample font (tests/fixtures/fonts/sample6x9.fnt, a
  proportional 6x9 cut of petme128 with two-byte columns) matches a reference
  raster of its own file, and fits, aligns and measures by its advances.
* The reader: every refusal by its code, a source at an odd address taken,
  the slots, and a freed id refused everywhere it is passed.
* A fuzz: mutated font files never break the reader or write outside the
  target, and the boards' module (the desktop MicroPython's `moy_ui`)
  answers the corpus as the host's does (tests/on_glass.py runs the same
  corpus on glass).
* The theme's `fonts` key: roles, file names, inheritance, notes; every
  seed draws `ui` and `title` in Tiny5, `night`'s.
"""

import ctypes
import json
import os
import subprocess
import sys

import pytest

import runtime  # noqa: F401  (registers the host's moy_ui)
import moy_ui
from runtime import font as petme
from runtime import glass_binding

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from on_glass import (FONT_FIXTURE, FONT_FIXTURES_V2, FONT_PROBE, font_answer,  # noqa: E402
                      font_corpus)
from unix_mp import require_unix_mp  # noqa: E402

OK, BIG, SHORT, MAGIC, NEWER, BAD, FULL = 0, 1, 2, 3, 4, 5, 6
SLOTS = 16


class _Target(ctypes.Structure):
    _fields_ = [("px", ctypes.c_void_p), ("cap", ctypes.c_size_t),
                ("dw", ctypes.c_int)] + [(n, ctypes.c_int) for n in (
                    "cam_x", "cam_y", "cx0", "cy0", "cx1", "cy1")]


def _lib():
    lib = glass_binding._lib()
    lib.moy_text.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint8,
                             ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
                             ctypes.c_int, ctypes.c_uint16, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int]
    lib.moy_font_load.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_uint32)]
    return lib


def _sample():
    with open(FONT_FIXTURE, "rb") as f:
        return f.read()


class _Font:
    """A font file read by Python, as the format says (the oracle)."""

    def __init__(self, data):
        self.w, self.h, self.line, self.ascent = data[5], data[6], data[7], data[8]
        self.first, self.count = data[9], data[10]
        advs = (self.count + 3) & ~3
        self.adv = list(data[16:16 + self.count])
        self.bpc = (self.h + 7) // 8
        self.glyphs = data[16 + advs:]

    def glyph(self, b):
        gi = b - self.first
        return gi if 0 <= gi < self.count else 0

    def raster(self, s, x, y, scale, w, h, clip=None, cam=(0, 0)):
        cx0, cy0, cx1, cy1 = clip or (0, 0, w, h)
        out = [0] * (w * h)
        x -= cam[0]
        y -= cam[1]
        for b in s:
            gi = self.glyph(b)
            for j in range(self.w):
                for row in range(self.h):
                    byte = self.glyphs[(gi * self.w + j) * self.bpc + row // 8]
                    if not byte >> (row % 8) & 1:
                        continue
                    for sy in range(scale):
                        for sx in range(scale):
                            tx, ty = x + j * scale + sx, y + row * scale + sy
                            if cx0 <= tx < cx1 and cy0 <= ty < cy1:
                                out[ty * w + tx] = 1
            x += self.adv[gi] * scale
        return out


class _Petme(_Font):
    def __init__(self):
        self.w = self.h = self.line = 8
        self.first, self.count, self.bpc = 32, 96, 1
        self.adv = [8] * 96
        self.glyphs = petme._FONT


def _draw(lib, fid, s, x, y, scale, w=96, h=40, clip=None, cam=(0, 0), maxw=-1, align=0):
    buf = (ctypes.c_uint16 * (w * h))()
    cx0, cy0, cx1, cy1 = clip or (0, 0, w, h)
    t = _Target(ctypes.addressof(buf), w * h, w, cam[0], cam[1], cx0, cy0, cx1, cy1)
    drawn = lib.moy_text(ctypes.byref(t), fid, 1, s, len(s), x, y, 0xFFFF, scale, align, maxw)
    return drawn, [1 if v else 0 for v in buf]


@pytest.fixture
def sample():
    rc, fid = moy_ui.font_load(_sample())
    assert rc == OK
    yield fid
    moy_ui.font_free(fid)


# --- petme128 through its row ---------------------------------------------------

@pytest.mark.parametrize("scale", [1, 2, 3])
def test_petme128_through_its_row_is_the_cell_raster(scale):
    lib, ref = _lib(), _Petme()
    s = bytes(range(32, 128)) + "é€".encode()
    for at, clip, cam in (((2, 3), None, (0, 0)), ((-5, -2), (3, 1, 90, 30), (0, 0)),
                          ((10, 6), (0, 0, 96, 40), (4, -3))):
        drawn, px = _draw(lib, 0, s, *at, scale, w=96, h=40, clip=clip, cam=cam)
        assert drawn == len(s) * 8 * scale
        assert px == ref.raster(s, *at, scale, 96, 40, clip, cam), (scale, at, clip, cam)
    assert moy_ui.font_info(0) == (8, 8, 8, 7, 32, 96)


# --- a second row -----------------------------------------------------------------

@pytest.mark.parametrize("scale", [1, 2])
def test_a_second_row_draws_its_own_file(sample, scale):
    lib, ref = _lib(), _Font(_sample())
    s = FONT_PROBE.encode() + b"\x7f\x01gjpqy"
    for at, clip, cam in (((1, 1), None, (0, 0)), ((-7, 2), (2, 1, 80, 17), (0, 0)),
                          ((9, 4), (0, 0, 160, 40), (3, 2))):
        drawn, px = _draw(lib, sample, s, *at, scale, w=160, h=40, clip=clip, cam=cam)
        assert drawn == sum(ref.adv[ref.glyph(b)] for b in s) * scale
        assert px == ref.raster(s, *at, scale, 160, 40, clip, cam), (scale, at, clip)
    assert any(px)
    assert _draw(lib, 0, s, 1, 1, scale, w=160)[1] != _draw(lib, sample, s, 1, 1, scale,
                                                              w=160)[1]


def test_a_second_row_measures_fits_and_aligns_by_its_advances(sample):
    lib, ref = _lib(), _Font(_sample())
    s = b"Hi, iii WWW"
    width = sum(ref.adv[ref.glyph(b)] for b in s)
    assert moy_ui.text_width(s.decode(), 1, sample) == width
    assert moy_ui.text_width(s.decode(), 3, sample) == 3 * width
    assert moy_ui.font_info(sample) == (6, 9, 11, 8, 32, 95)
    for maxw in (0, 5, 17, width - 1, width, width + 9):
        fit, at = 0, 0
        for b in s:
            at += ref.adv[ref.glyph(b)]
            if at > maxw:
                break
            fit += 1
        want = sum(ref.adv[ref.glyph(b)] for b in s[:fit])
        drawn, px = _draw(lib, sample, s, 0, 0, 1, w=120, maxw=maxw, align=2)
        assert drawn == want, maxw
        assert px == ref.raster(s[:fit], maxw - want, 0, 1, 120, 40), maxw


# --- the reader ----------------------------------------------------------------------

def _with(data, **at):
    b = bytearray(data)
    for k, v in at.items():
        b[int(k[1:])] = v
    return bytes(b)


def test_the_reader_refuses_by_its_code():
    good = _sample()
    n = len(good)
    resized = good + b"\0\0\0\0"
    cases = [
        (b"", SHORT), (good[:15], SHORT), (good[:-1], SHORT), (good + b"\0", SHORT),
        (b"x" * 65537, BIG), (b"MFNX" + good[4:], MAGIC), (_with(good, b4=3), NEWER),
        (_with(good, b4=2), BAD),                              # a version 1 file read as 2
        (_with(good, b4=0), BAD), (_with(good, b5=0), BAD), (_with(good, b5=17), BAD),
        (_with(good, b6=0), BAD), (_with(good, b6=17), BAD), (_with(good, b7=8), BAD),
        (_with(good, b7=33), BAD), (_with(good, b8=10), BAD), (_with(good, b10=0), BAD),
        (_with(good, b9=200), BAD), (_with(good, b11=1), BAD),
        (_with(good, b16=33), BAD),                            # an advance past the line cap
        (_with(good, **{"b%d" % (16 + 95): 1}), BAD),          # a padding byte set
        (_with(good, **{"b%d" % (n - 1): 2}), BAD),            # a bit below the 9-row box
        (resized[:12] + (n + 4).to_bytes(4, "little") + resized[16:], BAD),  # tables != size
    ]
    for data, want in cases:
        assert moy_ui.font_load(data)[0] == want, (len(data), data[:16], want)


def test_a_source_at_any_address_is_read_alike():
    lib = _lib()
    good = _sample()
    for off in (1, 2, 3, 5):
        buf = ctypes.create_string_buffer(b"\xaa" * off + good)
        fid = ctypes.c_uint32()
        assert lib.moy_font_load(ctypes.addressof(buf) + off, len(good), ctypes.byref(fid)) == OK
        assert moy_ui.font_info(fid.value) == (6, 9, 11, 8, 32, 95)
        assert moy_ui.font_free(fid.value)


def test_the_slots_fill_and_a_freed_id_is_refused_everywhere():
    lib = _lib()
    moy_ui.font_reset()             # the slots are the process's: a theme's font may hold one
    ids = [moy_ui.font_load(_sample()) for _ in range(SLOTS)]
    try:
        assert all(rc == OK for rc, _f in ids) and len({f for _rc, f in ids}) == SLOTS
        assert moy_ui.font_load(_sample()) == (FULL, None)
    finally:
        for _rc, fid in ids:
            assert moy_ui.font_free(fid)
    stale = ids[0][1]
    assert not moy_ui.font_free(stale) and not moy_ui.font_free(0)
    assert moy_ui.font_info(stale) is None and moy_ui.text_width("hi", 1, stale) == 0
    assert _draw(lib, stale, b"hi", 0, 0, 1)[0] == -1
    rc, again = moy_ui.font_load(_sample())
    try:
        assert rc == OK and again != stale and moy_ui.font_info(stale) is None
    finally:
        moy_ui.font_free(again)
    for bad in (1, 0xFF, 0x100 | SLOTS + 1, 0xFFFFFFFF):
        assert moy_ui.font_info(bad) is None and _draw(lib, bad, b"x", 0, 0, 1)[0] == -1


# --- the fuzz --------------------------------------------------------------------------

def test_mutated_fonts_never_break_the_reader_or_the_target():
    lib = _lib()
    w, h, guard = 64, 24, 64
    taken = 0
    for src in font_corpus(3000):
        rc, fid = moy_ui.font_load(src)
        assert 0 <= rc <= 7
        if rc != OK:
            assert fid is None
            continue
        taken += 1
        try:
            info = moy_ui.font_info(fid)
            if src[4] == 1:
                assert 1 <= info[0] <= 16 and 1 <= info[1] <= info[2] <= 32
            else:
                assert info[0] >= 1 and info[2] >= 1 and info[4] == 0 and info[5] >= 1
            buf = (ctypes.c_uint16 * (w * h + 2 * guard))(*([0x1234] * (w * h + 2 * guard)))
            t = _Target(ctypes.addressof(buf) + 2 * guard, w * h, w, 0, 0, -50, -50, 500, 500)
            for x, y, sc in ((-3, -2, 1), (40, 10, 2), (0, 20, 3)):
                lib.moy_text(ctypes.byref(t), fid, 1, bytes(range(256)), 256, x, y, 7, sc, 0, -1)
            assert list(buf[:guard]) == [0x1234] * guard
            assert list(buf[guard + w * h:]) == [0x1234] * guard
        finally:
            moy_ui.font_free(fid)
    assert taken > 50, taken


_MP_DRIVER = """
import moy_ui
f = open(@CORPUS@, "rb")
data = f.read()
f.close()
i = 0
while i < len(data):
    n = data[i] | data[i + 1] << 8
    src = data[i + 2:i + 2 + n]
    i += 2 + n
    rc, fid = moy_ui.font_load(src)
    if rc != 0:
        print(repr((rc, None, None)))
        continue
    print(repr((rc, moy_ui.font_info(fid), (moy_ui.text_width(@PROBE@, 1, fid),
                                            moy_ui.text_width(@PROBE@, 2, fid)))))
    moy_ui.font_free(fid)
"""


def test_the_boards_module_answers_as_the_host_does(tmp_path):
    exe = require_unix_mp(
        "moy_ui",
        why="Without it the font reader's MicroPython face, the one every board "
            "imports, never meets an untrusted font on the host.")
    corpus = font_corpus(600)
    blob = b"".join(len(s).to_bytes(2, "little") + s for s in corpus)
    (tmp_path / "corpus.bin").write_bytes(blob)
    script = tmp_path / "driver.py"
    script.write_text(_MP_DRIVER.replace("@CORPUS@", repr(str(tmp_path / "corpus.bin")))
                      .replace("@PROBE@", repr(FONT_PROBE)))
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=120)
    lines = out.stdout.splitlines()
    assert len(lines) == len(corpus), out.stdout[-500:] + out.stderr
    for src, line in zip(corpus, lines):
        assert eval(line) == font_answer(moy_ui, src), (len(src), line)


# --- the theme's `fonts` key ---------------------------------------------------------

def _theme(name, **keys):
    return json.dumps(dict({"format": 2, "name": name}, **keys)).encode()


NIGHT_FONTS = (("ui", "tiny5.fnt"), ("title", "tiny5.fnt"))


def test_every_seed_draws_in_tiny5():
    """`night` names Tiny5 for `ui` and `title`, and every other seed
    inherits it; `mono` stays petme128 (phase 4 step 3) but in `moy` and
    `blocks`, whose Code tab is JetBrains Mono (docs/look_2026-10.md steps
    23 and 26b), a face of the font set and not embedded in the tables."""
    for name in moy_ui.themes():
        want = NIGHT_FONTS + ((("mono", "jbmono-11-400-2.fnt"),)
                              if name in ("moy", "blocks") else ())
        assert moy_ui.theme_fonts(name) == want, name
    assert moy_ui.font_file("jbmono-11-400-2.fnt") is None


def test_the_fonts_key_names_a_file_per_role():
    rc, notes = moy_ui.theme_install(_theme("typed", fonts={
        "ui": "ui.fnt", "title": "big-title_2.fnt", "mono": "m.fnt"}))
    assert (rc, notes) == (OK, 0)
    assert moy_ui.theme_fonts("typed") == (("ui", "ui.fnt"), ("title", "big-title_2.fnt"),
                                           ("mono", "m.fnt"))
    rc, notes = moy_ui.theme_install(_theme("typed2", fonts={
        "ui": ".hidden", "title": "../up.fnt", "mono": 3, "bold": "b.fnt"}))
    assert rc == OK and notes == 2 | 4          # an unknown role; dropped names
    assert moy_ui.theme_fonts("typed2") == NIGHT_FONTS     # the default's, inherited
    rc, notes = moy_ui.theme_install(_theme("typed3", fonts={"ui": "x" * 25}))
    assert rc == OK and notes == 4 and moy_ui.theme_fonts("typed3") == NIGHT_FONTS
    assert moy_ui.theme_install(_theme("typed4", fonts=["ui.fnt"]))[1] == 4


def test_a_package_over_a_seed_keeps_its_own_roles():
    rc, _n = moy_ui.theme_install(_theme("kid", inherits="berry",
                                         fonts={"title": "t.fnt"}))
    assert rc == OK and moy_ui.theme_fonts("kid") == (("ui", "tiny5.fnt"), ("title", "t.fnt"))
    assert moy_ui.theme_fonts("no-such-theme") == moy_ui.theme_fonts("night") == NIGHT_FONTS


# --- version 2: per-glyph boxes, 1/2/4 bpp, kerning, the byte map -------------------

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "native", "moy_ui", "fonts")
BYTE_MAP = (0x2212, 0x2190, 0x2192, 0x2191, 0x2193, 0x2026, 0x2022,
            0x201C, 0x201D, 0x2018, 0x2019, 0x2013, 0x2014)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


class _Font2:
    """A version 2 file read by Python, as the format says (the oracle)."""

    def __init__(self, data):
        self.bpp, self.line, self.ascent, self.cell, self.count = data[5:10]
        nk = int.from_bytes(data[10:12], "little")
        self.index = data[16:272]
        r0 = 272
        self.recs = [data[r0 + 8 * i:r0 + 8 * i + 8] for i in range(self.count)]
        k0 = r0 + 8 * self.count
        self.kern = {(data[k0 + 3 * i], data[k0 + 3 * i + 1]):
                     ctypes.c_int8(data[k0 + 3 * i + 2]).value for i in range(nk)}
        self.cov = data[k0 + 3 * nk:]

    def bytes_of(self, s):
        out = []
        for ch in s.decode("utf-8", "replace") if isinstance(s, bytes) else s:
            cp = ord(ch)
            if cp < 0x80 or 0xA0 <= cp <= 0xFF:
                out.append(cp)
            elif cp in BYTE_MAP:
                out.append(0x80 + BYTE_MAP.index(cp))
            else:
                out.append(0)
        return out

    def rec(self, b):
        r = self.recs[self.index[b]]
        return (int.from_bytes(r[:3], "little"), r[3], r[4], ctypes.c_int8(r[5]).value,
                ctypes.c_int8(r[6]).value, r[7])

    def pens(self, bs):
        """Each glyph's pen x, and the width."""
        at, out, prev = 0, [], None
        for b in bs:
            if prev is not None:
                at += self.kern.get((prev, b), 0)
            out.append(at)
            at += self.rec(b)[5]
            prev = b
        return out, at

    def width(self, s):
        return self.pens(self.bytes_of(s))[1]

    def raster(self, bs, x, y, scale, w, h, clip=None, cam=(0, 0)):
        """1 where any coverage lands (tests/test_glyph_lane.py holds what it
        blends to)."""
        cx0, cy0, cx1, cy1 = clip or (0, 0, w, h)
        out = [0] * (w * h)
        pens, _w = self.pens(bs)
        top = (1 << self.bpp) - 1
        for b, pen in zip(bs, pens):
            off, gw, gh, gx, gy, _adv = self.rec(b)
            ox = x + (pen + gx) * scale - cam[0]
            oy = y + (self.ascent - gy) * scale - cam[1]
            for r in range(gh):
                for c in range(gw):
                    bit = (r * gw + c) * self.bpp
                    v = self.cov[off + (bit >> 3)] >> (8 - self.bpp - (bit & 7)) & top
                    if not v:
                        continue
                    for sy in range(scale):
                        for sx in range(scale):
                            tx, ty = ox + c * scale + sx, oy + r * scale + sy
                            if cx0 <= tx < cx1 and cy0 <= ty < cy1:
                                out[ty * w + tx] = 1
        return out


def _v2_files():
    return list(FONT_FIXTURES_V2) + [os.path.join(FONTS, n) for n in (
        "inter-12-400-2.fnt", "daysans-19-700-4.fnt", "jbmono-11-400-2.fnt")]


@pytest.fixture(params=_v2_files(), ids=os.path.basename)
def v2(request):
    data = _read(request.param)
    rc, fid = moy_ui.font_load(data)
    assert rc == OK
    yield fid, _Font2(data)
    moy_ui.font_free(fid)


PROBE2 = "AVTa yo., 0123 Hi é−…x"


@pytest.mark.parametrize("scale", [1, 2])
def test_a_v2_font_draws_its_coverage(v2, scale):
    lib = _lib()
    fid, ref = v2
    s = PROBE2.encode()
    for at, clip, cam in (((3, 2), None, (0, 0)), ((-5, 4), (2, 3, 150, 30), (0, 0)),
                          ((10, 6), None, (4, -3))):
        drawn, got = _draw(lib, fid, s, at[0], at[1], scale, w=160, h=40, clip=clip, cam=cam)
        assert drawn == ref.width(PROBE2) * scale
        assert got == ref.raster(ref.bytes_of(PROBE2), at[0], at[1], scale, 160, 40, clip, cam)
        assert any(got)


def test_a_v2_font_measures_kerned_widths_and_fits_whole_codepoints(v2):
    fid, ref = v2
    assert moy_ui.text_width(PROBE2, 1, fid) == ref.width(PROBE2)
    assert moy_ui.text_width(PROBE2, 3, fid) == 3 * ref.width(PROBE2)
    lib = glass_binding._lib()
    lib.moy_text_fit_px.restype = ctypes.c_size_t
    lib.moy_text_fit_px.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                    ctypes.c_int, ctypes.c_int]
    s = PROBE2.encode()
    for maxw in range(0, ref.width(PROBE2) + 2):
        k = lib.moy_text_fit_px(fid, s, len(s), maxw, 1)
        head = s[:k].decode()                        # never mid-codepoint
        assert ref.width(head) <= maxw
        if k < len(s):
            nxt = s[:k + len(PROBE2[len(head)].encode())].decode()
            assert ref.width(nxt) > maxw


def test_the_byte_map_and_malformed_utf8():
    data = _read(FONT_FIXTURES_V2[2])
    ref = _Font2(data)
    rc, fid = moy_ui.font_load(data)
    try:
        minus, dots = ref.rec(0x80), ref.rec(0x85)
        assert minus[1] and dots[1]                   # the fixture carries both
        assert moy_ui.text_width("−", 1, fid) == minus[5]
        assert moy_ui.text_width("…", 1, fid) == dots[5]
        sp = ref.rec(0x20)[5]
        lib = _lib()
        lib.moy_text_width.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                       ctypes.c_int]
        for raw, glyphs in ((b"\x80", 1), (b"\xc3", 1), (b"\xe2\x88", 2), (b"\xc0\xaf", 2),
                            (b"\xe0\x80\x80", 3), (b"\xf0\x9f\x98\x80", 1)):
            assert lib.moy_text_width(fid, raw, len(raw), 1) == glyphs * sp, raw
    finally:
        moy_ui.font_free(fid)


def test_a_v2_cut_ends_in_an_ellipsis():
    lib = _lib()
    lib.moy_text_cut_px.restype = ctypes.c_size_t
    lib.moy_text_cut_px.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t,
                                    ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    data = _read(os.path.join(FONTS, "inter-15-500-4.fnt"))
    ref = _Font2(data)
    rc, fid = moy_ui.font_load(data)
    assert rc == OK
    try:
        s = "A long window title".encode()
        full = ref.width(s)
        ell = ctypes.c_int()
        assert lib.moy_text_cut_px(fid, s, len(s), full, 1, ctypes.byref(ell)) == len(s)
        assert ell.value == 0
        for maxw in (full - 1, full // 2, 40, 26):
            k = lib.moy_text_cut_px(fid, s, len(s), maxw, 1, ctypes.byref(ell))
            assert ell.value == 1 and k < len(s) and not s[:k].endswith(b" ")
            drawn, px = _draw(lib, fid, s, 0, 0, 1, w=200, h=24, maxw=maxw)
            assert drawn <= maxw and drawn == ref.width(s[:k].decode() + "…")
        # petme128 and a version 1 font cut as they did: no ellipsis.
        p = b"petme128 cuts"
        assert lib.moy_text_cut_px(0, p, len(p), 40, 1, ctypes.byref(ell)) == 5
        assert ell.value == 0
    finally:
        moy_ui.font_free(fid)


def _v2_good():
    return _read(FONT_FIXTURES_V2[1])


def test_the_v2_reader_refuses_by_its_code():
    good = _v2_good()
    n = len(good)
    ref = _Font2(good)
    nk = len(ref.kern)
    rec0 = 272
    k0 = rec0 + 8 * ref.count
    cov0 = k0 + 3 * nk
    assert nk >= 2                         # the 2 bpp cut kerns AV, Ta, ...
    cases = [
        (_with(good, b5=3), BAD), (_with(good, b5=0), BAD), (_with(good, b5=8), BAD),
        (_with(good, b6=0), BAD), (_with(good, b7=good[6] + 1), BAD), (_with(good, b8=0), BAD),
        (_with(good, b9=0), BAD),
        (_with(good, **{"b%d" % 16: ref.count}), BAD),           # an index past the records
        (_with(good, **{"b%d" % (rec0 + 8): good[rec0 + 8] + 1}), BAD),   # an offset off its run
        (_with(good, **{"b%d" % (k0 + 2): 0}), BAD),             # a pair of 0
        (good[:k0] + good[k0 + 3:k0 + 6] + good[k0:k0 + 3] + good[k0 + 6:], BAD),  # out of order
        (good[:10] + (nk + 1).to_bytes(2, "little") + good[12:], BAD),   # tables past size
        (good[:12] + (n + 1).to_bytes(4, "little") + good[16:] + b"\0", BAD),
        (good[:-1], SHORT),
    ]
    # a pad bit set: the last byte of the first glyph whose bits leave some
    r = next(r for r in ref.recs if r[3] * r[4] * ref.bpp % 8)
    last = cov0 + int.from_bytes(r[:3], "little") + (r[3] * r[4] * ref.bpp + 7) // 8 - 1
    cases.append((_with(good, **{"b%d" % last: good[last] | 1}), BAD))
    for data, want in cases:
        assert moy_ui.font_load(data)[0] == want, (data[:16], want)
    rc, fid = moy_ui.font_load(good)
    assert rc == OK and moy_ui.font_free(fid)


def test_version_2_font_info():
    for path in FONT_FIXTURES_V2:
        data = _read(path)
        ref = _Font2(data)
        rc, fid = moy_ui.font_load(data)
        try:
            w, h, line, ascent, first, count = moy_ui.font_info(fid)
            deep = max(r[4] - ctypes.c_int8(r[6]).value for r in ref.recs if r[4])
            assert (w, line, ascent, first, count) == (ref.cell, ref.line, ref.ascent, 0,
                                                       ref.count)
            assert h == ascent + deep
        finally:
            moy_ui.font_free(fid)


# --- the font set (tools/make_font.py, native/moy_ui/fonts/) --------------------------

def _set():
    with open(os.path.join(FONTS, "set.json")) as f:
        return json.load(f)


def test_every_face_of_the_set_loads_with_its_licence():
    meta = _set()
    names = set(meta["faces"])
    on_disk = {f[:-4] for f in os.listdir(FONTS) if f.endswith(".fnt")}
    assert names == on_disk == set(ui_font_set())
    for tier, faces in meta["tiers"].items():
        assert faces and set(faces) <= names, tier
    for n, f in meta["faces"].items():
        data = ui_font_file(n + ".fnt")
        assert len(data) == f["bytes"] <= 65536
        assert data[4] == 2 and data[5] == f["bpp"]
        lic = ui_font_file(n + ".txt")
        assert lic == _read(os.path.join(FONTS, f["family"] + ".txt"))
        assert b"SIL Open Font License" in lic
        rc, fid = moy_ui.font_load(data)
        assert rc == OK, n
        try:
            assert moy_ui.text_width("Moybyte", 1, fid) > 0
        finally:
            moy_ui.font_free(fid)
    assert ui_font_file("nope-1-1-1.fnt") is None and ui_font_file("../set.json") is None


def ui_font_set():
    from runtime import fontset
    return fontset.names()


def ui_font_file(name):
    from runtime import appearance
    return appearance.font_file(name)


def test_the_mono_face_has_the_code_tabs_7px_cell():
    rc, fid = moy_ui.font_load(_read(os.path.join(FONTS, "jbmono-11-400-2.fnt")))
    try:
        assert moy_ui.font_info(fid)[0] == 7
        assert moy_ui.text_width("x" * 45, 1, fid) == 315
    finally:
        moy_ui.font_free(fid)


def test_every_slot_takes_a_face_at_once():
    moy_ui.font_reset()
    meta = _set()
    faces = meta["tiers"]["desktop"][:SLOTS]
    ids = []
    try:
        for n in faces:
            rc, fid = moy_ui.font_load(ui_font_file(n + ".fnt"))
            assert rc == OK
            ids.append(fid)
        assert moy_ui.font_load(_sample())[0] == FULL
        for role in ("ui", "title", "mono", "caption"):
            assert moy_ui.use_font(role, ids[0]) and moy_ui.font(role)[0] == ids[0]
    finally:
        for role in ("ui", "title", "mono", "caption"):
            moy_ui.use_font(role, 0)
        moy_ui.font_reset()


def test_the_code_tab_hit_tests_at_the_mono_faces_7px_cell(tmp_path):
    from runtime import host_app
    ws = host_app.build_workstation(str(tmp_path / "carts"))
    ws.launcher.sel = 0
    ws.open()
    rc, fid = moy_ui.font_load(_read(os.path.join(FONTS, "jbmono-11-400-2.fnt")))
    assert rc == OK
    try:
        assert moy_ui.use_font("mono", fid)
        ws._relayout_code()
        ws.screen = "menu"
        ws.set_menu_view("code")
        drv = host_app.ConsoleDriver(ws)
        lay, ed, cl = ws.code_layout, ws.editor, ws.code_layer
        # a row is the face's glyph box, 14 (petme128's is its line and the lead)
        assert lay.cell == 7 and lay.lh == lay.text_h == moy_ui.font_info(fid)[1] == 14
        assert lay.cols == (lay.w - lay.x0) // 7
        ed.set_text("\n".join("%02d " % i + "abcdefghij" * 4 for i in range(12)))
        drv.frame(1 / 30)
        tx0 = cl._text_x0(lay, ed)
        for col, row in ((0, 0), (3, 1), (17, 2), (min(30, lay.cols - 1), 4)):
            for dx in (0, 6):                         # either edge of the 7 px cell
                drv.touch(tx0 + (col - ed.left) * 7 + dx, lay.y0 + (row - ed.top) * lay.lh + 1)
                drv.frame(1 / 30)
                drv.frame(1 / 30)
                assert (ed.row, ed.col) == (row, col), (col, row, dx)
    finally:
        moy_ui.use_font("mono", 0)
        moy_ui.font_free(fid)
