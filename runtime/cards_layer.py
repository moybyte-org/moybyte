"""The "Make it mine" config-card editor (#3/#15), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/shell_layers_refactor_v1.md Phase 2.

The cards surface is the kid's read-light way to tune a cart: each `edit` field is a
card the child steps with -/+ (or taps a picture for choice/sprite/bg pickers), then
PLAY (in the unified bar) re-runs + persists the cart. This module owns the card
DRAWING, the per-card LAYOUT/geometry, the scroll window (msel/mtop), and the tap/
scroll/keyboard handling. Stage-4 bar rollout / fix B dissolved its own GO/CODE/CLOSE
buttons into the bar (PLAY / the Code tab / the context X) and reflowed the cards to
fill the FULL width below the 18px bar (no centered mini-panel).

Boundary (the anti-spaghetti line, per the doc): CART STATE lives on the open
Project -- the Config tab reads its DATA through the injected workspace,
`ws.project.config`/`ws.project.cart`/`ws.project.sheet` (Stage 3 of
docs/shell_ux_technical_plan_v1.md moved the tabs' data reach-through off the ws.*
god-API onto Project; the ws.config/ws.cart forwards stay as tested surface). It is
the single source of truth, and `ws.apply` re-runs the cart. CardsLayer never OWNS
config; it mutates ws.project.config in
place and dispatches the stepping through `ws.adjust(...)` (which reads this layer's
`msel` to know which card is selected) and re-runs via `ws.apply()`. The card-only
constants live here (single source; console.py imports them back so tests + a couple
of console call sites resolve `console._CARD_H` / `_RUN_BTN` / ...). `NAMES` (palette),
`_in` (rect hit-test) and `_err_text` are injected at construction (the same circular-
import dodge the other extracted UIs use). Shared draw toolkit (ws._glyph/_icon_btn)
stays on Workstation; the bar draws through it via self.ws.

Stage 4 (#46 zoned bar): draw() calls ws.bar_layer._draw_status_strip("menu") LAST
(chrome over content) so the Editor's lent top-bar zone (the tab ladder + PLAY,
EditorApp.draw_zone) shows on this tab; handle_pointer routes a tap through
ws.bar_layer.handle_bar_tap("menu", ...) FIRST, before the card/button hit-tests.
"""


# -- card geometry (single source; console.py imports these back) -------------
# Stage-4 bar rollout / fix B: the Config screen's own GO / CODE / CLOSE buttons are
# GONE -- PLAY (bar) runs + persists the config, the Code tab is in the ladder, and the
# context X exits. Freed of that bottom button bar, the "Make it mine" cards fill the
# FULL 320 x (240-18) below the unified bar (fix C), not a small centered panel.
_CARD_X = 12
_CARD_W = 286
_CARD_Y0 = 44
_CARD_H = 20
# Cards-menu scroll window (#3): cards lay out from _CARD_Y0 down; rows whose bottom
# would pass _CARD_VIEW_BOTTOM are scrolled off. With the button bar gone the view now
# runs almost to the screen floor. A small up/down chevron strip on the right scrolls.
_CARD_VIEW_BOTTOM = 232
_CARD_SCROLL_UP = (300, 44, 16, 14)     # tap to scroll cards up (toward the top)
_CARD_SCROLL_DN = (300, 214, 16, 14)    # tap to scroll cards down

_BASE_W = 320
_BASE_H = 240


class CardsLayout:
    """Responsive "Make it mine" geometry (#39 step 3): the full-width panel, the
    card column + scroll chevrons, the per-display card heights and the picture-cell
    sizes, derived from the SYSTEM canvas size (w, h) + font scale.

    The single hard contract (mirrors Layout/CodeLayout/PaintLayout/...): at
    (w, h, fs) == (320, 240, 1) every field equals the frozen `_CARD_*` module
    constant, byte for byte (the `_base` branch); the responsive formulas only run
    on a larger canvas / bigger font. A bigger panel shows MORE cards at once (the
    view band grows) and the cards span its full width."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        self.w = int(w)
        self.h = int(h)
        self.fs = max(1, int(font_scale))
        fs = self.fs
        self._base = (self.w == _BASE_W and self.h == _BASE_H and fs == 1)
        if self._base:
            self.body = (0, 18, _BASE_W, _BASE_H - 18)
            self.head_glyph = (8, 22, 14, 14)
            self.head_xy = (26, 22)
            self.card_x, self.card_w = _CARD_X, _CARD_W
            self.card_y0, self.card_h = _CARD_Y0, _CARD_H
            self.view_bottom = _CARD_VIEW_BOTTOM
            self.scroll_up, self.scroll_dn = _CARD_SCROLL_UP, _CARD_SCROLL_DN
            self.gap = 2
            self.h_cells, self.h_icons, self.h_meter = 44, 36, 32
            return
        bar_h = 18 * fs
        self.body = (0, bar_h, self.w, self.h - bar_h)
        self.head_glyph = (8 * fs, bar_h + 4 * fs, 14 * fs, 14 * fs)
        self.head_xy = (26 * fs, bar_h + 4 * fs)
        self.card_x = _CARD_X * fs
        self.card_w = self.w - 34 * fs
        self.card_y0 = bar_h + 26 * fs
        self.card_h = _CARD_H * fs
        self.view_bottom = self.h - 8 * fs
        self.scroll_up = (self.w - 20 * fs, self.card_y0, 16 * fs, 14 * fs)
        self.scroll_dn = (self.w - 20 * fs, self.view_bottom - 18 * fs,
                          16 * fs, 14 * fs)
        self.gap = 2 * fs
        # Per-display card heights (#15): sprite/bg picker cells, icon choices,
        # gauge/count meters -- all scale with the font so the pictures stay tappable.
        self.h_cells, self.h_icons, self.h_meter = 44 * fs, 36 * fs, 32 * fs


class CardsLayer:
    """The cards ("Make it mine") content Layer (SYSTEM domain, responsive #39
    step 3): a full-screen panel on the reflowed system canvas (the frozen-cart
    backdrop is gone -- the panel always covered every pixel of it anyway).
    handle_input/handle_pointer own the selection + scroll and dispatch config
    edits to ws.adjust / the run to ws.apply, hit-testing in SYSTEM coords."""

    id = "cards"
    domain = "system"

    # A card field MAY carry an optional `display` hint -- "gauge" | "count" |
    # "choice-icons" | "sprite-tiles" | "bg-thumbs" -- that draws the VALUE as a
    # picture a kid who can't read can recognize, with the number/word kept as a small
    # SECONDARY cue. When `display` is absent the card renders as one text line.
    _DISPLAYS = ("gauge", "count", "choice-icons", "sprite-tiles", "bg-thumbs")
    _CELL_DISPLAYS = ("choice-icons", "sprite-tiles", "bg-thumbs")

    def __init__(self, ws, names, in_rect, err_text):
        self.ws = ws
        self._NAMES = names
        self._in = in_rect
        self._err_text = err_text
        self.msel = 0                 # selected card in the menu
        self.mtop = 0                 # first card scrolled into view (#3)
        sc = ws.sys_canvas
        self.layout = CardsLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs):
        """Rebuild the responsive geometry (#39 step 3) -- called by ws._relayout on
        a font-scale change."""
        self.layout = CardsLayout(w, h, fs)

    def reset(self):
        """Reset the scroll/selection state (called by ws.open on a fresh cart)."""
        self.msel = 0
        self.mtop = 0

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        ws = self.ws
        ws._reset_canvas_state()          # game-canvas hygiene (degradation shares it)
        try:
            self._draw_cards()
        except Exception as exc:  # noqa: BLE001
            # A malformed card (e.g. a bad tiles/choices entry) must NOT escape the
            # frame loop -- the device would hang silently with no error surface. Fall
            # back to a readable panel (on the SYSTEM canvas, where this layer lives);
            # the unified bar (drawn below) keeps the context X reachable so the kid
            # can exit.
            ws.cart_error = self._err_text(exc)
            print("Moybyte cards error:", exc)
            ws.player._draw_error_panel(ws.sys_canvas)
        # The Editor's lent top-bar zone (Stage 4, #46 zoned bar): the tab ladder +
        # PLAY, replacing the old pause-only tool switcher for this tab. Drawn LAST
        # (chrome over content), byte-identical cost to the #43 strip cache.
        ws.bar_layer._draw_status_strip("menu")

    def handle_input(self, i):
        ws = self.ws
        ed = ws.project.cart.get("edit")
        if not ed:
            return True
        if i.pressed("up"):
            self.msel = (self.msel - 1) % len(ed)
            self._reveal_card(self.msel)
        if i.pressed("down"):
            self.msel = (self.msel + 1) % len(ed)
            self._reveal_card(self.msel)
        if i.pressed("left"):
            ws.adjust(-1)
        if i.pressed("right"):
            ws.adjust(1)
        # Enter / RUN in Config = PLAY the cart (the bar's PLAY path). The device keyboard
        # maps Enter (0x0D) to the "a" button and the host maps it to "run", so BOTH must
        # play here: _leave_menu() -> EditorApp.leave()'s cards branch re-runs the cart with
        # the freshly-tuned config, persists config.json, and hands it to the Player with the
        # Editor as the caller (so the cart's exit returns to these cards). This replaces the
        # old "a" -> Code-editor shortcut (Code is one tap away on the bar ladder) -- that
        # shortcut is why a device tap of Enter "just entered code" instead of playing.
        if i.pressed("a") or i.pressed("run"):
            ws._leave_menu()
        else:
            ws._leave_or_home(ws._leave_menu)
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        # SYSTEM coords (#39 step 3): hit-test the raw pointer, no _game_xy.
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        ci = self._card_at(px, py)
        if ci is not None:
            self.msel = ci                 # hover highlights
        if click:
            # GO/CODE/CLOSE dissolved into the unified bar (fix B): PLAY runs+persists,
            # the Code tab is in the ladder, the context X exits.
            if self._cards_scrollable() and self._in(px, py, self.layout.scroll_up):
                self.scroll_cards(-1)
            elif self._cards_scrollable() and self._in(px, py, self.layout.scroll_dn):
                self.scroll_cards(1)
            elif ci is not None:
                self._card_tap(px, py, ci)
        return True

    # -- card text / value helpers -------------------------------------------

    def card_text(self, i):
        ws = self.ws
        f = ws.project.cart["edit"][i]
        v = ws.project.config.get(f["key"], f.get("default"))
        if f["type"] == "choice":
            v = self._choice_label(f, v)
        t = f.get("card")
        return t.replace("{value}", str(v)) if t else "%s: %s" % (f["key"].upper(), v)

    def _card_display(self, f):
        d = f.get("display")
        return d if d in self._DISPLAYS else None

    def _choice_label(self, f, v):
        """A short readable label for a choice value -- a kid-friendly word for a
        string choice, or just the id for tile/number choices."""
        if isinstance(v, str):
            return v.replace("_", " ").upper()
        return str(v)

    def _choice_index(self, f, cur):
        ch = f["choices"]
        return ch.index(cur) if cur in ch else 0

    def _resolve_tiles(self, f):
        """For a `sprite-tiles` field, the list of sprite tile ids its choices map
        to. `choices` may be ints (tile ids directly) or names paired with a
        parallel `tiles` list. Returns ints; non-resolvable entries become 0."""
        src = f.get("tiles")
        if not src:
            src = f.get("choices", [])
        out = []
        for c in src:                          # guard BOTH branches: a non-numeric
            try:                               # tiles/choices entry must not escape
                out.append(int(c))             # _draw_cards -> device hang (#15).
            except (TypeError, ValueError):
                out.append(0)
        return out

    # -- geometry / scroll ---------------------------------------------------

    def _card_height(self, f):
        lay = self.layout
        d = self._card_display(f)
        if d in ("sprite-tiles", "bg-thumbs"):
            return lay.h_cells
        if d == "choice-icons":
            return lay.h_icons    # cells are 22px tall at y+12 -> bottom y+34 fits
        if d in ("gauge", "count"):
            return lay.h_meter
        return lay.card_h

    def _card_layout(self):
        """Pure (no-draw) per-card geometry for the VISIBLE cards so draw and
        hit-test agree (#3). Cards lay out top-down from layout.card_y0 starting at
        the scrolled-in index self.mtop; a row is included only while its bottom
        stays within layout.view_bottom. Returns dicts: {i, f, display, x, y, w, h}."""
        ws = self.ws
        lay = self.layout
        rows = []
        y = lay.card_y0
        top = self._clamp_mtop()
        for i in range(top, len(ws.project.cart["edit"])):
            f = ws.project.cart["edit"][i]
            h = self._card_height(f)
            if i > top and y + h > lay.view_bottom:
                break                       # next row would spill past the buttons
            rows.append({"i": i, "f": f, "display": self._card_display(f),
                         "x": lay.card_x, "y": y, "w": lay.card_w, "h": h})
            y += h + lay.gap
        return rows

    def _card_count(self):
        ws = self.ws
        return len(ws.project.cart["edit"]) if ws.project.cart and ws.project.cart.get("edit") else 0

    def _max_mtop(self):
        """Topmost card index that still leaves the view full from the bottom up:
        walk heights backwards, summing until the next card would no longer fit."""
        ws = self.ws
        lay = self.layout
        n = self._card_count()
        if n == 0:
            return 0
        avail = lay.view_bottom - lay.card_y0
        used = 0
        top = n
        for i in range(n - 1, -1, -1):
            h = self._card_height(ws.project.cart["edit"][i])
            step = h if top == n else h + lay.gap
            if used + step > avail:
                break
            used += step
            top = i
        # Never park past the last card: even a card taller than the window must
        # still be reachable (_card_layout always shows at least the top row).
        return min(top, n - 1)

    def _clamp_mtop(self):
        self.mtop = max(0, min(self._max_mtop(), self.mtop))
        return self.mtop

    def scroll_cards(self, d):
        """Scroll the cards window by d rows (clamped). Independent of msel."""
        self.mtop = max(0, min(self._max_mtop(), self.mtop + d))

    def _cards_scrollable(self):
        """True when not all cards fit at once (so the chevrons are live)."""
        return self._max_mtop() > 0

    def _reveal_card(self, i):
        """Scroll so card i is on screen (mirror Launcher._scroll): bring it down
        into view if it's above the window, or up into view if it's below."""
        if i < self.mtop:
            self.mtop = i
        else:
            # Page the window down one card at a time until i's row is included.
            guard = self._card_count()
            while guard >= 0:
                if any(r["i"] == i for r in self._card_layout()):
                    break
                if self.mtop >= self._max_mtop():
                    break
                self.mtop += 1
                guard -= 1
        self._clamp_mtop()

    def _choice_cells(self, row):
        """Tappable cells for a choice-icons / sprite-tiles card: one box per
        choice, laid out left-to-right under the label. Returns a list of
        (choice_index, cell_rect)."""
        f = row["f"]
        fs = self.layout.fs
        n = len(f.get("choices", []))
        if n <= 0:
            return []
        if row["display"] == "bg-thumbs":
            cw, ch = 40 * fs, 26 * fs      # wide thumbnails for background previews
        elif row["display"] == "sprite-tiles":
            cw = ch = 26 * fs
        else:
            cw = ch = 22 * fs
        gap = 4 * fs
        x0 = row["x"] + 4 * fs
        top = row["y"] + 12 * fs
        cells = []
        for k in range(n):
            cells.append((k, (x0 + k * (cw + gap), top, cw, ch)))
        return cells

    # -- taps ----------------------------------------------------------------

    def _card_at(self, px, py):
        for row in self._card_layout():
            if self._in(px, py, (row["x"], row["y"], row["w"], row["h"])):
                return row["i"]
        return None

    def _card_tap(self, px, py, ci):
        """Apply a tap inside card `ci`. For an icon/sprite picker, tapping a
        specific choice cell SETS that choice (no scrolling needed -- a kid taps
        the picture they want). Otherwise the card is a -/+ stepper: the left half
        decrements, the right half increments (matching the on-card glyphs)."""
        ws = self.ws
        for row in self._card_layout():
            if row["i"] != ci:
                continue
            if row["display"] in self._CELL_DISPLAYS:
                for k, cell in self._choice_cells(row):
                    if self._in(px, py, cell):
                        ws.project.config[row["f"]["key"]] = row["f"]["choices"][k]
                        return
            ws.adjust(-1 if px < self.layout.card_x + self.layout.card_w // 2 else 1)
            return

    # -- draw ----------------------------------------------------------------

    def _draw_cards(self):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        # Fullscreen "Make it mine" panel below the unified bar (fix B/C): edge to
        # edge, no centered mini-card. GO/CODE/CLOSE are gone -- PLAY/Code-tab/X are all
        # in the bar (drawn after this by the layer).
        cv.rect(*(lay.body + (NAMES["dark_purple"],)))
        cv.rectb(*(lay.body + (NAMES["pink"],)))
        ws._glyph("edit", lay.head_glyph, NAMES["yellow"], cv)  # pencil = "make it yours"
        cv.print("MAKE IT MINE", lay.head_xy[0], lay.head_xy[1], NAMES["white"], 2)
        for row in self._card_layout():
            self._draw_card(row)
        if self._cards_scrollable():           # up/down chevrons when cards overflow
            if self.mtop > 0:
                cv.print("^", lay.scroll_up[0], lay.scroll_up[1], NAMES["yellow"], 2)
            if self.mtop < self._max_mtop():
                cv.print("v", lay.scroll_dn[0], lay.scroll_dn[1], NAMES["yellow"], 2)

    def _draw_card(self, row):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        i, f = row["i"], row["f"]
        x, y, w, h = row["x"], row["y"], row["w"], row["h"]
        sel = (i == self.msel)
        if sel:
            cv.rect(x, y - 1 * fs, w, h, NAMES["indigo"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        disp = row["display"]
        if disp is None:                                # today's plain text card
            ws._glyph("minus", (x, y, 14 * fs, 14 * fs), NAMES["yellow"], cv)
            cv.print(self.card_text(i), x + 18 * fs, y, fg, 2)
            ws._glyph("plus", (x + w - 14 * fs, y, 14 * fs, 14 * fs), NAMES["yellow"], cv)
            return
        # Visual card: a small label line (the SECONDARY text cue) + a picture row.
        cv.print(self.card_text(i), x + 2 * fs, y, fg, 1)
        if disp == "gauge":
            self._draw_gauge(row)
        elif disp == "count":
            self._draw_count(row)
        elif disp == "bg-thumbs":
            self._draw_bg_thumbs(row)
        elif disp in ("choice-icons", "sprite-tiles"):
            self._draw_choice_icons(row)

    def _draw_gauge(self, row):
        # A slow->fast slider: a turtle at the low end, a rabbit at the high end,
        # a track filled to the value's fraction, and a knob. Tap left/right of the
        # card to step it (the -/+ contract is preserved by _card_tap).
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        f = row["f"]
        x, y, w = row["x"], row["y"], row["w"]
        lo = f.get("min", 0)
        hi = f.get("max", lo + 1)
        cur = ws.project.config.get(f["key"], f.get("default", lo))
        try:
            frac = (float(cur) - lo) / (hi - lo) if hi > lo else 0.0
        except (TypeError, ValueError):
            frac = 0.0
        frac = max(0.0, min(1.0, frac))
        ends = f.get("gauge", {}) if isinstance(f.get("gauge"), dict) else {}
        ty = y + 18 * fs
        tx0 = x + 18 * fs
        tx1 = x + w - 18 * fs
        tw = tx1 - tx0
        ws._glyph(ends.get("low", "turtle"), (x, ty - 6 * fs, 16 * fs, 14 * fs),
                  NAMES["green"], cv)
        ws._glyph(ends.get("high", "rabbit"),
                  (x + w - 16 * fs, ty - 6 * fs, 16 * fs, 14 * fs), NAMES["peach"], cv)
        cv.rect(tx0, ty, tw, 3 * fs, NAMES["dark_grey"])            # track
        cv.rect(tx0, ty, int(tw * frac), 3 * fs, NAMES["yellow"])   # filled portion
        kx = tx0 + int(tw * frac)
        cv.rect(kx - 1 * fs, ty - 3 * fs, 3 * fs, 9 * fs, NAMES["white"])   # knob

    def _draw_count(self, row):
        # N repeated icons == the value, so a count reads at a glance. Kept to ONE
        # tidy row -- the count card is 32px tall, so a 2nd row of glyphs would
        # spill into the next card. The number itself is the label cue above, so an
        # over-cap value still reads correctly even when not every icon fits.
        ws = self.ws
        fs = self.layout.fs
        f = row["f"]
        x, y, w = row["x"], row["y"], row["w"]
        cur = ws.project.config.get(f["key"], f.get("default", 0))
        try:
            n = int(cur)
        except (TypeError, ValueError):
            n = 0
        glyph = f.get("icon", "star")
        step = 16 * fs
        per_row = max(1, (w - 4 * fs) // step)
        cap = int(f.get("count_max", min(f.get("max", 12), 14)))
        shown = max(0, min(n, cap, per_row))    # clamp to a single row
        for k in range(shown):
            gx = x + 2 * fs + k * step
            ws._glyph(glyph, (gx, y + 14 * fs, 14 * fs, 14 * fs),
                      self._NAMES["yellow"], ws.sys_canvas)

    def _draw_choice_icons(self, row):
        # Each choice is its own tappable cell -- a glyph (choice-icons) or a real
        # sprite tile from the cart sheet (sprite-tiles). The current pick is boxed.
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        f = row["f"]
        cur = ws.project.config.get(f["key"], f.get("default"))
        sel_k = self._choice_index(f, cur)
        tiles = self._resolve_tiles(f) if row["display"] == "sprite-tiles" else None
        icons = f.get("icons") or []
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            chosen = (k == sel_k)
            cv.rect(cx, cy, cw, ch, NAMES["black"] if chosen else NAMES["dark_purple"])
            cv.rectb(cx, cy, cw, ch, NAMES["yellow"] if chosen else NAMES["dark_grey"])
            if tiles is not None and ws.project.sheet is not None:
                img = ws.project.sheet.tile_image(tiles[k] if k < len(tiles) else 0, -1)
                if img is not None:
                    cv.spr(img, cx + (cw - 16 * fs) // 2, cy + (ch - 16 * fs) // 2,
                           2 * fs)
            else:
                glyph = icons[k] if k < len(icons) else "dot"
                ws._glyph(glyph, (cx + (cw - 14 * fs) // 2, cy + (ch - 14 * fs) // 2,
                                  14 * fs, 14 * fs), NAMES["white"], cv)

    # Each bg-thumbs choice is drawn as a tiny "what the screen will look like"
    # preview. A cart reads the chosen name in cfg("bg") and paints to match.
    # "night"/"stripes" get a patterned thumbnail; any other name renders as a
    # solid swatch via NAMES.get, so arbitrary palette colors (e.g. "indigo")
    # just work -- no preset list to keep in sync.

    def _draw_bg_thumb(self, name, rect):
        """Paint a small preview of background preset `name` inside `rect`."""
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        fs = self.layout.fs
        x, y, w, h = rect
        if name == "night":                              # starfield
            cv.rect(x, y, w, h, NAMES["black"])
            for sx, sy in ((4, 4), (14, 9), (24, 5), (30, 15), (9, 17), (20, 12)):
                if fs <= 1:
                    cv.pix(x + sx, y + sy, NAMES["white"])
                else:
                    cv.rect(x + sx * fs, y + sy * fs, fs, fs, NAMES["white"])
        elif name == "stripes":
            for i in range(0, w, 6 * fs):
                cv.rect(x + i, y, 3 * fs, h, NAMES["indigo"])
                cv.rect(x + i + 3 * fs, y, 3 * fs, h, NAMES["dark_blue"])
        else:                                            # a solid color swatch
            cv.rect(x, y, w, h, NAMES.get(name, NAMES["black"]))

    def _draw_bg_thumbs(self, row):
        # Each choice is a tappable thumbnail of the resulting background (#15 P3).
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        f = row["f"]
        cur = ws.project.config.get(f["key"], f.get("default"))
        sel_k = self._choice_index(f, cur)
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            self._draw_bg_thumb(f["choices"][k],
                                (cx + 1 * fs, cy + 1 * fs, cw - 2 * fs, ch - 2 * fs))
            cv.rectb(cx, cy, cw, ch,
                     NAMES["yellow"] if k == sel_k else NAMES["dark_grey"])
