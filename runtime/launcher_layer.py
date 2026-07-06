"""The desktop home / launcher (#28), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/shell_layers_refactor_v1.md (Move 1b,
the last surface). Two classes:

  * `Launcher`     -- the cart icon GRID model + draw: items/sel/paging/nav + the
                      tile rendering. The single instance is ws.launcher (built by
                      Workstation, the single source everything reads via
                      ws.launcher.items/sel/selected()); this file just holds its class.
  * `LauncherHomeLayer` -- the "launcher" content Layer: the home composition
                      (wallpaper backdrop -> icon grid -> top bar) + the grid nav /
                      selection input. It reaches everything through self.ws and
                      dispatches a cart open to ws.open().

Boundary (single source of truth): ws.launcher (the instance) + ws.open() (open the
selected cart -- lifecycle, pinned) + the cart store stay on Workstation. The Launcher
CLASS needs the palette + the shared glyph blitter for its tile art -- `NAMES` and
`_blit_glyph` are INJECTED at construction (the established pattern; `_blit_glyph` is the
one shared toolkit fn, like bar_layer takes `_in`), and the launcher-only tile-type maps
`_TYPE_GLYPH`/`_TYPE_COLOR` live here. `_in` is duplicated (pure/trivial). LauncherHomeLayer
takes NAMES + `_in` injected too. No circular import: this is a leaf (the only console
touch is a lazy Layout fallback for a bare Launcher() that no caller ever constructs).
"""


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


# Per-type icon glyph + art-box color for a cart tile on the desktop (the pre-literate
# cue), used when a cart has no sprite of its own.
_TYPE_GLYPH = {"wallpaper": "paint", "game": "run", "app": "app", "tool": "gear"}
_TYPE_COLOR = {"wallpaper": 12, "game": 8, "app": 11, "tool": 9}  # index by type


class Launcher:
    """The desktop home (#28): carts laid out as a PAGED GRID of tappable icon
    tiles over the wallpaper backdrop, instead of a flat vertical strip. Keeps the
    selection model (items/sel/selected/move) the rest of the console relies on;
    `page`/PAGE is the grid's scroll unit (one screen of COLS x ROWS icons).

    The grid geometry comes from an injected `Layout` (#39) so it reflows with the
    system canvas size + font scale; COLS/ROWS/PAGE are instance attributes mirrored
    from the live layout (so callers reading them, and the selection/paging model,
    track the reflowed grid). A bare Launcher(items) (unit construction) falls back
    to the 320x240 / scale-1 baseline -- exactly today's 4x2/PAGE=8 grid.

    `names` (the palette) + `blit_glyph` (the shared glyph blitter) are injected so the
    tile draw stays free of a console back-import."""

    def __init__(self, items, layout=None, names=None, blit_glyph=None):
        self.items = items
        self.sel = 0
        self.page = 0
        self._NAMES = names
        self._blit_glyph = blit_glyph
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
        """Adopt a new grid layout (size/font-scale change) and re-clamp the page so
        the selection stays on a valid screen. Mirrors COLS/ROWS/PAGE as instance
        attributes for the callers/tests that read them directly."""
        self.layout = layout
        self.COLS = layout.cols
        self.ROWS = layout.rows
        self.PAGE = layout.page
        self._clamp_page()

    # -- selection ----------------------------------------------------------
    def set_items(self, items):
        """Replace the cart list (after a create/duplicate/delete) and re-clamp the
        selection + page so neither dangles past the new end. The public re-sync
        entry point -- callers must not poke the private page bookkeeping."""
        self.items = items
        if self.sel >= len(items):
            self.sel = max(0, len(items) - 1)
        self._page_to_sel()

    def nav2d(self, dx, dy):
        """Grid navigation: dx steps a column, dy steps a row. Clamped within the
        list (no wrap) so arrow nav feels like a real grid."""
        n = len(self.items)
        if not n:
            return
        step = dx + dy * self.COLS
        self.sel = max(0, min(n - 1, self.sel + step))
        self._page_to_sel()

    def _page_to_sel(self):
        self.page = self.sel // self.PAGE
        self._clamp_page()

    def max_page(self):
        n = len(self.items)
        return max(0, (n - 1) // self.PAGE) if n else 0

    def _clamp_page(self):
        self.page = max(0, min(self.max_page(), self.page))

    def flip_page(self, d):
        """Page the grid by d screens (chevron tap), moving the selection onto the
        first tile of the new page so keyboard nav continues from there."""
        self.page = max(0, min(self.max_page(), self.page + d))
        first = self.page * self.PAGE
        if self.items and not (first <= self.sel < first + self.PAGE):
            self.sel = min(len(self.items) - 1, first)

    def selected(self):
        return self.items[self.sel] if self.items else None

    def _page_range(self):
        start = self.page * self.PAGE
        return range(start, min(len(self.items), start + self.PAGE))

    def tile_rect(self, i):
        """The grid-cell rect for cart index i, or None if it's not on the current
        page. Cells lay out left-to-right, top-to-bottom in the icon area (geometry
        from the live Layout, so it reflows with the system canvas / font scale)."""
        return self.layout.tile_rect(i, self.page)

    def tile_at(self, px, py):
        for i in self._page_range():
            r = self.tile_rect(i)
            if r and _in(px, py, r):
                return i
        return None

    def draw(self, cv, sheet_for=None):
        # Icon tiles only -- the wallpaper backdrop + status strip + dock are drawn
        # by the Workstation around this (so the wallpaper shows through). For each
        # cart: a rounded art box (its sprite tile 0 if it has one, else a type
        # glyph), the selection ring, and a short name beneath. All geometry scales
        # with the layout (font scale), so a bigger panel shows bigger tiles (#39).
        NAMES = self._NAMES
        lay = self.layout
        box = lay.icon_box
        fw = lay.font_w                              # on-screen char-cell width (8*fs)
        spr_scale = max(1, box // 16)                # fit the 16x16 icon sprite in the box
        for i in self._page_range():
            x, y, w, h = self.tile_rect(i)
            it = self.items[i]
            sel = (i == self.sel)
            bx = x + (w - box) // 2
            by = y + 2
            cv.rect(bx, by, box, box, NAMES["dark_purple"])
            cv.rectb(bx, by, box, box,
                     NAMES["yellow"] if sel else NAMES["dark_grey"])
            img = sheet_for(it) if sheet_for is not None else None
            if img is not None:
                cv.spr(img, bx + (box - 16 * spr_scale) // 2,
                       by + (box - 16 * spr_scale) // 2, spr_scale)
            else:
                self._tile_glyph(cv, it, (bx, by, box, box))
            # short name (one line, truncated to the tile width: fw-wide cells)
            name = it["title"]
            maxc = w // fw
            if len(name) > maxc:
                name = name[:maxc]
            cv.print(name, x + (w - len(name) * fw) // 2, by + box + 3,
                     NAMES["white"] if sel else NAMES["light_grey"], 1)

    def _tile_glyph(self, cv, it, box):
        # A type-colored art box with a centered type glyph, for carts with no
        # sprite. Uses the injected shared glyph blitter (host == device).
        NAMES = self._NAMES
        x, y, w, h = box
        cv.rect(x + 6, y + 6, w - 12, h - 12, _TYPE_COLOR.get(it["type"], NAMES["indigo"]))
        self._blit_glyph(cv, _TYPE_GLYPH.get(it["type"], "app"), box, NAMES["black"])


class LauncherHomeLayer:
    """The "launcher" content Layer (system domain): the home desktop. draw composes
    the wallpaper backdrop -> the cart icon grid (ws.launcher) -> the top bar; input is
    the grid nav / selection, dispatching an open to ws.open(). Owns only the trackball
    hover state (_lhover); ws.launcher is the single source it reads."""

    id = "launcher"
    domain = "system"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._lhover = (-1, -1)       # last cursor pos used for desktop icon hover-highlight

    def draw(self, dt):
        """The home desktop: wallpaper backdrop -> cart icon grid -> top status
        strip. The wallpaper is drawn first and the rest layer over it, exactly the
        Picotron model (wallpaper shows through the chrome). All on the SYSTEM canvas,
        reflowed to its size + font scale (#39).

        The bottom in-cart tool dock is NOT drawn here (#46): on the launcher the
        code/draw/map/run slots have no cart to act on, so the dock was a dead row.
        It returns the moment a cart is open (the in-cart top-bar buttons / Settings'
        dock). Settings stays reachable via the gear button in the status strip; the
        cart grid reclaims the freed bottom band (Layout.grid_bottom)."""
        NAMES = self._NAMES
        ws = self.ws
        ws.wallpaper.draw(dt)
        cv = ws.sys_canvas
        lay = ws.layout
        ws.launcher.draw(cv, ws._icon_sheet_for)
        # page chevrons when more than one page of carts
        if ws.launcher.max_page() > 0:
            if ws.launcher.page > 0:
                px, py = lay.page_prev[0], lay.page_prev[1]
                cv.print("<", px + 3, py + 8, NAMES["white"], 2)
            if ws.launcher.page < ws.launcher.max_page():
                px, py = lay.page_next[0], lay.page_next[1]
                cv.print(">", px + 3, py + 8, NAMES["white"], 2)
        ws.bar_layer._draw_status_strip("home")

    def handle_input(self, i):
        ws = self.ws
        # Konami Easter egg (#21): watch every button press on the home desktop
        # for the secret sequence (the nav below still runs normally -- the egg
        # is a passive observer, so it never blocks the launcher).
        for _b in ws.ach_ui._KONAMI:
            if i.pressed(_b):
                ws.ach_ui._konami_step(_b)
                break
        # Grid nav (#28): left/right step a column, up/down a whole row.
        if i.pressed("left"):
            ws.launcher.nav2d(-1, 0)
        if i.pressed("right"):
            ws.launcher.nav2d(1, 0)
        if i.pressed("up"):
            ws.launcher.nav2d(0, -1)
        if i.pressed("down"):
            ws.launcher.nav2d(0, 1)
        if i.pressed("a") or i.pressed("run"):
            ws.open()
        return True

    def handle_pointer(self, px, py, click):
        # Desktop home (#28): a tap on a cart icon opens it; the gear + management
        # row + page chevrons fire on the press edge. There's no list drag anymore --
        # the grid pages instead. Trackball hover still previews the icon under it.
        # The bottom in-cart dock is no longer drawn on the launcher (#46), so it's not
        # hit-tested here; Settings is reached via the gear in the status strip.
        ws = self.ws
        if click:
            # The top-bar tap slice (clock egg / ≡ / NEW / DUP / DEL) is owned by the
            # bar surface now (#46 BarLayer); it also runs the clock-run reset for any
            # non-clock tap, so page/tile taps that fall through stay byte-identical.
            if ws.bar_layer.handle_home_tap(px, py):
                return True
            lay = ws.layout
            if ws.launcher.max_page() > 0 and self._in(px, py, lay.page_prev):
                ws.launcher.flip_page(-1); return True
            if ws.launcher.max_page() > 0 and self._in(px, py, lay.page_next):
                ws.launcher.flip_page(1); return True
            i = ws.launcher.tile_at(px, py)
            if i is not None:
                ws.launcher.sel = i
                ws.open()
                return True
        # Trackball cursor hover (no click): highlight the icon the cursor MOVED
        # onto. Only when the position actually changed frame-to-frame, so a
        # parked cursor doesn't fight keyboard nav. _lhover seeds to the live
        # pointer position on the first frame so the initial centered cursor isn't
        # treated as a move (which would clobber the first arrow step).
        if self._lhover == (-1, -1):
            self._lhover = (px, py)
        elif (px, py) != self._lhover:
            self._lhover = (px, py)
            i = ws.launcher.tile_at(px, py)
            if i is not None:
                ws.launcher.sel = i
        return True
