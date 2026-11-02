# Map (grep -n a name to jump there):
#   HomeDetails                   the mixin the home's details and desk buttons live on
#   HomeDetails.card_verbs        the selected card's verbs, its menu's rows
#   HomeDetails._desk_buttons     the desk pane's verb buttons, laid out
#   HomeDetails._draw_details     the desk hero's details: title, facts, words, buttons
#   HomeDetails._details_tap      a tap on the desk pane's buttons
"""The home's DETAILS: what the desk home says of the selected card beside
its hero -- the title, the facts line (`_facts`: kind, plays, the last day),
the description and the rows of verb buttons (docs/theme_and_shell_2026-11.md
§8.10) -- with the buttons' layout and their tap, and the card's verbs a
card's menu lists (`card_verbs`). A mixin over `LauncherHomeLayer`: the
details are drawn on the desk home's painted frames, never on a band frame.
It owns no state of its own: the button focus it draws is the layer's.
"""

try:
    import ui as _ui
    import ui_draw as _ui_draw
    from home_grid import (day_words, desk_button_layout, DESK_ROW_GAP, _dot_top,
                           NEW_TILE_TYPE, PSEUDO_TILE_TYPES, _VERB_HINT)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui as _ui
    from runtime import ui_draw as _ui_draw
    from runtime.home_grid import (day_words, desk_button_layout, DESK_ROW_GAP, _dot_top,
                                   NEW_TILE_TYPE, PSEUDO_TILE_TYPES, _VERB_HINT)
_in = _ui.rect_in   # one hit-test (ui.rect_in)

DESC_LINES = 3       # the desk home's description: at most this many lines,
DESC_EMS = 48        # ... as wide as this many n's: about 60 characters of
                     # running text, the width of the buttons' row


def times_words(n):
    """A play count as the home says it."""
    return "Not yet" if not n else ("Once" if n == 1 else "%d times" % n)


class HomeDetails:
    """The desk hero's details, its verb buttons and the card's verbs of
    `LauncherHomeLayer`."""

    def card_verbs(self):
        """The selected card's verbs beyond its tap, the ONE list every
        input reaches them by: the system menu's card group, a long press
        on the card, a gamepad's X, the desk's buttons. Its favourite star,
        then CHANGE with COPY beside it, then DEL, where
        the active tab has them (`zone_verbs`)."""
        zone = self.zone_verbs()
        out = []
        if self.favourable() is not None:
            out.append("favourite")
        if "change" in zone:
            out.append("change")
            out.append("copy")
        for v in zone:
            if v == "delete":
                out.append(v)
        return out

    def _desk_verbs(self):
        """The desk home's buttons (step 18): the active tab's tap verb,
        then the card's verbs but its star (the hero wears that). On Make
        a real card's are its own, CHANGE first: NEW makes another cart,
        which the shelf's + tile and N keep. None off the desk."""
        if not (self._carousel() and self.grid.desk()):
            return ()
        zone = self.zone_verbs()
        out = [] if "change" in zone and self.grid.tab == "make" else \
            [v for v in zone[:1] if v in ("play", "open", "new")]
        out.extend(v for v in self.card_verbs() if v != "favourite")
        return out

    def _desk_button_top(self):
        """The y of the details pane's first row of buttons."""
        dk = self.grid._geom()[9]
        h, block, u = dk[5][4], dk[5][5], dk[5][6]
        return dk[6] + block - 6 // u - h - dk[8]

    def _desk_buttons(self):
        """[(verb, rect, kind)] of the details pane's buttons over the
        pane's foot (`desk_button_layout`): rows from the first the pane
        keeps (`_desk_button_top`), every one of the card's verbs."""
        verbs = self._desk_verbs()
        if not verbs:
            return []                        # off the desk
        dk = self.grid._geom()[9]
        dx, dw = dk[1], dk[2]
        h, u = dk[5][4], dk[5][6]
        y = self._desk_button_top()
        return [(v, (dx + x, y + r * (h + DESK_ROW_GAP // u) + dy, w, h), kind)
                for v, x, r, w, kind, dy in desk_button_layout(verbs, dw,
                                                               self.ws.layout.fs, u)]

    def _details_tap(self, px, py):
        """A press on the desk home's hero (the active tab's tap) or one of
        its buttons."""
        g = self.grid
        if not g.items:
            return False
        if _in(px, py, g.hero_rect()):
            self._tab_focus = False
            self.ws.defer(self._launch)          # #184: start behind the paint
            return True
        for v, r, _k in self._desk_buttons():
            if _in(px, py, r):
                self._do_verb(v)
                return True
        return False

    def _draw_details(self, cv):
        """The desk home's details pane beside the hero (step 18): the
        selected card's title in the `title` face, its metadata, its facts
        (how often and when it was played, the play record's; who made it,
        its id's), then its buttons. On the home's ground under a theme's
        depth, on a plate in the bar's colours without one."""
        ws = self.ws
        g = self.grid
        if not g.items:
            return
        it = g.items[g.nearest()]
        th = ws.theme_colors
        dk = g._geom()[9]
        dx, dw, hy = dk[1], dk[2], dk[6]
        ts, ms, ks, vs, bh, block, u = dk[5]
        ink, dim = th["ink"], th["ink_dim"]
        name = it.get("title") or ""
        if it.get("type") == NEW_TILE_TYPE:
            name = "New"
        ty = hy + 4 // u
        was = _ui_draw.print_font(_ui_draw.font("title")[0])
        cv.print_scaled(_ui_draw.print_ellipsis(name, dw, ts), dx, ty, ink, ts)
        if was is not None:
            _ui_draw.print_font(was)
        lh = _ui_draw.font()[2]
        facts_on, meta_on = dk[7]
        my = ty + _ui_draw.font("title")[2] * ts + 14 // u
        sep = 8 // u
        x = dx
        for k, t in enumerate(g._meta(it, played=False) if meta_on else ()):
            if k:                                            # the facts' Last
                if x + ms + sep >= dx + dw:
                    break
                cv.rect(x, _dot_top(my, ms, ms), ms, ms, dim)
                x += ms + sep
            cv.print_scaled(_ui_draw.print_fit(t, dx + dw - x, ms), x, my, dim, ms)
            x += _ui_draw.print_width(t, ms) + sep
        if not meta_on:
            my -= lh * ms + 14 // u
        buttons = self._desk_buttons()
        by = self._desk_button_top()
        if it.get("type") not in PSEUDO_TILE_TYPES:
            vy = by - 16 // u - lh * vs
            ky = vy - 6 // u - lh * ks
            if not facts_on:
                ky = by - 6 // u
            # the manifest's description (SPEC.md 3.7) in the room between
            # the metadata and the facts: a measure of DESC_EMS, DESC_LINES
            # lines at most, the last cut with an ellipsis (owner, 2026-10-19)
            gap = 10 // u
            room = min(DESC_LINES, (ky - gap - (my + lh * ms + gap)) // (lh * ms))
            text = it.get("description")
            if text and room > 0:
                tw = min(dw, _ui_draw.print_width("n" * DESC_EMS, ms))
                lines = list(_ui_draw.wrap_px(text, tw, ms))
                if len(lines) > room:
                    lines = lines[:room]
                    lines[-1] = _ui_draw.print_fit(lines[-1], tw - _ui_draw.print_width("...", ms), ms) + "..."
                dy = my + lh * ms + gap
                for line in lines:
                    cv.print_scaled(line, dx, dy, dim, ms)
                    dy += lh * ms
            # the facts' columns stand on the buttons' left edges under them
            # (design review R4-31); a fact past the last button a column on
            col = 150 // u
            facts = self._facts(it) if facts_on else ()
            xs = [r[0] for _v, r, _k in buttons if r[1] == by][:len(facts)]
            while len(xs) < len(facts):
                xs.append(xs[-1] + col if xs else dx)
            for k, (key, val) in enumerate(facts):
                fx = xs[k]
                w = (xs[k + 1] - fx if k + 1 < len(xs) else col) - 10 // u
                w = min(w, dx + dw - fx)
                if w <= 0:
                    break                    # a column past the pane's edge
                cv.print_scaled(key, fx, ky, dim, ks)
                cv.print_scaled(_ui_draw.print_fit(val, w, vs), fx, vy, ink, vs)
        on = self._button_focus
        for k, (v, r, kind) in enumerate(buttons):
            _ui_draw.button(cv, th, r, _VERB_HINT[v][1], kind=kind, app="home",
                            glyph="play" if v in ("play", "open") else None,
                            glyph_draw=self._play_glyph, focus=k == on)

    def _play_glyph(self, kind, rect, c, cv):
        """A details-pane button's glyph: the theme's icon set's (the dock's,
        Files' and the Editor's Play), else the toolkit's own raster."""
        if not self.ws.draw_icon(kind, rect, c, cv):
            _ui_draw.glyph(cv, "run" if kind == "play" else kind, rect, c)

    def _facts(self, it):
        """The details pane's facts of a cart: (caption, value) pairs."""
        g = self.grid
        rec = g.played(it)
        out = [("Played", times_words(rec[0] if rec is not None else 0))]
        if rec is not None and rec[1]:
            out.append(("Last", day_words(rec[1], g.today())))
        cid = it.get("id") or ""
        if g.own_for is not None and g.own_for(it):
            by = "You"
        elif "." in cid:
            by = cid.split(".", 1)[0]
            by = "Moybyte" if by == "moybyte" else by
        else:
            by = None
        if by:
            out.append(("By", by))
        return out
