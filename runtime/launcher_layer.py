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


try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
_in = _ui.rect_in   # one hit-test (ui.rect_in)


# Display-type helpers for the Library shelf (visual identity v1's library-concept
# mockup): block-scaled petme128 headings that render identically on every canvas.
try:
    from chrome import _print_scaled, _text_w
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _print_scaled, _text_w


def _wrap_words(text, maxc):
    """Greedy word-wrap of `text` into lines of at most `maxc` chars (an
    over-long single word gets its own truncated line)."""
    lines = []
    cur = ""
    for word in str(text).split():
        cand = word if not cur else cur + " " + word
        if len(cand) <= maxc:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = word[:maxc]
    if cur:
        lines.append(cur)
    return lines


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
               "story": "run",
               MAKE_TILE_TYPE: "edit", NEW_TILE_TYPE: "plus"}
_TYPE_COLOR = {"wallpaper": 12, "game": 8, "app": 11, "tool": 9,
               "story": 15,     # a kid's Storybook cart (#78): peach, playable
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
        # Visual identity v1 (docs/visual_identity_v1.md Section 1.2): the HOME grid
        # exposes PLAY/CHANGE on the selected card on the desktop-density tiers
        # (console sets this True on ws.launcher). The picker keeps it False -- a
        # pick has ONE meaning there (open in the Editor).
        self.actions = False
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
        self.theme = None             # chrome THEME tokens (ws.set_theme pushes them);
                                      # None -> the yellow default accent
        self.icon_for = None          # optional kind -> 16x16 IconSheet Image (console
                                      # wires ws._bar_image) -- the shelf's pseudo cards
                                      # draw the real themeable pencil/plus icon big
        self.cover_for = None         # optional (cart, w, h) -> full-bleed cover
                                      # blittable (visual identity v1 Section 11.4)
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
        list (no wrap) so arrow nav feels like a real grid. On the shelf tiers
        (non-base) slot 0 spans both rows (the tall featured card), so horizontal
        nav walks the linear order and vertical nav hops between the two card
        rows -- the tall card itself has no vertical neighbor."""
        n = len(self.items)
        if not n:
            return
        if self.layout._base:
            step = dx + dy * self.COLS
            self.sel = max(0, min(n - 1, self.sel + step))
            self._page_to_sel()
            return
        cols = self.COLS
        base = self.page * self.PAGE
        k = self.sel - base
        if dx:
            self.sel = max(0, min(n - 1, self.sel + dx))
        elif dy > 0 and 1 <= k <= cols - 1:            # row 0 -> row 1
            nk = k + (cols - 1)
            if base + nk < n:
                self.sel = base + nk
        elif dy < 0 and k >= cols:                     # row 1 -> row 0
            self.sel = base + (k - (cols - 1))
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

    def action_rects(self):
        """The selected card's PLAY / CHANGE button rects as {"play": r, "change": r}
        (visual identity v1 Sections 1.2/6.1: the selected cartridge exposes the two
        verbs; primary activation still always plays). The mockup's in-card row along
        the card's bottom edge, under the title band. DESKTOP-density tiers only:
        returns None on the 320x240 baseline (the lent bar zone carries the verbs
        there -- LauncherHomeLayer.draw_zone), when `actions` is off (the picker),
        for a pseudo tile (Make has one verb, its tap), or when the selection is off
        the current page. Draw and hit-test both read this, so they can't desync."""
        lay = self.layout
        if lay._base or not self.actions or not self.items:
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

    def draw(self, cv, sheet_for=None):
        # Icon tiles only -- the wallpaper backdrop + status strip + dock are drawn
        # by the Workstation around this (so the wallpaper shows through). The
        # 320x240 baseline keeps the frozen tile look; the desktop-density tiers
        # render the Library SHELF cards (visual identity v1's library mockup).
        NAMES = self._NAMES
        lay = self.layout
        if not lay._base:
            self._draw_shelf(cv, sheet_for)
            return
        box = lay.icon_box
        fw = lay.font_w                              # on-screen char-cell width (8*fs)
        spr_scale = max(1, box // 16)                # fit the 16x16 icon sprite in the box
        # The selection accent follows the panel THEME when one is pushed (Settings
        # -> THEME); the default is the frozen yellow (byte-identical baseline).
        acc = (self.theme or {}).get("accent", NAMES["yellow"])
        for i in self._page_range():
            x, y, w, h = self.tile_rect(i)
            it = self.items[i]
            sel = (i == self.sel)
            bx = x + (w - box) // 2
            by = y + 2
            cv.rect(bx, by, box, box, NAMES["dark_purple"])
            cv.rectb(bx, by, box, box,
                     acc if sel else NAMES["dark_grey"])
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
            nx = x + (w - len(name) * fw) // 2
            ny = by + box + 3
            cv.print(name, nx, ny,
                     NAMES["white"] if sel else NAMES["light_grey"], 1)

    # -- the Library shelf cards (visual identity v1, desktop density) ----------

    def _draw_shelf(self, cv, sheet_for):
        """The mockup's card grid: the tall featured slot (MAKE STUDIO / +New / a
        featured cart) + cover-art cartridge cards, plus the footer pager arrows.
        The framed panel around this is the home layer's (the picker draws the same
        cards over its own tool backdrop)."""
        for i in self._page_range():
            rect = self.tile_rect(i)
            it = self.items[i]
            if it.get("type") in PSEUDO_TILE_TYPES:
                self._draw_pseudo_card(cv, it, rect, i == self.sel)
            else:
                self._draw_cart_card(cv, it, rect, i == self.sel, sheet_for)
        if self.max_page() > 0:
            self._draw_pager(cv)

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
        # Cover: authored images/cover.moyimg art FULL-BLEED when the cart has
        # one (Section 11.4), else the cart's own sprite scaled up on the dark
        # field, else its type glyph in the type color (both the deterministic
        # fallback). The field is the theme's `dim` tint: the one token that
        # contrasts BOTH the home shelf's light surface and the picker's dark
        # tool backdrop in every shipped theme.
        cover = self.cover_for(it, w, cover_h) if self.cover_for is not None else None
        if cover is not None:
            cv.spr(cover, x, y, 1)
        else:
            cv.rect(x, y, w, cover_h, th.get("dim", NAMES["dark_blue"]))
            img = sheet_for(it) if sheet_for is not None else None
            if img is not None:
                sc = max(1, min((w - 6 * fs) // 16, (cover_h - 4 * fs) // 16))
                cv.spr(img, x + (w - 16 * sc) // 2, y + (cover_h - 16 * sc) // 2, sc)
            else:
                gs = max(1, min((w - 12 * fs) // 12, (cover_h - 8 * fs) // 12))
                self._blit_glyph(cv, _TYPE_GLYPH.get(it["type"], "app"),
                                 (x, y, w, cover_h),
                                 _TYPE_COLOR.get(it["type"], NAMES["indigo"]), gs)
        # Title band: cream text centered on the strongest ink.
        cv.rect(x, y + cover_h, w, band_h, NAMES["black"])
        name = it["title"]
        fw = lay.font_w
        maxc = max(3, (w - 4 * fs) // fw)
        if len(name) > maxc:
            name = name[:maxc]
        cv.print(name, x + (w - len(name) * fw) // 2,
                 y + cover_h + (band_h - 8 * fs) // 2, NAMES["white"], 1)
        # PLAY / CHANGE (mockup: green PLAY, warm-light CHANGE, both dark-edged).
        if ar is not None:
            bx, by, bw, bh = ar["play"]
            cv.rect(bx, by, bw, bh, th.get("play", NAMES["green"]))
            cv.rectb(bx, by, bw, bh, NAMES["black"])
            tw = 4 * fw + 14 * fs                    # run glyph + "PLAY"
            if tw > bw - 2 * fs:                     # narrow card -> text only
                cv.print("PLAY", bx + max(fs, (bw - 4 * fw) // 2),
                         by + (bh - 8 * fs) // 2, NAMES["white"], 1)
            else:
                tx = bx + (bw - tw) // 2
                self._blit_glyph(cv, "run", (tx, by, 12 * fs, bh),
                                 NAMES["white"], fs)
                cv.print("PLAY", tx + 14 * fs, by + (bh - 8 * fs) // 2,
                         NAMES["white"], 1)
            bx, by, bw, bh = ar["change"]
            cv.rect(bx, by, bw, bh, NAMES["white"])
            cv.rectb(bx, by, bw, bh, NAMES["black"])
            label = "CHANGE"
            maxc = max(2, (bw - 2 * fs) // fw)
            if len(label) > maxc:
                label = label[:maxc]
            cv.print(label, bx + max(fs, (bw - len(label) * fw) // 2),
                     by + (bh - 8 * fs) // 2, NAMES["black"], 1)
        self._card_frame(cv, rect, selected)

    def _draw_pseudo_card(self, cv, it, rect, selected):
        """The pinned tall card: MAKE STUDIO on the home shelf (the mockup's yellow
        pencil card), + New on the picker. Focus-yellow field, big tool glyph, a
        display-type heading and a small caption, and the corner pin."""
        NAMES = self._NAMES
        th = self.theme or {}
        lay = self.layout
        fs = lay.fs
        x, y, w, h = rect
        make = it.get("type") == MAKE_TILE_TYPE
        cv.rect(x, y, w, h, th.get("focus", NAMES["yellow"]))
        # Big tool art in the card's upper half: the real themeable IconSheet
        # PENCIL (Make) / plus (New) sprite when the console wired icon_for --
        # pal-remapped so it reads on the yellow field (white outline -> black,
        # yellow body -> the orange of the mockup's pencil). The 12x12 glyph
        # stays as the no-sheet fallback.
        img = self.icon_for("edit" if make else "new") \
            if self.icon_for is not None else None
        if img is not None:
            sc = max(fs, min((w - 12 * fs) // 16, (h // 2 - 4 * fs) // 16))
            cv.pal(NAMES["white"], NAMES["black"])
            cv.pal(NAMES["yellow"], NAMES["orange"])
            cv.spr(img, x + (w - 16 * sc) // 2,
                   y + 4 * fs + (h // 2 - 16 * sc) // 2, sc)
            cv.pal()
        else:
            gs = max(fs, min((w - 16 * fs) // 12, (h // 2 - 8 * fs) // 12))
            self._blit_glyph(cv, _TYPE_GLYPH[it["type"]],
                             (x, y + 4 * fs, w, h // 2), NAMES["black"], gs)
        # Corner pushpin (the mockup's "pinned" cue).
        px_, py_ = x + w - 8 * fs, y + 7 * fs
        cv.circ(px_, py_, 2 * fs, NAMES["black"])
        cv.line(px_, py_ + 2 * fs, px_ - 2 * fs, py_ + 5 * fs, NAMES["black"])
        # Heading (display type) + caption, centered in the lower half.
        heading = ("MAKE", "STUDIO") if make else ("NEW",)
        caption = "Open or create a project" if make else "Start a fresh project"
        ty = y + h * 11 // 20
        mult = getattr(lay, "lib_mult", 2)           # ~32px display type at any fs
        for line in heading:
            tw = _text_w(cv, line, mult)
            if tw > w - 4 * fs:                      # narrow card -> body size
                tw = _text_w(cv, line, 1)
                cv.print(line, x + (w - tw) // 2, ty, NAMES["black"], 1)
                ty += 10 * fs
            else:
                _print_scaled(cv, line, x + (w - tw) // 2, ty, NAMES["black"], mult)
                ty += 8 * fs * mult + 2 * fs
        ty += 2 * fs
        maxc = max(6, (w - 4 * fs) // lay.font_w)
        for line in _wrap_words(caption, maxc):
            tw = _text_w(cv, line, 1)
            cv.print(line, x + (w - tw) // 2, ty, NAMES["dark_grey"], 1)
            ty += 10 * fs
        self._card_frame(cv, rect, selected, ring=th.get("author", NAMES["orange"]))

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
        if selected:
            color = ring if ring is not None else th.get("focus", NAMES["yellow"])
            for i in range(max(2, fs)):
                d = fs + 1 + i
                cv.rectb(x - d, y - d, w + 2 * d, h + 2 * d, color)

    def _draw_pager(self, cv):
        """The footer pager arrows (boxed, mockup-style), drawn at the layout's
        page_prev/page_next hit rects -- dimmed at the ends of the page range.
        The HOME grid (actions on) sits on the light shelf panel, so it uses the
        surface ink; the picker sits on its dark tool backdrop, so it keeps the
        light chrome ink."""
        NAMES = self._NAMES
        th = self.theme or {}
        lay = self.layout
        if self.actions:
            ink = th.get("ink", NAMES["white"])
            dim = th.get("ink_dim", NAMES["light_grey"])
        else:
            ink = NAMES["white"]
            dim = NAMES["light_grey"]
        for rect, glyph, on in ((lay.page_prev, "<", self.page > 0),
                                (lay.page_next, ">", self.page < self.max_page())):
            x, y, w, h = rect
            cv.rectb(x, y, w, h, ink if on else dim)
            cv.print(glyph, x + (w - lay.font_w) // 2,
                     y + (h - 8 * lay.fs) // 2, ink if on else dim, 1)

    def _tile_glyph(self, cv, it, box):
        # A type-colored art box with a centered type glyph, for carts with no
        # sprite (320x240 baseline tiles). Uses the injected shared glyph blitter
        # (host == device). MAKE wears the theme's AUTHORING accent (the frozen
        # default keeps today's yellow), per visual identity v1 Section 6.2.
        NAMES = self._NAMES
        x, y, w, h = box
        ttype = it["type"]
        fill = _TYPE_COLOR.get(ttype, NAMES["indigo"])
        if ttype == MAKE_TILE_TYPE:
            fill = (self.theme or {}).get("author", _TYPE_COLOR[MAKE_TILE_TYPE])
        cv.rect(x + 6, y + 6, w - 12, h - 12, fill)
        self._blit_glyph(cv, _TYPE_GLYPH.get(ttype, "app"), box, NAMES["black"])


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
        cv = ws.sys_canvas
        # #76 sub-surface marks: on a RECORDING canvas, partition the home into
        # wallpaper / grid / bar streams so the web delta can skip the static grid +
        # bar while a live wallpaper animates underneath (they were one "launcher"
        # surface -- the wallpaper's motion re-shipped everything each frame). The
        # marks are positional slices of the same flat stream: replayed in order the
        # pixels are identical, and on the RAW canvas _surf is None (zero cost).
        _surf = getattr(cv, "begin_surface", None)
        ws.wallpaper.draw(dt)
        lay = ws.layout
        if _surf is not None:
            _surf("home-grid", "system")
        if not lay._base:
            # The Library shelf panel (visual identity v1's library mockup): the
            # warm tool surface framed over the construction field, with the
            # "LIBRARY" header and the footer cartridge count. The grid + pager
            # arrows draw inside it (Launcher._draw_shelf).
            self._draw_shelf_panel(cv)
        ws.launcher.draw(cv, ws._icon_sheet_for)
        # page chevrons when more than one page of carts (baseline tier; the
        # shelf tiers draw boxed pager arrows in the panel footer instead)
        if lay._base and ws.launcher.max_page() > 0:
            if ws.launcher.page > 0:
                px, py = lay.page_prev[0], lay.page_prev[1]
                cv.print("<", px + 3, py + 8, NAMES["white"], 2)
            if ws.launcher.page < ws.launcher.max_page():
                px, py = lay.page_next[0], lay.page_next[1]
                cv.print(">", px + 3, py + 8, NAMES["white"], 2)
        if _surf is not None:
            _surf("home-bar", "system")
        ws.bar_layer._draw_status_strip("home")

    def _draw_shelf_panel(self, cv):
        """The framed Library panel: surface fill + border, the Moy + "LIBRARY"
        header, and the footer's centered cartridge count between thin rules
        (the pager arrows are drawn by the grid, at the layout's footer rects)."""
        ws = self.ws
        NAMES = self._NAMES
        th = ws.theme_colors
        lay = ws.layout
        fs = lay.fs
        x, y, w, h = lay.lib_panel
        cv.rect(x, y, w, h, th["surface"])
        for i in range(fs):
            cv.rectb(x - i, y - i, w + 2 * i, h + 2 * i, th["border"])
        # Header: the mascot + display-type "LIBRARY", both sized by the shelf's
        # display multiplier (resolution-locked ~32px, whatever the body fs).
        mult = getattr(lay, "lib_mult", 2)
        hx = x + max(10 * fs, 20)
        moy = getattr(ws, "_icon_image_keyed", lambda kind: None)("moy")
        isc = max(1, (8 * fs * mult) // 16)          # mascot ~= the type height
        if moy is not None:
            cv.spr(moy, hx, y + (lay.lib_header_h - 16 * isc) // 2, isc)
        else:
            ws._icon("moy", hx, y + (lay.lib_header_h - 16 * fs) // 2, cv)
        _print_scaled(cv, "LIBRARY", hx + 16 * isc + 6 * fs,
                      y + (lay.lib_header_h - 8 * fs * mult) // 2, th["ink"], mult)
        # Footer: "N CARTRIDGES" centered between thin rules.
        n = 0
        for it in ws.launcher.items:
            if it.get("path"):
                n += 1
        label = str(n) + (" CARTRIDGE" if n == 1 else " CARTRIDGES")
        fw = lay.font_w
        tw = len(label) * fw
        ty = y + h - lay.lib_footer_h + (lay.lib_footer_h - 8 * fs) // 2
        tx = x + (w - tw) // 2
        cv.print(label, tx, ty, th["ink_dim"], 1)
        ly = ty + 4 * fs
        lx0 = lay.page_prev[0] + lay.page_prev[2] + 10 * fs
        lx1 = lay.page_next[0] - 10 * fs
        cv.rect(lx0, ly, max(0, tx - 8 * fs - lx0), fs, th["ink_dim"])
        cv.rect(tx + tw + 8 * fs, ly, max(0, lx1 - (tx + tw + 8 * fs)), fs,
                th["ink_dim"])

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
        if i.pressed("code"):
            # CHANGE (visual identity v1 Section 1.2): open the selected cartridge
            # in place in the Studio/Editor, landing on Config. The keyboard route
            # to the second verb; PLAY stays the primary activation above.
            ws.change_selected()
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
            # The selected card's PLAY / CHANGE buttons (desktop-density tiers).
            # Checked before the tile hit so a button tap never falls through to
            # the card's primary activation underneath it.
            ar = ws.launcher.action_rects()
            if ar is not None:
                if self._in(px, py, ar["play"]):
                    ws.launch_selected()
                    return True
                if self._in(px, py, ar["change"]):
                    ws.change_selected()
                    return True
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

    def _zone_action_rects(self, rect):
        """PLAY / CHANGE chip rects inside the lent bar zone -- the 320x240-baseline
        home of the selected card's two verbs (visual identity v1 Section 7: on the
        small tier 'selected actions use the zoned bar'; the desktop-density tiers
        draw them on the card itself, Launcher.action_rects). None off-baseline,
        with no real cart selected (the Make tile has one verb, its tap), or when
        the zone is too narrow to keep any room for the name."""
        ws = self.ws
        lay = ws.layout
        if not lay._base:
            return None
        sel = ws.launcher.selected()
        if sel is None or sel.get("type") in PSEUDO_TILE_TYPES:
            return None
        fw = lay.font_w
        pw = 4 * fw + 6                     # "PLAY"
        cw = 6 * fw + 6                     # "CHANGE"
        cx = rect[0] + rect[2] - cw         # CHANGE flush right, PLAY beside it
        px_ = cx - 4 - pw
        if px_ < rect[0] + 6 * fw:          # keep some room for the name
            return None
        return {"play": (px_, 2, pw, 13), "change": (cx, 2, cw, 13)}

    def draw_zone(self, cv, rect):
        """The launcher's lent left zone: the selected cart's name (or empty when
        there is none) -- cart management (create/copy/delete) moved to the Editor
        picker's zone (docs/shell_ux_v1.md: the launcher is for PLAYING, the picker
        is for MANAGING projects), so NEW/DUP/DEL no longer draw here. On the
        320x240 baseline the zone also carries the selected card's PLAY / CHANGE
        chips (visual identity v1 Section 1.2), right-aligned so the name keeps its
        flush-left spot."""
        ws = self.ws
        NAMES = self._NAMES
        lay = ws.layout
        if not lay._base:
            # Shelf tiers: the OS wordmark (the mockup's top-left "moybyte") --
            # the selected cart's name reads on the card itself, and the verbs
            # are the card's own PLAY/CHANGE row.
            fs = lay.fs
            ws._icon("moy", rect[0] + 2, rect[1], cv)
            cv.print("moybyte", rect[0] + 2 + 20 * fs,
                     rect[1] + (16 * fs - 8 * fs) // 2, NAMES["white"], 1)
            return
        sel = ws.launcher.selected()
        if sel is None:
            return
        chips = self._zone_action_rects(rect)
        limit = rect[2]
        if chips is not None:
            limit = chips["play"][0] - 4 - rect[0]
        name = sel["title"]
        maxc = max(4, limit // lay.font_w)
        if len(name) > maxc:
            name = name[:maxc]
        cv.print(name, rect[0] + 2, 3, NAMES["white"], 1)
        if chips is not None:
            th = ws.theme_colors
            for verb, label, bg in (("play", "PLAY", th["play"]),
                                    ("change", "CHANGE", th["author"])):
                x, y, w, h = chips[verb]
                cv.rect(x, y, w, h, bg)
                cv.rectb(x, y, w, h, NAMES["black"])
                cv.print(label, x + 3, y + 3, NAMES["black"], 1)

    def zone_tap(self, px, py, rect=None):
        """The launcher's lent left-zone tap slice: the PLAY / CHANGE chips on the
        320x240 baseline (visual identity v1 Section 1.2 -- PLAY runs the selected
        cart, CHANGE opens it in the Studio/Editor on Config). Anything else falls
        through (the rest of the zone is display-only)."""
        ws = self.ws
        chips = self._zone_action_rects(
            rect if rect is not None else ws.layout.zone_left)
        if chips is not None:
            if self._in(px, py, chips["play"]):
                ws.launch_selected()
                return True
            if self._in(px, py, chips["change"]):
                ws.change_selected()
                return True
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
    pinned grid tile). DEL is two-tap guarded (`_del_armed`): a project sits right next
    to its icon in this grid, so a single accidental tap must not delete it -- the
    first DEL tap arms a "DELETE? TAP AGAIN" prompt (folded into `zone_gen` so the bar
    cache repaints), the second confirms. Any navigation/selection change (nav2d, a
    tile tap/hover, paging, exiting) disarms it, and `reset()` (called by
    ws.open_picker()) clears it fresh on every visit."""

    id = "picker"
    domain = "system"

    def __init__(self, ws, names, in_rect):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._phover = (-1, -1)       # trackball hover pos (like LauncherHomeLayer._lhover)
        self._del_armed = False       # DEL confirm-guard: first tap arms, second confirms
        self._confirm_gen = 0         # bumped on arm/disarm so zone_gen reflects it too

    def reset(self):
        """Clear any armed delete-confirm state -- called by ws.open_picker() so a
        stale "DELETE? TAP AGAIN" from a previous visit never carries into a fresh
        one."""
        self._disarm_delete()

    def _arm_delete(self):
        self._del_armed = True
        self._confirm_gen += 1

    def _disarm_delete(self):
        if self._del_armed:
            self._del_armed = False
            self._confirm_gen += 1

    def draw(self, dt):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        # The picker is a TOOL space (owner call, 2026-07-08): a STATIC backdrop
        # instead of the animated wallpaper -- "it's software". Besides the look,
        # this makes the screen FREE under the redraw gate (#44): nothing here
        # animates, so an idle picker draws only on input. Styled to the panel
        # THEME (Settings -> THEME; the "night" default is the moybyte site
        # colorway): the theme's panel field with a faint dot-grid texture,
        # matching the desktop shell + Settings (still one fill + a few hundred
        # pix -- static, so effectively free).
        th = ws.theme_colors
        cv.cls(th["panel"])
        lay = ws.layout
        _fs = lay.fs
        for gy in range(8 * _fs, cv.h, 24 * _fs):
            for gx in range(8 * _fs, cv.w, 24 * _fs):
                cv.pix(gx, gy, th["dim"])
        ws.picker.draw(cv, ws._icon_sheet_for)
        # Baseline chevrons only -- the shelf tiers draw boxed pager arrows
        # inside Launcher._draw_shelf.
        if lay._base and ws.picker.max_page() > 0:
            if ws.picker.page > 0:
                px, py = lay.page_prev[0], lay.page_prev[1]
                cv.print("<", px + 3, py + 8, NAMES["white"], 2)
            if ws.picker.max_page() > 0 and ws.picker.page < ws.picker.max_page():
                px, py = lay.page_next[0], lay.page_next[1]
                cv.print(">", px + 3, py + 8, NAMES["white"], 2)
        ws.bar_layer._draw_status_strip("picker")

    def handle_input(self, i):
        ws = self.ws
        if i.pressed("left"):
            self._disarm_delete(); ws.picker.nav2d(-1, 0)
        if i.pressed("right"):
            self._disarm_delete(); ws.picker.nav2d(1, 0)
        if i.pressed("up"):
            self._disarm_delete(); ws.picker.nav2d(0, -1)
        if i.pressed("down"):
            self._disarm_delete(); ws.picker.nav2d(0, 1)
        if i.pressed("a") or i.pressed("run"):
            self._disarm_delete()
            ws.pick_selected()               # open the picked cart in the Editor (or + New)
        if i.pressed("b") or i.pressed("home") or i.pressed("stop"):
            self._disarm_delete()
            ws.exit()                        # back to the launcher root
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if click:
            if ws.bar_layer.handle_bar_tap("picker", px, py):   # clock/≡/wifi/X + lent zone
                return True
            lay = ws.layout
            if ws.picker.max_page() > 0 and self._in(px, py, lay.page_prev):
                self._disarm_delete(); ws.picker.flip_page(-1); return True
            if ws.picker.max_page() > 0 and self._in(px, py, lay.page_next):
                self._disarm_delete(); ws.picker.flip_page(1); return True
            i = ws.picker.tile_at(px, py)
            if i is not None:
                self._disarm_delete()
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
                self._disarm_delete()
                ws.picker.sel = i
        return True

    # -- the lent left zone (Stage 4, #46 zoned bar) --------------------------

    @property
    def zone_gen(self):
        """Proxy onto ws.picker.zone_gen PLUS the local delete-confirm generation --
        the picker GRID owns sel/items (same trick LauncherHomeLayer uses), but
        arming/disarming DELETE changes the zone's pixels (the title <-> "DELETE? TAP
        AGAIN") without necessarily touching sel/items, so it needs its own counter
        folded in so BarLayer's cache repaints on that transition too."""
        return self.ws.picker.zone_gen + self._confirm_gen

    def draw_zone(self, cv, rect):
        """The picker's lent left zone: DUP/DEL icons over the picker's SELECTED cart
        (when can_manage and a real cart -- not the pinned "+ New" tile -- is picked),
        then a title: "DELETE? TAP AGAIN" while a delete is armed, else "PICK A
        PROJECT" -- the picker is the one place a kid creates/copies/deletes/edits a
        project (docs/shell_ux_v1.md)."""
        ws = self.ws
        NAMES = self._NAMES
        lay = ws.layout
        real = ws._real_selected(ws.picker)
        if ws.can_manage and real is not None:
            ws._icon("dup", lay.dup_btn[0], lay.dup_btn[1], cv)
            ws._icon("del", lay.del_btn[0], lay.del_btn[1], cv)
        armed = self._del_armed and real is not None
        title = "DELETE? TAP AGAIN" if armed else "PICK A PROJECT"
        th = ws.theme_colors
        if ws.bar_layer.zone_band_light("picker"):
            ink = th["danger"] if armed else th["ink"]
        else:
            ink = NAMES["yellow"] if armed else NAMES["white"]
        cv.print(title, lay.status_name_x, 3, ink, 1)

    def zone_tap(self, px, py, rect=None):
        """The picker's lent left-zone tap slice: DUP (copy) fires immediately on the
        picker's selected cart -- a copy only ADDS, so it needs no guard. DEL is
        two-tap guarded: the first tap arms "DELETE? TAP AGAIN" (draw_zone shows it),
        the second (while still armed) confirms and deletes; nothing lower ever sees a
        raw one-tap delete. Both no-op when read-only or the selection is the pinned
        "+ New" tile."""
        ws = self.ws
        lay = ws.layout
        real = ws._real_selected(ws.picker)
        if ws.can_manage and real is not None and self._in(px, py, lay.dup_btn):
            self._disarm_delete()
            ws.dup_cart()
            return True
        if ws.can_manage and real is not None and self._in(px, py, lay.del_btn):
            if self._del_armed:
                self._disarm_delete()
                ws.del_cart()
            else:
                self._arm_delete()
            return True
        return False
