# Map (grep -n a name to jump there):
#   Carousel                    the home's row: the centre card scaled by its distance
#   Carousel._geom              its geometry: the handheld's row or the desk's hero and shelf
#   Carousel.anim_frame         the fling, the glide to the centre, the settle
#   Carousel.pointer_frame      one pointer sample over the row
#   Carousel.band_pass          the row as a table of parts side by side, the wallpaper around the cards
#   Carousel._paint_details     the desk's hero and its details
"""The home's CAROUSEL (docs/theme_and_shell_2026-11.md §8.10): the `carousel`
arrangement, the default (the layout's `home`), and the home window's,
always. On the handheld one row of square cover cards slides sideways, the
centre card larger by a function of its distance from the centre; on the
desk's metrics the selected card is the hero beside its details over a
shelf of one-size cards. A `Launcher` (home_grid.py): the same selection
model, its own geometry, motion and draw. Its instances are ws.launcher in
the `carousel` arrangement and ws.projects_grid.
"""

import ui as _ui
import ui_draw as _ui_draw
import layout_base as _lb
from home_grid import (_clean_side, _COVER_BASE, _COVER_SIDE, desk_button_span,
                       _DESK_VERB_SETS, DETAILS_MIN, _dot_top, _draw_star_badge,
                       _HH_CAPTION, _HH_CARD, _HH_GAP, _HH_SQUEEZE, _hint_strip,
                       HOME_SCALE_HH, _in, Launcher, NEW_TILE_TYPE, _NO_KEYS, _PART,
                       _PART_COL, PSEUDO_TILE_TYPES, SHELF_GAP, _TAB_NAME, text_scale)


class Carousel(Launcher):
    """The home's carousel (docs/theme_and_shell_2026-11.md §8.10, the 2026-10
    look's steps 17 and 18), on the class in force. On the handheld one row
    of square cover cards slides sideways, the card at the centre larger: a
    card's size is a function of its distance from the centre and of
    nothing else -- 1.0 at one pitch or more, rising to the centre scale at
    distance 0 -- so a settled row is a still row. The row rests with a card
    at the centre: a drag, a fling or a key moves the offset, and once the
    motion is spent a GLIDE carries the nearest card the rest of the way
    (`flinging` covers both, so every moving frame takes the drag partial).
    `sel` changes only on settle, and so does the bar's zone (`zone_gen`).
    On the desk the selected card is the hero beside its details, over a
    shelf of one-size cards: a key or a tap selects at once and the shelf
    glides to show it two cards in, a drag scrolls the shelf alone, and the
    hero follows the selection (`statics_sel`).

    The cards lie on the home's wallpaper, so a moving frame cannot shift the
    band's pixels (the wallpaper would slide with them): it restores the
    wallpaper around the cards from the band's capture
    (`LauncherHomeLayer._backdrop_fill`) and paints the cards over it."""

    arrangement = "carousel"
    verb_sets = _DESK_VERB_SETS     # the buttons its desk pane keeps rows for

    def __init__(self, items, layout=None, names=None):
        self.motion = "full"
        self.scale = HOME_SCALE_HH
        self._was_drag = False
        self._g_lay = None            # the layout _g was computed for
        self._g_reach = 0
        self._g_keys = True
        self._g = None
        self.layout = None
        self.own_for = None           # optional cart -> bool (ws.carts.is_own)
        self.plays_for = None         # optional cart -> its play record (ws.carts.plays)
        self.today_for = None         # optional () -> today as YYYYMMDD, 0 unknown
        self._cap = None              # the caption laid out: (card, layout, parts)
        self.wall_ink = None          # (white, black) over a wallpaper (_wall_ink)
        self._quiet_for = None        # the geometry `quiet` was computed for
        self._quiet_q = ()
        self._vis_g = None            # the geometry and key `_vis` was laid out for
        self._vis_key = None
        self._vis = None
        Launcher.__init__(self, items, layout, names)

    # -- geometry ------------------------------------------------------------

    def _geom(self):
        """(tabs, band, card, pitch, ring, gap, plate_h, centre_y, foot,
        desk): the handheld's (`_geom_hh`) or the desk's (`_geom_desk`) on
        the class in force. A card is square (a cover is, SPEC.md 3.6) and
        shows its whole cover at a clean scale (`_clean_side`)."""
        lay = self.layout
        reach = _ui_draw.mark_reach("card")    # the centre card's focus mark
        keys = _ui_draw.hint_kind() != "touch"    # touch alone shows no hint strip
        if self._g_lay is lay and self._g_reach == reach and self._g_keys == keys:
            return self._g
        self._g = (self._geom_hh(lay, reach, keys) if _lb.metric_class() == _lb.HANDHELD
                   else self._geom_desk(lay, reach, keys))
        self._g_lay = lay
        self._g_reach = reach
        self._g_keys = keys
        return self._g

    def _geom_hh(self, lay, reach, keys=True):
        """The handheld home's geometry (docs/history/look_2026-10.md step 17), the
        same tuple with its foot: no tab row (the tabs are the bar's), the
        hero twice a neighbour's side, its title and its metadata under it
        and the hint strip along the content's foot (none with no `keys`: touch
        shows no keycaps, and the cards take its height). A neighbour is half the
        cover's side at the font scale where that fits, and the next clean
        side down (`_clean_side`) where it does not."""
        fs = lay.fs
        cx, cy, cw = lay.cx, lay.cy, lay.cw
        hints = _hint_strip(lay, keys, False)
        tf = _ui_draw.font("hero")
        tsc = max(fs, (16 * fs) // max(1, tf[2]))
        v2 = _ui_draw.font_v2(tf[0])
        # A version 2 face's line holds its leading: the caption lays out on
        # the size class's lines (`_HH_CAPTION`), the title 6 under the hero
        # and 10 over the hints (the targets'), and each line draws on its
        # baseline there, whatever leading the mode's faces set; a pixel
        # face's line is its ink, so 12, 3 and 4.
        if v2:
            tl, tb, mb, ml, ma = _HH_CAPTION
            title_h, meta_h = tl * tsc, ml * fs
            between = tb * tsc + (mb - ma) * fs - title_h
            above, below = 6 * fs, 10 * fs
        else:
            title_h, meta_h = tf[2] * tsc, lay.line_h
            above, between, below = 12 * fs, 3 * fs, 4 * fs
        ring = max(2 * fs + 2, reach)
        space = hints[1] - cy
        # The gaps give before the card does: a face taller than the targets'
        # (Day's against Night's) takes its pixels from over and under the
        # caption, down to `_HH_SQUEEZE`, so every mode of a theme keeps one
        # hero side; only what the gaps cannot hold steps the card down.
        text = title_h + between + meta_h
        lo = (_HH_SQUEEZE[0] * fs, _HH_SQUEEZE[1] * fs)
        card = _clean_side(_HH_CARD * fs)
        while card > _COVER_SIDE // 8 and ring + 2 * card + text + lo[0] + lo[1] > space:
            card = _clean_side(card - 1)
        short = ring + 2 * card + text + above + below - space
        if short > 0:
            give = min(short, above + below - lo[0] - lo[1])
            cut = give * (above - lo[0]) // max(1, above + below - lo[0] - lo[1])
            above, below = above - cut, below - (give - cut)
        foot = above + text + below
        gap = _HH_GAP * fs
        top = cy + max(ring, (space - 2 * card - foot) // 2)
        title_y = top + 2 * card + above
        meta_y = title_y + title_h + between
        if v2:
            # each face on its line's baseline
            meta_y += (ma - _ui_draw.font()[3]) * fs
            title_y += (tb - tf[3]) * tsc
        band = (cx, cy, cw, hints[1] - cy)
        return (None, band, card, card + gap, ring, gap, 0, top + card,
                (title_y, title_h, tsc, meta_y, meta_h, hints), None)

    def _geom_desk(self, lay, reach, keys=True):
        """The desk home's geometry (`_geom_desk_at`) with room in its pane
        for as many rows of buttons as its width wraps them to
        (`desk_button_span`): a pane narrowed by a smaller hero is laid out
        again, the rows it then needs kept."""
        span = 0
        for _ in range(3):
            g = self._geom_desk_at(lay, reach, keys, span)
            dk = g[9]
            got = desk_button_span(dk[2], lay.fs, dk[5][6], dk[5][4], self.verb_sets)
            if got <= span:
                break
            span = got
        return g

    def _geom_desk_at(self, lay, reach, keys, brows):
        """The desk home's geometry (docs/history/look_2026-10.md step 18), the same
        tuple with its foot (the hint strip) and the desk's own: the hero at
        the top left, the details pane beside it, the shelf under them
        bleeding off the right edge, its caption over it. The desk lays out
        in glass pixels (the target's 1x), its type at whole scales of the
        face no smaller than the font scale. The hero is the largest whole
        multiple of the cover's side a quarter of the width holds with the
        shelf under it, else half the cover's side, and a shelf card is half
        the hero, so every card shows its whole cover at a clean scale; the pane
        is as tall as the hero, or as its type needs where that is more.
        Where the pane would be narrower than `DETAILS_MIN` (a narrow
        window, the desk's PROJECTS) the hero stands over it instead, the
        largest clean side the height holds with the pane and the shelf,
        and the pane takes the width -- where that hero is no smaller than
        the one beside it; else the pane stays beside, its buttons as many
        as it holds. The margins narrow with the width, to a twentieth of
        it. Where the height cannot hold the pane over a half-cover shelf,
        the pane drops its facts, then its metadata (the desk's `rows`), and
        the hero halves down to an eighth of the cover. The shelf's caption
        stands `SHELF_GAP` under the details and what height is left goes
        under the shelf (design review R5-08), but under touch (no hint
        strip, no `keys`) the shelf keeps at most the side margin under it,
        the rest over its caption (R3-07). The
        tuple's band is the shelf's: its caption, its cards, their lift
        and their shadows; the desk's own ends with the pane's top and the
        pixels its rows of buttons take under their first, `brows`
        (`desk_button_span`)."""
        fs = lay.fs
        cx, cy, cw = lay.cx, lay.cy, lay.cw
        u = _lb.render_scale()
        hints = _hint_strip(lay, keys, True)
        pad = min(48 // u, max(12 // u, cw // 20))
        top, gap, apart = 24 // u, 20 // u, 32 // u
        ring = max(2 * fs + 2, reach)
        lh, tl = _ui_draw.font()[2], _ui_draw.font("title")[2]
        ts, ms = text_scale(40 // u, fs, "title"), text_scale(14 // u, fs)
        ks, vs = text_scale(13 // u, fs), text_scale(15 // u, fs)
        bh = max(42 // u, 20 * fs)
        need = tl * ts + lh * (ms + ks + vs) + bh + 60 // u + brows
        cap_h = lh * ms + 8 // u
        room = hints[1] - cy - top - cap_h - 72 // u
        # A short window gives up, in turn, most of the slack round the
        # shelf's caption, the facts' row and the metadata's line.
        rows = [True, True]                  # (facts, metadata) shown
        least = _COVER_SIDE // 2
        if max(least, need) + least // 2 > room:
            room += 48 // u
        for k, h in ((0, lh * (ks + vs) + 22 // u), (1, lh * ms + 14 // u)):
            if max(least, need) + least // 2 > room:
                rows[k] = False
                need -= h
        hero = cw // 4 // _COVER_SIDE * _COVER_SIDE
        while hero > _COVER_SIDE and max(hero, need) + hero // 2 > room:
            hero -= _COVER_SIDE
        if hero < _COVER_SIDE or max(hero, need) + hero // 2 > room:
            hero = least
        while hero > _COVER_SIDE // 8 and max(hero, need) + hero // 2 > room:
            hero //= 2
        hx, hy = cx + pad, cy + top
        dx, dy, pane = hx + hero + apart, hy, max(hero, need)
        block = pane
        if cx + cw - pad - dx < DETAILS_MIN // u:
            # too narrow for the pane beside the hero: the hero over the
            # pane where the height holds both at a hero no smaller
            up = _COVER_SIDE
            while up > hero and up + apart + need + up // 2 > room:
                up //= 2
            if up >= hero and up + apart + need + up // 2 <= room:
                hero = up
                dx, dy, pane = hx, hy + hero + apart, need
                block = hero + apart + need
        shelf = hero // 2
        spare = max(0, hints[1] - cy - top - block - cap_h - shelf)
        d = _ui_draw.depth()
        blur = d[8] if d[5] else 0
        down = max(ring, blur + d[10])
        # the caption SHELF_GAP under the details, the spare height under the
        # shelf; a height short of that splits what it has, keeping the
        # shelf's lift and shadow off the strip
        cap_y = hy + block + min(SHELF_GAP // u,
                                 max(spare // 2, spare - max(down, 20 // u)))
        if not keys:
            # no strip under touch: the shelf stands at most the side margin
            # off the glass's foot, and the strip's room goes over its caption
            cap_y = max(cap_y, hints[1] - pad - shelf - cap_h)
        shelf_y = cap_y + cap_h
        band = (cx, cap_y, cw, max(0, min(hints[1], shelf_y + shelf + down) - cap_y))
        desk = ((hx, hy, hero, hero), dx, max(0, cx + cw - pad - dx), shelf_y, pad,
                (ts, ms, ks, vs, bh, pane, u), dy, tuple(rows), brows)
        return (None, band, shelf, shelf + gap, ring, gap, 0, shelf_y + shelf // 2,
                (0, 0, 0, 0, 0, hints), desk)

    def handheld(self):
        """Whether the home is the handheld's (step 17): its tabs in the bar,
        its foot under the band."""
        g = self._geom()
        return g[8] is not None and g[9] is None

    def desk(self):
        """Whether the home is the desk's console home (step 18): the hero,
        its details and the shelf, its tabs in the bar and its foot the
        hint strip."""
        return self._geom()[9] is not None

    def foot(self):
        """(title y, title h, title scale, meta y, meta h, the hint strip's
        rect) on the handheld, the hint strip's rect last on the desk's
        console home, else None."""
        return self._geom()[8]

    def hero_rect(self):
        """The desk home's hero, or None."""
        dk = self._geom()[9]
        return None if dk is None else dk[0]

    def quiet(self):
        """The home's quiet rectangles (docs/theme_and_shell_2026-11.md §3.9), which
        a per-glass wallpaper keeps its subject out of: on the handheld the
        title and the metadata, a column as wide as the hero and half a
        neighbour each side, and the hint strip; on the desk the hero and
        its details, the shelf's band and the hint strip. Computed once per
        geometry."""
        g = self._geom()
        if self._quiet_for is g:
            return self._quiet_q
        foot, dk = g[8], g[9]
        if foot is None:
            q = ()
        elif dk is None:
            title_y, _th, _ts, meta_y, meta_h, hints = foot
            lay = self.layout
            tw = min(lay.cw, 3 * g[2])
            q = ((lay.cx + (lay.cw - tw) // 2, title_y, tw,
                  meta_y + meta_h - title_y), hints)
        else:
            (hx, hy, hero, _h), dx, dw, dy = dk[0], dk[1], dk[2], dk[6]
            q = ((hx, hy, max(dx + dw, hx + hero) - hx,
                  max(hy + hero, dy + dk[5][5]) - hy), g[1], foot[5])
        self._quiet_for = g
        self._quiet_q = q
        return q
    def band_rect(self):
        """The carousel band: every card at any scale, the centre's ring and
        its title plate. The rect the drag partial fills and hands as damage."""
        return self._geom()[1]

    def card_side(self):
        return self._geom()[2]

    def pitch(self):
        return self._geom()[3]

    # -- the offset, the motion, the selection ---------------------------------

    @property
    def sel(self):
        return self._sel

    @sel.setter
    def sel(self, value):
        if value != getattr(self, "_sel", None):
            self.zone_gen += 1
        self._sel = value
        # A selection made from outside (a tab switch, a resume, a remote
        # run) brings its card to the centre; the settle's own assignment
        # lands where the row already rests.
        if self.layout is not None and not self.moving:
            self._scroll = self._rest_of(value)

    def _rest_of(self, i):
        """The offset the row rests at with card `i` selected: at the centre,
        or on the desk's shelf with two cards before it where the shelf has
        them."""
        if self.desk():
            return max(0, min((i - 2) * self.pitch(), self.max_scroll()))
        return max(0, i) * self.pitch()

    @property
    def flinging(self):
        """True while the row coasts on its own: a released fling, or the
        glide that carries the nearest card to the centre (a key's motion
        too) -- the frames that take the drag partial."""
        return self._region.animating

    @property
    def moving(self):
        return self._taps.dragging or self.flinging

    def max_scroll(self):
        g = self._geom()
        if g[9] is not None:
            # the shelf's last card ends its margin short of the band's edge
            n = len(self.items)
            return max(0, (n - 1) * g[3] + g[2] + 2 * g[9][4] - g[1][2]) if n else 0
        return max(0, (len(self.items) - 1) * self.pitch())

    def nearest(self):
        """The card nearest the centre at the current offset; on the desk's
        shelf, the selected one."""
        n = len(self.items)
        if not n:
            return 0
        if self.desk():
            return max(0, min(n - 1, self._sel))
        p = self.pitch()
        return max(0, min(n - 1, (self.scroll + p // 2) // p))

    def set_items(self, items):
        self._region.stop()
        Launcher.set_items(self, items)

    def _scroll_to_sel(self):
        self._region.stop()
        self._scroll = max(0, min(self._rest_of(self.sel), self.max_scroll()))

    def _scroll_region(self):
        band = self.band_rect()
        self._region.set(band, self.max_scroll() + band[2])
        if not (self._region.drag_active or self._region.animating):
            self._region.offset = self.scroll
        return self._region

    def glide_to(self, i):
        """Carry card `i` to the centre, frame by frame (`anim_frame`): on
        the handheld the row slides there, on the desk the shelf glides --
        the scroll model's two eases (`ScrollRegion.glide_to`)."""
        n = len(self.items)
        if not n:
            return
        self._region.stop()
        region = self._scroll_region()
        i = max(0, min(n - 1, i))
        if self.desk():
            # the desk selects at once and the shelf glides to show it
            if i != self._sel:
                self.zone_gen += 1
                self._sel = i
            region.glide_to(self._rest_of(i))
            return
        region.glide_to(i * self.pitch(), slide=self.handheld())

    def nav2d(self, dx, dy):
        """Left/right move the row by a card with the glide; a key held is one
        motion, each repeat pushing the target a card further."""
        if not self.items or not dx:
            return
        if self.desk():
            self.glide_to(self._sel + dx)
            return
        p = self.pitch()
        base = self._region.target
        if base is None:
            base = self.nearest() * p
        self.glide_to(base // p + dx)

    def _snap(self):
        if not self.desk():                  # the desk's shelf rests anywhere
            self.glide_to(self.nearest())

    def anim_frame(self, dt):
        """Advance the fling or the glide one frame (dt in SECONDS), and
        settle the selection once the row is still: a fling that comes to
        rest glides the nearest card the rest of the way. True when the view
        moved."""
        moved = False
        region = self._region
        if region.animating:
            fling = region.flinging
            was = self._scroll
            self._scroll_region()
            if region.tick(dt * 1000.0):
                self._scroll = region.offset   # direct: the setter would stop it
                moved = fling or self._scroll != was
            if fling and not region.animating:
                self._snap()
        if not self.moving and not self.desk():
            c = self.nearest()
            if c != self._sel:
                self.sel = c
        return moved

    def pointer_frame(self, px, py, click, down, dt_ms=None):
        i = Launcher.pointer_frame(self, px, py, click, down, dt_ms)
        drag = self._taps.dragging
        if self._was_drag and not drag and not self._region.animating:
            self._snap()
        self._was_drag = drag
        return i

    def scroll_cols(self, d):
        self.nav2d(d, 0)

    # -- cards -----------------------------------------------------------------

    def _side(self, d, moving, g, big):
        """A card's side at distance `d` px from the centre, under geometry
        `g` with the hero's side `big` (`_big`)."""
        card, pitch = g[2], g[3]
        if (d >= pitch or self.motion == "one"
                or (moving and self.motion == "rest")):
            return card
        return card + (big - card) * (pitch - d) // pitch

    def _big(self):
        """The handheld hero's side at rest: the board's scale, at most 2x."""
        g = self._geom()
        return int(g[2] * min(self.scale, HOME_SCALE_HH) + 0.5)

    def _rect_at(self, i, moving):
        return self._rect_of(i, moving, self._geom(), self.scroll, self._big())

    def _rect_of(self, i, moving, g, s, big):
        """Card `i`'s rect at offset `s` under geometry `g` (`_side`'s)."""
        bx, _by, bw, _bh = g[1]
        if g[9] is not None:
            return (bx + g[9][4] + i * g[3] - s, g[9][3], g[2], g[2])
        mid = bx + bw // 2
        d = i * g[3] - s
        t = self._side(abs(d), moving, g, big)
        if g[8] is not None:
            # The handheld's cards keep their gap beside the hero: each is
            # pushed out by half what the centre card grew, in proportion
            # to its distance up to one pitch.
            grow = self._side(0, moving, g, big) - g[2]
            ad = min(abs(d), g[3])
            push = grow * ad // (2 * g[3])
            d += push if d > 0 else -push
        xc = mid + d
        return (xc - t // 2, g[7] - t // 2, t, t)

    def _visible(self):
        """(index, rect) for every card in the band, smallest first -- the
        order they paint in, so the centre card lands on top. Kept for the
        offset, motion and selection it was laid out at, so a frame's
        restore and its paint lay the row out once; callers read it and
        never change it."""
        n = len(self.items)
        if not n:
            return []
        g = self._geom()
        s = self.scroll
        moving = self.moving
        c = self.nearest()
        key = (s, moving, c, n, self.motion, self.scale)
        if self._vis_g is g and self._vis_key == key:
            return self._vis
        bx, _by, bw, _bh = g[1]
        pitch = g[3]
        if g[9] is not None:
            i0 = max(0, s // pitch - 1)
            i1 = min(n - 1, (s + bw) // pitch + 1)
        else:
            reach = bw // 2 + 2 * g[2]
            i0 = max(0, (s - reach) // pitch)
            i1 = min(n - 1, (s + reach) // pitch + 1)
        out = []
        big = self._big()
        for i in range(i0, i1 + 1):
            r = self._rect_of(i, moving, g, s, big)
            if r[0] + r[2] > bx and r[0] < bx + bw:
                out.append((i, r))
        out.sort(key=lambda e: (e[1][2], e[0] == c))
        self._vis_g, self._vis_key, self._vis = g, key, out
        return out

    def tile_rect(self, i):
        if not (0 <= i < len(self.items)):
            return None
        r = self._rect_at(i, self.moving)
        bx, _by, bw, _bh = self.band_rect()
        if r[0] + r[2] <= bx or r[0] >= bx + bw:
            return None
        return r

    def tile_at(self, px, py):
        bx, by, bw, bh = self.band_rect()
        if not (bx <= px < bx + bw):
            return None
        vis = self._visible()
        for k in range(len(vis) - 1, -1, -1):
            if _in(px, py, vis[k][1]):
                return vis[k][0]
        return None

    def plate_rect(self):
        """The handheld's title and metadata lines across the band; on the
        desk, the details pane beside the hero."""
        g = self._geom()
        bx, by, bw, bh = g[1]
        if g[9] is not None:
            return (g[9][1], g[9][6], g[9][2], g[9][5][5])
        f = g[8]
        return (bx, f[0], bw, f[3] + f[4] - f[0])

    def action_rects(self):
        return None

    def statics_sel(self):
        return self._sel if self.desk() else -1

    def paint_key(self):
        """What the band's pixels depend on besides the offset and the
        statics: the selection, the motion (a card at rest wears its mark),
        whether the tabs or the bar hold the focus (the mark goes), the
        centre card's title and star (its caption's line is laid out once a
        card and a play record, `_draw_caption`)."""
        it = self.items[self.nearest()] if self.items else None
        f = self.favorite_for
        return (self._sel, self.moving, self.tab_focus, it and it.get("title"),
                it is not None and f is not None and bool(f(it)))

    def favorite_rect(self, i):
        """The centre card's favorite-star badge, at rest."""
        if (self.favorite_for is None or not self.items or i is None
                or self.moving or i != self.nearest()):
            return None
        if not (0 <= i < len(self.items)):
            return None
        it = self.items[i]
        if it.get("type") in PSEUDO_TILE_TYPES or not it.get("path"):
            return None
        hero = self.hero_rect()
        if hero is not None and hero[2] * _lb.render_scale() < _COVER_SIDE:
            return None                  # a small hero wears no badge (F, the menu)
        r = hero or self.tile_rect(i)
        return None if r is None else self._favorite_badge_geom(r)

    def cover_specs(self):
        out = []
        for it in self.items:
            if it.get("type") in PSEUDO_TILE_TYPES or not it.get("path"):
                continue
            out.append((it, _COVER_BASE))
        return out

    def draw(self, cv, sheet_for=None, key=_NO_KEYS, over=None, beside=None):
        if self.desk():
            self._paint_details(cv)
        return self.band_pass(cv, sheet_for, None, key, True, over, beside)

    def repaint_band(self, cv, sheet_for, fill, key=_NO_KEYS, over=None, beside=None, need=False,
                     marks=False, whole=False):
        """A band frame: the row (`band_pass`) over the ground `fill` puts
        back around its cards, every card's corner rows with `marks` (the
        buffer may hold the focus mark on another card)."""
        return self.band_pass(cv, sheet_for, fill, key, whole, over, beside, need, marks)

    def band_key(self):
        return self.paint_key()

    def band_pass(self, cv, sheet_for, fill, key=_NO_KEYS, whole=True, over=None, beside=None,
                  need=False, marks=False, note=False):
        """The row as a table of parts side by side that keeps them
        (`ui.table` with `o.axis` "x" and `o.keep`) on the band's own app
        host, the scroll the home's (`o.offset`): on the handheld under
        `o.snap`, row 0 at the centre at scroll 0, each part room past its
        cell for the card its distance from the centre grows (`o.spill`);
        on the desk the shelf's cards one size from its margin. A card's
        size is a function of its distance from the centre, and what the
        centre card shows is in `key` (`band_key`), so the engine's floor
        is the band whole or what was drawn over it (`over`, the cursor),
        or nothing. In each floor rect the ground goes back round the
        cards (`fill`; None: a full paint drew it) and the cards the engine
        says show draw in the order the row paints them (`_paint`).
        `beside`, `note` and the answer are `Launcher.band_pass`'s."""
        band = self.band_rect()
        g = self._geom()
        n = len(self.items)
        held = self._begin_band(cv, band, g[9] is not None, key, need, over)
        if held is None:
            return None
        _ui.at(band[0], band[1], band[2], band[3])
        o = self._bo
        p = o["part"]
        p[0] = g[3]
        p[1] = 0
        o["axis"] = "x"
        if g[9] is None:
            o["snap"] = True
            o["offset"] = self.scroll
            o["spill"] = self._big()
        else:
            o["snap"] = False
            o["offset"] = self.scroll - g[9][4]
            o["spill"] = g[3]
        o["keep"] = key[1]
        o["whole"] = whole or not held
        o["over"] = over
        _ui.table("", _PART_COL, self._band_rows(n), self.nearest(), o)
        if beside is not None:
            beside(cv, whole or not held)
        end = self.band_floor(cv, None, not note)
        if note:
            del self.floor[:]
        shows = self._shows
        if len(shows) != n:
            shows = self._shows = bytearray(n)
        else:
            for k in range(n):
                shows[k] = 0
        r = _PART
        i = _ui.table_part(r)
        while i >= 0:
            shows[i] = 1
            i = _ui.table_part(r)
        for q in self.floor:
            if fill is not None:
                self._fill_around_cards(cv, fill, q, marks)
            self._paint(cv, sheet_for, q)
        _ui.end()
        return end

    def _fill_around_cards(self, cv, fill, r, marks=False):
        """Restore rect `r`'s backdrop except where a card lies: every card
        paints its whole square opaque, so the backdrop is owed only around
        them -- on a big panel most of the band's pixels are the cards'.
        The focus mark's rounded ring blends over the corner rows of the card
        it lies on, past the card's own corner: the selected card's rows
        are restored that far, and with `marks` (the buffer may hold the
        mark on another card) every card's."""
        if self.restore_whole:
            fill(cv, r)
            return
        x0, y0, x1, y1 = r[0], r[1], r[0] + r[2], r[1] + r[3]
        x = x0
        reach = _ui_draw.mark_reach("card") + 1
        on = -1 if marks else self.nearest()
        lim, vis = (x0, self._visible()) if self.handheld() else self._shelf()
        for i, c in sorted(vis, key=lambda e: e[1][0]):
            cx0 = max(c[0], x, lim)
            cx1 = min(c[0] + c[2], x1)
            if cx1 <= cx0:
                continue
            if cx0 > x:
                fill(cv, (x, y0, cx0 - x, y1 - y0))
            ct = max(c[1], y0)
            cb = min(c[1] + c[3], y1)
            k = self._corner_r(c[2])
            if k and (on < 0 or i == on):
                k += reach
            k = min(k, (cb - ct) // 2)
            if k > 0:
                # rounded cards leave their corners' pixels to the backdrop:
                # the rows the corners span are restored too
                ct += k
                cb -= k
            if ct > y0:
                fill(cv, (cx0, y0, cx1 - cx0, ct - y0))
            if cb < y1:
                fill(cv, (cx0, cb, cx1 - cx0, y1 - cb))
            x = cx1
        if x < x1:
            fill(cv, (x, y0, x1 - x, y1 - y0))

    def _corner_r(self, side):
        """The corner radius of a card `side` across under the theme's depth:
        on the handheld its `card_r` on the hero and 7/10 of it on a
        neighbour, on the desk `card_r` on the shelf and 7/5 of it on the
        hero."""
        r = _ui_draw.depth()[1]
        g = self._geom()
        if g[9] is not None:              # the desk's
            return r * 7 // 5 if side > g[2] else r
        return r if side > g[2] else r * 7 // 10

    def _paint(self, cv, sheet_for, r):
        """The band, clipped to rect `r`: the handheld's (`_paint_hh`), or the
        desk's shelf -- its caption, every card's shadow (the theme's depth,
        `ui.cast`; a moving card keeps its own), the cards, the selected one
        last and at rest under the theme's focus mark."""
        if self.handheld():
            return self._paint_hh(cv, sheet_for, r)
        g = self._geom()
        left, vis = self._shelf()
        shows = self._shows
        if r[0] < left:
            r = (left, r[1], max(0, r[0] + r[2] - left), r[3])
        clip = getattr(cv, "clip", None)
        if clip is not None:
            self._card_clip = r
            clip(*r)
        self._draw_shelf_caption(cv)
        c = self.nearest()
        moving = self.moving
        for i, t in vis:
            if shows[i]:
                _ui_draw.cast(cv, self.theme, t, _ui_draw.CAST_CARD, _ui_draw.UNDER_SMALL)
        for i, t in vis:
            if shows[i]:
                self._draw_home_card(cv, self.items[i], t, i == c, sheet_for)
        if clip is not None:
            self._card_clip = None
            clip()

    def _face(self, cv, it, pic, r, cover):
        """A shelf card's face on the desk (`_draw_home_card`'s away from
        the selection's mark): its picture, framed where its corners are
        square."""
        self._face_picture(cv, it, pic, r, cover)
        if not r:
            self._card_frame(cv, pic)

    def _face_div(self, n):
        return _COVER_BASE

    def _face_ring(self):
        return None

    def _shelf(self):
        """(left, cards) of the desk's shelf: it starts at its margin
        `left`, where a card scrolled across it is cut, moving or at rest,
        as the band's right edge cuts one; `cards` the (index, rect) it
        shows."""
        g = self._geom()
        left = g[1][0] + g[9][4] - g[5]
        return left, [e for e in self._visible() if e[1][0] + e[1][2] > left]

    def _draw_shelf_caption(self, cv):
        """The shelf's caption over its first card: the tab and its count,
        apart by a dot, in the dim ink on the home's ground."""
        th = self.theme or {}
        fs = self.layout.fs
        dk = self._geom()[9]
        sc = dk[5][1]
        lh = _ui_draw.font()[2] * sc
        x = self.band_rect()[0] + dk[4]
        y = dk[3] - 8 // dk[5][6] - lh
        name = _TAB_NAME.get(self.tab, "")
        count = str(len(self.items))
        sep = 6 * fs
        nw = _ui_draw.print_width(name, sc)
        dim = th.get("ink_dim", 6)
        cv.print_scaled(name, x, y, dim, sc)
        x += nw + sep
        cv.rect(x, _dot_top(y, sc, sc), sc, sc, dim)
        cv.print_scaled(count, x + sc + sep, y, dim, sc)

    def _paint_details(self, cv):
        """The desk home's hero (a full paint's): its shadow, the
        selected card's picture at the hero's size and its favourite star
        (on a hero of the cover's side or more in glass pixels).
        The details beside it are the home layer's (`_draw_details`)."""
        hero = self.hero_rect()
        if not self.items:
            return
        it = self.items[self.nearest()]
        _ui_draw.cast(cv, self.theme, hero, _ui_draw.CAST_CARD)
        self._draw_picture(cv, it, hero, True)
        if (self.favorite_for is not None and it.get("path")
                and hero[2] * _lb.render_scale() >= _COVER_SIDE
                and it.get("type") not in PSEUDO_TILE_TYPES):
            _draw_star_badge(cv, self._NAMES, self.theme or {},
                             self._favorite_badge_geom(hero),
                             bool(self.favorite_for(it)))
        if not self._corner_r(hero[2]):
            self._card_frame(cv, hero)

    def _paint_hh(self, cv, sheet_for, r):
        """The handheld home's band (docs/history/look_2026-10.md step 17): every
        card's shadow, the hero's (the card nearest the centre) at the
        theme's size and the others' smaller, in motion as at rest; the
        cards, smallest first, the hero last and at rest lifted by the
        theme's focus mark; then the title and the metadata of the hero."""
        clip = getattr(cv, "clip", None)
        if clip is not None:
            self._card_clip = r
            clip(*r)
        c = self.nearest()
        th = self.theme
        vis = self._visible()
        shows = self._shows
        for i, t in vis:
            if not shows[i]:
                continue
            if i == c:
                _ui_draw.cast(cv, th, t, _ui_draw.CAST_CARD)
            else:
                _ui_draw.cast(cv, th, t, _ui_draw.CAST_CARD, _ui_draw.UNDER_SMALL)
        for i, t in vis:
            if shows[i]:
                self._draw_home_card(cv, self.items[i], t, i == c, sheet_for)
        if self.items:
            self._draw_caption(cv, self.items[c])
        if clip is not None:
            self._card_clip = None
            clip()

    def _draw_caption(self, cv, it):
        """The title of the card nearest the centre in the `title` face,
        centred, and its metadata in the `ui` face under it, the items
        apart by a dot: on the home's ground in its ink under a theme's
        depth, on a plate in the bar's colours without one (a legacy
        ground is any colour)."""
        th = self.theme or {}
        lay = self.layout
        fs = lay.fs
        ty, tht, tsc, my, mh, _hints = self._geom()[8]
        bx, _by, bw, _bh = self.band_rect()
        sep = 12 * fs
        fid = _ui_draw.font("hero")[0]
        cap = self._cap
        rec = self.played(it)
        if (cap is None or cap[0] is not it or cap[1] is not self._g
                or cap[7] != rec):
            # laid out once a card: a moving frame draws it again as it is
            name = it.get("title") or ""
            if it.get("type") == NEW_TILE_TYPE:
                name = "New"
            was = _ui_draw.print_font(fid)
            name = _ui_draw.print_fit(name, bw - 16 * fs, tsc)
            tw = _ui_draw.print_width(name, tsc)
            if was is not None:
                _ui_draw.print_font(was)
            parts = self._meta(it)
            ws_ = [_ui_draw.print_width(t, fs) for t in parts]
            cap = self._cap = (it, self._g, name, tw, parts, ws_,
                               sum(ws_) + sep * (len(parts) - 1), rec)
        name, tw, parts, ws_, mw = cap[2], cap[3], cap[4], cap[5], cap[6]
        was = _ui_draw.print_font()
        ink, dim = th.get("ink", 7), th.get("ink_dim", 6)
        wall = self.wall_ink
        if wall is not None:
            ink = dim = wall[0]
        tx = bx + (bw - tw) // 2
        swap = _ui_draw.print_font(fid)
        if wall is not None:
            cv.print_scaled(name, tx + 1, ty + 1, wall[1], tsc)
        cv.print_scaled(name, tx, ty, ink, tsc)
        if swap is not None:
            _ui_draw.print_font(was)
        x0 = x = bx + (bw - mw) // 2
        d = 2 * fs if _ui_draw.font_v2(_ui_draw.font()[0]) else fs
        dy = _dot_top(my, fs, d)
        for pas in ((1, wall[1]), (0, dim)) if wall is not None else ((0, dim),):
            o, c = pas
            x = x0
            for k, t in enumerate(parts):
                if k:
                    cv.rect(x - (sep + d) // 2 + o, dy + o, d, d, c)
                cv.print(t, x + o, my + o, c, 1)
                x += ws_[k] + sep

    def _dims(self, centre):
        """A handheld neighbour's dimming (step 15), off the centre."""
        if centre or self.dim is None or not self.handheld():
            return None
        return self.dim

    def _mark_rect(self, cv, at, selected=False):
        """The rect a card at `at` hands its focus mark: `at` shrunk by as
        much as the theme's mark grows a card (a lift's `grow`, read off the
        canvas's edge, where it draws nothing), so the mark's box is the
        card's own side, raised where it lifts. The cover keeps its clean
        scale (owner call 2026-10-17) and the lift's rim hugs it, with no
        plate between (design review R3-05): a picture card lifts by its
        rise and its shadow alone. `selected`: the mark is the selected
        card's current lift (`ui_draw.focus`)."""
        w, h = at[2], at[3]
        _ink, box = _ui_draw.focus(cv, self.theme, (-4 * w - 64, -4 * h - 64, w, h),
                                   item="card", after=True, selected=selected)
        g = (box[2] - w) // 2
        if g <= 0:
            return at
        return (at[0] + g, at[1] + g, w - 2 * g, h - 2 * g)

    def _draw_home_card(self, cv, it, rect, centre, sheet_for):
        NAMES = self._NAMES
        # The centre card at rest takes the theme's focus mark (ui.focus; a
        # lifted card draws raised, `_mark_rect`).
        rest = centre and not self.moving
        mark = rest and not self.tab_focus
        # On the desk the selected card is the one the details pane
        # describes: under touch, and while the tabs or the pane's buttons
        # hold the focus, it keeps its lift as a current mark (design review
        # R4-09); the theme's focus mark is the shelf's own focus.
        sel = rest and self.desk() and (self.tab_focus or _ui_draw.input_kind() == "touch"
                                        or not _ui_draw.marks()[0])
        at = rect
        if mark or sel:
            at = self._mark_rect(cv, rect, sel)
            _ink, rect = _ui_draw.focus(cv, self.theme, at, item="card",
                                        r=self._corner_r(rect[2]), selected=sel)
        cr = self._corner_r(rect[2])
        self._draw_picture(cv, it, rect, centre, sheet_for, cr)
        if centre and self.favorite_for is not None and not self.moving \
                and not self.desk() \
                and it.get("path") and it.get("type") not in PSEUDO_TILE_TYPES:
            _draw_star_badge(cv, NAMES, self.theme or {},
                             self._favorite_badge_geom(rect),
                             bool(self.favorite_for(it)))
        if not cr:
            self._card_frame(cv, rect)
        if mark or sel:
            _ui_draw.focus(cv, self.theme, at, item="card", r=cr,
                           after=True, selected=sel)
