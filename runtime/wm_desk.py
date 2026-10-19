"""The windowed desk's ROOT layer: the wallpaper + the one OS bar + the desk
icon column, with the retained-backdrop cache a gesture blits instead of
re-rendering the desk (#58/#155), and under the theme's depth the windows'
shadows and outline fringes over whatever of it was just painted (the cache
holds the desk without them). `WindowedWM` holds one as `_backdrop_layer`
and hands it the cache state; the layer decides per frame whether to render
live, blit the cache, or skip the restore outright.
"""

try:
    from layers import Layer
    from widgets import _in, _ticks_ms
    import ui_draw as _ui_draw        # the desk's tiles and labels
    import layout_base as _layout_base
    import iconsets as _iconsets
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import Layer
    from runtime.widgets import _in, _ticks_ms
    from runtime import ui_draw as _ui_draw
    from runtime import layout_base as _layout_base
    from runtime import iconsets as _iconsets

# A desk label over a wallpaper (a picture or a cart, pixels the theme does
# not know): white over a black shadow (chrome.FIXED's), 80% and one pixel
# down and right.
_WALL_SHADOW_A = 204
_NONE = -2147483648


def _set(v):
    """A look leaf that is set: a value, not silent (None) or MOY_THEME_NONE."""
    return type(v) is int and v != _NONE


def _tile_fill(tiles, key):
    """Desk key `key`'s colour of a look's `tiles` (docs/look_2026-10.md
    step 26b), which names an app by its icon slot (Paint's `artwork` is the
    `paint` tile), or None."""
    slot = _iconsets.slot_of(key)
    v = tiles.get(_iconsets.SLOTS[slot] if slot is not None else key)
    return v if _set(v) else None


def _icon_box(box):
    """The square a desk tile's icon is drawn in: 24 of the tile's 44, centred,
    the set's 24-pixel masks, whose line art spans about 20 (owner,
    2026-10-19: the dock's glyphs at 20, their 20-pixel masks' 14 read thin)."""
    s = box[2] * 24 // 44
    return (box[0] + (box[2] - s) // 2, box[1] + (box[3] - s) // 2, s, s)


class _BackdropLayer(Layer):
    """The real desktop root (wallpaper + ONE OS bar) with a DRAG cache (#58).
    The home is a launch surface only while the process stack has no windows;
    as soon as PLAY/CHANGE pushes one, this layer replaces it so the home
    never reads as wallpaper behind Studio. During a drag/resize only the window
    position changes, so the first frame captures the desktop and later frames
    blit the retained backdrop.

    Correctness: this layer precedes _win_layer in the z-order, so the capture
    snapshots the desktop backdrop with NO windows on it; each drag frame blits
    that clean backdrop (erasing the dragged window's old position for free) and
    _win_layer then stamps the windows at their current spots. Double-buffer-safe
    -- the cache is its own off-screen buffer, re-blitted into whichever ping-pong
    buffer the frame targets. The desktop background routes only its OS bar;
    hidden Library cards cannot be activated through it."""

    id = "launcher"
    domain = "system"

    def __init__(self, wm):
        self.wm = wm
        self.ws = wm.ws

    def draw(self, dt):
        wm = self.wm
        # A CONTENT gesture (a finger scrolling inside a window) counts as a
        # gesture for cache purposes, exactly like a window drag/resize (#155,
        # owner "the project picker is choppy, the play launcher is smooth").
        # The desk cannot change while a window's content is being dragged, but
        # this layer used to re-render the whole desktop -- wallpaper cover-crop
        # + icon column + bar -- on EVERY such frame. Measured on glass during a
        # picker scroll: 107ms of a 181ms frame (~5fps) against the fullscreen
        # Library's 36ms, which pays no desk at all. The cached blit is ~26ms.
        # Same staleness trade the window-drag path already accepts: a clock tick
        # mid-gesture waits for the release, which re-renders live.
        content_anim = wm._content_gesture or wm._content_flinging()
        gesture = (wm._drag is not None or wm._resize is not None
                   or content_anim)
        # The cache is gated on the desk being UNCHANGED, not on being in a
        # gesture (#155). Gating it on the gesture meant every non-gesture
        # painted frame re-rendered the whole desk -- wallpaper cover-crop + icon
        # column + bar -- which on P4 glass is a 120ms frame, and one landed on
        # every gesture RELEASE. The desk's own content is a pure function of the
        # signature below; the clock is the one live part and it is repainted
        # over the cached blit instead of invalidating it.
        if wm._pop_live():
            wm._pop_restore(False)            # start as if no popover were drawn
        sig = self._desk_sig()
        stale = sig != wm._desk_sig
        wm._damage_rects = None               # opened below where describable
        # A change to the window SHAPE (open/close/minimize/move/resize) uncovers
        # desk the departed window was covering -- pixels the skip's own
        # justification ("fully covered by the window's stamp") no longer holds
        # for. The desk STATICS are unchanged then (the cache stays valid); only
        # the restore must actually run again, so reset the skip streak. Same
        # one-signature-beats-hunting-mutation-sites rule as the stamp voider.
        # (Owner report 2026-07-27: "close it and it remains as an artifact on
        # the desktop" -- the close frame skipped the restore outright.)
        # A DISCRETE shape change (open/close/minimize/maximize, or a gesture
        # settling on release) uncovers desk the departed footprint was
        # covering: invalidate the cache so that frame renders the desk LIVE
        # (erasing the ghost regardless of cache content -- on the P4 the boot
        # capture was found holding the SPLASH, #165) and re-captures fresh.
        # NEVER for gesture-driven changes: geometry is in the sig, so
        # invalidating during a drag/resize would re-render + re-capture the
        # whole desk EVERY frame (owner: "drags are slow, settings flickers" --
        # the first cut did exactly that), and doing it on RELEASE would
        # re-add the 120ms release frame #155 killed (the trail machinery
        # already restores a gesture's footprint). So the holder TRACKS the
        # sig silently while a gesture is live -- release finds it equal --
        # and only a discrete change (open/close/min/max) invalidates. Focus
        # is deliberately NOT in this sig: focus moves no desk pixels.
        s = wm._shape_sig()
        # order + geometry; focus too under the depth, whose shadows and
        # outline fringes (drawn here, _draw_unders) follow it
        wsig = (s[0], s[2], s[1]) if _ui_draw.depth()[13] else (s[0], s[2])
        if wsig != wm._desk_win_sig:
            wm._desk_win_sig = wsig
            if wm._drag is None and wm._resize is None:
                wm._desk_streak = 0
                wm._backdrop_valid = False
        if (stale or wm._backdrop_disabled or wm._backdrop_unsupported
                or self.ws._animating(dt)):   # a toast/confetti moves desk pixels
            wm._desk_sig = sig
            wm._backdrop_valid = False        # live: re-render, then re-snapshot
            wm._desk_streak = 0
            wm._desk_painted = True           # wiped the buffer -> windows repaint
            if wm._gesture_hist and not gesture:
                wm._gesture_hist = []         # gesture over: drop the damage trail
            self._draw_desktop(dt)
            wm._capture_backdrop()
            wm._draw_unders()
            return
        if wm._backdrop_valid:
            # CONTENT gesture: the window is STATIONARY, so the desk outside it
            # never changes and the desk under it is fully covered by the
            # window's own stamp. Once the cache has been laid into BOTH
            # ping-pong buffers (two consecutive gesture frames), every later
            # frame's target already holds the right pixels -- skip the restore
            # entirely. That is the last ~28ms between a windowed content scroll
            # and the fullscreen Library's (owner: "choppy vs smooth"). A window
            # DRAG still restores every frame: there the window moves, so the
            # backdrop it uncovers is genuinely damaged.
            if wm._drag is None and wm._resize is None:
                # A VISIBLE cursor whose drawn state changed forces the restore:
                # the cursor sprite from the last paint is baked into the
                # retained buffer, and skipping here left a trail of stale
                # cursors across the desk (measured on the host: from the third
                # consecutive moving frame on). Finger gestures keep the #155
                # skip -- the cursor is hidden there, nothing to erase.
                ptr = self.ws._ptr_state()
                last = self.ws._last_ptr
                cursor_live = ((ptr is not None and ptr[2])
                               or (last is not None and last[2]))
                if not (cursor_live and ptr != last) \
                        and wm._desk_streak >= wm._retained_n() and not wm._scrim_up():
                    wm._desk_painted = False   # untouched: windows may skip too
                    wm._damage_rects = []      # the desk changed nowhere
                    self.ws.bar_layer.redraw_clock("desk")
                    return
                wm._desk_streak += 1
            wm._desk_painted = True
            _perf = getattr(self.ws, "perf_capture", False)
            _t0 = _ticks_ms() if _perf else 0
            union = wm._blit_backdrop_cache()
            if wm._restored is not False:
                wm._draw_unders(wm._restored)
            if _perf:
                self.ws._pf_wm_restore = _ticks_ms() - _t0
            if union is not None:
                wm._damage_rects = [union]     # the desk changed inside the gesture
            self.ws.bar_layer.redraw_clock("desk")
            return
        wm._desk_streak = 0
        wm._desk_painted = True
        self._draw_desktop(dt)                # cache lost: render + re-snapshot
        wm._capture_backdrop()
        wm._draw_unders()

    def _desk_sig(self):
        """Everything the desk's wallpaper + icon column depends on.

        Deliberately NOT the clock (repainted over the cache instead) and NOT
        ws.covers.gen: a cover landing in the picker would otherwise invalidate
        the desk on the very frames a scroll is trying to stay cheap. Cheap to
        compute -- no per-frame scan of the cart list."""
        ws = self.ws
        cv = ws.sys_canvas
        return (cv.w, cv.h, ws.look.look_gen, ws.look.effective_font_scale(),
                ws.look.wallpaper_id,
                len(getattr(ws, "_apps", ())), len(ws.carts.all))

    def _draw_desktop(self, dt):
        self.ws.wallpaper.draw(dt, quiet=self.quiet())
        self._draw_desk_icons()
        self.ws.bar_layer._draw_status_strip("desk")

    def quiet(self):
        """The desk's quiet rectangles (docs/theming_2026-09.md §4.9): the
        bars and the icon column, its tiles and labels as one rectangle per
        column. Built when the desk cache is lost, never per frame."""
        cols = {}
        for _key, box, band, _label, _cart in self._icon_rects():
            x0 = min(box[0], band[0])
            y0 = min(box[1], band[1])
            x1 = max(box[0] + box[2], band[0] + band[2])
            y1 = max(box[1] + box[3], band[1] + band[3])
            c = cols.get(box[0])
            if c is not None:
                x0, y0 = min(x0, c[0]), min(y0, c[1])
                x1, y1 = max(x1, c[2]), max(y1, c[3])
            cols[box[0]] = (x0, y0, x1, y1)
        return self.ws.wallpaper.bars() + tuple(
            (x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in
            (cols[k] for k in sorted(cols)))

    # -- desk icons (#105: desktop mode's launch surface) ---------------------
    #
    # A static v1 column: PLAY (console mode, over the home), PROJECTS (the
    # home in a window, on Make), then every desktop-only system app. Geometry is deterministic
    # (the _chip_rects pattern -- computed per call, no stored state), so draw
    # and hit-test can never disagree, and the icons render before the drag
    # backdrop capture, so the drag cache carries them for free. Under a
    # theme's look the column is the look's (docs/look_2026-10.md step 25:
    # `_look_rects`, `_draw_look_icons`); without one, tiles and label pills
    # in the theme's shape.

    ICON_GLYPHS = {"play": "run", "projects": "edit"}

    @property
    def HIDDEN_APPS(self):
        """The WM's: reached through Settings, not a desk tool."""
        return self.wm.HIDDEN_APPS

    def _icon_catalog(self):
        """[(key, label, cart)]: under a look the labels are the titles as
        their apps name them, else in capitals."""
        ws = self.ws
        looked = bool(_ui_draw.depth()[0])
        out = [("play", "Play" if looked else "PLAY", None),
               ("projects", "Projects" if looked else "PROJECTS", None)]
        for app, _text in getattr(ws, "_apps", ()):
            if app.id in self.HIDDEN_APPS:
                continue
            cart = None
            for c in ws.carts.all:
                if app.is_app(c):
                    cart = c
                    break
            title = str(ws.app_title(app.id) or app.id.upper())
            out.append((app.id, title if looked else title.upper(), cart))
        return out

    def _icon_rects(self):
        """[(key, box_rect, label_rect, label, cart), ...] -- a left-edge column
        wrapping into further columns; recomputed per call from live geometry."""
        ws = self.ws
        fs = ws.look.effective_font_scale()
        dx, dy, dw, dh = self.wm._desk_rect()
        box = _layout_base.metric("tile", fs)
        catalog = self._icon_catalog()
        if _ui_draw.depth()[0]:
            return self._look_rects(catalog, box, fs, dx, dy, dw, dh)
        gut = 10 * fs                   # the cell's trailing gutter: pill = cell_w - gut
        cell_h = _layout_base.metric("tile_cell", fs)
        x0 = dx + 14 * fs
        y0 = dy + _layout_base.metric("tile_top", fs)
        bottom = dy + dh - 6 * fs
        # The pill must hold the longest catalog label or the chip clips it
        # (#174: a fixed 66*fs cell cut PROJECTS/STORYBOOK) -- but only as wide
        # as the columns it takes still fit the canvas, or a long app title
        # marches the last column off the right edge and out of reach. The last
        # column draws no gutter, so its width is not charged against the fit.
        maxw = max((_ui_draw.text_width(label, fs, "ui") for _k, label, _c in catalog),
                   default=0)
        rows = max(1, (bottom - y0) // cell_h)
        cols = max(1, (len(catalog) + rows - 1) // rows)
        fit_w = (dx + dw - x0 + gut) // cols
        cell_w = max(66 * fs, maxw + gut + 4)
        cell_w = max(box + gut, min(cell_w, fit_w))
        x = x0
        y = y0
        out = []
        for key, label, cart in catalog:
            if y + cell_h > bottom:
                y = y0
                x += cell_w
            bx = x + (cell_w - gut - box) // 2
            out.append((key,
                        (bx, y, box, box),
                        (x, y + box + 3 * fs, cell_w - gut, 13 * fs),
                        label, cart))
            y += cell_h
        return out

    def _look_rects(self, catalog, box, fs, dx, dy, dw, dh):
        """The column under a look (docs/look_2026-10.md step 25): `box`
        tiles on the class's pitch, their centres 54 of the tile's 44 in from
        the desk's edge or further where a label needs it, each label under
        its tile, 2 of the 44 below it, as wide as the column: nearer its own
        tile than the next one down."""
        k = box / 44
        cell_h = _layout_base.metric("tile_cell", fs)
        y0 = dy + _layout_base.metric("tile_top", fs)
        bottom = dy + dh - int(6 * k)
        line = _ui_draw.font("ui")[2] * fs
        maxw = max((_ui_draw.text_width(label, fs, "ui") for _k, label, _c in catalog),
                   default=0)
        pitch = max(box + int(20 * k), maxw + int(20 * k))
        cx = dx + max(int(54 * k), (maxw + 1) // 2 + int(10 * k))
        gap = int(2 * k)
        y = y0
        out = []
        for key, label, cart in catalog:
            if y + box + gap + line > bottom and y > y0:
                y = y0
                cx += pitch
            out.append((key,
                        (cx - box // 2, y, box, box),
                        (cx - pitch // 2, y + box + gap, pitch, line),
                        label, cart))
            y += cell_h
        return out

    def _draw_look_icons(self, ws, cv, th):
        """Each tile as the look's part (`ui.bar_part`'s BAR_TILE) in its
        app's colour of the look's `tiles` where it names one, its app's
        icon from the theme's set at the 24-pixel size in `tile_ink` (else
        the ink; the app's own art where the set has none), its label in
        `label.ink` (else the ink) over the theme's backdrop with no plate
        and `label.shadow` under it where the look sets one; over a
        wallpaper, white over a shadow."""
        look = ws.look.live.look
        leaves = look[0]
        ink = th.get("ink", 0)
        tiles = dict(look[2]) if len(look) > 2 else {}
        tink = leaves.get("tile_ink")
        tink = tink if _set(tink) else ink
        if ws.look.wallpaper_id is not None:
            black, white = ws.look.live.fixed[2:]
            lab, shadow = white, (black, _WALL_SHADOW_A, 1, 1)
        else:
            lab = leaves.get("label.ink")
            lab = lab if _set(lab) else ink
            shadow = None
            sc = leaves.get("label.shadow.c")
            if _set(sc):
                a, dx, dy = (leaves.get("label.shadow." + f) for f in ("alpha", "dx", "dy"))
                shadow = (sc, (a * 255 + 50) // 100 if _set(a) else 255,
                          dx if _set(dx) else 1, dy if _set(dy) else 1)
        for key, box, band, label, cart in self._icon_rects():
            _ui_draw.bar_part(cv, th, _ui_draw.BAR_TILE, box, 0, _tile_fill(tiles, key))
            if not ws.draw_icon(key, _icon_box(box), tink, cv):
                img = ws.covers.icon_sheet_for(cart) if cart is not None else None
                if img is not None:
                    sc = max(1, (box[2] * 24 // 44) // 16)
                    cv.spr(img, box[0] + (box[2] - 16 * sc) // 2,
                           box[1] + (box[3] - 16 * sc) // 2, sc)
                else:
                    ws._glyph(self.ICON_GLYPHS.get(key, "app"), _icon_box(box), tink, cv)
            _ui_draw.label(cv, band, label, lab, _ui_draw.ALIGN_CENTER, shadow=shadow)

    def _draw_desk_icons(self):
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        fs = ws.look.effective_font_scale()
        if _ui_draw.depth()[0]:
            self._draw_look_icons(ws, cv, th)
            return
        for key, box, pill, label, cart in self._icon_rects():
            _ui_draw.icon_tile(cv, th, box)
            img = ws.covers.icon_sheet_for(cart) if cart is not None else None
            if img is not None:
                sc = max(1, (box[2] - 8 * fs) // 16)
                cv.spr(img, box[0] + (box[2] - 16 * sc) // 2,
                       box[1] + (box[3] - 16 * sc) // 2, sc)
            else:
                glyph = self.ICON_GLYPHS.get(key, "app")
                ink = th.get("accent", 10) if key == "play" else th.get("title_ink", 0)
                ws._glyph(glyph, (box[0] + 6 * fs, box[1] + 6 * fs,
                                  box[2] - 12 * fs, box[3] - 12 * fs), ink, cv)
            _ui_draw.icon_label(cv, th, pill, label, key == "play", fs)

    @staticmethod
    def label_rect(pill, label, fs):
        """The pill a label is drawn in (`ui.icon_label_rect`; the hit-test
        keeps the whole band)."""
        return _ui_draw.icon_label_rect(pill, label, fs)

    def _open_icon(self, key):
        ws = self.ws
        if key == "play":
            ws.open_library()
        elif key == "projects":
            ws.open_projects()
        else:
            app = ws._apps_by_id.get(key)
            if app is not None:
                ws.open_app(app)

    def handle_input(self, i):
        return True

    def handle_pointer(self, px, py, click):
        if click:
            if self.ws.bar_layer.bar_at("desk", px, py):
                self.ws.bar_layer.handle_bar_tap("desk", px, py)
                return True
            for key, box, pill, _label, _cart in self._icon_rects():
                if _in(px, py, box) or _in(px, py, pill):
                    self._open_icon(key)
                    return True
        return True
