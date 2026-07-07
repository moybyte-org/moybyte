"""The desktop home / launcher (#28), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/shell_layers_refactor_v1.md (Move 1b,
the last surface). Three classes:

  * `Launcher`     -- the cart icon GRID model + draw: items/sel/paging/nav + the
                      tile rendering. Two instances: ws.launcher (the home RUN-grid)
                      and ws.picker (the Editor's PROJECT-PICKER grid); both reuse the
                      same class + tile draw. This file just holds the class.
  * `LauncherHomeLayer` -- the "launcher" content Layer: the home composition
                      (wallpaper backdrop -> icon grid -> top bar) + the grid nav /
                      selection input. It reaches everything through self.ws and
                      dispatches a cart open to ws.launch_selected() (tap = RUN, or the
                      pinned Make tile -> the Editor project-picker).
  * `EditorPickerLayer` -- the "picker" content Layer (spec shell_ux_v1.md): the SAME
                      grid look over ws.picker (every editable cart + a "+ New" tile);
                      picking a cart opens it in the Editor (ws.pick_selected).

Boundary (single source of truth): ws.launcher (the instance) + ws.open() (open the
selected cart -- lifecycle, pinned) + the cart store stay on Workstation. The Launcher
CLASS needs the palette + the shared glyph blitter for its tile art -- `NAMES` and
`_blit_glyph` are INJECTED at construction (the established pattern; `_blit_glyph` is the
one shared toolkit fn, like bar_layer takes `_in`), and the launcher-only tile-type maps
`_TYPE_GLYPH`/`_TYPE_COLOR` live here. `_in` is duplicated (pure/trivial). LauncherHomeLayer
takes NAMES + `_in` injected too. No circular import: this is a leaf (the only console
touch is a lazy Layout fallback for a bare Launcher() that no caller ever constructs).

Stage 4 (#46 zoned bar, docs/shell_ux_technical_plan_v1.md): `LauncherHomeLayer` grows
`draw_zone`/`zone_tap` (the lent left zone -- originally NEW/DUP/DEL + the selected
cart's name, the old where=="home" branch of BarLayer._draw_status_strip) and a
`zone_gen` property proxying `Launcher.zone_gen` -- an int the GRID bumps whenever its
`sel` (a property now) or `items` changes, so BarLayer's strip cache can trust one int
instead of re-deriving "did the selection move" itself.

Cart management moves off the launcher home (docs/shell_ux_v1.md's "Editor-as-an-app"
model: the launcher is for PLAYING, the Editor picker is for MANAGING projects): the
launcher's lent zone now only shows the selected cart's name. NEW/DUP/DEL move to
`EditorPickerLayer`'s zone (a later stage).
"""


def _in(px, py, rect):
    x, y, w, h = rect
    return x <= px < x + w and y <= py < y + h


# The launcher's pinned "Make" tile + the picker's pinned "+ New" tile are PSEUDO-
# entries (not real carts): a plain dict with a marker `type` + a title, flowing
# through the SAME grid (nav/sel/tile_at) as real carts, dispatched by their type at
# tap time. `path` is None so cart management (dup/del) + the icon-sheet loader skip
# them, and a fresh dict per call avoids aliasing one shared mutable tile.
MAKE_TILE_TYPE = "make"          # launcher slot 0: tap -> Editor project-picker
NEW_TILE_TYPE = "new"            # picker slot 0: tap -> create a game + open the Editor
PSEUDO_TILE_TYPES = (MAKE_TILE_TYPE, NEW_TILE_TYPE)


def make_tile():
    return {"type": MAKE_TILE_TYPE, "title": "Make", "path": None}


def new_tile():
    return {"type": NEW_TILE_TYPE, "title": "New", "path": None}


# Per-type icon glyph + art-box color for a cart tile on the desktop (the pre-literate
# cue), used when a cart has no sprite of its own. The two pseudo tiles get a
# distinctive bright box + a clear glyph (pencil = Make, plus = New).
_TYPE_GLYPH = {"wallpaper": "paint", "game": "run", "app": "app", "tool": "gear",
               MAKE_TILE_TYPE: "edit", NEW_TILE_TYPE: "plus"}
_TYPE_COLOR = {"wallpaper": 12, "game": 8, "app": 11, "tool": 9,
               MAKE_TILE_TYPE: 10, NEW_TILE_TYPE: 14}  # index by type


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
        # Stage 4 (#46 zoned bar): bumped whenever anything the lent left zone
        # shows (the selected cart's title) changes -- the `sel` property below
        # covers nav/tap/hover; set_items bumps it too (a NEW/DUP/DEL/rename can
        # change the title under an unchanged sel). Must be set BEFORE `self.sel`
        # below, since the sel setter reads it. BarLayer folds this into its cache
        # key so the zoned strip re-renders only on a real change.
        self.zone_gen = 0
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

    @property
    def sel(self):
        return self._sel

    @sel.setter
    def sel(self, value):
        # Stage 4 (#46 zoned bar): bump zone_gen on every ACTUAL change, regardless
        # of call site (nav2d / flip_page / a tap / trackball hover all assign
        # `.sel` directly) -- this is what lets BarLayer's cache key trust
        # `zone_gen` instead of re-deriving "did the selection move" itself.
        if value != getattr(self, "_sel", None):
            self.zone_gen += 1
        self._sel = value

    def set_items(self, items):
        """Replace the cart list (after a create/duplicate/delete) and re-clamp the
        selection + page so neither dangles past the new end. The public re-sync
        entry point -- callers must not poke the private page bookkeeping."""
        self.items = items
        self.zone_gen += 1          # a rename/new/dup/del can change the title text
                                    # the lent zone shows even when sel is unchanged
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
            ws.launch_selected()             # launcher tap = RUN the selected cart
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
                ws.launch_selected()         # launcher tap = RUN the selected cart
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

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    @property
    def zone_gen(self):
        """Proxy onto ws.launcher.zone_gen -- the launcher GRID owns sel/items
        (the state that actually varies the zone's pixels), so this just gives
        BarLayer a uniform `owner.zone_gen` regardless of which app owns a zone."""
        return self.ws.launcher.zone_gen

    def draw_zone(self, cv, rect):
        """The launcher's lent left zone: just the selected cart's name (or empty
        when there is none) -- cart management (create/copy/delete) moved to the
        Editor picker's zone (docs/shell_ux_v1.md: the launcher is for PLAYING, the
        picker is for MANAGING projects), so NEW/DUP/DEL no longer draw here. Starts
        flush at the zone's left edge now that nothing else shares the space."""
        ws = self.ws
        NAMES = self._NAMES
        lay = ws.layout
        sel = ws.launcher.selected()
        if sel is not None:
            name = sel["title"]
            maxc = max(4, rect[2] // lay.font_w)
            if len(name) > maxc:
                name = name[:maxc]
            cv.print(name, rect[0] + 2, 3, NAMES["white"], 1)

    def zone_tap(self, px, py, rect=None):
        """The launcher's lent left zone is display-only now (just the selected
        cart's name) -- NEW/DUP/DEL moved to EditorPickerLayer.zone_tap, so there is
        nothing left to claim here."""
        return False


class EditorPickerLayer:
    """The Editor's PROJECT-PICKER content Layer (spec shell_ux_v1.md, the Editor's
    entry state when no project is chosen). It reuses the launcher's cart-grid look --
    the SAME `Launcher` tile rendering, over `ws.picker` (a second grid instance whose
    items are the "+ New" pseudo tile followed by every EDITABLE cart: games, tools,
    apps, wallpapers, built-ins). Picking a cart opens it in the Editor (ws.pick_selected
    -> ws.open_in_editor); the "+ New" tile creates a game + opens it. Exit (the bar X /
    B) pops back to the launcher; the Editor's own "projects" affordance returns HERE.

    Mirrors LauncherHomeLayer (grid nav + trackball hover + page chevrons) but dispatches
    to the picker verbs instead of RUN, and lends the bar a "PICK A PROJECT" title (or
    DUP/DEL, see below). Owns the trackball hover state; ws.picker is the single source
    of truth for items/selection.

    Cart management lives HERE now (docs/shell_ux_v1.md: the launcher is for PLAYING,
    the picker is for MANAGING projects) -- DUP/DEL act on the picker's SELECTED cart
    via the lent zone (ws.dup_cart/ws.del_cart, which read `ws.picker`'s selection
    instead of the launcher's -- see console.py). "+ New" was already picker-only (the
    pinned grid tile)."""

    id = "picker"
    domain = "system"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._phover = (-1, -1)       # trackball hover pos (like LauncherHomeLayer._lhover)

    def draw(self, dt):
        NAMES = self._NAMES
        ws = self.ws
        ws.wallpaper.draw(dt)
        cv = ws.sys_canvas
        lay = ws.layout
        ws.picker.draw(cv, ws._icon_sheet_for)
        if ws.picker.max_page() > 0:
            if ws.picker.page > 0:
                px, py = lay.page_prev[0], lay.page_prev[1]
                cv.print("<", px + 3, py + 8, NAMES["white"], 2)
            if ws.picker.page < ws.picker.max_page():
                px, py = lay.page_next[0], lay.page_next[1]
                cv.print(">", px + 3, py + 8, NAMES["white"], 2)
        ws.bar_layer._draw_status_strip("picker")

    def handle_input(self, i):
        ws = self.ws
        if i.pressed("left"):
            ws.picker.nav2d(-1, 0)
        if i.pressed("right"):
            ws.picker.nav2d(1, 0)
        if i.pressed("up"):
            ws.picker.nav2d(0, -1)
        if i.pressed("down"):
            ws.picker.nav2d(0, 1)
        if i.pressed("a") or i.pressed("run"):
            ws.pick_selected()               # open the picked cart in the Editor (or + New)
        if i.pressed("b") or i.pressed("home") or i.pressed("stop"):
            ws.exit()                        # back to the launcher root
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click:
            if ws.bar_layer.handle_bar_tap("picker", px, py):   # clock/≡/wifi/X + lent zone
                return True
            lay = ws.layout
            if ws.picker.max_page() > 0 and self._in(px, py, lay.page_prev):
                ws.picker.flip_page(-1); return True
            if ws.picker.max_page() > 0 and self._in(px, py, lay.page_next):
                ws.picker.flip_page(1); return True
            i = ws.picker.tile_at(px, py)
            if i is not None:
                ws.picker.sel = i
                ws.pick_selected()
                return True
        # Trackball hover (no click): preview the tile the cursor moved onto (mirrors
        # LauncherHomeLayer -- seed to the live pos so the first centered frame isn't a move).
        if self._phover == (-1, -1):
            self._phover = (px, py)
        elif (px, py) != self._phover:
            self._phover = (px, py)
            i = ws.picker.tile_at(px, py)
            if i is not None:
                ws.picker.sel = i
        return True

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    @property
    def zone_gen(self):
        """Proxy onto ws.picker.zone_gen -- the picker GRID owns sel/items, so this
        gives BarLayer a uniform owner.zone_gen (same trick LauncherHomeLayer uses)."""
        return self.ws.picker.zone_gen

    def draw_zone(self, cv, rect):
        """The picker's lent left zone: DUP/DEL icons over the picker's SELECTED cart
        (when can_manage and a real cart -- not the pinned "+ New" tile -- is picked),
        then a "PICK A PROJECT" title -- the picker is the one place a kid creates/
        copies/deletes/edits a project (docs/shell_ux_v1.md)."""
        ws = self.ws
        NAMES = self._NAMES
        lay = ws.layout
        real = ws._real_selected(ws.picker)
        if ws.can_manage and real is not None:
            ws._icon("dup", lay.dup_btn[0], lay.dup_btn[1], cv)
            ws._icon("del", lay.del_btn[0], lay.del_btn[1], cv)
        cv.print("PICK A PROJECT", lay.status_name_x, 3, NAMES["white"], 1)

    def zone_tap(self, px, py, rect=None):
        """The picker's lent left-zone tap slice: DUP (copy) + DEL (delete) act on the
        picker's SELECTED cart. No-op when read-only or the selection is the pinned
        "+ New" tile (there is no real cart to act on)."""
        ws = self.ws
        lay = ws.layout
        real = ws._real_selected(ws.picker)
        if ws.can_manage and real is not None and self._in(px, py, lay.dup_btn):
            ws.dup_cart(); return True
        if ws.can_manage and real is not None and self._in(px, py, lay.del_btn):
            ws.del_cart(); return True
        return False
