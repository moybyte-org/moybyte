"""The focus and selection marks as drawn (docs/media/focus_2026-11/README.md,
the legibility checks): the state sheets of tools/focus_shots.py rendered on
the goldens' workstation, each focusable item focused under the keys and
under touch, per theme-mode x surface. The loader holds L1-L4 on the
declarations (moy_theme_marks, tests/test_ui_style_data.py); this holds them
on the pixels:

- L1/L2: a focus mark puts at least a 2 px line 60 % of a row high of pixels
  that clear 3:1 against what they cover;
- L3: a selected row's mark covers a fifth of the row in a colour at least
  1.3:1 from the rest row's ground;
- L5: a danger item's red ink survives its focus;
- L6: focus moves no pixel past the focused item's box and the marks' reach
  (rules, card frames and corners stay as at rest), and draws one mark;
- L9: under touch no focus pixel is drawn.

The default suite holds the T-Deck's group and buttons sheets in every
theme-mode, and the control center's rows; MOYBYTE_MARKS_FULL=1 sweeps every
surface on the T-Deck and the desk.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import focus_shots as F  # noqa: E402

L = F.L
FULL = bool(os.environ.get("MOYBYTE_MARKS_FULL"))
TMS = [(t, m) for t, m in F.MODES]

# surface -> the stops each is focused on (the sheets' own, keys-free)
STOPS = {s: sorted({f for _st, f, keys, _p, _i in states
                    if f is not None and f != "Other" and not keys})
         for s, states in F.SURFACES.items()}
DANGER = {"Danger", "Delete cart", "Address"}
SHEETS = ([(s, tag) for s in F.SURFACES for tag in ("tdeck", "desk")] if FULL
          else [("group", "tdeck"), ("buttons", "tdeck")])
CONFIG = dict(F.SHEET_CONFIGS)
HINTS = 30          # a window's foot: its hint strip

_G = []
_CACHE = {}


def _g():
    if not _G:
        _G.append(L._goldens())
    return _G[0]


def _sheet(theme, mode, surface, tag, focus, touch):
    """(RGB image, {stop: box}, region) for the sheet with `focus` focused:
    `region` the crop box the sheet's items draw in, above its hint strip
    (which shows the input's own keys)."""
    key = (theme, mode, surface, tag, focus, touch)
    if key not in _CACHE:
        from runtime import ui_draw
        apps = []
        img, _ok, _r = L.render(_g(), CONFIG[tag], theme, mode,
                                F._sheet_setup(surface, focus, (), None, apps), False,
                                "touch" if touch else None)
        app = apps[-1]
        # a stop's box is the app canvas's: on the desk, its window's content
        win = getattr(app.ws.wm, "_wins", {}).get(app.id)
        ox, oy = win.content_rect()[:2] if win is not None else (0, 0)
        if win is not None:
            x, y, w, h = win.content_rect()
            region = (x, y, x + w, y + h - HINTS)
        else:
            x0, y0, x1, y1 = F._content(img.size, CONFIG[tag])
            region = (x0, y0, x1, y1)
        boxes = {}
        for s in STOPS[surface] + ["Rest", "Selected"]:
            b = ui_draw._C.api_box(app.ctx, s)
            boxes[s] = None if b is None else (b[0] + ox, b[1] + oy, b[2], b[3])
        _CACHE[key] = (img.convert("RGB"), boxes, region)
    return _CACHE[key]


def _lum(p):
    v = [x / 255 for x in p]
    v = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in v]
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2]


def _cr(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _diff(a, b, region=None):
    """{(x, y): (a's pixel, b's pixel)} where the two renders differ, within
    crop box `region` (the sheet's content: the hint strip under it shows
    the input's own keys)."""
    x0, y0, x1, y1 = region or (0, 0) + a.size
    pa, pb = a.load(), b.load()
    return {(x, y): (pa[x, y], pb[x, y]) for y in range(y0, y1) for x in range(x0, x1)
            if pa[x, y] != pb[x, y]}


def _marks_apart(pts, reach):
    """How many marks the changed pixels make: groups of `reach`-square cells
    that touch (two changes no closer than `reach` may share a group when
    their cells touch, never the other way)."""
    left = {(x // reach, y // reach) for x, y in pts}
    n = 0
    while left:
        n += 1
        todo = [left.pop()]
        while todo:
            cx, cy = todo.pop()
            for q in [(cx + dx, cy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]:
                if q in left:
                    left.discard(q)
                    todo.append(q)
    return n


def _pixels(im):
    get = getattr(im, "get_flattened_data", None) or im.getdata
    return list(get())


def _reach():
    from runtime import ui_draw
    return max(ui_draw.mark_reach(k) for k in ("row", "cell", "card")) + 2


def _focus_checks(kb, touch, box, fs, where, region, danger=False):
    """L1/L2, L5, L6 on one focused stop: `kb` drawn under the keys, `touch`
    the same frame under touch (no focus drawn, L9), `box` the stop's."""
    d = _diff(kb, touch, region)
    clear = [p for p, (a, b) in d.items() if _cr(a, b) >= 3]
    if len(clear) < 24 * fs:
        # a mark under 3:1 on its ground reads by its edge: a keyline, or a
        # frame it lands inside (Contrast Day's yellow in its selected frame)
        pk, (w, h) = kb.load(), kb.size
        clear = [p for p, (a, _b) in d.items()
                 if any((q not in d and 0 <= q[0] < w and 0 <= q[1] < h and _cr(a, pk[q]) >= 3)
                        for q in ((p[0] + 1, p[1]), (p[0] - 1, p[1]),
                                  (p[0], p[1] + 1), (p[0], p[1] - 1)))]
    assert len(clear) >= 24 * fs, (where, "L1/L2: a focus mark that reads", len(clear))
    r = _reach() * fs
    if box is not None:
        x, y, w, h = box
        out = [p for p in d if not (x - r <= p[0] < x + w + r and y - r <= p[1] < y + h + r)]
        assert not out, (where, "L6: pixels past the item's reach", sorted(out)[:6])
    assert _marks_apart(d, r) == 1, (where, "L6: one mark")
    if danger:
        def reds(im):
            return sum(1 for p in _pixels(im.crop(_grow(box, r)))
                       if p[0] > p[1] + 80 and p[0] > p[2] + 80)
        assert reds(touch) and reds(kb) >= reds(touch) // 2, \
            (where, "L5: the danger ink survives focus", reds(touch), reds(kb))


def _grow(box, r):
    x, y, w, h = box
    return (x - r, y - r, x + w + r, y + h + r)


@pytest.mark.parametrize("theme,mode", TMS, ids=["%s-%s" % tm for tm in TMS])
@pytest.mark.parametrize("surface,tag", SHEETS, ids=["%s-%s" % s for s in SHEETS])
def test_the_sheets_marks_read(theme, mode, surface, tag):
    where = "%s-%s %s %s" % (theme, mode, surface, tag)
    fs = 1
    stops = STOPS[surface]
    base, first, region = _sheet(theme, mode, surface, tag, stops[0], True)
    for stop in stops:
        kb, boxes, _r = _sheet(theme, mode, surface, tag, stop, False)
        touch, _b, _r = _sheet(theme, mode, surface, tag, stop, True)
        moved = _diff(touch, base, region)
        if surface == "fields":
            # a focused field's caret is its typing, not a focus mark
            def inside(p):
                return any(b and b[0] <= p[0] < b[0] + b[2] and b[1] <= p[1] < b[1] + b[3]
                           for b in (boxes.get(stop), first.get(stops[0])))
            moved = [p for p in moved if not inside(p)]
        assert not moved, (where, stop, "L9: no focus under touch")
        _focus_checks(kb, touch, boxes.get(stop), fs, "%s %s" % (where, stop), region,
                      danger=stop in DANGER)


@pytest.mark.parametrize("theme,mode", TMS, ids=["%s-%s" % tm for tm in TMS])
def test_a_selected_row_reads_against_the_rest_row(theme, mode):
    """L3 on the group card: the selected row's mark covers a fifth of it in
    a colour 1.3:1 from the rest row's ground, under touch (no focus)."""
    img, boxes, _r = _sheet(theme, mode, "group", "tdeck", "Rest", True)

    def shares(box):
        x, y, w, h = box
        px = _pixels(img.crop((x, y + 1, x + w, y + h - 1)))
        counts = {}
        for p in px:
            counts[p] = counts.get(p, 0) + 1
        return {c: n / len(px) for c, n in counts.items()}
    rest = shares(boxes["Rest"])
    ground = max(rest, key=rest.get)
    sel = shares(boxes["Selected"])
    marks = [c for c, s in sel.items() if s >= 0.2 and c != ground]
    assert marks and max(_cr(c, ground) for c in marks) >= 1.3, (theme, mode, ground, marks)


@pytest.mark.parametrize("theme,mode", TMS, ids=["%s-%s" % tm for tm in TMS])
def test_the_control_centers_rows_mark_only_themselves(theme, mode):
    """The control center's rows on their panel card (findings 13): a
    focused row's mark is one mark, and touch draws none."""
    rects = []

    def cc(focus, touch):
        def setup(g, ws):
            F._cc(focus)(g, ws)
            rects.append(ws.sysmenu.comp)
        img, _ok, _r = L.render(_g(), L.HANDHELD, theme, mode, setup, False,
                                "touch" if touch else None)
        return img.convert("RGB")
    base = cc("Volume", True)
    # the panel alone: under touch the home behind it loses its hint strip
    x, y, w, h = rects[-1].rect
    region = (x, y, x + w, y + h)
    for stop in ("Brightness", "Wi-Fi"):
        kb, touch = cc(stop, False), cc(stop, True)
        assert not _diff(touch, base, region), (theme, mode, stop, "L9")
        _focus_checks(kb, touch, None, 1, "%s-%s control center %s" % (theme, mode, stop),
                      region)
