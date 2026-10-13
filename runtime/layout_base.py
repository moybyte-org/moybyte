"""`LayoutBase` -- the common head of every responsive `*Layout`, in ONE body.

Every responsive `*Layout` class in the shell (#39) starts from the same four
fields: `w`, `h`, `fs` (clamped to >= 1) and `cs`, plus the content rect
`cx, cy, cw, ch` -- the surface less the host's bar, wherever `place_bar` put
it -- and lays its surface out from them through formulas every canvas runs,
the T-Deck's 320x240 included. The same module answers the bar's own rows
(`bar_y`) and every other surface's content rect (`content_of`), so a bar
and the surfaces under it cannot disagree about where it is.
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

# Which edge of a surface the host's bar sits on: "top" or "bottom". One
# value for every surface that has a bar (bars are global,
# docs/shell_layout_2026-10.md §7); `place_bar` is the one writer.
_edge = "top"


def bar_edge():
    return _edge


def place_bar(edge):
    """Move the host's bar to `edge` on every surface. The shell relayouts
    after it (`Workstation.place_bar`); nothing here does."""
    global _edge
    if edge not in ("top", "bottom"):
        raise ValueError("bar edge: " + str(edge))
    _edge = edge


def bar_y(h, rows):
    """The first row of a `rows`-tall bar on a surface `h` rows tall."""
    return h - rows if _edge == "bottom" else 0


def content_of(w, h, rows):
    """`content_rect()` of a `w` x `h` surface under a `rows`-tall bar: the
    part the host neither paints over nor swallows taps in, `(x, y, w, h)`.
    `rows` 0 is a surface with no bar (a window's content)."""
    if rows <= 0:
        return (0, 0, w, h)
    if _edge == "bottom":
        return (0, 0, w, h - rows)
    return (0, rows, w, h - rows)


class LayoutBase:
    """The common `__init__` head. Constructed at init and relayout only,
    never per frame."""

    def __init__(self, w=BASE_W, h=BASE_H, font_scale=1, chrome_scale=None):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        self.cs = max(self.fs, int(chrome_scale)) if chrome_scale else self.fs
        # The content rect under the host bar, at the chrome scale the bar
        # takes: what every subclass lays its surface out in.
        self.cx, self.cy, self.cw, self.ch = content_of(
            self.w, self.h, BAR_ROWS * self.cs)
