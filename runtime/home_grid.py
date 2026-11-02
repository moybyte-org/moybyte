# Map (grep -n a name to jump there):
#   new_tile                    the "+ New" card
#   draw_new_art                the "+ New" card's art
#   _draw_star_badge            a favourite's star on its card
#   _hint_strip                 the hint strip's rect on a glass
#   _Cells                      a grid arrangement's geometry on a glass
#   Launcher                    the grid arrangement: a shelf of cards that scrolls sideways
#   Launcher._lay_cells         its geometry on a glass (_Cells)
#   Launcher.nav2d              cell navigation
#   Launcher.pointer_frame      one pointer sample over the cells
#   Launcher.cover_specs        every cover the next full draw needs
#   Launcher.band_pass          the band as a table of parts, on its own app host
#   HOME_TABS                   the home's tabs and their words
#   day_words                   a date as the home says it
#   desk_button_layout          the desk pane's rows of verb buttons
"""The home's GRID arrangement and the cards every arrangement draws.

`Launcher` is the selection model (items, sel, scroll, nav) every home
arrangement shares, and the `grid` arrangement's draw
(docs/history/look_2026-10.md step 33): a shelf of cover cards in the rows
the glass holds, scrolling sideways, each band an engine table of parts
that keeps its parts (`band_pass`). Its instance is ws.launcher in the
home's `grid` arrangement; `Carousel` (home_carousel.py) and `IconHome`
(home_icons.py) are its subclasses.

Beside it, what the arrangements and the home layers share: the "+ New"
pseudo card and its art, the favourite's star, the cover decodes and their
clean scales, the hint strip's rect, the home's tabs and their nouns, the
words a card's details say (`day_words`, `text_scale`), and the desk pane's
rows of verb buttons (`desk_button_layout`, `desk_button_span`). A leaf: it
imports no home module.
"""

from array import array

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import cart_listing as _cl
    import iconsets as _iconsets
    import layout_base as _lb
    from chrome import _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import cart_listing as _cl
    from runtime import iconsets as _iconsets
    from runtime import layout_base as _lb
    from runtime.chrome import _ticks_ms, _ticks_diff
_in = _ui.rect_in   # one hit-test (ui.rect_in)




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

# The band's table of parts (`Launcher.band_pass`): its one column, a part;
# a row's one cell; the part's cell `table_part` answers.
_PART_COL = ("",)
_ROW_CELL = ("",)
_PART = array("i", [0] * 4)
_FLOOR = array("i", [0] * 4)            # a floor rect `table_floor` answers
_FACE_GEN = [0]                         # the faces' keys (`ui.slots`), one per layer and look
_FACE_SLOTS = 128                       # the slots the engine keeps at most (`ui.slots`)
_FACES_BYTES = 192 << 10                # the faces' layer at most: the slots a board's cards fit
_NO_KEYS = array("i", [-1, -1])         # a band paint that keeps nothing (`band_pass`)


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


# The grid arrangement's shelf (docs/history/look_2026-10.md step 33): on the
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
    """The home's grid arrangement (docs/history/look_2026-10.md step 33): a shelf
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
        self.cover_ready = None       # optional (cart, div) -> the decoded cover, None
                                      # for none, False not yet (CoverCache.ready_for)
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
        self.restore_whole = False    # a band's backdrop goes back in one copy (the home's)
        self.face_key = None          # (look gen, plain ground or None): the cards'
                                      # faces are kept (`_faces_for`); None: drawn
        self.frame_no = 0             # the painted frame a paint is (the home's)
        self._face_lay = None         # the faces' layer: square slots stacked down
        self._face_geo = None         # ...the canvas, card side, margin and slots it is for
        self._face_fill = None        # ...each slot's title tile (block, letter's rects)
        self._face_of = None          # the face_key the engine's key (`_fkey`) is of
        self._fkey = 0
        self._fdiv = 0                # the cover decode, corners and ring a paint's
        self._fstepped = False        # faces are drawn with (`_faces_for`)
        self._fring = None
        self._faces_off = False       # a canvas whose layers hold no pixels
        self._faces_done = False      # a round of `mint_faces` found every face drawn
        self._c = None                # the cells laid out, and what for
        self._c_key = None
        self._show = None             # the cells the band shows (`_shown`), and what for
        self._show_key = None
        self._host = None             # the band's app host (`band_pass`)
        self._rows = []               # its table's rows
        self._krows = []              # ...as their cards' keys, and the list they are of
        self._krows_of = None
        self._kcols = ()              # ...and their columns, one a card across
        self.floor = []               # the rects the band's frame draws (`band_floor`)
        self.copied = []              # ...and those it copied out of the last frame's buffer
        self._bo = {"id": "band", "part": [0, 0], "axis": "x", "snap": False, "offset": 0,
                    "spill": 0, "gen": 0, "current": False, "mark": False, "shift": False,
                    "fade": 0}
        self.flat = False             # the band's ground is one colour (a band frame may shift)
        self.shifted = []             # the view a band frame moved out of the last frame's buffer
        self._scv = None              # the canvas `_shift` and `_fence` are bound to
        self._shift = None
        self._fence = None
        self._shows = bytearray()     # the cards the engine says show (`Carousel`)
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
        (docs/theme_and_shell_2026-11.md §3.9): the cells' band and the hint strip."""
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
        """(index, card rect) of every cell the band shows, whole or cut, in
        index order: the cells of the columns (or rows) the scroll brings into
        the band, never a walk of every item. Laid out once per scroll."""
        return self._shown()[0]

    def _shown(self):
        """([(index, card rect)], [the reach rect of each], [the rect each
        draws in without the focus]) of the cells the band shows
        (`_visible`), kept for the cells, the scroll, the list
        and the focus mark's reach they were laid out at."""
        c = self._cells()
        s = self.scroll
        items = self.items
        n = len(items)
        mr = _ui_draw.mark_reach("card")
        k = self._show_key
        if (k is not None and k[0] is c and k[1] == s and k[2] is items and k[3] == n
                and k[4] == mr):
            return self._show
        vis, reach, body = [], [], []
        self._show_key = (c, s, items, n, mr)
        self._show = (vis, reach, body)
        if not n:
            return self._show
        b = c.band
        bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
        r = c.grow + mr
        side = c.side
        if self.horizontal:
            lo = bx0 + s - c.x0 - side - r
            hi = bx1 + s - c.x0 + r
            per, span = c.rows, c.px
        else:
            lo = by0 + s - c.y0 - side - r
            hi = by1 + s - c.y0 + r
            per, span = c.cols, c.py
        m = r + 2
        e = self.layout.fs + 1                   # an unfocused card's edge
        th = _ui_draw.font()[2] * self.layout.fs + 2
        tw = c.title_w
        horizontal = self.horizontal
        for i in range(max(0, lo // span) * per, min(n, (hi // span + 1) * per)):
            if horizontal:                        # (cell_of, of a shelf and of the icons)
                x, y = c.x0 + (i // per) * c.px - s, c.y0 + (i % per) * c.py
            else:
                x, y = c.x0 + (i % per) * c.px, c.y0 + (i // per) * c.py - s
            if (x + side + r <= bx0 or x - r >= bx1
                    or y + side + r <= by0 or y - r >= by1):
                continue
            vis.append((i, (x, y, side, side)))
            for g in (m, e):
                x0, y0, x1, y1 = x - g, y - g, x + side + g, y + side + g
                if c.title_dy:
                    tx = x + side // 2 - tw // 2 - 2
                    if tx < x0:
                        x0 = tx
                    if tx + tw + 4 > x1:
                        x1 = tx + tw + 4
                    if y + c.title_dy + th > y1:
                        y1 = y + c.title_dy + th
                (reach if g == m else body).append((x0, y0, x1 - x0, y1 - y0))
        return self._show


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

    def band_key(self):
        """What the band's every card is a function of beside the look, the
        covers and its row's cards (`band_pass`'s key): nothing; the
        selection's card is its row's gen."""
        return None

    def cover_specs(self):
        """Every (cart, div) cover the next full draw requests: each card's
        whole cover at its clean side's decode."""
        div = _cover_div_for(self._cells().side)
        return [(it, div) for it in self.items
                if it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES]

    # -- the draw -------------------------------------------------------------

    def draw(self, cv, sheet_for=None, key=_NO_KEYS, over=None, beside=None):
        """The band's cells over the backdrop the home drew (a full paint's):
        the band's table drawn whole (`band_pass`); the floor's end."""
        return self.band_pass(cv, sheet_for, None, key, True, over, beside)

    def repaint_band(self, cv, sheet_for, fill, key=_NO_KEYS, over=None, beside=None, need=False):
        """A band frame: the band's table (`band_pass`), the ground put back
        by `fill(cv, rect)` under what its floor draws."""
        return self.band_pass(cv, sheet_for, fill, key, False, over, beside, need)

    def _band_host(self):
        """The band's app host (`ui_draw.AppHost`): the context its table
        is drawn in, made once."""
        h = self._host
        if h is None:
            h = self._host = _ui_draw.AppHost()
        return h

    def _band_rows(self, n):
        """The band table's rows, `n` of them (the shelf's columns, the
        icons' rows), each one cell: kept while `n` is."""
        rows = self._rows
        if len(rows) != n:
            rows = self._rows = [_ROW_CELL] * n
        return rows

    def _band_keys(self, n, per):
        """The band table's rows as their cards' keys, `n` rows of `per`
        cards across: each cell names what its card is drawn from (the
        item, its title, its kind), so a card that changed draws again.
        Made again only for another list or another `per`; the columns
        (`per` empty headers) with them."""
        items = self.items
        rows = self._krows
        if self._krows_of is items and len(rows) == n and len(self._kcols) == per:
            return rows
        rows = []
        for i in range(n):
            row = []
            for k in range(i * per, (i + 1) * per):
                if k < len(items):
                    it = items[k]
                    row.append("%s\x1f%s\x1f%s" % (it.get("path"), it.get("title"),
                                                    it.get("type")))
                else:
                    row.append("")
            rows.append(tuple(row))
        rows = tuple(rows)              # (the table's memo knows it by identity)
        self._krows = rows
        self._krows_of = items
        self._kcols = ("",) * per
        return rows

    def begin_band_host(self, cv):
        """Begin this painted frame's paint on the band's host, drawing
        nothing: the parts the home names there (`ui.held`) are then
        asked for, the band's pass beginning it again."""
        host = self._band_host()
        host.whole()
        host.begin(cv, self.theme, self.band_rect(), self.desk(), False, True)

    def resume_band_host(self, cv):
        """Point the verbs at the band's host again, beginning nothing:
        the parts named in the paint this frame began there."""
        self._band_host().resume(cv, self.theme)

    def _begin_band(self, cv, b, desk, key, need, over=None):
        """Begin the band's frame on its host: whether the buffer this frame
        paints holds the band as a frame drew it under `key` (`ui.held`);
        None, with the frame ended, where `need` asks for that and it does
        not."""
        host = self._band_host()
        host.whole()
        host.begin(cv, self.theme, b, desk, False, True)   # its keys are the home's
        # (a stamp drawn over the frame round the band: no later frame of
        # the band's puts that buffer's frame back)
        held = _ui.held("band", key[0] if over is None or (
            b[0] <= over[0] and over[0] + over[2] <= b[0] + b[2]
            and b[1] <= over[1] and over[1] + over[3] <= b[1] + b[3]) else -1)
        if need and not held:
            _ui.end()
            return None
        return held

    def band_floor(self, cv, fill, copy=True):
        """The floor of the band's table (`ui.table_floor`): each rect the
        frame draws, kept in `floor`, its ground put back by `fill` where
        given; then the floor's end. With `copy`, first the rects the
        buffer the last frame painted holds as this frame draws them
        (`ui.table_copy`), copied out of it (`copy_last`) into `copied`:
        the double buffer's catch-up of a step costs a copy, not its
        cards."""
        fl = self.floor
        del fl[:]
        cp = self.copied
        del cp[:]
        r = _FLOOR
        cl = getattr(cv, "copy_last", None) if copy else None
        if cl is not None:
            while _ui.table_copy(r) > 0:
                if not cl(r[0], r[1], r[2], r[3]):
                    _ui.table_copy(r, True)  # (drawn: the floor answers it)
                    break
                cp.append((r[0], r[1], r[2], r[3]))
        v = _ui.table_floor(r)
        while v > 0:
            q = (r[0], r[1], r[2], r[3])
            fl.append(q)
            if fill is not None:
                fill(cv, q)
            v = _ui.table_floor(r)
        return v

    def _in_floor(self, x0, y0, x1, y1):
        """Rect (x0, y0)-(x1, y1) cut to the floor rect it meets (a card's
        reach meets one at most), or None."""
        for q in self.floor:
            a = x0 if x0 > q[0] else q[0]
            b = y0 if y0 > q[1] else q[1]
            c = x1 if x1 < q[0] + q[2] else q[0] + q[2]
            d = y1 if y1 < q[1] + q[3] else q[1] + q[3]
            if c > a and d > b:
                return (a, b, c - a, d - b)
        return None

    def band_pass(self, cv, sheet_for, fill, key=_NO_KEYS, whole=True, over=None, beside=None,
                  need=False, note=False):
        """The band as a table of parts that keeps its parts (`ui.table`
        with `o.part` and `o.keep`, its rows the shelf's columns side by
        side, or the icons' rows, each row's cell its cards' keys): on the
        band's own app host, the scroll the home's (`o.offset`), no mark of
        the table's, `key` the frame round the band's key (`ui.held`) and
        what every card is a function of beside its row (`o.keep`,
        `_band_key`), `over` the cursor's stamp. The engine answers the floor
        the buffer this frame paints lacks (`band_floor`: the ground put
        back by `fill` under it; None: a full paint drew it, and `whole`
        draws the band whole) and the parts that meet it, each clipped to
        its floor rect, a column's (a row's) cards drawn in it; the focused
        card after them all, its growth and ring over its neighbours'.
        `beside(cv, whole)` draws the parts outside the band, before the
        floor's end is read. `note`: the home put the band there whole by
        other means (the retained frame's stamp), so nothing draws and the
        record says the buffer holds it. Answers the floor's end, or None
        where `need` and the buffer does not hold the band under `key`."""
        c = self._cells()
        b = c.band
        held = self._begin_band(cv, b, c.desk, key, need, over)
        if held is None:
            return None
        _ui.at(b[0], b[1], b[2], b[3])
        n = len(self.items)
        per = c.rows if self.horizontal else c.cols
        o = self._bo
        p = o["part"]
        if self.horizontal:
            p[0] = c.px
            p[1] = 0
            o["offset"] = self.scroll - (c.x0 - b[0])
        else:
            p[0] = 0
            p[1] = c.py
            o["offset"] = self.scroll - (c.y0 - b[1])
        o["axis"] = "x" if self.horizontal else "y"
        sp = o["spill"] = c.grow + _ui_draw.mark_reach("card") + 2
        sel = self.sel
        # the cards across a row: the first's offset into the band, the pitch
        a = o.get("across")
        if a is None:
            a = o["across"] = [0, 0]
        a[0] = c.y0 - b[1] if self.horizontal else c.x0 - b[0]
        a[1] = c.py if self.horizontal else c.px
        o["at"] = sel % per
        o["gen"] = 1 if self._focused(True) else 0
        o["keep"] = key[1]
        o["whole"] = whole or not held
        o["over"] = over
        hz = self.horizontal
        # (a band frame over one colour may move the band out of the last
        # frame's buffer; the handheld icons' fade blends the band's end)
        o["shift"] = self.flat and not whole
        o["fade"] = (0 if hz or c.desk or c.hints[3] <= 0 or self.wall_ink is not None
                     else _FADE * self.layout.fs)
        rows = self._band_keys((n + per - 1) // per, per)
        _ui.table("", self._kcols, rows, sel // per, o)
        sh = self.shifted
        del sh[:]
        r = _FLOOR
        d = _ui.table_shift(r)
        if d is not None:
            # the view moved out of the last frame's buffer by the scroll's
            # difference (`shift_last`, fenced before the floor draws over
            # it); a canvas that cannot draws the view whole
            if cv is not self._scv:
                self._scv = cv
                self._shift = getattr(cv, "shift_last", None)
                self._fence = getattr(cv, "shift_fence", None)
            sl = self._shift
            if sl is None or not (sl(r[0], r[1], r[2], r[3], d, 0) if hz
                                  else sl(r[0], r[1], r[2], r[3], 0, d)):
                _ui.table_shift(r, True)
            else:
                sh.append((r[0], r[1], r[2], r[3]))
                if self._fence is not None:
                    self._fence()
        faces = self.face_key is not None and self._faces_for(cv, c.side, c.r, self._face_cap(c))
        if beside is not None:
            beside(cv, whole or not held)
        end = self.band_floor(cv, fill, not note)
        if note:
            del self.floor[:]
        r = _PART
        items = self.items
        on = None
        i = _ui.table_part(r)
        side = c.side
        while i >= 0:
            # the part is a column of the shelf (a row of the icons): its
            # cards lie along it, a pitch apart, from where the engine put
            # it, drawn in its reach inside the floor rect it meets
            x = r[0] if hz else c.x0
            y = c.y0 if hz else r[1]
            q = self._in_floor(r[0] - sp, r[1] - sp, r[0] + r[2] + sp, r[1] + r[3] + sp)
            for k in range(i * per, min(n, (i + 1) * per)):
                t = (x, y, side, side)
                if hz:
                    y += c.py
                else:
                    x += c.px
                if k == sel:
                    on = q
                    continue
                if (q is None or t[0] - sp >= q[0] + q[2] or t[0] + c.px + sp <= q[0]
                        or t[1] - sp >= q[1] + q[3] or t[1] + c.py + sp <= q[1]):
                    continue               # (its reach lies outside the floor)
                if faces:
                    self._stamp_cell(cv, items[k], k, t, c, q)
                else:
                    self._draw_cell(cv, items[k], t, False, c)
            i = _ui.table_part(r)
        if on is not None:
            # the focused card last, over its neighbours' reach
            cv.clip(on[0], on[1], on[2], on[3])
            if not faces or self._focused(True):
                self._draw_cell(cv, items[sel], self._cell_rect(c, sel), True, c)
            else:
                self._stamp_cell(cv, items[sel], sel, self._cell_rect(c, sel), c, on)
            cv.clip()
        for q in self.floor:
            cv.clip(q[0], q[1], q[2], q[3])
            self._fade(cv, c)
        cv.clip()
        _ui.end()
        return end

    def _cell_rect(self, c, i):
        """Cell `i`'s card rect at the current scroll (`cell_of`'s)."""
        row, col = self.cell_of(i)
        if self.horizontal:
            return (c.x0 + col * c.px - self.scroll, c.y0 + row * c.py, c.side, c.side)
        return (c.x0 + col * c.px, c.y0 + row * c.py - self.scroll, c.side, c.side)

    def _face_cap(self, c):
        """The slots the cards' faces lie in (`_faces_for`): the band's
        cards and a row past either end at least, and as many as
        `_FACES_BYTES` holds of them up to the engine's `_FACE_SLOTS`, so the
        faces of the rows a scroll passes stay drawn (an idle frame draws
        them outward from the band, `mint_faces`)."""
        least = (c.cols + 2) * c.rows if self.horizontal else (c.rows + 2) * c.cols
        s = c.side + 2 * (0 if c.r else self.layout.fs - 1)
        most = min(_FACE_SLOTS, _FACES_BYTES // (2 * s * s))
        return least if least > most else most

    def _faces_for(self, cv, side, r, cap):
        """Whether this paint stamps its cards from their kept faces
        (`ui.slots` on the table just drawn: the engine keeps which card's
        face each slot holds), `cap` of them on canvas `cv`, drawn for
        cards of `side` with corners `r`, where the home keeps them
        (`face_key`): the faces' layer made for them, each slot the card's
        square and the square frame's margin round it. A canvas whose layer
        holds no pixels forfeits them."""
        key = self.face_key
        if key is None or self._faces_off:
            return False
        m = 0 if r else self.layout.fs - 1
        g = self._face_geo
        if (self._face_lay is None or g[0] is not cv or g[1] != side or g[2] != m
                or g[3] != cap):
            self.release_faces()
            s = side + 2 * m
            try:
                self._face_lay = cv.new_layer(s, s * cap)
            except Exception:  # noqa: BLE001 -- a recording canvas: draw the cards
                self._faces_off = True
                return False
            self._face_geo = (cv, side, m, cap)
            self._face_fill = [None] * cap
            self._face_of = None
        if self._face_of is not key:
            # (another layer or look: every face is drawn again)
            self._face_of = key
            _FACE_GEN[0] = self._fkey = (_FACE_GEN[0] + 1) & 0x3fffffff
        _ui.slots(cap, self._fkey)
        self._faces_done = False         # (the table drawn: its rows to name again)
        # what every face of this paint is drawn with, read once a paint
        self._fdiv = self._face_div(side)
        self._fstepped = bool(_ui_draw.depth()[4])
        self._fring = self._face_ring()
        return True

    def release_faces(self):
        """Give the faces' layer back (the fit check's release, a layout
        change, an arrangement left): True when there was one."""
        lay = self._face_lay
        if lay is None:
            return False
        self._face_lay = None
        self._face_fill = None
        rel = getattr(lay, "release", None)
        if rel is not None:
            rel()
        return True

    def faces_bytes(self):
        """The faces' layer's bytes, 0 where none is kept."""
        if self._face_lay is None:
            return 0
        g = self._face_geo
        s = g[1] + 2 * g[2]
        return s * s * g[3] * 2

    def _face_name(self, i):
        """Card `i`'s cell in the band table's rows (`_band_keys`): what its
        face is drawn from beside its cover."""
        per = len(self._kcols)
        return self._krows[i // per][i % per]

    def _face_div(self, n):
        """The decode a card of side `n` draws its cover from."""
        return _cover_div_for(n)

    def _titled(self, c):
        """Whether a cell carries its title under it."""
        return True

    def _cover_of(self, it, div):
        """The cover a card's face draws (`cover_for`), None for the "+ New"
        page and for a cart whose cover is not there (its title tile)."""
        if self.cover_for is None or it.get("type") in PSEUDO_TILE_TYPES:
            return None
        return self.cover_for(it, div)

    def _face(self, cv, it, pic, r, cover):
        """A card's face over `pic` (corners `r`, the cells' radius and its
        growth): its picture (`_face_picture`), then its rim, the edge ring
        or the square frame."""
        self._face_picture(cv, it, pic, r, cover)
        if r:
            cv.rrect(pic[0], pic[1], pic[2], pic[3], r, None, ring=self._face_ring())
        else:
            self._card_frame(cv, pic)

    def _face_ring(self):
        """The colour of the edge ring a face with round corners wears
        (`_face`'s), None for none."""
        return (self.theme or {}).get("edge", 0)

    def _face_picture(self, cv, it, pic, r, cover):
        """A card's picture over `pic`, corners `r`: the "+ New" page, the
        cover drawn from `cover`, else the title tile (`_draw_picture`'s,
        undimmed, with the cover in hand)."""
        stepped = bool(_ui_draw.depth()[4])
        if it.get("type") in PSEUDO_TILE_TYPES:
            draw_new_art(cv, self.theme or {}, pic, r, stepped,
                         self.icon_spec() if self.icon_spec is not None else None,
                         self.layout.fs)
        elif cover is not None:
            cv.cover(cover.pix, cover.w, 0, 0, cover.w, cover.h, pic[0], pic[1], pic[2],
                     pic[3], r, stepped)
        else:
            self._draw_title_tile(cv, it, pic, r)

    def _stamp_cell(self, cv, it, i, t, c, b):
        """An unfocused cell, its card out of its kept face (`_stamp_face`)
        and its title, which lies on the ground."""
        self._stamp_face(cv, it, i, t, c.r, b)
        if self._titled(c):
            self._draw_title(cv, it, t[0] + t[2] // 2, t[1] + c.title_dy, c.title_w, False)

    def _stamp_face(self, cv, it, i, t, r, b, plain=True):
        """Card `it` (index `i`) over `t`, corners `r`, out of its kept face
        (`ui.slot`), clipped to `b`: copied from the slot holding the card
        as it is now --
        whole on a plain ground, which the slot's corners were drawn over;
        over a backdrop, as where the corners lie over anything but the
        plain ground (not `plain`: the shelf's shadows), a corner's partly
        covered pixels blend over what lies under them, and are drawn as the
        card draws them (`DeviceCanvas.face`, a cover's and a title tile's;
        the "+ New" page's rows under its corners from its own draw,
        clipped). A card no slot holds is drawn as it is: a paint never
        draws a face, so a step that brings in a cold row evicts nothing
        (the idle frames' `mint_faces` draws them)."""
        x, y, n, _n = t
        cover = self._cover_of(it, self._fdiv)
        new = it.get("type") in PSEUDO_TILE_TYPES
        j = _ui.slot(self._face_name(i), 0 if cover is None else cover.serial, False)
        if j < 0:
            cv.clip(b[0], b[1], b[2], b[3])
            self._face(cv, it, t, r, cover)
            return
        m = self._face_geo[2]
        s = self._face_geo[1] + 2 * m
        lay = self._face_lay
        if (self.face_key[1] is None or not plain) and (r or new):
            if m == 0 and r and not new and 2 * r < n:
                fill = self._face_fill[j]
                if cover is not None or fill is not None:
                    # the face copied, its corners' partly covered pixels
                    # drawn over what is there: one call (`DeviceCanvas.face`)
                    if cover is None:
                        cv.face(lay, j * s, x, y, n, n, None, 0, 0, 0, fill[0], fill[1], r,
                                self._fstepped, self._fring)
                    else:
                        cv.face(lay, j * s, x, y, n, n, cover.pix, cover.w, cover.w,
                                cover.h, None, None, r, self._fstepped, self._fring)
                    return
            # the "+ New" page's frame runs round its corners: its rows
            # under them drawn from its own draw, the rest copied
            k = r + 2 * self.layout.fs + 1
            if 2 * k >= n:
                y0 = y1 = y + n // 2
            else:
                y0 = y + k
                y1 = y + n - k
        else:
            y0 = y - m
            y1 = y + n + m
        bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
        ry0 = y0 if y0 > by0 else by0
        ry1 = y1 if y1 < by1 else by1
        rx0 = x - m if x - m > bx0 else bx0
        rx1 = x + n + m if x + n + m < bx1 else bx1
        if ry1 > ry0 and rx1 > rx0:
            cv.blit_strip_rect(lay, x - m, y - m - j * s, rx0, ry0, rx1 - rx0, ry1 - ry0)
        if y0 > y - m and rx1 > rx0:
            self._face_rows(cv, it, t, r, cover, rx0, rx1,
                            y - m if y - m > by0 else by0, y0 if y0 < by1 else by1)
            self._face_rows(cv, it, t, r, cover, rx0, rx1,
                            y1 if y1 > by0 else by0, y + n + m if y + n + m < by1 else by1)
            cv.clip(b[0], b[1], b[2], b[3])

    def _draw_face(self, j, it, r, cover, new):
        """Card `it`'s face drawn into slot `j`, over the plain ground where
        the faces have one, and what its corners are drawn from kept beside
        it (a title tile's block and letter)."""
        g = self._face_geo
        n = g[1]
        m = g[2]
        s = n + 2 * m
        lay = self._face_lay
        sy = j * s
        lay.clip(0, sy, s, s)
        flat = self.face_key[1]
        if flat is not None:
            lay.rect(0, sy, s, s, flat)
        self._face(lay, it, (m, sy + m, n, n), r, cover)
        lay.clip()
        self._face_fill[j] = None if cover is not None or new else \
            _ui_draw.initial_parts(lay, it.get("title") or "", n, n)

    def mint_faces(self, ms=4):
        """On a frame nothing waits on: draw the faces the engine names
        next (`ui.slot_next`: the cards the band shows, then the rows past
        either end of it, nearest first) whose covers are decoded or known absent, for
        about `ms` -- a step then copies the row it brings in -- the walk
        itself held to `ms` too, the next such frame going on from where
        it stopped (a key between two frames waits on neither). True where
        one was drawn. A round that found every face drawn ends the asking
        until the band's table is drawn again (`_faces_for`)."""
        if self._faces_done:
            return False
        g = self._face_geo
        ready = self.cover_ready
        if (self._face_lay is None or self.face_key is None or ready is None
                or self._host is None or self._krows_of is not self.items):
            return False
        c = self._cells()
        if c.side != g[1] or (0 if c.r else self.layout.fs - 1) != g[2]:
            return False                 # (the faces are another layout's)
        self._host.resume(g[0], self.theme)
        items = self.items
        sel = self.sel if self._focused(True) else -1
        r = self._face_r()
        t0 = _ticks_ms()
        done = 0
        owed = False                     # a part whose cover has yet to land
        i = _ui.slot_next()
        while i >= 0:
            if i < len(items) and i != sel:
                it = items[i]
                new = it.get("type") in PSEUDO_TILE_TYPES
                cover = None if new else ready(it, self._fdiv)
                if cover is False:       # (its cover lands first: the paint takes it)
                    owed = True
                else:
                    j = _ui.slot(self._face_name(i), 0 if cover is None else cover.serial,
                                 True)
                    if j >= _ui.SLOT_MINT:
                        self._draw_face(j - _ui.SLOT_MINT, it, r, cover, new)
                        done += 1
            if _ticks_diff(_ticks_ms(), t0) >= ms:
                return done > 0          # (the round goes on from here next time)
            i = _ui.slot_next()
        if not done and not owed:
            self._faces_done = True
        return done > 0

    def _face_r(self):
        """The corners a face is drawn with."""
        return self._cells().r

    def _face_rows(self, cv, it, t, r, cover, x0, x1, y0, y1):
        """Card `it`'s face over `t` drawn in rows [y0, y1) of columns
        [x0, x1) alone: the rows under its corners, over what is there."""
        if y1 > y0:
            cv.clip(x0, y0, x1 - x0, y1 - y0)
            self._face(cv, it, t, r, cover)

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
        focus = self._focused(selected)
        x, y, n, _n = rect
        g = c.grow if focus else 0
        pic = (x - g, y - g, n + 2 * g, n + 2 * g)
        r = c.r + g if c.r else 0
        self._face(cv, it, pic, r, self._cover_of(it, self._face_div(n)))
        if focus:
            _ui_draw.focus(cv, self.theme, pic, size="card", r=r, flat=True)
        if self._titled(c):
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


def _cut(rects, h):
    """`rects` less rect `h`: each one that meets it in up to four parts."""
    hx0, hy0, hx1, hy1 = h[0], h[1], h[0] + h[2], h[1] + h[3]
    out = []
    for r in rects:
        x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
        if hx0 >= x1 or hx1 <= x0 or hy0 >= y1 or hy1 <= y0:
            out.append(r)
            continue
        if hy0 > y0:
            out.append((x0, y0, r[2], hy0 - y0))
        if hy1 < y1:
            out.append((x0, hy1, r[2], y1 - hy1))
        m0, m1 = max(y0, hy0), min(y1, hy1)
        if hx0 > x0:
            out.append((x0, m0, hx0 - x0, m1 - m0))
        if hx1 < x1:
            out.append((hx1, m0, x1 - hx1, m1 - m0))
    return out


def _cover_div_for(side):
    """The cover decode a card `side` across draws: the whole cover where it
    takes 128 or more, else its half (a handheld shelf card's 1x, an icon's
    reduction)."""
    return _COVER_BASE if side > _COVER_SIDE // 2 else _COVER_HALF


# The home's tabs (docs/theme_and_shell_2026-11.md §8.10), in tab-row order, and
# the noun the bar's title counts each in.
HOME_TABS = (("games", "Games"), ("mine", "Mine"), ("apps", "Apps"),
             ("make", "Make"))
_TAB_NAME = dict(HOME_TABS)
_TAB_ICON = {"games": "run", "mine": "moy", "apps": "gear", "make": "edit"}
_TAB_NOUN = {"games": ("GAME", "GAMES"), "mine": ("CART", "CARTS"),
             "apps": ("APP", "APPS"), "make": ("PROJECT", "PROJECTS")}
# The handheld hero's scale the home is laid out for: a board's verdict
# (FullscreenStackWM.HOME_SCALE) draws it at most this large.
HOME_SCALE_HH = 2.0
# The handheld home (docs/history/look_2026-10.md step 17): each card shows its
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
              "favourite": ("F", "Add favourite"), "options": ("X", "Options"),
              "window": ("P", "Play")}
SHELF_GAP = 42       # the details block's foot to the shelf's caption, in
                     # glass pixels: the full PROJECTS window's, at every size
DETAILS_MIN = 280    # the narrowest details pane beside the hero, in glass
                     # pixels at the target's 1x: a primary button and one more
# The most buttons a desk card shows, per kind of tab: a played tab's
# (Games, Mine), Make's on a real card. The details pane keeps room for as
# many rows as the widest of them wraps to, so the shelf stays put as the
# selection moves.
_DESK_VERB_SETS = (("play", "change", "copy"), ("change", "copy", "delete"))
# ...and the desk's Projects window's, whose Make card has Play third
PROJECTS_VERB_SETS = _DESK_VERB_SETS + (("change", "copy", "window", "delete"),)
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


def desk_button_span(dw, fs, u, bh, sets=_DESK_VERB_SETS):
    """The pixels the desk pane keeps for its buttons under their first
    row: as many as the widest card's (`sets`, a home's verb sets) rows
    take in a pane `dw` wide, `bh` a button's height."""
    return max(b[2] * (bh + DESK_ROW_GAP // u) + b[5]
               for vs in sets
               for b in desk_button_layout(vs, dw, fs, u)[-1:])


def tab_count_title(tab, n):
    """The bar's title on the home: the active tab's count."""
    one, many = _TAB_NOUN.get(tab, ("CART", "CARTS"))
    return "%d %s" % (n, one if n == 1 else many)
