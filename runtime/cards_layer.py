"""The "Make it mine" config-card editor (#3/#15), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/shell_layers_refactor_v1.md Phase 2.

The cards surface is the kid's read-light way to tune a cart: each `edit` field is a
card the child steps with -/+ (or taps a picture for choice/sprite/bg pickers), then
GO re-runs the cart. This module owns the card DRAWING, the per-card LAYOUT/geometry,
the scroll window (msel/mtop), and the tap/scroll/keyboard handling.

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
_RUN_BTN = (28, 188, 70, 24)
_CODE_BTN = (104, 188, 84, 24)
_CLOSE_BTN = (194, 188, 96, 24)
_CARD_X = 24
_CARD_W = 272
_CARD_Y0 = 52
_CARD_H = 20
# Cards-menu scroll window (#3): cards lay out from _CARD_Y0 down; rows whose bottom
# would pass _CARD_VIEW_BOTTOM are scrolled off rather than drawn over the RUN/CODE/
# CLOSE bar (y=188). A small up/down chevron strip on the right scrolls.
_CARD_VIEW_BOTTOM = 186
_CARD_SCROLL_UP = (300, 38, 16, 14)     # tap to scroll cards up (toward the top)
_CARD_SCROLL_DN = (300, 168, 16, 14)    # tap to scroll cards down


class CardsLayer:
    """The cards ("Make it mine") content Layer (game domain): an editor panel over
    the frozen cart frame. draw = the shared cart backdrop (ws._draw_menu_backdrop)
    then the cards; handle_input/handle_pointer own the selection + scroll and
    dispatch config edits to ws.adjust / the run to ws.apply."""

    id = "cards"
    domain = "game"

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

    def reset(self):
        """Reset the scroll/selection state (called by ws.open on a fresh cart)."""
        self.msel = 0
        self.mtop = 0

    # -- Layer facets --------------------------------------------------------

    def draw(self, dt):
        ws = self.ws
        ws._draw_menu_backdrop()          # frozen cart frame + reset draw state
        try:
            self._draw_cards()
        except Exception as exc:  # noqa: BLE001
            # A malformed card (e.g. a bad tiles/choices entry) must NOT escape the
            # frame loop -- the device would hang silently with no error surface. Fall
            # back to a readable panel + CLOSE.
            ws.cart_error = self._err_text(exc)
            print("Moybyte cards error:", exc)
            ws._draw_error_panel()
            ws._icon_btn("close", "", _CLOSE_BTN, self._NAMES["red"])
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
        if i.pressed("a"):
            ws.set_menu_view("code")
        if i.pressed("run"):
            ws.apply()
        else:
            ws._leave_or_home(ws._leave_menu)
        return True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        gx, gy = ws._game_xy(px, py)
        px, py = gx, gy
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        ci = self._card_at(px, py)
        if ci is not None:
            self.msel = ci                 # hover highlights
        if click:
            if self._in(px, py, _RUN_BTN):
                ws.apply()
            elif self._in(px, py, _CODE_BTN):
                ws.set_menu_view("code")
            elif self._in(px, py, _CLOSE_BTN):
                ws._leave_menu()
            elif self._cards_scrollable() and self._in(px, py, _CARD_SCROLL_UP):
                self.scroll_cards(-1)
            elif self._cards_scrollable() and self._in(px, py, _CARD_SCROLL_DN):
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
        d = self._card_display(f)
        if d in ("sprite-tiles", "bg-thumbs"):
            return 44
        if d == "choice-icons":
            return 36         # cells are 22px tall at y+12 -> bottom y+34 fits in 36
        if d in ("gauge", "count"):
            return 32
        return _CARD_H

    def _card_layout(self):
        """Pure (no-draw) per-card geometry for the VISIBLE cards so draw and
        hit-test agree (#3). Cards lay out top-down from _CARD_Y0 starting at the
        scrolled-in index self.mtop; a row is included only while its bottom stays
        within _CARD_VIEW_BOTTOM (so cards never overlap the RUN/CODE/CLOSE bar).
        Returns dicts: {i, f, display, x, y, w, h}."""
        ws = self.ws
        rows = []
        y = _CARD_Y0
        top = self._clamp_mtop()
        for i in range(top, len(ws.project.cart["edit"])):
            f = ws.project.cart["edit"][i]
            h = self._card_height(f)
            if i > top and y + h > _CARD_VIEW_BOTTOM:
                break                       # next row would spill past the buttons
            rows.append({"i": i, "f": f, "display": self._card_display(f),
                         "x": _CARD_X, "y": y, "w": _CARD_W, "h": h})
            y += h + 2
        return rows

    def _card_count(self):
        ws = self.ws
        return len(ws.project.cart["edit"]) if ws.project.cart and ws.project.cart.get("edit") else 0

    def _max_mtop(self):
        """Topmost card index that still leaves the view full from the bottom up:
        walk heights backwards, summing until the next card would no longer fit."""
        ws = self.ws
        n = self._card_count()
        if n == 0:
            return 0
        avail = _CARD_VIEW_BOTTOM - _CARD_Y0
        used = 0
        top = n
        for i in range(n - 1, -1, -1):
            h = self._card_height(ws.project.cart["edit"][i])
            step = h if top == n else h + 2
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
        n = len(f.get("choices", []))
        if n <= 0:
            return []
        if row["display"] == "bg-thumbs":
            cw, ch = 40, 26                # wide thumbnails for background previews
        elif row["display"] == "sprite-tiles":
            cw = ch = 26
        else:
            cw = ch = 22
        gap = 4
        x0 = row["x"] + 4
        top = row["y"] + 12
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
            ws.adjust(-1 if px < _CARD_X + _CARD_W // 2 else 1)
            return

    # -- draw ----------------------------------------------------------------

    def _draw_cards(self):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.canvas
        cv.rect(20, 16, 280, 206, NAMES["dark_purple"])
        cv.rectb(20, 16, 280, 206, NAMES["pink"])
        ws._glyph("edit", (28, 20, 14, 14), NAMES["yellow"])   # pencil = "make it yours"
        cv.print("MAKE IT MINE", 46, 22, NAMES["white"], 2)
        for row in self._card_layout():
            self._draw_card(row)
        if self._cards_scrollable():           # up/down chevrons when cards overflow
            if self.mtop > 0:
                cv.print("^", _CARD_SCROLL_UP[0], _CARD_SCROLL_UP[1], NAMES["yellow"], 2)
            if self.mtop < self._max_mtop():
                cv.print("v", _CARD_SCROLL_DN[0], _CARD_SCROLL_DN[1], NAMES["yellow"], 2)
        ws._icon_btn("run", "GO", _RUN_BTN, NAMES["green"])
        ws._icon_btn("edit", "CODE", _CODE_BTN, NAMES["blue"])
        ws._icon_btn("close", "", _CLOSE_BTN, NAMES["red"])

    def _draw_card(self, row):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.canvas
        i, f = row["i"], row["f"]
        x, y, w, h = row["x"], row["y"], row["w"], row["h"]
        sel = (i == self.msel)
        if sel:
            cv.rect(x, y - 1, w, h, NAMES["indigo"])
        fg = NAMES["white"] if sel else NAMES["light_grey"]
        disp = row["display"]
        if disp is None:                                # today's plain text card
            ws._glyph("minus", (x, y, 14, 14), NAMES["yellow"])
            cv.print(self.card_text(i), x + 18, y, fg, 2)
            ws._glyph("plus", (x + w - 14, y, 14, 14), NAMES["yellow"])
            return
        # Visual card: a small label line (the SECONDARY text cue) + a picture row.
        cv.print(self.card_text(i), x + 2, y, fg, 1)
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
        cv = ws.canvas
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
        ty = y + 18
        tx0 = x + 18
        tx1 = x + w - 18
        tw = tx1 - tx0
        ws._glyph(ends.get("low", "turtle"), (x, ty - 6, 16, 14), NAMES["green"])
        ws._glyph(ends.get("high", "rabbit"), (x + w - 16, ty - 6, 16, 14), NAMES["peach"])
        cv.rect(tx0, ty, tw, 3, NAMES["dark_grey"])                 # track
        cv.rect(tx0, ty, int(tw * frac), 3, NAMES["yellow"])        # filled portion
        kx = tx0 + int(tw * frac)
        cv.rect(kx - 1, ty - 3, 3, 9, NAMES["white"])               # knob

    def _draw_count(self, row):
        # N repeated icons == the value, so a count reads at a glance. Kept to ONE
        # tidy row -- the count card is 32px tall, so a 2nd row of glyphs would
        # spill into the next card. The number itself is the label cue above, so an
        # over-cap value still reads correctly even when not every icon fits.
        ws = self.ws
        f = row["f"]
        x, y, w = row["x"], row["y"], row["w"]
        cur = ws.project.config.get(f["key"], f.get("default", 0))
        try:
            n = int(cur)
        except (TypeError, ValueError):
            n = 0
        glyph = f.get("icon", "star")
        step = 16
        per_row = max(1, (w - 4) // step)
        cap = int(f.get("count_max", min(f.get("max", 12), 14)))
        shown = max(0, min(n, cap, per_row))    # clamp to a single row
        for k in range(shown):
            gx = x + 2 + k * step
            ws._glyph(glyph, (gx, y + 14, 14, 14), self._NAMES["yellow"])

    def _draw_choice_icons(self, row):
        # Each choice is its own tappable cell -- a glyph (choice-icons) or a real
        # sprite tile from the cart sheet (sprite-tiles). The current pick is boxed.
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.canvas
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
                    cv.spr(img, cx + (cw - 16) // 2, cy + (ch - 16) // 2, 2)
            else:
                glyph = icons[k] if k < len(icons) else "dot"
                ws._glyph(glyph, (cx + (cw - 14) // 2, cy + (ch - 14) // 2, 14, 14),
                          NAMES["white"])

    # Each bg-thumbs choice is drawn as a tiny "what the screen will look like"
    # preview. A cart reads the chosen name in cfg("bg") and paints to match.
    # "night"/"stripes" get a patterned thumbnail; any other name renders as a
    # solid swatch via NAMES.get, so arbitrary palette colors (e.g. "indigo")
    # just work -- no preset list to keep in sync.

    def _draw_bg_thumb(self, name, rect):
        """Paint a small preview of background preset `name` inside `rect`."""
        NAMES = self._NAMES
        cv = self.ws.canvas
        x, y, w, h = rect
        if name == "night":                              # starfield
            cv.rect(x, y, w, h, NAMES["black"])
            for sx, sy in ((4, 4), (14, 9), (24, 5), (30, 15), (9, 17), (20, 12)):
                cv.pix(x + sx, y + sy, NAMES["white"])
        elif name == "stripes":
            for i in range(0, w, 6):
                cv.rect(x + i, y, 3, h, NAMES["indigo"])
                cv.rect(x + i + 3, y, 3, h, NAMES["dark_blue"])
        else:                                            # a solid color swatch
            cv.rect(x, y, w, h, NAMES.get(name, NAMES["black"]))

    def _draw_bg_thumbs(self, row):
        # Each choice is a tappable thumbnail of the resulting background (#15 P3).
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.canvas
        f = row["f"]
        cur = ws.project.config.get(f["key"], f.get("default"))
        sel_k = self._choice_index(f, cur)
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            self._draw_bg_thumb(f["choices"][k], (cx + 1, cy + 1, cw - 2, ch - 2))
            cv.rectb(cx, cy, cw, ch,
                     NAMES["yellow"] if k == sel_k else NAMES["dark_grey"])
