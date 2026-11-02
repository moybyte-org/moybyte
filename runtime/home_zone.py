# Map (grep -n a name to jump there):
#   HomeZone                    the mixin the home's part of the bar lives on
#   HomeZone.zone_verbs         the active tab's verbs on the selected card
#   HomeZone.zone_tabs          the tabs' rects in the lent zone
#   HomeZone.draw_zone          the lent zone drawn: the home's tabs
#   HomeZone.zone_tap           a tap in the lent zone
"""The home's LENT ZONE (Stage 4, #46 zoned bar): the home's tabs drawn in
the bar's zone (`draw_zone`, `_draw_zone_tabs`) and tapped there
(`zone_tap`), and the active tab's verbs on the selected card
(`zone_verbs`, the docs/theme_and_shell_2026-11.md §12 table) the hint
strip and the card's menu name. A mixin over `LauncherHomeLayer`, after its
hints and details among the bases: the bar draws the zone when its key
moves, and the verbs are made again only when what they are made of moves
(`_outside_key`). `zone_gen`, the one int BarLayer's strip cache reads every
frame, stays on the class. It owns no state of its own.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    import layout_base as _lb
    from home_grid import HOME_TABS, NEW_TILE_TYPE, PSEUDO_TILE_TYPES, text_scale
    from moy_store_base import builtin_name as _builtin_name
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _lb
    from runtime.home_grid import HOME_TABS, NEW_TILE_TYPE, PSEUDO_TILE_TYPES, text_scale
    from runtime.moy_store_base import builtin_name as _builtin_name
_in = _ui.rect_in   # one hit-test (ui.rect_in)


class HomeZone:
    """The home's part of the bar of `LauncherHomeLayer`: its tabs and its verbs."""

    def zone_verbs(self):
        """The active tab's verbs on the selected card (§12's table): Games
        PLAY, CHANGE, SEARCH; Mine PLAY, CHANGE; Apps OPEN; Make NEW, CHANGE,
        COPY, DEL. CHANGE, COPY and DEL need a real cart and a store that
        manages projects, and DEL one the kid's (a built-in is copied, never
        deleted: `builtin_name`, the open-cart menu's own test); the "+ New"
        card's verb is NEW. SEARCH is the
        fullscreen home's alone."""
        ws = self.ws
        grid = self.grid
        tab = grid.tab
        sel = grid.selected()
        real = sel is not None and sel.get("path") \
            and sel.get("type") not in PSEUDO_TILE_TYPES
        change = bool(real) and bool(getattr(ws, "can_manage", True))
        if tab == "apps":
            return ("open",) if real else ()
        if tab == "make":
            if not getattr(ws, "can_manage", True):
                return ()                        # a read-only store makes nothing
            if not change:
                return ("new",)
            if _builtin_name(sel["path"]) is not None:
                return ("new", "change", "copy")
            return ("new", "change", "copy", "delete")
        if sel is not None and sel.get("type") == NEW_TILE_TYPE:
            return ("new",)
        out = ("play", "change") if change else (("play",) if real else ())
        if tab == "games" and self.searchable:
            out = out + ("search",)
        return out

    def zone_tabs(self, rect):
        """[(tab id, label, rect)] of the home's tabs in the bar's zone
        `rect` (steps 17 and 18), flush left, as many as fit. A plate may
        reach a chrome unit past the zone above and below, so the label's
        line and a mark under it both fit (B-15)."""
        cs = self.ws.layout.cs
        sc, h, pad, gap = self._zone_tab_metrics()
        h = min(rect[3] + 2 * cs, h)
        y = rect[1] + (rect[3] - h) // 2
        x = rect[0] + 2 * cs
        out = []
        for tid, label in HOME_TABS:
            w = _ui_draw.print_width(label, sc) + 2 * pad
            if x + w > rect[0] + rect[2]:
                break
            out.append((tid, label, (x, y, w, h)))
            x += w + gap
        return out

    def _zone_tab_metrics(self):
        """(label scale, plate height, label pad, gap) of the tabs in the bar:
        the handheld's at the font scale on a plate a unit taller than the
        line either side (the targets'), the desk's a line about 15 glass
        pixels tall on a 28-pixel plate."""
        lay = self.ws.layout
        fs = lay.fs
        if self.grid.desk():
            u = _lb.render_scale()
            sc = text_scale(15 // u, fs)
            return (sc, max(28 // u, _ui_draw.font()[2] * sc + 6 * fs),
                    max(12 // u, 4 * fs), max(6 // u, 2 * fs))
        return fs, lay.line_h + 2 * fs, 6 * fs, 2 * fs

    def _draw_zone_tabs(self, cv, rect):
        """The tabs in the bar (step 17): the active one under the current
        mark, the rest in the dim
        chrome ink; while the keys are the tab row's, the focus pill on the
        one they are on (`focused_tab`), its plate under the label and its
        ring round the plate, so the focused tab and the open one read
        apart."""
        ws = self.ws
        th = ws.theme_colors
        fs = ws.layout.fs
        sc, _h, pad, _gap = self._zone_tab_metrics()
        at = self.focused_tab()
        u = _lb.unit()
        for tid, label, r in self.zone_tabs(rect):
            on = tid == self.grid.tab
            ink = th["chrome_ink_dim"]
            if on:
                got = _ui_draw.current(cv, th, r, r=5 * fs, fs=fs)
                ink = th["chrome_ink"] if got is None else got
            if tid == at:
                _ui_draw.focus(cv, th, r, r=5 * fs, fs=u, pill=True)
                if not on:
                    ink = th["chrome_ink"]
            if sc == fs:
                # the line centred on the plate, as the bar's clock is on the bar,
                # so the two share a baseline
                cv.print(label, r[0] + pad, r[1] + (r[3] - ws.layout.line_h) // 2, ink, 1)
            else:
                lh = _ui_draw.font()[2] * sc
                cv.print_scaled(label, r[0] + pad, r[1] + (r[3] - lh) // 2, ink, sc)
            if tid == at:
                _ui_draw.focus(cv, th, r, r=5 * fs, fs=u, after=True, pill=True)

    def _zone_action_rects(self, rect):
        """{verb: rect} of the hint strip's pairs, where the verbs are
        (`rect`, the zone, unread)."""
        spans, hr = self._hint_spans(self.ws.sys_canvas)
        return {v: (x0, hr[1], x1 - x0, hr[3]) for v, x0, x1 in spans if v != "tabs"}

    def draw_zone(self, cv, rect):
        """The home's lent left zone: the tabs."""
        return self._draw_zone_tabs(cv, rect)

    def zone_tap(self, px, py, rect=None):
        """A tab in the zone, else a verb's hint; anything else falls
        through."""
        ws = self.ws
        for tid, _label, r in self.zone_tabs(
                rect if rect is not None else ws.layout.zone_left):
            if _in(px, py, r):
                self._tab_focus = False
                ws.set_home_tab(tid, self.grid)
                return True
        chips = self._zone_action_rects(
            rect if rect is not None else ws.layout.zone_left)
        for verb, r in chips.items():
            if _in(px, py, r):
                self._do_verb(verb)
                return True
        return False
