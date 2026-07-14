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
# The tick helpers feed the perf_capture-gated home-frame split (see draw below).
try:
    from chrome import _print_scaled, _text_w, _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.chrome import _print_scaled, _text_w, _ticks_ms, _ticks_diff


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
    """The desktop home (#28): carts laid out as the Library SHELF -- a card grid
    that SCROLLS continuously LEFT-RIGHT (a shelf slides sideways; no pages)
    with the ONE tall featured card (the pinned MAKE/+New) at the head of the
    list. Keeps the selection model (items/sel/selected/move) the rest of the
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
        # Continuous scroll state: `_scroll` (raw px, the state of record) is
        # clamped LAZILY against the live layout (the `scroll` property), so the
        # windowed tier's per-window layout swaps (wm_windowed set_layout's on
        # every context switch) can never destroy a position. `_region` is the
        # touch INTERACTION model (a HORIZONTAL ui.ScrollRegion: drag + the slim
        # scrollbar), synced from `_scroll` whenever a drag isn't active -- the
        # Settings-rows pattern (settings_layer._scroll_region); `_taps` is the
        # shared tap-vs-drag machine over it.
        self._scroll = 0
        self._region = _ui.ScrollRegion(horizontal=True)
        self._taps = _ui.DragTap(self._region)
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
        """Adopt a new grid layout (size/font-scale change). Mirrors COLS/ROWS as
        instance attributes for the callers/tests that read them directly. The
        scroll offset is deliberately NOT re-clamped here (it clamps lazily on
        read) -- the windowed tier applies every window's layout to BOTH grids on
        each context switch, and an eager clamp against a small window would lose
        the desktop grid's position."""
        self.layout = layout
        self.COLS = layout.cols
        self.ROWS = layout.rows

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
        row, col = lay.tile_cell(self.sel)
        if dx:
            col += dx
            if col <= 0:
                self.sel = 0               # back onto the tall card
            else:
                self.sel = max(0, min(n - 1, lay.tile_index(row, col)))
        elif dy and self.sel > 0:          # the tall card spans both rows
            row = max(0, min(lay.rows - 1, row + dy))
            self.sel = max(0, min(n - 1, lay.tile_index(row, col)))
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

    @property
    def dragging(self):
        """True while a touch drag (past the slop) is scrolling the grid."""
        return self._taps.dragging

    def max_scroll(self):
        lay = self.layout
        return max(0, lay.grid_content_w(len(self.items)) - lay.lib_grid[2])

    def scroll_cols(self, d):
        """Nudge the grid by d card columns (the footer arrow buttons)."""
        self.scroll = self.scroll + d * self.layout.lib_step

    def _scroll_to_sel(self):
        """Keep the selected card fully inside the grid viewport (keyboard nav)."""
        lay = self.layout
        gw = lay.lib_grid[2]
        _row, col = lay.tile_cell(self.sel)
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
        self._region.set(lay.lib_grid, lay.grid_content_w(len(self.items)))
        if not self._region.drag_active:
            self._region.offset = self.scroll
        return self._region

    def pointer_frame(self, px, py, click, down):
        """One pointer sample over the grid, fed through the shared ui.DragTap
        machine: returns the tapped tile index on a clean RELEASE (press and
        release on the same card with no drag travel), else None -- so a
        scroll gesture can never launch a cart. `click` is the press edge,
        `down` the held state (ws.pointer.down)."""
        region = self._scroll_region()
        press = self._taps.frame(px, py, click, down,
                                 slop=4 * self.layout.fs + 2)
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

    def _visible(self):
        """(index, rect) for every card intersecting the grid viewport at the
        current scroll (partially clipped cards included -- the draw clips)."""
        s = self.scroll
        lay = self.layout
        for i in range(len(self.items)):
            r = lay.tile_rect(i, s)
            if r is not None:
                yield i, r

    def tile_rect(self, i):
        """The grid-cell rect for cart index i at the current scroll, or None if
        it's outside the grid viewport (geometry from the live Layout, so it
        reflows with the system canvas / font scale)."""
        return self.layout.tile_rect(i, self.scroll)

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

    def draw(self, cv, sheet_for=None):
        # Cards only -- the framed Library panel / tool backdrop + status strip
        # are drawn by the owning layer around this. One look on EVERY tier now
        # (the 320x240 baseline included): the Library SHELF cards (visual
        # identity v1's library mockup).
        self._draw_shelf(cv, sheet_for)

    # -- the Library shelf cards (visual identity v1) ---------------------------

    def _draw_shelf(self, cv, sheet_for):
        """The mockup's card grid: the tall featured slot (MAKE STUDIO / +New) at
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
            clip(gx, gy - d, gw, gh + 2 * d)
        for i, rect in self._visible():
            it = self.items[i]
            if it.get("type") in PSEUDO_TILE_TYPES:
                self._draw_pseudo_card(cv, it, rect, i == self.sel)
            else:
                self._draw_cart_card(cv, it, rect, i == self.sel, sheet_for)
        if clip is not None:
            clip()
        self._draw_scroll_ui(cv)

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
        # Drag-scroll PARTIAL repaint bookkeeping (#58/#66: a FULL home repaint
        # measured ~100-140ms on BOTH boards' glass -- backdrop + panel + cards
        # stack megabytes of writes -- capping a shelf drag at ~7fps). During a
        # drag only the grid band + bar change, so eligible frames skip the
        # wallpaper/panel chrome entirely (see _try_drag_partial): the streak
        # counts consecutive FULL paints with identical statics, so a partial
        # only runs once every retained framebuffer already holds them.
        self._full_streak = 0
        self._statics = None

    def _statics_key(self, cv):
        """Everything the home frame's STATIC chrome (wallpaper backdrop +
        Library panel fill/header/footer) is a pure function of. A key change
        forces full paints until the streak re-arms."""
        ws = self.ws
        return (cv.w, cv.h, ws.layout.fs, id(ws.theme_colors),
                ws.wallpaper_id, len(ws.launcher.items))

    def _try_drag_partial(self, cv, dt):
        """The shelf drag fast path: while a touch drag scrolls the grid and
        every retained framebuffer already holds this frame's static chrome
        (two prior full paints, unchanged statics -- the ping-pong stale-by-2
        rule), repaint ONLY what moves: the inflated grid band (fill + cards +
        scroll UI, the exact rect the card clip uses) and the bar strip. The
        wallpaper backdrop and panel chrome are byte-identical in the target
        buffer and are simply left alone. Full paints resume on release, so
        any straggler is erased within a frame."""
        ws = self.ws
        if not ws.launcher.dragging:
            return False
        if getattr(cv, "RETAINED_FRAMES", 0) < 1 or self._full_streak < 2:
            return False
        if self._statics != self._statics_key(cv):
            return False
        # Anything animating over the home (toast/confetti/splash/live
        # wallpaper) moves pixels outside the band -> full frames.
        if ws._animating(dt):
            return False
        top = getattr(ws.wm, "top_kind", None)
        if top is not None and top() != "launcher":
            return False
        lay = ws.layout
        gx, gy, gw, gh = lay.lib_grid
        d = 2 * lay.fs + 2
        p = ws.pointer
        if p is not None and getattr(p, "visible", False):
            # The composited cursor must land fully inside the repainted band,
            # or its previous stamp would ghost on the untouched chrome.
            if not (gx <= p.x and p.x + 8 <= gx + gw
                    and gy - d <= p.y and p.y + 13 <= gy + gh + d):
                return False
        _t0 = _ticks_ms() if getattr(ws, "perf_capture", False) else None
        cv.rect(gx, gy - d, gw, gh + 2 * d, ws.theme_colors["surface"])
        ws.launcher.draw(cv, ws._icon_sheet_for)
        _t1 = _ticks_ms() if _t0 is not None else None
        ws.bar_layer._draw_status_strip("home")
        if _t0 is not None:
            ws._pf_home = (0, _ticks_diff(_t1, _t0),
                           _ticks_diff(_ticks_ms(), _t1))
        return True

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
        ws = self.ws
        cv = ws.sys_canvas
        if self._try_drag_partial(cv, dt):    # drag-scroll fast path (#58/#66)
            return
        # #76 sub-surface marks: on a RECORDING canvas, partition the home into
        # wallpaper / grid / bar streams so the web delta can skip the static grid +
        # bar while a live wallpaper animates underneath (they were one "launcher"
        # surface -- the wallpaper's motion re-shipped everything each frame). The
        # marks are positional slices of the same flat stream: replayed in order the
        # pixels are identical, and on the RAW canvas _surf is None (zero cost).
        _surf = getattr(cv, "begin_surface", None)
        # perf_capture (#66 instrument-before-cutting): time the home frame's
        # three sections so a launcher HITCH names its eater (the device diag
        # appends ws._pf_home to the HITCH line -- wallpaper vs grid vs bar).
        _t0 = _ticks_ms() if getattr(ws, "perf_capture", False) else None
        ws.wallpaper.draw(dt)
        _t1 = _ticks_ms() if _t0 is not None else None
        if _surf is not None:
            _surf("home-grid", "system")
        # The Library shelf panel (visual identity v1's library mockup) on every
        # tier: the warm tool surface framed over the construction field, with
        # the "LIBRARY" header and the footer cartridge count. The scrolling
        # card grid + its scroll arrows/bar draw inside it (Launcher._draw_shelf).
        self._draw_shelf_panel(cv)
        ws.launcher.draw(cv, ws._icon_sheet_for)
        _t2 = _ticks_ms() if _t0 is not None else None
        if _surf is not None:
            _surf("home-bar", "system")
        ws.bar_layer._draw_status_strip("home")
        if _t0 is not None:
            _t3 = _ticks_ms()
            ws._pf_home = (_ticks_diff(_t1, _t0), _ticks_diff(_t2, _t1),
                           _ticks_diff(_t3, _t2))
        # A FULL paint landed in the current framebuffer: advance (or restart)
        # the partial path's statics streak.
        key = self._statics_key(cv)
        if key == self._statics:
            if self._full_streak < 2:
                self._full_streak += 1
        else:
            self._statics = key
            self._full_streak = 1

    def _draw_shelf_panel(self, cv):
        """The framed Library panel: surface fill + border, the Moy + "LIBRARY"
        header, and the footer's centered cartridge count between thin rules
        (the scroll arrows are drawn by the grid, at the layout's footer rects)."""
        ws = self.ws
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
        lx0 = lay.scroll_lt[0] + lay.scroll_lt[2] + 10 * fs
        lx1 = lay.scroll_rt[0] - 10 * fs
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
        # Grid nav (#28): left/right slide a card column, up/down step within it.
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
        # Desktop home (#28): the bar / scroll arrows / the selected card's
        # PLAY-CHANGE row fire on the press edge; a tap on a CARD selects + runs
        # on RELEASE (Launcher.pointer_frame), so drag-to-scroll on the grid can
        # never launch a cart. Trackball hover still previews the icon under it
        # (pointer-up only -- a touch drag must not re-select under the finger).
        # The bottom in-cart dock is no longer drawn on the launcher (#46), so it's
        # not hit-tested here; Settings is reached via the gear in the status strip.
        ws = self.ws
        down = ws.pointer.down
        if click:
            # The top-bar tap slice (clock egg / ≡ / NEW / DUP / DEL) is owned by the
            # bar surface now (#46 BarLayer); it also runs the clock-run reset for any
            # non-clock tap, so arrow/tile taps that fall through stay byte-identical.
            if ws.bar_layer.handle_home_tap(px, py):
                return True
            lay = ws.layout
            if ws.launcher.max_scroll() > 0 and self._in(px, py, lay.scroll_lt):
                ws.launcher.scroll_cols(-1); return True
            if ws.launcher.max_scroll() > 0 and self._in(px, py, lay.scroll_rt):
                ws.launcher.scroll_cols(1); return True
            # The selected card's PLAY / CHANGE buttons (wide-card tiers).
            # Checked before the grid press so a button tap never falls through to
            # the card's primary activation underneath it.
            ar = ws.launcher.action_rects()
            if ar is not None:
                if self._in(px, py, ar["play"]):
                    ws.launch_selected()
                    return True
                if self._in(px, py, ar["change"]):
                    ws.change_selected()
                    return True
        # The grid's press/drag/release machine: returns an index only on a clean
        # tap release -- the launcher tap = RUN the selected cart.
        i = ws.launcher.pointer_frame(px, py, click, down)
        if i is not None:
            ws.launcher.sel = i
            ws.launch_selected()
            return True
        if click or down:
            self._lhover = (px, py)   # track the finger so the release frame
            return True               # isn't read as a hover "move" below
        # Trackball cursor hover (pointer up, no click): highlight the icon the
        # cursor MOVED onto. Only when the position actually changed frame-to-
        # frame, so a parked cursor doesn't fight keyboard nav. _lhover seeds to
        # the live pointer position on the first frame so the initial centered
        # cursor isn't treated as a move (which would clobber the first arrow step).
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
        """PLAY / CHANGE chip rects inside the lent bar zone -- the narrow-card
        tiers' home of the selected card's two verbs (visual identity v1 Section 7:
        on the small tier 'selected actions use the zoned bar'; the wide-card tiers
        draw them on the card itself, Launcher.action_rects -- lay.lib_card_actions
        is the one predicate both read). None on wide-card tiers, with no real cart
        selected (the Make tile has one verb, its tap), or when the zone is too
        narrow to keep any room for the name."""
        ws = self.ws
        lay = ws.layout
        if lay.lib_card_actions:
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
        narrow-card tiers (the 320x240 baseline) the zone also carries the selected
        card's PLAY / CHANGE chips (visual identity v1 Section 1.2), right-aligned
        so the name keeps its flush-left spot."""
        ws = self.ws
        NAMES = self._NAMES
        lay = ws.layout
        if lay.lib_card_actions:
            # Wide-card tiers: the OS wordmark (the mockup's top-left "moybyte") --
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
        down = ws.pointer.down
        if click:
            if ws.bar_layer.handle_bar_tap("picker", px, py):   # clock/≡/wifi/X + lent zone
                return True
            lay = ws.layout
            if ws.picker.max_scroll() > 0 and self._in(px, py, lay.scroll_lt):
                self._disarm_delete(); ws.picker.scroll_cols(-1); return True
            if ws.picker.max_scroll() > 0 and self._in(px, py, lay.scroll_rt):
                self._disarm_delete(); ws.picker.scroll_cols(1); return True
        # The grid's press/drag/release machine (mirrors LauncherHomeLayer): a
        # clean tap release picks; a drag scrolls and disarms the DEL confirm.
        i = ws.picker.pointer_frame(px, py, click, down)
        if i is not None:
            self._disarm_delete()
            ws.picker.sel = i
            ws.pick_selected()
            return True
        if click or down:
            if ws.picker.dragging:
                self._disarm_delete()
            self._phover = (px, py)   # track the finger so the release frame
            return True               # isn't read as a hover "move" below
        # Trackball hover (pointer up, no click): preview the tile the cursor moved
        # onto (mirrors LauncherHomeLayer -- seed to the live pos so the first
        # centered frame isn't a move).
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
