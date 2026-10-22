"""Paint's marks in the kernel (moy_gfx's one-byte-a-pixel ops) make the
pixels, the undo tiles and the dirty rect the Python marks made.

`_Was` is the Python body the marks had, kept here as the reference: every
mark a kid makes -- the pencil's and the brush's dots, the eraser's squares,
the box, the oval, the fill -- runs on both, and the drawing, each undo
step's tiles and their pixels, and the dirty rect must agree."""

import random

import pytest

from runtime.artwork import PaintDocument


class _Was:
    TILE = 32

    def __init__(self, w, h):
        self.W, self.H = w, h
        self.pix = bytearray(bytes((PaintDocument.PAPER,)) * (w * h))
        self.tiles_x = (w + self.TILE - 1) // self.TILE
        self.act = {}
        self.dirty = None

    def _tile(self, t):
        T = self.TILE
        x, y = (t % self.tiles_x) * T, (t // self.tiles_x) * T
        w, h = min(T, self.W - x), min(T, self.H - y)
        return bytes(b for r in range(h) for b in self.pix[(y + r) * self.W + x:(y + r) * self.W + x + w])

    def _grow(self, x0, y0, x1, y1):
        d = self.dirty
        self.dirty = [x0, y0, x1, y1] if d is None else [min(d[0], x0), min(d[1], y0),
                                                         max(d[2], x1), max(d[3], y1)]

    def _touch(self, x0, y0, x1, y1):
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.W - 1, x1), min(self.H - 1, y1)
        if x1 < x0 or y1 < y0:
            return False
        self._grow(x0, y0, x1 + 1, y1 + 1)
        T, tx = self.TILE, self.tiles_x
        for ty in range(y0 // T, y1 // T + 1):
            for t in range(ty * tx + x0 // T, ty * tx + x1 // T + 1):
                if t not in self.act:
                    self.act[t] = self._tile(t)
        return True

    def _span(self, x0, x1, y, c):
        if y < 0 or y >= self.H:
            return
        x0, x1 = max(0, x0), min(self.W - 1, x1)
        if x1 >= x0:
            self.pix[y * self.W + x0:y * self.W + x1 + 1] = bytes((c,)) * (x1 - x0 + 1)

    def _dot(self, x, y, c, d, round_):
        x0, y0 = x - d // 2, y - d // 2
        if not round_ or d <= 2:
            for r in range(d):
                self._span(x0, x0 + d - 1, y0 + r, c)
            return
        h2 = d / 2
        for r in range(d):
            dy = r + 0.5 - h2
            k = h2 * h2 - dy * dy
            if k < 0:
                continue
            i1 = min(d - 1, int(h2 - 0.5 + k ** 0.5))
            self._span(x0 + d - 1 - i1, x0 + i1, y0 + r, c)

    def line(self, x0, y0, x1, y1, c, d, round_):
        a = d // 2
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        err = dx - dy
        while True:
            if self._touch(x0 - a, y0 - a, x0 - a + d - 1, y0 - a + d - 1):
                self._dot(x0, y0, c, d, round_)
            if x0 == x1 and y0 == y1:
                return
            e2 = err + err
            if e2 > -dy:
                err -= dy
                x0 += sx
            if e2 < dx:
                err += dx
                y0 += sy

    def box(self, a, b, c, filled, d):
        x0, x1 = min(a[0], b[0]), max(a[0], b[0])
        y0, y1 = min(a[1], b[1]), max(a[1], b[1])
        if not self._touch(x0, y0, x1, y1):
            return
        for y in range(y0, y1 + 1):
            if filled or 2 * d >= min(x1 - x0, y1 - y0) + 1 or y < y0 + d or y > y1 - d:
                self._span(x0, x1, y, c)
            else:
                self._span(x0, x0 + d - 1, y, c)
                self._span(x1 - d + 1, x1, y, c)

    def flood(self, x, y, color):
        W, pix = self.W, self.pix
        old = pix[y * W + x]
        if old == color:
            return
        stack = [(x, y)]
        while stack:
            sx, sy = stack.pop()
            row = sy * W
            if pix[row + sx] != old:
                continue
            left = right = sx
            while left > 0 and pix[row + left - 1] == old:
                left -= 1
            while right + 1 < W and pix[row + right + 1] == old:
                right += 1
            self._touch(left, sy, right, sy)
            self._span(left, right, sy, color)
            for ny in (sy - 1, sy + 1):
                if 0 <= ny < self.H:
                    px = left
                    while px <= right:
                        if pix[ny * W + px] == old:
                            stack.append((px, ny))
                            while px <= right and pix[ny * W + px] == old:
                                px += 1
                        px += 1


def _agree(doc, was):
    """After the mark's finish: the drawing, the step it left (none at all
    where its tiles outgrow every step's room, as trimming it leaves none),
    and the dirty rect, which may only be wider."""
    assert bytes(doc.pix) == bytes(was.pix)
    if was.dirty is not None:
        got = doc.dirty
        assert got is not None
        assert got[0] <= was.dirty[0] and got[1] <= was.dirty[1]
        assert got[2] >= was.dirty[2] and got[3] >= was.dirty[3]
    if not was.act:
        return
    if sum(len(v) for v in was.act.values()) > PaintDocument.UNDO_BYTES:
        assert doc.history == [] and doc.future == []
        return
    act = dict(doc.history[-1])
    assert sorted(act) == sorted(was.act)
    for t in act:
        assert bytes(act[t]) == was.act[t], "tile %d recorded differently" % t


@pytest.mark.parametrize("w, h", [(320, 240), (1024, 600)])
def test_the_kernels_marks_are_the_python_marks(w, h):
    rnd = random.Random(w * 7 + h)
    doc = PaintDocument(w, h)
    was = _Was(w, h)
    lost = 0
    for step in range(60):
        kind = rnd.choice(("line", "line", "dot", "erase", "box", "fill"))
        c = rnd.randrange(64)
        x0, y0 = rnd.randrange(-20, w + 20), rnd.randrange(-20, h + 20)
        x1, y1 = x0 + rnd.randrange(-90, 90), y0 + rnd.randrange(-90, 90)
        d = rnd.choice((1, 2, 3, 4, 5, 8, 16, 28))
        doc.snapshot()
        was.act = {}
        doc.dirty = was.dirty = None
        if kind == "line":
            doc.line(x0, y0, x1, y1, c, d, True)
            was.line(x0, y0, x1, y1, c, d, True)
        elif kind == "dot":
            round_ = rnd.random() < 0.5
            doc.stamp(x0, y0, c, d, round_)
            was.line(x0, y0, x0, y0, c, d, round_)
        elif kind == "erase":
            doc.line(x0, y0, x1, y1, PaintDocument.PAPER, d * 2, False)
            was.line(x0, y0, x1, y1, PaintDocument.PAPER, d * 2, False)
        elif kind == "box":
            a, b = (x0, y0), (x1, y1)
            filled = rnd.random() < 0.3
            doc.box(a, b, c, filled, d)
            was.box(a, b, c, filled, d)
        else:
            fx, fy = rnd.randrange(w), rnd.randrange(h)
            doc.flood(fx, fy, c)
            was.flood(fx, fy, c)
        doc.finish()
        _agree(doc, was)
        lost += sum(len(v) for v in was.act.values()) > PaintDocument.UNDO_BYTES
    if w > 512:
        assert lost, "no mark outgrew the undo's room: that rule went untested"
