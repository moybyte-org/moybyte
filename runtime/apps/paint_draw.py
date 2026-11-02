# Map (grep -n a name to jump there):
#   PaintDraw                     Paint's draw half: the first base of paint_app.PaintAppLayer
#   PaintDraw._canvas             the drawing in its view, the cursor and the rail
#   PaintDraw._stroke_frame       a frame only a mark changed: the view's changed part alone
#   PaintDraw._view_page          a desk's drawing viewed on a handheld
#   PaintDraw._more               More: the drawing's verbs
"""Paint's draw half (paint_app.PaintAppLayer): the canvas page, its tools,
rail and drip, the stroke frame that repaints only what a mark changed, the
desk's row, panel and foot, the popovers and More, and the pages a drawing
goes to (a game, a story, Show).

`PaintDraw` is PaintAppLayer's FIRST base: the layer's `draw` (and the WM's
`retained`/`changed`) stay on the class, and every method here they reach
each frame is found at the first base, the frame-path rule's limit. It
takes paint_app's constants at its FOOT: paint_app imports this module after
binding them, so either may load first. MicroPython-safe; a frame allocates
nothing here that it did not before.
"""

try:
    import ui
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime import ui
try:
    from artwork import colour_name, GAME_W, GAME_H
except ImportError:  # pragma: no cover - direct host import
    from runtime.artwork import colour_name, GAME_W, GAME_H
try:
    import moy_loop as _loop
except ImportError:  # pragma: no cover - direct host import
    try:
        from runtime import moy_loop as _loop
    except ImportError:
        _loop = None
try:
    from drawing_pages import shown, dot as _dot
except ImportError:  # pragma: no cover - direct host import
    from runtime.drawing_pages import shown, dot as _dot


class PaintDraw:
    """Paint's draw half, PaintAppLayer's first base (see above)."""

    def _lend(self, title, tools=(), off=(), back=False):
        got = ui.bar(title, tools, off, back)
        if got == ui.UP:
            self.back()
        return got

    def _canvas(self, cv, th):
        lay = self.layout
        d = self.doc
        zw = _zoom_word(self._quarters())
        if lay.desk:
            got = self._lend(self.doc_title())
        else:
            got = self._lend("Paint", ("undo", "redo", zw),
                             (not d.history, not d.future, False))
        if got == 0:
            self._verb("undo")
        elif got == 1:
            self._verb("redo")
        elif got == 2:
            self._verb("zoom")
        if self.pop is None:
            ui.focus("nowhere")       # the arrows are the cursor's
        if lay.desk:
            self._desk_row(cv, th)
        self._tools(cv, th)
        self._drawing(cv, th)
        if lay.desk:
            self._desk_panel(cv, th)
            self._desk_foot(cv, th)
        else:
            self._rail(cv, th)
            self._hints()
        if self.pop == "colours":
            self._colours_pop(cv, th)
        elif self.pop == "brush":
            self._brush_pop()
        elif self.pop == "more":
            self._more()

    def _tools(self, cv, th):
        lay = self.layout
        for i, (icon, key, word) in enumerate(TOOLS):
            if i == SHAPE:
                icon = SHAPES[self.shape][0]
            ui.at(*lay.tools[i])
            label = word + "\t" + key if lay.desk else ""
            if ui.tool(icon, label, {"id": "Tool " + word, "current": i == self.tool}):
                self._pick_tool(i)
            if i == PICK:
                self._drip(cv, lay.tools[i])

    def _drip(self, cv, r):
        """Pick's drip, in the colour under the cursor."""
        x, y, w, h = r
        side = 24 if self.layout.desk else 16 * self.layout.fs
        k = side / 24
        o = (w - side) // 2
        dd = max(3, side // 6)
        c = self._drip_colour()
        cx = x + o + int(5.8 * k)
        cy = y + (h - side) // 2 + int(20 * k)
        cv.rrect(cx - dd // 2, cy - dd // 2, dd, dd, dd // 2, c)

    def _drip_colour(self):
        """The colour under the cursor; while a stroke is drawn, the one it
        was under when the stroke began."""
        if self._down or self._drawing_key:
            return self._drip_c
        d = self.doc
        c = self._drip_c = d.get(min(d.W - 1, self.cur[0]), min(d.H - 1, self.cur[1]))
        return c

    def _drawing(self, cv, th):
        lay = self.layout
        d = self.doc
        vx, vy, vw, vh = lay.view
        dirty = d.take_dirty()
        if dirty is not None:
            cv.rebake(d.image, *dirty)
        spec = self._spec()
        self._view(cv, th, spec)
        g = self._cursor_at()
        if g is not None:
            cv.clip(vx, vy, vw, vh)
            ui.cursor(g[0], g[1], g[2], g[2])
            cv.clip()

    def _cursor_at(self):
        """(x, y, side) of the ring cursor's rect on the glass, or None where
        it is not drawn."""
        if not self._cursor_shown():
            return None
        s = self.size()
        gx, gy = self._to_glass((self.cur[0] - s // 2, self.cur[1] - s // 2))
        vx, vy, vw, vh = self.layout.view
        if not (vx <= gx < vx + vw and vy <= gy < vy + vh):
            return None
        return (gx, gy, max(2, s * self._quarters() // 4))

    def _cursor_shown(self):
        """The ring cursor: the keys', so not under touch, under a popover,
        or while the pointer draws (the pointer is where it draws)."""
        return not self.layout.touch and self.pop is None and not self._down

    def _view(self, cv, th, spec):
        """The view: the drawing on its ground, its frame and thumbs, and a
        shape's outline while it is dragged."""
        lay = self.layout
        d = self.doc
        vx, vy, vw, vh = lay.view
        x, y, w, h, sx, sy, sw, sh, q = spec
        r = lay.radius
        src = self._src
        src[0] = sx
        src[1] = sy
        src[2] = sw
        src[3] = sh
        full = w == vw and h == vh
        if lay.desk:
            cv.rrect(vx, vy, vw, vh, 10, th["panel"])
            hole = self._hole
            hole[0] = x
            hole[1] = y
            hole[2] = w
            hole[3] = h
            cv.shadow(x, y, w, h, 2, 0, 60, 6, 0, 2, hole)
            cv.picture(d.image, x, y, w, h, 0, src)
            cv.rrect(x - 1, y - 1, w + 2, h + 2, 0, None, th["border"])
        else:
            if w < vw or h < vh:
                cv.rrect(vx, vy, vw, vh, r, th["surface_alt"], th["border"])
            cv.picture(d.image, x, y, w, h, r if full else 0, src)
            if full:
                cv.rrect(vx, vy, vw, vh, r, None, th["border"])
            self._thumbs(cv, th, x, y, w, h, sx, sy, sw, sh)
        self._preview(cv)

    # -- the stroke frame ----------------------------------------------------------
    #
    # A frame whose only change is a mark's -- the doc's pixels, a shape's
    # outline, the ring cursor the keys move, the pointer's sprite over them
    # -- repaints the part of the view
    # those changed and nothing else: the page around it, the toolkit's pass
    # included, is the pixels already there. That holds when the canvas keeps
    # them: the last RETAINED_FRAMES frames all painted this page, the same
    # page (`_epoch`, moved by any change but a mark's and by anything the page
    # reads), into this canvas. The view is then painted again inside the
    # union of what those frames and this one changed, by the same calls a
    # whole frame makes, so the pixels are a whole frame's. A recording canvas
    # keeps nothing, and the loop's drawn-frame count is what says the frames
    # were consecutive. In a desk window the WM says instead what the target
    # holds (`retained`, docs/surface_model_v1.md §14.3), and is told what
    # each frame changed (`changed`).

    def _stroke_frame(self, cv, th):
        """Paint this frame as a stroke frame where it may: True when it did
        (the frame is done), False when the whole page must be painted. Either
        way the doc's marks are baked and this frame's change is recorded."""
        self._sf_ran = True
        d = self.doc
        spec = self._spec()
        c = self._chg
        c[0] = c[1] = 1 << 29
        c[2] = c[3] = -(1 << 29)
        dirty = d.take_dirty()
        if dirty is not None:
            cv.rebake(d.image, dirty[0], dirty[1], dirty[2], dirty[3])
            x, y, w, h, sx, sy, sw, sh, q = spec
            dx, dy, dw, dh = dirty
            _grow(c, x + (dx - sx) * q // 4 - 1, y + (dy - sy) * q // 4 - 1,
                  x + ((dx + dw - sx) * q + 3) // 4 + 1, y + ((dy + dh - sy) * q + 3) // 4 + 1)
        pv = self._pv
        _grow(c, pv[0], pv[1], pv[2], pv[3])
        a, b = self._shape0, self._shape1
        if a is not None and b is not None:
            ga = self._to_glass(a)
            gb = self._to_glass(b)
            pv[0] = min(ga[0], gb[0]) - 2
            pv[1] = min(ga[1], gb[1]) - 2
            pv[2] = max(ga[0], gb[0]) + 3
            pv[3] = max(ga[1], gb[1]) + 3
            _grow(c, pv[0], pv[1], pv[2], pv[3])
        else:
            pv[2] = pv[0]
        cu = self._cu
        _grow(c, cu[0], cu[1], cu[2], cu[3])
        g = self._cursor_at()
        if g is not None:
            # the ring reaches five pixels past the rect it is round
            cu[0] = g[0] - 6
            cu[1] = g[1] - 6
            cu[2] = g[0] + g[2] + 6
            cu[3] = g[1] + g[2] + 6
            _grow(c, cu[0], cu[1], cu[2], cu[3])
        else:
            cu[2] = cu[0]
        # The pointer's sprite, drawn over the page after it: where it was
        # and where it is, when it moved. One that reaches past the view is
        # a whole frame.
        sp = self._sp
        x0, y0, x1, y1 = sp[0], sp[1], sp[2], sp[3]
        p = self._surf.pointer()
        if p is not None and p[4]:
            u = self.layout.fs
            sp[0] = p[0] - 1
            sp[1] = p[1] - 1
            sp[2] = p[0] + 8 * u + 1
            sp[3] = p[1] + 13 * u + 1
        else:
            sp[2] = sp[0]
        if sp[0] != x0 or sp[1] != y0 or sp[2] != x1 or sp[3] != y1:
            _grow(c, x0, y0, x1, y1)
            _grow(c, sp[0], sp[1], sp[2], sp[3])
            self._sp_out = (not self._in_view(x0, y0, x1, y1)
                            or not self._in_view(sp[0], sp[1], sp[2], sp[3]))
        else:
            self._sp_out = False
        epoch = self._page_epoch(cv, th, d, spec)
        f = _loop.drawn() if _loop is not None else -1
        ok = self._region(cv, f, epoch, self._said)
        if f >= 0:
            e = self._ring[f % (_RING + 1)]
            e[0] = f
            e[1] = epoch
            e[2] = self._own[0]
            e[3] = self._own[1]
            e[4] = self._own[2]
            e[5] = self._own[3]
            self._ring_cv = cv
        if not ok:
            # A whole frame: until the canvas's every buffer holds this page,
            # the frames after it are asked for now, while nothing is drawn,
            # so the next press is a stroke frame from its first.
            if f >= 0 and self._may(cv) and not self._kept(f + 1, epoch, None):
                self._damage.settle()
            return False
        if c[2] > c[0] and c[3] > c[1]:
            self._clip_r = c
            cv.clip(c[0], c[1], c[2] - c[0], c[3] - c[1])
            self._view(cv, th, spec)
            if g is not None:
                # the toolkit's verbs on this page's context, its frame not
                # begun again: the ring is drawn as a whole frame draws it
                self._ui.resume(cv, th)
                ui.cursor(g[0], g[1], g[2], g[2])
            cv.clip()
            self._clip_r = None
        return True

    def _region(self, cv, f, epoch, held=False):
        """Whether this frame may be a stroke frame; when it may, `_chg`
        becomes the part of the view it repaints: this frame's change and
        those of the frames since the canvas's buffer was last painted -- or,
        where the window said what the target holds (`held`), the part it
        said lags. The frame's own change stays in `_own`, which the ring
        records."""
        o = self._own
        c = self._chg
        o[0] = c[0]
        o[1] = c[1]
        o[2] = c[2]
        o[3] = c[3]
        if f < 0 or self._sp_out or not self._may(cv):
            return False
        if held:
            if self._moved:
                return False
            h = self._word
            _grow(c, h[1], h[2], h[3], h[4])
        elif cv is not self._ring_cv or not self._kept(f, epoch, c):
            return False
        lay = self.layout
        vx, vy, vw, vh = lay.view
        if c[0] < vx:
            c[0] = vx
        if c[1] < vy:
            c[1] = vy
        if c[2] > vx + vw:
            c[2] = vx + vw
        if c[3] > vy + vh:
            c[3] = vy + vh
        # The view's rounded corners blend into what is under them, so a
        # second paint there would not be the first's.
        k = 10 if lay.desk else lay.radius
        if k > 0 and c[2] > c[0] and c[3] > c[1]:
            if ((c[0] < vx + k or c[2] > vx + vw - k)
                    and (c[1] < vy + k or c[3] > vy + vh - k)):
                return False
        return True

    def _in_view(self, x0, y0, x1, y1):
        """Whether rect [x0, y0, x1, y1) is empty or inside the view."""
        if x1 <= x0 or y1 <= y0:
            return True
        vx, vy, vw, vh = self.layout.view
        return vx <= x0 and vy <= y0 and x1 <= vx + vw and y1 <= vy + vh

    def _may(self, cv):
        """Whether the page and the canvas are ones a stroke frame paints."""
        if (self.page is not None or self.pop is not None or self.viewer is not None
                or self.toast is not None or self._touch is None
                or getattr(cv, "covered", False)):
            return False
        n = getattr(cv, "RETAINED_FRAMES", 0)
        return 1 <= n <= _RING

    def _kept(self, f, epoch, c):
        """Whether the RETAINED_FRAMES frames before frame `f` all painted
        page `epoch`, so the buffer `f` draws into holds it; `c`, when given,
        grows over what those frames changed since that buffer was painted."""
        n = self._ring_cv.RETAINED_FRAMES
        ring = self._ring
        for i in range(1, n + 1):
            e = ring[(f - i) % (_RING + 1)]
            if e[0] != f - i or e[1] != epoch:
                return False
            if c is not None and i < n:
                _grow(c, e[2], e[3], e[4], e[5])
        return True

    def _page_epoch(self, cv, th, d, spec):
        """The page's epoch: moved by any change a mark is not (the damage's
        own count) and by anything the page outside the view reads."""
        g = self._damage.gen
        moved = g != self._gen
        self._gen = g
        cvm = self._sig_is(0, cv)
        moved = self._sig_is(1, self.layout) | moved
        moved = self._sig_is(2, th) | moved
        moved = self._sig_is(3, d) | moved
        moved = self._sig_is(4, self.page) | moved
        moved = self._sig_is(5, self.pop) | moved
        moved = self._sig_is(6, self.viewer) | moved
        moved = self._sig_is(7, self.toast) | moved
        h = (self.tool | self.shape << 4 | (1 if self.shape_fill else 0) << 6
             | self.size_i << 7 | self.color << 10 | (1 if self._unsaved else 0) << 16
             | (1 if d.history else 0) << 17 | (1 if d.future else 0) << 18
             | (1 if self._touch else 0) << 20
             | (1 if self._art.editable() else 0) << 21)
        moved = self._sig_eq(8, h) | moved
        moved = self._sig_eq(9, self.zoom) | moved
        for i in range(9):
            moved = self._sig_eq(10 + i, spec[i]) | moved
        moved = self._sig_eq(19, self._drip_colour()) | moved
        # The page moved; a new canvas is a new epoch too, but where the
        # window said what the target holds, it is only a new target.
        self._moved = moved
        if moved or cvm:
            self._epoch += 1
        return self._epoch

    def _sig_is(self, i, v):
        if self._sig[i] is v:
            return False
        self._sig[i] = v
        return True

    def _sig_eq(self, i, v):
        if self._sig[i] == v:
            return False
        self._sig[i] = v
        return True

    def _thumbs(self, cv, th, x, y, w, h, sx, sy, sw, sh):
        """A panned view's thumbs: where the view stands in the drawing."""
        d = self.doc
        u = self.layout.fs
        ink = th["ink_dim"]
        if sw < d.W:
            tw = w - 18 * u
            cv.rrect(x + 6 * u + sx * tw // d.W, y + h - 6 * u, max(6, sw * tw // d.W), 3 * u,
                     1 * u, ink)
        if sh < d.H:
            tw = h - 18 * u
            cv.rrect(x + w - 6 * u, y + 6 * u + sy * tw // d.H, 3 * u, max(6, sh * tw // d.H),
                     1 * u, ink)

    def _preview(self, cv):
        a, b = self._shape0, self._shape1
        if a is None or b is None:
            return
        ga = self._to_glass(a)
        gb = self._to_glass(b)
        vx, vy, vw, vh = self.layout.view
        k = self._clip_r
        if k is not None:
            x0, y0 = max(vx, k[0]), max(vy, k[1])
            cv.clip(x0, y0, min(vx + vw, k[2]) - x0, min(vy + vh, k[3]) - y0)
        else:
            cv.clip(vx, vy, vw, vh)
        c = self.color
        if self.shape == 0:
            cv.line(ga[0], ga[1], gb[0], gb[1], c)
        else:
            x, y = min(ga[0], gb[0]), min(ga[1], gb[1])
            w, h = abs(ga[0] - gb[0]) + 1, abs(ga[1] - gb[1]) + 1
            if self.shape == 1:
                (cv.rect if self.shape_fill else cv.rectb)(x, y, w, h, c)
            else:
                (cv.oval if self.shape_fill else cv.ovalb)(x, y, w, h, c)
        if k is not None:
            cv.clip(k[0], k[1], k[2] - k[0], k[3] - k[1])
        else:
            cv.clip()

    def _two_tone(self, cv, th, x, y, w, h):
        """The colour in use's edge: a line of ink, then one of surface
        within, never a palette colour."""
        cv.rectb(x, y, w, h, th["ink"])
        cv.rectb(x + 1, y + 1, w - 2, h - 2, th["surface"])

    def _rail(self, cv, th):
        lay = self.layout
        u = lay.fs
        ui.at(*lay.colour)
        if ui.tool("", "", {"id": "Colour in use", "current": lay.touch and self.pop == "colours"}):
            self._open("colours")
        x, y, w, h = lay.colour
        cv.rect(x, y, w, h, self.color)
        self._two_tone(cv, th, x, y, w, h)
        if lay.swatch_page is not None:
            x0, y0, s = lay.swatch_page
            first = (self.color // 16) * 16
            for i in range(16):
                sx, sy = x0 + (i % 2) * s, y0 + (i // 2) * s
                cv.rect(sx, sy, s, s, first + i)
                if first + i == self.color:
                    self._two_tone(cv, th, sx, sy, s, s)
            cv.rectb(x0, y0, 2 * s, 8 * s, th["border"])
        ui.at(*lay.brush)
        if ui.tool("", "", {"id": "Brush", "current": self.pop == "brush"}):
            self._open("brush")
        x, y, w, h = lay.brush
        if lay.touch:
            cv.rrect(x, y, w, h, 6 * u, None, th["border"])
        ds = min(self.size(), 8) * u
        cv.rrect(x + (w - ds) // 2, y + (h - ds) // 2, ds, ds, ds // 2, th["ink"])
        ui.at(*lay.more)
        if ui.tool("more", "", {"id": "More", "current": self.pop == "more"}):
            self._open("more")

    def _hints(self):
        lay = self.layout
        if lay.hints is None or lay.touch:
            return
        x, y, w, h = lay.hints
        u = lay.fs
        if self.pop is not None:
            ui.at(x, y, w, h)
            ui.hints(("back", "Close"))
            return
        lead = TOOLS[self.tool][2] + (" ·" if ui.has_symbols() else " -")
        ui.face("caption")
        lw = ui.text_size(lead)[0]
        ui.face()
        ui.at(x + 8 * u, y, lw + 2 * u, h)
        ui.label(lead, {"face": "caption"})
        ui.at(x + 8 * u + lw, y, w - 8 * u - lw, h)
        ui.hints(("confirm", "Draw", "C", "Colours", "M", "More"))

    # -- the desk's row, panel and foot ----------------------------------------------

    def _desk_row(self, cv, th):
        lay = self.layout
        x, y, w, h = lay.row
        d = self.doc
        for bx, icon, verb, word, key, off in (
                (0, "new", "new", "New drawing", "N", False),
                (40, "folder_open", "open", "Open", "O", False),
                (96, "undo", "undo", "Undo", "Ctrl Z", not d.history),
                (136, "redo", "redo", "Redo", "Ctrl Y", not d.future)):
            ui.at(x + bx, y, h, h)
            if ui.tool(icon, word + "\t" + key, {"id": "Row " + verb, "disabled": off}):
                self._verb(verb)
        ui.at(x + 86, y + 6, 1, h - 12)
        ui.rule()
        xr = x + w - h
        ui.at(xr, y, h, h)
        if ui.tool("more", "More\tM", {"id": "Row more", "current": self.pop == "more"}):
            self._open("more")
        xr -= 8
        ui.face("caption")
        verbs = (("Show", "maximize", "show"), ("Wallpaper", "wallpaper", "wallpaper"),
                 ("Put in a story", "storybook", "story"))
        widths = [ui.text_size(v[0])[0] + 48 for v in verbs]
        ui.face()
        for i in range(len(verbs) - 1, -1, -1):
            label, icon, verb = verbs[i]
            xr -= widths[i]
            ui.at(xr, y + (h - 32) // 2, widths[i], 32)
            ui.icon(icon)
            if ui.button(label, "normal", {"id": "Row " + verb}):
                self._verb(verb)
            xr -= 8
        segs = [_zoom_word(z) if z else "Fit" for z in ZOOM_SEG]
        cur = ZOOM_SEG.index(self.zoom) if self.zoom in ZOOM_SEG else 0
        sx = x + 180
        ui.at(sx, y, xr - 8 - sx, h)
        got = ui.seg("", segs, cur, {"id": "Zoom"})
        if got != cur and 0 <= got < len(ZOOM_SEG):
            self._zoom_to(ZOOM_SEG[got])
        ui.at(*lay.rule)
        ui.rule()

    def _desk_panel(self, cv, th):
        lay = self.layout
        px, py, pw, ph = lay.panel
        ui.at(px, py, pw, 20)
        ui.label("Colour", {"tone": "dim", "face": "caption"})
        x, y, w, h = lay.colour
        cv.rect(x, y, w, h, self.color)
        self._two_tone(cv, th, x, y, w, h)
        ui.at(x + w + 12, y, pw - w - 12, h)
        ui.label(colour_name(self.color, _dot()))
        x0, y0, s = lay.swatch_page
        for i in range(64):
            sx, sy = x0 + (i % 8) * s, y0 + (i // 8) * s
            cv.rect(sx, sy, s - 2, s - 2, i)
            if i == 7 or i == 48:
                cv.rectb(sx, sy, s - 2, s - 2, th["ink_dim"])
            if i == self.color:
                self._two_tone(cv, th, sx, sy, s - 2, s - 2)
        ty = y0 + 8 * s + 6
        ui.at(px, ty, pw, 20)
        word = TOOLS[self.tool][2]
        ui.label(word, {"tone": "dim", "face": "caption"})
        sizes = self.sizes()
        ui.at(px - 10, ty + 22, pw + 30, 30)
        got = ui.seg("Size", [str(v) for v in sizes], self.size_i, {"id": "Size"})
        if got != self.size_i and 0 <= got < len(sizes):
            self.size_i = got
        if self.tool == SHAPE:
            ui.at(px - 10, ty + 56, pw + 30, 30)
            got = ui.seg("Shape", [s[1] for s in SHAPES], self.shape, {"id": "Shape"})
            if got != self.shape and 0 <= got < len(SHAPES):
                self.shape = got
            ui.at(px - 10, ty + 90, pw + 30, 30)
            f = 1 if self.shape_fill else 0
            got = ui.seg("Fill", ("Outline", "Filled"), f, {"id": "Fill"})
            if got != f and got in (0, 1):
                self.shape_fill = got == 1

    def _desk_foot(self, cv, th):
        lay = self.layout
        fx, fy, fw, fh = lay.foot
        ui.at(fx + 16, fy, fw - 32, 1)
        ui.rule()
        hints = ("Z", "Zoom", "C", "Colours", "Ctrl Z", "Undo")
        hw = ui.hints_width(hints)
        ui.at(fx + 26, fy + 8, max(0, fw - 52 - hw), 30)
        ui.label(self._status(), {"tone": "dim", "face": "caption"})
        if hw:
            ui.at(fx + fw - 16 - hw, fy + 8, hw, 30)
            ui.hints(hints)

    def _status(self):
        """The desk foot's sentence: the tool, its size, the zoom, the save."""
        dot = _dot()
        q = self._quarters()
        z = ("Fit, " + _zoom_word(q)) if self.zoom == FIT else _zoom_word(q)
        state = "saved" if not self._unsaved else "changed"
        if not self._art.editable():
            state = "only to look at"
        return TOOLS[self.tool][2] + dot + "size %d" % self.size() + dot + z + dot + state

    # -- popovers ---------------------------------------------------------------------

    def _hang(self, w):
        """Hang the rail's popover inside the drawing's frame: its right 2 px
        in from the frame's, its bottom on the frame's bottom."""
        vx, vy, vw, vh = self.layout.view
        u = self.layout.fs
        ui.at(vx + vw - 2 * u - w, vy + vh + 4 * u, w, 1)

    def _colours_pop(self, cv, th):
        lay = self.layout
        u = lay.fs
        if lay.touch:
            vx, vy, vw, vh = lay.view
            cols, s, first = 7, 32 * u, (self.color // 28) * 28 if self.color < 56 else 56
            w = vw
            ui.at(vx, vy + vh + 4 * u, vw, 1)
        else:
            cols, s, first = 8, 18 * u, 0
            w = 162 * u
            self._hang(w)
        shown_ = ui.popover("Colours", w)
        n = 64 - first if not lay.touch else min(28, 64 - first)
        rows = (n + cols - 1) // cols
        head = (14 if lay.touch else 20) * u
        foot = 10 * u if lay.touch else 0
        x, y, pw, ph, _st = ui.part("Swatches", w, head + rows * s + foot, {"disabled": True})
        if lay.touch:
            pages = ("1\u201328", "29\u201356", "57\u201364") if ui.has_symbols() \
                else ("1-28", "29-56", "57-64")
            cur = first // 28
            got = ui.seg("", pages, cur, {"id": "Colour page"})
            if got != cur and 0 <= got < 3:
                self.color = got * 28
        ui.popover()
        if not shown_:
            self.pop = None
            self._damage.all()
            return
        # the head and the grid over the part's well, on the card's ground
        cv.rect(x, y, pw, ph, th["surface"])
        gx = x + (pw - cols * s) // 2
        gy = y + head
        ui.at(gx, y, pw // 2, head - 4 * u)
        ui.label("Colour", {"face": "title"})
        for i in range(n):
            c = first + i
            sx, sy = gx + (i % cols) * s, gy + (i // cols) * s
            cv.rect(sx, sy, s - 2 * u, s - 2 * u, c)
            if c == self.color:
                self._two_tone(cv, th, sx, sy, s - 2 * u, s - 2 * u)
        cv.rectb(gx - u, gy - u, cols * s, rows * s, th["border"])
        self._pop_rect = (gx, gy, cols, rows, s, first)
        on = self.color
        if not lay.touch:
            on = self.swatch
            i = on - first
            if 0 <= i < n:
                ui.cursor(gx + (i % cols) * s, gy + (i // cols) * s, s - 2 * u, s - 2 * u)
        word = colour_name(on, _dot())
        ui.face("caption")
        tw = ui.text_size(word)[0]
        ui.face()
        ui.at(gx + cols * s - 2 * u - tw, y, tw, head - 4 * u)
        ui.label(word, {"tone": "dim", "face": "caption"})

    def _brush_pop(self):
        lay = self.layout
        u = lay.fs
        w = 194 * u
        self._hang(w)
        shown_ = ui.popover("Brush", w)
        word = TOOLS[self.tool][2]
        ui.label(word, {"face": "title"})
        sizes = self.sizes()
        got = ui.seg("Size", [str(v) for v in sizes], self.size_i, {"id": "Size"})
        if got != self.size_i and 0 <= got < len(sizes):
            self.size_i = got
        if self.tool == SHAPE:
            got = ui.seg("Shape", [s[1] for s in SHAPES], self.shape, {"id": "Shape"})
            if got != self.shape and 0 <= got < len(SHAPES):
                self.shape = got
            f = 1 if self.shape_fill else 0
            got = ui.seg("Fill", ("Outline", "Filled"), f, {"id": "Fill"})
            if got != f and got in (0, 1):
                self.shape_fill = got == 1
        ui.popover()
        if not shown_:
            self.pop = None
            self._damage.all()

    def _more_rows(self):
        """More's rows: (verb, label, icon, key), "-" a rule between parts."""
        d = self._doc
        rows = []
        if self.viewer is None:
            rows.append(("new", "New drawing", "new", "N"))
            if self.layout.desk:
                rows.append(("new_game", "New game-size drawing (320×240)"
                             if ui.has_symbols() else "New game-size drawing (320x240)",
                             "new", ""))
        rows.append(("open", "Open…" if ui.has_symbols() else "Open...",
                     "folder_open", "O"))
        rows.append("-")
        if self.viewer is None and not self._art.editable():
            rows.append(("copy", "Copy to My Art", "dup", ""))
        rows.append(("wallpaper", "Use as wallpaper", "wallpaper", "W"))
        if self.viewer is None and d is not None and (d.W, d.H) == (GAME_W, GAME_H):
            rows.append(("game", "Put in a game…" if ui.has_symbols() else "Put in a game...",
                         "export", "G"))
        if self.viewer is not None or self.layout.desk:
            rows.append(("story", "Put in a story…" if ui.has_symbols()
                         else "Put in a story...", "storybook", "T"))
        rows.append(("show", "Show", "maximize", "V"))
        return rows

    def _more(self):
        lay = self.layout
        u = lay.fs
        rows = self._more_rows()
        if lay.desk:
            x, y, w, h = lay.row
            ui.at(x + w - h, y, h, h)
            width = 300
        elif self.viewer is not None:
            width = 194 * u
            ui.at(lay.cx + lay.cw - 6 * u - width, lay.cy - 4 * u, width, 1)
        else:
            width = 182 * u
            self._hang(width)
        shown_ = ui.popover("More", width)
        part = []
        parts = [part]
        for r in rows:
            if r == "-":
                part = []
                parts.append(part)
            else:
                part.append(r)
        pick = None
        keys = ui.hints_width(("back", "Back")) > 0
        for p in parts:
            if not p:
                continue
            got = ui.menu("", [r[1] + ("\t" + r[3] if r[3] and keys else "") for r in p],
                          [r[2] for r in p])
            if got >= 0:
                pick = p[got][0]
        ui.popover()
        if pick is not None:
            self.pop = None
            self._ui.reset()
            self._verb(pick)
        elif not shown_:
            self.pop = None
            self._damage.all()

    # -- the pages ----------------------------------------------------------------------

    def _game_page(self, cv):
        lay = self.layout
        self._lend("Put in a game", (), (), True)
        if self.game is None:
            self.page = None
            return
        got = self.game.draw(cv, lay.page, lay.desk, lay.fs)
        if got is not None:
            self.page = None
            if got:
                self._say(shown(self.game.name) + " is the background of " + got)
            else:
                self._say(self._art.copies.last_error or "Can't put it there", "warn", True)
            self.game = None

    def _story_page(self, cv):
        lay = self.layout
        self._lend("Put in a story", (), (), True)
        if self.story is None:
            self.page = None
            return
        got = self.story.draw(cv, lay.page, lay.desk, lay.fs)
        if got is not None:
            self.page = None
            title, slide, undo = got
            dot = _dot()
            if slide:
                self._say("Added as slide %d of %s%sUndo" % (slide, title, dot),
                          undo=undo)
            else:
                self._say("Can't add it to " + title, "warn", True)
            self.story = None

    def _view_page(self, cv, th):
        lay = self.layout
        v = self.viewer
        q = v.quarters()
        got = self._lend(shown(v.name), (_zoom_word(q), "more"), (False, False))
        if got == 0:
            self._verb("zoom")
        elif got == 1:
            self._open("more")
        if self.pop is None:
            ui.focus("nowhere")
        u = lay.fs
        cx, cy, cw, ch = lay.full
        hint_h = 22 * u if not lay.touch else 0
        if v.zoom == FIT:
            bmp = v.fit_bitmap()
            if bmp is not None:
                x = cx + (cw - bmp.w) // 2
                y = cy + 6 * u
                cv.shadow(x, y, bmp.w, bmp.h, 2, 0, 60, 6, 0, 2, (x, y, bmp.w, bmp.h, 0))
                cv.picture(bmp, x, y, bmp.w, bmp.h)
                cv.rrect(x - 1, y - 1, bmp.w + 2, bmp.h + 2, 0, None, th["border"])
                cap = "%d × %d, made on a desk%sshown at %s" % (
                    v.W, v.H, _dot(), _zoom_word(q)) if ui.has_symbols() else \
                    "%dx%d, made on a desk - shown at %s" % (v.W, v.H, _zoom_word(q))
                ui.at(cx, y + bmp.h + 6 * u, cw, 18 * u)
                ui.label(cap, {"tone": "dim", "face": "caption", "align": "center"})
        else:
            v.view = (cx, cy, cw, ch - hint_h)
            bmp, k = v.window()
            if bmp is not None:
                cv.picture(bmp, cx, cy, bmp.w * k, bmp.h * k)
            mini = v.mini()
            if mini is not None:
                mx = cx + cw - 10 * u - mini.w
                my = cy + 6 * u
                cv.rect(mx - 2 * u, my - 2 * u, mini.w + 4 * u, mini.h + 4 * u, th["surface"])
                cv.picture(mini, mx, my, mini.w, mini.h)
                fx, fy, fw, fh = v.frame()
                cv.rectb(mx + fx // 16, my + fy // 16, max(2, fw // 16), max(2, fh // 16),
                         th["accent"])
                cv.rectb(mx - 2 * u, my - 2 * u, mini.w + 4 * u, mini.h + 4 * u, th["border"])
        if not lay.touch and lay.hints is not None:
            x, y, w, h = lay.hints
            if self.pop is not None:
                ui.at(x, y, w, h)
                ui.hints(("confirm", "Use", "back", "Close"))
            else:
                lead = "View only"
                ui.face("caption")
                lw = ui.text_size(lead)[0]
                ui.face()
                ui.at(x + 8 * u, y, lw + 2 * u, h)
                ui.label(lead, {"face": "caption"})
                ui.at(x + 8 * u + lw + 8 * u, y, w - 16 * u - lw, h)
                if v.zoom == FIT:
                    ui.hints(("Z", "Zoom", "M", "More", "back", "Back"))
                else:
                    ui.hints(("move", "Pan", "Z", "Zoom", "M", "More"))
        if self.pop == "more":
            self._more()

    def _show(self, cv, th):
        """Show: the drawing alone on the whole surface, black around it;
        any key or tap returns."""
        cv.cls(self.names["black"])
        v = self.viewer
        if v is not None:
            w = cv.w
            h = v.H * w // v.W
            if h > cv.h:
                h = cv.h
                w = v.W * h // v.H
            bmp = v.show_bitmap(w, h)
            if bmp is not None:
                cv.picture(bmp, (cv.w - w) // 2, (cv.h - h) // 2, w, h)
            return
        d = self.doc
        dirty = d.take_dirty()
        if dirty is not None:
            cv.rebake(d.image, *dirty)
        w = cv.w
        h = d.H * w // d.W
        if h > cv.h:
            h = cv.h
            w = d.W * h // d.H
        cv.picture(d.image, (cv.w - w) // 2, (cv.h - h) // 2, w, h)

    def _draw_toast(self):
        lay = self.layout
        text, icon, _until, _undo = self.toast
        if self.viewer is not None and self.viewer.zoom == FIT:
            bmp = self.viewer.fit_bitmap()
            u = lay.fs
            cx, cy, cw, ch = lay.full
            h = bmp.h if bmp is not None else ch // 2
            ui.at(cx, cy + 6 * u, cw, h - 10 * u)
        elif lay.desk:
            ui.at(*lay.view)
        else:
            vx, vy, vw, vh = lay.view
            ui.at(vx, vy, vw, vh - 9 * lay.fs)
        ui.toast(text, icon)


# paint_app's tools, zooms and helpers, imported last (see above).
try:
    from paint_app import FIT, PICK, SHAPE, SHAPES, TOOLS, ZOOM_SEG, _RING, _grow, _zoom_word
except ImportError:  # pragma: no cover - direct host import
    from runtime.paint_app import (FIT, PICK, SHAPE, SHAPES, TOOLS, ZOOM_SEG, _RING, _grow,
                                   _zoom_word)
