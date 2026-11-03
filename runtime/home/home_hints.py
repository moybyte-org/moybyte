# Map (grep -n a name to jump there):
#   HomeHints                the mixin the home's hint strip lives on (its first base)
#   modal_keys               the keys a system modal's strip names
#   HomeHints._hint_groups   the selected card's verbs and star, as the strip names them
#   HomeHints._draw_hints    the hint strip along the home's foot
#   HomeHints._hint_tap      a tap on a hint
#   HomeHints._hint_keys     a verb's letter, the star's, from a keyboard
"""The home's HINT STRIP: the keycap hints along the home's foot -- the
selected card's verbs and its star, the tab row's keys while it has them, a
system modal's while one holds them (`modal_keys`) -- fitted to the strip,
drawn on the wallpaper's ink, and their taps and their letters on a
keyboard. A mixin over `LauncherHomeLayer`, its FIRST base: a band frame
draws the strip beside the band (`_beside`) on every moving frame, and on
MicroPython a method the class lacks is looked up through its bases in
order. It owns no state of its own: the tab row's focus and the selection
it names are the layer's.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
    from home_grid import _VERB_HINT
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb
    from runtime.home_grid import _VERB_HINT
_in = _ui.rect_in   # one hit-test (ui.rect_in)

# The strip's words and keys: the star's, the verbs' letters, the strips'
# keys a modal or the tab row names, the strip's lead.
_UNFAVOURITE = "Remove favourite"   # the star's words on a card that wears it
_VERB_KEY = {"c": "change", "/": "search", "n": "new", "y": "copy", "x": "delete",
             "p": "window"}
_FAVOURITE_KEY = "f"     # the selected card's star, on every tab that draws one
# The inputs (ui_draw.INPUTS) that type those letters: a gamepad has no C.
_LETTER_INPUTS = ("keys", "tdeck")
_TAB_KEYS = ("tabs", "Tabs")
_HINT_LEAD = 5       # the handheld's margin ui.hints leads with, at the font
                     # scale: the top bar's (MOY_UI_HINT_LEAD)
_HINT_LEAD_DESK = 8  # the desk's
_MENU_KEYS = ("back", "Close")   # the strip under the system menu
# The strip while the keys are on the tab row: left and right move the focus
# along it, confirm opens the tab it is on, back comes off the row.
_TAB_ROW_KEYS = ("change", "Move", "confirm", "Open", "back", "Back")
_CONFIRM_KEYS = ("confirm", "Choose", "back", "Cancel")   # under the delete confirm


def modal_keys(ws):
    """The keys a home's hint strip names while a system modal holds them:
    the delete confirm's dialog's, the system menu's card's, the bar's while
    the keys are on it, else None (the home's own verbs)."""
    if ws.confirm.open:
        return _CONFIRM_KEYS
    if ws.menu_ui.looked():
        return _MENU_KEYS
    if _ui_draw.on_bar():
        return _ui_draw.BAR_KEYS
    return None


class HomeHints:
    """The hint strip of `LauncherHomeLayer` (its first base)."""

    def _hint_groups(self):
        """The hint strip's two groups: the active tab's verbs on the
        selected card (on the desk, its buttons' and SEARCH), then the tab
        keys; and the verb of each pair. A keyboard types each verb's
        letter, the star last, its words following its state; a gamepad
        shows the verb its confirm does (`launch_selected`), on that key,
        and X for the card's other verbs (`card_verbs`). With the keys on a
        desk button, confirm names that button's verb."""
        items = []
        verbs = []
        zone = self.zone_verbs()
        on = self._button_focus
        if on is not None:
            # the keys on the desk's buttons: confirm presses the one they are on
            buttons = self._desk_buttons()
            on = buttons[min(on, len(buttons) - 1)][0] if buttons else None
        if _ui_draw.hint_kind() not in _LETTER_INPUTS:
            tap = on or self._confirm_verb(zone)
            if tap:
                items.append("confirm")
                items.append(_VERB_HINT[tap][1])
                verbs.append(tap)
            if self.card_verbs():
                items.extend(_VERB_HINT["options"])
                verbs.append("options")
            return items, verbs
        desk = tuple(self._desk_verbs())
        if desk and "new" in zone and "new" not in desk:
            desk = ("new",) + desk           # Make's N, off the card's buttons
        for v in (desk + tuple(v for v in zone if v == "search")) if desk else zone:
            k, words = _VERB_HINT[v]
            if on is not None and (v == on or k == "confirm"):
                if v != on:
                    continue                 # the shelf's tap: no key on the buttons
                k = "confirm"
            items.append(k)
            items.append(words)
            verbs.append(v)
        it = self.favourable()
        if it is not None:
            # the star last, where the strip has room for it (`_hint_spans`)
            items.append(_VERB_HINT["favourite"][0])
            items.append(_UNFAVOURITE if self.ws.carts.is_favorite(it)
                         else _VERB_HINT["favourite"][1])
            verbs.append("favourite")
        return items, verbs

    def _confirm_verb(self, zone):
        """Which of the verbs `zone` the selected card's confirm does: the
        "+ New" card's NEW, a Make card's CHANGE (it opens in the Editor),
        else PLAY or OPEN; "" for none."""
        if self.grid.tab == "make" and "change" in zone:
            return "change"
        return zone[0] if zone[:1] in (("play",), ("open",), ("new",)) else ""

    def _hint_spans(self, cv):
        """[(verb or "tabs", x0, x1)] across the hint strip, and the strip's
        rect: what `_draw_hints` draws and a tap hits."""
        out, rect, _items = self._hint_fit(cv)
        return out, rect

    def _hint_fit(self, cv):
        """(spans, rect, items) of the hint strip: the spans `_hint_spans`
        answers and the verbs' keycap items they hold. Where the verbs run
        into the tab keys the star goes first, then Duplicate (it stays on
        X and in the card's menu); the tab keys go last (design review R4-22)."""
        th = self.ws.theme_colors
        rect = self.grid.foot()[5]
        desk = self.grid.desk()
        items, verbs = self._hint_groups()
        items, verbs = list(items), list(verbs)
        if self.searchable and (getattr(self.ws, "search_query", "")
                                or getattr(self.ws, "search_typing", False)):
            items, verbs = [], []
        inner = self._hint_inner(rect)
        sp = _ui_draw.hints(cv, th, inner, _TAB_KEYS, desk, draw=False)
        dx = rect[0] + rect[2] - self._hint_pad() - sp[0][1] if sp else 0
        while True:
            out = []
            if items:
                for v, (x0, x1) in zip(verbs, _ui_draw.hints(cv, th, inner, items, desk,
                                                              draw=False)):
                    out.append((v, x0, x1))
            if not sp or not out or sp[0][0] + dx > out[-1][2]:
                break
            drop = "favourite" if verbs[-1] == "favourite" else \
                "copy" if "copy" in verbs else None
            if drop is None:
                break
            k = verbs.index(drop)
            del verbs[k]
            del items[2 * k:2 * k + 2]
        if sp and (not out or sp[0][0] + dx > out[-1][2]):
            out.append(("tabs", sp[0][0] + dx, sp[0][1] + dx))
        return out, rect, items

    def _draw_hints(self, cv):
        """The home's hint strip (steps 17 and 18): keycaps naming the
        active tab's verbs on the selected card, from the left, and the tab
        keys at the right (the theme's `keycap`, `ui.hints`'s part). While
        a search query is held it shows in the verbs' place; while the
        system menu or the delete confirm is up, their keys (`modal_keys`)."""
        th = self.ws.theme_colors
        wall = self._wall()
        if wall is not None:
            th = self._wall_theme(th, wall[0])
        keys = modal_keys(self.ws)
        if keys is None and self._tab_keys:
            keys = _TAB_ROW_KEYS
        if keys is not None:
            # the system menu's card or the delete confirm is up over the
            # home: the keys it takes, never the home's verbs
            rect = self.grid.foot()[5]
            _ui_draw.hints(cv, th, self._hint_inner(rect), keys, self.grid.desk())
            return
        spans, rect, items = self._hint_fit(cv)
        n = sum(1 for v, _a, _b in spans if v != "tabs")
        query = getattr(self.ws, "search_query", "")
        typing = getattr(self.ws, "search_typing", False)
        if self.searchable and (query or typing):
            n = 0
        desk = self.grid.desk()
        if n:
            _ui_draw.hints(cv, th, self._hint_inner(rect), items[:2 * n], desk)
        elif self.searchable and (query or typing):
            self._draw_search(cv, th, th["ink"])
        for v, x0, _x1 in spans:
            if v == "tabs":
                left = x0 - (rect[0] + self._hint_lead())
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

    def _hint_lead(self):
        """The margin `ui.hints` leads with here, at the font scale."""
        return (_HINT_LEAD_DESK if self.grid.desk() else _HINT_LEAD) * self.ws.layout.fs

    def _hint_pad(self):
        """The hint strip's margin at either end: the handheld's is the one
        `ui.hints` leads with, the top bar's, the desk's wider."""
        if self.grid.desk():
            return max(24 // _lb.render_scale(), self._hint_lead())
        return self._hint_lead()

    def _hint_inner(self, rect):
        """The strip `ui.hints` draws in for the margin `_hint_pad`."""
        e = self._hint_pad() - self._hint_lead()
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
        C, /, N, Y, X and P the verbs it names, Q and E the tabs, F the
        selected card's favourite star."""
        k = self.ws.input.last_key
        prev = self._kprev
        self._kprev = k
        if not k or k == prev or not (32 < k < 127):
            return False
        ch = chr(k).lower()
        if ch in ("q", "e"):
            self.ws.step_home_tab(-1 if ch == "q" else 1, self.grid)
            if self._tab_keys:
                self._tab_at = self.grid.tab
                self._zone_bump += 1
            return True
        if ch == _FAVOURITE_KEY:
            return self.toggle_favourite()
        v = _VERB_KEY.get(ch)
        if v is None or not (v in self.zone_verbs() or v in self.card_verbs()
                             or (v == "window" and v in self._desk_verbs())):
            return False
        self._do_verb(v)
        return True
