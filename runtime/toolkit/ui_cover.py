# Map (grep -n a name to jump there):
#   initial_block                     a coverless cart's (block, ink) colours
#   initial_cover                     a coverless cart's picture: its title's letter on its block
#   initial_parts                     the block and the letter's rects initial_cover draws, kept
"""The generated cover of a cart with no cover: its title's first letter
(petme128) on a colour block that stays the cart's, drawn as a cover's
picture of INITIAL_SIDE would be at any card size.

A leaf (its one import is array); ui_draw re-exports the public names.
MicroPython-safe.
"""

from array import array


# A coverless cart's cover (`initial_cover`): (block, ink) pairs of MOY64
# indices, the colours a cart's own cover draws in, each ink at 3:1 or more
# on its block: yellow, red, blue, green, pink, orange, dark purple, dark
# green, indigo, dark blue.
INITIAL_BLOCKS = ((10, 1), (8, 7), (12, 1), (11, 1), (14, 1), (9, 1), (2, 7), (3, 7),
                  (13, 7), (1, 15))


def initial_block(title):
    """The (block, ink) pair of `title`'s generated cover: the same for the
    same title on every tier and every run (no `hash`, which is salted)."""
    n = 0
    for ch in str(title or ""):
        n = (n * 31 + ord(ch)) & 0xffff
    return INITIAL_BLOCKS[n % len(INITIAL_BLOCKS)]


# The generated cover is a picture of a cover's side (SPEC.md 3.6's 128),
# drawn into any card as a cover is (`cv.cover`): its letter's pixels are
# INITIAL_UNIT of that side each -- a sixteenth, so the letter is whole
# pixels at every clean card side (`home_grid._clean_side`) and no
# taller than half the block.
INITIAL_SIDE = 128
INITIAL_UNIT = 8
_initial_runs = {}       # letter -> its ink as rects on the INITIAL_SIDE picture


def _initial_rects(cv, letter):
    """`letter`'s ink as (x0, y0, x1, y1) rects on the INITIAL_SIDE picture,
    the columns' vertical runs (a run the column before ended the same way
    widened by one): centred on the letter's own ink, not on its cell. Kept
    per letter; () where the font has no ink for it."""
    got = _initial_runs.get(letter)
    if got is not None:
        return got
    cols = cv.glyph_cols(letter)
    lit = [c for c in range(8) if cols and cols[c]]
    out = []
    if lit:
        rows = 0
        for b in cols:
            rows |= b
        r0 = 0
        while not rows >> r0 & 1:
            r0 += 1
        r1 = 7
        while not rows >> r1 & 1:
            r1 -= 1
        u = INITIAL_UNIT
        ox = (INITIAL_SIDE - (lit[-1] - lit[0] + 1) * u) // 2 - lit[0] * u
        oy = (INITIAL_SIDE - (r1 - r0 + 1) * u) // 2 - r0 * u
        open_ = {}                        # (y0, y1) -> the rect ending at this column
        for c in lit:
            x0 = ox + c * u
            j = 0
            while j < 8:
                if not cols[c] >> j & 1:
                    j += 1
                    continue
                k = j
                while k < 8 and cols[c] >> k & 1:
                    k += 1
                y0, y1 = oy + j * u, oy + k * u
                prev = open_.get((y0, y1))
                if prev is not None and out[prev][2] == x0:
                    out[prev] = (out[prev][0], y0, x0 + u, y1)
                else:
                    open_[(y0, y1)] = len(out)
                    out.append((x0, y0, x0 + u, y1))
                j = k
    got = _initial_runs[letter] = tuple(out)
    return got


def _cover_edge(a, f):
    """The first of a card's columns (or rows) that a cover drawn into it
    samples at or past the picture's column `a`, at the 16.16 step `f`:
    `cv.cover` reads column (i * f + f / 2) >> 16 for card column i."""
    e = ((a << 16) - (f >> 1) + f - 1) // f
    return e if e > 0 else 0


_initial_of = {}          # title -> (block, ink, its letter or None)
_initial_quads = {}       # (letter, w, h) -> the letter's rects in a w x h card


def initial_cover(cv, rect, r, title, stepped=False):
    """A cart with no cover's picture over `rect` (corners `r`): its title's
    first letter on a colour block that stays the cart's (`initial_block`),
    a pixel letter (petme128) drawn as a cover of INITIAL_SIDE scaled to
    `rect` would be -- each letter pixel the card columns and rows
    `cv.cover` samples it at -- so it keeps its proportion to its card at
    every size a carousel's motion passes through, as a cover's art does.
    The letter's rects at a card's size are kept, and fill in one call."""
    x, y, w, h = rect
    block, ink, q = _initial_parts(cv, title, w, h)
    cv.rrect(x, y, w, h, r, block, stepped=stepped)
    if q:
        cv.fill_rects(q, -1, x, y, ink)


def _initial_parts(cv, title, w, h):
    """(block, ink, the letter's rects in a w x h card or None) of the
    title tile `initial_cover` draws, each kept."""
    got = _initial_of.get(title)
    if got is None:
        s = str(title or "").strip()
        got = initial_block(title) + ((s[0].upper() if s else None),)
        if len(_initial_of) >= 64:
            _initial_of.clear()
        _initial_of[title] = got
    block, ink, letter = got
    if letter is None or w <= 0 or h <= 0:
        return block, ink, None
    key = (letter, w, h)
    q = _initial_quads.get(key)
    if q is None:
        fx = (INITIAL_SIDE << 16) // w
        fy = (INITIAL_SIDE << 16) // h
        q = array("h")
        for x0, y0, x1, y1 in _initial_rects(cv, letter):
            a, b = _cover_edge(x0, fx), _cover_edge(y0, fy)
            cw, ch = _cover_edge(x1, fx) - a, _cover_edge(y1, fy) - b
            if cw > 0 and ch > 0:
                q.extend((a, b, cw, ch, ink))
        if len(_initial_quads) >= 64:
            _initial_quads.clear()
        _initial_quads[key] = q
    return block, ink, q


def initial_parts(cv, title, w, h):
    """(block, the letter's rects or None) of the title tile `initial_cover`
    draws over a w x h card: its colour block's rounded rect, and the rects
    (x, y, w, h, ink) its letter fills over it, kept."""
    block, _ink, q = _initial_parts(cv, title, w, h)
    return block, q or None
