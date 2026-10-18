# Map (grep -n a name to jump there):
#   new_tile                    the "+ New" card
#   Launcher                    the picker's grid: carts laid out as a shelf
#   Launcher.nav2d              grid navigation
#   Launcher.pointer_frame      one pointer sample over the grid
#   Launcher.cover_specs        every cover the next full draw needs
#   Carousel                    the home's row: the centre card scaled by its distance
#   Carousel.anim_frame         the fling, the glide to the centre, the settle
#   Carousel.draw_shift         the moving frame: the wallpaper restored around the cards
#   IconHome                    the icon arrangement: the grid over one-tile cells
#   LauncherHomeLayer           the home: wallpaper, tab row, carousel, zone
#   LauncherHomeLayer.draw      the home frame
#   LauncherHomeLayer.zone_tap  the active tab's verb chips
#   HomeWindowLayer             the home in a desktop-mode window (PROJECTS)
"""The home (#28), extracted from Workstation (runtime/console.py) as a Layer
-- docs/history/shell_layers_refactor_v1.md (Move 1b).

  * `Launcher`     -- the card GRID model + draw: items/sel/scroll/nav + the card
                      rendering, a shelf of two rows that scrolls sideways. Its
                      instances are ws.launcher in the home's `grid`
                      arrangement and ws.projects_grid, the home window's.
  * `Carousel`     -- the home's row (docs/shell_layout_2026-10.md §12): the
                      same selection model over one row of square cover cards,
                      the centre card larger by a function of its distance
                      from the centre. Its instance is ws.launcher in the
                      `carousel` arrangement, the default (the layout's `home`).
  * `IconHome`     -- the `icons` arrangement: the grid's model over one-tile
                      cells (`_IconCells`), the focused cell's name in the
                      desk's label pill.
  * `LauncherHomeLayer` -- the "launcher" content Layer: the home, console mode's
                      root on every board (wallpaper -> tab row -> carousel ->
                      bar), its four tabs (Games / Mine / Apps / Make, the lists
                      ws._launcher_items builds) and their input. A tap on the
                      centre card is the active tab's (ws.launch_selected).
  * `HomeWindowLayer` -- the "projects" content Layer: the SAME home in a
                      desktop-mode window (docs/shell_layout_2026-10.md §12, the
                      desk's PROJECTS), always the `grid` arrangement over
                      ws.projects_grid, opened on its Make tab.

Boundary (single source of truth): ws.launcher (the instance) + ws.open() (open the
selected cart -- lifecycle, pinned) + the cart store stay on Workstation. The card
draw needs the palette + the shared glyph raster -- `NAMES` and `ui.glyph` are
INJECTED at construction (`self._blit_glyph`), and the card-type maps
`_TYPE_GLYPH`/`_TYPE_COLOR` live here. The rect hit-test is `ui.rect_in`. No
circular import: this is a leaf (the only console touch is a lazy Layout fallback
for a bare Launcher() that no caller ever constructs).

The lent bar zone (#46): each home layer draws and taps it (`draw_zone`/`zone_tap`)
and proxies its grid's `zone_gen` -- an int the grid bumps whenever its `sel` (a
property) or `items` changes, so BarLayer's strip cache trusts one int instead of
re-deriving "did the selection move". The zone carries the active tab's verbs
on the selected card.
"""


try:
    import ui as _ui
    import ui_draw as _ui_draw
    import cart_listing as _cl
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import cart_listing as _cl
_in = _ui.rect_in   # one hit-test (ui.rect_in)


# Display-type helpers for the Library shelf (visual identity v1's library-concept
# mockup): block-scaled petme128 headings that render identically on every canvas.
# The tick helpers feed the perf_capture-gated home-frame split (see draw below).
try:
    from chrome import Layout as _Layout, _print_scaled, _text_w, _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import (Layout as _Layout, _print_scaled, _text_w,
                                _ticks_ms, _ticks_diff)

try:
    import layout_base as _lb
    import shell_layout as _shell_layout
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import layout_base as _lb
    from runtime import shell_layout as _shell_layout

try:
    from widgets import ConfirmTap
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import ConfirmTap


# The "+ New" card (Make's first, an empty Mine's only) is a
# PSEUDO entry (not a real cart): a plain dict with a marker `type` + a title,
# flowing through the SAME grid (nav/sel/tile_at) as real carts, dispatched by its
# type at tap time (create a game + open the Editor). `path` is None so cart
# management (dup/del) + the icon-sheet loader skip it, and a fresh dict per call
# avoids aliasing one shared mutable tile.
NEW_TILE_TYPE = "new"
PSEUDO_TILE_TYPES = (NEW_TILE_TYPE,)

# A cover's side (SPEC.md 3.6) and the decodes of it a card draws:
# CoverCache's BASE and HALF, and the icon home's ICON (cover_cache.py names
# them).
_COVER_SIDE = 128
_COVER_BASE = 1
_COVER_HALF = 2
_COVER_ICON = 8
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


def _cover_div(it, w, h):
    """Which decode of the cart's cover a card whose unselected art slot is
    w x h draws: the BASE when
    the slot takes 128 at a whole-number scale (`_cover_scale`); under that,
    the HALF -- the grid's interim need -- unless the cart names its own icon
    (SPEC.md 3.4), which its author drew for small sizes and the card draws
    instead. 0: no cover in this slot at all."""
    if _cover_scale(_COVER_SIDE, w, h):
        return _COVER_BASE
    if it.get("icon") or w < _COVER_SIDE // 2 or h <= 0:
        return 0
    return _COVER_HALF


def _centred(slot, size):
    # Where `size` starts in `slot`: centred, and when it overflows the slot,
    # cropped by the smaller half at the start.
    if size <= slot:
        return (slot - size) // 2
    return -((size - slot) // 2)


def _draw_cover(cv, img, x, y, w, h, art_h, outer=None):
    """A decoded cover centred in the (x, y, w, h) slot at the `_cover_scale`
    of the card's UNSELECTED w x art_h slot, so selecting a card (its action
    row shortens the slot to h) crops the cover and never rescales it -- 1x
    at the least: a HALF in a slot shorter than it. What overflows the slot
    is cropped by a clip to the slot inside `outer`, the clip the caller
    draws its cards in (None: the whole canvas), which is the clip it is
    left with."""
    s = max(1, _cover_scale(img.w, w, art_h))
    sw = img.w * s
    sh = img.h * s
    cut = sw > w or sh > h
    if cut:
        x0, y0, x1, y1 = x, y, x + w, y + h
        if outer is not None:
            x0 = max(x0, outer[0])
            y0 = max(y0, outer[1])
            x1 = min(x1, outer[0] + outer[2])
            y1 = min(y1, outer[1] + outer[3])
        if x1 <= x0 or y1 <= y0:
            return
        cv.clip(x0, y0, x1 - x0, y1 - y0)
    cv.blit565(img.pix, img.w, img.h, x + _centred(w, sw), y + _centred(h, sh),
               s)
    if cut:
        if outer is None:
            cv.clip()
        else:
            cv.clip(*outer)


def new_tile():
    return {"type": NEW_TILE_TYPE, "title": "New", "path": None}


# Per-type icon glyph + art-box color for a cart tile on the desktop (the pre-literate
# cue), used when a cart has no sprite of its own. The "+ New" card gets a
# distinctive bright box + a clear plus glyph.
_TYPE_GLYPH = {"wallpaper": "paint", "game": "run", "app": "app", "tool": "gear",
               "story": "run", NEW_TILE_TYPE: "plus"}
_TYPE_COLOR = {"wallpaper": 12, "game": 8, "app": 11, "tool": 9,
               "story": 15,     # a kid's Storybook cart (#78): peach, playable
               NEW_TILE_TYPE: 14}  # index by type


def _draw_new_icon(cv, names, img, x, y, sc):
    """The IconSheet plus at `sc`, pal-remapped so it reads on the yellow
    "+ New" field (white outline -> black, yellow body -> orange)."""
    cv.pal(names["white"], names["black"])
    cv.pal(names["yellow"], names["orange"])
    cv.spr(img, x, y, sc)
    cv.pal()


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
    top left: (its edge, its inside). A five-pointed star, point up, sampled
    at pixel centres about the chip's centre pixel, so its halves mirror
    each other; its edge the pixels with a side out of it. Made once a
    side."""
    got = _STAR.get(side)
    if got is not None:
        return got
    import math
    ro = side * 0.44
    ri = ro * 0.42
    c = side // 2
    pts = []
    for k in range(10):
        a = math.pi * (k / 5.0 - 0.5)
        r = ro if k % 2 == 0 else ri
        pts.append((r * math.cos(a), r * math.sin(a) + side * 0.04))

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
    when `on`, its edge alone in the dim ink when not."""
    x, y, side, _h = rect
    cv.rrect(x, y, side, side, side // 2, th.get("surface", names["white"]),
             ring=th.get("border", names["light_grey"]))
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


class Launcher:
    """The home's grid arrangement (#28): carts laid out as a SHELF -- a card grid that
    SCROLLS continuously LEFT-RIGHT (a shelf slides sideways; no pages) with
    the ONE tall featured card (the pinned "+ New") at the head of the list. Keeps the selection model (items/sel/selected/move) the rest of the
    console relies on; `scroll` is the grid's pixel offset, owned here and
    ridden through a ui.ScrollRegion + ui.DragTap (touch drag + the slim
    scrollbar + tap-vs-drag disambiguation).

    The grid geometry comes from an injected `Layout` (#39) so it reflows with the
    system canvas size + font scale; COLS/ROWS are instance attributes mirrored
    from the live layout (so callers reading them, and the selection model, track
    the reflowed grid). A bare Launcher(items) (unit construction) falls back to
    the 320x240 / scale-1 baseline shelf.

    `names` (the palette) + `blit_glyph` (the shared glyph blitter) are injected so the
    tile draw stays free of a console back-import."""

    arrangement = "grid"          # the layout's `home` value this class draws

    tab_focus = False             # the tab row holds the focus, not a card

    def __init__(self, items, layout=None, names=None, blit_glyph=None):
        self.items = items
        # Visual identity v1 (docs/visual_identity_v1.md Section 1.2): the HOME grid
        # exposes PLAY/CHANGE on the selected card on the desktop-density tiers
        # (console sets this True where a grid wants them).
        self.actions = False
        self.tab = HOME_TABS[0][0]   # the home's active tab (ws.set_home_tab)
        self.tab_sel = {}            # tab -> the path its card was on when left
        # Stage 4 (#46 zoned bar): bumped whenever anything the lent left zone
        # shows (the selected cart's title) changes -- the `sel` property below
        # covers nav/tap/hover; set_items bumps it too (a NEW/DUP/DEL/rename can
        # change the title under an unchanged sel). Must be set BEFORE `self.sel`
        # below, since the sel setter reads it. BarLayer folds this into its cache
        # key so the zoned strip re-renders only on a real change.
        self.zone_gen = 0
        self.sel = 0
        # Continuous scroll state: `_scroll` (raw px, the state of record) is
        # clamped LAZILY against the live layout (the `scroll` property), so the
        # windowed tier's per-window layout swaps (wm_windowed set_layout's on
        # every context switch) can never destroy a position. `_region` is the
        # touch INTERACTION model (a HORIZONTAL ui.ScrollRegion: drag + the slim
        # scrollbar), synced from `_scroll` whenever a drag isn't active -- the
        # Settings-rows pattern (settings_layer._scroll_region); `_taps` is the
        # shared tap-vs-drag machine over it.
        self._scroll = 0
        self._region = _ui_draw.ScrollRegion(horizontal=True)
        self._taps = _ui_draw.DragTap(self._region)
        self._NAMES = names
        self._blit_glyph = blit_glyph
        self.theme = None             # chrome THEME tokens (look.set_theme pushes them);
                                      # None -> the yellow default accent
        self.icon_for = None          # optional kind -> 16x16 IconSheet Image (console
                                      # wires ws._bar_image) -- the shelf's pseudo cards
                                      # draw the real themeable pencil/plus icon big
        self.cover_for = None         # optional (cart, div) -> the cart's decoded
                                      # cover (CoverCache.cover_for, SPEC.md 3.6)
        self.tinted_for = None        # optional (cart, colour, alpha) -> the
                                      # cover dimmed (CoverCache.tinted)
        self.dim = None               # (colour, alpha 0..255): the theme dims the
                                      # carousel's neighbours (`dim_neighbours`)
        self._card_clip = None        # the clip cards draw in while a draw runs
        self.favorite_for = None     # optional cart -> bool (console wires
                                      # ws.carts.is_favorite as a bound method,
                                      # #105): when set, the SELECTED card's corner star
                                      # badge draws + is tappable (favorite_rect below)
        if layout is None:
            # Defensive default for a bare Launcher(items) (no caller does this); lazy so
            # there's no launcher_layer<->console module-load cycle.
            try:
                from console import Layout
            except ImportError:  # pragma: no cover - host fallback when not yet aliased
                from runtime.console import Layout
            layout = Layout()
        self.set_layout(layout)

    def set_layout(self, layout):
        """Adopt a new grid layout (size/font-scale change). Mirrors COLS/ROWS as
        instance attributes for the callers/tests that read them directly. The
        scroll offset is deliberately NOT re-clamped here (it clamps lazily on
        read) -- the windowed tier applies every window's layout to BOTH grids on
        each context switch, and an eager clamp against a small window would lose
        the desktop grid's position."""
        self.layout = layout
        self.COLS = layout.cols
        self.ROWS = layout.rows
        # (#113) NO eager ring invalidate here. This is called MANY times per
        # frame on the windowed tier -- _install swaps in a window's layout
        # context to draw its content and swaps the ROOT context back after, for
        # BOTH grids, so the picker's layout legitimately alternates
        # window<->root ~4x a frame (measured on glass 2026-07-25: 1040 calls in
        # 4 idle seconds). Invalidating here wiped the paint ring every time, so
        # blit_shift's ring was ALWAYS empty and the scroll-as-blit path could
        # never arm on the desktop tier -- every drag frame repainted every
        # visible card. The ring's own key is the real guard: it carries the
        # surface geometry (canvas w/h, font scale, grid rect) plus the theme
        # and item count, so a paint recorded under a different layout can never
        # be mistaken for a match. set_items still invalidates -- an item LIST
        # change alters pixels the key's length alone can't distinguish.

    # -- selection ----------------------------------------------------------

    @property
    def sel(self):
        return self._sel

    @sel.setter
    def sel(self, value):
        # Stage 4 (#46 zoned bar): bump zone_gen on every ACTUAL change, regardless
        # of call site (nav2d / a tap / trackball hover all assign
        # `.sel` directly) -- this is what lets BarLayer's cache key trust
        # `zone_gen` instead of re-deriving "did the selection move" itself.
        if value != getattr(self, "_sel", None):
            self.zone_gen += 1
        self._sel = value

    def set_items(self, items):
        """Replace the cart list (after a create/duplicate/delete) and re-clamp the
        selection + scroll so neither dangles past the new end. The public re-sync
        entry point -- callers must not poke the private scroll bookkeeping."""
        self.items = items
        self.zone_gen += 1          # a rename/new/dup/del can change the title text
                                    # the lent zone shows even when sel is unchanged
        self._region.invalidate()   # #113: the shelf pixels no longer match any
                                    # recorded paint -- full paints until re-armed
        if self.sel >= len(items):
            self.sel = max(0, len(items) - 1)
        self._scroll_to_sel()

    def nav2d(self, dx, dy):
        """Grid navigation: dx hops a card COLUMN (the shelf's scroll axis --
        the layout's packing maps columns<->indices; the tall featured slot 0
        is the whole of column 0), dy steps within the column. Clamped within
        the list (no wrap) so arrow nav feels like a real grid, and the scroll
        follows the selection."""
        n = len(self.items)
        if not n:
            return
        lay = self.layout
        f = self._featured()
        row, col = lay.tile_cell(self.sel, f)
        if dx:
            col += dx
            if col <= 0 and f:
                self.sel = 0               # back onto the tall card
            else:
                self.sel = max(0, min(n - 1, lay.tile_index(row, max(0, col), f)))
        elif dy and (self.sel > 0 or not f):   # the tall card spans both rows
            row = max(0, min(lay.rows - 1, row + dy))
            self.sel = max(0, min(n - 1, lay.tile_index(row, col, f)))
        self._scroll_to_sel()

    # -- continuous scroll (no pages) ----------------------------------------

    @property
    def scroll(self):
        """The effective pixel scroll offset: the raw position clamped against
        the LIVE layout (lazy, see set_layout)."""
        return max(0, min(self._scroll, self.max_scroll()))

    @scroll.setter
    def scroll(self, value):
        self._scroll = max(0, min(int(value), self.max_scroll()))
        if self._region.animating:
            self._region.stop()     # a programmatic scroll cancels a fling

    @property
    def dragging(self):
        """True while a touch drag (past the slop) is scrolling the grid."""
        return self._taps.dragging

    @property
    def flinging(self):
        """True while a released kinetic fling is coasting the grid (#113)."""
        return self._region.animating

    def anim_frame(self, dt):
        """Advance a live fling one frame (dt in SECONDS, the loop's tick) --
        the owning layer calls this at the top of draw, BEFORE painting, so
        the painted offset is this frame's. Returns True when the view moved."""
        if not self._region.animating:
            return False
        region = self._scroll_region()     # live geometry; sync skips a fling
        if region.tick(dt * 1000.0):
            self._scroll = region.offset   # direct: the setter would stop us
            return True
        return False

    def max_scroll(self):
        lay = self.layout
        return max(0, lay.grid_content_w(len(self.items), self._featured())
                   - lay.lib_grid[2])

    def scroll_cols(self, d):
        """Nudge the grid by d card columns (the footer arrow buttons)."""
        self.scroll = self.scroll + d * self.layout.lib_step

    def _scroll_to_sel(self):
        """Keep the selected card fully inside the grid viewport (keyboard nav)."""
        lay = self.layout
        gw = lay.lib_grid[2]
        _row, col = lay.tile_cell(self.sel, self._featured())
        x = col * lay.lib_step                   # content-relative card left
        cw = lay.lib_card_w
        s = self.scroll
        if x < s:
            s = x
        elif x + cw > s + gw:
            s = x + cw - gw
        self.scroll = s

    def _scroll_region(self):
        """The grid's ui.ScrollRegion, synced to the live layout + item count.
        `_scroll` stays the state of record; the region owns the offset only
        while a touch drag is active (the Settings-rows pattern -- re-snapping
        every sample would discard sub-slop finger travel)."""
        lay = self.layout
        self._region.set(lay.lib_grid, lay.grid_content_w(len(self.items),
                                                          self._featured()))
        if not (self._region.drag_active or self._region.animating):
            self._region.offset = self.scroll
        return self._region

    def band_rect(self):
        """The shelf band: the grid viewport inflated vertically by the focus-ring
        margin -- the EXACT rect the card clip uses (_draw_shelf), the drag-partial
        path fills, and the #113 blit path shifts. One formula so they can't drift."""
        lay = self.layout
        gx, gy, gw, gh = lay.lib_grid
        d = 2 * lay.fs + 2
        return (gx, gy - d, gw, gh + 2 * d)

    def draw_shift(self, cv, sheet_for, delta, damage, fill):
        """The #113 scroll-as-blit band draw: shift the shelf band's pixels by
        `-delta` (the cards move opposite the offset) and repaint ONLY the exposed
        strip plus the caller's `damage` rects (the stale cursor stamp), pixel-
        identical to a full _draw_shelf of the same state. The caller has proven
        eligibility via ScrollRegion.blit_shift (same items/sel/statics, pixels of
        a known offset in the target buffer); `fill(cv, rect)` paints the owning
        layer's band backdrop inside a rect (surface fill on the home shelf, the
        dotted panel on the picker).

        SIZE MATTERS, and on the desktop tier this barely pays. Measured on P4
        glass 2026-07-26, picker drag at 1024x600: this path 26.8ms per frame
        against 31.3ms for the plain full draw -- a 4.5ms saving, not the order of
        magnitude it buys on the T-Deck. 15.2ms of it is the one scroll_rect,
        moving an 874x429 band (750KB, ~148MB/s of cache traffic counting the
        write-allocate read) which is about what this PSRAM can do; the rest is
        the exposed strip plus the two or three cards it uncovers, all of it real
        work. The premise of #113 -- that shifting pixels beats redrawing them --
        holds at 320x240, where the band is 25x smaller than the cards it carries
        are here. It does NOT generalise to a desktop-sized band, and the cards
        cover 420 of the band's 429 rows, so there is no empty backdrop to stop
        shifting either. Making this materially faster means moving fewer bytes or
        overlapping the move with the repaint (async DMA), not tuning the loop."""
        bx, by, bw, bh = self.band_rect()
        if delta:
            cv.scroll_rect(bx, by, bw, bh, -delta, 0)
            if delta > 0:
                damage = damage + [(bx + bw - delta, by, delta, bh)]
            else:
                damage = damage + [(bx, by, -delta, bh)]
        # Clamp damage to the band -- pixels outside it were never shifted.
        rects = []
        for r in damage:
            x0 = max(r[0], bx)
            y0 = max(r[1], by)
            x1 = min(r[0] + r[2], bx + bw)
            y1 = min(r[1] + r[3], by + bh)
            if x0 < x1 and y0 < y1:
                rects.append((x0, y0, x1 - x0, y1 - y0))
        for r in rects:
            fill(cv, r)
        lay = self.layout
        d = 2 * lay.fs + 2
        clip = getattr(cv, "clip", None)
        if clip is not None:
            self._card_clip = (bx, by, bw, bh)
            clip(bx, by, bw, bh)
        sel = self.sel
        for i, rect in self._visible():
            # Inflate by the focus-ring margin so a card whose ring sliver was
            # damaged redraws whole (a full card redraw over shifted pixels is
            # byte-identical -- only the damaged sliver actually changes).
            rx0 = rect[0] - d
            ry0 = rect[1] - d
            rx1 = rect[0] + rect[2] + d
            ry1 = rect[1] + rect[3] + d
            if i == sel:
                # The selected card's reach past its cell (`_sel_reach`).
                rx0, ry0, rx1, ry1 = self._sel_reach(rect, rx0, ry0, rx1, ry1)
            for r in rects:
                if rx0 < r[0] + r[2] and r[0] < rx1 \
                        and ry0 < r[1] + r[3] and r[1] < ry1:
                    break
            else:
                continue
            it = self.items[i]
            if it.get("type") in PSEUDO_TILE_TYPES:
                self._draw_pseudo_card(cv, it, rect, i == self.sel)
            else:
                self._draw_cart_card(cv, it, rect, i == self.sel, sheet_for)
        if clip is not None:
            self._card_clip = None
            clip()
        self._draw_scroll_ui(cv)   # arrows redraw (dim state); draw_bar refills
                                   # its whole track, healing the shifted thumb

    def _sel_reach(self, rect, x0, y0, x1, y1):
        """The (x0, y0, x1, y1) the selected card at `rect` paints, given its
        ring's: the shelf's card paints no further."""
        return x0, y0, x1, y1

    def pointer_frame(self, px, py, click, down, dt_ms=None):
        """One pointer sample over the grid, fed through the shared ui.DragTap
        machine: returns the tapped tile index on a clean RELEASE (press and
        release on the same card with no drag travel), else None -- so a
        scroll gesture can never launch a cart. `click` is the press edge,
        `down` the held state (ws.pointer.down); `dt_ms` the loop's tick
        (feeds the kinetic release velocity, #113)."""
        region = self._scroll_region()
        press = self._taps.frame(px, py, click, down,
                                 slop=4 * self.layout.fs + 2, dt_ms=dt_ms)
        if self._taps.dragging:
            self._scroll = region.offset       # the grid follows the finger
        if press is None:
            return None
        i = self.tile_at(press[0], press[1])
        if i is not None and i == self.tile_at(px, py):
            return i
        return None

    def selected(self):
        return self.items[self.sel] if self.items else None

    def _featured(self):
        """Whether slot 0 is the tall featured card: a "+ New" heading the
        list. Every other list packs uniform columns."""
        items = self.items
        return bool(items) and items[0].get("type") in PSEUDO_TILE_TYPES

    @property
    def moving(self):
        return self._taps.dragging or self.flinging

    def tabs_rect(self):
        """The home's tab row in the grid arrangement: in the panel's header
        band, where the shelf's heading was, its plate (`_draw_home_chrome`:
        2*fs out, an fs-thick border) clear of the shelf band -- a drag frame
        shifts and refills the band, so chrome reaching into it would be torn."""
        lay = self.layout
        px, py, pw, _ph = lay.lib_panel
        fs = lay.fs
        inset = max(10 * fs, lay.cw // 42)
        h = min(_TAB_H * lay.cs, lay.lib_header_h - 4 * fs)
        y = py + (lay.lib_header_h - h) // 2
        y = min(y, self.band_rect()[1] - h - 3 * fs)
        return (px + inset, y, pw - 2 * inset, h)

    def repaint_band(self, cv, sheet_for, fill):
        """The whole band again: its backdrop (`fill(cv, rect)`), then the
        cards."""
        fill(cv, self.band_rect())
        self.draw(cv, sheet_for)

    def statics_sel(self):
        """The selection where pixels outside the band follow it (the desk
        home's hero and details), else -1: a moving frame repaints the band
        alone."""
        return -1

    def paint_key(self):
        """What a recorded paint's band pixels depend on besides the offset
        and the statics (#113's shift key): the selection, whose card wears
        the ring."""
        return self.sel

    def _visible(self):
        """(index, rect) for every card intersecting the grid viewport at the
        current scroll (partially clipped cards included -- the draw clips)."""
        s = self.scroll
        lay = self.layout
        f = self._featured()
        for i in range(len(self.items)):
            r = lay.tile_rect(i, s, f)
            if r is not None:
                yield i, r

    def tile_rect(self, i):
        """The grid-cell rect for cart index i at the current scroll, or None if
        it's outside the grid viewport (geometry from the live Layout, so it
        reflows with the system canvas / font scale)."""
        return self.layout.tile_rect(i, self.scroll, self._featured())

    def tile_at(self, px, py):
        gx, gy, gw, _gh = self.layout.lib_grid
        if not (gx <= px < gx + gw):         # the clipped part of a card is not
            return None                      # tappable -- viewport-bounded hits
        for i, r in self._visible():
            if _in(px, py, r):
                return i
        return None

    def action_rects(self):
        """The selected card's PLAY / CHANGE button rects as {"play": r, "change": r}
        (visual identity v1 Sections 1.2/6.1: the selected cartridge exposes the two
        verbs; primary activation still always plays). The mockup's in-card row along
        the card's bottom edge, under the title band. Wide-card tiers only: returns
        None when the cards are too narrow for the row (lay.lib_card_actions -- the
        lent bar zone carries the verbs there, LauncherHomeLayer.draw_zone), when
        `actions` is off (the picker), for a pseudo tile (Make has one verb, its
        tap), or when the selection is scrolled out of the grid viewport. Draw and
        hit-test both read this, so they can't desync."""
        lay = self.layout
        if not lay.lib_card_actions or not self.actions or not self.items:
            return None
        it = self.items[self.sel]
        if it.get("type") in PSEUDO_TILE_TYPES:
            return None
        r = self.tile_rect(self.sel)
        if r is None:
            return None
        x, y, w, h = r
        fs = lay.fs
        pad = max(2 * fs, 3)
        bh = max(13 * fs, 22)          # touch-target floor: 1x text, full-size button
        # Asymmetric split (the mockup's proportions): PLAY compact, CHANGE wide
        # enough for its six-glyph label at every shelf card width.
        avail = w - 3 * pad
        pw = avail * 45 // 100
        by = y + h - pad - bh
        return {"play": (x + pad, by, pw, bh),
                "change": (x + 2 * pad + pw, by, avail - pw, bh)}

    def _favorite_badge_geom(self, rect):
        """The corner badge square for card rect `rect` -- one formula shared by
        the draw path (_draw_cart_card) and favorite_rect (hit-test), so they can
        never desync."""
        x, y, w, _h = rect
        fs = self.layout.fs
        sz = max(14 * fs, 16, 12 * _lb.unit()) | 1      # odd: the star centres on a pixel
        d = _ui_draw.depth()
        pad = max(2 * fs, (d[2] // 2 + 2) if d[0] else 0)
        return (x + w - sz - pad, y + pad, sz, sz)

    def favorite_rect(self, i):
        """The SELECTED real card's corner favorite-star badge rect (#105: mirrors
        the pseudo card's corner pushpin -- a small always-present corner control,
        not a new chrome idiom), or None for a pseudo tile, an out-of-range index,
        a scrolled-out-of-view card, or when no favorite_for predicate is wired.
        Draw and hit-test both call this, so they can't desync (the action_rects
        pattern)."""
        if self.favorite_for is None or not self.items or i is None:
            return None
        if not (0 <= i < len(self.items)):
            return None
        it = self.items[i]
        if it.get("type") in PSEUDO_TILE_TYPES or not it.get("path"):
            return None
        r = self.tile_rect(i)
        if r is None:
            return None
        return self._favorite_badge_geom(r)

    def draw(self, cv, sheet_for=None):
        # Cards only -- the framed Library panel / tool backdrop + status strip
        # are drawn by the owning layer around this. One look on EVERY tier now
        # (the 320x240 baseline included): the Library SHELF cards (visual
        # identity v1's library mockup).
        self._draw_shelf(cv, sheet_for)

    # -- the Library shelf cards (visual identity v1) ---------------------------

    def _draw_shelf(self, cv, sheet_for):
        """The mockup's card grid: the tall featured slot ("+ New") at
        the head of the list + cover-art cartridge cards, continuously SCROLLED
        left-right -- cards clip to the grid viewport (clip is a v0.4 canvas
        verb: host, device and web all honor it), with the footer scroll arrows
        + slim scrollbar drawn outside the clip. The framed panel around this is
        the home layer's (the picker draws the same cards over its own tool
        backdrop)."""
        lay = self.layout
        gx, gy, gw, gh = lay.lib_grid
        clip = getattr(cv, "clip", None)
        if clip is not None:
            # Inflate vertically so the selection focus ring survives above/
            # below the rows; horizontal stays exact so a scrolled card never
            # bleeds past the panel's side insets.
            d = 2 * lay.fs + 2
            self._card_clip = (gx, gy - d, gw, gh + 2 * d)
            clip(*self._card_clip)
        for i, rect in self._visible():
            it = self.items[i]
            if it.get("type") in PSEUDO_TILE_TYPES:
                self._draw_pseudo_card(cv, it, rect, i == self.sel)
            else:
                self._draw_cart_card(cv, it, rect, i == self.sel, sheet_for)
        if clip is not None:
            self._card_clip = None
            clip()
        self._draw_scroll_ui(cv)

    def cover_specs(self):
        """Every (cart, div) cover this grid's NEXT full draw would request --
        each cart card's, at the size its unselected slot takes
        (`_cover_div`; selecting a card never changes it). Mirrors
        _draw_cart_card's geometry; the idle prefetch
        (CoverCache._prebuild_tick) walks it."""
        lay = self.layout
        fs = lay.fs
        band_h = max(14 * fs, 20)
        # Every CART card shares the uniform grid cell (only the pseudo MAKE
        # tile takes the tall slot) -- so the sizes never need per-index rects
        # (tile_rect is None for scrolled-out cards, which a prebuild must
        # still cover).
        cw, art_h = lay.lib_card_w, lay.lib_card_h - band_h
        out = []
        for i in range(len(self.items)):
            it = self.items[i]
            if it.get("type") in PSEUDO_TILE_TYPES or not it.get("path"):
                continue
            div = _cover_div(it, cw, art_h)
            if div:
                out.append((it, div))
        return out

    def _draw_cart_card(self, cv, it, rect, selected, sheet_for):
        """One cartridge card: cover art over the dark field, a title band, a thin
        border -- and, when selected, the focus ring plus the PLAY / CHANGE button
        row (home grid only; the picker's selected card gets just the ring)."""
        NAMES = self._NAMES
        th = self.theme or {}
        lay = self.layout
        fs = lay.fs
        x, y, w, h = rect
        band_h = max(14 * fs, 20)
        ar = self.action_rects() if selected else None
        btn_area = 0
        if ar is not None:
            btn_area = max(13 * fs, 22) + 2 * max(2 * fs, 3)
        cover_h = h - band_h - btn_area
        # The art slot, on the theme's `dim` field: the one token that contrasts
        # BOTH the home shelf's light surface and the picker's dark tool
        # backdrop in every shipped theme. In it, the cart's cover (SPEC.md 3.6,
        # Section 11.4) centred at the whole-number scale the unselected slot
        # takes, cropped to the slot (`_cover_scale`; a selected card's
        # shorter slot crops, never rescales); under 128 the cart's own icon
        # when it names one, else the cover's half (`_cover_div`). Without a
        # cover: the icon home's cell, `_art_tile`.
        cv.rect(x, y, w, cover_h, th.get("dim", NAMES["dark_blue"]))
        art_h = h - band_h
        div = _cover_div(it, w, art_h) if self.cover_for is not None else 0
        cover = self.cover_for(it, div) if div else None
        if cover is not None:
            _draw_cover(cv, cover, x, y, w, cover_h, art_h, self._card_clip)
        else:
            t = min(min(w, art_h) * 5 // 8, cover_h - 4 * fs)
            self._art_tile(cv, th, (x + (w - t) // 2, y + (cover_h - t) // 2, t, t),
                           sheet_for(it) if sheet_for is not None else None,
                           it.get("type"))
        # Title band: cream text centered on the strongest ink.
        cv.rect(x, y + cover_h, w, band_h, NAMES["black"])
        name = it["title"]
        fw = lay.font_w
        name = _ui_draw.print_fit(name, max(3 * fw, w - 4 * fs), fs)
        cv.print(name, x + (w - _ui_draw.print_width(name, fs)) // 2,
                 y + cover_h + (band_h - lay.line_h) // 2, NAMES["white"], 1)
        # PLAY / CHANGE (mockup: green PLAY, warm-light CHANGE, both dark-edged).
        if ar is not None:
            bx, by, bw, bh = ar["play"]
            cv.rect(bx, by, bw, bh, th.get("play", NAMES["green"]))
            cv.rectb(bx, by, bw, bh, NAMES["black"])
            pw = _ui_draw.print_width("PLAY", fs)
            tw = pw + 14 * fs                        # run glyph + "PLAY"
            if tw > bw - 2 * fs:                     # narrow card -> text only
                cv.print("PLAY", bx + max(fs, (bw - pw) // 2),
                         by + (bh - lay.line_h) // 2, NAMES["white"], 1)
            else:
                tx = bx + (bw - tw) // 2
                self._blit_glyph(cv, "run", (tx, by, 12 * fs, bh),
                                 NAMES["white"], fs)
                cv.print("PLAY", tx + 14 * fs, by + (bh - lay.line_h) // 2,
                         NAMES["white"], 1)
            bx, by, bw, bh = ar["change"]
            cv.rect(bx, by, bw, bh, NAMES["white"])
            cv.rectb(bx, by, bw, bh, NAMES["black"])
            label = "CHANGE"
            label = _ui_draw.print_fit(label, max(2 * fw, bw - 2 * fs), fs)
            cv.print(label, bx + max(fs, (bw - _ui_draw.print_width(label, fs)) // 2),
                     by + (bh - lay.line_h) // 2, NAMES["black"], 1)
        # Favorite star (#105): a corner badge on the SELECTED card only, mirroring
        # the pseudo card's corner-pushpin idiom rather than inventing new chrome
        # (LauncherHomeLayer.handle_pointer hit-tests the identical geometry via
        # favorite_rect, so draw and tap can't desync).
        if selected and self.favorite_for is not None:
            _draw_star_badge(cv, NAMES, self.theme or {},
                             self._favorite_badge_geom(rect),
                             bool(self.favorite_for(it)))
        self._card_frame(cv, rect, selected)

    def _draw_pseudo_card(self, cv, it, rect, selected):
        """The picker's pinned tall "+ New" card. Focus-yellow field, big plus
        glyph, a display-type heading and a small caption, and the corner pin."""
        NAMES = self._NAMES
        th = self.theme or {}
        lay = self.layout
        fs = lay.fs
        x, y, w, h = rect
        cv.rect(x, y, w, h, th.get("focus", NAMES["yellow"]))
        # Big plus in the card's upper half: the real themeable IconSheet sprite
        # when the console wired icon_for -- pal-remapped so it reads on the
        # yellow field (white outline -> black, yellow body -> orange). The
        # 12x12 glyph stays as the no-sheet fallback.
        img = self.icon_for("new") if self.icon_for is not None else None
        if img is not None:
            sc = max(fs, min((w - 12 * fs) // 16, (h // 2 - 4 * fs) // 16))
            _draw_new_icon(cv, NAMES, img, x + (w - 16 * sc) // 2,
                           y + 4 * fs + (h // 2 - 16 * sc) // 2, sc)
        else:
            gs = max(fs, min((w - 16 * fs) // 12, (h // 2 - 8 * fs) // 12))
            self._blit_glyph(cv, _TYPE_GLYPH[it["type"]],
                             (x, y + 4 * fs, w, h // 2), NAMES["black"], gs)
        # Corner pushpin (the mockup's "pinned" cue).
        px_, py_ = x + w - 8 * fs, y + 7 * fs
        cv.circ(px_, py_, 2 * fs, NAMES["black"])
        cv.line(px_, py_ + 2 * fs, px_ - 2 * fs, py_ + 5 * fs, NAMES["black"])
        # Heading (display type) + caption, centered in the lower half.
        heading = ("NEW",)
        caption = "Start a fresh project"
        ty = y + h * 11 // 20
        mult = getattr(lay, "lib_mult", 2)           # ~32px display type at any fs
        for line in heading:
            tw = _text_w(cv, line, mult)
            if tw > w - 4 * fs:                      # narrow card -> body size
                tw = _text_w(cv, line, 1)
                cv.print(line, x + (w - tw) // 2, ty, NAMES["black"], 1)
                ty += lay.line_h + 2 * fs
            else:
                _print_scaled(cv, line, x + (w - tw) // 2, ty, NAMES["black"], mult)
                ty += lay.line_h * mult + 2 * fs
        ty += 2 * fs
        for line in _ui_draw.wrap_px(caption, max(6 * lay.font_w, w - 4 * fs), fs):
            tw = _text_w(cv, line, 1)
            cv.print(line, x + (w - tw) // 2, ty, NAMES["dark_grey"], 1)
            ty += lay.line_h + 2 * fs
        self._card_frame(cv, rect, selected, ring=th.get("author", NAMES["orange"]))

    def _art_tile(self, cv, th, box, img, kind):
        """A cart's art where it has no cover: the theme's `desk.icon` tile
        in the square `box`, the icon `img` centred in it at a whole-number
        scale, else the cart's type glyph in its type colour -- the icon
        home's cell, which the shelf's and the carousel's cards draw too."""
        fs = self.layout.fs
        x, y, t, _t = box
        if th:
            _ui_draw.icon_tile(cv, th, box)
        if img is not None:
            iw, ih = img.w, img.h
            sc = max(1, (t - 8 * fs) // max(iw, ih, 1))
            cv.spr(img, x + (t - iw * sc) // 2, y + (t - ih * sc) // 2, sc)
        else:
            self._blit_glyph(cv, _TYPE_GLYPH.get(kind, "app"), box,
                             _TYPE_COLOR.get(kind, _TYPE_COLOR["app"]),
                             max(1, (t - 12 * fs) // 12))

    def _card_frame(self, cv, rect, selected, ring=None):
        """The card's thin border, plus the selection focus ring (Section 5.2:
        focus = signal yellow + a shape change, visible without hover). `ring`
        overrides the ring color where yellow would vanish (the yellow MAKE card)."""
        NAMES = self._NAMES
        th = self.theme or {}
        lay = self.layout
        fs = lay.fs
        x, y, w, h = rect
        for i in range(fs):
            cv.rectb(x - i, y - i, w + 2 * i, h + 2 * i,
                     th.get("border", NAMES["black"]))
        if selected and _ui_draw.marks_on():
            _ui_draw.focus(cv, self.theme, rect, size="card")
            _ui_draw.focus(cv, self.theme, rect, size="card", after=True)
        elif selected:
            color = ring if ring is not None else th.get("focus", NAMES["yellow"])
            for i in range(max(2, fs)):
                d = fs + 1 + i
                cv.rectb(x - d, y - d, w + 2 * d, h + 2 * d, color)

    def _draw_scroll_ui(self, cv):
        """The footer left/right nudge arrows (boxed, mockup-style; each tap =
        one card column) + the slim scrollbar along the grid's bottom edge --
        hidden entirely when every card fits the viewport. The HOME grid
        (actions on) sits on the light shelf panel, so it uses the surface ink;
        the picker sits on its dark tool backdrop, so it keeps the light
        chrome ink."""
        if self.max_scroll() <= 0:
            return
        NAMES = self._NAMES
        th = self.theme or {}
        lay = self.layout
        if self.actions:
            ink = th.get("ink", NAMES["white"])
            dim = th.get("ink_dim", NAMES["light_grey"])
        else:
            ink = NAMES["white"]
            dim = NAMES["light_grey"]
        s = self.scroll
        for rect, glyph, on in ((lay.scroll_lt, "<", s > 0),
                                (lay.scroll_rt, ">", s < self.max_scroll())):
            x, y, w, h = rect
            cv.rectb(x, y, w, h, ink if on else dim)
            cv.print(glyph, x + (w - lay.font_w) // 2,
                     y + (h - 8 * lay.fs) // 2, ink if on else dim, 1)
        self._scroll_region().draw_bar(cv, self.theme or {})


# The home's tabs (docs/shell_layout_2026-10.md §12), in tab-row order, and
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
# The handheld home (docs/look_2026-10.md step 17): each card shows its
# whole cover at a clean scale (`_clean_side`), a neighbour at half the
# cover's side and the hero at twice that, a gap between cards, the corners
# of a neighbour 7/10 of the hero's.
_HH_CARD = _COVER_SIDE // 2
_HH_GAP = 14
# The least the handheld home leaves over and under the hero's caption
# (title over metadata), at the font scale, when a face needs their room.
_HH_SQUEEZE = (2, 4)


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
# keys stay off the T-Deck keyboard's buttons (moy_kbd.c: w a s d, l, k).
_VERB_HINT = {"play": ("enter", "Play"), "open": ("enter", "Open"),
              "change": ("C", "Change"), "search": ("/", "Search"),
              "new": ("N", "New"), "copy": ("Y", "Copy"),
              "delete": ("X", "Delete"), "sure": ("X", "Sure?")}
_VERB_KEY = {"c": "change", "/": "search", "n": "new", "y": "copy", "x": "delete"}
_TAB_KEYS = ("Q E", "Tabs")
_HINT_LEAD = 8       # the margin ui.hints leads with, at the font scale
_MENU_KEYS = ("enter", "Choose", "Esc", "Close")   # the strip under the system menu
_TAB_H = 16          # the tab row's height, in chrome-scale units
_GLIDE_HALF_MS = 45.0  # the glide covers half the distance left in this long
_SLIDE_MS = 200        # the handheld's slide: 6 frames at the home's 30 fps
_VERB_LABEL = {"play": "PLAY", "open": "OPEN", "change": "CHANGE",
               "new": "NEW", "search": "SEARCH", "copy": "COPY",
               "delete": "DEL", "sure": "SURE?"}
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

    def __init__(self, items, layout=None, names=None, blit_glyph=None):
        self.motion = "full"
        self.scale = HOME_SCALE_HH
        self._glide = None            # the offset a glide is carrying the row to
        self._slide = None            # the handheld's slide: [from, ms so far]
        self._was_drag = False
        self._g_lay = None            # the layout _g was computed for
        self._g_reach = 0
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
        Launcher.__init__(self, items, layout, names, blit_glyph)

    # -- geometry ------------------------------------------------------------

    def _geom(self):
        """(tabs, band, card, pitch, ring, gap, plate_h, centre_y, foot,
        desk): the handheld's (`_geom_hh`) or the desk's (`_geom_desk`) on
        the class in force. A card is square (a cover is, SPEC.md 3.6) and
        shows its whole cover at a clean scale (`_clean_side`)."""
        lay = self.layout
        reach = _ui_draw.mark_reach()          # the centre card's focus mark
        if self._g_lay is lay and self._g_reach == reach:
            return self._g
        self._g = (self._geom_hh(lay, reach) if _lb.metric_class() == _lb.HANDHELD
                   else self._geom_desk(lay, reach))
        self._g_lay = lay
        self._g_reach = reach
        return self._g

    def _geom_hh(self, lay, reach):
        """The handheld home's geometry (docs/look_2026-10.md step 17), the
        same tuple with its foot: no tab row (the tabs are the bar's), the
        hero twice a neighbour's side, its title and its metadata under it
        and the hint strip along the content's foot. A neighbour is half the
        cover's side at the font scale where that fits, and the next clean
        side down (`_clean_side`) where it does not."""
        fs = lay.fs
        cx, cy, cw, ch = lay.cx, lay.cy, lay.cw, lay.ch
        hint_h = 20 * fs
        hints = (cx, cy + ch - hint_h, cw, hint_h)
        tf = _ui_draw.font("hero")
        tsc = max(fs, (16 * fs) // max(1, tf[2]))
        title_h = tf[2] * tsc
        meta_h = lay.line_h
        # A version 2 face's line holds its leading: the title sits 6 under
        # the hero, the metadata right under it, 10 over the hints (the
        # targets'); a pixel face's line is its ink, so 12, 3 and 4.
        above, between, below = ((6 * fs, 0, 10 * fs) if _ui_draw.font_v2(tf[0])
                                 else (12 * fs, 3 * fs, 4 * fs))
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
        band = (cx, cy, cw, hints[1] - cy)
        return (None, band, card, card + gap, ring, gap, 0, top + card,
                (title_y, title_h, tsc, meta_y, meta_h, hints), None)

    def _geom_desk(self, lay, reach):
        """The desk home's geometry (docs/look_2026-10.md step 18), the same
        tuple with its foot (the hint strip) and the desk's own: the hero at
        the top left, the details pane beside it, the shelf under them
        bleeding off the right edge, its caption over it. The desk lays out
        in glass pixels (the target's 1x), its type at whole scales of the
        face no smaller than the font scale. The hero is the largest whole
        multiple of the cover's side a quarter of the width holds with the
        shelf under it, else half the cover's side, and a shelf card is half
        the hero, so every card shows its whole cover at a clean scale; the pane
        is as tall as the hero, or as its type needs where that is more;
        what height is left goes half above the shelf's caption and half
        under the shelf. The tuple's band is the shelf's: its caption, its
        cards, their lift and their shadows."""
        fs = lay.fs
        cx, cy, cw, ch = lay.cx, lay.cy, lay.cw, lay.ch
        u = _lb.render_scale()
        hint_h = max(34 // u, 18 * fs)
        hints = (cx, cy + ch - hint_h, cw, hint_h)
        pad, top, gap = 48 // u, 24 // u, 20 // u
        ring = max(2 * fs + 2, reach)
        lh, tl = _ui_draw.font()[2], _ui_draw.font("title")[2]
        ts, ms = text_scale(40 // u, fs, "title"), text_scale(14 // u, fs)
        ks, vs = text_scale(13 // u, fs), text_scale(15 // u, fs)
        bh = max(42 // u, 20 * fs)
        need = tl * ts + lh * (ms + ks + vs) + bh + 60 // u
        cap_h = lh * ms + 8 // u
        room = hints[1] - cy - top - cap_h - 72 // u
        hero = cw // 4 // _COVER_SIDE * _COVER_SIDE
        while hero > _COVER_SIDE and max(hero, need) + hero // 2 > room:
            hero -= _COVER_SIDE
        if hero < _COVER_SIDE or max(hero, need) + hero // 2 > room:
            hero = _COVER_SIDE // 2
        shelf = hero // 2
        block = max(hero, need)
        spare = max(0, hints[1] - cy - top - block - cap_h - shelf)
        hx, hy = cx + pad, cy + top
        cap_y = hy + block + spare // 2
        shelf_y = cap_y + cap_h
        d = _ui_draw.depth()
        blur = d[9] if d[0] and d[6] else 0
        down = max(ring, blur + d[11])
        band = (cx, cap_y, cw, min(hints[1], shelf_y + shelf + down) - cap_y)
        dx = hx + hero + 32 // u
        desk = ((hx, hy, hero, hero), dx, max(0, cx + cw - pad - dx), shelf_y, pad,
                (ts, ms, ks, vs, bh, block, u))
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
            (hx, hy, hero, _h), dx, dw = dk[0], dk[1], dk[2]
            q = ((hx, hy, dx + dw - hx, max(hero, dk[5][5])), g[1], foot[5])
        self._quiet_for = g
        self._quiet_q = q
        return q
    def tabs_rect(self):
        return self._geom()[0]

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
            self._glide = None
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
        return self._region.animating or self._glide is not None

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
        self._glide = None
        self._region.stop()
        Launcher.set_items(self, items)

    def _scroll_to_sel(self):
        self._glide = None
        self._scroll = max(0, min(self._rest_of(self.sel), self.max_scroll()))

    def _scroll_region(self):
        band = self.band_rect()
        self._region.set(band, self.max_scroll() + band[2])
        if not (self._region.drag_active or self._region.animating):
            self._region.offset = self.scroll
        return self._region

    def glide_to(self, i):
        """Carry card `i` to the centre, frame by frame (`anim_frame`)."""
        n = len(self.items)
        if not n:
            return
        self._region.stop()
        i = max(0, min(n - 1, i))
        if self.desk():
            # the desk selects at once and the shelf glides to show it
            if i != self._sel:
                self.zone_gen += 1
                self._sel = i
            t = self._rest_of(i)
            self._glide = t if t != self._scroll else None
            self._slide = None
            return
        t = i * self.pitch()
        self._glide = t if t != self._scroll else None
        self._slide = [self._scroll, 0] if self._glide is not None else None

    def nav2d(self, dx, dy):
        """Left/right move the row by a card with the glide; a key held is one
        motion, each repeat pushing the target a card further."""
        if not self.items or not dx:
            return
        if self.desk():
            self.glide_to(self._sel + dx)
            return
        p = self.pitch()
        base = self._glide if self._glide is not None else self.nearest() * p
        self.glide_to(base // p + dx)

    def _snap(self):
        if not self.desk():                  # the desk's shelf rests anywhere
            self.glide_to(self.nearest())

    def anim_frame(self, dt):
        """Advance the fling or the glide one frame (dt in SECONDS), and
        settle the selection once the row is still. True when the view moved."""
        moved = False
        if self._region.animating:
            moved = Launcher.anim_frame(self, dt)
            if not self._region.animating:
                self._snap()
        elif self._glide is not None and self._slide is not None and self.handheld():
            # The handheld's slide (the 2026-10 look's motion): from where
            # the row was to the card in _SLIDE_MS, eased out, in TIME so a
            # slow board shows it in fewer frames; a key held retargets it
            # from where it is.
            sl = self._slide
            sl[1] += max(1.0, min(200.0, dt * 1000.0))
            u = min(1.0, sl[1] / _SLIDE_MS + 1e-6)
            e = 1.0 - (1.0 - u) ** 3
            to = self._glide if u >= 1.0 else sl[0] + int((self._glide - sl[0]) * e)
            moved = to != self._scroll
            self._scroll = to
            if to == self._glide:
                self._glide = None
                self._slide = None
        elif self._glide is not None:
            # An ease-out in TIME (half the distance left every
            # _GLIDE_HALF_MS), so a slow board shows the same motion in
            # fewer frames rather than a jump.
            rem = self._glide - self._scroll
            ms = max(1, min(200, int(dt * 1000)))
            step = int(rem * (1.0 - 0.5 ** (ms / _GLIDE_HALF_MS)))
            lo = min(abs(rem), 2)
            if abs(step) < lo:
                step = lo if rem > 0 else -lo
            if abs(step) > abs(rem):
                step = rem
            self._scroll += step
            moved = step != 0
            if self._scroll == self._glide:
                self._glide = None
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
            return (g[9][1], g[9][0][1], g[9][2], g[9][5][5])
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
        r = self.hero_rect() or self.tile_rect(i)
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

    def repaint_band(self, cv, sheet_for, fill):
        band = self.band_rect()
        self._fill_around_cards(cv, fill, band)
        self._paint(cv, sheet_for, band)

    def _fill_around_cards(self, cv, fill, r):
        """Restore rect `r`'s backdrop except where a card lies: every card
        paints its whole square opaque, so the backdrop is owed only around
        them -- on a big panel most of the band's pixels are the cards'."""
        x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
        x = x0
        for _i, c in sorted(self._visible(), key=lambda e: e[1][0]):
            cx0 = max(c[0], x)
            cx1 = min(c[0] + c[2], x1)
            if cx1 <= cx0:
                continue
            if cx0 > x:
                fill(cv, (x, y0, cx0 - x, y1 - y0))
            ct = max(c[1], y0)
            cb = min(c[1] + c[3], y1)
            k = min(self._corner_r(c[2]), (cb - ct) // 2)
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
        hero; 0 without one."""
        d = _ui_draw.depth()
        if not d[0]:
            return 0
        r = d[2]
        if self.desk():
            return r * 7 // 5 if side > self._geom()[2] else r
        return r if side > self._geom()[2] else r * 7 // 10

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
        apart by a dot, in the dim ink on the home's ground under a depth,
        on the bar's plate in its ink without one."""
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
        if not _ui_draw.depth()[0]:
            w = nw + 2 * sep + sc + _ui_draw.print_width(count, sc)
            cv.rect(x - sep, y - 3 * fs, w + 2 * sep, lh + 6 * fs, th.get("bar", 0))
            dim = th.get("chrome_ink_dim", 6)
        cv.print_scaled(name, x, y, dim, sc)
        x += nw + sep
        cv.rect(x, _dot_top(y, sc, sc), sc, sc, dim)
        cv.print_scaled(count, x + sc + sep, y, dim, sc)

    def _paint_details(self, cv):
        """The desk home's hero (a full paint's): its shadow, the
        selected card's picture at the hero's size and its favourite star.
        The details beside it are the home layer's (`_draw_details`)."""
        hero = self.hero_rect()
        if not self.items:
            return
        it = self.items[self.nearest()]
        _ui_draw.cast(cv, self.theme, hero, _ui_draw.CAST_CARD)
        self._draw_picture(cv, it, hero, True)
        if (self.favorite_for is not None and it.get("path")
                and it.get("type") not in PSEUDO_TILE_TYPES):
            _draw_star_badge(cv, self._NAMES, self.theme or {},
                             self._favorite_badge_geom(hero),
                             bool(self.favorite_for(it)))
        if not self._corner_r(hero[2]):
            self._card_frame(cv, hero, False)

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

    def played(self, it):
        """(times run, day of the last run or 0) of the card's play record
        (`CartManager.plays`), or None."""
        f = self.plays_for
        return None if f is None or it.get("type") in PSEUDO_TILE_TYPES else f(it)

    def today(self):
        """Today as YYYYMMDD, 0 where there is no wall clock."""
        f = self.today_for
        return 0 if f is None else f()

    def _meta(self, it):
        """The metadata of the selected card, its items: the cart's genre
        where its manifest names one (SPEC.md 3.7) and its kind where not,
        where it came from (built in, the kid's own, installed) and, where
        its play record has a day, when it was last played."""
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
        rec = self.played(it)
        words = day_words(rec[1], self.today()) if rec is not None else None
        if words is None:
            return (kind, src)
        if words in ("Today", "Yesterday"):
            words = words.lower()
        return (kind, src, "Played " + words)

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
        if not _ui_draw.depth()[0]:
            pw = min(bw, max(tw, mw) + 16 * fs)
            px, py = bx + (bw - pw) // 2, ty - 3 * fs
            ph = my + mh + 3 * fs - py
            cv.rect(px, py, pw, ph, th.get("bar", 0))
            for k in range(fs):
                cv.rectb(px - k, py - k, pw + 2 * k, ph + 2 * k, th.get("border", 0))
            ink, dim = th.get("chrome_ink", 7), th.get("chrome_ink_dim", 6)
        wall = self.wall_ink
        if wall is not None and _ui_draw.depth()[0]:
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
        dy = _dot_top(my, mh // max(1, _ui_draw.font()[2]), d)
        for pas in ((1, wall[1]), (0, dim)) if wall is not None else ((0, dim),):
            o, c = pas
            x = x0
            for k, t in enumerate(parts):
                if k:
                    cv.rect(x - (sep + d) // 2 + o, dy + o, d, d, c)
                cv.print(t, x + o, my + o, c, 1)
                x += ws_[k] + sep

    def _draw_title_tile(self, cv, it, rect, r):
        """A card for a cart with no cover (GAPS 21): the theme's tile field,
        its corners the card's, the cart's title wrapped across it."""
        th = self.theme or {}
        fs = self.layout.fs
        x, y, w, h = rect
        cv.rrect(x, y, w, h, r, th.get("surface_alt", 1),
                 stepped=bool(_ui_draw.depth()[5]))
        sc = 2 * fs if w >= _COVER_SIDE * fs else fs
        words = (it.get("title") or "").split()
        lines = []
        cur = ""
        room = w - 8 * fs
        for wd in words:
            nxt = (cur + " " + wd) if cur else wd
            if cur and _ui_draw.print_width(nxt, sc) > room:
                lines.append(cur)
                cur = wd
            else:
                cur = nxt
        if cur:
            lines.append(cur)
        lh = self.layout.line_h * sc // fs
        lines = lines[:max(1, (h - 8 * fs) // max(1, lh))]
        yy = y + (h - lh * len(lines)) // 2
        for ln in lines:
            ln = _ui_draw.print_fit(ln, room, sc)
            cv.print_scaled(ln, x + (w - _ui_draw.print_width(ln, sc)) // 2, yy,
                            th.get("ink", 7), sc)
            yy += lh

    def _draw_hh_cover(self, cv, it, rect, centre):
        """A card's picture: the whole cover scaled to the card (the
        neighbour's dimmed once and kept where the theme dims them, step 15),
        else the title tile."""
        r = self._corner_r(rect[2])
        stepped = bool(_ui_draw.depth()[5])
        cover = None
        if self.cover_for is not None:
            dim = self.dim if self.handheld() else None
            if dim is not None and not centre and self.tinted_for is not None:
                cover = self.tinted_for(it, dim[0], dim[1])
                if cover is not None:
                    cv.cover(cover.pix, cover.w, 0, 0, cover.w, cover.h, rect[0],
                             rect[1], rect[2], rect[3], r, stepped)
                    return
            cover = self.cover_for(it, _COVER_BASE)
        if cover is not None:
            cv.cover(cover.pix, cover.w, 0, 0, cover.w, cover.h, rect[0], rect[1],
                     rect[2], rect[3], r, stepped)
        else:
            self._draw_title_tile(cv, it, rect, r)

    def _draw_picture(self, cv, it, rect, centre, sheet_for=None):
        """A card's picture over its whole rect: the "+ New" card's icon on
        the focus colour, else its cover (`_draw_hh_cover`)."""
        th = self.theme or {}
        NAMES = self._NAMES
        x, y, w, h = rect
        if it.get("type") in PSEUDO_TILE_TYPES:
            cv.rect(x, y, w, h, th.get("focus", 0))
            img = self.icon_for("new") if self.icon_for is not None else None
            sc = max(1, (w * 5 // 8) // 16)
            if img is not None:
                _draw_new_icon(cv, NAMES, img, x + (w - 16 * sc) // 2,
                               y + (h - 16 * sc) // 2, sc)
            else:
                self._blit_glyph(cv, _TYPE_GLYPH[NEW_TILE_TYPE], (x, y, w, h),
                                 th.get("ink", 0), max(1, (w * 5 // 8) // 12))
        else:
            self._draw_hh_cover(cv, it, rect, centre)

    def _draw_home_card(self, cv, it, rect, centre, sheet_for):
        NAMES = self._NAMES
        th = self.theme or {}
        # The centre card at rest takes the theme's focus mark (ui.focus; a
        # lifted card draws grown and raised); a theme without marks rings it.
        marks = _ui_draw.marks_on()
        mark = centre and not self.moving and marks and not self.tab_focus
        at = rect
        if mark:
            _ink, rect = _ui_draw.focus(cv, self.theme, at, size="card",
                                        r=self._corner_r(at[2]))
        self._draw_picture(cv, it, rect, centre, sheet_for)
        if centre and self.favorite_for is not None and not self.moving \
                and not self.desk() \
                and it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES:
            _draw_star_badge(cv, NAMES, self.theme or {},
                             self._favorite_badge_geom(rect),
                             bool(self.favorite_for(it)))
        ring = th.get("author", 0) if it.get("type") in PSEUDO_TILE_TYPES \
            else None
        if not self._corner_r(rect[2]):
            self._card_frame(cv, rect, centre and not marks, ring=ring)
        if mark:
            _ui_draw.focus(cv, self.theme, at, size="card", r=self._corner_r(at[2]),
                           after=True)


class _IconCells:
    """The icon home's cells in the grid's viewport (`Layout.lib_grid`): a
    square tile with its label band under it, `rows` cells a column, the
    columns spread to fill the viewport's width. It stands in as the
    `IconHome`'s layout -- the shelf's cell names (`lib_card_w`, `lib_step`,
    `tile_rect`...) answer the cells, every other name the layout it was
    built from -- so the shelf's selection, scroll and drag bodies run on it
    unchanged. One per Layout (`IconHome.set_layout` keeps it on the layout),
    so a layout swap never rebuilds one."""

    tile_cell = _Layout.tile_cell
    tile_index = _Layout.tile_index

    def __init__(self, lay):
        self._lay = lay
        fs = lay.fs
        gx, gy, gw, gh = lay.lib_grid
        gap = lay.lib_gap
        ring = 2 * fs + 2
        self.label_dy = ring + fs          # the label band's top under the tile
        self.label_h = 13 * fs             # the desk's label band
        under = self.label_dy + self.label_h
        target = max(40 * fs, lay.cw // 10)
        rows = max(1, (gh + gap) // (target + under + gap))
        tile = max(16, (gh - (rows - 1) * gap) // rows - under)
        cols = max(1, (gw + gap) // (tile + gap))
        cgap = (gw - cols * tile) // (cols - 1) if cols > 1 else gap
        # The names the shelf's bodies read, answered here directly (a miss
        # goes through __getattr__ to the layout).
        self.fs = fs
        self.cs = lay.cs
        self.cw = lay.cw
        self.font_w = lay.font_w
        self.line_h = lay.line_h
        self.lib_panel = lay.lib_panel
        self.lib_header_h = lay.lib_header_h
        self.scroll_lt = lay.scroll_lt
        self.scroll_rt = lay.scroll_rt
        self.lib_grid = lay.lib_grid
        self.lib_gap = gap                 # between rows
        self.rows = rows
        self.cols = cols
        self.tile = tile
        self.lib_card_w = tile
        self.lib_card_h = tile + under
        self.lib_step = tile + cgap        # one column
        self.lib_card_actions = False      # the verbs ride the bar's zone

    def __getattr__(self, name):
        return getattr(self._lay, name)

    def tile_rect(self, i, scroll=0, featured=False):
        """Cell `i`'s rect (tile and label band) at `scroll`, or None when
        it and the selected label's reach lie wholly outside the viewport."""
        gx, gy, gw, _gh = self.lib_grid
        rows = self.rows
        x = gx + (i // rows) * self.lib_step - scroll
        w = self.lib_card_w
        half = self.lib_step // 2          # the selected cell's label reach
        if x + w + half <= gx or x - half >= gx + gw:
            return None
        h = self.lib_card_h
        return (x, gy + (i % rows) * (h + self.lib_gap), w, h)

    def grid_content_w(self, n, featured=False):
        if n <= 0:
            return 0
        return (n - 1) // self.rows * self.lib_step + self.lib_card_w


class IconHome(Launcher):
    """The home's icon arrangement (docs/shell_layout_2026-10.md §12, the
    layout's `home: icons`): the grid arrangement with one-tile cards -- its
    panel, its tab row, its selection, scroll, drag and verbs -- where each
    cell is the cart's icon (the cover's reduction, else its sheet's tile,
    `CoverCache.icon_sheet_for`'s order) in the theme's `desk.icon` tile, no
    title under it, and the focused cell's name in the desk's label pill
    (`ui.icon_label`). "+ New" is a cell like any other: no tall card."""

    arrangement = "icons"

    def set_layout(self, layout):
        cells = getattr(layout, "_icon_cells", None)
        if cells is None:
            cells = _IconCells(layout)
            layout._icon_cells = cells
        Launcher.set_layout(self, cells)

    def _featured(self):
        return False

    def _label_band(self, rect):
        """The selected cell's label band: under its tile, half a column
        wider on each side, so a name longer than the tile still reads."""
        lay = self.layout
        x, y, w, _h = rect
        half = lay.lib_step // 2
        return (x - half, y + lay.tile + lay.label_dy, w + 2 * half, lay.label_h)

    def _sel_reach(self, rect, x0, y0, x1, y1):
        b = self._label_band(rect)
        return (min(x0, b[0]), y0, max(x1, b[0] + b[2]), max(y1, b[1] + b[3]))

    def cover_specs(self):
        """The (cart, div) covers the next full draw requests: each cart's
        ICON reduction, where the cart names no icon of its own."""
        out = []
        for it in self.items:
            if (it.get("path") and not it.get("icon")
                    and it.get("type") not in PSEUDO_TILE_TYPES):
                out.append((it, _COVER_ICON))
        return out

    def _icon_of(self, it, sheet_for):
        if it.get("type") in PSEUDO_TILE_TYPES:
            return self.icon_for("new") if self.icon_for is not None else None
        img = None
        if self.cover_for is not None and not it.get("icon"):
            img = self.cover_for(it, _COVER_ICON)
        if img is None and sheet_for is not None:
            img = sheet_for(it)
        return img

    def _draw_cart_card(self, cv, it, rect, selected, sheet_for):
        """One cell: the tile, the icon centred in it at a whole-number scale
        (or the type glyph), and on the selected one its focus ring, the
        favourite star and the name's pill."""
        th = self.theme
        lay = self.layout
        fs = lay.fs
        x, y, w, _h = rect
        t = lay.tile
        box = (x, y, t, t)
        self._art_tile(cv, th, box, self._icon_of(it, sheet_for), it.get("type"))
        if not selected:
            return
        if _ui_draw.marks_on():
            _ui_draw.focus(cv, th, box, size="card")
            _ui_draw.focus(cv, th, box, size="card", after=True)
        else:
            for i in range(max(2, fs)):
                d = fs + 1 + i
                cv.rectb(x - d, y - d, t + 2 * d, t + 2 * d, th["focus"])
        if self.favorite_for is not None and it.get("path") \
                and it.get("type") not in PSEUDO_TILE_TYPES:
            _draw_star_badge(cv, self._NAMES, self.theme or {},
                             self._favorite_badge_geom(rect),
                             bool(self.favorite_for(it)))
        name = "NEW" if it.get("type") in PSEUDO_TILE_TYPES else it.get("title", "")
        _ui_draw.icon_label(cv, th, self._label_band(rect), name, True, fs)

    def _draw_pseudo_card(self, cv, it, rect, selected):
        self._draw_cart_card(cv, it, rect, selected, None)


def _wall_ink(ws):
    """(white, black) where the home lies on a wallpaper -- a cart or a
    picture, pixels the theme does not know -- under a theme with a depth,
    else None: its text is then white over a black shadow, as the desk's
    labels are (docs/look_2026-10.md steps 25 and 28)."""
    if ws.look.wallpaper_id is None or not _ui_draw.depth()[0]:
        return None
    black, white = ws.look.live.fixed[2:]
    return white, black


def _dim_of(ws):
    """The theme's neighbour dimming (`dim_neighbours`, Moy Night's): the
    colour the carousel's neighbours are dimmed toward -- the home's ground,
    `home_bg` or the `desktop` fill -- and its alpha (0..255), or None."""
    look = getattr(getattr(ws, "look", None), "live", None)
    if look is None:
        return None
    lk = look.look[0]
    pct = lk.get("dim_neighbours", 0)
    if not pct or pct < 0:
        return None
    c = lk.get("home_bg", _UNSET)
    if c == _UNSET:
        c = ws.theme_colors["desktop"]
    return (c, (pct * 255 + 50) // 100)


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
    """The grid layers' drag fast path (#113, #58/#66), ONE body for the home
    shelf and the Editor picker: while a touch drag or fling scrolls `grid`
    and every retained framebuffer already holds this frame's static chrome
    (two prior full paints under an unchanged statics key -- the ping-pong
    stale-by-2 rule), repaint ONLY the shelf band and the bar strip. The
    band is blit-SHIFTED when the ring proves the target buffer holds this
    shelf's pixels at a known offset (same sel/statics/covers, consecutive
    paints) and only the exposed strip + the stale cursor stamp are repainted;
    otherwise the whole band. Everything outside the band (wallpaper, panel
    chrome, dot grid) is byte-identical in the target buffer and left alone;
    full paints resume on release, so any straggler is erased within a frame.

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
    the active tab's tap (ws.launch_selected). Owns the trackball hover state
    (_lhover), the tab-row focus and the armed DEL; `grid` is the single source
    it reads. `HomeWindowLayer` is the same home in a window."""

    id = "launcher"
    domain = "system"
    strip = "home"            # the bar strip it draws and taps (BarLayer's `where`)
    searchable = True         # Games carries SEARCH (the query is the home's)

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        self._lhover = (-1, -1)       # the last pointer sample (a release is not a move)
        self._tab_focus = False       # keys drive the tab row (up from the carousel)
        self._delete = ConfirmTap()   # Make's DEL: the first tap arms, the second deletes
        self._delete_path = None      # ...the card it was armed on
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
        # ~150ms wallpaper + panel + cards + bar rebuild. NOT captured on idle
        # frames, though that would be free: the P4 root ping-pongs (DPI
        # num_fbs=2), so after present the canvas's CURRENT buffer holds the
        # frame BEFORE last -- an idle capture can cache the previous screen's
        # pixels under this frame's key.
        self._lib_cache = None        # cv.new_layer sized to the canvas
        self._lib_key = None          # _retained_key at capture
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

    @property
    def grid(self):
        """The card model this home draws: ws.launcher, whose class is the
        layout's arrangement."""
        return self.ws.launcher

    @property
    def _tab_focus(self):
        return self._tab_keys

    @_tab_focus.setter
    def _tab_focus(self, on):
        # one focus mark a screen: the tab row's takes it from the cards
        if on != getattr(self, "_tab_keys", None):
            self._zone_bump = getattr(self, "_zone_bump", 0) + 1
        self._tab_keys = on
        g = getattr(self.ws, "launcher", None)
        if g is not None:
            g.tab_focus = on

    def _statics_key(self, cv):
        """Everything the home frame's STATIC chrome (wallpaper backdrop +
        Library panel fill/header/footer) is a pure function of: the shared
        base plus the wallpaper and the search field. A key change forces
        full paints until the streak re-arms."""
        ws = self.ws
        return _statics_base(ws, cv, self.grid) + (
            ws.look.wallpaper_id,
            getattr(ws, "search_query", ""), getattr(ws, "search_typing", False),
            self.grid.tab, self._tab_focus, self.grid.arrangement,
            self.grid.statics_sel())

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
        # A RECORDING canvas (the wasm head's CommandCanvas root) can't replay
        # a framebuffer capture -- its stream would blit a layer no command
        # ever defined. Two probes: the recording marker (begin_surface, same
        # probe frame() uses) and the retention contract (recording canvases
        # declare RETAINED_FRAMES = 0 for exactly this reason; a canvas that
        # retains nothing can't be captured from either).
        if (getattr(cv, "begin_surface", None) is not None
                or getattr(cv, "RETAINED_FRAMES", 0) < 1):
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
        if (getattr(cv, "begin_surface", None) is not None
                or getattr(cv, "RETAINED_FRAMES", 0) < 1):
            return
        cache = self._lib_cache
        try:
            if cache is None or cache.w != cv.w or cache.h != cv.h:
                self._lib_cache = cv.new_layer(cv.w, cv.h)
                self._lib_key = None      # freshly minted: no captured pixels
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
        if self._lib_unsupported or ws._animating(dt):
            return
        if (getattr(cv, "begin_surface", None) is not None
                or getattr(cv, "RETAINED_FRAMES", 0) < 1):
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

    def _capture_retained(self, cv, dt):
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
        if (getattr(cv, "begin_surface", None) is not None
                or getattr(cv, "RETAINED_FRAMES", 0) < 1):
            return              # recording/non-retaining canvas: a framebuffer
                                # copy is not a recordable op (see
                                # _try_stamp_retained); the raster side would
                                # diverge from the recorded stream
        key = self._retained_key(cv)
        if key == self._lib_key:
            return
        try:
            cache = self._lib_cache
            if cache is None or cache.w != cv.w or cache.h != cv.h:
                cache = self._lib_cache = cv.new_layer(cv.w, cv.h)
            ws.note_cost("home.frame.capture")
            cache.blit_strip(cv, 0, 0)
            self._lib_key = key
        except Exception:  # noqa: BLE001 -- a recording root has no pixels to
            self._lib_cache = None     # copy; forfeit the mechanism for the
            self._lib_key = None       # session rather than retry every paint
            self._lib_unsupported = True

    def _carousel(self):
        """Whether the home shows the carousel (else the grid arrangement)."""
        return isinstance(self.grid, Carousel)

    def _surface_fill(self, cv, r):
        """The grid arrangement's band backdrop: its panel's surface."""
        cv.rect(r[0], r[1], r[2], r[3], self.ws.theme_colors["surface"])

    def _try_drag_partial(self, cv, dt):
        """The home's moving frame (`_drag_partial`), timed for the home
        diag's HITCH split. The carousel's only once the band's backdrop holds
        this frame's wallpaper; the grid's band lies on its panel."""
        if not self._carousel():
            return _drag_partial(self, cv, dt, self.grid, self.id,
                                 self._surface_fill, self.strip, timed=True)
        if self._band_bg is None or self._band_key != self._band_key_of(cv):
            return False
        return _drag_partial(self, cv, dt, self.grid, self.id,
                             self._backdrop_fill, self.strip, timed=True)

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
        # The board's motion verdict (a WM constant, §12's degradation order).
        wm = ws.wm
        carousel = self._carousel()
        if carousel:
            self.grid.motion = getattr(wm, "HOME_MOTION", "full")
            self.grid.scale = getattr(wm, "HOME_SCALE", HOME_SCALE_HH)
            self.grid.dim = _dim_of(ws)
            self.grid.wall_ink = _wall_ink(ws)
        # Kinetic fling (#113) and the glide to the centre card: advance the
        # row BEFORE painting, and keep the redraw gate open until it rests.
        # Both ride the partial path (its gate includes flinging).
        self.grid.anim_frame(dt)
        if self.grid.flinging:
            ws.request_frame()
        if self._try_drag_partial(cv, dt):    # drag-scroll fast path (#58/#66)
            return
        # Retained-frame stamp (2026-07-27): a re-entry whose pixels are provably
        # identical to the captured frame presents it in one blit. Bookkeeping
        # matches a full paint exactly -- pixel-wise it IS one -- so the scroll
        # ring and the drag partial's streak stay honest.
        if self._try_stamp_retained(cv, dt):
            self.grid._scroll_region().note_painted(
                ws._frames_drawn,
                (self.grid.paint_key(), self._statics_key(cv), ws.covers.gen),
                _cursor_stamp(ws))
            _advance_streak(self, cv)
            return
        # #76 sub-surface marks: on a RECORDING canvas, partition the home into
        # wallpaper / grid / bar streams so the web delta can skip the static grid +
        # bar while a live wallpaper animates underneath (they were one "launcher"
        # surface -- the wallpaper's motion re-shipped everything each frame). The
        # marks are positional slices of the same flat stream: replayed in order the
        # pixels are identical, and on the RAW canvas _surf is None (zero cost).
        _surf = getattr(cv, "begin_surface", None)
        # perf_capture (#66 instrument-before-cutting): time the home frame's
        # three sections so a slow launcher frame names its eater (PERF's
        # home= field carries ws._pf_home -- wallpaper vs grid vs bar).
        _t0 = _ticks_ms() if getattr(ws, "perf_capture", False) else None
        ws.wallpaper.draw(dt, home=True, quiet=self._quiet(ws, carousel))
        if carousel:
            self._capture_band_backdrop(cv, dt)
        else:
            self._draw_grid_panel(cv)
        _t1 = _ticks_ms() if _t0 is not None else None
        if _surf is not None:
            _surf("home-grid", "system")
        # The tab row and the carousel's band (docs/shell_layout_2026-10.md
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
        if _surf is not None:
            _surf("home-bar", "system")
        ws.bar_layer._draw_status_strip("home")
        if _t0 is not None:
            _t3 = _ticks_ms()
            ws._pf_home = (_ticks_diff(_t1, _t0), _ticks_diff(_t2, _t1),
                           _ticks_diff(_t3, _t2))
        # A FULL paint landed in the current framebuffer.
        _advance_streak(self, cv)
        self._capture_retained(cv, dt)   # retain this frame for re-entry stamps

    def _hh(self):
        """The carousel home (docs/look_2026-10.md steps 17 and 18), the
        handheld's or the desk's: its tabs in the bar's zone and its verbs a
        hint strip along its foot."""
        return self._carousel()

    def tab_rects(self):
        """[(tab id, rect, labels_on)] of the home's tab row; on the
        handheld, of its tabs in the bar's zone (`zone_tabs`), on the
        glass."""
        if self._hh():
            got = _shell_layout.cell_of(self.ws.layout.bars, "zone")
            if got is None:
                return []
            rx, ry = self.ws.layout.bars[got[0]][1][:2]
            zx, zy, zw, zh = got[1]
            return [(tid, (rx + r[0], ry + r[1], r[2], r[3]), True)
                    for tid, _l, r in self.zone_tabs((zx, zy, zw, zh))]
        fs = self.ws.layout.fs
        return _ui_draw.tab_row_rects(self.grid.tabs_rect(), HOME_TABS, fs)

    def _draw_grid_panel(self, cv):
        """The grid arrangement's framed panel: the surface the shelf's cards
        lie on, the tab row in its header band, the active tab's count in its
        footer between thin rules (the scroll arrows are the grid's)."""
        ws = self.ws
        th = ws.theme_colors
        lay = ws.layout
        fs = lay.fs
        x, y, w, h = lay.lib_panel
        cv.rect(x, y, w, h, th["surface"])
        for i in range(fs):
            cv.rectb(x - i, y - i, w + 2 * i, h + 2 * i, th["border"])
        label = ws.home_title(self.grid)
        tw = _ui_draw.print_width(label, fs)
        ty = y + h - lay.lib_footer_h + (lay.lib_footer_h - lay.line_h) // 2
        tx = x + (w - tw) // 2
        cv.print(label, tx, ty, th["ink_dim"], 1)
        ly = ty + 4 * fs
        lx0 = lay.scroll_lt[0] + lay.scroll_lt[2] + 10 * fs
        lx1 = lay.scroll_rt[0] - 10 * fs
        cv.rect(lx0, ly, max(0, tx - 8 * fs - lx0), fs, th["ink_dim"])
        cv.rect(tx + tw + 8 * fs, ly, max(0, lx1 - (tx + tw + 8 * fs)), fs,
                th["ink_dim"])

    def _draw_home_chrome(self, cv):
        """The tab row on its plate (with the search query beside it while one
        is held). The carousel's band has no backdrop of its own: the cards
        lie on the wallpaper. On the handheld, the hint strip instead."""
        if self._hh():
            return self._draw_hints(cv)
        ws = self.ws
        th = ws.theme_colors
        lay = ws.layout
        fs = lay.fs
        grid = self.grid
        rects = self.tab_rects()
        tx, ty, tw, tht = grid.tabs_rect()
        p = 2 * fs
        x1 = rects[-1][1][0] + rects[-1][1][2] if rects else tx
        cv.rect(tx - p, ty - p, x1 - tx + 2 * p, tht + 2 * p, th["surface"])
        for k in range(fs):
            cv.rectb(tx - p - k, ty - p - k, x1 - tx + 2 * (p + k),
                     tht + 2 * (p + k), th["border"])
        tabs = [(tid, label, _TAB_ICON[tid]) for tid, label in HOME_TABS]
        _ui_draw.tab_row(cv, th, grid.tabs_rect(), tabs, grid.tab,
                    icon_for=getattr(ws, "_icon_image_keyed", None), ink=th["ink"])
        if self._tab_focus:
            for tid, r, _on in rects:
                if tid == grid.tab and _ui_draw.marks_on():
                    _ui_draw.focus(cv, th, r, fs=fs)
                elif tid == grid.tab:
                    for k in range(max(2, fs)):
                        d = fs + k
                        cv.rectb(r[0] - d, r[1] - d, r[2] + 2 * d, r[3] + 2 * d,
                                 th["focus"])
        query = getattr(ws, "search_query", "")
        typing = getattr(ws, "search_typing", False)
        if self.searchable and (query or typing):
            label = "SEARCH: " + query + ("_" if typing else "")
            sx = x1 + 4 * p
            label = _ui_draw.print_fit(label, max(6 * lay.font_w, tx + tw - sx - p), fs)
            w = _ui_draw.print_width(label, fs) + 2 * p
            cv.rect(sx - p, ty - p, w + p, tht + 2 * p, th["surface"])
            cv.print(label, sx, ty + (tht - lay.line_h) // 2, th["ink"], 1)

    def _desk_verbs(self):
        """The desk home's buttons (step 18): the active tab's verbs but
        SEARCH, the hint strip's, with COPY beside CHANGE. None off the
        desk."""
        if not (self._carousel() and self.grid.desk()):
            return ()
        out = [v for v in self.zone_verbs() if v != "search"]
        if "change" in out and "copy" not in out:
            out.insert(out.index("change") + 1, "copy")
        return out

    def _desk_buttons(self):
        """[(verb, rect, kind)] of the details pane's buttons along the hero's
        foot, the first a primary where it plays, opens or makes, DEL a
        danger, as many as the pane holds."""
        g = self.grid
        _hx, hy, _hs, _ = g.hero_rect()
        dk = g._geom()[9]
        dx, dw = dk[1], dk[2]
        fs = self.ws.layout.fs
        h, block, u = dk[5][4], dk[5][5], dk[5][6]
        y = hy + block - 6 // u - h
        x = dx
        out = []
        for k, v in enumerate(self._desk_verbs()):
            label = _VERB_HINT[v][1]
            if k == 0 and v in ("play", "open", "new"):
                kind = "primary"
            elif v in ("delete", "sure"):
                kind = "danger"
            else:
                kind = "normal"
            w = max((148 if kind == "primary" else 112) // u,
                    _ui_draw.print_width(label, fs) + 56 // u + 16 * fs)
            if x + w > dx + dw:
                break
            out.append((v, (x, y, w, h), kind))
            x += w + 14 // u
        return out

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

    def _quiet(self, ws, carousel):
        """The home's quiet rectangles for its wallpaper: the bars and, on a
        carousel, its own (`Carousel.quiet`); kept while both are the same
        objects, so a live wallpaper's frame builds nothing."""
        bars = ws.wallpaper.bars()
        own = self.grid.quiet() if carousel else ()
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
        fs = ws.layout.fs
        hy = g.hero_rect()[1]
        dk = g._geom()[9]
        dx, dw = dk[1], dk[2]
        ts, ms, ks, vs, bh, block, u = dk[5]
        ink, dim = th["ink"], th["ink_dim"]
        if not _ui_draw.depth()[0]:
            # a legacy ground is any colour: the pane on the bar's plate
            p = 12 // u
            cv.rect(dx - p, hy, dw + 2 * p, block, th["bar"])
            for k in range(fs):
                cv.rectb(dx - p - k, hy - k, dw + 2 * (p + k), block + 2 * k, th["border"])
            ink, dim = th["chrome_ink"], th["chrome_ink_dim"]
            dx += p
            dw -= 2 * p
        name = it.get("title") or ""
        if it.get("type") == NEW_TILE_TYPE:
            name = "New"
        ty = hy + 4 // u
        was = _ui_draw.print_font(_ui_draw.font("title")[0])
        cv.print_scaled(_ui_draw.print_fit(name, dw, ts), dx, ty, ink, ts)
        if was is not None:
            _ui_draw.print_font(was)
        lh = _ui_draw.font()[2]
        my = ty + _ui_draw.font("title")[2] * ts + 14 // u
        sep = 8 // u
        x = dx
        for k, t in enumerate(g._meta(it)):
            if k:
                cv.rect(x, _dot_top(my, ms, ms), ms, ms, dim)
                x += ms + sep
            cv.print_scaled(t, x, my, dim, ms)
            x += _ui_draw.print_width(t, ms) + sep
        buttons = self._desk_buttons()
        by = hy + block - 6 // u - bh
        if it.get("type") not in PSEUDO_TILE_TYPES:
            vy = by - 16 // u - lh * vs
            ky = vy - 6 // u - lh * ks
            # the manifest's description (SPEC.md 3.7) in the room between
            # the metadata and the facts, as many lines as fit
            gap = 10 // u
            room = (ky - gap - (my + lh * ms + gap)) // (lh * ms)
            text = it.get("description")
            if text and room > 0:
                lines = list(_ui_draw.wrap_px(text, dw, ms))
                if len(lines) > room:
                    lines = lines[:room]
                    lines[-1] = _ui_draw.print_fit(lines[-1], dw - _ui_draw.print_width("...", ms), ms) + "..."
                dy = my + lh * ms + gap
                for line in lines:
                    cv.print_scaled(line, dx, dy, dim, ms)
                    dy += lh * ms
            col = 150 // u
            for k, (key, val) in enumerate(self._facts(it)):
                fx = dx + k * col
                cv.print_scaled(key, fx, ky, dim, ks)
                cv.print_scaled(_ui_draw.print_fit(val, col - 10 // u, vs), fx, vy, ink, vs)
        for v, r, kind in buttons:
            _ui_draw.button(cv, th, r, _VERB_HINT[v][1], kind=kind, app="home",
                            glyph="run" if v in ("play", "open") else None)

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
        selected card, then the tab keys; and the verb of each pair."""
        items = []
        verbs = []
        for v in self.zone_verbs():
            k, words = _VERB_HINT[v]
            items.append(k)
            items.append(words)
            verbs.append(v)
        return items, verbs

    def _hint_spans(self, cv):
        """[(verb or "tabs", x0, x1)] across the hint strip, and the strip's
        rect: what `_draw_hints` draws and a tap hits."""
        th = self.ws.theme_colors
        rect = self.grid.foot()[5]
        desk = self.grid.desk()
        items, verbs = self._hint_groups()
        out = []
        if self.searchable and (getattr(self.ws, "search_query", "")
                                or getattr(self.ws, "search_typing", False)):
            items = ()
        inner = self._hint_inner(rect)
        if items:
            for v, (x0, x1) in zip(verbs, _ui_draw.hints(cv, th, inner, items, desk,
                                                          draw=False)):
                out.append((v, x0, x1))
        sp = _ui_draw.hints(cv, th, inner, _TAB_KEYS, desk, draw=False)
        if sp:
            dx = rect[0] + rect[2] - self._hint_pad() - sp[0][1]
            if not out or sp[0][0] + dx > out[-1][2]:
                out.append(("tabs", sp[0][0] + dx, sp[0][1] + dx))
        return out, rect

    def _draw_hints(self, cv):
        """The home's hint strip (steps 17 and 18): keycaps naming the
        active tab's verbs on the selected card, from the left, and the tab
        keys at the right (the theme's `keycap`, `ui.hints`'s part). While
        a search query is held it shows in the verbs' place."""
        th = self.ws.theme_colors
        wall = _wall_ink(self.ws)
        if wall is not None:
            th = self._wall_theme(th, wall[0])
        if self.ws.menu_ui.looked():
            # the system menu's card is open over the home: its keys
            rect = self.grid.foot()[5]
            _ui_draw.hints(cv, th, self._hint_inner(rect), _MENU_KEYS, self.grid.desk())
            return
        spans, rect = self._hint_spans(cv)
        items, verbs = self._hint_groups()
        n = sum(1 for v, _a, _b in spans if v != "tabs")
        query = getattr(self.ws, "search_query", "")
        typing = getattr(self.ws, "search_typing", False)
        if self.searchable and (query or typing):
            n = 0
        if not _ui_draw.depth()[0]:
            # a legacy ground is any colour: the strip on the bar's band
            x, y, w, h = rect
            cv.rect(x, y, w, h, th["bar"])
            cv.rect(x, y, w, self.ws.layout.fs, th["border"])
        desk = self.grid.desk()
        pad = self._hint_pad()
        if n:
            _ui_draw.hints(cv, th, self._hint_inner(rect), items[:2 * n], desk)
        elif self.searchable and (query or typing):
            fs = self.ws.layout.fs
            label = "SEARCH: " + query + ("_" if typing else "")
            label = _ui_draw.print_fit(label, rect[2] // 2, fs)
            ink = th["ink"] if _ui_draw.depth()[0] else th["chrome_ink"]
            cv.print(label, rect[0] + pad,
                     rect[1] + (rect[3] - self.ws.layout.line_h) // 2, ink, 1)
        for v, x0, _x1 in spans:
            if v == "tabs":
                left = x0 - (rect[0] + _HINT_LEAD * self.ws.layout.fs)
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

    def _hint_pad(self):
        """The hint strip's margin at either end: the handheld's is the one
        `ui.hints` leads with, the desk's wider."""
        fs = self.ws.layout.fs
        if self.grid.desk():
            return max(24 // _lb.render_scale(), _HINT_LEAD * fs)
        return _HINT_LEAD * fs

    def _hint_inner(self, rect):
        """The strip `ui.hints` draws in for the margin `_hint_pad`."""
        e = self._hint_pad() - _HINT_LEAD * self.ws.layout.fs
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
        C, /, N, Y and X the verbs it names, Q and E the tabs."""
        k = self.ws.input.last_key
        prev = self._kprev
        self._kprev = k
        if not k or k == prev or not (32 < k < 127):
            return False
        ch = chr(k).lower()
        if ch in ("q", "e"):
            self.ws.step_home_tab(-1 if ch == "q" else 1, self.grid)
            return True
        v = _VERB_KEY.get(ch)
        if v == "delete" and self._armed():
            v = "sure"
        if v is None or not (v in self.zone_verbs() or v in self._desk_verbs()):
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
        if k and k != self._search_kprev:
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
        # Konami Easter egg (#21): watch every button press on the home desktop
        # for the secret sequence (the nav below still runs normally -- the egg
        # is a passive observer, so it never blocks the launcher).
        for _b in ws.ach_ui._KONAMI:
            if i.pressed(_b):
                ws.ach_ui._konami_step(_b)
                break
        if self._tab_focus:
            # The tab row (up from the carousel): left/right switch tabs,
            # down or enter returns to the row.
            if i.pressed("left"):
                ws.step_home_tab(-1, self.grid)
            if i.pressed("right"):
                ws.step_home_tab(1, self.grid)
            if i.pressed("down") or i.pressed("a") or i.pressed("run"):
                self._tab_focus = False
                ws.mark_dirty()
            if i.pressed("b") and ws.ach_ui._konami_pos == 0:
                self._tab_focus = False
                ws.mark_dirty()
            elif i.pressed("home"):
                ws.home_key()
            return True
        if self._hh() and self._hint_keys(i):
            return True
        # The carousel (§12): left/right glide by a card, a held key one
        # motion; up takes the keys to the tab row. The grid: the arrows
        # step its cells, and up from its top row takes them to the tabs.
        grid = self.grid
        if i.pressed("left"):
            grid.nav2d(-1, 0)
            ws.mark_dirty()
        if i.pressed("right"):
            grid.nav2d(1, 0)
            ws.mark_dirty()
        if i.pressed("up"):
            if self._carousel() or self._grid_top_row():
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
        if i.pressed("code"):
            # CHANGE (visual identity v1 Section 1.2): open the selected cartridge
            # in place in the Studio/Editor, landing on Config.
            ws.defer(self._change)
        if i.pressed("b") and ws.ach_ui._konami_pos == 0:
            self._back()                     # (a B inside the Konami code is the code's)
        elif i.pressed("home"):
            ws.home_key()
        return True

    def _back(self):
        """B: the home's X, from a keyboard -- where the X shows."""
        if self.ws.bar_layer._show_x(self.strip):
            self.ws.open_desk()

    # The doors out of the home; each leaves DEL disarmed behind it.
    def _launch(self):
        self._delete.disarm()
        self.ws.launch_selected(self.grid)

    def _change(self):
        self._delete.disarm()
        self.ws.change_selected(self.grid)

    def _new(self):
        self._delete.disarm()
        self.ws.new_cart_and_edit(self.grid)

    def _grid_top_row(self):
        grid = self.grid
        if not grid.items:
            return True
        return grid.layout.tile_cell(grid.sel, grid._featured())[0] == 0

    def _grid_pointer(self, px, py, click):
        """The grid arrangement's pointer: the footer arrows and the selected
        card's star fire on the press; a card fires the active tab's tap on a
        clean RELEASE (ui.DragTap), so a drag never launches; the trackball's
        cursor selects the card it moves onto."""
        ws = self.ws
        grid = self.grid
        lay = ws.layout
        down = ws.pointer.down
        if click:
            if grid.max_scroll() > 0 and _in(px, py, lay.scroll_lt):
                grid.scroll_cols(-1)
                return True
            if grid.max_scroll() > 0 and _in(px, py, lay.scroll_rt):
                grid.scroll_cols(1)
                return True
        i = grid.pointer_frame(px, py, click, down, dt_ms=ws._pointer_dt_ms)
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
        if click:
            if ws.bar_layer.handle_bar_tap(self.strip, px, py):
                return True
            if self._hh() and self._hint_tap(px, py):
                return True
            for tid, r, _on in self.tab_rects():
                if _in(px, py, r):
                    self._tab_focus = False
                    ws.set_home_tab(tid, grid)
                    return True
            frect = grid.favorite_rect(grid.sel)
            if frect is not None and _in(px, py, frect):
                ws.carts.toggle_favorite(grid.selected())
                return True
            if self._carousel() and grid.desk() and self._details_tap(px, py):
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

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    @property
    def zone_gen(self):
        """Proxy onto the grid's zone_gen -- the GRID owns sel/items (the state
        that actually varies the zone's pixels) -- plus the DEL guard's
        generation, so BarLayer's strip cache repaints the armed prompt."""
        return self.grid.zone_gen + self._delete.gen + self._zone_bump

    def _armed(self):
        """Whether DEL is armed on the selected card."""
        sel = self.grid.selected()
        return (self._delete.armed and sel is not None
                and sel.get("path") == self._delete_path)

    def zone_verbs(self):
        """The active tab's verbs on the selected card (§12's table): Games
        PLAY, CHANGE, SEARCH; Mine PLAY, CHANGE; Apps OPEN; Make NEW, CHANGE,
        COPY, DEL. CHANGE, COPY and DEL need a real cart and a store that
        manages projects; the "+ New" card's verb is NEW. DEL reads SURE?
        once armed. SEARCH is the fullscreen home's alone."""
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
            return ("new", "change", "copy",
                    "sure" if self._armed() else "delete")
        if sel is not None and sel.get("type") == NEW_TILE_TYPE:
            return ("new",)
        out = ("play", "change") if change else (("play",) if real else ())
        if tab == "games" and self.searchable:
            out = out + ("search",)
        return out

    def zone_tabs(self, rect):
        """[(tab id, label, rect)] of the home's tabs in the bar's zone
        `rect` (steps 17 and 18), flush left, as many as fit."""
        cs = self.ws.layout.cs
        sc, h, pad, gap = self._zone_tab_metrics()
        h = min(rect[3] - 2 * cs, h)
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
        the handheld's at the font scale, the desk's a line about 15 glass
        pixels tall on a 28-pixel plate."""
        lay = self.ws.layout
        fs = lay.fs
        if self.grid.desk():
            u = _lb.render_scale()
            sc = text_scale(15 // u, fs)
            return (sc, max(28 // u, _ui_draw.font()[2] * sc + 6 * fs),
                    max(12 // u, 4 * fs), max(6 // u, 2 * fs))
        return fs, lay.line_h + 8 * fs, 6 * fs, 2 * fs

    def _draw_zone_tabs(self, cv, rect):
        """The tabs in the bar (step 17): the active one under the current
        mark (a theme without marks: its selection), the rest in the dim
        chrome ink, the focus mark on the active one while the keys are the
        tab row's."""
        ws = self.ws
        th = ws.theme_colors
        fs = ws.layout.fs
        marks = _ui_draw.marks_on()
        sc, _h, pad, _gap = self._zone_tab_metrics()
        for tid, label, r in self.zone_tabs(rect):
            on = tid == self.grid.tab
            ink = th["chrome_ink_dim"]
            if on and marks:
                got = _ui_draw.current(cv, th, r, r=5 * fs, fs=fs)
                ink = th["chrome_ink"] if got is None else got
            elif on:
                cv.rect(r[0], r[1], r[2], r[3], th["selection"])
                ink = th["selection_ink"]
            if on and self._tab_focus:
                if marks:
                    _ui_draw.focus(cv, th, r, fs=fs)
                else:
                    for k in range(max(2, fs)):
                        cv.rectb(r[0] - k, r[1] - k, r[2] + 2 * k, r[3] + 2 * k,
                                 th["focus"])
            if sc == fs:
                # the line centred on the plate, as the bar's clock is on the bar,
                # so the two share a baseline
                cv.print(label, r[0] + pad, r[1] + (r[3] - ws.layout.line_h) // 2, ink, 1)
            else:
                lh = _ui_draw.font()[2] * sc
                cv.print_scaled(label, r[0] + pad, r[1] + (r[3] - lh) // 2, ink, sc)

    def _zone_action_rects(self, rect):
        """{verb: rect} of the verb chips in the lent bar zone, flush left in
        it, as many as fit; on the carousel home, of the hint strip's pairs
        (`rect` unread), where its verbs are."""
        ws = self.ws
        if self._hh():
            spans, hr = self._hint_spans(ws.sys_canvas)
            return {v: (x0, hr[1], x1 - x0, hr[3]) for v, x0, x1 in spans if v != "tabs"}
        lay = ws.layout
        fs, cs = lay.fs, lay.cs
        x = rect[0] + 2 * cs
        y = rect[1] + cs
        h = max(lay.line_h + 4 * fs, rect[3] - 3 * cs)
        out = {}
        for verb in self.zone_verbs():
            w = _ui_draw.print_width(_VERB_LABEL[verb], fs) + 6 * fs
            if x + w > rect[0] + rect[2]:
                break
            out[verb] = (x, y, w, h)
            x += w + 4 * cs
        return out

    def draw_zone(self, cv, rect):
        """The home's lent left zone: the active tab's verb chips on the
        selected card (the grid and icon arrangements). On the carousel
        home, the tabs."""
        if self._hh():
            return self._draw_zone_tabs(cv, rect)
        ws = self.ws
        NAMES = self._NAMES
        th = ws.theme_colors
        lay = ws.layout
        fill = {"play": th["play"], "open": th["play"], "change": th["author"],
                "new": th["author"], "search": NAMES["light_grey"],
                "copy": th["author"], "delete": NAMES["light_grey"],
                "sure": th["danger"]}
        for verb, (x, y, w, h) in self._zone_action_rects(rect).items():
            cv.rect(x, y, w, h, fill[verb])
            cv.rectb(x, y, w, h, NAMES["black"])
            cv.print(_VERB_LABEL[verb], x + 3 * lay.fs, y + (h - lay.line_h) // 2,
                     NAMES["black"], 1)

    def zone_tap(self, px, py, rect=None):
        """The zone's verb chips (the carousel home's tabs); anything else
        falls through."""
        ws = self.ws
        if self._hh():
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
        if verb != "sure":
            self._delete.disarm()
        if verb in ("play", "open"):
            ws.defer(self._launch)   # #184: start behind the paint
        elif verb == "change":
            ws.defer(self._change)
        elif verb == "new":
            ws.defer(self._new)
        elif verb == "copy":
            ws.carts.dup(self.grid.selected())
        elif verb == "delete":
            self._delete_path = self.grid.selected().get("path")
            self._delete.arm()
        elif verb == "sure":
            self._delete.disarm()
            ws.carts.delete(self.grid.selected())
        else:
            ws.toggle_search()
        ws.mark_dirty()


class HomeWindowLayer(LauncherHomeLayer):
    """The "projects" content Layer: the home in a desktop-mode window, the
    desk's PROJECTS (docs/shell_layout_2026-10.md §12). The same tabs,
    sources, taps and verbs over its own card model, ws.projects_grid, always
    the `grid` arrangement whatever the fullscreen home's, opened on Make.

    A window is a tool space: the panel field around the grid's own panel,
    never the wallpaper (the desk shows its own around the window), so nothing
    in it animates and an idle window draws nothing. The WM retains the
    window's buffer, so it keeps no retained frame of its own; a drag rides
    the shared `_drag_partial` and the WM's content-gesture path."""

    id = "projects"
    strip = "projects"
    searchable = False

    @property
    def grid(self):
        return self.ws.projects_grid

    def _back(self):
        """B closes this window alone, as its title strip's X does."""
        self.ws.wm.close_window_kind(self.id)

    def prealloc_retained(self):
        return

    def release_retained(self):
        return False

    def draw(self, dt):
        ws = self.ws
        cv = ws.sys_canvas
        grid = self.grid
        _paint_continuity(self, ws)           # foreign paints void the buffers
        grid.anim_frame(dt)
        if grid.flinging:
            ws.request_frame()
        if self._try_drag_partial(cv, dt):    # drag-scroll fast path (#113)
            return
        cv.cls(ws.theme_colors["panel"])
        self._draw_grid_panel(cv)
        self._draw_home_chrome(cv)
        grid.draw(cv, ws.covers.sheet_icon)
        grid._scroll_region().note_painted(
            ws._frames_drawn,
            (grid.paint_key(), self._statics_key(cv), ws.covers.gen),
            _cursor_stamp(ws))
        ws.bar_layer._draw_status_strip(self.strip)
        _advance_streak(self, cv)
