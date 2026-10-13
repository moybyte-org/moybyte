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

A LEAF: it imports nothing from the runtime tree.
"""

EDGES = ("top", "bottom", "left", "right")
REQUIRED = ("menu", "zone", "close")

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
    "calendar": ((112, 80), (112, 80)),
    "wifi": ((16, 16), (16, 16)),
    "battery": ((16, 16), (16, 16)),
    "chips": ((24, 16), (16, 16)),
    "text": None,
    "spacer": ((0, 0), (0, 0)),
    "separator": ((4, 16), (16, 4)),
}
WIDGETS = tuple(CELLS)
_TEXT_H = 8

# The last resort when even the default seed's layout does not resolve on
# this glass: today's one top bar, placed as is.
FALLBACK = (("top", 18, ("zone",), (), ("clock", "close", "menu", "wifi", "battery"), ()),)


def _place(bars, w, h, cs, catalogue, edges, text_w, notes):
    """The bars that survive §10's drops, or None when §9 replaces them."""
    kept = []
    for bar in bars:
        if bar[0] in edges:
            kept.append(bar)
        else:
            notes.append("%s bar: not on this tier" % bar[0])
    across_h = 0
    for bar in kept:
        if bar[0] in ("top", "bottom"):
            across_h += bar[1] * cs
    across_w = 0
    placed = set()
    out = []
    for bar in kept:
        edge, size = bar[0], bar[1]
        horiz = edge in ("top", "bottom")
        length = w if horiz else h - across_h
        thick = size * cs
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
