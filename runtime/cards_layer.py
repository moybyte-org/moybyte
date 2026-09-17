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
of console call sites resolve `console._CARD_H` / `_RUN_BTN` / ...). `NAMES` (palette)
and `_err_text` are injected at construction (the same circular-import dodge the
other extracted UIs use); the rect hit-test is `ui.rect_in`, imported directly. Shared draw toolkit (ws._glyph/_icon_btn)
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
    import text_modes as _modes
except ImportError:  # pragma: no cover - host fallback
    from runtime import text_modes as _modes

try:
    from layout_base import LayoutBase, BASE_W as _BASE_W, BASE_H as _BASE_H
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layout_base import (LayoutBase, BASE_W as _BASE_W,
                                     BASE_H as _BASE_H)

try:
    from editors import KeyEdge, TextEntry, TE_COMMIT, TE_CANCEL
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.editors import KeyEdge, TextEntry, TE_COMMIT, TE_CANCEL

try:
    from widgets import arm_prompt as _arm_prompt
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import arm_prompt as _arm_prompt

_in = _ui.rect_in   # one hit-test (ui.rect_in)


class _Prompt:
    """One open dialog: `kind` names what OK does, `fields` are its TextEntry
    buffers over ONE shared key edge (so Tab does not re-fire the byte that
    switched fields), `field` the focused one, `msg` the inline status line.
    `fields` is built from (label, placeholder, cap) triples."""

    def __init__(self, kind, title, fields):
        self.kind = kind
        self.title = title
        self.labels = tuple(f[0] for f in fields)
        self.hints = tuple(f[1] for f in fields)
        self.edge = KeyEdge()
        self.fields = tuple(TextEntry(f[2], edge=self.edge) for f in fields)
        self.field = 0
        self.msg = None

    def open(self, texts, seed):
        for i, entry in enumerate(self.fields):
            entry.open(texts[i], seed)
        self.edge.seed(seed, guard=True)

    def key(self, ch):
        """One typed byte: Tab moves the focus, anything else goes to the
        focused field; returns the field's event (editors_base.text_key)."""
        if ch == 9:
            self.field = (self.field + 1) % len(self.fields)
            return None
        ev = self.fields[self.field].key(ch)
        if ev is not None:
            self.msg = None
        return ev

    def feed(self, inp):
        """One input frame: the event of a fresh byte, else None (the guarded
        opening pass included)."""
        k = inp.last_key
        if self.edge.arming(k) or not self.edge.hit(k):
            return None
        return self.key(k)



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


# The ADVANCED row (step 5 of docs/text_editing_2026-09.md): the last row of the
# card column, and the ONE door to a project's own files AS FILES. It is here
# rather than in the Files app because the loader has to stay in the loop -- what
# it opens is reached through the Editor, which re-reads the folder on the way
# back (Workstation.open_project_file).
_ADVANCED_ROW = "ADVANCED: FILES IN THIS PROJECT"

# A project file that is an ASSET is opened by the TAB that owns it, never as
# text -- the row is a router, not a viewer. `images/` is deliberately absent: a
# cart's image assets are copies the WALL/GAME flow made from a drawing and no
# tab edits one in place, so the row says so instead of opening the wrong editor.
_ASSET_TABS = {
    "sprites.moygfx": "paint",
    "map.moymap": "map",
    "sounds.json": "music",
    "blocks.json": "blocks",
}
_ASSET_SUBDIR_TABS = {"scenes": "scene"}

# The last row of that list on a cart whose runtime runs more than one script:
# the ONE door that adds a file to a cart (#89). A sentinel rather than a real
# name, because the list's rows come from the store and a real file could be
# called anything -- including this.
_NEW_ROW = "+ NEW SCRIPT"


# The Config tab draws in its OWN palette (`_tones`), not the theme's, so the
# toolkit cannot resolve a widget kind against it without help. Same answer as
# `code_layer._as_theme`: carry the role names the toolkit looks up, each
# pointing at a role this surface already had. A card row is then an ordinary
# "row_chrome" -- bright label at rest, the selection wash when it is the one
# being stepped -- and a skin reaches it (#207).
def _as_theme(t):
    """Add the ui-toolkit token aliases to a tone map, in place."""
    t["chrome_ink"] = t["text"]         # row_chrome REST ink
    t["hilite"] = t["row"]              # row_chrome ON field
    t["selection_ink"] = t["sel_text"]  # row_chrome ON ink
    return t


class CardsLayout(LayoutBase):
    """Responsive "Make it mine" geometry (#39 step 3): the full-width panel, the
    card column + scroll chevrons, the per-display card heights and the picture-cell
    sizes, derived from the SYSTEM canvas size (w, h) + font scale.

    The single hard contract (mirrors Layout/CodeLayout/PaintLayout/...): at
    (w, h, fs) == (320, 240, 1) every field equals the frozen `_CARD_*` module
    constant, byte for byte (the `_base` branch); the responsive formulas only run
    on a larger canvas / bigger font. A bigger panel shows MORE cards at once (the
    view band grows) and the cards span its full width."""

    def __init__(self, w=_BASE_W, h=_BASE_H, font_scale=1,
                 chrome_scale=None):
        LayoutBase.__init__(self, w, h, font_scale, chrome_scale=chrome_scale)
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
        bar_h = 18 * self.cs          # the OS bar's own height (#203)
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

    def __init__(self, ws, names, err_text):
        self.ws = ws
        self._NAMES = names
        self._err_text = err_text
        self.msel = 0                 # selected card in the menu
        self.mtop = 0                 # first card scrolled into view (#3)
        self._t = None                # per-draw tone map (set by _draw_cards)
        self._dragv = None            # drag-to-scroll anchor (held vertical drag)
        # The open dialog (CART INFO or NEW SCRIPT), a `_Prompt`, or None.
        self.prompt = None
        # The ADVANCED row's file list: None when closed, else
        # {"rows", "sel", "top", "msg"} -- see _open_files.
        self.files = None
        sc = ws.sys_canvas
        self.layout = CardsLayout(sc.w, sc.h, getattr(sc, "font_scale", 1))

    def relayout(self, w, h, fs, cs=None):
        """Rebuild the responsive geometry (#39 step 3) -- called by ws._relayout on
        a font-scale change."""
        self.layout = CardsLayout(w, h, fs, chrome_scale=cs)

    def reset(self):
        """Reset the scroll/selection state (called by ws.open on a fresh cart)."""
        self.msel = 0
        self.mtop = 0
        self.files = None
        if self.prompt is not None:
            # never leak an open dialog across a cart switch
            self.prompt = None
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
        # An open dialog draws OVER the bar too -- same order as the block
        # editor's blk_kbd prompt (chrome, then any modal on top).
        if self.prompt is not None:
            self._draw_prompt()

    def handle_input(self, i):
        ws = self.ws
        if self.prompt is not None:
            return self._prompt_input(i)
        if self.files is not None:
            return self._files_input(i)
        n = self._card_count()
        if not n:
            return True
        if i.pressed("up"):
            self.msel = (self.msel - 1) % n
            self._reveal_card(self.msel)
        if i.pressed("down"):
            self.msel = (self.msel + 1) % n
            self._reveal_card(self.msel)
        if i.pressed("left"):
            ws.adjust(-1)
        if i.pressed("right"):
            ws.adjust(1)
        if self._is_advanced(self.msel):
            # The last row is a DOOR, not a stepper: A opens it rather than
            # playing, the way A opens a row in every other list on the console.
            if i.pressed("a") or i.pressed("run"):
                self._open_files()
            else:
                ws._leave_or_home(ws._leave_menu)
            return True
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
            area = (lay.card_x, self._cards_top(), lay.card_w,
                    lay.view_bottom - self._cards_top())
            if not self._cards_scrollable() or not _in(px, py, area):
                return
            self._dragv = py
            return
        was = self.mtop
        self._dragv, self.mtop = _ui.row_drag(self._dragv, py,
                                              max(1, lay.card_h + lay.gap),
                                              self.mtop, self._max_mtop())
        if self.mtop != was:
            ws._dirty = True

    def handle_pointer(self, px, py, click):
        ws = self.ws
        if self.prompt is not None:
            return self._prompt_pointer(px, py, click)
        if self.files is not None:
            if click and ws.bar_layer.handle_bar_tap("menu", px, py):
                return True
            return self._files_pointer(px, py, click)
        # SYSTEM coords (#39 step 3): hit-test the raw pointer, no _game_xy.
        self._cards_drag(px, py)           # held drag scrolls the card column
        if click and ws.bar_layer.handle_bar_tap("menu", px, py):
            return True         # the Editor's lent zone (Stage 4) claimed the tap
        if click and _in(px, py, self.layout.info_btn):
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
            if self._cards_scrollable() and _in(px, py, self.layout.scroll_up):
                self.scroll_cards(-1)
            elif self._cards_scrollable() and _in(px, py, self.layout.scroll_dn):
                self.scroll_cards(1)
            elif ci is not None:
                self._card_tap(px, py, ci)
        return True

    # -- card text / value helpers -------------------------------------------

    def card_text(self, i):
        ws = self.ws
        if self._is_advanced(i):
            return _ADVANCED_ROW
        f = self._fields()[i]
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

    def _fields(self):
        """The open cart's `edit` schema, or () -- the ADVANCED row means the
        card column outlives an empty (or unreadable) one."""
        cart = self.ws.project.cart
        return (cart.get("edit") or ()) if cart else ()

    def _is_advanced(self, i):
        """True when card index `i` is the ADVANCED row -- always the last."""
        return bool(self.ws.project.cart) and i == len(self._fields())

    def _card_layout(self):
        """Pure (no-draw) per-card geometry for the VISIBLE cards so draw and
        hit-test agree (#3). Cards lay out top-down from layout.card_y0 starting at
        the scrolled-in index self.mtop; a row is included only while its bottom
        stays within layout.view_bottom. Returns dicts: {i, f, display, x, y, w, h,
        error} -- `error` (#94) is None for a well-formed field, else the short
        reason _validate_field gave; `display` is forced None on an errored row
        (_draw_card/_card_tap branch off `error` before ever reading `display`)."""
        lay = self.layout
        rows = []
        y = self._cards_top()
        top = self._clamp_mtop()
        fields = self._fields()
        for i in range(top, self._card_count()):
            adv = i >= len(fields)
            f = None if adv else fields[i]
            err = None if adv else self._validate_field(f)
            h = self._row_height(i)
            if i > top and y + h > lay.view_bottom:
                break                       # next row would spill past the buttons
            rows.append({"i": i, "f": f,
                         "display": None if (adv or err) else self._card_display(f),
                         "x": lay.card_x, "y": y, "w": lay.card_w, "h": h,
                         "error": err, "advanced": adv})
            y += h + lay.gap
        return rows

    def _card_count(self):
        """Rows in the card column: the cart's `edit` fields plus the ADVANCED
        row. Zero only when there is no open cart at all."""
        return (len(self._fields()) + 1) if self.ws.project.cart else 0

    def _row_height(self, i):
        return (self.layout.card_h if self._is_advanced(i)
                else self._card_height(self._fields()[i]))

    def _cards_top(self):
        """Where the card column starts. A BROKEN cart spends one row of it on
        the reason (see _draw_cards), so every reader of the column -- draw,
        hit-test and the scroll clamp -- asks here rather than reading card_y0."""
        lay = self.layout
        return lay.card_y0 + (10 * lay.fs if self._broken() else 0)

    def _broken(self):
        """The reason this cart's manifest would not parse, or "" -- what
        moy_carts.load left behind instead of dropping the project."""
        cart = self.ws.project.cart
        return (cart.get("broken") or "") if cart else ""

    def _max_mtop(self):
        """Topmost card index that still leaves the view full from the bottom up:
        walk heights backwards, summing until the next card would no longer fit."""
        lay = self.layout
        n = self._card_count()
        if n == 0:
            return 0
        avail = lay.view_bottom - self._cards_top()
        used = 0
        top = n
        for i in range(n - 1, -1, -1):
            h = self._row_height(i)
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
            if _in(px, py, (row["x"], row["y"], row["w"], row["h"])):
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
            if row.get("advanced"):
                self._open_files()
                return
            if row.get("error"):
                return                 # a malformed card def can't be stepped (#94)
            if row["display"] in self._CELL_DISPLAYS:
                for k, cell in self._choice_cells(row):
                    if _in(px, py, cell):
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
            return _as_theme(
                   {"body": NAMES["dark_purple"], "edge": NAMES["pink"],
                    "head": NAMES["white"], "text": NAMES["light_grey"],
                    "sel_text": NAMES["white"], "row": NAMES["indigo"],
                    "accent": NAMES["yellow"], "track": NAMES["dark_grey"],
                    "knob": NAMES["white"], "cell": NAMES["dark_purple"],
                    "cell_edge": NAMES["dark_grey"]})
        th = self.ws.theme_colors
        return _as_theme(
               {"body": th["surface"], "edge": th["border"],
                "head": th["ink"], "text": th["ink"],
                "sel_text": th["selection_ink"], "row": th["hilite"],
                "accent": th["author"], "track": th["ink_dim"],
                "knob": th["ink"], "cell": th["dim"],
                "cell_edge": th["ink_dim"]})

    def _draw_cards(self):
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        t = self._t = self._tones()
        if self.files is not None:
            self._draw_files()          # the ADVANCED row's list, same panel
            return
        # Fullscreen "Make it mine" panel below the unified bar (fix B/C): edge to
        # edge, no centered mini-card. GO/CODE/CLOSE are gone -- PLAY/Code-tab/X are all
        # in the bar (drawn after this by the layer).
        cv.rect(*(lay.body + (t["body"],)))
        cv.rectb(*(lay.body + (t["edge"],)))
        ws._glyph("edit", lay.head_glyph, t["accent"], cv)  # pencil = "make it yours"
        broken = self._broken()
        if broken:
            # The cart is on the shelf only because load() refused to drop it.
            # Say why, and leave the ADVANCED row below as the way to the file.
            cv.print("MANIFEST BROKEN", lay.head_xy[0], lay.head_xy[1],
                     self._NAMES["red"], 2)
            cv.print(broken[:(lay.card_w // (8 * lay.fs))],
                     lay.card_x, lay.card_y0, t["text"], 1)
        else:
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
        if row.get("advanced"):
            self._draw_advanced(row)
            return
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
        disp = row["display"]
        # ONE list row: the selection field (the card's full height, starting a
        # pixel above its text) plus the card's label line, which sits 18px in on
        # a stepper card (clear of the -/+ glyphs) and 2px in on a visual one.
        # The stepper glyphs and the picture displays below are this card's own
        # CONTENT -- `row` draws the row, not the card.
        _ui.row(cv, t, (x, y - 1 * fs, w, h), self.card_text(i),
                kind="row_chrome", on=sel, edge=False,
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

    def _draw_advanced(self, row):
        """The ADVANCED row: an ordinary list row with a chevron, so it reads as
        somewhere to GO and not as another value to step."""
        cv = self.ws.sys_canvas
        fs = self.layout.fs
        t = self._t
        x, y, w, h = row["x"], row["y"], row["w"], row["h"]
        _ui.row(cv, t, (x, y - 1 * fs, w, h), _ADVANCED_ROW,
                kind="row_chrome", on=(row["i"] == self.msel), edge=False,
                pad=6 * fs, text_dy=1 * fs, fs=fs)
        cv.print(">", x + w - 10 * fs, y + 1 * fs, t["accent"], fs)

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

    # -- ADVANCED: the project's own files (step 5) --------------------------
    #
    # A ROUTER over one project folder, not a viewer: a text-shaped file (by
    # `text_modes`) opens in the shell's editor handle through the SAME request
    # door Notes uses, the cart's main file opens the Code tab, and an asset
    # opens the tab that owns it. Everything it lists comes from the store's
    # `list_files` on the project KIND (moy_carts.PROJECT_KIND), so the panel
    # never spells a cart's layout itself.

    def _open_files(self):
        ws = self.ws
        cart = ws.project.cart
        path = (cart or {}).get("path")
        if not path:
            return
        rows, msg = (), None
        try:
            kind = ws.carts_store.project_kind(path)
            rows = tuple(ws._with_sd(
                lambda: ws.carts_store.list_files(kind, ws.carts_root)))
        except Exception as exc:  # noqa: BLE001 -- an unreadable folder lists none
            msg = self._err_text(exc)
        if self._can_add_source(cart):
            rows = tuple(rows) + (_NEW_ROW,)
        self.files = {"rows": rows, "sel": 0, "top": 0, "msg": msg}
        ws._dirty = True

    def _can_add_source(self, cart):
        """Whether this folder may gain another SCRIPT: a writable cart whose
        runtime actually loads more than main (moy_carts.multi_script). The
        gate is the store's, not a runtime string spelled here."""
        store = self.ws.carts_store
        return bool(self.ws.can_manage and store is not None
                    and store.multi_script(cart))

    def _close_files(self):
        self.files = None
        self.ws._dirty = True

    def _files_visible(self):
        """How many rows the list band fits."""
        lay = self.layout
        return max(1, (lay.view_bottom - lay.card_y0)
                   // (lay.card_h + lay.gap))

    def _files_open(self, name):
        """Route one project file. The main file is the Code tab (that is where
        PLAY, the journal and crash-to-code live); an asset is its own tab; a
        text file goes through the request door and comes BACK here."""
        ws = self.ws
        cart = ws.project.cart
        if cart is None:
            return
        if name == _NEW_ROW:
            self._open_newf()
            return
        # ANY of the cart's scripts, not only main (SPEC.md 4, #89): the Code
        # tab holds a file now, and it is the tab that has PLAY, the journal and
        # crash-to-code. A port's p8.lua used to fall past this into the text
        # handle, where a crash at `p8.lua:412:` had nothing to land on.
        if name in ws.carts_store.cart_sources(cart):
            self._close_files()
            ws.editor_app.set_tab("code")
            ws.open_code_file(name)
            return
        tab = _ASSET_TABS.get(name)
        if tab is None and "/" in name:
            tab = _ASSET_SUBDIR_TABS.get(name.split("/")[0])
        if tab is not None:
            self._close_files()
            ws.editor_app.set_tab(tab)
            return
        if _modes.is_image(name):
            # A cart's OWN image (`images/cover.moyimg`) is a picture, so it
            # takes the picture door -- the same `ws.open_image` the Files
            # router takes, opening it in Paint on this project's kind and
            # writing it back in place. A picture is never refused for its
            # shape: one Paint cannot edit opens read-only.
            self._close_files()
            if not ws.open_image(name, cart=cart):
                self.files = {"rows": (), "sel": 0, "top": 0,
                              "msg": "NO PAINT APP"}
            return
        if "/" in name:
            # Anything else in a subfolder no tab claims. Saying so beats
            # opening a blob as text.
            self.files["msg"] = "NO EDITOR FOR THIS"
            ws._dirty = True
            return
        mode = _modes.mode_for(name)
        self._close_files()
        if not ws.open_project_file(cart, name, mode):
            self.files = {"rows": (), "sel": 0, "top": 0, "msg": "CAN'T OPEN"}

    def _files_rects(self):
        """(row rect, name) for each VISIBLE list row, plus the BACK button --
        the draw pass and the hit-test read the same geometry."""
        lay = self.layout
        f = self.files
        step = lay.card_h + lay.gap
        out = []
        y = lay.card_y0
        for k in range(f["top"], min(len(f["rows"]),
                                     f["top"] + self._files_visible())):
            out.append(((lay.card_x, y, lay.card_w, lay.card_h), f["rows"][k], k))
            y += step
        return out

    def _files_reveal(self):
        f = self.files
        vis = self._files_visible()
        if f["sel"] < f["top"]:
            f["top"] = f["sel"]
        elif f["sel"] >= f["top"] + vis:
            f["top"] = f["sel"] - vis + 1

    def _files_input(self, i):
        ws = self.ws
        f = self.files
        n = len(f["rows"])
        if i.pressed("b") or i.pressed("home"):
            self._close_files()
            if i.pressed("home"):
                ws.go_home()
            return True
        if not n:
            return True
        if i.pressed("up"):
            f["sel"] = (f["sel"] - 1) % n
            self._files_reveal()
            ws._dirty = True
        if i.pressed("down"):
            f["sel"] = (f["sel"] + 1) % n
            self._files_reveal()
            ws._dirty = True
        if i.pressed("a") or i.pressed("run"):
            self._files_open(f["rows"][f["sel"]])
        return True

    def _files_pointer(self, px, py, click):
        if not click:
            return True
        if _in(px, py, self.layout.info_btn):
            self._close_files()
            return True
        for rect, name, k in self._files_rects():
            if _in(px, py, rect):
                self.files["sel"] = k
                self._files_open(name)
                return True
        return True

    def _draw_files(self):
        ws = self.ws
        cv = ws.sys_canvas
        lay = self.layout
        t = self._t
        f = self.files
        cv.rect(*(lay.body + (t["body"],)))
        cv.rectb(*(lay.body + (t["edge"],)))
        ws._glyph("code", lay.head_glyph, t["accent"], cv)
        cv.print("PROJECT FILES", lay.head_xy[0], lay.head_xy[1], t["head"], 2)
        _ui.mini_btn(cv, lay.info_btn, "BACK", t["accent"])
        rows = self._files_rects()
        if not rows:
            cv.print(f["msg"] or "NO FILES", lay.card_x, lay.card_y0,
                     t["text"], lay.fs)
            return
        for rect, name, k in rows:
            _ui.row(cv, t, rect, name, kind="row_chrome", on=(k == f["sel"]),
                    edge=False, pad=6 * lay.fs, text_dy=1 * lay.fs, fs=lay.fs)
        if f["msg"]:
            cv.print(f["msg"][:34], lay.card_x, lay.view_bottom - 8 * lay.fs,
                     self._NAMES["red"], lay.fs)

    # -- the two dialogs: CART INFO (#94) and NEW SCRIPT (#89) ---------------
    #
    # One prompt state machine (`_Prompt`), parameterised by its fields and by
    # what OK means. CART INFO edits the manifest's title/author through
    # Project.commit_manifest (`permissions` stays read-only: see the comment
    # over moy_carts.save_manifest_meta). NEW SCRIPT is the ONE door that adds
    # a file to a cart, on the ADVANCED row and nowhere else, because a console
    # for eight-year-olds does not want a New File button two taps from every
    # cart, and a cart that is one file should stay one file unless somebody
    # went looking.
    #
    # Typing is driven PURELY off `i.last_key` (never i.pressed("a")/("b")/
    # nav): _set_text_mode(True) does not stop the T-Deck's ASCII-mode keyboard
    # from ALSO firing a typed key's game-button alias (w/a/s/d/z/x), so a typed
    # letter that collided with a checked button would fire it. Field switch is
    # Tab (ASCII 9) or a tap, never up/down, for the same reason. The prompt
    # opens through widgets.arm_prompt and its key edge opens GUARDED
    # (editors_base.KeyEdge.seed): the tap/key that opened it can still be
    # latched on its first input pass, and that pass only arms it.

    def _open_prompt(self, kind, title, fields, texts):
        ws = self.ws
        _arm_prompt(ws)
        self.prompt = _Prompt(kind, title, fields)
        self.prompt.open(texts, getattr(ws.input, "last_key", 0) or 0)
        ws._dirty = True

    def _open_meta(self):
        cart = self.ws.project.cart
        if not cart:
            return
        self._open_prompt("meta", "CART INFO",
                          (("TITLE", "", 24), ("AUTHOR", "(optional)", 24)),
                          (str(cart.get("title") or ""),
                           str(cart.get("author") or "")))

    def _open_newf(self):
        self._open_prompt("newf", "NEW SCRIPT", (("NAME", "helpers", 24),),
                          ("",))

    def _close_prompt(self):
        self.prompt = None
        self.ws._set_text_mode(False)
        self.ws._dirty = True

    def _commit_prompt(self):
        p = self.prompt
        if p is None:
            return
        if p.kind == "meta":
            self._commit_meta(p)
        else:
            self._commit_newf(p)

    def _commit_meta(self, p):
        ws = self.ws
        title = p.fields[0].text.strip()
        author = p.fields[1].text.strip()
        if not title:
            p.msg = "TITLE CAN'T BE BLANK"
            ws._dirty = True
            return                          # stay open: never persist a blank title
        if not ws.project.commit_manifest(title=title, author=author):
            p.msg = "COULD NOT SAVE"
            ws._dirty = True
            return
        self._close_prompt()

    def _newf_filename(self, typed):
        """The file a typed name lands on: slugged to letters, digits and
        underscore, under the cart's own runtime extension. `free_file_name`'s
        rule for a project file is "keep it verbatim" (a FORMAT chose the name),
        which is right for manifest.json and wrong for something a person is
        typing now, so the slug is here."""
        cart = self.ws.project.cart or {}
        mainf = cart.get("main", "main.py")
        cut = mainf.rfind(".")
        ext = mainf[cut:] if cut > 0 else ".lua"
        stem = typed.strip()
        if stem.lower().endswith(ext):
            stem = stem[:-len(ext)]
        out = ""
        last = "_"
        for ch in stem.lower():
            if ch == "_" or ch.isalpha() or ch.isdigit():
                out += ch
            elif last != "_":
                out += "_"
            last = out[-1:] or "_"
        out = out.strip("_")[:24]
        if not out or not out[0].isalpha():
            return None
        return out + ext

    def _commit_newf(self, p):
        ws = self.ws
        cart = ws.project.cart
        name = self._newf_filename(p.fields[0].text)
        if name is None:
            p.msg = "NAME IT WITH LETTERS"
            ws._dirty = True
            return
        made = ws._with_sd(lambda: ws.carts_store.add_source(cart, name,
                                                             "-- " + name + "\n"))
        if made is None:
            p.msg = "THAT NAME IS TAKEN"
            ws._dirty = True
            return
        self._close_prompt()
        self._close_files()
        ws.editor_app.set_tab("code")
        ws.open_code_file(made)

    def _prompt_event(self, ev):
        if ev == TE_COMMIT:
            self._commit_prompt()
        elif ev == TE_CANCEL:
            self._close_prompt()

    def _prompt_key(self, ch):
        """One typed byte into the open prompt (the tests' door)."""
        self._prompt_event(self.prompt.key(ch))

    def _prompt_input(self, i):
        self._prompt_event(self.prompt.feed(i))
        return True

    def _prompt_rects(self):
        """Dialog geometry: a centered panel, one 14px field per entry under
        its label, a status line and OK/X, scaled by the system font like
        every other responsive Cards element."""
        lay = self.layout
        fs = lay.fs
        n = len(self.prompt.fields)
        w, h = 240 * fs, (52 + 28 * n) * fs
        x = (lay.w - w) // 2
        y = (lay.h - h) // 2
        fields = tuple((x + 12 * fs, y + (26 + 28 * i) * fs, w - 24 * fs, 14 * fs)
                       for i in range(n))
        ok_r = (x + w - 96 * fs, y + h - 22 * fs, 40 * fs, 16 * fs)
        cancel_r = (x + w - 50 * fs, y + h - 22 * fs, 40 * fs, 16 * fs)
        return (x, y, w, h), fields, ok_r, cancel_r

    def _prompt_pointer(self, px, py, click):
        if not click:
            return True
        _, fields, ok_r, cancel_r = self._prompt_rects()
        for i, r in enumerate(fields):
            if _in(px, py, r):
                self.prompt.field = i
        if _in(px, py, ok_r):
            self._commit_prompt()
        elif _in(px, py, cancel_r):
            self._close_prompt()
        self.ws._dirty = True
        return True

    def _draw_prompt(self):
        NAMES = self._NAMES
        cv = self.ws.sys_canvas
        fs = self.layout.fs
        p = self.prompt
        (x, y, w, h), fields, ok_r, cancel_r = self._prompt_rects()
        _ui.dialog(cv, (x, y, w, h), ring=NAMES["yellow"])
        cv.print(p.title, x + 10 * fs, y + 8 * fs, NAMES["white"], 1)
        for i, r in enumerate(fields):
            cv.print(p.labels[i], x + 12 * fs, r[1] - 9 * fs, NAMES["light_grey"], 1)
            _ui.text_field(cv, r, p.fields[i].text, p.hints[i])
            if i == p.field:
                cv.rectb(r[0], r[1], r[2], r[3], NAMES["yellow"])
        if p.msg:
            cv.print(p.msg[:34], x + 12 * fs, y + h - 38 * fs, NAMES["red"], 1)
        _ui.game_btn(cv, ok_r, "OK", NAMES["green"])
        _ui.game_btn(cv, cancel_r, "X", NAMES["dark_grey"])
