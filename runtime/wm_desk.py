"""The windowed desk's ROOT layer: the wallpaper + the one OS bar + the desk
icon column, with the retained-backdrop cache a gesture blits instead of
re-rendering the desk (#58/#155). `WindowedWM` holds one as `_backdrop_layer`
and hands it the cache state; the layer decides per frame whether to render
live, blit the cache, or skip the restore outright.
"""

try:
    from layers import Layer
    from widgets import _in, _ticks_ms
    import ui as _ui                  # desk icon label pills (ui.chip)
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.layers import Layer
    from runtime.widgets import _in, _ticks_ms
    from runtime import ui as _ui


class _BackdropLayer(Layer):
    """The real desktop root (wallpaper + ONE OS bar) with a DRAG cache (#58).
    The Library is a launch surface only while the process stack has no windows;
    as soon as PLAY/CHANGE pushes one, this layer replaces it so the Library
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
        wsig = (s[0], s[2])               # order + geometry, no focus
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
                        and wm._desk_streak >= wm._retained_n():
                    wm._desk_painted = False   # untouched: windows may skip too
                    wm._damage_rects = []      # the desk changed nowhere
                    self.ws.bar_layer.redraw_clock("desk")
                    return
                wm._desk_streak += 1
            wm._desk_painted = True
            _perf = getattr(self.ws, "perf_capture", False)
            _t0 = _ticks_ms() if _perf else 0
            union = wm._blit_backdrop_cache()
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

    def _desk_sig(self):
        """Everything the desk's wallpaper + icon column depends on.

        Deliberately NOT the clock (repainted over the cache instead) and NOT
        ws.covers.gen: a cover landing in the picker would otherwise invalidate
        the desk on the very frames a scroll is trying to stay cheap. Cheap to
        compute -- no per-frame scan of the cart list."""
        ws = self.ws
        cv = ws.sys_canvas
        return (cv.w, cv.h, ws.look.theme_name, ws.look.theme_variant,
                ws.look.effective_font_scale(), id(ws.look.icon_sheet),
                ws.look.wallpaper_id,
                len(getattr(ws, "_apps", ())), len(ws.carts.all))

    def _draw_desktop(self, dt):
        self.ws.wallpaper.draw(dt)
        self._draw_desk_icons()
        self.ws.bar_layer._draw_status_strip("desk")

    # -- desk icons (#105: the make world's launch surface) --------------------
    #
    # A static v1 column: PLAY (drop to the fullscreen Library), PROJECTS (the
    # picker), then every desktop-only system app. Geometry is deterministic
    # (the _chip_rects pattern -- computed per call, no stored state), so draw
    # and hit-test can never disagree, and the icons render before the drag
    # backdrop capture, so the drag cache carries them for free.

    ICON_GLYPHS = {"play": "run", "projects": "edit"}
    HIDDEN_APPS = ("appearance",)     # reachable via Settings, not a desk tool

    def _icon_catalog(self):
        ws = self.ws
        out = [("play", "PLAY", None), ("projects", "PROJECTS", None)]
        for app, _text in getattr(ws, "_apps", ()):
            if app.id in self.HIDDEN_APPS:
                continue
            cart = None
            for c in ws.carts.all:
                if app.is_app(c):
                    cart = c
                    break
            title = ws.app_title(app.id) or app.id.upper()
            out.append((app.id, str(title).upper(), cart))
        return out

    def _icon_rects(self):
        """[(key, box_rect, label_rect, label, cart), ...] -- a left-edge column
        wrapping into further columns; recomputed per call from live geometry."""
        ws = self.ws
        fs = ws.look.effective_font_scale()
        bar_h = self.wm._bar_h()
        box = 40 * fs
        gut = 10 * fs                   # the cell's trailing gutter: pill = cell_w - gut
        catalog = self._icon_catalog()
        cell_h = 62 * fs
        x0 = 14 * fs
        y0 = bar_h + 12 * fs
        bottom = ws.sys_canvas.h - 6 * fs
        # The pill must hold the longest catalog label or the chip clips it
        # (#174: a fixed 66*fs cell cut PROJECTS/STORYBOOK) -- but only as wide
        # as the columns it takes still fit the canvas, or a long app title
        # marches the last column off the right edge and out of reach. The last
        # column draws no gutter, so its width is not charged against the fit.
        maxc = max((len(label) for _k, label, _c in catalog), default=0)
        rows = max(1, (bottom - y0) // cell_h)
        cols = max(1, (len(catalog) + rows - 1) // rows)
        fit_w = (ws.sys_canvas.w - x0 + gut) // cols
        cell_w = max(66 * fs, maxc * 8 * fs + gut + 4)
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

    def _draw_desk_icons(self):
        ws = self.ws
        cv = ws.sys_canvas
        th = ws.theme_colors
        fs = ws.look.effective_font_scale()
        for key, box, pill, label, cart in self._icon_rects():
            cv.rect(box[0], box[1], box[2], box[3], th.get("panel", 60))
            cv.rectb(box[0], box[1], box[2], box[3], th.get("edge", 13))
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
            _ui.chip(cv, th, pill, label, on=key == "play", fs=fs)

    def _open_icon(self, key):
        ws = self.ws
        if key == "play":
            ws.open_library()
        elif key == "projects":
            ws.open_picker()
        else:
            app = ws._apps_by_id.get(key)
            if app is not None:
                ws.open_app(app)

    def handle_input(self, i):
        return True

    def handle_pointer(self, px, py, click):
        if click:
            if py < self.wm._bar_h():
                self.ws.bar_layer.handle_bar_tap("desk", px, py)
                return True
            for key, box, pill, _label, _cart in self._icon_rects():
                if _in(px, py, box) or _in(px, py, pill):
                    self._open_icon(key)
                    return True
        return True
