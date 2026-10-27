# Map (grep -n a name to jump there):
#   new_tile                    the "+ New" card
#   Launcher                    the grid arrangement: a shelf of cards that scrolls sideways
#   Launcher._lay_cells         its geometry on a glass (_Cells)
#   Launcher.nav2d              cell navigation
#   Launcher.pointer_frame      one pointer sample over the cells
#   Launcher.cover_specs        every cover the next full draw needs
#   Carousel                    the home's row: the centre card scaled by its distance
#   Carousel.anim_frame         the fling, the glide to the centre, the settle
#   Carousel.draw_shift         the moving frame: the wallpaper restored around the cards
#   IconHome                    the icons arrangement: tiles that scroll down, a caption band
#   LauncherHomeLayer           the home: wallpaper, the cells, the bar's tabs, the hints
#   LauncherHomeLayer.draw      the home frame
#   LauncherHomeLayer.zone_tap  the tabs in the bar's zone
#   HomeWindowLayer             the home in a desktop-mode window (PROJECTS)
"""The home (#28), extracted from Workstation (runtime/console.py) as a Layer
-- docs/history/shell_layers_refactor_v1.md (Move 1b).

  * `Launcher`     -- the selection model (items/sel/scroll/nav) and the
                      `grid` arrangement's draw (docs/look_2026-10.md step
                      33): a shelf of cover cards in the rows the glass
                      holds, scrolling sideways. Its instance is ws.launcher
                      in the home's `grid` arrangement.
  * `Carousel`     -- the home's row (docs/shell_layout_2026-10.md §12): the
                      same selection model over one row of square cover cards,
                      the centre card larger by a function of its distance
                      from the centre; on the desk's metrics the selected
                      card is the hero beside its details over a shelf. Its
                      instances are ws.launcher in the `carousel`
                      arrangement, the default (the layout's `home`), and
                      ws.projects_grid, the home window's, always.
  * `IconHome`     -- the `icons` arrangement: the same model over tiles of
                      the covers' reduction that scroll down, the focused
                      cart named in a caption band on the handheld and
                      every tile named under it on the desk.
  * `LauncherHomeLayer` -- the "launcher" content Layer: the home, console mode's
                      root on every board (wallpaper -> cells -> hint strip ->
                      bar, its tabs in the bar's zone), its four tabs (Games / Mine / Apps / Make, the lists
                      ws._launcher_items builds) and their input. A tap on the
                      centre card is the active tab's (ws.launch_selected).
  * `HomeWindowLayer` -- the "projects" content Layer: the SAME home in a
                      desktop-mode window (docs/shell_layout_2026-10.md §12, the
                      desk's PROJECTS): the console home's design over
                      ws.projects_grid, laid out in the window's content
                      rect, opened on its Make tab.

Boundary (single source of truth): ws.launcher (the instance) + ws.open() (open the
selected cart -- lifecycle, pinned) + the cart store stay on Workstation. The card
draw needs the palette -- `NAMES` is INJECTED at construction. The rect
hit-test is `ui.rect_in`. No circular import: this is a leaf (the only console touch is a lazy Layout fallback
for a bare Launcher() that no caller ever constructs).

The lent bar zone (#46): each home layer draws and taps it (`draw_zone`/`zone_tap`)
and answers its `zone_gen` -- one int of what the zone shows, the home's tabs (the
open one and the tab row's focus), so BarLayer's strip cache trusts one int. The
grid's own `zone_gen` (bumped whenever its `sel` or `items` changes) is the
grid's.
"""


try:
    import ui as _ui
    import ui_draw as _ui_draw
    import cart_listing as _cl
    import iconsets as _iconsets
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import cart_listing as _cl
    from runtime import iconsets as _iconsets
try:
    from moy_input import SHIFT as _SHIFT
except ImportError:  # pragma: no cover - host fallback
    from runtime.moy_input import SHIFT as _SHIFT
_in = _ui.rect_in   # one hit-test (ui.rect_in)


# The tick helpers feed the perf_capture-gated home-frame split (see draw below).
try:
    from chrome import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _ticks_ms, _ticks_diff

try:
    import layout_base as _lb
    import shell_layout as _shell_layout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _lb
    from runtime import shell_layout as _shell_layout

try:
    from moy_store_base import builtin_name as _builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_store_base import builtin_name as _builtin_name


# The "+ New" card (Make's first, an empty Mine's only) is a
# PSEUDO entry (not a real cart): a plain dict with a marker `type` + a title,
# flowing through the SAME grid (nav/sel/tile_at) as real carts, dispatched by its
# type at tap time (create a game + open the Editor). `path` is None so cart
# management (dup/del) + the icon-sheet loader skip it, and a fresh dict per call
# avoids aliasing one shared mutable tile.
NEW_TILE_TYPE = "new"
PSEUDO_TILE_TYPES = (NEW_TILE_TYPE,)

# A cover's side (SPEC.md 3.6) and the decodes of it a card draws:
# CoverCache's BASE and HALF (cover_cache.py names them).
_COVER_SIDE = 128
_COVER_BASE = 1
_COVER_HALF = 2
_UNSET = -2147483648      # a look leaf the theme leaves silent


def _cover_scale(side, w, h):
    """The whole-number scale a side x side picture takes in a w x h slot: the
    largest that fits, or the next one up when that overflows the slot by at
    most a tenth in each direction (the overflow is cropped, centred). 0:
    not even 1x near-fits."""
    s = max(0, min(w, h) // side)
    n = s + 1
    if 10 * side * n <= 11 * w and 10 * side * n <= 11 * h:
        return n
    return s


def new_tile():
    return {"type": NEW_TILE_TYPE, "title": "New", "path": None}


# The New tile's plus across the tile, in 32nds of its side: about the
# third a hand-drawn "add" page gives it.
NEW_PLUS = 11


def draw_new_art(cv, th, rect, r, stepped, spec, fs=1):
    """The "+ New" card's picture over `rect` (corners `r`): a blank page,
    the theme's `surface`, with icon set `spec`'s plus (`iconsets.plus`) in
    `ink_accent`, the accent ink the theme holds readable on that surface.
    No rim, as a cover has none; but where the theme casts no shadow a page
    on a ground of its own colour would show only its plus, so there it is
    framed in the theme's `border`, 2 px at font scale `fs`: the page
    inside the frame keeps a cover's corners `r` and the frame's run round
    them, `r` plus its width, so the frame's corner reads rounded at 1x as
    a filled cover's does where a thin line at `r` reads square (design
    review R5-10, F6-04)."""
    x, y, w, h = rect
    if not _ui_draw.depth()[5]:
        line = 2 * fs
        cv.rrect(x, y, w, h, r + line, th.get("border", 0), stepped=stepped)
        cv.rrect(x + line, y + line, w - 2 * line, h - 2 * line, r, th.get("surface", 0),
                 stepped=stepped)
    else:
        cv.rrect(x, y, w, h, r, th.get("surface", 0), stepped=stepped)
    s = min(w, h) * NEW_PLUS // 32
    _iconsets.plus(cv, (x + (w - s) // 2, y + (h - s) // 2, s, s),
                   th.get("ink_accent", th.get("ink", 0)), spec, stepped)


def _dot_top(y, s, d):
    """The top of a `d`-pixel dot between words of the `ui` face drawn at
    row `y` and scale `s`: on the middle of a version 2 face's lowercase
    (its x-height taken as half its ascent), on the line's middle in a
    pixel face."""
    f = _ui_draw.font()
    if not _ui_draw.font_v2(f[0]):
        return y + (f[2] * s - d) // 2
    asc = f[3] * s
    return y + asc - (asc // 2 + d) // 2


_STAR = {}           # chip side -> (edge runs, inside runs) of its star


def _star_runs(side):
    """The star of a `side`-square chip as runs (dx, dy, w) from the chip's
    top left: (its edge, its inside). A five-pointed star, point up, about
    10 pixels across in a 16-pixel chip so the chip's rim stands clear of
    its points (design review R4-30), its box centred on the chip, sampled
    at pixel centres about the chip's centre pixel, so its halves mirror
    each other; its edge the pixels with a side out of it. Made once a
    side."""
    got = _STAR.get(side)
    if got is not None:
        return got
    import math
    ro = side * 0.31
    ri = ro * 0.42
    c = side // 2
    pts = []
    for k in range(10):
        a = math.pi * (k / 5.0 - 0.5)
        r = ro if k % 2 == 0 else ri
        # its points reach ro up and 0.81 ro down: lowered by half the odds
        pts.append((r * math.cos(a), r * math.sin(a) + ro * 0.095))

    def inside(px, py):
        hit = False
        j = 9
        for i in range(10):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                hit = not hit
            j = i
        return hit

    on = [[inside(x - c, y - c) for x in range(side)] for y in range(side)]

    def edge(x, y):
        return any(not (0 <= x + dx < side and 0 <= y + dy < side and on[y + dy][x + dx])
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))

    runs = ([], [])
    for y in range(side):
        for want in (0, 1):
            x = 0
            while x < side:
                if on[y][x] and (edge(x, y) == (want == 0)):
                    x0 = x
                    while x < side and on[y][x] and (edge(x, y) == (want == 0)):
                        x += 1
                    runs[want].append((x0, y, x - x0))
                else:
                    x += 1
    got = _STAR[side] = (tuple(runs[0]), tuple(runs[1]))
    return got


def _draw_star_badge(cv, names, th, rect, on):
    """The favourite star's corner badge (#105): a round chip on the
    theme's surface holding a five-pointed star, gold inside an orange edge
    when `on`, its edge alone in the dim ink when not. The chip's rim is the
    dim ink too, so it holds on a cover as dark as the chip."""
    x, y, side, _h = rect
    cv.rrect(x, y, side, side, side // 2, th.get("surface", names["white"]),
             ring=th.get("ink_dim", names["light_grey"]))
    edge, inner = _star_runs(side)
    if on:
        gold = names["yellow"]
        for dx, dy, w in inner:
            cv.rect(x + dx, y + dy, w, 1, gold)
        ink = names["orange"]
    else:
        ink = th.get("ink_dim", names["light_grey"])
    for dx, dy, w in edge:
        cv.rect(x + dx, y + dy, w, 1, ink)


def _hint_strip(lay, keys, desk):
    """The home's hint strip along the content's foot, (x, y, w, h): the
    handheld's 20 rows at the font scale, the desk's 34 glass pixels at the
    target's 1x or 18 rows, none under touch (no `keys`), whose cards take
    its height. Every arrangement's, so the strip stays put across them."""
    if desk:
        h = max(34 // _lb.render_scale(), 18 * lay.fs) if keys else 0
    else:
        h = 20 * lay.fs if keys else 0
    return (lay.cx, lay.cy + lay.ch - h, lay.cw, h)


# The grid arrangement's shelf (docs/look_2026-10.md step 33): on the
# handheld at the font scale, on the desk in glass pixels at the target's
# 1x -- the margin the first column stands in from the glass's edge, the
# least and the most gap a column takes, the shelf's top under the bar, the
# title's baseline under its card and the rows a card's title takes.
_SHELF_HH = (16, 20, 28, 14, 17, 28)
_SHELF_DK = (48, 32, 44, 24, 26, 44)
# The icons arrangement's tiles: a narrow handheld's tile, a wide one's
# (`_WIDE` or more across), the desk's; the column gap, the row gap on the
# handheld and the desk's (its label under the tile), the grid's top under
# the bar, the desk's label baseline under its tile and the handheld's
# caption band.
_ICONS_HH = (40, 52, 18, 8, 8, 28)
_ICONS_DK = (64, 32, 44, 24, 20)
_WIDE = 400
_GROW = (3, 4)       # what focus grows a card by: the handheld's, the desk's
_FADE = 12           # the handheld shelf's last rows dissolve into the hint
                     # strip over this many rows, at the font scale


class _Cells:
    """A grid arrangement's geometry on a glass (`Launcher._cells`): `side`
    the card's (or tile's) square, its corners `r`; the first cell's left
    `x0` and top `y0` at scroll 0; the pitches `px` and `py`; the `rows`
    and `cols` the glass shows whole or cut; `band` the rect the cells lie
    in, `hints` the hint strip's and `caption` the focused cart's band (the
    handheld icons'), else None; `title_dy` the title's top under a card's,
    `title_w` its width, `grow` what focus grows a card by, `desk` the
    class."""

    def __init__(self, side, r, x0, y0, px, py, rows, cols, band, hints, caption,
                 title_dy, title_w, grow, desk):
        self.side = side
        self.r = r
        self.x0 = x0
        self.y0 = y0
        self.px = px
        self.py = py
        self.rows = rows
        self.cols = cols
        self.band = band
        self.hints = hints
        self.caption = caption
        self.title_dy = title_dy
        self.title_w = title_w
        self.grow = grow
        self.desk = desk


class Launcher:
    """The home's grid arrangement (docs/look_2026-10.md step 33): a shelf
    of cards, each cart's whole cover at a clean scale with the card radius
    and its title under it cut to the card's width, in as many rows as the
    glass holds under the bar (two on the T-Deck, three on the desk),
    scrolling sideways, its columns spaced so the last one on the glass
    stands cut by a third or more. The focused card grows and wears the
    theme's ring, flat -- no shadow over its neighbours -- its title bold.
    The cards lie on the home's ground, the tabs in the bar and the verbs in
    the hint strip, as the carousel's do: Enter plays, C changes.

    Keeps the selection model (items, sel, selected) the console relies on;
    `scroll` is the shelf's pixel offset along `horizontal`'s axis, ridden
    through a ui.ScrollRegion and ui.DragTap (a drag, a fling, a tap that
    never fires from a drag). `IconHome` is the same model over a grid of
    tiles that scrolls down.

    `names` (the palette) is injected so the card draw stays free of a
    console back-import."""

    arrangement = "grid"          # the layout's `home` value this class draws
    horizontal = True             # the axis the cells scroll on

    tab_focus = False             # the tab row holds the focus, not a card

    def __init__(self, items, layout=None, names=None):
        self.items = items
        self.tab = HOME_TABS[0][0]   # the home's active tab (ws.set_home_tab)
        self.tab_sel = {}            # tab -> the path its card was on when left
        # bumped whenever anything the bar's zone shows changes (BarLayer
        # folds it into its cache key); set before `sel`, whose setter reads it
        self.zone_gen = 0
        self.sel = 0
        # `_scroll` (raw px) is the state of record, clamped LAZILY against
        # the live layout (the `scroll` property), so a layout swap never
        # destroys a position; `_region` is the touch model, synced from it
        # whenever a drag is not active.
        self._scroll = 0
        self._region = _ui_draw.ScrollRegion(horizontal=self.horizontal)
        self._taps = _ui_draw.DragTap(self._region)
        self._NAMES = names
        self.theme = None             # chrome THEME tokens (look.set_theme pushes them)
        self.icon_spec = None         # optional () -> the live icon set: "+ New"'s plus
        self.cover_for = None         # optional (cart, div) -> the cart's decoded
                                      # cover (CoverCache.cover_for, SPEC.md 3.6)
        self.tinted_for = None        # optional (cart, colour, alpha) -> the
                                      # cover dimmed (CoverCache.tinted)
        self.dim = None               # (colour, alpha 0..255): the theme dims the
                                      # carousel's neighbours (`dim_neighbours`)
        self.wall_ink = None          # (white, black) over a wallpaper (_wall_ink)
        self.ground = None            # the home's ground's colour (_ground_of)
        self.favorite_for = None      # optional cart -> bool (ws.carts.is_favorite)
        self.own_for = None           # optional cart -> bool (ws.carts.is_own)
        self.plays_for = None         # optional cart -> its play record (ws.carts.plays)
        self.today_for = None         # optional () -> today as YYYYMMDD, 0 unknown
        self._card_clip = None        # the clip cards draw in while a draw runs
        self._c = None                # the cells laid out, and what for
        self._c_key = None
        if layout is None:
            # a bare Launcher(items) (unit construction); lazy, so there is
            # no launcher_layer<->console module-load cycle
            try:
                from console import Layout
            except ImportError:  # pragma: no cover - host fallback when not yet aliased
                from runtime.console import Layout
            layout = Layout()
        self.set_layout(layout)

    def set_layout(self, layout):
        """Adopt a new layout (a size or font-scale change). The scroll is
        not re-clamped here (it clamps lazily on read): the windowed tier
        applies every window's layout to both home models on each context
        switch, and an eager clamp against a small window would lose the
        position. The scroll ring keys on the geometry itself
        (`_statics_base`), so a paint recorded under another layout never
        matches."""
        self.layout = layout

    # -- geometry ------------------------------------------------------------

    def _cells(self):
        """The cells on the class in force (`_Cells`), laid out once per
        layout, theme depth and input."""
        lay = self.layout
        keys = _ui_draw.hint_kind() != "touch"     # touch alone shows no hint strip
        depth = _ui_draw.depth()
        k = self._c_key
        if k is not None and k[0] is lay and k[1] == keys and k[2] is depth:
            return self._c
        self._c = self._lay_cells(lay, keys, _lb.metric_class() != _lb.HANDHELD, depth)
        self._c_key = (lay, keys, depth)
        return self._c

    def _lay_cells(self, lay, keys, desk, depth):
        """The shelf: cards of the clean side a neighbour of the handheld's
        carousel takes (the desk's: the whole cover) in the rows the band
        holds under the bar -- a card's title may run under the hint strip,
        never a card -- and the least column gap in its range that leaves the
        last column on the glass cut by a third or more."""
        fs = lay.fs
        hints = _hint_strip(lay, keys, desk)
        band = (lay.cx, lay.cy, lay.cw, hints[1] - lay.cy)
        if desk:
            u = _lb.render_scale()
            margin, g0, g1, top, base, room = (v // u for v in _SHELF_DK)
            side = _clean_side(_COVER_SIDE // u)
        else:
            margin, g0, g1, top, base, room = (v * fs for v in _SHELF_HH)
            side = _clean_side(_HH_CARD * fs)
        while True:
            rows = max(1, (band[3] - top - side) // (side + room) + 1)
            if rows > 1 or side <= _COVER_SIDE // 8:
                break
            side = _clean_side(side - 1)
        avail = lay.cw - margin
        gap = g0
        for g in range(g0, g1 + 1):
            p = side + g
            last = avail - (avail - 1) // p * p      # the last column's part shown
            if last <= side * 2 // 3:
                gap = g
                break
        p = side + gap
        grow = _GROW[1] if desk else _GROW[0]
        return _Cells(side, depth[1], lay.cx + margin, lay.cy + top,
                      p, side + room, rows, (avail - 1) // p + 1, band, hints, None,
                      side + base - _ui_draw.font()[3] * fs, side,
                      grow, desk)

    def handheld(self):
        return not self._cells().desk

    def desk(self):
        """Whether the home lays out on the desk's metrics: its hint strip's
        and its tabs' sizes."""
        return self._cells().desk

    def foot(self):
        """The hint strip's rect last, as the carousel's `foot` answers it."""
        return (0, 0, 0, 0, 0, self._cells().hints)

    def hero_rect(self):
        return None

    def quiet(self):
        """The rectangles a per-glass wallpaper keeps its subject out of
        (docs/theming_2026-09.md §4.9): the cells' band and the hint strip."""
        c = self._cells()
        return (c.band, c.hints)

    def band_rect(self):
        """The rect the cells lie in: the drag partial's fill and damage,
        the card clip."""
        return self._cells().band

    def grow(self):
        """What focus grows a card by, on each side."""
        return self._cells().grow

    def cell_of(self, i):
        """(row, column) of cell `i`: a shelf's columns stack `rows` cards."""
        rows = self._cells().rows
        return i % rows, i // rows

    def index_of(self, row, col):
        return col * self._cells().rows + row

    def _extent(self):
        """The content's length along the scroll axis: the shelf's columns
        with a margin either side."""
        c = self._cells()
        n = len(self.items)
        if n <= 0:
            return 0
        cols = self.cell_of(n - 1)[1] + 1
        m = c.x0 - c.band[0]
        return 2 * m + (cols - 1) * c.px + c.side

    def _span(self, i):
        """Cell `i`'s (start, length) along the scroll axis in the content,
        its margin either side, which `_scroll_to_sel` keeps in view."""
        c = self._cells()
        return (self.cell_of(i)[1] * c.px, c.side + 2 * (c.x0 - c.band[0]))

    def _view(self):
        b = self._cells().band
        return b[2] if self.horizontal else b[3]

    # -- selection ----------------------------------------------------------

    @property
    def sel(self):
        return self._sel

    @sel.setter
    def sel(self, value):
        # bump zone_gen on every actual change, whatever the call site, so
        # BarLayer's cache key can trust it
        if value != getattr(self, "_sel", None):
            self.zone_gen += 1
        self._sel = value

    def set_items(self, items):
        """Replace the cart list (after a create/duplicate/delete) and re-clamp the
        selection + scroll so neither dangles past the new end."""
        self.items = items
        self.zone_gen += 1          # a rename can change the title under an unchanged sel
        self._region.invalidate()   # the pixels no longer match any recorded paint
        if self.sel >= len(items):
            self.sel = max(0, len(items) - 1)
        self._scroll_to_sel()

    def nav2d(self, dx, dy):
        """Cell navigation, clamped within the list (no wrap): on the shelf
        `dx` hops a column and `dy` steps within it; the scroll follows."""
        n = len(self.items)
        if not n:
            return
        row, col = self.cell_of(self.sel)
        if dx:
            col += dx
        elif dy:
            row = max(0, min(self._cells().rows - 1, row + dy))
        self.sel = max(0, min(n - 1, self.index_of(row, max(0, col))))
        self._scroll_to_sel()

    def selected(self):
        return self.items[self.sel] if self.items else None

    # -- the scroll -----------------------------------------------------------

    @property
    def scroll(self):
        """The pixel offset, clamped against the live layout."""
        return max(0, min(self._scroll, self.max_scroll()))

    @scroll.setter
    def scroll(self, value):
        self._scroll = max(0, min(int(value), self.max_scroll()))
        if self._region.animating:
            self._region.stop()     # a programmatic scroll cancels a fling

    @property
    def dragging(self):
        """True while a touch drag (past the slop) is scrolling the cells."""
        return self._taps.dragging

    @property
    def flinging(self):
        """True while a released fling is coasting the cells (#113)."""
        return self._region.animating

    @property
    def moving(self):
        return self._taps.dragging or self.flinging

    def anim_frame(self, dt):
        """Advance a live fling one frame (dt in seconds), before painting.
        True when the view moved."""
        if not self._region.animating:
            return False
        region = self._scroll_region()
        if region.tick(dt * 1000.0):
            self._scroll = region.offset   # direct: the setter would stop us
            return True
        return False

    def max_scroll(self):
        return max(0, self._extent() - self._view())

    def _scroll_to_sel(self):
        """Keep the selected cell, with its margin, inside the view."""
        if not self.items:
            return
        p, size = self._span(self.sel)
        s = self.scroll
        if p < s:
            s = p
        elif p + size > s + self._view():
            s = p + size - self._view()
        self.scroll = s

    def _scroll_region(self):
        """The ScrollRegion synced to the live geometry; `_scroll` stays the
        state of record, the region owning the offset only while a drag or
        a fling is active."""
        self._region.set(self._cells().band, self._extent())
        if not (self._region.drag_active or self._region.animating):
            self._region.offset = self.scroll
        return self._region

    def pointer_frame(self, px, py, click, down, dt_ms=None):
        """One pointer sample through ui.DragTap: the tapped cell on a clean
        release (press and release on it with no drag travel), else None, so
        a scroll gesture never launches a cart. `dt_ms` feeds the fling."""
        region = self._scroll_region()
        press = self._taps.frame(px, py, click, down,
                                 slop=4 * self.layout.fs + 2, dt_ms=dt_ms)
        if self._taps.dragging:
            self._scroll = region.offset       # the cells follow the finger
        if press is None:
            return None
        i = self.tile_at(press[0], press[1])
        if i is not None and i == self.tile_at(px, py):
            return i
        return None

    # -- cells ----------------------------------------------------------------

    def tile_rect(self, i):
        """Cell `i`'s card at the current scroll, or None when it and the
        focus ring's reach lie outside the band."""
        if not (0 <= i < len(self.items)):
            return None
        c = self._cells()
        row, col = self.cell_of(i)
        x, y = c.x0 + col * c.px, c.y0 + row * c.py
        if self.horizontal:
            x -= self.scroll
        else:
            y -= self.scroll
        reach = c.grow + _ui_draw.mark_reach("card")
        b = c.band
        if (x + c.side + reach <= b[0] or x - reach >= b[0] + b[2]
                or y + c.side + reach <= b[1] or y - reach >= b[1] + b[3]):
            return None
        return (x, y, c.side, c.side)

    def _visible(self):
        """(index, card rect) of every cell the band shows, whole or cut."""
        out = []
        for i in range(len(self.items)):
            r = self.tile_rect(i)
            if r is not None:
                out.append((i, r))
        return out

    def tile_at(self, px, py):
        """The cell whose card or title lies under (px, py) in the band."""
        c = self._cells()
        if not _in(px, py, c.band):
            return None
        for i, r in self._visible():
            if _in(px, py, (r[0], r[1], r[2], r[3] + c.py - c.side)):
                return i
        return None

    def favorite_rect(self, i):
        """No badge on a cell: F and the card's menu star it."""
        return None

    def action_rects(self):
        return None

    def statics_sel(self):
        return -1

    def paint_key(self):
        """What a recorded paint's band pixels depend on besides the offset:
        the selection, whose card wears the ring."""
        return self.sel

    def cover_specs(self):
        """Every (cart, div) cover the next full draw requests: each card's
        whole cover at its clean side's decode."""
        div = _cover_div_for(self._cells().side)
        return [(it, div) for it in self.items
                if it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES]

    # -- the draw -------------------------------------------------------------

    def draw(self, cv, sheet_for=None):
        """The band's cells over the backdrop the home drew (a full paint's),
        clipped to the band."""
        self._paint(cv, sheet_for)

    def draw_shift(self, cv, sheet_for, delta, damage, fill):
        """The moving frame: the band's pixels cannot shift, since the cards
        lie on the home's ground (a wallpaper must stay put while they move),
        so the band is repainted whole over the backdrop `fill` restores."""
        self.repaint_band(cv, sheet_for, fill)

    def repaint_band(self, cv, sheet_for, fill):
        fill(cv, self.band_rect())
        self._paint(cv, sheet_for)

    def _paint(self, cv, sheet_for):
        c = self._cells()
        clip = getattr(cv, "clip", None)
        if clip is not None:
            self._card_clip = c.band
            clip(*c.band)
        sel = self.sel
        for i, r in self._visible():
            if i != sel:
                self._draw_cell(cv, self.items[i], r, False, c)
        r = self.tile_rect(sel)
        if r is not None:
            self._draw_cell(cv, self.items[sel], r, True, c)
        self._fade(cv, c)
        if clip is not None:
            self._card_clip = None
            clip()

    def _fade(self, cv, c):
        """The handheld shelf's last rows dissolve into the hint strip, on
        the home's ground (not over a wallpaper, whose pixels it would hide)."""
        if c.desk or c.hints[3] <= 0 or self.wall_ink is not None:
            return
        h = _FADE * self.layout.fs
        b = c.band
        _ui_draw.fade(cv, (b[0], b[1] + b[3] - h, b[2], h), self._ground())

    def _ground(self):
        """The home's ground's colour (the home layer's `_ground_of`, set on
        every full paint), else the theme's desktop."""
        if self.ground is not None:
            return self.ground
        return (self.theme or {}).get("desktop", 0)

    def _focused(self, selected):
        """Whether the selected card wears the focus: the keys are on the
        cells, not the tabs or the bar."""
        return selected and not self.tab_focus

    def _draw_cell(self, cv, it, rect, selected, c):
        """One card: the picture over its square (grown by `grow` and ringed
        flat where it holds the focus), its 1 px edge, and its title under
        it in the `ui` face, cut to the card's width, bold on focus."""
        th = self.theme or {}
        focus = self._focused(selected)
        x, y, n, _n = rect
        g = c.grow if focus else 0
        pic = (x - g, y - g, n + 2 * g, n + 2 * g)
        r = c.r + g if c.r else 0
        self._draw_picture(cv, it, pic, True, None, r, _cover_div_for(n))
        if c.r:
            cv.rrect(pic[0], pic[1], pic[2], pic[3], r, None, ring=th.get("edge", 0))
        else:
            self._card_frame(cv, pic)
        if focus:
            _ui_draw.focus(cv, self.theme, pic, size="card", r=r, flat=True)
        self._draw_title(cv, it, x + n // 2, y + c.title_dy, c.title_w, focus)

    def _draw_title(self, cv, it, cx, y, w, bold):
        """A cell's title centred on `cx` from row `y` in the `ui` face, cut
        to `w` with the font's ellipsis: bold drawn twice a pixel apart (the
        look holds one weight of the face), on a wallpaper white over its
        black."""
        th = self.theme or {}
        name = "New" if it.get("type") in PSEUDO_TILE_TYPES else (it.get("title") or "")
        fs = self.layout.fs
        name = _ui_draw.print_ellipsis(name, w - (fs if bold else 0), fs)
        rect = (cx - w // 2, y, w, _ui_draw.font()[2] * fs)
        ink = th.get("ink", 7)
        wall = self.wall_ink
        if wall is not None:
            _ui_draw.label(cv, rect, name, wall[0], _ui_draw.ALIGN_CENTER, None,
                           (wall[1], 255, 1, 1))
        elif bold:
            _ui_draw.label(cv, rect, name, ink, _ui_draw.ALIGN_CENTER, None,
                           (ink, 255, 1, 0))
        else:
            _ui_draw.label(cv, rect, name, ink, _ui_draw.ALIGN_CENTER)

    # -- pictures -------------------------------------------------------------

    def _dims(self, centre):
        """(colour, alpha) a card's cover is dimmed toward, or None."""
        return None

    def _draw_title_tile(self, cv, it, rect, r):
        """A card for a cart with no cover: a generated one, its title's
        initial on the colour block that stays the cart's
        (`ui_draw.initial_cover`)."""
        _ui_draw.initial_cover(cv, rect, r, it.get("title") or "",
                               bool(_ui_draw.depth()[4]))

    def _draw_hh_cover(self, cv, it, rect, centre, r=None, div=_COVER_BASE):
        """A card's picture: the whole cover (decode `div`) scaled to the
        card (a carousel neighbour's dimmed once and kept where the theme
        dims them, step 15), else the title tile. `r`: its corners, where
        the caller has them."""
        if r is None:
            r = self._corner_r(rect[2])
        stepped = bool(_ui_draw.depth()[4])
        cover = None
        if self.cover_for is not None:
            dim = self._dims(centre)
            if dim is not None and self.tinted_for is not None:
                cover = self.tinted_for(it, dim[0], dim[1])
                if cover is not None:
                    cv.cover(cover.pix, cover.w, 0, 0, cover.w, cover.h, rect[0],
                             rect[1], rect[2], rect[3], r, stepped)
                    return
            cover = self.cover_for(it, div)
        if cover is not None:
            cv.cover(cover.pix, cover.w, 0, 0, cover.w, cover.h, rect[0], rect[1],
                     rect[2], rect[3], r, stepped)
        else:
            self._draw_title_tile(cv, it, rect, r)

    def _draw_picture(self, cv, it, rect, centre, sheet_for=None, r=None,
                      div=_COVER_BASE):
        """A card's picture over its whole rect: the "+ New" card's blank
        page (`draw_new_art`) in the corners a cover of its size takes, else
        its cover (`_draw_hh_cover`). `r`: its corners, where the caller has
        them."""
        if r is None:
            r = self._corner_r(rect[2])
        if it.get("type") in PSEUDO_TILE_TYPES:
            draw_new_art(cv, self.theme or {}, rect, r,
                         bool(_ui_draw.depth()[4]),
                         self.icon_spec() if self.icon_spec is not None else None,
                         self.layout.fs)
        else:
            self._draw_hh_cover(cv, it, rect, centre, r, div)

    def _corner_r(self, side):
        return self._cells().r

    def _favorite_badge_geom(self, rect):
        """The corner badge square for card rect `rect` -- one formula shared by
        the draw path and favorite_rect (hit-test), so they can never desync."""
        x, y, w, _h = rect
        fs = self.layout.fs
        sz = max(14 * fs, 16, 12 * _lb.unit()) | 1      # odd: the star centres on a pixel
        pad = max(2 * fs, _ui_draw.depth()[1] // 2 + 2)
        return (x + w - sz - pad, y + pad, sz, sz)

    def _card_frame(self, cv, rect):
        """A card's thin border where the theme's cards are square (`card_r`
        0)."""
        th = self.theme or {}
        fs = self.layout.fs
        x, y, w, h = rect
        for i in range(fs):
            cv.rectb(x - i, y - i, w + 2 * i, h + 2 * i, th.get("border", self._NAMES["black"]))

    # -- the selected card's words -------------------------------------------

    def played(self, it):
        """(times run, day of the last run or 0) of the card's play record
        (`CartManager.plays`), or None."""
        f = self.plays_for
        return None if f is None or it.get("type") in PSEUDO_TILE_TYPES else f(it)

    def today(self):
        """Today as YYYYMMDD, 0 where there is no wall clock."""
        f = self.today_for
        return 0 if f is None else f()

    def _meta(self, it, played=True):
        """The metadata of the selected card, its items: the cart's genre
        where its manifest names one (SPEC.md 3.7) and its kind where not,
        where it came from (built in, the kid's own, installed) and, where
        its play record has a day and `played`, when it was last played
        (the desk's details pane says that in its facts)."""
        if it.get("type") in PSEUDO_TILE_TYPES:
            return ("A new game",)
        kind = _cl.genre_word(it.get("genre")) or _KIND_NAME.get(it.get("type"), "App")
        cid = it.get("id") or ""
        if self.own_for is not None and self.own_for(it):
            src = "Yours"
        elif cid.startswith("moybyte."):
            src = "Built in"
        else:
            src = "Installed"
        rec = self.played(it) if played else None
        words = day_words(rec[1], self.today()) if rec is not None else None
        if words is None:
            return (kind, src)
        if words in ("Today", "Yesterday"):
            words = words.lower()
        return (kind, src, "Played " + words)


def _cover_div_for(side):
    """The cover decode a card `side` across draws: the whole cover where it
    takes 128 or more, else its half (a handheld shelf card's 1x, an icon's
    reduction)."""
    return _COVER_BASE if side > _COVER_SIDE // 2 else _COVER_HALF


# The home's tabs (docs/shell_layout_2026-10.md §12), in tab-row order, and
# the noun the bar's title counts each in.
HOME_TABS = (("games", "Games"), ("mine", "Mine"), ("apps", "Apps"),
             ("make", "Make"))
_TAB_NAME = dict(HOME_TABS)
_TAB_ORDER = {tid: i for i, (tid, _label) in enumerate(HOME_TABS)}
_TAB_ICON = {"games": "run", "mine": "moy", "apps": "gear", "make": "edit"}
_TAB_NOUN = {"games": ("GAME", "GAMES"), "mine": ("CART", "CARTS"),
             "apps": ("APP", "APPS"), "make": ("PROJECT", "PROJECTS")}
# The handheld hero's scale the home is laid out for: a board's verdict
# (FullscreenStackWM.HOME_SCALE) draws it at most this large.
HOME_SCALE_HH = 2.0
# The handheld home (docs/look_2026-10.md step 17): each card shows its
# whole cover at a clean scale (`_clean_side`), a neighbour at half the
# cover's side and the hero at twice that, a gap between cards, the corners
# of a neighbour 7/10 of the hero's.
_HH_CARD = _COVER_SIDE // 2
_HH_GAP = 14
# The least the handheld home leaves over and under the hero's caption
# (title over metadata), at the font scale, when a face needs their room.
_HH_SQUEEZE = (2, 4)
# The handheld caption's lines for a version 2 face, at the font scale (the
# title's at its own scale): the title's line and its baseline, the
# metadata's baseline under the title's, its line and its baseline. The
# size class's -- the targets' Night faces' -- so a mode whose faces set
# more leading draws its caption on the same baselines.
_HH_CAPTION = (24, 19, 17, 15, 12)


def _clean_side(limit):
    """The largest card side at most `limit` that shows a whole cover at a
    clean scale (owner call 2026-10-17): a whole multiple of the cover's
    side, else its half, quarter or eighth -- never a ratio like 1.5x."""
    if limit >= _COVER_SIDE:
        return limit // _COVER_SIDE * _COVER_SIDE
    side = _COVER_SIDE // 2
    while side > _COVER_SIDE // 8 and side > limit:
        side //= 2
    return side


_KIND_NAME = {"game": "Game", "app": "App", "tool": "Tool", "wallpaper": "Wallpaper"}
# The home's hint strip (steps 17 and 18): each verb's key and words. The
# keys stay off the T-Deck keyboard's buttons (moy_kbd.c: w a s d, l, k), so
# Duplicate is Y on a keyboard, and a gamepad's Y names nothing on the home: its X
# opens the card's verbs ("options"), the star among them.
_VERB_HINT = {"play": ("confirm", "Play"), "open": ("confirm", "Open"),
              "change": ("C", "Change"), "search": ("search", "Search"),
              "new": ("N", "New"), "copy": ("Y", "Duplicate"),
              "delete": ("X", "Delete"),
              "favourite": ("F", "Add favourite"), "options": ("X", "Options")}
_UNFAVOURITE = "Remove favourite"   # the star's words on a card that wears it
_VERB_KEY = {"c": "change", "/": "search", "n": "new", "y": "copy", "x": "delete"}
_FAVOURITE_KEY = "f"     # the selected card's star, on every tab that draws one
# The inputs (ui_draw.INPUTS) that type those letters: a gamepad has no C.
_LETTER_INPUTS = ("keys", "tdeck")
# A press held this long on a card without travel opens the card's verbs (a
# phone home screen's long press), in frame milliseconds.
LONG_PRESS_MS = 500
_TAB_KEYS = ("tabs", "Tabs")
_HINT_LEAD = 5       # the handheld's margin ui.hints leads with, at the font
                     # scale: the top bar's (MOY_UI_HINT_LEAD)
_HINT_LEAD_DESK = 8  # the desk's
BACK_TIP = "Desk"    # the tooltip on the bar's chevron back to the desk
SHELF_GAP = 42       # the details block's foot to the shelf's caption, in
                     # glass pixels: the full PROJECTS window's, at every size
DETAILS_MIN = 280    # the narrowest details pane beside the hero, in glass
                     # pixels at the target's 1x: a primary button and one more
# The most buttons a desk card shows, per kind of tab: a played tab's
# (Games, Mine), Make's on a real card. The details pane keeps room for as
# many rows as the widest of them wraps to, so the shelf stays put as the
# selection moves.
_DESK_VERB_SETS = (("play", "change", "copy"), ("change", "copy", "delete"))
DESC_LINES = 3       # the desk home's description: at most this many lines,
DESC_EMS = 48        # ... as wide as this many n's: about 60 characters of
                     # running text, the width of the buttons' row
_MENU_KEYS = ("back", "Close")   # the strip under the system menu
# The strip while the keys are on the tab row: left and right move the focus
# along it, confirm opens the tab it is on, back comes off the row.
_TAB_ROW_KEYS = ("change", "Move", "confirm", "Open", "back", "Back")
_CONFIRM_KEYS = ("confirm", "Choose", "back", "Cancel")   # under the delete confirm


def modal_keys(ws):
    """The keys a home's hint strip names while a system modal holds them:
    the delete confirm's dialog's, the system menu's card's, the bar's while
    the keys are on it, else None (the home's own verbs)."""
    if ws.confirm.open:
        return _CONFIRM_KEYS
    if ws.menu_ui.looked():
        return _MENU_KEYS
    if _ui_draw.on_bar():
        return _ui_draw.BAR_KEYS
    return None
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
           "Oct", "Nov", "Dec")


def _days(ymd):
    """Days since 1970-01-01 of a YYYYMMDD date (the civil-from-days
    inverse)."""
    y, m, d = ymd // 10000, ymd // 100 % 100, ymd % 100
    if m <= 2:
        y -= 1
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    return era * 146097 + yoe * 365 + yoe // 4 - yoe // 100 + doy - 719468


def day_words(day, today):
    """A YYYYMMDD day as the home says it, beside `today` (0: unknown):
    Today, Yesterday, N days ago within a week, else the date, its year
    where it is not this one. None for no day."""
    if not day:
        return None
    if today:
        n = _days(today) - _days(day)
        if n == 0:
            return "Today"
        if n == 1:
            return "Yesterday"
        if 1 < n < 7:
            return "%d days ago" % n
    y, m = day // 10000, day // 100 % 100
    out = "%d %s" % (day % 100, _MONTHS[(m - 1) % 12])
    return out if today and today // 10000 == y else "%s %d" % (out, y)


def times_words(n):
    """A play count as the home says it."""
    return "Not yet" if not n else ("Once" if n == 1 else "%d times" % n)


def text_scale(px, fs, role="ui"):
    """The whole scale `role`'s face draws a line about `px` glass pixels
    tall in, the font scale `fs` at the least."""
    lh = _ui_draw.font(role)[2]
    return max(fs, (px + lh // 2) // max(1, lh))


def desk_button_layout(verbs, dw, fs, u):
    """[(verb, x, row, w, kind, dy)] of the desk details pane's buttons in a
    pane `dw` wide: the first a primary where it plays, opens, makes or
    changes, DEL a danger set apart by a group's gap (three buttons' gaps,
    the button-row form of a menu's rule), the rest plain, left to right, a
    button that does not fit starting the next row (never dropped), `x` from
    the pane's left edge. Where they wrap, every button takes the widest
    one's width, and a danger that starts a row stands a group's gap under
    the row above (two rows' gaps): `dy`, the pixels its row and the rows
    after it stand lower than the row pitch puts them (DESK_ROW_GAP)."""
    kinds, widths = [], []
    for k, v in enumerate(verbs):
        label = _VERB_HINT[v][1]
        if k == 0 and v in ("play", "open", "new", "change"):
            kind = "primary"
        elif v == "delete":
            kind = "danger"
        else:
            kind = "normal"
        kinds.append(kind)
        widths.append(min(dw, max((148 if kind == "primary" else 112) // u,
                                  _ui_draw.print_width(label, fs) + 56 // u + 16 * fs)))
    out = _desk_button_flow(verbs, kinds, widths, dw, u)
    if out and out[-1][2]:
        out = _desk_button_flow(verbs, kinds, [max(widths)] * len(widths), dw, u)
    return out


DESK_ROW_GAP = 8     # the desk pane's rows of buttons stand this far apart


def _desk_button_flow(verbs, kinds, widths, dw, u):
    out = []
    x, row, dy = 0, 0, 0
    gap = 14 // u
    apart = 3 * 14 // u
    for v, kind, w in zip(verbs, kinds, widths):
        lead = (apart if kind == "danger" else gap) if x else 0
        if x and x + lead + w > dw:
            row += 1
            x, lead = 0, 0
            if kind == "danger":
                dy += DESK_ROW_GAP // u
        out.append((v, x + lead, row, w, kind, dy))
        x += lead + w
    return out


def desk_button_span(dw, fs, u, bh):
    """The pixels the desk pane keeps for its buttons under their first
    row: as many as the widest card's (`_DESK_VERB_SETS`) rows take in a
    pane `dw` wide, `bh` a button's height."""
    return max(b[2] * (bh + DESK_ROW_GAP // u) + b[5]
               for vs in _DESK_VERB_SETS
               for b in desk_button_layout(vs, dw, fs, u)[-1:])


def tab_count_title(tab, n):
    """The bar's title on the home: the active tab's count."""
    one, many = _TAB_NOUN.get(tab, ("CART", "CARTS"))
    return "%d %s" % (n, one if n == 1 else many)


class Carousel(Launcher):
    """The home's carousel (docs/shell_layout_2026-10.md §12, the 2026-10
    look's steps 17 and 18), on the class in force. On the handheld one row
    of square cover cards slides sideways, the card at the centre larger: a
    card's size is a function of its distance from the centre and of
    nothing else -- 1.0 at one pitch or more, rising to the centre scale at
    distance 0 -- so a settled row is a still row. The row rests with a card
    at the centre: a drag, a fling or a key moves the offset, and once the
    motion is spent a GLIDE carries the nearest card the rest of the way
    (`flinging` covers both, so every moving frame takes the drag partial).
    `sel` changes only on settle, and so does the bar's zone (`zone_gen`).
    On the desk the selected card is the hero beside its details, over a
    shelf of one-size cards: a key or a tap selects at once and the shelf
    glides to show it two cards in, a drag scrolls the shelf alone, and the
    hero follows the selection (`statics_sel`).

    The cards lie on the home's wallpaper, so a moving frame cannot shift the
    band's pixels (the wallpaper would slide with them): it restores the
    wallpaper around the cards from the band's capture
    (`LauncherHomeLayer._backdrop_fill`) and paints the cards over it."""

    arrangement = "carousel"

    def __init__(self, items, layout=None, names=None):
        self.motion = "full"
        self.scale = HOME_SCALE_HH
        self._was_drag = False
        self._g_lay = None            # the layout _g was computed for
        self._g_reach = 0
        self._g_keys = True
        self._g = None
        self.layout = None
        self.own_for = None           # optional cart -> bool (ws.carts.is_own)
        self.plays_for = None         # optional cart -> its play record (ws.carts.plays)
        self.today_for = None         # optional () -> today as YYYYMMDD, 0 unknown
        self._cap = None              # the caption laid out: (card, layout, parts)
        self.wall_ink = None          # (white, black) over a wallpaper (_wall_ink)
        self._quiet_for = None        # the geometry `quiet` was computed for
        self._quiet_q = ()
        self._vis_g = None            # the geometry and key `_vis` was laid out for
        self._vis_key = None
        self._vis = None
        Launcher.__init__(self, items, layout, names)

    # -- geometry ------------------------------------------------------------

    def _geom(self):
        """(tabs, band, card, pitch, ring, gap, plate_h, centre_y, foot,
        desk): the handheld's (`_geom_hh`) or the desk's (`_geom_desk`) on
        the class in force. A card is square (a cover is, SPEC.md 3.6) and
        shows its whole cover at a clean scale (`_clean_side`)."""
        lay = self.layout
        reach = _ui_draw.mark_reach("card")    # the centre card's focus mark
        keys = _ui_draw.hint_kind() != "touch"    # touch alone shows no hint strip
        if self._g_lay is lay and self._g_reach == reach and self._g_keys == keys:
            return self._g
        self._g = (self._geom_hh(lay, reach, keys) if _lb.metric_class() == _lb.HANDHELD
                   else self._geom_desk(lay, reach, keys))
        self._g_lay = lay
        self._g_reach = reach
        self._g_keys = keys
        return self._g

    def _geom_hh(self, lay, reach, keys=True):
        """The handheld home's geometry (docs/look_2026-10.md step 17), the
        same tuple with its foot: no tab row (the tabs are the bar's), the
        hero twice a neighbour's side, its title and its metadata under it
        and the hint strip along the content's foot (none with no `keys`: touch
        shows no keycaps, and the cards take its height). A neighbour is half the
        cover's side at the font scale where that fits, and the next clean
        side down (`_clean_side`) where it does not."""
        fs = lay.fs
        cx, cy, cw = lay.cx, lay.cy, lay.cw
        hints = _hint_strip(lay, keys, False)
        tf = _ui_draw.font("hero")
        tsc = max(fs, (16 * fs) // max(1, tf[2]))
        v2 = _ui_draw.font_v2(tf[0])
        # A version 2 face's line holds its leading: the caption lays out on
        # the size class's lines (`_HH_CAPTION`), the title 6 under the hero
        # and 10 over the hints (the targets'), and each line draws on its
        # baseline there, whatever leading the mode's faces set; a pixel
        # face's line is its ink, so 12, 3 and 4.
        if v2:
            tl, tb, mb, ml, ma = _HH_CAPTION
            title_h, meta_h = tl * tsc, ml * fs
            between = tb * tsc + (mb - ma) * fs - title_h
            above, below = 6 * fs, 10 * fs
        else:
            title_h, meta_h = tf[2] * tsc, lay.line_h
            above, between, below = 12 * fs, 3 * fs, 4 * fs
        ring = max(2 * fs + 2, reach)
        space = hints[1] - cy
        # The gaps give before the card does: a face taller than the targets'
        # (Day's against Night's) takes its pixels from over and under the
        # caption, down to `_HH_SQUEEZE`, so every mode of a theme keeps one
        # hero side; only what the gaps cannot hold steps the card down.
        text = title_h + between + meta_h
        lo = (_HH_SQUEEZE[0] * fs, _HH_SQUEEZE[1] * fs)
        card = _clean_side(_HH_CARD * fs)
        while card > _COVER_SIDE // 8 and ring + 2 * card + text + lo[0] + lo[1] > space:
            card = _clean_side(card - 1)
        short = ring + 2 * card + text + above + below - space
        if short > 0:
            give = min(short, above + below - lo[0] - lo[1])
            cut = give * (above - lo[0]) // max(1, above + below - lo[0] - lo[1])
            above, below = above - cut, below - (give - cut)
        foot = above + text + below
        gap = _HH_GAP * fs
        top = cy + max(ring, (space - 2 * card - foot) // 2)
        title_y = top + 2 * card + above
        meta_y = title_y + title_h + between
        if v2:
            # each face on its line's baseline
            meta_y += (ma - _ui_draw.font()[3]) * fs
            title_y += (tb - tf[3]) * tsc
        band = (cx, cy, cw, hints[1] - cy)
        return (None, band, card, card + gap, ring, gap, 0, top + card,
                (title_y, title_h, tsc, meta_y, meta_h, hints), None)

    def _geom_desk(self, lay, reach, keys=True):
        """The desk home's geometry (`_geom_desk_at`) with room in its pane
        for as many rows of buttons as its width wraps them to
        (`desk_button_span`): a pane narrowed by a smaller hero is laid out
        again, the rows it then needs kept."""
        span = 0
        for _ in range(3):
            g = self._geom_desk_at(lay, reach, keys, span)
            dk = g[9]
            got = desk_button_span(dk[2], lay.fs, dk[5][6], dk[5][4])
            if got <= span:
                break
            span = got
        return g

    def _geom_desk_at(self, lay, reach, keys, brows):
        """The desk home's geometry (docs/look_2026-10.md step 18), the same
        tuple with its foot (the hint strip) and the desk's own: the hero at
        the top left, the details pane beside it, the shelf under them
        bleeding off the right edge, its caption over it. The desk lays out
        in glass pixels (the target's 1x), its type at whole scales of the
        face no smaller than the font scale. The hero is the largest whole
        multiple of the cover's side a quarter of the width holds with the
        shelf under it, else half the cover's side, and a shelf card is half
        the hero, so every card shows its whole cover at a clean scale; the pane
        is as tall as the hero, or as its type needs where that is more.
        Where the pane would be narrower than `DETAILS_MIN` (a narrow
        window, the desk's PROJECTS) the hero stands over it instead, the
        largest clean side the height holds with the pane and the shelf,
        and the pane takes the width -- where that hero is no smaller than
        the one beside it; else the pane stays beside, its buttons as many
        as it holds. The margins narrow with the width, to a twentieth of
        it. Where the height cannot hold the pane over a half-cover shelf,
        the pane drops its facts, then its metadata (the desk's `rows`), and
        the hero halves down to an eighth of the cover. The shelf's caption
        stands `SHELF_GAP` under the details and what height is left goes
        under the shelf (design review R5-08), but under touch (no hint
        strip, no `keys`) the shelf keeps at most the side margin under it,
        the rest over its caption (R3-07). The
        tuple's band is the shelf's: its caption, its cards, their lift
        and their shadows; the desk's own ends with the pane's top and the
        pixels its rows of buttons take under their first, `brows`
        (`desk_button_span`)."""
        fs = lay.fs
        cx, cy, cw = lay.cx, lay.cy, lay.cw
        u = _lb.render_scale()
        hints = _hint_strip(lay, keys, True)
        pad = min(48 // u, max(12 // u, cw // 20))
        top, gap, apart = 24 // u, 20 // u, 32 // u
        ring = max(2 * fs + 2, reach)
        lh, tl = _ui_draw.font()[2], _ui_draw.font("title")[2]
        ts, ms = text_scale(40 // u, fs, "title"), text_scale(14 // u, fs)
        ks, vs = text_scale(13 // u, fs), text_scale(15 // u, fs)
        bh = max(42 // u, 20 * fs)
        need = tl * ts + lh * (ms + ks + vs) + bh + 60 // u + brows
        cap_h = lh * ms + 8 // u
        room = hints[1] - cy - top - cap_h - 72 // u
        # A short window gives up, in turn, most of the slack round the
        # shelf's caption, the facts' row and the metadata's line.
        rows = [True, True]                  # (facts, metadata) shown
        least = _COVER_SIDE // 2
        if max(least, need) + least // 2 > room:
            room += 48 // u
        for k, h in ((0, lh * (ks + vs) + 22 // u), (1, lh * ms + 14 // u)):
            if max(least, need) + least // 2 > room:
                rows[k] = False
                need -= h
        hero = cw // 4 // _COVER_SIDE * _COVER_SIDE
        while hero > _COVER_SIDE and max(hero, need) + hero // 2 > room:
            hero -= _COVER_SIDE
        if hero < _COVER_SIDE or max(hero, need) + hero // 2 > room:
            hero = least
        while hero > _COVER_SIDE // 8 and max(hero, need) + hero // 2 > room:
            hero //= 2
        hx, hy = cx + pad, cy + top
        dx, dy, pane = hx + hero + apart, hy, max(hero, need)
        block = pane
        if cx + cw - pad - dx < DETAILS_MIN // u:
            # too narrow for the pane beside the hero: the hero over the
            # pane where the height holds both at a hero no smaller
            up = _COVER_SIDE
            while up > hero and up + apart + need + up // 2 > room:
                up //= 2
            if up >= hero and up + apart + need + up // 2 <= room:
                hero = up
                dx, dy, pane = hx, hy + hero + apart, need
                block = hero + apart + need
        shelf = hero // 2
        spare = max(0, hints[1] - cy - top - block - cap_h - shelf)
        d = _ui_draw.depth()
        blur = d[8] if d[5] else 0
        down = max(ring, blur + d[10])
        # the caption SHELF_GAP under the details, the spare height under the
        # shelf; a height short of that splits what it has, keeping the
        # shelf's lift and shadow off the strip
        cap_y = hy + block + min(SHELF_GAP // u,
                                 max(spare // 2, spare - max(down, 20 // u)))
        if not keys:
            # no strip under touch: the shelf stands at most the side margin
            # off the glass's foot, and the strip's room goes over its caption
            cap_y = max(cap_y, hints[1] - pad - shelf - cap_h)
        shelf_y = cap_y + cap_h
        band = (cx, cap_y, cw, max(0, min(hints[1], shelf_y + shelf + down) - cap_y))
        desk = ((hx, hy, hero, hero), dx, max(0, cx + cw - pad - dx), shelf_y, pad,
                (ts, ms, ks, vs, bh, pane, u), dy, tuple(rows), brows)
        return (None, band, shelf, shelf + gap, ring, gap, 0, shelf_y + shelf // 2,
                (0, 0, 0, 0, 0, hints), desk)

    def handheld(self):
        """Whether the home is the handheld's (step 17): its tabs in the bar,
        its foot under the band."""
        g = self._geom()
        return g[8] is not None and g[9] is None

    def desk(self):
        """Whether the home is the desk's console home (step 18): the hero,
        its details and the shelf, its tabs in the bar and its foot the
        hint strip."""
        return self._geom()[9] is not None

    def foot(self):
        """(title y, title h, title scale, meta y, meta h, the hint strip's
        rect) on the handheld, the hint strip's rect last on the desk's
        console home, else None."""
        return self._geom()[8]

    def hero_rect(self):
        """The desk home's hero, or None."""
        dk = self._geom()[9]
        return None if dk is None else dk[0]

    def quiet(self):
        """The home's quiet rectangles (docs/theming_2026-09.md §4.9), which
        a per-glass wallpaper keeps its subject out of: on the handheld the
        title and the metadata, a column as wide as the hero and half a
        neighbour each side, and the hint strip; on the desk the hero and
        its details, the shelf's band and the hint strip. Computed once per
        geometry."""
        g = self._geom()
        if self._quiet_for is g:
            return self._quiet_q
        foot, dk = g[8], g[9]
        if foot is None:
            q = ()
        elif dk is None:
            title_y, _th, _ts, meta_y, meta_h, hints = foot
            lay = self.layout
            tw = min(lay.cw, 3 * g[2])
            q = ((lay.cx + (lay.cw - tw) // 2, title_y, tw,
                  meta_y + meta_h - title_y), hints)
        else:
            (hx, hy, hero, _h), dx, dw, dy = dk[0], dk[1], dk[2], dk[6]
            q = ((hx, hy, max(dx + dw, hx + hero) - hx,
                  max(hy + hero, dy + dk[5][5]) - hy), g[1], foot[5])
        self._quiet_for = g
        self._quiet_q = q
        return q
    def band_rect(self):
        """The carousel band: every card at any scale, the centre's ring and
        its title plate. The rect the drag partial fills and hands as damage."""
        return self._geom()[1]

    def card_side(self):
        return self._geom()[2]

    def pitch(self):
        return self._geom()[3]

    # -- the offset, the motion, the selection ---------------------------------

    @property
    def sel(self):
        return self._sel

    @sel.setter
    def sel(self, value):
        if value != getattr(self, "_sel", None):
            self.zone_gen += 1
        self._sel = value
        # A selection made from outside (a tab switch, a resume, a remote
        # run) brings its card to the centre; the settle's own assignment
        # lands where the row already rests.
        if self.layout is not None and not self.moving:
            self._scroll = self._rest_of(value)

    def _rest_of(self, i):
        """The offset the row rests at with card `i` selected: at the centre,
        or on the desk's shelf with two cards before it where the shelf has
        them."""
        if self.desk():
            return max(0, min((i - 2) * self.pitch(), self.max_scroll()))
        return max(0, i) * self.pitch()

    @property
    def flinging(self):
        """True while the row coasts on its own: a released fling, or the
        glide that carries the nearest card to the centre (a key's motion
        too) -- the frames that take the drag partial."""
        return self._region.animating

    @property
    def moving(self):
        return self._taps.dragging or self.flinging

    def max_scroll(self):
        g = self._geom()
        if g[9] is not None:
            # the shelf's last card ends its margin short of the band's edge
            n = len(self.items)
            return max(0, (n - 1) * g[3] + g[2] + 2 * g[9][4] - g[1][2]) if n else 0
        return max(0, (len(self.items) - 1) * self.pitch())

    def nearest(self):
        """The card nearest the centre at the current offset; on the desk's
        shelf, the selected one."""
        n = len(self.items)
        if not n:
            return 0
        if self.desk():
            return max(0, min(n - 1, self._sel))
        p = self.pitch()
        return max(0, min(n - 1, (self.scroll + p // 2) // p))

    def set_items(self, items):
        self._region.stop()
        Launcher.set_items(self, items)

    def _scroll_to_sel(self):
        self._region.stop()
        self._scroll = max(0, min(self._rest_of(self.sel), self.max_scroll()))

    def _scroll_region(self):
        band = self.band_rect()
        self._region.set(band, self.max_scroll() + band[2])
        if not (self._region.drag_active or self._region.animating):
            self._region.offset = self.scroll
        return self._region

    def glide_to(self, i):
        """Carry card `i` to the centre, frame by frame (`anim_frame`): on
        the handheld the row slides there, on the desk the shelf glides --
        the scroll model's two eases (`ScrollRegion.glide_to`)."""
        n = len(self.items)
        if not n:
            return
        self._region.stop()
        region = self._scroll_region()
        i = max(0, min(n - 1, i))
        if self.desk():
            # the desk selects at once and the shelf glides to show it
            if i != self._sel:
                self.zone_gen += 1
                self._sel = i
            region.glide_to(self._rest_of(i))
            return
        region.glide_to(i * self.pitch(), slide=self.handheld())

    def nav2d(self, dx, dy):
        """Left/right move the row by a card with the glide; a key held is one
        motion, each repeat pushing the target a card further."""
        if not self.items or not dx:
            return
        if self.desk():
            self.glide_to(self._sel + dx)
            return
        p = self.pitch()
        base = self._region.target
        if base is None:
            base = self.nearest() * p
        self.glide_to(base // p + dx)

    def _snap(self):
        if not self.desk():                  # the desk's shelf rests anywhere
            self.glide_to(self.nearest())

    def anim_frame(self, dt):
        """Advance the fling or the glide one frame (dt in SECONDS), and
        settle the selection once the row is still: a fling that comes to
        rest glides the nearest card the rest of the way. True when the view
        moved."""
        moved = False
        region = self._region
        if region.animating:
            fling = region.flinging
            was = self._scroll
            self._scroll_region()
            if region.tick(dt * 1000.0):
                self._scroll = region.offset   # direct: the setter would stop it
                moved = fling or self._scroll != was
            if fling and not region.animating:
                self._snap()
        if not self.moving and not self.desk():
            c = self.nearest()
            if c != self._sel:
                self.sel = c
        return moved

    def pointer_frame(self, px, py, click, down, dt_ms=None):
        i = Launcher.pointer_frame(self, px, py, click, down, dt_ms)
        drag = self._taps.dragging
        if self._was_drag and not drag and not self._region.animating:
            self._snap()
        self._was_drag = drag
        return i

    def scroll_cols(self, d):
        self.nav2d(d, 0)

    # -- cards -----------------------------------------------------------------

    def _side(self, d, moving, g, big):
        """A card's side at distance `d` px from the centre, under geometry
        `g` with the hero's side `big` (`_big`)."""
        card, pitch = g[2], g[3]
        if (d >= pitch or self.motion == "one"
                or (moving and self.motion == "rest")):
            return card
        return card + (big - card) * (pitch - d) // pitch

    def _big(self):
        """The handheld hero's side at rest: the board's scale, at most 2x."""
        g = self._geom()
        return int(g[2] * min(self.scale, HOME_SCALE_HH) + 0.5)

    def _rect_at(self, i, moving):
        return self._rect_of(i, moving, self._geom(), self.scroll, self._big())

    def _rect_of(self, i, moving, g, s, big):
        """Card `i`'s rect at offset `s` under geometry `g` (`_side`'s)."""
        bx, _by, bw, _bh = g[1]
        if g[9] is not None:
            return (bx + g[9][4] + i * g[3] - s, g[9][3], g[2], g[2])
        mid = bx + bw // 2
        d = i * g[3] - s
        t = self._side(abs(d), moving, g, big)
        if g[8] is not None:
            # The handheld's cards keep their gap beside the hero: each is
            # pushed out by half what the centre card grew, in proportion
            # to its distance up to one pitch.
            grow = self._side(0, moving, g, big) - g[2]
            ad = min(abs(d), g[3])
            push = grow * ad // (2 * g[3])
            d += push if d > 0 else -push
        xc = mid + d
        return (xc - t // 2, g[7] - t // 2, t, t)

    def _visible(self):
        """(index, rect) for every card in the band, smallest first -- the
        order they paint in, so the centre card lands on top. Kept for the
        offset, motion and selection it was laid out at, so a frame's
        restore and its paint lay the row out once; callers read it and
        never change it."""
        n = len(self.items)
        if not n:
            return []
        g = self._geom()
        s = self.scroll
        moving = self.moving
        c = self.nearest()
        key = (s, moving, c, n, self.motion, self.scale)
        if self._vis_g is g and self._vis_key == key:
            return self._vis
        bx, _by, bw, _bh = g[1]
        pitch = g[3]
        if g[9] is not None:
            i0 = max(0, s // pitch - 1)
            i1 = min(n - 1, (s + bw) // pitch + 1)
        else:
            reach = bw // 2 + 2 * g[2]
            i0 = max(0, (s - reach) // pitch)
            i1 = min(n - 1, (s + reach) // pitch + 1)
        out = []
        big = self._big()
        for i in range(i0, i1 + 1):
            r = self._rect_of(i, moving, g, s, big)
            if r[0] + r[2] > bx and r[0] < bx + bw:
                out.append((i, r))
        out.sort(key=lambda e: (e[1][2], e[0] == c))
        self._vis_g, self._vis_key, self._vis = g, key, out
        return out

    def tile_rect(self, i):
        if not (0 <= i < len(self.items)):
            return None
        r = self._rect_at(i, self.moving)
        bx, _by, bw, _bh = self.band_rect()
        if r[0] + r[2] <= bx or r[0] >= bx + bw:
            return None
        return r

    def tile_at(self, px, py):
        bx, by, bw, bh = self.band_rect()
        if not (bx <= px < bx + bw):
            return None
        vis = self._visible()
        for k in range(len(vis) - 1, -1, -1):
            if _in(px, py, vis[k][1]):
                return vis[k][0]
        return None

    def plate_rect(self):
        """The handheld's title and metadata lines across the band; on the
        desk, the details pane beside the hero."""
        g = self._geom()
        bx, by, bw, bh = g[1]
        if g[9] is not None:
            return (g[9][1], g[9][6], g[9][2], g[9][5][5])
        f = g[8]
        return (bx, f[0], bw, f[3] + f[4] - f[0])

    def action_rects(self):
        return None

    def statics_sel(self):
        return self._sel if self.desk() else -1

    def paint_key(self):
        """No selection in the key: the band never shifts (`draw_shift`
        repaints it whole), and `sel` changes only on settle."""
        return 0

    def favorite_rect(self, i):
        """The centre card's favorite-star badge, at rest."""
        if (self.favorite_for is None or not self.items or i is None
                or self.moving or i != self.nearest()):
            return None
        if not (0 <= i < len(self.items)):
            return None
        it = self.items[i]
        if it.get("type") in PSEUDO_TILE_TYPES or not it.get("path"):
            return None
        hero = self.hero_rect()
        if hero is not None and hero[2] * _lb.render_scale() < _COVER_SIDE:
            return None                  # a small hero wears no badge (F, the menu)
        r = hero or self.tile_rect(i)
        return None if r is None else self._favorite_badge_geom(r)

    def cover_specs(self):
        out = []
        for it in self.items:
            if it.get("type") in PSEUDO_TILE_TYPES or not it.get("path"):
                continue
            out.append((it, _COVER_BASE))
        return out

    def draw(self, cv, sheet_for=None):
        if self.desk():
            self._paint_details(cv)
        self._paint(cv, sheet_for, self.band_rect())

    def draw_shift(self, cv, sheet_for, delta, damage, fill):
        """The moving frame. The band's pixels cannot shift: the cards lie on
        the wallpaper, which must stay put while they move. So the band is
        repainted whole -- `fill` restores the wallpaper around the cards,
        then the cards -- whatever the ring offers to shift."""
        self.repaint_band(cv, sheet_for, fill)

    def repaint_band(self, cv, sheet_for, fill, marks=False):
        band = self.band_rect()
        self._fill_around_cards(cv, fill, band, marks)
        self._paint(cv, sheet_for, band)

    def _fill_around_cards(self, cv, fill, r, marks=False):
        """Restore rect `r`'s backdrop except where a card lies: every card
        paints its whole square opaque, so the backdrop is owed only around
        them -- on a big panel most of the band's pixels are the cards'.
        The focus mark's rounded ring blends over the corner rows of the card
        it lies on, past the card's own corner: the selected card's rows
        are restored that far, and with `marks` (the buffer may hold the
        mark on another card) every card's."""
        x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
        x = x0
        reach = _ui_draw.mark_reach("card") + 1
        on = -1 if marks else self.nearest()
        for i, c in sorted(self._visible(), key=lambda e: e[1][0]):
            cx0 = max(c[0], x)
            cx1 = min(c[0] + c[2], x1)
            if cx1 <= cx0:
                continue
            if cx0 > x:
                fill(cv, (x, y0, cx0 - x, y1 - y0))
            ct = max(c[1], y0)
            cb = min(c[1] + c[3], y1)
            k = self._corner_r(c[2])
            if k and (on < 0 or i == on):
                k += reach
            k = min(k, (cb - ct) // 2)
            if k > 0:
                # rounded cards leave their corners' pixels to the backdrop:
                # the rows the corners span are restored too
                ct += k
                cb -= k
            if ct > y0:
                fill(cv, (cx0, y0, cx1 - cx0, ct - y0))
            if cb < y1:
                fill(cv, (cx0, cb, cx1 - cx0, y1 - cb))
            x = cx1
        if x < x1:
            fill(cv, (x, y0, x1 - x, y1 - y0))

    def _corner_r(self, side):
        """The corner radius of a card `side` across under the theme's depth:
        on the handheld its `card_r` on the hero and 7/10 of it on a
        neighbour, on the desk `card_r` on the shelf and 7/5 of it on the
        hero."""
        r = _ui_draw.depth()[1]
        g = self._geom()
        if g[9] is not None:              # the desk's
            return r * 7 // 5 if side > g[2] else r
        return r if side > g[2] else r * 7 // 10

    def _paint(self, cv, sheet_for, r):
        """The band, clipped to rect `r`: the handheld's (`_paint_hh`), or the
        desk's shelf -- its caption, every card's shadow (the theme's depth,
        `ui.cast`; a moving card keeps its own), the cards, the selected one
        last and at rest under the theme's focus mark."""
        if self.handheld():
            return self._paint_hh(cv, sheet_for, r)
        g = self._geom()
        # the shelf starts at its margin: a card scrolled past it is gone
        left = g[1][0] + g[9][4] - g[5]
        if r[0] < left:
            r = (left, r[1], max(0, r[0] + r[2] - left), r[3])
        clip = getattr(cv, "clip", None)
        if clip is not None:
            self._card_clip = r
            clip(*r)
        self._draw_shelf_caption(cv)
        c = self.nearest()
        moving = self.moving
        # at rest a card the scroll left across the margin is not shown
        vis = [e for e in self._visible()
               if ((e[1][0] + e[1][2] > left) if moving else (e[1][0] >= left))]
        for _i, t in vis:
            _ui_draw.cast(cv, self.theme, t, _ui_draw.CAST_CARD, _ui_draw.UNDER_SMALL)
        for i, t in vis:
            self._draw_home_card(cv, self.items[i], t, i == c, sheet_for)
        if clip is not None:
            self._card_clip = None
            clip()

    def _draw_shelf_caption(self, cv):
        """The shelf's caption over its first card: the tab and its count,
        apart by a dot, in the dim ink on the home's ground."""
        th = self.theme or {}
        fs = self.layout.fs
        dk = self._geom()[9]
        sc = dk[5][1]
        lh = _ui_draw.font()[2] * sc
        x = self.band_rect()[0] + dk[4]
        y = dk[3] - 8 // dk[5][6] - lh
        name = _TAB_NAME.get(self.tab, "")
        count = str(len(self.items))
        sep = 6 * fs
        nw = _ui_draw.print_width(name, sc)
        dim = th.get("ink_dim", 6)
        cv.print_scaled(name, x, y, dim, sc)
        x += nw + sep
        cv.rect(x, _dot_top(y, sc, sc), sc, sc, dim)
        cv.print_scaled(count, x + sc + sep, y, dim, sc)

    def _paint_details(self, cv):
        """The desk home's hero (a full paint's): its shadow, the
        selected card's picture at the hero's size and its favourite star
        (on a hero of the cover's side or more in glass pixels).
        The details beside it are the home layer's (`_draw_details`)."""
        hero = self.hero_rect()
        if not self.items:
            return
        it = self.items[self.nearest()]
        _ui_draw.cast(cv, self.theme, hero, _ui_draw.CAST_CARD)
        self._draw_picture(cv, it, hero, True)
        if (self.favorite_for is not None and it.get("path")
                and hero[2] * _lb.render_scale() >= _COVER_SIDE
                and it.get("type") not in PSEUDO_TILE_TYPES):
            _draw_star_badge(cv, self._NAMES, self.theme or {},
                             self._favorite_badge_geom(hero),
                             bool(self.favorite_for(it)))
        if not self._corner_r(hero[2]):
            self._card_frame(cv, hero)

    def _paint_hh(self, cv, sheet_for, r):
        """The handheld home's band (docs/look_2026-10.md step 17): every
        card's shadow, the hero's (the card nearest the centre) at the
        theme's size and the others' smaller, in motion as at rest; the
        cards, smallest first, the hero last and at rest lifted by the
        theme's focus mark; then the title and the metadata of the hero."""
        clip = getattr(cv, "clip", None)
        if clip is not None:
            self._card_clip = r
            clip(*r)
        c = self.nearest()
        th = self.theme
        vis = self._visible()
        for i, t in vis:
            if i == c:
                _ui_draw.cast(cv, th, t, _ui_draw.CAST_CARD)
            else:
                _ui_draw.cast(cv, th, t, _ui_draw.CAST_CARD, _ui_draw.UNDER_SMALL)
        for i, t in vis:
            self._draw_home_card(cv, self.items[i], t, i == c, sheet_for)
        if self.items:
            self._draw_caption(cv, self.items[c])
        if clip is not None:
            self._card_clip = None
            clip()

    def _draw_caption(self, cv, it):
        """The title of the card nearest the centre in the `title` face,
        centred, and its metadata in the `ui` face under it, the items
        apart by a dot: on the home's ground in its ink under a theme's
        depth, on a plate in the bar's colours without one (a legacy
        ground is any colour)."""
        th = self.theme or {}
        lay = self.layout
        fs = lay.fs
        ty, tht, tsc, my, mh, _hints = self._geom()[8]
        bx, _by, bw, _bh = self.band_rect()
        sep = 12 * fs
        fid = _ui_draw.font("hero")[0]
        cap = self._cap
        rec = self.played(it)
        if (cap is None or cap[0] is not it or cap[1] is not self._g
                or cap[7] != rec):
            # laid out once a card: a moving frame draws it again as it is
            name = it.get("title") or ""
            if it.get("type") == NEW_TILE_TYPE:
                name = "New"
            was = _ui_draw.print_font(fid)
            name = _ui_draw.print_fit(name, bw - 16 * fs, tsc)
            tw = _ui_draw.print_width(name, tsc)
            if was is not None:
                _ui_draw.print_font(was)
            parts = self._meta(it)
            ws_ = [_ui_draw.print_width(t, fs) for t in parts]
            cap = self._cap = (it, self._g, name, tw, parts, ws_,
                               sum(ws_) + sep * (len(parts) - 1), rec)
        name, tw, parts, ws_, mw = cap[2], cap[3], cap[4], cap[5], cap[6]
        was = _ui_draw.print_font()
        ink, dim = th.get("ink", 7), th.get("ink_dim", 6)
        wall = self.wall_ink
        if wall is not None:
            ink = dim = wall[0]
        tx = bx + (bw - tw) // 2
        swap = _ui_draw.print_font(fid)
        if wall is not None:
            cv.print_scaled(name, tx + 1, ty + 1, wall[1], tsc)
        cv.print_scaled(name, tx, ty, ink, tsc)
        if swap is not None:
            _ui_draw.print_font(was)
        x0 = x = bx + (bw - mw) // 2
        d = 2 * fs if _ui_draw.font_v2(_ui_draw.font()[0]) else fs
        dy = _dot_top(my, fs, d)
        for pas in ((1, wall[1]), (0, dim)) if wall is not None else ((0, dim),):
            o, c = pas
            x = x0
            for k, t in enumerate(parts):
                if k:
                    cv.rect(x - (sep + d) // 2 + o, dy + o, d, d, c)
                cv.print(t, x + o, my + o, c, 1)
                x += ws_[k] + sep

    def _dims(self, centre):
        """A handheld neighbour's dimming (step 15), off the centre."""
        if centre or self.dim is None or not self.handheld():
            return None
        return self.dim

    def _mark_rect(self, cv, at, selected=False):
        """The rect a card at `at` hands its focus mark: `at` shrunk by as
        much as the theme's mark grows a card (a lift's `grow`, read off the
        canvas's edge, where it draws nothing), so the mark's box is the
        card's own side, raised where it lifts. The cover keeps its clean
        scale (owner call 2026-10-17) and the lift's rim hugs it, with no
        plate between (design review R3-05): a picture card lifts by its
        rise and its shadow alone. `selected`: the mark is the selected
        card's current lift (`ui_draw.focus`)."""
        w, h = at[2], at[3]
        _ink, box = _ui_draw.focus(cv, self.theme, (-4 * w - 64, -4 * h - 64, w, h),
                                   size="card", after=True, selected=selected)
        g = (box[2] - w) // 2
        if g <= 0:
            return at
        return (at[0] + g, at[1] + g, w - 2 * g, h - 2 * g)

    def _draw_home_card(self, cv, it, rect, centre, sheet_for):
        NAMES = self._NAMES
        # The centre card at rest takes the theme's focus mark (ui.focus; a
        # lifted card draws raised, `_mark_rect`).
        rest = centre and not self.moving
        mark = rest and not self.tab_focus
        # On the desk the selected card is the one the details pane
        # describes: under touch, and while the tabs or the pane's buttons
        # hold the focus, it keeps its lift as a current mark (design review
        # R4-09); the theme's focus mark is the shelf's own focus.
        sel = rest and self.desk() and (self.tab_focus or _ui_draw.input_kind() == "touch"
                                        or not _ui_draw.marks()[0])
        at = rect
        if mark or sel:
            at = self._mark_rect(cv, rect, sel)
            _ink, rect = _ui_draw.focus(cv, self.theme, at, size="card",
                                        r=self._corner_r(rect[2]), selected=sel)
        cr = self._corner_r(rect[2])
        self._draw_picture(cv, it, rect, centre, sheet_for, cr)
        if centre and self.favorite_for is not None and not self.moving \
                and not self.desk() \
                and it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES:
            _draw_star_badge(cv, NAMES, self.theme or {},
                             self._favorite_badge_geom(rect),
                             bool(self.favorite_for(it)))
        if not cr:
            self._card_frame(cv, rect)
        if mark or sel:
            _ui_draw.focus(cv, self.theme, at, size="card", r=cr,
                           after=True, selected=sel)


class IconHome(Launcher):
    """The home's icons arrangement (docs/look_2026-10.md step 33): each
    cart's icon -- its cover's reduction -- in a tile with the card radius,
    as many columns as the glass holds across, scrolling down; focus grows
    a tile and rings it flat, as the shelf's does. On the handheld no tile
    carries a label: the focused cart's name and its line sit in one
    caption band at the grid's foot, over the hint strip, which clips the
    top of the next row so the grid reads as scrolling. On the desk every
    tile is named in full under it, the focused one bold."""

    arrangement = "icons"
    horizontal = False

    def _lay_cells(self, lay, keys, desk, depth):
        fs = lay.fs
        hints = _hint_strip(lay, keys, desk)
        if desk:
            u = _lb.render_scale()
            t, gap, room, top, base = (v // u for v in _ICONS_DK)
            margin = 16 // u
            caption = None
            band = (lay.cx, lay.cy, lay.cw, hints[1] - lay.cy)
            title_dy = t + base - _ui_draw.font()[3] * fs
        else:
            narrow, wide, gap, room, top, cap = (v * fs for v in _ICONS_HH)
            t = wide if lay.cw >= _WIDE * fs else narrow
            margin = 16 * fs
            caption = (lay.cx, hints[1] - cap, lay.cw, cap)
            band = (lay.cx, lay.cy, lay.cw, caption[1] - lay.cy)
            title_dy = 0
        p = t + gap
        cols = max(1, (lay.cw - margin) // p)
        py = t + room
        return _Cells(t, depth[1],
                      lay.cx + (lay.cw - cols * p) // 2 + gap // 2, lay.cy + top, p, py,
                      max(1, (band[3] - top - 1) // py + 1), cols, band, hints, caption,
                      title_dy, p, _GROW[0], desk)

    def cell_of(self, i):
        cols = self._cells().cols
        return i // cols, i % cols

    def index_of(self, row, col):
        return row * self._cells().cols + col

    def nav2d(self, dx, dy):
        """Left and right step a tile, up and down a row, clamped within the
        list; the scroll follows."""
        n = len(self.items)
        if not n:
            return
        cols = self._cells().cols
        if dx:
            row, col = self.cell_of(self.sel)
            self.sel = self.index_of(row, max(0, min(cols - 1, col + dx)))
        elif dy:
            self.sel += dy * cols
        self.sel = max(0, min(n - 1, self.sel))
        self._scroll_to_sel()

    def _extent(self):
        c = self._cells()
        n = len(self.items)
        if n <= 0:
            return 0
        top = c.y0 - c.band[1]
        return 2 * top + self.cell_of(n - 1)[0] * c.py + c.side + (c.py - c.side
                                                                     if c.desk else 0)

    def _span(self, i):
        c = self._cells()
        top = c.y0 - c.band[1]
        return (self.cell_of(i)[0] * c.py,
                2 * top + c.side + (c.py - c.side if c.desk else 0))

    def tile_at(self, px, py):
        c = self._cells()
        if not _in(px, py, c.band):
            return None
        for i, r in self._visible():
            if _in(px, py, (r[0] - (c.px - c.side) // 2, r[1], c.px,
                            c.py if c.desk else c.side)):
                return i
        return None

    def _fade(self, cv, c):
        return

    def _draw_cell(self, cv, it, rect, selected, c):
        """One tile: the cart's picture reduced to it (grown and ringed flat
        where it holds the focus), its 1 px edge, and on the desk its name
        under it, whole where the column holds it, bold on focus."""
        if c.desk:
            return Launcher._draw_cell(self, cv, it, rect, selected, c)
        th = self.theme or {}
        focus = self._focused(selected)
        x, y, n, _n = rect
        g = c.grow if focus else 0
        pic = (x - g, y - g, n + 2 * g, n + 2 * g)
        r = c.r + g if c.r else 0
        self._draw_picture(cv, it, pic, True, None, r, _COVER_HALF)
        if c.r:
            cv.rrect(pic[0], pic[1], pic[2], pic[3], r, None, ring=th.get("edge", 0))
        else:
            self._card_frame(cv, pic)
        if focus:
            _ui_draw.focus(cv, self.theme, pic, size="card", r=r, flat=True)

    def cover_specs(self):
        return [(it, _COVER_HALF) for it in self.items
                if it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES]

    def draw(self, cv, sheet_for=None):
        self._paint(cv, sheet_for)
        c = self._cells()
        if c.caption is not None:
            self._draw_caption_band(cv, c)

    def _draw_caption_band(self, cv, c):
        """The handheld's caption band: on the home's ground under a
        hairline, the focused cart's name in the `title` face from the first
        column's left, its metadata after it in the dim ink, the items apart
        by a dot."""
        th = self.theme or {}
        fs = self.layout.fs
        x, y, w, h = c.caption
        cv.rect(x, y, w, h, self._ground())
        cv.rect(x, y, w, fs, th.get("edge", 0))
        it = self.selected()
        if it is None:
            return
        base = y + 22 * fs
        left = c.x0
        right = x + w - (c.x0 - x)
        fid, _w, _lh, asc = _ui_draw.font("title")
        name = "New" if it.get("type") in PSEUDO_TILE_TYPES else (it.get("title") or "")
        was = _ui_draw.print_font(fid)
        name = _ui_draw.print_ellipsis(name, right - left, fs)
        tw = _ui_draw.print_width(name, fs)
        cv.print(name, left, base - asc * fs, th.get("ink", 7), 1)
        if was is not None:
            _ui_draw.print_font(was)
        f = _ui_draw.font()
        sep = 12 * fs
        mx = left + tw + 10 * fs
        my = base - f[3] * fs
        d = 2 * fs if _ui_draw.font_v2(f[0]) else fs
        dim = th.get("ink_dim", 6)
        for k, t in enumerate(self._meta(it)):
            t = _ui_draw.print_fit(t, right - mx, fs)
            if not t:
                break
            if k:
                cv.rect(mx - (sep + d) // 2, _dot_top(my, fs, d), d, d, dim)
            cv.print(t, mx, my, dim, 1)
            mx += _ui_draw.print_width(t, fs) + sep


def _wall_ink(ws):
    """(white, black) where the home lies on a wallpaper -- a cart or a
    picture, pixels the theme does not know -- else None: its text is then
    white over a black shadow, as the desk's labels are
    (docs/look_2026-10.md steps 25 and 28)."""
    if ws.look.wallpaper_id is None:
        return None
    black, white = ws.look.live.fixed[2:]
    return white, black


def _overlaps_any(rects, others):
    """Whether any of `rects` meets any of `others` (x, y, w, h)."""
    for a in rects:
        for b in others:
            if (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
                    and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]):
                return True
    return False


def _ground_of(ws):
    """The home's ground's colour: the look's `home_bg`, else the theme's
    `desktop` fill."""
    look = getattr(getattr(ws, "look", None), "live", None)
    c = _UNSET if look is None else look.look[0].get("home_bg", _UNSET)
    return ws.theme_colors["desktop"] if c == _UNSET else c


def _dim_of(ws):
    """The theme's neighbour dimming (`dim_neighbours`, Moy Night's): the
    colour the carousel's neighbours are dimmed toward -- the home's ground
    (`_ground_of`) -- and its alpha (0..255), or None."""
    look = getattr(getattr(ws, "look", None), "live", None)
    if look is None:
        return None
    pct = look.look[0].get("dim_neighbours", 0)
    if not pct or pct < 0:
        return None
    return (_ground_of(ws), (pct * 255 + 50) // 100)


def _retained_n(cv):
    """The canvas's physical-buffer rotation -- the staleness horizon the
    drag-partial streaks must respect (a back buffer on an N-buffer root is N
    frames stale). Parameterized for the P4 triple-framebuffer render overlap
    (#58); FLOORED at 2 so the host's single persistent buffer keeps today's
    conservative gates (behavior-identical at N<=2). Mirrors
    WindowedWM._retained_n."""
    n = getattr(cv, "RETAINED_FRAMES", 1)
    return n if n > 2 else 2


def _advance_streak(layer, cv):
    """Advance (or restart) a grid layer's consecutive-full-paint streak --
    the gate `_try_drag_partial` reads, so a partial only runs once every
    retained framebuffer already holds this frame's statics. A full paint
    under an unchanged `_statics_key` counts up to `_retained_n(cv)`; a
    changed key restarts at one. ONE body for the home shelf and the Editor
    picker (#209): repeated invalidation logic is the family
    surface_model_v1.md §14.2 names as the silent-cache bug."""
    key = layer._statics_key(cv)
    if key == layer._statics:
        if layer._full_streak < _retained_n(cv):
            layer._full_streak += 1
    else:
        layer._statics = key
        layer._full_streak = 1


def _cursor_stamp(ws):
    """The cursor sprite's footprint on the system canvas this frame, or None
    when it isn't drawn -- recorded with each shelf paint (#113) so a blitted
    frame can repaint the stale stamp the shift dragged along (CURSOR is 8x13
    at the system font scale, hotspot top-left)."""
    p = ws.pointer
    if p is None or not getattr(p, "visible", False):
        return None
    fs = ws.look.font_scale
    return (p.x, p.y, 8 * fs, 13 * fs)


def _paint_continuity(layer, ws):
    """Re-arm a grid layer's streak from zero when other surfaces painted
    since its last draw: the retained ping-pong buffers hold foreign pixels
    after a visit elsewhere, and a streak carried across it would let a
    partial repaint over the PREVIOUS screen's chrome in the second buffer."""
    nf = ws._frames_drawn
    if layer._last_pf is not None and nf != layer._last_pf + 1:
        layer._full_streak = 0
    layer._last_pf = nf


def _statics_base(ws, cv, grid):
    """What BOTH grid layers' static chrome is a pure function of: the canvas,
    the font scale, the look (`look_gen`), the grid rect (#113:
    set_layout no longer invalidates the ring eagerly, since the windowed tier
    re-applies layouts several times per frame, so this key is what makes a
    paint recorded under a different layout unmatchable) and the item count.
    The home shelf folds its own extras on top."""
    return (cv.w, cv.h, ws.layout.fs, ws.look.look_gen,
            grid.band_rect(), len(grid.items))


def _drag_partial(layer, cv, dt, grid, kind, fill, strip, timed=False):
    """The home layers' drag fast path (#113, #58/#66), ONE body for the home
    and the home window: while a touch drag or fling scrolls `grid` and
    every retained framebuffer already holds this frame's static chrome (two
    prior full paints under an unchanged statics key -- the ping-pong
    stale-by-2 rule), repaint ONLY the cards' band and the bar strip. The
    ring's `blit_shift` answers the offset the target buffer's band was
    painted at (same sel/statics/covers, consecutive paints), and the grid's
    `draw_shift` takes it; every arrangement's cards lie on the home's
    ground, so each repaints its band whole over `fill`. Everything outside
    the band (wallpaper, bars, hint strip) is byte-identical in the target
    buffer and left alone; full paints resume on release, so any straggler
    is erased within a frame.

    `fill(cv, rect)` paints the band backdrop the owner would paint under the
    cards; `strip` is the bar strip's kind; `timed` records the home diag's
    `ws._pf_home` split under perf_capture. Returns False when the frame
    must be painted in full."""
    ws = layer.ws
    if not (grid.dragging or grid.flinging):
        return False
    if (getattr(cv, "RETAINED_FRAMES", 0) < 1
            or layer._full_streak < _retained_n(cv)):
        return False
    if layer._statics != layer._statics_key(cv):
        return False
    # Anything animating over the surface (toast/confetti/splash/live
    # wallpaper) moves pixels outside the band -> full frames.
    if ws._animating(dt):
        return False
    top = getattr(ws.wm, "top_kind", None)
    if top is not None and top() != kind:
        return False
    bx, by, bw, bh = grid.band_rect()
    fs = ws.look.font_scale
    p = ws.pointer
    if p is not None and getattr(p, "visible", False):
        # The composited cursor must land fully inside the repainted band,
        # or its previous stamp would ghost on the untouched chrome.
        if not (bx <= p.x and p.x + 8 * fs <= bx + bw
                and by <= p.y and p.y + 13 * fs <= by + bh):
            return False
    _t0 = _ticks_ms() if timed and getattr(ws, "perf_capture", False) else None
    region = grid._scroll_region()
    key = (grid.paint_key(), layer._statics, ws.covers.gen)
    shift = region.blit_shift(cv, ws._frames_drawn, key)
    if shift is not None:
        delta, old_stamp = shift
        damage = []
        if old_stamp is not None:
            damage.append(old_stamp)               # where the shift left it...
            if delta:                              # ...and where it came from
                damage.append((old_stamp[0] - delta, old_stamp[1],
                               old_stamp[2], old_stamp[3]))
        grid.draw_shift(cv, ws.covers.sheet_icon, delta, damage, fill)
    else:
        grid.repaint_band(cv, ws.covers.sheet_icon, fill)
    # Re-pin the cover gen POST-draw: a cover landing during the card draw
    # is in these pixels, so the recorded key must carry the new gen.
    region.note_painted(ws._frames_drawn,
                        (grid.paint_key(), layer._statics, ws.covers.gen),
                        _cursor_stamp(ws))
    _t1 = _ticks_ms() if _t0 is not None else None
    ws.bar_layer._draw_status_strip(strip)
    # The band is the frame's damage; the bar strip carries its own word.
    ws.wm.hand_content_damage(cv, bx, by, bw, bh)
    if _t0 is not None:
        ws._pf_home = (0, _ticks_diff(_t1, _t0), _ticks_diff(_ticks_ms(), _t1))
    return True


class LauncherHomeLayer:
    """The "launcher" content Layer (system domain): the home, console mode's root.
    draw composes the wallpaper backdrop -> the tab row and the cards (`grid`,
    ws.launcher) -> the top bar; input is the grid nav / selection, dispatching
    the active tab's tap (ws.launch_selected). Owns the cursor's hover state
    (_lhover), the tab-row focus; `grid` is the single source
    it reads. `HomeWindowLayer` is the same home in a window."""

    id = "launcher"
    domain = "system"
    strip = "home"            # the bar strip it draws and taps (BarLayer's `where`)
    searchable = True         # Games carries SEARCH (the query is the home's)

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self._lhover = (-1, -1)       # the last pointer sample (a release is not a move)
        self._back_tip = False        # a mouse rests on the bar's way back to the desk
        self._hold = None             # a press on a card: [index, x, y, ms held]
        self._tab_at = None           # the tab the keys are on, on the tab row
        self._on_bar = False          # the keys are on the home's bar (BarLayer.focus_enter)
        self._tab_focus = False       # keys drive the tab row (up from the carousel)
        # Drag-scroll PARTIAL repaint bookkeeping (#58/#66: a FULL home repaint
        # measured ~100-140ms on BOTH boards' glass -- backdrop + panel + cards
        # stack megabytes of writes -- capping a shelf drag at ~7fps). During a
        # drag only the grid band + bar change, so eligible frames skip the
        # wallpaper/panel chrome entirely (see _try_drag_partial): the streak
        # counts consecutive FULL paints with identical statics, so a partial
        # only runs once every retained framebuffer already holds them.
        self._full_streak = 0
        self._statics = None
        self._search_kprev = 0        # keyboard edge detect while typing a search query
        self._kprev = 0               # ...and for the carousel home's hint keys
        self._zone_bump = 0           # the tab row's focus moved (the bar's zone shows it)
        # Retained WHOLE-frame cache (2026-07-27, the damage-model doctrine at
        # shelf granularity): the last full home frame, captured at the END of
        # a live paint (right after our own last pixel -- overlays/cursor draw
        # AFTER this layer, so the copy is exactly our output) and stamped back
        # on re-entry when NOTHING the frame reads has changed (_retained_key)
        # -- a repeat home visit becomes one full-screen blit instead of the
        # ~150ms wallpaper + panel + cards + bar rebuild. An idle frame
        # captures only what every buffer holds (`capture_due`, owed by a
        # still frame): the P4 root rotates its buffers, so after present the
        # canvas's CURRENT buffer may hold an older frame, and an idle capture
        # of it would cache another screen's pixels under this frame's key.
        self._lib_cache = None        # cv.new_layer sized to the canvas
        self._lib_key = None          # _retained_key at capture
        self._cap_owed = None         # (frame, key, parts) a still frame owes
        self._lib_unsupported = False # web RecordingLayer: no pixels to copy
        # Paint-continuity guard for the ping-pong trust (#113's stale-by-2
        # rule): if OTHER surfaces painted since our last draw, the retained
        # buffers hold foreign pixels and the partial/streak machinery must
        # re-arm from zero. Without this, a re-entry inherited streak=2 from
        # the previous visit and the first drag partially repainted over the
        # PREVIOUS screen's chrome in the second buffer.
        self._last_pf = None
        # The wallpaper under the carousel band, captured on every full paint
        # right after the wallpaper drew: a moving frame restores the band's
        # backdrop from it (the cards lie on the wallpaper, so the band cannot
        # shift). The key is what the capture was taken under -- the canvas,
        # the theme, the wallpaper and the band rect.
        self._band_bg = None          # cv.new_layer sized to the band
        self._band_key = None
        # The desk home's still-backdrop frames (`_try_still_frame`): one
        # entry a painted frame, [frame, backdrop, selection, band] keys,
        # the last RETAINED_FRAMES of them; the backdrop `restore_key` this
        # frame answered; and the parts the retained frame was captured
        # under (`_capture_retained` copies only the ones that moved).
        self._still_ring = []
        self._bd_now = None
        self._lib_parts = None

    @property
    def grid(self):
        """The card model this home draws: ws.launcher, whose class is the
        layout's arrangement."""
        return self.ws.launcher

    def _grid_or_none(self):
        """The card model, None while it is not minted yet (the window's
        before its first open, the home's during the console's build)."""
        try:
            return self.grid
        except AttributeError:
            return None

    def _wall(self):
        """(white, black) where this home lies on a wallpaper (`_wall_ink`),
        else None."""
        return _wall_ink(self.ws)

    @property
    def _tab_focus(self):
        return self._tab_keys

    def at_top(self):
        """Up goes on to the bar from the tab row where the row is below
        the bar (Workstation._bar_keys); where the bar holds the tabs they
        are one row with its items, and up stays."""
        return bool(self._tab_keys) and not self.tabs_in_bar()

    def tabs_in_bar(self):
        """`tab_rects` where the tabs sit in the bar's row (the handheld's
        zone), so the keys walk them and the bar's items as one row
        (`BarLayer.focus_beside_tabs`); else []."""
        return self.tab_rects()

    def tab_enter(self, k):
        """The keys onto the tab row at its `k`th tab (-1 the last), from
        the bar's items beside it."""
        self._tab_focus = True
        ids = self._tab_ids()
        self._tab_at = ids[k]
        self._zone_bump += 1
        self.ws.mark_dirty()

    def _tab_ids(self):
        """The ids of the tabs the row shows, left to right."""
        return [t[0] for t in self.tab_rects()] or [t for t, _l in HOME_TABS]

    def focused_tab(self):
        """The tab the keys are on while the tab row has them (it opens on
        confirm), else None."""
        if not self._tab_keys:
            return None
        ids = self._tab_ids()
        at = self._tab_at
        return at if at in ids else self.grid.tab

    def _tab_step(self, d):
        """The tab row's focus `d` tabs along; off its ends onto the bar's
        item beside it where the bar holds the tabs, else it stops there.
        The open tab stays until a confirm."""
        ids = self._tab_ids()
        at = self.focused_tab()
        k = ids.index(at) + d if at in ids else 0
        if not 0 <= k < len(ids) and self.ws.bar_layer.focus_beside_tabs(d):
            return
        k = max(0, min(len(ids) - 1, k))
        if ids[k] != self._tab_at:
            self._tab_at = ids[k]
            self._zone_bump += 1
            self.ws.mark_dirty()

    @_tab_focus.setter
    def _tab_focus(self, on):
        # one focus mark a screen: the tab row's takes it from the cards
        if on != getattr(self, "_tab_keys", None):
            self._zone_bump = getattr(self, "_zone_bump", 0) + 1
            g = self._grid_or_none()
            self._tab_at = g.tab if on and g is not None else None
        self._tab_keys = on
        if on:
            self._btn = None
        g = self._grid_or_none()
        if g is not None:
            g.tab_focus = (on or getattr(self, "_btn", None) is not None
                           or getattr(self, "_on_bar", False))

    def bar_focus(self, on):
        """The keys go on to the home's bar (`on`) or off it: the cards let
        their focus mark go meanwhile, one mark a screen."""
        if on != self._on_bar:
            self._on_bar = on
            self._zone_bump += 1
        g = self._grid_or_none()
        if g is not None:
            g.tab_focus = on or self._tab_keys or getattr(self, "_btn", None) is not None
        self.ws.mark_dirty()

    @property
    def _button_focus(self):
        """The desk home's button the keys are on (up from the shelf), an
        index into `_desk_buttons`, or None: the shelf's card has them."""
        return getattr(self, "_btn", None)

    @_button_focus.setter
    def _button_focus(self, k):
        # one focus mark a screen: the button's takes it from the card
        if k != getattr(self, "_btn", None):
            self._zone_bump = getattr(self, "_zone_bump", 0) + 1
        self._btn = k
        if k is not None:
            self._tab_keys = False
        g = self._grid_or_none()
        if g is not None:
            g.tab_focus = (k is not None or bool(getattr(self, "_tab_keys", False))
                           or getattr(self, "_on_bar", False))
        self.ws.mark_dirty()

    @staticmethod
    def _button_rows_apart(buttons, k):
        """(the button over `k`, the button under it) where the pane's
        buttons wrap to rows, each the nearest in its row; None for none."""
        r = buttons[k][1]
        mid = r[0] + r[2] // 2
        out = []
        for above in (True, False):
            ys = [b[1][1] for b in buttons if (b[1][1] < r[1]) == above and b[1][1] != r[1]]
            if not ys:
                out.append(None)
                continue
            y = max(ys) if above else min(ys)
            row = [j for j, b in enumerate(buttons) if b[1][1] == y]
            out.append(min(row, key=lambda j: abs(buttons[j][1][0] + buttons[j][1][2] // 2
                                                   - mid)))
        return out[0], out[1]

    def _button_keys(self, i):
        """The keys on the desk home's buttons: left and right move along
        them, confirm presses one, up and down move between their rows where
        they wrap, up from the first row goes on to the tabs, down from the
        last and B back to the shelf. False where no button has them, or for another key."""
        k = self._button_focus
        if k is None:
            return False
        ws = self.ws
        buttons = self._desk_buttons()
        if not buttons:
            self._button_focus = None
            return False
        k = min(k, len(buttons) - 1)
        if not any(i.pressed(n) for n in ("left", "right", "up", "down", "a", "run", "b",
                                          "home")):
            return False                     # the strip's letters still act
        up, down = self._button_rows_apart(buttons, k)
        if i.pressed("left"):
            self._button_focus = max(0, k - 1)
        elif i.pressed("right"):
            self._button_focus = min(len(buttons) - 1, k + 1)
        elif i.pressed("up") and up is not None:
            self._button_focus = up
        elif i.pressed("up"):
            self._tab_focus = True
        elif i.pressed("down") and down is not None:
            self._button_focus = down
        elif i.pressed("down") or (i.pressed("b") and ws.ach_ui._konami_pos == 0):
            self._button_focus = None
        elif i.pressed("a") or i.pressed("run"):
            v = buttons[k][0]
            if v != "delete":                # the confirm's Cancel comes back to DEL
                self._button_focus = None
            self._do_verb(v)
        elif i.pressed("home"):
            ws.home_key()
        return True

    def _statics_key(self, cv):
        """Everything the home frame's STATIC chrome (wallpaper backdrop +
        Library panel fill/header/footer) is a pure function of: the shared
        base plus the wallpaper, the search field and the keys a modal's hint
        strip names (`modal_keys`). A key change forces full paints until the
        streak re-arms."""
        ws = self.ws
        return _statics_base(ws, cv, self.grid) + (
            ws.look.wallpaper_id, modal_keys(ws),
            getattr(ws, "search_query", ""), getattr(ws, "search_typing", False),
            getattr(ws, "search_typing", False) and ws.can_type(),
            self.grid.tab, self._tab_focus, self._tab_at, self._button_focus,
            self._on_bar, self.grid.arrangement,
            self.grid.statics_sel(), self._back_tip)

    def _retained_key(self, cv):
        """Everything the WHOLE home frame's pixels are a pure function of: the
        chrome statics plus the grid's dynamics (selection, scroll offset, the
        cover-cache generation, trackball hover, per-item titles, favorites)
        and the bar's own pixel key (`shown_key` -- clock minute, wifi state,
        the look; reusing it means the bar's cache discipline IS this cache's
        discipline). Anything that draws a pixel of the home frame and is NOT
        derivable from this tuple is a staleness bug -- the silent-cache family
        surface_model_v1.md §14.2 documents -- so additions to the home draw
        must extend this key."""
        ws = self.ws
        return (self._statics_key(cv), self.grid.sel, self.grid.scroll,
                ws.covers.gen, self._lhover,
                tuple(it.get("title") for it in self.grid.items),
                ws.system.text("favorites"), ws.system.text("plays"),
                ws.bar_layer.shown_key("home"))

    def _try_stamp_retained(self, cv, dt):
        """Present the retained home frame when nothing it depends on changed:
        one full-screen blit (~26ms on P4 glass) instead of the full rebuild.
        Only for settled frames -- a drag/fling wants the partial path, and an
        animating overlay/wallpaper means the pixels are NOT a pure function
        of the key. Returns True when the frame was presented."""
        ws = self.ws
        cache = self._lib_cache
        if (cache is None or self._lib_unsupported
                or self.grid.dragging or self.grid.flinging
                or ws._animating(dt)):
            return False
        # A canvas that retains nothing (RETAINED_FRAMES < 1) holds no frame to
        # capture from or replay.
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return False
        if cache.w != cv.w or cache.h != cv.h:
            return False
        if self._retained_key(cv) != self._lib_key:
            return False
        cv.blit_strip(cache, 0, 0)
        return True

    def prealloc_retained(self):
        """Mint the retained-frame layer on an IDLE frame (called from frame()'s
        idle branch): the device new_layer pre-collects, which measured as a
        371ms first home visit when the allocation rode the capture's paint
        frame (~150ms collect + ~35ms copy on top of the ~180ms live paint).
        Allocation reads no pixels, so the ping-pong trap in __init__'s note
        does not apply -- only the CAPTURE must stay on the paint path."""
        ws = self.ws
        if self._lib_unsupported:
            return
        cv = ws.sys_canvas
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return
        cache = self._lib_cache
        try:
            if cache is None or cache.w != cv.w or cache.h != cv.h:
                self._lib_cache = cv.new_layer(cv.w, cv.h)
                self._lib_key = None      # freshly minted: no captured pixels
                self._lib_parts = None
            self._band_layer(cv)
        except Exception:  # noqa: BLE001
            self._lib_cache = None
            self._band_bg = None
            self._lib_unsupported = True

    def _band_layer(self, cv):
        """The band backdrop's layer, minted (or re-minted for a new band
        size) on demand."""
        bx, by, bw, bh = self.grid.band_rect()
        bg = self._band_bg
        if bg is None or bg.w != bw or bg.h != bh:
            if bg is not None and getattr(bg, "release", None) is not None:
                bg.release()
            self._band_bg = bg = cv.new_layer(bw, bh)
            self._band_key = None
        return bg

    def release_retained(self):
        """Free the retained-frame layer (the fit check's release, after any
        parked window stamps): True when there was one. The next settled
        visit re-mints and re-captures it. The band's backdrop goes with it."""
        freed = False
        for name in ("_lib_cache", "_band_bg"):
            cache = getattr(self, name)
            if cache is None:
                continue
            setattr(self, name, None)
            rel = getattr(cache, "release", None)
            if rel is not None:
                rel()
            freed = True
        self._lib_key = None
        self._lib_parts = None
        self._band_key = None
        return freed

    def _band_key_of(self, cv):
        ws = self.ws
        return _statics_base(ws, cv, self.grid) + (ws.look.wallpaper_id,)

    def _capture_band_backdrop(self, cv, dt):
        """Copy the band's rect of the wallpaper just drawn -- before any
        chrome or card -- on every full paint, so an edited wallpaper (the
        Paint app's document keeps its id) is never restored stale. Not while
        the wallpaper animates (those frames are full paints anyway) and not
        on a canvas that retains nothing."""
        ws = self.ws
        self._band_key = None
        if (self._lib_unsupported or ws._animating(dt)
                or ws.wallpaper.restore_key(True) is not None):
            return                # (a wallpaper that restores needs no copy)
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return
        key = self._band_key_of(cv)
        try:
            bg = self._band_layer(cv)
            bx, by, _bw, _bh = self.grid.band_rect()
            bg.blit_strip(cv, -bx, -by)
            self._band_key = key
        except Exception:  # noqa: BLE001 -- forfeit, as the retained frame does
            self._band_bg = None
            self._band_key = None
            self._lib_unsupported = True

    def _backdrop_fill(self, cv, r):
        """The band's backdrop inside rect `r`: the wallpaper, restored from
        the captured band."""
        bx, by, _bw, _bh = self.grid.band_rect()
        cv.blit_strip_rect(self._band_bg, bx, by, r[0], r[1], r[2], r[3])

    def _capture_retained(self, cv, dt, parts=None):
        """Capture the frame just painted into the retained cache -- called at
        the END of a live draw, while cv is still the back buffer being painted
        (pre-present, so it is provably THIS frame; see __init__'s ping-pong
        note for why an idle capture cannot be trusted). Overlays and the
        cursor draw after this layer, so the copy is exactly our output. Costs
        one full-screen copy (~26ms on P4) charged only when the key CHANGED --
        once per settled state, not per frame. Skipped mid-drag/fling (the key
        carries the scroll offset, so every frame would re-capture) and while
        anything animates (those pixels are not a pure function of the key)."""
        ws = self.ws
        if (self._lib_unsupported or self.grid.dragging
                or self.grid.flinging or ws._animating(dt)):
            return
        if getattr(cv, "RETAINED_FRAMES", 0) < 1:
            return              # a canvas that retains nothing has no frame to copy
        key = self._retained_key(cv)
        if key == self._lib_key:
            return
        if parts is None and self._carousel():
            parts = self._still_parts(cv, ws.wallpaper.restore_key(True))
        bar = ws.bar_layer.shown_key("home")
        try:
            cache = self._lib_cache
            lp = self._lib_parts
            if cache is None or cache.w != cv.w or cache.h != cv.h:
                cache = self._lib_cache = cv.new_layer(cv.w, cv.h)
                lp = None
            ws.note_cost("home.frame.capture")
            if parts is not None and lp is not None and lp[0] == parts[0]:
                # The captured frame has this one's backdrop: only the
                # parts that moved since are copied.
                for r in self._moved_rects(parts, lp, bar):
                    cache.blit_strip_rect(cv, 0, 0, r[0], r[1], r[2], r[3])
            else:
                cache.blit_strip(cv, 0, 0)
            self._lib_key = key
            self._lib_parts = None if parts is None else parts + (bar,)
        except Exception:  # noqa: BLE001 -- a recording root has no pixels to
            self._lib_cache = None     # copy; forfeit the mechanism for the
            self._lib_key = None       # session rather than retry every paint
            self._lib_parts = None
            self._lib_unsupported = True

    def _moved_rects(self, parts, was, bar):
        """The rects of the desk home's frame `parts` (and bar key `bar`)
        that differ from the frame `was` held, where the two share a
        backdrop: the selection's, the band, the bar's and the hint strip."""
        g = self.grid
        out = []
        if was[1] != parts[1]:
            out.append(self._sel_rect())
        if was[2] != parts[2]:
            out.append(g.band_rect())
        if was[3] != bar:
            out.extend(self.ws.wallpaper.bars())
        h = g.foot()[5]
        if h[2] > 0 and h[3] > 0:
            out.append(h)
        return out

    def _carousel(self):
        """Whether the home shows the carousel (else the grid arrangement)."""
        return isinstance(self.grid, Carousel)

    def _try_drag_partial(self, cv, dt):
        """The home's moving frame (`_drag_partial`), timed for the home
        diag's HITCH split: only once the band's backdrop holds this frame's
        wallpaper, every arrangement's cards lying on it. Never under
        touch's search line, which lies in the band."""
        if self._bd_now is not None:
            fill = self.ws.wallpaper.restorer(cv, self._bd_now)
        elif self._band_bg is None or self._band_key != self._band_key_of(cv):
            return False
        else:
            fill = self._backdrop_fill
        if self._search_line() is not None:
            return False
        return _drag_partial(self, cv, dt, self.grid, self.id,
                             fill, self.strip, timed=True)

    # -- the desk home's still-backdrop frame ------------------------------
    #
    # On the desk the home is a still backdrop under three parts that
    # change: the hero with its details pane (the selection), the shelf's
    # band and the bar. A frame repaints only the parts the buffer it paints
    # lacks, where the wallpaper can put its own pixels back
    # (`Wallpaper.restore_key`): every buffer holds this backdrop once the
    # RETAINED_FRAMES frames before this one were the home's alone, on it,
    # and each of those frames left its buffer holding its own parts. A
    # part that differs from any of theirs lags in this frame's buffer.

    def _still_parts(self, cv, bd):
        """(backdrop, selection, band) keys of the desk home's frame: what
        everything outside the selection's rect and the band is a pure
        function of (the wallpaper's `restore_key` `bd` among it), what the
        hero and its details are, and what the shelf's band is. None off
        the desk, or where the backdrop cannot be put back."""
        g = self.grid
        if bd is None or not g.desk():
            return None
        ws = self.ws
        typing = getattr(ws, "search_typing", False)
        base = (bd, cv.w, cv.h, ws.layout.fs, ws.look.look_gen, g._geom(),
                ws.look.wallpaper_id, modal_keys(ws), getattr(ws, "search_query", ""),
                typing, typing and ws.can_type(), self._tab_focus, self._tab_at,
                self._on_bar, self._back_tip)
        items = g.items
        it = items[g.nearest()] if items else None
        st = ws.system
        fav = st.text("favorites")
        plays = st.text("plays")
        gen = ws.covers.gen
        sel = g.statics_sel()
        return (base,
                (g.tab, sel, it, it and it.get("title"), it and it.get("description"),
                 gen, fav, plays, self._button_focus, g.today()),
                (g.tab, sel, gen, self._lhover, tuple(i.get("title") for i in items),
                 fav, plays, g.scroll, g.moving))

    def _sel_rect(self):
        """The desk home's hero and details pane with all they cast (the
        hero's shadow, a button's focus mark),
        between the bar and the shelf's band: what a change of the
        selection repaints."""
        g = self.grid
        lay = self.ws.layout
        q = g.quiet()[0]
        d = _ui_draw.depth()
        m = _ui_draw.mark_reach("part") + 1
        if d[5]:
            m += d[8] + max(abs(d[9]), abs(d[10]))    # the hero's shadow
        x0 = max(lay.cx, q[0] - m)
        y0 = max(lay.cy, q[1] - m)
        x1 = min(lay.cx + lay.cw, q[0] + q[2] + m)
        y1 = min(g.band_rect()[1], q[1] + q[3] + m)
        return (x0, y0, x1 - x0, y1 - y0)

    def _fill_around_hero(self, cv, r, fill):
        """Put the backdrop back in the selection's rect `r` but under the
        hero, which paints its square opaque but for its corners' rows (as
        a shelf card does, `Carousel._fill_around_cards`)."""
        hx, hy, hs, _h = self.grid.hero_rect()
        x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
        if not (x0 <= hx and y0 <= hy and hx + hs <= x1 and hy + hs <= y1):
            fill(cv, r)
            return
        k = self.grid._corner_r(hs)
        k = min(k + _ui_draw.mark_reach("card") + 1 if k else 0, hs // 2)
        fill(cv, (x0, y0, r[2], hy - y0))
        fill(cv, (x0, hy + hs, r[2], y1 - hy - hs))
        fill(cv, (x0, hy, hx - x0, hs))
        fill(cv, (hx + hs, hy, x1 - hx - hs, hs))
        if k:
            fill(cv, (hx, hy, hs, k))
            fill(cv, (hx, hy + hs - k, hs, k))

    def _content_alone(self, dt):
        """Whether this frame's pixels are the home's alone: on top, nothing
        animating, nothing drawn over it (`content_alone`)."""
        ws = self.ws
        wm = ws.wm
        if ws._animating(dt):
            return False
        top = getattr(wm, "top_kind", None)
        if top is not None and top() != self.id:
            return False
        alone = getattr(wm, "content_alone", None)
        return alone is not None and alone()

    def _note_still(self, cv, parts, bar=None):
        """Record this painted frame's parts in the ring, with the bar's key
        (`shown_key`) its strip was drawn under, or with None that its
        buffer holds something else."""
        ring = self._still_ring
        f = self.ws._frames_drawn
        if parts is None or (ring and ring[-1][0] != f - 1):
            del ring[:]                  # (another surface painted between)
        if parts is None:
            return
        if bar is None:
            bar = self.ws.bar_layer.shown_key("home")
        ring.append((f,) + parts + (bar,))
        if len(ring) > _retained_n(cv):
            del ring[0]

    def _try_still_frame(self, cv, dt):
        """The desk home's frame over its still backdrop: the parts this
        frame's buffer lacks put back over the wallpaper's own pixels, and
        the bar. False when the frame must take another path."""
        ws = self.ws
        if (self._lib_unsupported or self._back_tip
                or self._search_line() is not None):
            return False
        parts = self._still_parts(cv, self._bd_now)
        if parts is None or not self._content_alone(dt):
            return False
        n = _retained_n(cv)
        ring = self._still_ring
        if len(ring) < n:
            return False
        f = ws._frames_drawn
        base, sel, band = parts
        bar = ws.bar_layer.shown_key("home")
        sel_lag = band_lag = bar_lag = False
        for k in range(1, n + 1):
            e = ring[-k]
            if e[0] != f - k or e[1] != base:
                return False
            if e[2] != sel:
                sel_lag = True
            if e[3] != band:
                band_lag = True
            if e[4] != bar:
                bar_lag = True
        g = self.grid
        wm = ws.wm
        # A part the last frame painted as this one would is copied out of
        # its buffer (§14.6): whole rows where the bar there is this one's.
        e = ring[-1]
        rows = e[4] == bar
        lay_sel = sel_lag and not (e[2] == sel and self._copy_part(cv, self._sel_rect(), rows))
        lay_band = band_lag and not (e[3] == band and self._copy_part(cv, g.band_rect(), rows))
        if bar_lag and rows and not (lay_sel or lay_band):
            for r in ws.wallpaper.bars():
                if not self._copy_part(cv, r, True):
                    break
            else:
                bar_lag = False
        laid = self._lay_parts(cv, lay_sel, lay_band, sel_lag)
        h = g.foot()[5]
        if h[2] > 0 and h[3] > 0:
            laid.append(h)
            ws.wallpaper.restorer(cv, self._bd_now)(cv, h)
            self._draw_home_chrome(cv)
            wm.hand_content_damage(cv, h[0], h[1], h[2], h[3])
        g._scroll_region().note_painted(
            f, (g.paint_key(), self._statics_key(cv), ws.covers.gen),
            _cursor_stamp(ws))
        # A strip this buffer holds under this key stays, unless a part
        # above was put back over it.
        if bar_lag or _overlaps_any(laid, ws.wallpaper.bars()):
            ws.bar_layer._draw_status_strip("home")
        # The drag partial's streak counts whole frames under one statics
        # key: a selection painted here is not one of them.
        self._full_streak = 0
        self._note_still(cv, parts, bar)
        if self._still_held(parts):
            # every buffer holds this frame: the retained frame takes it once
            # nothing waits on the board (`capture_due`)
            key = self._retained_key(cv)
            if key != self._lib_key:
                self._cap_owed = (ws._frames_drawn, key, parts)
        else:
            self._still_debt()
        return True

    def capture_due(self):
        """Take the retained frame a still frame owed (`_cap_owed`) on an
        idle frame, or at the start of the next painted one before anything
        draws: every buffer held that frame when it was owed and still does
        while no frame has painted since and its key holds. Dropped
        otherwise; the next still frame that finds every buffer holding it
        owes it again."""
        owed = self._cap_owed
        self._cap_owed = None
        ws = self.ws
        if owed is None or ws._frames_drawn != owed[0] + 1:
            return
        cv = ws.sys_canvas
        if self._retained_key(cv) == owed[1]:
            self._capture_retained(cv, 1 / 60, owed[2])

    def _copy_part(self, cv, r, rows):
        """Part rect `r` copied out of the buffer the last frame drew into,
        which holds it as this frame would draw it (§14.6) -- its whole rows
        with `rows`: what else they hold, the backdrop and the bar, is this
        frame's there too. False where the canvas cannot copy."""
        cl = getattr(cv, "copy_last", None)
        if rows:
            r = (0, r[1], cv.w, r[3])
        if cl is None or not cl(r[0], r[1], r[2], r[3]):
            return False
        self.ws.wm.hand_content_damage(cv, r[0], r[1], r[2], r[3])
        return True

    def _lay_parts(self, cv, sel_lag, band_lag, marks):
        """Put the lagging parts back over the backdrop and draw them; the
        rects laid. `marks`: the buffer may hold the focus mark on another
        card."""
        ws = self.ws
        fill = ws.wallpaper.restorer(cv, self._bd_now)
        g = self.grid
        wm = ws.wm
        laid = []
        if sel_lag:
            r = self._sel_rect()
            laid.append(r)
            self._fill_around_hero(cv, r, fill)
            cv.clip(r[0], r[1], r[2], r[3])
            g._paint_details(cv)
            self._draw_details(cv)
            cv.clip()
            wm.hand_content_damage(cv, r[0], r[1], r[2], r[3])
        if band_lag:
            g.repaint_band(cv, ws.covers.sheet_icon, fill, marks)
            bx, by, bw, bh = g.band_rect()
            laid.append((bx, by, bw, bh))
            wm.hand_content_damage(cv, bx, by, bw, bh)
        return laid

    def _still_held(self, parts):
        """Whether every entry of the ring is `parts`: each retained buffer
        holds this frame."""
        for e in self._still_ring:
            if e[1] != parts[0] or e[2] != parts[1] or e[3] != parts[2]:
                return False
        return True

    def _still_debt(self):
        """Ask for the frame that brings one more buffer to this frame,
        while nothing moves and no press waits on it (as the windowed WM
        pays its own, `debt_owed`)."""
        ws = self.ws
        if (not self.grid.moving and not ws._ptr_changed()
                and not getattr(ws.pointer, "down", False)):
            ws.request_frame()

    def draw(self, dt):
        """The home desktop: wallpaper backdrop -> cart icon grid -> top status
        strip. The wallpaper is drawn first and the rest layer over it, exactly the
        Picotron model (wallpaper shows through the chrome). All on the SYSTEM canvas,
        reflowed to its size + font scale (#39).

        There is no bottom tool dock: the launcher dropped it in #46 (its
        code/draw/map/run slots had no cart to act on, so it was a dead row) and it
        was deleted outright on 2026-08-21 -- see bar_layer.py. Settings is reached
        via the gear in the status strip; the cart grid keeps the freed bottom band
        (Layout.grid_bottom)."""
        ws = self.ws
        cv = ws.sys_canvas
        _paint_continuity(self, ws)           # foreign paints void the buffers
        if ws.menu_ui.put_home(cv, dt):
            # the system menu's frame of the home, washed: the buffers hold
            # the scrim, no frame of the home's own
            self._full_streak = 0
            self._note_still(cv, None)
            return
        # The board's motion verdict (a WM constant, §12's degradation order).
        wm = ws.wm
        carousel = self._carousel()
        if carousel:
            self.grid.motion = getattr(wm, "HOME_MOTION", "full")
            self.grid.scale = getattr(wm, "HOME_SCALE", HOME_SCALE_HH)
            self.grid.dim = _dim_of(ws)
        self.grid.wall_ink = self._wall()
        self.grid.ground = _ground_of(ws)
        # Kinetic fling (#113) and the glide to the centre card: advance the
        # row BEFORE painting, and keep the redraw gate open until it rests.
        # Both ride the partial path (its gate includes flinging).
        self.grid.anim_frame(dt)
        if self.grid.flinging:
            ws.request_frame()
        self._bd_now = ws.wallpaper.restore_key(True)
        if carousel and self._try_still_frame(cv, dt):
            return
        if self._try_drag_partial(cv, dt):    # drag-scroll fast path (#58/#66)
            self._note_still(cv, None)
            return
        # Retained-frame stamp (2026-07-27): a re-entry whose pixels are provably
        # identical to the captured frame presents it in one blit. Bookkeeping
        # matches a full paint exactly -- pixel-wise it IS one -- so the scroll
        # ring and the drag partial's streak stay honest.
        # (under the menu's hold the retained frame is the one before it)
        if not ws.menu_ui.holds_home() and self._try_stamp_retained(cv, dt):
            self.grid._scroll_region().note_painted(
                ws._frames_drawn,
                (self.grid.paint_key(), self._statics_key(cv), ws.covers.gen),
                _cursor_stamp(ws))
            _advance_streak(self, cv)
            parts = self._still_now(cv, dt)
            self._note_still(cv, parts)
            if parts is not None and len(self._still_ring) < _retained_n(cv):
                self._still_debt()
            return
        # perf_capture (#66 instrument-before-cutting): time the home frame's
        # three sections so a slow launcher frame names its eater (PERF's
        # home= field carries ws._pf_home -- wallpaper vs grid vs bar).
        _t0 = _ticks_ms() if getattr(ws, "perf_capture", False) else None
        ws.wallpaper.draw(dt, home=True, quiet=self._quiet(ws))
        self._capture_band_backdrop(cv, dt)
        _t1 = _ticks_ms() if _t0 is not None else None
        # The hint strip and the cells' band (docs/shell_layout_2026-10.md
        # §12) over the wallpaper; the cards draw inside the band.
        self._draw_home_chrome(cv)
        self.grid.draw(cv, ws.covers.sheet_icon)
        if carousel and self.grid.desk():
            self._draw_details(cv)
        # #113: record this full paint in the shelf's blit ring (offset + the
        # state the pixels depend on + the cursor stamp about to land on top),
        # so an eligible drag frame can shift instead of repainting every card.
        self.grid._scroll_region().note_painted(
            ws._frames_drawn,
            (self.grid.paint_key(), self._statics_key(cv), ws.covers.gen),
            _cursor_stamp(ws))
        _t2 = _ticks_ms() if _t0 is not None else None
        ws.bar_layer._draw_status_strip("home")
        if self._back_tip:
            self._draw_back_tip(cv)
        if _t0 is not None:
            _t3 = _ticks_ms()
            ws._pf_home = (_ticks_diff(_t1, _t0), _ticks_diff(_t2, _t1),
                           _ticks_diff(_t3, _t2))
        # A FULL paint landed in the current framebuffer.
        _advance_streak(self, cv)
        parts = self._still_now(cv, dt)
        self._note_still(cv, parts)
        if not ws.menu_ui.holds_home():
            # (under the menu the retained frame stays the one its close stamps)
            self._capture_retained(cv, dt, parts)   # retain this frame for re-entry stamps
        if parts is not None and len(self._still_ring) < _retained_n(cv):
            self._still_debt()

    def _still_now(self, cv, dt):
        """This whole frame's parts where it is the home's alone, else None."""
        if not self._carousel() or not self._content_alone(dt):
            return None
        return self._still_parts(cv, self.ws.wallpaper.restore_key(True))

    def _hover_back(self, px, py, click, down):
        """Whether a mouse rests on the bar's chevron back to the desk (the
        home in console mode over a desk, `BarLayer.back_rect`): a pointer
        that moves with nothing held, never a touch, which has no hover."""
        if click or down or _ui_draw.input_kind() == "touch":
            return False
        r = self.ws.bar_layer.back_rect("home")
        return r is not None and _in(px, py, r)

    def _draw_back_tip(self, cv):
        """The chevron's tooltip, `BACK_TIP` on a plate under it: where it
        goes, which the bare chevron does not say (owner, 2026-10-19)."""
        ws = self.ws
        r = ws.bar_layer.back_rect("home")
        if r is None:
            return
        th = ws.theme_colors
        fs = ws.layout.fs
        u = _lb.unit()
        pad = 6 * u
        tw = _ui_draw.print_width(BACK_TIP, fs)
        h = ws.layout.line_h + pad
        x, y = r[0], ws.layout.cy + 4 * u      # under the bar
        cv.rrect(x, y, tw + 2 * pad, h, 4 * u, th["ink"])
        cv.print(BACK_TIP, x + pad, y + (h - ws.layout.line_h) // 2, th["surface"], 1)

    def tab_rects(self):
        """[(tab id, rect, labels_on)] of the home's tabs in the bar's zone
        (`zone_tabs`), on the glass."""
        got = _shell_layout.cell_of(self.ws.layout.bars, "zone")
        if got is None:
            return []
        rx, ry = self.ws.layout.bars[got[0]][1][:2]
        zx, zy, zw, zh = got[1]
        return [(tid, (rx + r[0], ry + r[1], r[2], r[3]), True)
                for tid, _l, r in self.zone_tabs((zx, zy, zw, zh))]

    def _draw_home_chrome(self, cv):
        """The hint strip along the home's foot (its tabs are the bar's)."""
        self._draw_hints(cv)

    def _search_label(self, query, typing):
        """The search box's words: "Search: " and the query (a bar caret
        follows it while typed, `_draw_search`), or, where the input in use
        cannot type, that a keyboard is needed (#239 is the on-screen
        keyboard)."""
        if typing and not query and not self.ws.can_type():
            return self.ws.no_keyboard_text
        return "Search: " + query

    def _search_open(self):
        """Whether the home's search is open: a query held or being typed."""
        ws = self.ws
        return bool(self.searchable and (getattr(ws, "search_query", "")
                                         or getattr(ws, "search_typing", False)))

    def _search_line(self):
        """(x, y, w, h) of the handheld home's search line while search is
        open: the hint strip, or under touch, which has none, a line on the
        home's ground 4 units over the glass's foot (design review R5-04);
        None while search is closed."""
        if not self._search_open():
            return None
        rect = self.grid.foot()[5]
        if rect[3] > 0:
            return rect
        lay = self.ws.layout
        h = lay.line_h
        return (rect[0], lay.cy + lay.ch - 4 * lay.fs - h, rect[2], h)

    def _search_close_items(self):
        """The strip's way out of search: a field's cancel while typing (the
        T-Deck's Mic, where its K types), else back."""
        return ("cancel" if getattr(self.ws, "search_typing", False) else "back", "Close")

    def _draw_search(self, cv, th, ink):
        """The search line (`_search_line`): its words, a bar caret after
        the query while it is typed, and where a strip shows, the key that
        closes it. Returns the line's right edge drawn to."""
        ws = self.ws
        line = self._search_line()
        if line is None:
            return 0
        fs = ws.layout.fs
        query = getattr(ws, "search_query", "")
        typing = getattr(ws, "search_typing", False)
        x = line[0] + self._hint_pad()
        y = line[1] + (line[3] - ws.layout.line_h) // 2
        label = _ui_draw.print_fit(self._search_label(query, typing), line[2] // 2, fs)
        cv.print(label, x, y, ink, 1)
        x += _ui_draw.print_width(label, fs)
        if typing and ws.can_type():
            # the field's caret: a unit wide, a unit over and under the ink
            x += fs
            cv.rect(x, y + fs, fs, ws.layout.line_h - 2 * fs, th["value"])
            x += fs
        if line[3] > 0 and line is self.grid.foot()[5]:
            x += 4 * fs
            sp = _ui_draw.hints(cv, th, (x, line[1], line[0] + line[2] - x, line[3]),
                                self._search_close_items(), self.grid.desk())
            if sp:
                x = sp[-1][1]
        return x

    def _search_tap(self, px, py):
        """A tap on the search line closes search, cleared (under touch its
        only way out but the system menu's Clear search)."""
        line = self._search_line()
        if line is None:
            return False
        spans, _rect = self._hint_spans(self.ws.sys_canvas)
        x1 = next((a for v, a, _b in spans if v == "tabs"), line[0] + line[2])
        if not _in(px, py, (line[0], line[1], x1 - line[0], line[3])):
            return False
        self.ws.close_search(clear=True)
        return True

    def card_verbs(self):
        """The selected card's verbs beyond its tap, the ONE list every
        input reaches them by: the system menu's card group, a long press
        on the card, a gamepad's X, the desk's buttons. Its favourite star,
        then CHANGE with COPY beside it, then DEL, where
        the active tab has them (`zone_verbs`)."""
        zone = self.zone_verbs()
        out = []
        if self.favourable() is not None:
            out.append("favourite")
        if "change" in zone:
            out.append("change")
            out.append("copy")
        for v in zone:
            if v == "delete":
                out.append(v)
        return out

    def _desk_verbs(self):
        """The desk home's buttons (step 18): the active tab's tap verb,
        then the card's verbs but its star (the hero wears that). On Make
        a real card's are its own, CHANGE first: NEW makes another cart,
        which the shelf's + tile and N keep. None off the desk."""
        if not (self._carousel() and self.grid.desk()):
            return ()
        zone = self.zone_verbs()
        out = [] if "change" in zone and self.grid.tab == "make" else \
            [v for v in zone[:1] if v in ("play", "open", "new")]
        out.extend(v for v in self.card_verbs() if v != "favourite")
        return out

    def _desk_button_top(self):
        """The y of the details pane's first row of buttons."""
        dk = self.grid._geom()[9]
        h, block, u = dk[5][4], dk[5][5], dk[5][6]
        return dk[6] + block - 6 // u - h - dk[8]

    def _desk_buttons(self):
        """[(verb, rect, kind)] of the details pane's buttons over the
        pane's foot (`desk_button_layout`): rows from the first the pane
        keeps (`_desk_button_top`), every one of the card's verbs."""
        verbs = self._desk_verbs()
        if not verbs:
            return []                        # off the desk
        dk = self.grid._geom()[9]
        dx, dw = dk[1], dk[2]
        h, u = dk[5][4], dk[5][6]
        y = self._desk_button_top()
        return [(v, (dx + x, y + r * (h + DESK_ROW_GAP // u) + dy, w, h), kind)
                for v, x, r, w, kind, dy in desk_button_layout(verbs, dw,
                                                               self.ws.layout.fs, u)]

    def _details_tap(self, px, py):
        """A press on the desk home's hero (the active tab's tap) or one of
        its buttons."""
        g = self.grid
        if not g.items:
            return False
        if _in(px, py, g.hero_rect()):
            self._tab_focus = False
            self.ws.defer(self._launch)          # #184: start behind the paint
            return True
        for v, r, _k in self._desk_buttons():
            if _in(px, py, r):
                self._do_verb(v)
                return True
        return False

    def _quiet(self, ws):
        """The home's quiet rectangles for its wallpaper: the bars and the
        arrangement's own (`quiet`); kept while both are the same objects,
        so a live wallpaper's frame builds nothing."""
        bars = ws.wallpaper.bars()
        own = self.grid.quiet()
        hit = getattr(self, "_quiet_key", None)
        if hit is not None and hit[0] is bars and hit[1] is own:
            return hit[2]
        q = bars + own
        self._quiet_key = (bars, own, q)
        return q

    def _draw_details(self, cv):
        """The desk home's details pane beside the hero (step 18): the
        selected card's title in the `title` face, its metadata, its facts
        (how often and when it was played, the play record's; who made it,
        its id's), then its buttons. On the home's ground under a theme's
        depth, on a plate in the bar's colours without one."""
        ws = self.ws
        g = self.grid
        if not g.items:
            return
        it = g.items[g.nearest()]
        th = ws.theme_colors
        dk = g._geom()[9]
        dx, dw, hy = dk[1], dk[2], dk[6]
        ts, ms, ks, vs, bh, block, u = dk[5]
        ink, dim = th["ink"], th["ink_dim"]
        name = it.get("title") or ""
        if it.get("type") == NEW_TILE_TYPE:
            name = "New"
        ty = hy + 4 // u
        was = _ui_draw.print_font(_ui_draw.font("title")[0])
        cv.print_scaled(_ui_draw.print_ellipsis(name, dw, ts), dx, ty, ink, ts)
        if was is not None:
            _ui_draw.print_font(was)
        lh = _ui_draw.font()[2]
        facts_on, meta_on = dk[7]
        my = ty + _ui_draw.font("title")[2] * ts + 14 // u
        sep = 8 // u
        x = dx
        for k, t in enumerate(g._meta(it, played=False) if meta_on else ()):
            if k:                                            # the facts' Last
                if x + ms + sep >= dx + dw:
                    break
                cv.rect(x, _dot_top(my, ms, ms), ms, ms, dim)
                x += ms + sep
            cv.print_scaled(_ui_draw.print_fit(t, dx + dw - x, ms), x, my, dim, ms)
            x += _ui_draw.print_width(t, ms) + sep
        if not meta_on:
            my -= lh * ms + 14 // u
        buttons = self._desk_buttons()
        by = self._desk_button_top()
        if it.get("type") not in PSEUDO_TILE_TYPES:
            vy = by - 16 // u - lh * vs
            ky = vy - 6 // u - lh * ks
            if not facts_on:
                ky = by - 6 // u
            # the manifest's description (SPEC.md 3.7) in the room between
            # the metadata and the facts: a measure of DESC_EMS, DESC_LINES
            # lines at most, the last cut with an ellipsis (owner, 2026-10-19)
            gap = 10 // u
            room = min(DESC_LINES, (ky - gap - (my + lh * ms + gap)) // (lh * ms))
            text = it.get("description")
            if text and room > 0:
                tw = min(dw, _ui_draw.print_width("n" * DESC_EMS, ms))
                lines = list(_ui_draw.wrap_px(text, tw, ms))
                if len(lines) > room:
                    lines = lines[:room]
                    lines[-1] = _ui_draw.print_fit(lines[-1], tw - _ui_draw.print_width("...", ms), ms) + "..."
                dy = my + lh * ms + gap
                for line in lines:
                    cv.print_scaled(line, dx, dy, dim, ms)
                    dy += lh * ms
            # the facts' columns stand on the buttons' left edges under them
            # (design review R4-31); a fact past the last button a column on
            col = 150 // u
            facts = self._facts(it) if facts_on else ()
            xs = [r[0] for _v, r, _k in buttons if r[1] == by][:len(facts)]
            while len(xs) < len(facts):
                xs.append(xs[-1] + col if xs else dx)
            for k, (key, val) in enumerate(facts):
                fx = xs[k]
                w = (xs[k + 1] - fx if k + 1 < len(xs) else col) - 10 // u
                w = min(w, dx + dw - fx)
                if w <= 0:
                    break                    # a column past the pane's edge
                cv.print_scaled(key, fx, ky, dim, ks)
                cv.print_scaled(_ui_draw.print_fit(val, w, vs), fx, vy, ink, vs)
        on = self._button_focus
        for k, (v, r, kind) in enumerate(buttons):
            _ui_draw.button(cv, th, r, _VERB_HINT[v][1], kind=kind, app="home",
                            glyph="play" if v in ("play", "open") else None,
                            glyph_draw=self._play_glyph, focus=k == on)

    def _play_glyph(self, kind, rect, c, cv):
        """A details-pane button's glyph: the theme's icon set's (the dock's,
        Files' and the Editor's Play), else the toolkit's own raster."""
        if not self.ws.draw_icon(kind, rect, c, cv):
            _ui_draw.glyph(cv, "run" if kind == "play" else kind, rect, c)

    def _facts(self, it):
        """The details pane's facts of a cart: (caption, value) pairs."""
        g = self.grid
        rec = g.played(it)
        out = [("Played", times_words(rec[0] if rec is not None else 0))]
        if rec is not None and rec[1]:
            out.append(("Last", day_words(rec[1], g.today())))
        cid = it.get("id") or ""
        if g.own_for is not None and g.own_for(it):
            by = "You"
        elif "." in cid:
            by = cid.split(".", 1)[0]
            by = "Moybyte" if by == "moybyte" else by
        else:
            by = None
        if by:
            out.append(("By", by))
        return out

    def _hint_groups(self):
        """The hint strip's two groups: the active tab's verbs on the
        selected card (on the desk, its buttons' and SEARCH), then the tab
        keys; and the verb of each pair. A keyboard types each verb's
        letter, the star last, its words following its state; a gamepad
        shows the verb its confirm does (`launch_selected`), on that key,
        and X for the card's other verbs (`card_verbs`). With the keys on a
        desk button, confirm names that button's verb."""
        items = []
        verbs = []
        zone = self.zone_verbs()
        on = self._button_focus
        if on is not None:
            # the keys on the desk's buttons: confirm presses the one they are on
            buttons = self._desk_buttons()
            on = buttons[min(on, len(buttons) - 1)][0] if buttons else None
        if _ui_draw.hint_kind() not in _LETTER_INPUTS:
            tap = on or self._confirm_verb(zone)
            if tap:
                items.append("confirm")
                items.append(_VERB_HINT[tap][1])
                verbs.append(tap)
            if self.card_verbs():
                items.extend(_VERB_HINT["options"])
                verbs.append("options")
            return items, verbs
        desk = tuple(self._desk_verbs())
        if desk and "new" in zone and "new" not in desk:
            desk = ("new",) + desk           # Make's N, off the card's buttons
        for v in (desk + tuple(v for v in zone if v == "search")) if desk else zone:
            k, words = _VERB_HINT[v]
            if on is not None and (v == on or k == "confirm"):
                if v != on:
                    continue                 # the shelf's tap: no key on the buttons
                k = "confirm"
            items.append(k)
            items.append(words)
            verbs.append(v)
        it = self.favourable()
        if it is not None:
            # the star last, where the strip has room for it (`_hint_spans`)
            items.append(_VERB_HINT["favourite"][0])
            items.append(_UNFAVOURITE if self.ws.carts.is_favorite(it)
                         else _VERB_HINT["favourite"][1])
            verbs.append("favourite")
        return items, verbs

    def _confirm_verb(self, zone):
        """Which of the verbs `zone` the selected card's confirm does: the
        "+ New" card's NEW, a Make card's CHANGE (it opens in the Editor),
        else PLAY or OPEN; "" for none."""
        if self.grid.tab == "make" and "change" in zone:
            return "change"
        return zone[0] if zone[:1] in (("play",), ("open",), ("new",)) else ""

    def _hint_spans(self, cv):
        """[(verb or "tabs", x0, x1)] across the hint strip, and the strip's
        rect: what `_draw_hints` draws and a tap hits."""
        out, rect, _items = self._hint_fit(cv)
        return out, rect

    def _hint_fit(self, cv):
        """(spans, rect, items) of the hint strip: the spans `_hint_spans`
        answers and the verbs' keycap items they hold. Where the verbs run
        into the tab keys the star goes first, then Duplicate (it stays on
        X and in the card's menu); the tab keys go last (design review R4-22)."""
        th = self.ws.theme_colors
        rect = self.grid.foot()[5]
        desk = self.grid.desk()
        items, verbs = self._hint_groups()
        items, verbs = list(items), list(verbs)
        if self.searchable and (getattr(self.ws, "search_query", "")
                                or getattr(self.ws, "search_typing", False)):
            items, verbs = [], []
        inner = self._hint_inner(rect)
        sp = _ui_draw.hints(cv, th, inner, _TAB_KEYS, desk, draw=False)
        dx = rect[0] + rect[2] - self._hint_pad() - sp[0][1] if sp else 0
        while True:
            out = []
            if items:
                for v, (x0, x1) in zip(verbs, _ui_draw.hints(cv, th, inner, items, desk,
                                                              draw=False)):
                    out.append((v, x0, x1))
            if not sp or not out or sp[0][0] + dx > out[-1][2]:
                break
            drop = "favourite" if verbs[-1] == "favourite" else \
                "copy" if "copy" in verbs else None
            if drop is None:
                break
            k = verbs.index(drop)
            del verbs[k]
            del items[2 * k:2 * k + 2]
        if sp and (not out or sp[0][0] + dx > out[-1][2]):
            out.append(("tabs", sp[0][0] + dx, sp[0][1] + dx))
        return out, rect, items

    def _draw_hints(self, cv):
        """The home's hint strip (steps 17 and 18): keycaps naming the
        active tab's verbs on the selected card, from the left, and the tab
        keys at the right (the theme's `keycap`, `ui.hints`'s part). While
        a search query is held it shows in the verbs' place; while the
        system menu or the delete confirm is up, their keys (`modal_keys`)."""
        th = self.ws.theme_colors
        wall = self._wall()
        if wall is not None:
            th = self._wall_theme(th, wall[0])
        keys = modal_keys(self.ws)
        if keys is None and self._tab_keys:
            keys = _TAB_ROW_KEYS
        if keys is not None:
            # the system menu's card or the delete confirm is up over the
            # home: the keys it takes, never the home's verbs
            rect = self.grid.foot()[5]
            _ui_draw.hints(cv, th, self._hint_inner(rect), keys, self.grid.desk())
            return
        spans, rect, items = self._hint_fit(cv)
        n = sum(1 for v, _a, _b in spans if v != "tabs")
        query = getattr(self.ws, "search_query", "")
        typing = getattr(self.ws, "search_typing", False)
        if self.searchable and (query or typing):
            n = 0
        desk = self.grid.desk()
        if n:
            _ui_draw.hints(cv, th, self._hint_inner(rect), items[:2 * n], desk)
        elif self.searchable and (query or typing):
            self._draw_search(cv, th, th["ink"])
        for v, x0, _x1 in spans:
            if v == "tabs":
                left = x0 - (rect[0] + self._hint_lead())
                _ui_draw.hints(cv, th, (rect[0] + left, rect[1], rect[2] - left, rect[3]),
                               _TAB_KEYS, desk)

    def _wall_theme(self, th, white):
        """`th` with its hint labels' roles white, for the strip over a
        wallpaper: one dict per theme load, so the toolkit's look ring keeps
        its slot."""
        k = getattr(self, "_wall_th", None)
        if k is not None and k[0] is th and k[1] == white:
            return k[2]
        wt = dict(th)
        wt["chrome_ink_dim"] = wt["ink_dim"] = white
        self._wall_th = (th, white, wt)
        return wt

    def _hint_lead(self):
        """The margin `ui.hints` leads with here, at the font scale."""
        return (_HINT_LEAD_DESK if self.grid.desk() else _HINT_LEAD) * self.ws.layout.fs

    def _hint_pad(self):
        """The hint strip's margin at either end: the handheld's is the one
        `ui.hints` leads with, the top bar's, the desk's wider."""
        if self.grid.desk():
            return max(24 // _lb.render_scale(), self._hint_lead())
        return self._hint_lead()

    def _hint_inner(self, rect):
        """The strip `ui.hints` draws in for the margin `_hint_pad`."""
        e = self._hint_pad() - self._hint_lead()
        return (rect[0] + e, rect[1], rect[2] - 2 * e, rect[3])

    def _hint_tap(self, px, py):
        """A tap on the hint strip: its verb, as the bar's chip was."""
        spans, rect = self._hint_spans(self.ws.sys_canvas)
        if not _in(px, py, rect):
            return False
        for v, x0, x1 in spans:
            if x0 <= px < x1:
                if v == "tabs":
                    self.ws.step_home_tab(1, self.grid)
                else:
                    self._do_verb(v)
                return True
        return True

    def _hint_keys(self, i):
        """The hint strip's keys, typed (an edge: a held key acts once):
        C, /, N, Y and X the verbs it names, Q and E the tabs, F the
        selected card's favourite star."""
        k = self.ws.input.last_key
        prev = self._kprev
        self._kprev = k
        if not k or k == prev or not (32 < k < 127):
            return False
        ch = chr(k).lower()
        if ch in ("q", "e"):
            self.ws.step_home_tab(-1 if ch == "q" else 1, self.grid)
            if self._tab_keys:
                self._tab_at = self.grid.tab
                self._zone_bump += 1
            return True
        if ch == _FAVOURITE_KEY:
            return self.toggle_favourite()
        v = _VERB_KEY.get(ch)
        if v is None or not (v in self.zone_verbs() or v in self.card_verbs()):
            return False
        self._do_verb(v)
        return True

    def _handle_search_typing(self, i):
        """Keyboard while a search query is being TYPED (ws.search_typing, entered
        via the sysmenu SEARCH item) -- mirrors settings_layer._wifi_input's
        password-typing branch: typed ASCII bytes edit the query live (plain
        printable range only -- no `=[]{}<>%` needed, so it works on the T-Deck's
        keyboard), BACKSPACE deletes, ENTER leaves typing (keeping the filtered
        results up for d-pad/trackball browsing), ESC leaves AND clears back to
        the full grid. Grid nav is intentionally NOT handled here (mirrors the
        wifi panel: while typing, the d-pad doesn't drive anything else)."""
        ws = self.ws
        k = ws.input.last_key
        if i.pressed("b"):                          # a gamepad's B: the strip's Close
            ws.close_search(clear=True)
        elif k and k != self._search_kprev:
            if k in (10, 13):                       # ENTER -> stop typing, keep results
                ws.close_search(clear=False)
            elif k == 27:                           # ESC -> cancel back to the full grid
                ws.close_search(clear=True)
            elif k == 8:                            # BACKSPACE -> delete
                ws.set_search_query(ws.search_query[:-1])
            elif 32 <= k <= 126 and len(ws.search_query) < 24:
                ws.set_search_query(ws.search_query + chr(k))
        self._search_kprev = k
        return True

    def handle_input(self, i):
        ws = self.ws
        if getattr(ws, "search_typing", False):
            return self._handle_search_typing(i)
        ws.ach_ui.konami_watch(i)
        if self._tab_focus:
            # The tab row (up from the carousel): left and right move the
            # focus along it (`_tab_step`), confirm opens the tab it is on
            # and takes the keys to its cards, down returns to them.
            if i.pressed("left"):
                self._tab_step(-1)
            elif i.pressed("right"):
                self._tab_step(1)
            if i.pressed("a") or i.pressed("run"):
                at = self.focused_tab()
                self._tab_focus = False
                if at != self.grid.tab:
                    ws.set_home_tab(at, self.grid)
                ws.mark_dirty()
            elif i.pressed("down"):
                self._tab_focus = False
                ws.mark_dirty()
            if i.pressed("b") and ws.ach_ui._konami_pos == 0:
                self._tab_focus = False
                ws.mark_dirty()
            elif i.pressed("home"):
                ws.home_key()
            return True
        if self._button_keys(i):
            return True
        if self._hint_keys(i):
            return True
        # The carousel (§12): left/right glide by a card, a held key one
        # motion; up takes the keys to the tab row (on the desk, to the
        # details pane's buttons first). The grid: the arrows step its
        # cells, and up from its top row takes them to the tabs.
        grid = self.grid
        wy, wx = getattr(i, "wheel_y", 0), getattr(i, "wheel_x", 0)
        if wy or wx:
            # The wheel: the carousel glides a card a notch, the grid steps a
            # row, Shift's wheel a column.
            if getattr(i, "mods", 0) & _SHIFT:
                wx, wy = wx + wy, 0
            if self._carousel():
                grid.nav2d(wx + wy, 0)
            else:
                grid.nav2d(wx, wy)
            ws.mark_dirty()
        if i.pressed("left"):
            grid.nav2d(-1, 0)
            ws.mark_dirty()
        if i.pressed("right"):
            grid.nav2d(1, 0)
            ws.mark_dirty()
        if i.pressed("up"):
            if self._desk_buttons():
                self._button_focus = 0
            elif self._carousel() or self._grid_top_row():
                self._tab_focus = True
            else:
                grid.nav2d(0, -1)
            ws.mark_dirty()
        if i.pressed("down") and not self._carousel():
            grid.nav2d(0, 1)
            ws.mark_dirty()
        if i.pressed("a") or i.pressed("run"):
            # #184: DEFERRED -- the cart start runs behind the next painted
            # frame (LOADING toast), never inside this input dispatch.
            ws.defer(self._launch)           # the active tab's tap
        if i.pressed("x"):
            self.open_card_menu()            # a gamepad's X: the card's verbs
        if i.pressed("b") and ws.ach_ui._konami_pos == 0:
            self._back()                     # (a B inside the Konami code is the code's)
        elif i.pressed("home"):
            ws.home_key()
        return True

    def favourable(self):
        """The selected card where it takes the favourite star (a cart of
        the store's on a home that keeps favourites), else None."""
        grid = self.grid
        it = grid.selected()
        if grid.favorite_for is None or it is None or not it.get("path") \
                or it.get("type") in PSEUDO_TILE_TYPES:
            return None
        return it

    def toggle_favourite(self):
        """Star or unstar the selected card (the badge's tap, F, the card
        menu's row); False where it takes no star."""
        it = self.favourable()
        if it is None:
            return False
        self.ws.carts.toggle_favorite(it)
        return True

    def _back(self):
        """B: closes a held search, cleared; else the home's X, from a
        keyboard -- where the X shows."""
        if self._search_open():
            self.ws.close_search(clear=True)
        elif self.ws.bar_layer._show_x(self.strip):
            self.ws.open_desk()

    # The doors out of the home.
    def _launch(self):
        self.ws.launch_selected(self.grid)

    def _change(self):
        self.ws.change_selected(self.grid)

    def _new(self):
        self.ws.new_cart_and_edit(self.grid)

    def _grid_top_row(self):
        grid = self.grid
        if not grid.items:
            return True
        return grid.cell_of(grid.sel)[0] == 0

    def _grid_pointer(self, px, py, click):
        """The grid and icons arrangements' pointer: a card fires the active
        tab's tap on a clean RELEASE (ui.DragTap), so a drag never launches;
        a mouse's cursor selects the card it moves onto."""
        ws = self.ws
        grid = self.grid
        down = ws.pointer.down
        i = grid.pointer_frame(px, py, click, down, dt_ms=ws._pointer_dt_ms)
        if grid.moving:
            ws.mark_dirty()
        if i is not None:
            grid.sel = i
            ws.defer(self._launch)           # #184: start behind the paint
            return True
        if click or down:
            self._lhover = (px, py)
            return True
        if self._lhover == (-1, -1):
            self._lhover = (px, py)
        elif (px, py) != self._lhover:
            self._lhover = (px, py)
            i = grid.tile_at(px, py)
            if i is not None and i != grid.sel:
                grid.sel = i
                # A selection move MARKS DIRTY (#177): in a window the content
                # freeze reuses the retained buffer on position-only frames.
                ws._dirty = True
        return True

    def handle_pointer(self, px, py, click):
        # The home (§12): the bar and the tab row fire on the press edge; the
        # carousel rides ui.DragTap, so a tap fires on a clean RELEASE and a
        # drag can never launch a cart. A tap on the centre card is the
        # active tab's tap; on any other card it glides that card to the
        # centre. The favorite star on the centre card fires on the press.
        ws = self.ws
        down = ws.pointer.down
        grid = self.grid
        tip = self._hover_back(px, py, click, down)
        if tip != self._back_tip:
            self._back_tip = tip
            ws.mark_dirty()
        if click:
            self._hold = None
            if self._button_focus is not None:
                self._button_focus = None    # a press takes the keys' mark away
            if ws.bar_layer.handle_bar_tap(self.strip, px, py):
                return True
            if self._search_tap(px, py) or self._hint_tap(px, py):
                return True
            for tid, r, _on in self.tab_rects():
                if _in(px, py, r):
                    self._tab_focus = False
                    ws.set_home_tab(tid, grid)
                    return True
            frect = grid.favorite_rect(grid.sel)
            if frect is not None and _in(px, py, frect):
                self.toggle_favourite()
                return True
            if self._carousel() and grid.desk() and self._details_tap(px, py):
                return True
        if self._long_press(px, py, click, down):
            return True
        if not self._carousel():
            return self._grid_pointer(px, py, click)
        i = grid.pointer_frame(px, py, click, down, dt_ms=ws._pointer_dt_ms)
        if grid.moving:
            ws.mark_dirty()
        if i is not None:
            self._tab_focus = False
            if i == grid.sel and not grid.moving:
                # #184: the tap frame paints first; the start runs behind it.
                ws.defer(self._launch)
            else:
                grid.glide_to(i)
                ws.mark_dirty()
            return True
        if click or down:
            self._lhover = (px, py)
        return True

    def _long_press(self, px, py, click, down):
        """A press held `LONG_PRESS_MS` on a card without travel selects
        that card and opens its verbs (`open_card_menu`), the phone home
        screens' long press; the gesture ends there, so its release is no
        tap. True the frame it opens."""
        grid = self.grid
        if click:
            i = grid.tile_at(px, py)
            self._hold = None if i is None else [i, px, py, 0.0]
            return False
        h = self._hold
        if h is None:
            return False
        slop = 4 * self.ws.layout.fs + 2              # the DragTap's own
        if not down or grid.dragging or abs(px - h[1]) > slop or abs(py - h[2]) > slop:
            self._hold = None
            return False
        h[3] += self.ws._frame_dt_ms
        if h[3] < LONG_PRESS_MS:
            return False
        self._hold = None
        grid.pointer_frame(px, py, False, False)      # the gesture ends untapped
        if h[0] != grid.sel and 0 <= h[0] < len(grid.items):
            grid.sel = h[0]
        self._tab_focus = False
        self.open_card_menu((h[1], h[2]))
        self.ws.mark_dirty()
        return True

    def open_card_menu(self, at=None):
        """The selected card's verbs (`card_verbs`) as a menu hung at `at`,
        (x, y) on the system canvas -- a long press's point, else the
        card's middle (the desk's hero's): a long press, a gamepad's X, the
        strip's Options. False where the card has none."""
        if not self.card_verbs():
            return False
        if at is None:
            grid = self.grid
            r = grid.hero_rect() if self._carousel() and grid.desk() else None
            r = r or grid.tile_rect(grid.sel)
            if r is None:
                c = self.ws.layout
                at = (c.cx + c.cw // 2, c.cy + c.ch // 2)
            else:
                at = (r[0] + r[2] // 2, r[1] + r[3] // 2)
        return self.ws.menu_ui.open_card(self, at)

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    @property
    def zone_gen(self):
        """What the zone's pixels follow, as one int for the bar's strip key:
        the open tab and the tab row's focus (`_zone_bump`). The zone draws
        the tabs alone, so a selection moving along the row (a held key's
        glide) leaves the bar's strip as it is."""
        return self._zone_bump * 8 + _TAB_ORDER.get(self.grid.tab, 7)

    def zone_verbs(self):
        """The active tab's verbs on the selected card (§12's table): Games
        PLAY, CHANGE, SEARCH; Mine PLAY, CHANGE; Apps OPEN; Make NEW, CHANGE,
        COPY, DEL. CHANGE, COPY and DEL need a real cart and a store that
        manages projects, and DEL one the kid's (a built-in is copied, never
        deleted: `builtin_name`, the open-cart menu's own test); the "+ New"
        card's verb is NEW. SEARCH is the
        fullscreen home's alone."""
        ws = self.ws
        grid = self.grid
        tab = grid.tab
        sel = grid.selected()
        real = sel is not None and sel.get("path") \
            and sel.get("type") not in PSEUDO_TILE_TYPES
        change = bool(real) and bool(getattr(ws, "can_manage", True))
        if tab == "apps":
            return ("open",) if real else ()
        if tab == "make":
            if not getattr(ws, "can_manage", True):
                return ()                        # a read-only store makes nothing
            if not change:
                return ("new",)
            if _builtin_name(sel["path"]) is not None:
                return ("new", "change", "copy")
            return ("new", "change", "copy", "delete")
        if sel is not None and sel.get("type") == NEW_TILE_TYPE:
            return ("new",)
        out = ("play", "change") if change else (("play",) if real else ())
        if tab == "games" and self.searchable:
            out = out + ("search",)
        return out

    def zone_tabs(self, rect):
        """[(tab id, label, rect)] of the home's tabs in the bar's zone
        `rect` (steps 17 and 18), flush left, as many as fit. A plate may
        reach a chrome unit past the zone above and below, so the label's
        line and a mark under it both fit (B-15)."""
        cs = self.ws.layout.cs
        sc, h, pad, gap = self._zone_tab_metrics()
        h = min(rect[3] + 2 * cs, h)
        y = rect[1] + (rect[3] - h) // 2
        x = rect[0] + 2 * cs
        out = []
        for tid, label in HOME_TABS:
            w = _ui_draw.print_width(label, sc) + 2 * pad
            if x + w > rect[0] + rect[2]:
                break
            out.append((tid, label, (x, y, w, h)))
            x += w + gap
        return out

    def _zone_tab_metrics(self):
        """(label scale, plate height, label pad, gap) of the tabs in the bar:
        the handheld's at the font scale on a plate a unit taller than the
        line either side (the targets'), the desk's a line about 15 glass
        pixels tall on a 28-pixel plate."""
        lay = self.ws.layout
        fs = lay.fs
        if self.grid.desk():
            u = _lb.render_scale()
            sc = text_scale(15 // u, fs)
            return (sc, max(28 // u, _ui_draw.font()[2] * sc + 6 * fs),
                    max(12 // u, 4 * fs), max(6 // u, 2 * fs))
        return fs, lay.line_h + 2 * fs, 6 * fs, 2 * fs

    def _draw_zone_tabs(self, cv, rect):
        """The tabs in the bar (step 17): the active one under the current
        mark, the rest in the dim
        chrome ink; while the keys are the tab row's, the focus pill on the
        one they are on (`focused_tab`), its plate under the label and its
        ring round the plate, so the focused tab and the open one read
        apart."""
        ws = self.ws
        th = ws.theme_colors
        fs = ws.layout.fs
        sc, _h, pad, _gap = self._zone_tab_metrics()
        at = self.focused_tab()
        u = _lb.unit()
        for tid, label, r in self.zone_tabs(rect):
            on = tid == self.grid.tab
            ink = th["chrome_ink_dim"]
            if on:
                got = _ui_draw.current(cv, th, r, r=5 * fs, fs=fs)
                ink = th["chrome_ink"] if got is None else got
            if tid == at:
                _ui_draw.focus(cv, th, r, r=5 * fs, fs=u, pill=True)
                if not on:
                    ink = th["chrome_ink"]
            if sc == fs:
                # the line centred on the plate, as the bar's clock is on the bar,
                # so the two share a baseline
                cv.print(label, r[0] + pad, r[1] + (r[3] - ws.layout.line_h) // 2, ink, 1)
            else:
                lh = _ui_draw.font()[2] * sc
                cv.print_scaled(label, r[0] + pad, r[1] + (r[3] - lh) // 2, ink, sc)
            if tid == at:
                _ui_draw.focus(cv, th, r, r=5 * fs, fs=u, after=True, pill=True)

    def _zone_action_rects(self, rect):
        """{verb: rect} of the hint strip's pairs, where the verbs are
        (`rect`, the zone, unread)."""
        spans, hr = self._hint_spans(self.ws.sys_canvas)
        return {v: (x0, hr[1], x1 - x0, hr[3]) for v, x0, x1 in spans if v != "tabs"}

    def draw_zone(self, cv, rect):
        """The home's lent left zone: the tabs."""
        return self._draw_zone_tabs(cv, rect)

    def zone_tap(self, px, py, rect=None):
        """A tab in the zone, else a verb's hint; anything else falls
        through."""
        ws = self.ws
        for tid, _label, r in self.zone_tabs(
                rect if rect is not None else ws.layout.zone_left):
            if _in(px, py, r):
                self._tab_focus = False
                ws.set_home_tab(tid, self.grid)
                return True
        chips = self._zone_action_rects(
            rect if rect is not None else ws.layout.zone_left)
        for verb, r in chips.items():
            if _in(px, py, r):
                self._do_verb(verb)
                return True
        return False

    def _do_verb(self, verb):
        """One of the active tab's verbs on the selected card (§12's table),
        from a zone chip, a hint or its key."""
        ws = self.ws
        if verb in ("play", "open"):
            ws.defer(self._launch)   # #184: start behind the paint
        elif verb == "change":
            ws.defer(self._change)
        elif verb == "new":
            ws.defer(self._new)
        elif verb == "copy":
            ws.carts.dup(self.grid.selected())
        elif verb == "delete":
            ws.confirm.ask(self.grid.selected())   # never on one press: it asks
        elif verb == "favourite":
            self.toggle_favourite()
        elif verb == "options":
            self.open_card_menu()
        else:
            ws.toggle_search()
        ws.mark_dirty()


class HomeWindowLayer(LauncherHomeLayer):
    """The "projects" content Layer: the home in a desktop-mode window, the
    desk's PROJECTS (docs/shell_layout_2026-10.md §12). The same tabs,
    sources, taps and verbs over its own card model, ws.projects_grid, a
    `Carousel` whatever the fullscreen home's arrangement, opened on Make:
    the console home's design (step 18) laid out in the window's content
    rect -- the hero, its details and buttons, the shelf under them, the
    hint strip along the foot and the tabs in the window's band. A narrow
    window stands the hero over its details (`Carousel._geom_desk`).

    A window is a tool space: the panel field under the cards, never the
    wallpaper (the desk shows its own around the window), so nothing in it
    animates and an idle window draws nothing. The WM retains the window's
    buffer, so it keeps no retained frame of its own; a drag rides the
    shared `_drag_partial` and the WM's content-gesture path."""

    id = "projects"
    strip = "projects"
    searchable = False

    @property
    def grid(self):
        return self.ws.projects_grid

    def _back(self):
        """B closes this window alone, as its title strip's X does."""
        self.ws.wm.close_window_kind(self.id)

    def _wall(self):
        return None                           # the window lies on its panel

    def _hover_back(self, px, py, click, down):
        return False                          # the window's X is the way out

    def prealloc_retained(self):
        return

    def release_retained(self):
        return False

    def _panel_fill(self, cv, r):
        """The band's backdrop: the window's panel."""
        cv.rect(r[0], r[1], r[2], r[3], self.ws.theme_colors["panel"])

    def _try_drag_partial(self, cv, dt):
        return _drag_partial(self, cv, dt, self.grid, self.id, self._panel_fill,
                             self.strip)

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        grid = self.grid
        _paint_continuity(self, ws)           # foreign paints void the buffers
        th = ws.theme_colors
        grid.motion = getattr(ws.wm, "HOME_MOTION", "full")
        grid.scale = getattr(ws.wm, "HOME_SCALE", HOME_SCALE_HH)
        dim = _dim_of(ws)                     # the neighbours dim toward the panel
        grid.dim = None if dim is None else (th["panel"], dim[1])
        grid.wall_ink = None
        grid.anim_frame(dt)
        if grid.flinging:
            ws.request_frame()
        if self._try_drag_partial(cv, dt):    # drag-scroll fast path (#113)
            return
        cv.cls(th["panel"])
        self._draw_home_chrome(cv)
        grid.draw(cv, ws.covers.sheet_icon)
        if grid.desk():
            self._draw_details(cv)
        grid._scroll_region().note_painted(
            ws._frames_drawn,
            (grid.paint_key(), self._statics_key(cv), ws.covers.gen),
            _cursor_stamp(ws))
        ws.bar_layer._draw_status_strip(self.strip)
        _advance_streak(self, cv)
