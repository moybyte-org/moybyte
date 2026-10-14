"""`LayoutBase` -- the common head of every responsive `*Layout`, in ONE body.

Every responsive `*Layout` class in the shell (#39) starts from the same four
fields: `w`, `h`, `fs` (clamped to >= 1) and `cs`, plus the content rect
`cx, cy, cw, ch` -- the surface less the host's bars, wherever `place_bars`
put them -- and lays its surface out from them through formulas every canvas
runs, the T-Deck's 320x240 included. The same module answers the bars' own
rects (`bar_rects_of`) and every other surface's content rect (`content_of`), so
the bars and the surfaces under them cannot disagree about where they are.
(Until theming phase 3 step 4 the base also held `_base`, a predicate that sent
320x240 / font scale 1 down frozen constants; `tests/test_layout_base.py`
keeps it from coming back.) What the base deliberately does NOT own is
everything the subclasses diverge on immediately after -- the per-class
`font_w`/`cell`/`lh` cell metrics, and the panel-rect block that map/paint/
scene share (`px, py = 8 * fs ...`). Those stay per class: this base is a
head, not a geometry library.

`cs` is the CHROME scale (#203): `fs` sizes TEXT, `cs` sizes the things a
FINGER lands on, and `cs >= fs` with `cs == fs` as the default. A board opts in
by declaring the physical size of its glass (`chrome.chrome_scale_floor`),
because "is a 16px icon big enough to tap" is a question only millimetres can
answer and only a board knows its own. The 3.5" Guition is the case that forced
it: 165 PPI put a bar icon at 2.5mm, and raising the FONT scale to compensate
was built, shipped and reverted the same day (#202, 2026-08-19).

**This module is a LEAF and has to stay one.** It imports nothing from the
runtime tree, because the obvious home -- beside `chrome.Layout` -- is not
importable by the classes that need it: `chrome.py` imports its geometry
constants FROM `bar_layer`/`settings_layer`/`code_layer`, so a base living
there is a cycle for exactly the editor surfaces that subclass it
(`docs/history/console_architecture_2026-08.md` §6).

The APP tier is a declared second vocabulary and is not here:
`app_shell.ListShellLayout` and the calc/appearance/artwork heads speak
`(w, h, fs, windowed=)`.
"""

# The T-Deck's system canvas: every layout's default size.
BASE_W = 320
BASE_H = 240

# The host bar's rows at chrome scale 1 (16px icons, 1px above and below).
# `bar_layer._STATUS_H` is this number.
BAR_ROWS = 18

# The bars in force on every surface that has them (bars are global,
# docs/shell_layout_2026-10.md §7): the theme layout as `shell_layout.resolve`
# left it, each (edge, size, left, center, right, texts) with `size` in
# logical rows. `place_bars` is the one writer.
DEFAULT_BARS = (("top", BAR_ROWS, ("zone",), ("chips",),
                 ("clock", "close", "menu", "wifi", "battery"), ()),)
# A window's toolbar band: the default bar at the window's own top, whatever
# the layout -- the bar layer draws its zone alone there.
BAND = DEFAULT_BARS
_bars = DEFAULT_BARS
_band = False


def bars():
    """The bars a surface laid out now takes: a window's band while its
    layouts build (`in_window`), else the bars in force."""
    return BAND if _band else _bars


def bars_in_force():
    return _bars


def bar_edge():
    """The first bar's edge."""
    return _bars[0][0]


def place_bars(new):
    """Put the host's bars on every surface. The shell relayouts after it
    (`Workstation.place_bars`); nothing here does."""
    global _bars
    for bar in new:
        if bar[0] not in ("top", "bottom", "left", "right"):
            raise ValueError("bar edge: " + str(bar[0]))
    _bars = tuple(new)


def place_bar(edge):
    """Today's one bar, moved to `edge`."""
    place_bars(((edge,) + DEFAULT_BARS[0][1:],))


def in_window(on):
    """While a window's layouts build, the surface is a window buffer and
    takes the band (`WindowedWM._make_ctx`)."""
    global _band
    _band = bool(on)


def bar_rects_of(w, h, rows, of=None):
    """Each bar of `of` (default: `bars()`) on a `w` x `h` surface, as
    (x, y, w, h), in the list's order. `rows` is a BAR_ROWS-row bar's
    height at the surface's chrome scale, so a bar `size` rows thick is
    `size * rows // BAR_ROWS`. Top and bottom bars span the surface; left
    and right bars the rows between them."""
    if of is None:
        of = bars()
    top = bottom = 0
    for bar in of:
        if bar[0] == "top":
            top = bar[1] * rows // BAR_ROWS
        elif bar[0] == "bottom":
            bottom = bar[1] * rows // BAR_ROWS
    out = []
    for bar in of:
        t = bar[1] * rows // BAR_ROWS
        e = bar[0]
        if e == "top":
            out.append((0, 0, w, t))
        elif e == "bottom":
            out.append((0, h - t, w, t))
        elif e == "left":
            out.append((0, top, t, h - top - bottom))
        else:
            out.append((w - t, top, t, h - top - bottom))
    return tuple(out)


def content_of(w, h, rows, of=None):
    """`content_rect()` of a `w` x `h` surface under the bars (`of`,
    default `bars()`), each sized off `rows` as `bar_rects_of` says: the
    part the host neither paints over nor swallows taps in, `(x, y, w, h)`.
    `rows` 0 is a surface with no bar (a window's content)."""
    if rows <= 0:
        return (0, 0, w, h)
    if of is None:
        of = bars()
    x0, y0, x1, y1 = 0, 0, w, h
    for bar in of:
        t = bar[1] * rows // BAR_ROWS
        e = bar[0]
        if e == "top":
            y0 = t
        elif e == "bottom":
            y1 = h - t
        elif e == "left":
            x0 = t
        else:
            x1 = w - t
    return (x0, y0, x1 - x0, y1 - y0)


class LayoutBase:
    """The common `__init__` head. Constructed at init and relayout only,
    never per frame."""

    def __init__(self, w=BASE_W, h=BASE_H, font_scale=1, chrome_scale=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        self.cs = max(self.fs, int(chrome_scale)) if chrome_scale else self.fs
        # The content rect under the host's bars, at the chrome scale they
        # take: what every subclass lays its surface out in.
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, BAR_ROWS * self.cs)
