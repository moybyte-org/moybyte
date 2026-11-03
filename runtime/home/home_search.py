# Map (grep -n a name to jump there):
#   HomeSearch                        the mixin the home's search lives on
#   HomeSearch._draw_search           the search line: the query, the caret, the way out
#   HomeSearch._search_tap            a tap on the search line
#   HomeSearch._handle_search_typing  the keys while a query is typed
"""The home's SEARCH (the Games tab's, the fullscreen home's alone): the
search line drawn in the hint strip's place (or on the ground under touch),
a tap on it, and the keys while a query is typed. A mixin over
`LauncherHomeLayer`, its last base: it runs while search is open, on an
event. Whether search is open and where its line lies (`_search_open`,
`_search_line`) stay on the class: the band and still frames ask them on
every frame. It owns no state of its own: the query is the console's
(`ws.search_query`, `ws.search_typing`).
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
_in = _ui.rect_in   # one hit-test (ui.rect_in)


class HomeSearch:
    """The search line, its tap and its typing of `LauncherHomeLayer`."""

    def _search_label(self, query, typing):
        """The search box's words: "Search: " and the query (a bar caret
        follows it while typed, `_draw_search`), or, where the input in use
        cannot type, that a keyboard is needed (#239 is the on-screen
        keyboard)."""
        if typing and not query and not self.ws.can_type():
            return self.ws.no_keyboard_text
        return "Search: " + query

    def _search_close_items(self):
        """The strip's way out of search: a field's cancel while typing (the
        T-Deck's Mic, where its K types), else back."""
        return ("cancel" if getattr(self.ws, "search_typing", False) else "back", "Close")

    def _draw_search(self, cv, th, ink):
        """The search line (`_search_line`): its words, a bar caret after
        the query while it is typed, and where a strip shows, the key that
        closes it. Returns the line's right edge drawn to."""
        ws = self.ws
        line = self._search_line()
        if line is None:
            return 0
        fs = ws.layout.fs
        query = getattr(ws, "search_query", "")
        typing = getattr(ws, "search_typing", False)
        x = line[0] + self._hint_pad()
        y = line[1] + (line[3] - ws.layout.line_h) // 2
        label = _ui_draw.print_fit(self._search_label(query, typing), line[2] // 2, fs)
        cv.print(label, x, y, ink, 1)
        x += _ui_draw.print_width(label, fs)
        if typing and ws.can_type():
            # the field's caret: a unit wide, a unit over and under the ink
            x += fs
            cv.rect(x, y + fs, fs, ws.layout.line_h - 2 * fs, th["value"])
            x += fs
        if line[3] > 0 and line is self.grid.foot()[5]:
            x += 4 * fs
            sp = _ui_draw.hints(cv, th, (x, line[1], line[0] + line[2] - x, line[3]),
                                self._search_close_items(), self.grid.desk())
            if sp:
                x = sp[-1][1]
        return x

    def _search_tap(self, px, py):
        """A tap on the search line closes search, cleared (under touch its
        only way out but the system menu's Clear search)."""
        line = self._search_line()
        if line is None:
            return False
        spans, _rect = self._hint_spans(self.ws.sys_canvas)
        x1 = next((a for v, a, _b in spans if v == "tabs"), line[0] + line[2])
        if not _in(px, py, (line[0], line[1], x1 - line[0], line[3])):
            return False
        self.ws.close_search(clear=True)
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
        if i.pressed("b"):                          # a gamepad's B: the strip's Close
            ws.close_search(clear=True)
        elif k and k != self._search_kprev:
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
