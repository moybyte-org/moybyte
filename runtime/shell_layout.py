"""The composer: a theme's `layout` resolved for this tier, glass and mode
(docs/shell_layout_2026-10.md §9, §10).

The C loader (native/moy_ui/moy_theme.c) has already capped, clamped and
checked the data, and resolved it along `inherits` (`moy_ui.theme_layout`).
What it cannot know is the glass, the chrome scale and the mode, so the second
half runs here, on the bars it hands over:

- a bar on an edge this tier does not place is dropped;
- a widget outside the WM's catalogue for the mode, or with no cell in its
  bar's orientation, or whose minimum cell does not fit its bar, is dropped;
  the bar stays;
- the content rectangle keeps at least 75% of each axis of the glass, and the
  bars place `menu`, `zone` and `close` (the required set, again, after the
  drops), or the layout is replaced by the tier default.

Each drop and replacement is noted. Nothing here raises. Runs at a theme
switch, a `layout_theme` write and a mode switch -- never per frame.

`pack` is the other half: the cells of every bar on one surface, the
arithmetic a relayout runs once and the layout object keeps (§14).

A LEAF beside `layout_base`, the one module it imports, whose bar rects it
packs.
"""

try:
    from layout_base import BAR_ROWS, DEFAULT_BARS, bar_rects_of, bar_rows, bars as _bars
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import BAR_ROWS, DEFAULT_BARS, bar_rects_of, bar_rows, bars as _bars

EDGES = ("top", "bottom", "left", "right")
REQUIRED = ("menu", "zone")

# The catalogue (§4): each widget's minimum cell in logical pixels, (w, h),
# in a horizontal bar and in a vertical one; None where the widget is not
# placed in that orientation. `text` is measured: its string's width by the
# line height.
CELLS = {
    "menu": ((16, 16), (16, 16)),
    "close": ((16, 16), (16, 16)),
    "zone": ((64, 16), None),
    "title": ((24, 8), None),
    "clock": ((40, 8), (16, 16)),
    "wifi": ((16, 16), (16, 16)),
    "battery": ((16, 16), (16, 16)),
    "chips": ((24, 16), (16, 16)),
    "text": None,
    "spacer": ((0, 0), (0, 0)),
    "separator": ((4, 16), (16, 4)),
}
WIDGETS = tuple(CELLS)
_TEXT_H = 8
# The widgets that take their bar's slack beyond their minimum cell.
FLEX = ("zone", "title", "chips", "spacer")

# The last resort when even the default seed's layout does not resolve on
# this glass: today's one top bar, placed as is.
FALLBACK = DEFAULT_BARS


def _place(bars, w, h, cs, catalogue, edges, text_w, notes):
    """The bars that survive §10's drops, or None when §9 replaces them."""
    kept = []
    for bar in bars:
        if bar[0] in edges:
            kept.append(bar)
        else:
            notes.append("%s bar: not on this tier" % bar[0])
    rows = bar_rows(cs)
    across_h = 0
    for bar in kept:
        if bar[0] in ("top", "bottom"):
            across_h += bar[1] * rows // BAR_ROWS
    across_w = 0
    placed = set()
    out = []
    for bar in kept:
        edge, size = bar[0], bar[1]
        horiz = edge in ("top", "bottom")
        length = w if horiz else h - across_h
        thick = size * rows // BAR_ROWS
        if not horiz:
            across_w += thick
        texts = list(bar[5])
        used = 0
        slots = []
        for slot in bar[2:5]:
            got = []
            for name in slot:
                text = texts.pop(0) if name == "text" and texts else None
                if name not in catalogue:
                    notes.append("%s: not in this mode" % name)
                    continue
                if name == "text":
                    tw = text_w(text) if text_w is not None else 8 * len(text)
                    cell = (tw, _TEXT_H)
                else:
                    cell = CELLS[name][0 if horiz else 1]
                if cell is None:
                    notes.append("%s: not on a %s bar" % (name, edge))
                    continue
                along, cross = (cell[0], cell[1]) if horiz else (cell[1], cell[0])
                if cross * cs > thick or used + along * cs > length:
                    notes.append("%s: does not fit the %s bar" % (name, edge))
                    continue
                used += along * cs
                placed.add(name)
                got.append(name if text is None else (name, text))
            slots.append(got)
        names = tuple(tuple(n if type(n) is str else n[0] for n in s) for s in slots)
        strs = tuple(n[1] for s in slots for n in s if type(n) is not str)
        out.append((edge, size) + names + (strs,))
    if (w - across_w) * 4 < w * 3 or (h - across_h) * 4 < h * 3:
        notes.append("the bars leave the content under 75% of the glass")
        return None
    for name in REQUIRED:
        if name not in placed:
            notes.append("no %s after the drops" % name)
            return None
    return tuple(out)


def resolve(bars, default, w, h, cs, catalogue, edges, text_w=None, notes=None):
    """(bars, notes): `bars` (as `moy_ui.theme_layout` hands them) resolved
    for a `w` x `h` glass at chrome scale `cs`, keeping the widgets in
    `catalogue` (the WM's, for the mode) on the edges in `edges`; else
    `default` (the default seed's) resolved the same way; else FALLBACK.
    `text_w` measures a `text` widget's string at scale 1."""
    notes = [] if notes is None else list(notes)
    got = _place(bars, w, h, cs, catalogue, edges, text_w, notes)
    if got is None:
        notes.append("replaced by the default")
        got = _place(default, w, h, cs, catalogue, edges, text_w, notes)
    if got is None:
        notes.append("the default does not fit: today's bar")
        got = FALLBACK
    return got, tuple(notes)


# -- packing: the cells of a surface's bars ------------------------------------

def _along(name, horiz, cs, fs, clock_w, text, sys_len=0):
    """A widget's minimum length along its bar, in pixels."""
    if name == "text":
        return text if horiz else _TEXT_H * fs
    if name == "clock" and horiz:
        return clock_w
    if name == "menu" and sys_len:
        return sys_len
    cell = CELLS[name][0 if horiz else 1]
    if cell is None:
        return 0
    return (cell[0] if horiz else cell[1]) * cs


# The status widgets: two side by side are a status cluster, a status gap
# apart (`pack`'s `sg`), which the bar evens out by their ink.
STATUS = ("wifi", "battery", "clock")


def _gap(a, b, gap, sg):
    return sg if a[0] in STATUS and b[0] in STATUS else gap


def _run(items, start, gap, sg):
    prev = None
    for it in items:
        if prev is not None:
            start += _gap(prev, it, gap, sg)
        it[3] = start
        start += it[1]
        prev = it


def _ends(items, gap, sg):
    n = 0
    prev = None
    for it in items:
        if prev is not None:
            n += _gap(prev, it, gap, sg)
        n += it[1]
        prev = it
    return n


def _grow(items, slack):
    """Share `slack` among the flex widgets of `items`, the remainder to the
    last of them."""
    flex = [it for it in items if it[2]]
    if not flex or slack <= 0:
        return
    each = slack // len(flex)
    for it in flex:
        it[1] += each
    flex[-1][1] += slack - each * len(flex)


def pack(w, h, rows, cs, fs, clock_w, text_w=None, of=None, sys_len=0):
    """The bars of `of` (default: the bars in force) on a `w` x `h` surface
    whose BAR_ROWS-row bar is `rows` tall, at chrome scale `cs` and font
    scale `fs`: ((edge, rect, dy, cells), ...), the rect on the surface,
    `dy` what re-centres the bar's text line in it, and each cell
    (name, (x, y, w, h), text, slot) in BAR coordinates, `text` a `text`
    widget's string and None for the rest, `slot` 0, 1 or 2 for left,
    center and right.

    Along a bar, `left` packs from its start and `right` from its end, a
    `2 * cs` margin at each end and gap between cells (two status widgets,
    `STATUS`, at least `4 * fs` apart); `center` centres on
    the middle. The flex widgets (`FLEX`) share the slack of the run they
    face: up to the centre's cells when the centre holds a fixed one, else
    the whole gap between the two ends. A centre of flex widgets alone
    bounds nothing: its cell runs from the bar's first quarter to the right
    slot, and what it holds centres on the bar (today's taskbar chips).
    Across, a 16-pixel icon sits 1 pixel in on an 18-row bar and keeps that
    inset centred on a thicker one; a side bar centres it. `sys_len`, where
    set, is the `menu` cell's length along its bar: a theme's system button
    larger than an icon (the 2026-10 look's tile)."""
    if of is None:
        of = _bars()
    rects = bar_rects_of(w, h, rows, of)
    m = g = 2 * cs
    sg = max(g, 4 * fs)
    ic = 16 * cs
    out = []
    for bar, rect in zip(of, rects):
        edge = bar[0]
        horiz = edge in ("top", "bottom")
        length = rect[2] if horiz else rect[3]
        thick = rect[3] if horiz else rect[2]
        texts = list(bar[5]) if len(bar) > 5 else []
        slots = []
        for slot in bar[2:5]:
            items = []
            for name in slot:
                text = None
                tw = 0
                if name == "text":
                    text = texts.pop(0) if texts else ""
                    tw = (text_w(text) if text_w is not None else 8 * len(text)) * fs
                # [name, length, flex, position, text, slot]
                items.append([name, _along(name, horiz, cs, fs, clock_w, tw, sys_len),
                              name in FLEX, 0, text, len(slots)])
            slots.append(items)
        left, center, right = slots
        floats = bool(center) and all(it[2] for it in center)
        if center and not floats:
            cstart = (length - _ends(center, g, sg)) // 2
            if left:
                _grow(left, cstart - g - (m + _ends(left, g, sg)))
            if right:
                cend = cstart + _ends(center, g, sg)
                _grow(right, (length - m - _ends(right, g, sg)) - g - cend)
        else:
            a = m + _ends(left, g, sg) + (g if left and right else 0)
            slack = length - m - _ends(right, g, sg) - a
            both = left + right
            _grow(both, slack)
        _run(left, m, g, sg)
        _run(right, length - m - _ends(right, g, sg), g, sg)
        rstart = right[0][3] if right else length - m
        if floats:
            q = length // 4
            for it in center:
                it[1] = max(0, rstart - q)
                it[3] = q
        else:
            _run(center, (length - _ends(center, g, sg)) // 2, g, sg)
        dy = (thick - BAR_ROWS * fs) // 2
        iy = 1 + (thick - BAR_ROWS * cs) // 2 if horiz else (thick - ic) // 2
        cells = []
        for it in left + center + right:
            name, n, _flex, at, text, slot = it
            if horiz:
                if name == "menu":
                    r = (at, iy, n, ic)
                elif name in ("close", "wifi", "battery"):
                    r = (at, iy, ic, ic)
                elif name in ("zone", "title", "separator"):
                    r = (at, iy, n, ic)
                else:
                    r = (at, 0, n, thick)
            elif name == "menu":
                r = (iy, at, ic, n)
            elif name in ("close", "wifi", "battery"):
                r = (iy, at, ic, ic)
            else:
                r = (0, at, thick, n)
            cells.append((name, r, text, slot))
        out.append((edge, rect, dy, tuple(cells)))
    return tuple(out)


def without(of, names):
    """The bars `of` with the widgets `names` left out of every slot: what
    `pack` places when those widgets give their room to the flex ones."""
    return tuple(bar[:2] + tuple(tuple(n for n in slot if n not in names)
                                 for slot in bar[2:5]) + bar[5:]
                 for bar in of)


def cell_of(packed, name):
    """(bar index, cell rect) of the first `name` cell in `packed`, or None."""
    for i in range(len(packed)):
        for c in packed[i][3]:
            if c[0] == name:
                return i, c[1]
    return None
