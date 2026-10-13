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
from on_glass import FONT_FIXTURE, FONT_PROBE, font_answer, font_corpus  # noqa: E402
from unix_mp import require_unix_mp  # noqa: E402

OK, BIG, SHORT, MAGIC, NEWER, BAD, FULL = 0, 1, 2, 3, 4, 5, 6
SLOTS = 4


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
        (b"x" * 8193, BIG), (b"MFNX" + good[4:], MAGIC), (_with(good, b4=2), NEWER),
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
            assert 1 <= info[0] <= 16 and 1 <= info[1] <= info[2] <= 32
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
    return json.dumps(dict({"format": 1, "name": name}, **keys)).encode()


NIGHT_FONTS = (("ui", "tiny5.fnt"), ("title", "tiny5.fnt"))


def test_every_seed_draws_in_tiny5():
    """`night` names Tiny5 for `ui` and `title`, and every other seed
    inherits it; `mono` stays petme128 (phase 4 step 3)."""
    for name in moy_ui.themes():
        assert moy_ui.theme_fonts(name) == NIGHT_FONTS, name


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
