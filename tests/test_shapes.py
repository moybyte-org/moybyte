"""The 2026-10 look's shapes (native/moy_gfx/moy_shape.h, docs/look_2026-10.md
step 6) against a reference written here in Python from the header's
definitions: corner coverage, rounded rects in both corner kinds with rings
and bevels, the masks, paints, soft and hard shadows, clipping, the camera,
the radius clamps and the caches. The compiled kernel is reached through the
host binding (runtime/gfx_binding.py); tests/test_gfx_binding.py runs the
same verbs through the board's compilation under a desktop MicroPython, and
tests/test_device_canvas_parity.py through the canvas class.
"""

import random
import struct

import pytest

from runtime import gfx_binding as g

pytestmark = pytest.mark.skipif(not g.available(), reason="no C compiler")

W, H = 40, 28


def sw(v):
    return ((v << 8) | (v >> 8)) & 0xFFFF


# -- the reference ------------------------------------------------------------------

def cov(r, i, j):
    c = 8 * r
    n = 0
    for u in range(4):
        dy = c - (8 * i + 2 * u + 1)
        for s in range(4):
            dx = c - (8 * j + 2 * s + 1)
            n += dx * dx + dy * dy <= c * c
    return (n * 15 + 8) // 16


def shape_cov(w, h, r, stepped, i, j):
    if i < 0 or j < 0 or i >= h or j >= w:
        return 0
    if i < r[0] and j < r[0]:
        v = cov(r[0], i, j)
    elif i < r[1] and j >= w - r[1]:
        v = cov(r[1], i, w - 1 - j)
    elif i >= h - r[2] and j >= w - r[2]:
        v = cov(r[2], h - 1 - i, w - 1 - j)
    elif i >= h - r[3] and j < r[3]:
        v = cov(r[3], h - 1 - i, j)
    else:
        return 15
    return (15 if v >= 8 else 0) if stepped else v


def mix3(a, wa, b, wb, c, wc):
    def ch(sh, m):
        return ((((a >> sh) & m) * wa + ((b >> sh) & m) * wb + ((c >> sh) & m) * wc + 7)
                // 15)
    return (ch(11, 31) << 11) | (ch(5, 63) << 5) | ch(0, 31)


def over(f, b, wt):
    def ch(sh, m):
        return (((f >> sh) & m) * wt + ((b >> sh) & m) * (256 - wt) + 128) >> 8
    return (ch(11, 31) << 11) | (ch(5, 63) << 5) | ch(0, 31)


def clamp_r(r, w, h):
    return max(0, min(min(r, 48), min(w, h) // 2))


def ref_rrect(px, x, y, w, h, r, corners, stepped, mask, line, hi, lo, fill, cam=(0, 0),
              clip=(0, 0, W, H)):
    """moy_shape_rrect over `px` (canonical words), colours canonical."""
    rr = [clamp_r(r, w, h) if (corners >> k) & 1 else 0 for k in range(4)]
    line = max(0, min(line, min(w, h) // 2))
    iw, ih = w - 2 * line, h - 2 * line
    has_in = line > 0 and iw > 0 and ih > 0
    ri = [clamp_r(v - line, iw, ih) for v in rr] if has_in else None
    bevel = lo is not None

    def ring_col(i, j):
        if not bevel:
            return hi
        R = [max(v, line) for v in rr]
        if i < R[0] and j < R[0]:
            return hi
        if i >= h - R[2] and j >= w - R[2]:
            return lo
        if i < R[1] and j >= w - R[1]:
            return hi if i < w - 1 - j else lo
        if i >= h - R[3] and j < R[3]:
            return hi if j < h - 1 - i else lo
        return hi if (i < line or j < line) else lo

    for i in range(h):
        for j in range(w):
            X, Y = x + j - cam[0], y + i - cam[1]
            if not (clip[0] <= X < clip[2] and clip[1] <= Y < clip[3]):
                continue
            co = shape_cov(w, h, rr, stepped, i, j)
            if co == 0:
                continue
            if line:
                ci = shape_cov(iw, ih, ri, stepped, i - line, j - line) if has_in else 0
            else:
                ci = co
            wr, wf = co - ci, (ci if fill is not None else 0)
            wu = 15 - wr - wf
            if wu >= 15:
                continue
            inner = co == 15 or wr >= 8
            if (mask == 1 and not inner) or (mask == 2 and inner):
                continue
            if mask == 1 and wu:
                wr += wu
                wu = 0
            k = Y * W + X
            px[k] = mix3(px[k], wu, ring_col(i, j) if wr else 0, wr,
                         fill if wf else 0, wf)


def falloff(sd16, b):
    b16 = b * 16
    tt = (sd16 + b16) * 256 // (2 * b16) if (sd16 + b16) >= 0 else -1
    tt = max(0, min(256, tt))
    s = (tt * tt * (768 - 2 * tt)) >> 16
    return ((256 - s) * 15 + 128) >> 8


def isqrt(v):
    r = int(v ** 0.5)
    while r * r > v:
        r -= 1
    while (r + 1) * (r + 1) <= v:
        r += 1
    return r


def mask_cell(r, b, stepped, ii, jj):
    if b == 0:
        if ii >= r or jj >= r:
            return 15
        v = cov(r, ii, jj)
        return (15 if v >= 8 else 0) if stepped else v
    r16 = r * 16
    qx, qy = r16 - ((jj - b) * 16 + 8), r16 - ((ii - b) * 16 + 8)
    ox, oy = max(qx, 0), max(qy, 0)
    sd = isqrt(ox * ox + oy * oy) + min(max(qx, qy), 0) - r16
    return falloff(sd, b)


def ref_shadow(px, x, y, w, h, r, b, dx, dy, col, alpha, stepped=False, hole=None,
               cam=(0, 0), clip=(0, 0, W, H)):
    b = max(0, min(b, 64))
    r = clamp_r(r, w, h)
    if b:
        stepped = False
    C = b + max(r, b)
    wt = [(k * alpha * 256 + 15 * 255 // 2) // (15 * 255) for k in range(16)]
    sx, sy = x + dx, y + dy

    def at(ii, jj):
        if ii < C and jj < C:
            return mask_cell(r, b, stepped, ii, jj)
        k = ii if ii < C else jj if jj < C else 2 * b
        return falloff(-((k - b) * 16 + 8), b) if k < 2 * b else 15

    for Yv in range(sy - b, sy + h + b):
        Y = Yv - cam[1]
        if not clip[1] <= Y < clip[3]:
            continue
        i = Yv - sy
        ii = min(i, h - 1 - i) + b
        hx0 = hx1 = 0
        if hole is not None:
            hi_ = Yv - hole[1]
            if 0 <= hi_ < hole[3]:
                hr = max(0, min(hole[4], 48, min(hole[2], hole[3]) // 2))
                ci = hi_ if hi_ < hr else hole[3] - 1 - hi_ if hi_ >= hole[3] - hr else -1
                # a corner row's span starts at the first pixel the arc covers whole
                inset = 0 if ci < 0 else next(
                    (j for j in range(hr) if g.shape_coverage(hr, ci, j) == 15), hr)
                hx0, hx1 = hole[0] + inset - cam[0], hole[0] + hole[2] - inset - cam[0]
        for Xv in range(sx - b, sx + w + b):
            X = Xv - cam[0]
            if not clip[0] <= X < clip[2] or hx0 <= X < hx1:
                continue
            j = Xv - sx
            a = wt[at(ii, min(j, w - 1 - j) + b)]
            if a == 0:
                continue
            k = Y * W + X
            px[k] = col if a >= 256 else over(col, px[k], a)


# -- harness --------------------------------------------------------------------

def _bg(seed):
    rnd = random.Random(seed)
    return [rnd.randrange(65536) for _ in range(W * H)]


def _buf(words):
    return bytearray(struct.pack("<%dH" % len(words), *[sw(v) for v in words]))


def _words(buf):
    return [sw(v) for v in struct.unpack("<%dH" % (len(buf) // 2), bytes(buf))]


def _opts(corners, stepped, mask, line):
    return (corners & 15) | (16 if stepped else 0) | (mask << 5) | (line << 8)


def _rrect(buf, x, y, w, h, r, corners=15, stepped=False, mask=0, line=0, hi=0, lo=None,
           fill=None, cam=(0, 0), clip=(0, 0, W, H)):
    f = None if fill is None else (fill if isinstance(fill, tuple) else sw(fill))
    return g.rrect(buf, W, x, y, w, h, r, _opts(corners, stepped, mask, line), sw(hi),
                   -1 if lo is None else sw(lo), f, cam[0], cam[1], *clip)


def _shadow(buf, x, y, w, h, r, b, dx, dy, col, alpha, stepped=False, hole=None,
            cam=(0, 0), clip=(0, 0, W, H)):
    return g.shadow(buf, W, x, y, w, h, r, b, dx, dy, sw(col), alpha, stepped, hole,
                    cam[0], cam[1], *clip)


def _diff(a, b):
    bad = [(i % W, i // W, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    return "%d px differ, first %s" % (len(bad), bad[:3])


# -- corners --------------------------------------------------------------------

@pytest.mark.parametrize("r", [1, 2, 3, 5, 8, 12, 16, 31, 48])
def test_the_corner_tables_are_the_supersample(r):
    for i in range(r):
        for j in range(r):
            assert g.shape_coverage(r, i, j) == cov(r, i, j), (r, i, j)


def test_a_corner_is_whole_inside_the_arc_and_empty_outside_it():
    r = 12
    assert g.shape_coverage(r, 0, 0) == 0
    assert g.shape_coverage(r, r - 1, r - 1) == 15
    for i in range(r):
        row = [g.shape_coverage(r, i, j) for j in range(r)]
        assert row == sorted(row)                       # inward, never less
        assert row == [g.shape_coverage(r, j, i) for j in range(r)]   # symmetric


# -- rounded rects against the reference ----------------------------------------

CASES = [
    dict(x=2, y=2, w=30, h=20, r=8),
    dict(x=2, y=2, w=30, h=20, r=8, stepped=True),
    dict(x=0, y=0, w=W, h=H, r=12, corners=3),                      # a window's top
    dict(x=5, y=3, w=20, h=20, r=10),                               # a circle
    dict(x=1, y=4, w=36, h=12, r=40),                               # a pill: r clamps
    dict(x=-6, y=-4, w=30, h=20, r=9),                              # past the buffer
    dict(x=3, y=3, w=30, h=20, r=7, clip=(6, 5, 25, 18)),
    dict(x=10, y=8, w=24, h=16, r=6, cam=(4, 3)),
    dict(x=2, y=2, w=30, h=20, r=8, line=1, hi=0xf800),             # ring and fill
    dict(x=2, y=2, w=30, h=20, r=8, line=3, hi=0x001f, fill=None),  # ring only
    dict(x=2, y=2, w=30, h=20, r=6, line=1, hi=0xffff, lo=0x0000),  # a bevel
    dict(x=2, y=2, w=30, h=20, r=8, line=2, hi=0xffff, lo=0x0000, stepped=True),
    dict(x=2, y=2, w=30, h=20, r=8, mask=1),
    dict(x=2, y=2, w=30, h=20, r=8, mask=2),
    dict(x=2, y=2, w=30, h=20, r=8, line=1, hi=0xf800, mask=1),
    dict(x=2, y=2, w=30, h=20, r=8, line=1, hi=0xf800, mask=2),
    dict(x=2, y=2, w=4, h=3, r=5, line=2, hi=0xf800),               # all ring
    dict(x=2, y=2, w=0, h=5, r=2),
]


@pytest.mark.parametrize("case", CASES, ids=[str(i) for i in range(len(CASES))])
def test_rounded_rects_match_the_reference(case):
    c = dict(corners=15, stepped=False, mask=0, line=0, hi=0, lo=None, fill=0x7bef,
             cam=(0, 0), clip=(0, 0, W, H))
    c.update(case)
    bg = _bg(1)
    want = list(bg)
    ref_rrect(want, c["x"], c["y"], c["w"], c["h"], c["r"], c["corners"], c["stepped"],
              c["mask"], c["line"], c["hi"], c["lo"], c["fill"], c["cam"], c["clip"])
    buf = _buf(bg)
    _rrect(buf, c["x"], c["y"], c["w"], c["h"], c["r"], c["corners"], c["stepped"],
           c["mask"], c["line"], c["hi"], c["lo"], c["fill"], c["cam"], c["clip"])
    got = _words(buf)
    assert got == want, _diff(got, want)


def test_fuzz_rounded_rects_against_the_reference():
    rnd = random.Random(6)
    for n in range(120):
        x, y = rnd.randrange(-8, W), rnd.randrange(-8, H)
        w, h = rnd.randrange(0, 44), rnd.randrange(0, 32)
        line = rnd.choice([0, 0, 1, 2, 3])
        lo = rnd.choice([None, rnd.randrange(65536)]) if line else None
        fill = rnd.choice([None, rnd.randrange(65536)]) if line else rnd.randrange(65536)
        args = (x, y, w, h, rnd.randrange(0, 20), rnd.randrange(16), rnd.random() < 0.3,
                rnd.randrange(3), line, rnd.randrange(65536), lo, fill,
                (rnd.randrange(-5, 6), rnd.randrange(-5, 6)),
                (rnd.randrange(0, 10), rnd.randrange(0, 8), rnd.randrange(20, W + 1),
                 rnd.randrange(14, H + 1)))
        bg = _bg(n)
        want = list(bg)
        ref_rrect(want, *args)
        buf = _buf(bg)
        _rrect(buf, *args)
        assert _words(buf) == want, (args, _diff(_words(buf), want))


def test_the_inner_and_the_fringe_make_the_whole_shape():
    """A window's frame draws the inner, the desk its fringe: together they
    are the shape drawn whole (a fill; a ring's dominant pixels are the
    inner's, opaque, by design)."""
    bg = _bg(3)
    whole = _buf(bg)
    _rrect(whole, 3, 2, 32, 22, 11, fill=0x4a69)
    parts = _buf(bg)
    _rrect(parts, 3, 2, 32, 22, 11, fill=0x4a69, mask=2)
    _rrect(parts, 3, 2, 32, 22, 11, fill=0x4a69, mask=1)
    assert parts == whole


def test_the_inner_repeats_to_the_same_pixels():
    buf = _buf(_bg(4))
    _rrect(buf, 3, 2, 32, 22, 11, fill=0x4a69, mask=1, line=1, hi=0xf800)
    once = bytes(buf)
    _rrect(buf, 3, 2, 32, 22, 11, fill=0x4a69, mask=1, line=1, hi=0xf800)
    assert bytes(buf) == once


def test_stepped_corners_never_blend():
    bg = [0] * (W * H)
    buf = _buf(bg)
    _rrect(buf, 2, 2, 34, 24, 12, stepped=True, fill=0xffff)
    assert set(_words(buf)) == {0, 0xffff}
    smooth = _buf(bg)
    _rrect(smooth, 2, 2, 34, 24, 12, fill=0xffff)
    assert len(set(_words(smooth))) > 2


def test_a_circle_is_symmetric():
    buf = _buf([0] * (W * H))
    _rrect(buf, 4, 2, 24, 24, 12, fill=0xffff)
    px = _words(buf)
    for y in range(2, 26):
        row = px[y * W + 4:y * W + 28]
        assert row == row[::-1]
        assert row == [px[(2 + (x - 4)) * W + 4 + (y - 2)] for x in range(4, 28)]


def test_the_camera_is_a_shift():
    a = _buf(_bg(5))
    _rrect(a, 12, 9, 20, 14, 6, line=1, hi=0xf800, fill=0x0700, cam=(5, 4))
    b = _buf(_bg(5))
    _rrect(b, 7, 5, 20, 14, 6, line=1, hi=0xf800, fill=0x0700)
    assert a == b


def test_a_shape_at_twice_the_scale_keeps_its_proportion():
    """The shell scales a radius with its chrome: a corner at 2r over a box
    twice the size covers four times the area, to within the coverage's
    rounding (summed as the blended green channel)."""
    def area(w, h, r):
        buf = _buf([0] * (W * H))
        _rrect(buf, 0, 0, w, h, r, fill=0x07e0)
        return sum((v >> 5) & 63 for v in _words(buf)) / 63.0
    one, two = area(12, 10, 4), area(24, 20, 8)
    assert abs(two - 4 * one) < 0.01 * two


# -- paints -----------------------------------------------------------------------

def _rows(buf, x):
    px = _words(buf)
    return [px[y * W + x] for y in range(H)]


def test_a_smooth_paint_is_one_colour_a_row_from_stop_to_stop():
    buf = _buf([0] * (W * H))
    _rrect(buf, 0, 0, W, H, 0, fill=(0, 0, 0, H, 0, sw(0x0000), 1000, sw(0xffff)))
    rows = _rows(buf, W // 2)
    assert rows[0] == 0x0000 and rows[-1] == 0xffff
    assert rows == sorted(rows)
    px = _words(buf)
    for y in range(H):
        assert len(set(px[y * W:(y + 1) * W])) == 1


def test_a_stepped_paint_has_its_bands():
    buf = _buf([0] * (W * H))
    _rrect(buf, 0, 0, W, H, 0, fill=(1, 4, 0, H, 0, sw(0x0000), 1000, sw(0xffff)))
    rows = _rows(buf, 3)
    assert len(set(rows)) == 4
    assert rows[0] == 0x0000 and rows[-1] == 0xffff


def test_a_dithered_paint_is_two_stops_in_a_pattern():
    a, b = 0x1234, 0xfedc
    buf = _buf([0] * (W * H))
    _rrect(buf, 0, 0, W, H, 0, fill=(2, 0, 0, H, 0, sw(a), 1000, sw(b)))
    px = _words(buf)
    assert set(px) == {a, b}
    lit = [sum(v == b for v in px[y * W:(y + 1) * W]) for y in range(H)]
    assert lit[0] == 0 and lit[-1] == W
    # the density climbs down the rows, the pattern anchored to the buffer
    assert sum(lit[: H // 2]) < sum(lit[H // 2:])
    assert px[5 * W: 5 * W + 4] == px[5 * W + 4: 5 * W + 8]


def test_a_paint_spans_the_rows_it_names():
    """A strip's paint can run over more rows than one rect draws (a title
    strip and its body painted as one): the rows are the paint's."""
    whole = _buf([0] * (W * H))
    _rrect(whole, 0, 0, W, H, 0, fill=(0, 0, 0, H, 0, sw(0x0000), 1000, sw(0xffff)))
    half = _buf([0] * (W * H))
    _rrect(half, 0, H // 2, W, H - H // 2, 0,
           fill=(0, 0, 0, H, 0, sw(0x0000), 1000, sw(0xffff)))
    assert _words(half)[H // 2 * W:] == _words(whole)[H // 2 * W:]


# -- shadows ----------------------------------------------------------------------

SHADOWS = [
    dict(x=8, y=6, w=20, h=12, r=6, b=6, dx=0, dy=3, col=0x0000, alpha=120),
    dict(x=8, y=6, w=20, h=12, r=6, b=6, dx=0, dy=3, col=0x18a8, alpha=46,
         hole=(8, 6, 20, 12, 6)),
    dict(x=4, y=4, w=10, h=6, r=2, b=12, dx=1, dy=1, col=0x0000, alpha=255),
    dict(x=8, y=6, w=20, h=12, r=5, b=0, dx=3, dy=2, col=0xf800, alpha=255),     # hard
    dict(x=8, y=6, w=20, h=12, r=5, b=0, dx=3, dy=2, col=0xf800, alpha=90,
         stepped=True),
    dict(x=-5, y=-3, w=30, h=20, r=9, b=5, dx=0, dy=4, col=0x0000, alpha=140,
         clip=(3, 2, 30, 20), cam=(2, 1)),
    dict(x=8, y=6, w=20, h=12, r=0, b=4, dx=0, dy=0, col=0x0000, alpha=200),
    # a flat tint (a scrim): one weight for the whole run, at odd edges
    dict(x=3, y=2, w=33, h=20, r=0, b=0, dx=0, dy=0, col=0x0000, alpha=102),
    dict(x=2, y=1, w=34, h=22, r=0, b=0, dx=0, dy=0, col=0x18a8, alpha=46,
         hole=(9, 5, 13, 9, 3)),
    dict(x=1, y=1, w=35, h=20, r=6, b=6, dx=1, dy=2, col=0x0000, alpha=120,
         hole=(12, 6, 9, 7, 2)),
    dict(x=-3, y=-2, w=40, h=26, r=0, b=0, dx=0, dy=0, col=0xffff, alpha=200,
         clip=(5, 3, 31, 19), cam=(1, 1)),
]


@pytest.mark.parametrize("case", SHADOWS, ids=[str(i) for i in range(len(SHADOWS))])
def test_shadows_match_the_reference(case):
    c = dict(stepped=False, hole=None, cam=(0, 0), clip=(0, 0, W, H))
    c.update(case)
    bg = _bg(7)
    want = list(bg)
    ref_shadow(want, c["x"], c["y"], c["w"], c["h"], c["r"], c["b"], c["dx"], c["dy"],
               c["col"], c["alpha"], c["stepped"], c["hole"], c["cam"], c["clip"])
    buf = _buf(bg)
    _shadow(buf, c["x"], c["y"], c["w"], c["h"], c["r"], c["b"], c["dx"], c["dy"], c["col"],
            c["alpha"], c["stepped"], c["hole"], c["cam"], c["clip"])
    got = _words(buf)
    assert got == want, _diff(got, want)


def test_a_hole_is_left_alone():
    bg = _bg(8)
    buf = _buf(bg)
    _shadow(buf, 8, 6, 20, 12, 4, 6, 0, 3, 0x0000, 200, hole=(8, 6, 20, 12, 4))
    px = _words(buf)
    for y in range(6 + 4, 6 + 12 - 4):
        for x in range(8, 28):
            assert px[y * W + x] == bg[y * W + x]


def test_a_shadow_fades_out_its_blur_from_the_edge():
    buf = _buf([0xffff] * (W * H))
    _shadow(buf, 10, 8, 20, 12, 0, 6, 0, 0, 0x0000, 255)
    px = _words(buf)
    col = [px[y * W + 20] for y in range(H)]
    assert col[8 - 7] == 0xffff                     # past the blur: untouched
    assert col[14] == 0x0000                        # deep inside: the whole alpha
    assert col[3] > col[5] > col[8] > col[10]       # darker toward the inside


def test_a_shadow_reaches_its_blur_past_the_offset_box():
    buf = _buf([0xffff] * (W * H))
    _shadow(buf, 20 - 2, 14 - 2, 4, 4, 0, 4, 0, 2, 0x0000, 255)
    px = _words(buf)
    lit = [(i % W, i // W) for i, v in enumerate(px) if v != 0xffff]
    xs, ys = [p[0] for p in lit], [p[1] for p in lit]
    assert min(xs) >= 18 - 4 and max(xs) < 22 + 4
    assert min(ys) >= 12 + 2 - 4 and max(ys) < 16 + 2 + 4


# -- the caches -------------------------------------------------------------------

def test_a_radius_bakes_once_and_a_shadow_mask_once():
    g.shape_reset(True)
    buf = _buf([0] * (W * H))
    for _ in range(3):
        _rrect(buf, 2, 2, 30, 20, 7, fill=0xffff)
    assert g.shape_stats()[0] == 1
    _rrect(buf, 2, 2, 30, 20, 9, fill=0xffff)
    assert g.shape_stats()[0] == 2
    for _ in range(4):
        _shadow(buf, 8, 6, 20, 12, 6, 6, 0, 3, 0x0000, 120)
    st = g.shape_stats()
    assert st[1] == 1 and st[2] == 4
    _shadow(buf, 8, 6, 20, 12, 6, 2, 0, 1, 0x0000, 120)       # a drag's smaller blur
    assert g.shape_stats()[1] == 2
    _shadow(buf, 2, 2, 30, 20, 6, 6, 0, 3, 0x0000, 120)       # any size, the same mask
    assert g.shape_stats()[1] == 2


def test_a_radius_past_the_cap_draws_at_the_cap():
    a = _buf([0] * (W * H))
    b = _buf([0] * (W * H))
    g.rrect(a, W, 0, 0, W, H, 200, 15, 0, -1, 0xffff, 0, 0, 0, 0, W, H)
    g.rrect(b, W, 0, 0, W, H, H // 2, 15, 0, -1, 0xffff, 0, 0, 0, 0, W, H)
    assert a == b


# -- the board's cases ----------------------------------------------------------

def test_the_conformance_shape_cases_agree_under_micropython(tmp_path):
    """tools/p4_conformance.py --shapes runs SHAPE_RUN on a board beside the
    host binding. The same snippet under the desktop MicroPython (the board's
    module compiled for the host) answers the host's CRCs: the cases are sound
    and the snippet runs where the board runs it."""
    import subprocess
    import sys as _sys
    from pathlib import Path
    from unix_mp import require_unix_mp
    root = Path(__file__).resolve().parent.parent
    _sys.path.insert(0, str(root / "tools"))
    import p4_conformance as pc
    exe = require_unix_mp("moy_gfx", why="the shape cases run under the board's module")
    script = tmp_path / "shapes.py"
    script.write_text(pc.SHAPE_RUN + "\nprint(_shapes(1))\n")
    out = subprocess.run([exe, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    got = eval(out.stdout.strip().splitlines()[-1])           # noqa: S307
    assert got == pc.shapes_host()
    assert len(set(got)) == len(got)        # every case draws something its own
