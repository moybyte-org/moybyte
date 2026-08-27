"""The "Make it mine" config-card editor (#3/#15), extracted from Workstation
(runtime/console.py) as its own Layer -- docs/history/shell_layers_refactor_v1.md Phase 2.

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
docs/history/shell_ux_technical_plan_v1.md moved the tabs' data reach-through off the ws.*
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

try:
    import ui as _ui
except ImportError:  # pragma: no cover - host fallback
    from runtime import ui as _ui

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H)



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
# The header "INFO" button (#94): opens the CART INFO modal (title/author manifest
# editing -- the tracker's gap 2). Sits in the header row (y=21..35), clear of both
# the "MAKE IT MINE" label (ends ~x234 at scale 2) and the scroll chevrons (y>=44).
_CARD_INFO_BTN = (278, 21, 36, 14)


class CardsLayout(LayoutBase):
    """Responsive "Make it mine" geometry (#39 step 3): the full-width panel, the
    card column + scroll chevrons, the per-display card heights and the picture-cell
    sizes, derived from the SYSTEM canvas size (w, h) + font scale.

    The single hard contract (mirrors Layout/CodeLayout/PaintLayout/...): at
    (w, h, fs) == (320, 240, 1) every field equals the frozen `_CARD_*` module
    constant, byte for byte (the `_base` branch); the responsive formulas only run
    on a larger canvas / bigger font. A bigger panel shows MORE cards at once (the
    view band grows) and the cards span its full width."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1):
        LayoutBase.__init__(self, w, h, font_scale)
        fs = self.fs
        if self._base:
            self.body = (0, 18, _BASE_W, _BASE_H - 18)
            self.head_glyph = (8, 22, 14, 14)
            self.head_xy = (26, 22)
            self.card_x, self.card_w = _CARD_X, _CARD_W
            self.card_y0, self.card_h = _CARD_Y0, _CARD_H
            self.view_bottom = _CARD_VIEW_BOTTOM
            self.scroll_up, self.scroll_dn = _CARD_SCROLL_UP, _CARD_SCROLL_DN
            self.info_btn = _CARD_INFO_BTN
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
        self.info_btn = (self.w - 44 * fs, bar_h + 3 * fs, 40 * fs, 14 * fs)
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
        self._t = None                # per-draw tone map (set by _draw_cards)
        self._dragv = None            # drag-to-scroll anchor (held vertical drag)
        # The CART INFO modal (#94): None when closed, else {"title", "author",
        # "field" (0=title/1=author), "msg", "armed"} -- the title/author edit
        # buffer + which field has focus + an inline status line. See _open_meta.
        self.meta = None
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
        if self.meta is not None:     # never leak an open modal across a cart switch
            self.meta = None
            self.ws._set_text_mode(False)

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
        # The CART INFO modal (#94), if open, draws OVER the bar too -- same order
        # as the block editor's blk_kbd prompt (chrome, then any modal on top).
        if self.meta is not None:
            self._draw_meta_modal()

    def handle_input(self, i):
        ws = self.ws
        if self.meta is not None:
            return self._meta_input(i)
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
            ws.defer(ws._leave_menu)   # #184: PLAY runs behind the next paint
        else:
            ws._leave_or_home(ws._leave_menu)
        return True

    def _cards_drag(self, px, py):
        """A held vertical drag on the card column scrolls the list, one card per
        base-card-height of travel (row heights vary per display kind, so the
        base height is the step unit; the sub-step remainder stays anchored).
        Starts on the column, may continue past its edge -- the Settings-rows
        drag contract."""
        ws = self.ws
        lay = self.layout
        if not ws.pointer.down:
            self._dragv = None
            return
        if self._dragv is None:
            area = (lay.card_x, lay.card_y0, lay.card_w,
                    lay.view_bottom - lay.card_y0)
            if not self._cards_scrollable() or not self._in(px, py, area):
                return
            self._dragv = py
            return
        step = max(1, lay.card_h + lay.gap)
        delta = self._dragv - py           # finger up -> content down
        moved = False
        while delta >= step and self.mtop < self._max_mtop():
            self.mtop += 1
            delta -= step
            moved = True
        while delta <= -step and self.mtop > 0:
            self.mtop -= 1
            delta += step
            moved = True
        self._dragv = py + delta           # keep the sub-step remainder
        if moved:
            ws._dirty = True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if self.meta is not None:
            return self._meta_pointer(px, py, click)
        # SYSTEM coords (#39 step 3): hit-test the raw pointer, no _game_xy.
        self._cards_drag(px, py)           # held drag scrolls the card column
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        if click and self._in(px, py, self.layout.info_btn):
            self._open_meta()
            return True
        ci = self._card_at(px, py)
        if ci is not None:
            if ci != self.msel:
                # Hover highlight -- marking dirty on the CHANGE (#177): the
                # window content freeze reuses the retained buffer on
                # position-only frames, so an unmarked msel move painted
                # nothing inside the Editor window (desktop tier + web).
                ws._dirty = True
            self.msel = ci
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

    # -- edit-field validation (#94) -----------------------------------------

    def _validate_field(self, f):
        """Sanity-check ONE `edit` field definition: returns a short human
        reason it can't be drawn/stepped, or None when it's fine. Checked once
        per row (in _card_height/_card_layout, so every call site that touches
        `f` before this line goes through it first) and again by ws.adjust
        before it mutates config, so a bad hand-edited manifest.json/config.json
        degrades to one inline "!" card + a no-op stepper instead of taking the
        whole Config tab down -- draw()'s try/except stays as the belt-and-
        braces net for a genuinely UNFORESEEN crash; this catches the KNOWN-bad
        shapes (missing key/type, min>max, a zero step, empty/missing choices,
        an unknown or type-mismatched `display`) with a message a kid's parent
        (or the kid, tapping past it) can actually read."""
        if not isinstance(f, dict):
            return "not a card"
        key = f.get("key")
        if not key or not isinstance(key, str):
            return "missing key"
        t = f.get("type")
        if t not in ("int", "choice"):
            return "bad type %r" % (t,)
        if t == "int":
            lo, hi = f.get("min"), f.get("max")
            if lo is not None and hi is not None:
                try:
                    if float(lo) > float(hi):
                        return "min > max"
                except (TypeError, ValueError):
                    return "bad min/max"
            step = f.get("step", 1)
            try:
                if float(step) == 0:
                    return "step is 0"
            except (TypeError, ValueError):
                return "bad step"
        else:                                             # "choice"
            ch = f.get("choices")
            if not isinstance(ch, list) or not ch:
                return "no choices"
        disp = f.get("display")
        if disp is not None and disp not in self._DISPLAYS:
            return "bad display %r" % (disp,)
        if disp in ("gauge", "count") and t != "int":
            return "display needs type int"
        if disp in self._CELL_DISPLAYS and t != "choice":
            return "display needs type choice"
        return None

    # -- geometry / scroll ---------------------------------------------------

    def _card_height(self, f):
        lay = self.layout
        if self._validate_field(f):        # a malformed field never reaches
            return lay.card_h              # _card_display -- see _draw_bad_card
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
        stays within layout.view_bottom. Returns dicts: {i, f, display, x, y, w, h,
        error} -- `error` (#94) is None for a well-formed field, else the short
        reason _validate_field gave; `display` is forced None on an errored row
        (_draw_card/_card_tap branch off `error` before ever reading `display`)."""
        ws = self.ws
        lay = self.layout
        rows = []
        y = lay.card_y0
        top = self._clamp_mtop()
        for i in range(top, len(ws.project.cart["edit"])):
            f = ws.project.cart["edit"][i]
            err = self._validate_field(f)
            h = self._card_height(f)
            if i > top and y + h > lay.view_bottom:
                break                       # next row would spill past the buttons
            rows.append({"i": i, "f": f,
                         "display": None if err else self._card_display(f),
                         "x": lay.card_x, "y": y, "w": lay.card_w, "h": h,
                         "error": err})
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
            if row.get("error"):
                return                 # a malformed card def can't be stepped (#94)
            if row["display"] in self._CELL_DISPLAYS:
                for k, cell in self._choice_cells(row):
                    if self._in(px, py, cell):
                        f = row["f"]
                        old = ws.project.config.get(f["key"], f.get("default"))
                        new = f["choices"][k]
                        ws.project.config[f["key"]] = new
                        ws.project.record_config(f["key"], old, new)  # #111 phase 4
                        return
            ws.adjust(-1 if px < self.layout.card_x + self.layout.card_w // 2 else 1)
            return

    # -- draw ----------------------------------------------------------------

    def _tones(self):
        """Per-draw color roles: the frozen literals on the 320x240 baseline
        (byte-identical), the semantic theme tokens on the shelf tiers -- the
        Phase 3 warm tool surface (visual identity v1 Section 10): cream body,
        dark ink, the orange authoring accent on the value controls."""
        NAMES = self._NAMES
        # The frozen 320x240 literals hold only in DARK chrome; a light theme
        # variant themes the base tier too (owner ask 2026-07-23).
        if self.layout._base and not self.ws.look.light_chrome():
            return {"body": NAMES["dark_purple"], "edge": NAMES["pink"],
                    "head": NAMES["white"], "text": NAMES["light_grey"],
                    "sel_text": NAMES["white"], "row": NAMES["indigo"],
                    "accent": NAMES["yellow"], "track": NAMES["dark_grey"],
                    "knob": NAMES["white"], "cell": NAMES["dark_purple"],
                    "cell_edge": NAMES["dark_grey"]}
        th = self.ws.theme_colors
        return {"body": th["surface"], "edge": th["border"],
                "head": th["ink"], "text": th["ink"],
                "sel_text": th["selection_ink"], "row": th["hilite"],
                "accent": th["author"], "track": th["ink_dim"],
                "knob": th["ink"], "cell": th["dim"],
                "cell_edge": th["ink_dim"]}

    def _draw_cards(self):
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        t = self._t = self._tones()
        # Fullscreen "Make it mine" panel below the unified bar (fix B/C): edge to
        # edge, no centered mini-card. GO/CODE/CLOSE are gone -- PLAY/Code-tab/X are all
        # in the bar (drawn after this by the layer).
        cv.rect(*(lay.body + (t["body"],)))
        cv.rectb(*(lay.body + (t["edge"],)))
        ws._glyph("edit", lay.head_glyph, t["accent"], cv)  # pencil = "make it yours"
        cv.print("MAKE IT MINE", lay.head_xy[0], lay.head_xy[1], t["head"], 2)
        _ui.mini_btn(cv, lay.info_btn, "INFO", t["accent"])   # #94: CART INFO modal
        for row in self._card_layout():
            self._draw_card(row)
        if self._cards_scrollable():           # up/down chevrons when cards overflow
            _ui.scroll_cues(
                cv, (lay.scroll_up[0], lay.scroll_up[1]),
                (lay.scroll_dn[0], lay.scroll_dn[1]),
                self.mtop > 0, self.mtop < self._max_mtop(), t["accent"], 2)

    def _draw_card(self, row):
        if row.get("error"):
            self._draw_bad_card(row)
            return
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        t = self._t
        i = row["i"]
        x, y, w, h = row["x"], row["y"], row["w"], row["h"]
        sel = (i == self.msel)
        fg = t["sel_text"] if sel else t["text"]
        disp = row["display"]
        # ONE list row: the selection field (the card's full height, starting a
        # pixel above its text) plus the card's label line, which sits 18px in on
        # a stepper card (clear of the -/+ glyphs) and 2px in on a visual one.
        # The stepper glyphs and the picture displays below are this card's own
        # CONTENT -- `row` draws the row, not the card.
        _ui.row(cv, t, (x, y - 1 * fs, w, h), self.card_text(i),
                colors=(t["row"] if sel else None, fg, None), edge=False,
                pad=(18 if disp is None else 2) * fs, text_dy=1 * fs, fs=fs)
        if disp is None:                                # today's plain text card
            ws._glyph("minus", (x, y, 14 * fs, 14 * fs), t["accent"], cv)
            ws._glyph("plus", (x + w - 14 * fs, y, 14 * fs, 14 * fs), t["accent"], cv)
            return
        if disp == "gauge":
            self._draw_gauge(row)
        elif disp == "count":
            self._draw_count(row)
        elif disp == "bg-thumbs":
            self._draw_bg_thumbs(row)
        elif disp in ("choice-icons", "sprite-tiles"):
            self._draw_choice_icons(row)

    def _draw_bad_card(self, row):
        """A card whose `edit` field definition failed _validate_field (#94):
        a short inline "!" warning instead of crashing the whole Config tab.
        draw()'s try/except stays the net for a genuinely unforeseen exception;
        this covers the KNOWN-bad shapes (bad type/min-max/step, missing/empty
        choices, a display that doesn't match its type) so a kid's hand-edited
        manifest degrades to one dead card, not a dead tab."""
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        x, y, w, h = row["x"], row["y"], row["w"], row["h"]
        f = row["f"]
        key = f.get("key") if isinstance(f, dict) else None
        label = str(key) if key else ("card %d" % row["i"])
        # The row FRAME only: the "!" and the reason are content with their own
        # budget (the message is already clipped to 32 chars, which `row`'s
        # symmetric pad would re-clip to 30).
        _ui.row(cv, self._t, (x, y, w, h), None,
                colors=(NAMES["dark_grey"], NAMES["light_grey"], NAMES["red"]),
                fs=fs)
        cv.print("!", x + 4 * fs, y + max(0, (h - 8 * fs) // 2), NAMES["red"], 2)
        msg = ("%s: %s" % (label, row["error"]))[:32]
        cv.print(msg, x + 20 * fs, y + max(0, (h - 8) // 2), NAMES["light_grey"], 1)

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
        t = self._t
        cv.rect(tx0, ty, tw, 3 * fs, t["track"])                    # track
        cv.rect(tx0, ty, int(tw * frac), 3 * fs, t["accent"])       # filled portion
        kx = tx0 + int(tw * frac)
        cv.rect(kx - 1 * fs, ty - 3 * fs, 3 * fs, 9 * fs, t["knob"])   # knob

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
                      self._t["accent"], ws.sys_canvas)

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
        t = self._t
        use_tiles = tiles is not None and ws.project.sheet is not None
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            chosen = (k == sel_k)
            # A choice IS a grid cell. The FRAME is the toolkit's; the picture --
            # a real sprite tile or a chrome glyph -- is this card's own, which is
            # why `cell` hands back the art rect instead of trying to draw it.
            # Deliberately NO hit rect per cell: `_choice_cells` hit-tests
            # arithmetically and `cell` takes no `hits` argument by design.
            img = None
            glyph = None
            if use_tiles:
                img = ws.project.sheet.tile_image(tiles[k] if k < len(tiles) else 0, -1)
            else:
                glyph = icons[k] if k < len(icons) else "dot"
            art = _ui.cell(cv, t, (cx, cy, cw, ch), pad=0, caption_h=0, fs=fs,
                           colors=(NAMES["black"] if chosen else t["cell"],
                                   NAMES["white"],
                                   NAMES["yellow"] if chosen else t["cell_edge"]),
                           glyph=glyph, glyph_draw=ws._glyph)
            if img is not None:
                cv.spr(img, art[0] + (art[2] - 16 * fs) // 2,
                       art[1] + (art[3] - 16 * fs) // 2, 2 * fs)

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
        t = self._t
        for k, (cx, cy, cw, ch) in self._choice_cells(row):
            # A FRAME-ONLY cell (`field` None): the picture is a hand-painted
            # preview of the resulting background, so it draws first into its own
            # inset rect and the cell puts the selection border around it.
            self._draw_bg_thumb(f["choices"][k],
                                (cx + 1 * fs, cy + 1 * fs, cw - 2 * fs, ch - 2 * fs))
            _ui.cell(cv, t, (cx, cy, cw, ch), pad=0, caption_h=0, fs=fs,
                     colors=(None, t["cell_edge"],
                             NAMES["yellow"] if k == sel_k else t["cell_edge"]))

    # -- CART INFO: manifest title/author editing (#94) ----------------------
    #
    # The tracker's gap 1 ("Cart manifest / metadata editing -- title, author,
    # permissions not editable here"): a small modal opened from the header INFO
    # button, editing title/author through Project.commit_manifest (which writes
    # manifest.json via moy_carts.save_manifest_meta). `permissions` stays
    # read-only by design -- see the comment over save_manifest_meta.
    #
    # Typing idiom: exactly the wifi-password field's shape (settings_layer.py
    # _wifi_input) -- while self.meta is open, input is driven PURELY off
    # `i.last_key` (never i.pressed("a")/("b")/nav), because _set_text_mode(True)
    # does not stop the T-Deck's ASCII-mode keyboard from ALSO firing a typed
    # key's game-button alias (w/a/s/d/z/x -> up/left/down/right/a/b) -- typing a
    # letter that collided with a checked button would spuriously fire it. Field
    # switch is Tab (ASCII 9) or a tap, never up/down, for the same reason. The
    # one-frame "armed" guard mirrors block_editor_ui._blk_arm_prompt: the tap/
    # key that OPENED the modal can still be latched on its first input pass, so
    # that pass only arms it -- never types/commits/cancels.

    def _open_meta(self):
        ws = self.ws
        cart = ws.project.cart
        if not cart:
            return
        self.meta = {"title": str(cart.get("title") or "")[:24],
                     "author": str(cart.get("author") or "")[:24],
                     "field": 0, "msg": None, "armed": False}
        ws._set_text_mode(True)             # clean ASCII typing (device keyboard)
        ws.input.release_all()               # EVERYBODY let go -- the shared
                                             # meaning, not a source's "I hold
                                             # nothing" (runtime/input.py)
        try:
            ws.input._pressed = set()
            ws.input._released = set()
            ws.input._last = set()          # device InputState edge snapshot
            ws.input._prev = set()          # host InputState edge snapshot
        except AttributeError:
            pass
        ws._ekey_prev = getattr(ws.input, "last_key", 0) or 0
        if ws.pointer is not None:
            ws.pointer.click = False        # the tap that opened this != a field tap
        ws._dirty = True

    def _close_meta(self):
        self.meta = None
        self.ws._set_text_mode(False)

    def _commit_meta(self):
        ws = self.ws
        m = self.meta
        if m is None:
            return
        title = m["title"].strip()
        author = m["author"].strip()
        if not title:
            m["msg"] = "TITLE CAN'T BE BLANK"
            ws._dirty = True
            return                          # stay open -- never persist a blank title
        if not ws.project.commit_manifest(title=title, author=author):
            m["msg"] = "COULD NOT SAVE"
            ws._dirty = True
            return
        self._close_meta()
        ws._dirty = True

    def _meta_key(self, ch):
        m = self.meta
        if m is None:
            return
        field = "title" if m["field"] == 0 else "author"
        if ch in (8, 127):                  # backspace / delete
            m[field] = m[field][:-1]
            m["msg"] = None
            return
        if ch in (13, 10):                  # Enter -> confirm
            self._commit_meta()
            return
        if ch == 27:                        # Esc -> cancel
            self._close_meta()
            return
        if ch == 9:                         # Tab -> switch field
            m["field"] = 1 - m["field"]
            return
        if not (32 <= ch < 127):
            return
        if len(m[field]) >= 24:             # matches the launcher/toast title cap
            return
        m[field] += chr(ch)
        m["msg"] = None

    def _meta_input(self, i):
        """Input while the CART INFO modal is open: last_key ONLY (see the note
        above the section) -- no i.pressed(...) branch, ever."""
        ws = self.ws
        m = self.meta
        if not m.get("armed"):
            m["armed"] = True
            ws._ekey_prev = i.last_key      # don't read the trigger byte as a keystroke
            return True
        k = i.last_key
        if k and k != ws._ekey_prev:
            self._meta_key(k)
        ws._ekey_prev = k
        return True

    def _meta_rects(self):
        """Modal geometry: a centered dialog with a TITLE field, an AUTHOR
        field, a status line and OK/CANCEL, scaled by the system font like
        every other responsive Cards element."""
        lay = self.layout
        fs = lay.fs
        w, h = 240 * fs, 108 * fs
        x = (lay.w - w) // 2
        y = (lay.h - h) // 2
        title_r = (x + 12 * fs, y + 26 * fs, w - 24 * fs, 14 * fs)
        author_r = (x + 12 * fs, y + 54 * fs, w - 24 * fs, 14 * fs)
        ok_r = (x + w - 96 * fs, y + h - 22 * fs, 40 * fs, 16 * fs)
        cancel_r = (x + w - 50 * fs, y + h - 22 * fs, 40 * fs, 16 * fs)
        return (x, y, w, h), title_r, author_r, ok_r, cancel_r

    def _meta_pointer(self, px, py, click):
        if not click:
            return True
        _, title_r, author_r, ok_r, cancel_r = self._meta_rects()
        if self._in(px, py, title_r):
            self.meta["field"] = 0
        elif self._in(px, py, author_r):
            self.meta["field"] = 1
        elif self._in(px, py, ok_r):
            self._commit_meta()
        elif self._in(px, py, cancel_r):
            self._close_meta()
        self.ws._dirty = True
        return True

    def _draw_meta_modal(self):
        NAMES = self._NAMES
        ws = self.ws
        cv = ws.sys_canvas
        fs = self.layout.fs
        m = self.meta
        (x, y, w, h), title_r, author_r, ok_r, cancel_r = self._meta_rects()
        _ui.dialog(cv, (x, y, w, h), ring=NAMES["yellow"])
        cv.print("CART INFO", x + 10 * fs, y + 8 * fs, NAMES["white"], 1)
        foc_title = m["field"] == 0
        cv.print("TITLE", x + 12 * fs, title_r[1] - 9 * fs, NAMES["light_grey"], 1)
        _ui.text_field(cv, title_r, m["title"], "")
        if foc_title:
            cv.rectb(title_r[0], title_r[1], title_r[2], title_r[3], NAMES["yellow"])
        cv.print("AUTHOR", x + 12 * fs, author_r[1] - 9 * fs, NAMES["light_grey"], 1)
        _ui.text_field(cv, author_r, m["author"], "(optional)")
        if not foc_title:
            cv.rectb(author_r[0], author_r[1], author_r[2], author_r[3], NAMES["yellow"])
        if m.get("msg"):
            bad = ("BLANK" in m["msg"] or "FAILED" in m["msg"] or "SAVE" in m["msg"])
            cv.print(m["msg"][:34], x + 12 * fs, y + h - 38 * fs,
                     NAMES["red"] if bad else NAMES["green"], 1)
        _ui.game_btn(cv, ok_r, "OK", NAMES["green"])
        _ui.game_btn(cv, cancel_r, "X", NAMES["dark_grey"])
