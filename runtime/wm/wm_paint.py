# Map (grep -n a name to jump there):
#   WindowPaint                        the mixin WindowedWM paints through (its first base)
#   WindowPaint._ensure_backdrop       the drag backdrop cache a gesture blits
#   WindowPaint.composite_game         the game's frame into the player window
#   WindowPaint.present_frame          the game's frame straight onto the glass
#   WindowPaint._frame_is_quiet        whether only the game window changes
#   WindowPaint._draw_windows          every open window, bottom -> top
#   WindowPaint._direct_render         a window's content drawn on the glass itself
#   WindowPaint._shown_ok              the shown ring: what each buffer holds
#   WindowPaint._debt_rows             the rows a debt frame copies
#   WindowPaint.refill_step            a frozen window's buffer brought current
#   WindowPaint._content_static        the focused window's content freeze (§14.1)
#   WindowPaint._draw_app_window       an app window: its stamp, or its content drawn
#   WindowPaint._draw_player_window    a game window: the cart's tick and its frame
#   WindowPaint._blit_game             the game canvas integer-scaled into its window
"""The windowed WM's PAINT: what a frame the draw stack chose puts on the
glass -- the window buffers' lifetime, the drag backdrop cache, the game's
viewport and composite, the windows drawn bottom -> top (retained stamps,
direct renders, the gesture's restore), an app's window and a game's, and
the shown ring that says what each of the root's buffers holds
(docs/surface_model_v1.md §14).

A mixin over `WindowedWM`, its FIRST base: on MicroPython a method the class
lacks is looked up through the bases in order, so the paths a frame runs
sit where that walk is shortest. The draw stack's choice itself
(`WindowedWM.draw_stack` and its predicates) stays on the class. It owns
no state of its own: the windows, stamps and rings it reads are the WM's,
made in `WindowedWM.__init__`.
"""

try:
    from wm import FullscreenStackWM, _VIEWPORT_BEZEL
    from wm_parts import _Win, _overlap, _grow, MODE_MARK, _SHOWN
    from wm_chrome import _SHADOW
    from widgets import _Blit, _ticks_ms
    import layout_base as _layout_base
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.wm import FullscreenStackWM, _VIEWPORT_BEZEL
    from runtime.wm_parts import _Win, _overlap, _grow, MODE_MARK, _SHOWN
    from runtime.wm_chrome import _SHADOW
    from runtime.widgets import _Blit, _ticks_ms
    from runtime import layout_base as _layout_base
    from runtime import ui_draw as _ui_draw


class WindowPaint:
    """The window buffers, the backdrop cache, the game's composite, the
    windows' draw and the shown ring of `WindowedWM` (its first base)."""


    # -- window buffer lifetime ------------------------------------------------
    #
    # A window's content buffer is a root-canvas layer, and on a board a layer
    # lives OUTSIDE the gc heap (heap_caps PSRAM, device_canvas._LayerComp),
    # where nothing collects it: the WM has to say when it is done. It did not,
    # and every window open leaked its buffer -- ~3.6MB per Library -> CHANGE
    # -> home round on the Guition P4 (window + re-minted backdrop + the
    # strip cache's canvases), measured 2026-09-09: 15.9MB of PSRAM free at
    # boot, 0.8MB after four rounds, after which every layer fell back onto
    # the gc heap and a collect cost 430ms. `release` is probed, so a
    # recording tier's layer (no release) and the host's are unaffected.

    def _release_layer(self, lay):
        rel = getattr(lay, "release", None)
        if rel is not None:
            rel()
            self._prealloc_refused = False    # room again, maybe (`prealloc`)

    def _drop_win(self, win):
        buf = win.buf
        if buf is None:
            return
        win.buf = None
        win.ctx = None
        self._release_layer(buf)

    def _drop_wins(self):
        for win in self._wins.values():
            self._drop_win(win)
        self._wins.clear()

    def _drop_backdrop(self):
        cache = self._backdrop
        self._backdrop = None
        if cache is not None:
            self._release_layer(cache)

    # -- the drag backdrop cache (#58 drag perf) -------------------------------

    def _ensure_backdrop(self):
        """The full-screen off-screen cache buffer (lazily allocated, re-made if
        the root canvas size changed under it)."""
        sc = self._root_canvas
        cache = self._backdrop
        if cache is None or cache.w != sc.w or cache.h != sc.h:
            self._drop_backdrop()
            cache = self._backdrop = sc.new_layer(sc.w, sc.h)
            self._backdrop_valid = False
        return cache

    def _capture_backdrop(self):
        """Snapshot the just-rendered desktop backdrop -- the root canvas with NO
        windows on it yet (_BackdropLayer precedes _win_layer) -- into the cache:
        one opaque full-frame copy via the cache canvas's own blit_strip reading
        the root canvas AS a source layer (native moy_gfx.blit565 on the device,
        host index slice otherwise -- the same kernel both directions)."""
        # UNSUPPORTED IS A HARD STOP, CHECKED HERE (2026-07-31). The callers gate
        # on _backdrop_unsupported only to choose the LIVE render path -- they
        # still called this afterwards, and _ensure_backdrop mints a fresh
        # full-screen layer BEFORE the blit_strip fails again. On a recording
        # root (web/wasm) that leaked one 1024x600 RecordingLayer per painted
        # desk frame: the recorder's _layers list is append-only (dense deflayer
        # ids, dropped only on a cart change), so the console's own dropped
        # reference frees nothing. Measured: ~614KB retained per leak, and since
        # every GC collect then scans a bigger heap, collect cost climbed
        # 13ms -> 380ms over one browsing session -- the owner's "the more the
        # kinetic scroll activates the slower it gets".
        if self._backdrop_unsupported or self._backdrop_disabled:
            return
        try:
            cache = self._ensure_backdrop()
            self.ws.note_cost("desk.backdrop")
            cache.blit_strip(self._root_canvas, 0, 0)
            self._backdrop_valid = True
            self._desk_tile = self.ws.bar_layer.tile_on()
        except Exception:  # noqa: BLE001 -- a failed capture (OOM, or a
            self._backdrop_valid = False   # recording root with no pixels to
            self._drop_backdrop()          # snapshot) forfeits the cache; the
            # drag re-renders live. Mark the mechanism unsupported so the next
            # frames don't retry (#113: each retry allocated a fresh layer --
            # on the web root that leaked one RecordingLayer per drag frame).
            self._backdrop_unsupported = True

    def _gesture_extent(self):
        """The moving window's CURRENT damage bbox (x, y, w, h) -- everything the
        gesture draws this frame: the window body (at the resize rubber size when
        larger OR smaller -- max covers the no-live-resize outline mode too), its
        border and the 3px drop shadow, padded a couple px for safety. None when
        no gesture is in flight."""
        g = self._gesture()
        if g is None:
            return None
        win = self._wins.get(g[0])
        if win is None:
            return None
        x, y, w, h = win.x, win.y, win.w, win.h
        # Gesture tuples carry the window REGISTRY key (g[0]); every
        # gesture-vs-window test below resolves through _wins identity.
        if g is self._resize:
            rx, ry, rw, rh = g[10], g[11], g[5], g[6]
            x1 = max(x + w, rx + rw)
            y1 = max(y + h, ry + rh)
            x, y = min(x, rx), min(y, ry)
            w, h = x1 - x, y1 - y
        return self._pad_extent(win, x, y, w, h)

    def _gesture(self):
        """The gesture the desk's restore follows: a drag or a resize in
        flight, else a released drag for the RETAINED_FRAMES painted frames
        after its release (`_rest`), which bring each buffer to the window
        where it came to rest as a drag frame that did not move would."""
        if self._drag is not None:
            return self._drag
        if self._resize is not None:
            return self._resize
        if self._rest is not None and self.ws._frames_drawn >= self._rest_end:
            self._rest = None
        return self._rest

    def _seed_gesture_hist(self):
        """Prime the damage history at gesture START: every physical buffer
        holds the window's pre-gesture stamp, so the first N restores must
        cover it. Where the desk's last change has not reached every buffer
        yet (its streak short of N: a window opened, closed, restored), the
        first N restore the whole glass instead."""
        ext = self._gesture_extent()
        n = self._retained_n()
        if ext is None:
            self._gesture_hist = []
        elif self._desk_streak < n:
            full = self._root_canvas
            self._gesture_hist = [(0, 0, full.w, full.h)] * n
        else:
            self._gesture_hist = [ext] * n

    def _blit_backdrop_cache(self):
        """Stamp the cached backdrop into the current (ping-pong) back buffer --
        restricted to the DIRTY UNION of the gesture's recent damage extents when
        the canvas supports a rect-clipped stamp (#58: a full-screen 1:1 copy is
        ~26ms PSRAM-bandwidth-bound on the P4 and no accelerator helps it; the
        union is window-sized, so the restore cost follows the WINDOW, not the
        screen). Everything outside the union is either untouched since that
        buffer's last frame or opaquely redrawn every frame (the other windows,
        the bar strip, the chips), so the partial restore is pixel-safe. Falls
        back to the full copy when the canvas lacks blit_strip_rect (the web
        RecordingLayer) or via the `union` A/B knob."""
        self._restored = False
        self._restored_f = self.ws._frames_drawn
        if self._backdrop is None:
            return
        rc = self._root_canvas
        ext = self._gesture_extent()
        n = self._retained_n()
        if (not hasattr(rc, "blit_strip_rect") or self._union_disabled or ext is None
                or not self._gesture_hist):
            rc.blit_strip(self._backdrop, 0, 0)
            self._restored = None
            if ext is not None:
                self._gesture_hist = (self._gesture_hist + [ext])[-n:] \
                    if self._gesture_hist else [ext] * n
            # A whole-cache copy still CHANGES only the gesture's footprint:
            # outside it the cache is the desk that was already there, and the
            # other windows re-stamp over it identically.
            return self._union_bbox(self._gesture_hist)
        rects = self._gesture_hist + [ext]
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        # Subtract the window's CURRENT opaque body from the union (measured on
        # glass 2026-07-10: the union alone bought ~nothing for a near-full-screen
        # window -- the default windows are ~60% of the desktop, so "window-sized"
        # ~= "screen-sized"). The body rect gets fully covered THIS frame anyway
        # (content stamp + chrome fills + shadow are all opaque), so only the
        # EXPOSED margin -- the thin trail strips the window just left -- needs
        # the backdrop back: for a real finger drag that's a few px of perimeter,
        # ~KBs instead of the window's ~700KB, making restore cost independent of
        # window size. Decomposed as up to 4 strips (top/bottom/left/right of the
        # union minus the body); the resize body is the rubber rect (live-body
        # covers it opaquely too).
        g = self._gesture()
        win = self._wins.get(g[0]) if g is not None else None
        bx0 = by0 = bx1 = by1 = 0
        if win is not None:
            wx, wy, bw, bh = win.x, win.y, win.w, win.h
            if g is self._resize \
                    and self._live_resize_ok() and win.kind != "desktop":
                wx, wy, bw, bh = g[10], g[11], g[5], g[6]
            # The rows the depth's rounded corners leave partly uncovered are
            # restored with the margin: only the square middle is opaque.
            r = self._win_radius(win)
            bx0 = max(x0, wx)
            by0 = max(y0, wy + r)
            bx1 = min(x1, wx + bw)
            by1 = min(y1, wy + bh - r)
        laid = self._laid
        del laid[:]
        if win is None or bx0 >= bx1 or by0 >= by1:
            laid.append((x0, y0, x1 - x0, y1 - y0))
        else:
            if by0 > y0:                                       # top strip
                laid.append((x0, y0, x1 - x0, by0 - y0))
            if by1 < y1:                                       # bottom strip
                laid.append((x0, by1, x1 - x0, y1 - by1))
            if bx0 > x0:                                       # left strip
                laid.append((x0, by0, bx0 - x0, by1 - by0))
            if bx1 < x1:                                       # right strip
                laid.append((bx1, by0, x1 - bx1, by1 - by0))
        for r in laid:
            rc.blit_strip_rect(self._backdrop, 0, 0, r[0], r[1], r[2], r[3])
        self._gesture_hist = (self._gesture_hist + [ext])[-n:]
        self._restored = (x0, y0, x1 - x0, y1 - y0)
        return self._restored

    @staticmethod
    def _union_bbox(rects):
        if not rects:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)

    def _win_extent(self, win):
        """Everything a window's draw touches: body, border and shadow, padded
        like _gesture_extent."""
        return self._pad_extent(win, win.x, win.y, win.w, win.h)

    def _hand_damage(self, touched):
        """Close this frame's damage description: hand the backdrop's rects and
        the touched windows' extents to a root canvas that takes them. Nothing
        is handed when the frame cannot be described -- the backdrop painted
        live, an overlay other than the popover (toast/notice/About/splash) is
        in the stack, or the cursor sprite is visible (it moves every frame and
        is drawn by a layer above this one) -- and the compositor then presents
        the whole frame. The popover's rects, open or closing, ride along."""
        rects = self._damage_rects
        self._damage_rects = None
        if rects is None:
            return
        nd = getattr(self._root_canvas, "note_damage", None)
        if nd is None:
            return
        if (self._cache_sig & ~66) != 0:
            return                        # the popover (64) is described below
        if getattr(self.ws.pointer, "visible", False):
            return
        pops = False
        for s in self._pop_slots:
            if s[1] is not None:
                pops = True
        if not rects and not touched and not pops:
            return                        # nothing to say: the safe full frame
        for r in rects:
            nd(*r)
        for r in touched:
            nd(*r)
        for s in self._pop_slots:
            if s[1] is not None:
                nd(*s[1])

    # -- game viewport == the player window (#39 mapping) ----------------------

    def app_surface(self):
        """(w, h) of the canvas a window-bound app cart draws on
        (`Workstation._bind_window_canvas`): the desk's work area, so its
        window shows any size of it with no new pixels."""
        _dx, _dy, dw, dh = self._desk_rect()
        return (dw, dh)

    def app_window_content(self):
        """(w, h) of the content a window-bound app cart's window shows from
        its canvas's top left: the player window's content rect, or the one
        its window opens at."""
        win = self._wins.get("desktop")
        if win is None:
            fs = self._fs()
            w, h = self._open_size("desktop", fs)
            win = _Win("desktop", 0, 0, w, h, _layout_base.metric("title", fs))
            win.foot = self._win_foot(win)
            win.edge = self._win_edge(win)
        cr = win.content_rect()
        lay = self._root_ctx.layout     # _desk_rect's source: app_surface's size
        c = self._app_wc
        if c is not None and c[0] is cr and c[1] == lay.cw and c[2] == lay.ch:
            return c[3]
        sw, sh = self.app_surface()
        r = (max(1, min(cr[2], sw)), max(1, min(cr[3], sh)))
        self._app_wc = (cr, lay.cw, lay.ch, r)
        return r

    def _view_src(self):
        """A window-bound app cart's window shows its canvas's top left, its
        content rect's size; else the cart view rule (wm.py)."""
        if self.ws.app_window_canvas and self.mode == "desktop":
            w, h = self.app_window_content()
            v = self._app_src
            if v is None or v[2] != w or v[3] != h:
                v = self._app_src = (0, 0, w, h)
            return v
        return FullscreenStackWM._view_src(self)

    def _player_view(self, win):
        """(ox, oy, scale) of the game canvas centered in the player window's
        content rect -- the windowed viewport. A cart-declared view (`view(w,
        h)`) scales ITS region here exactly like the fullscreen composite, so
        celeste's 128x128 fills the window at 4x instead of the container's 2x.
        A window-bound app cart's canvas lies at the content's top left, 1x."""
        if self.ws.app_window_canvas:
            cx, cy, _cw, _ch = win.content_rect()
            return (cx, cy, 1)
        gc = self.ws.canvas
        view = self._view_src()
        gw, gh = (view[2], view[3]) if view else (gc.w, gc.h)
        cx, cy, cw, ch = win.content_rect()
        scale = max(1, min(cw // gw, ch // gh))
        ox = cx + (cw - gw * scale) // 2
        oy = cy + (ch - gh * scale) // 2
        return (ox, oy, scale)

    def game_is_fullscreen(self):
        """Only in console mode. In desktop mode the game lives in a
        WINDOW -- _draw_player_window emits its own placement bracket around the
        cart's draw -- so the router must NOT also letterbox the whole desktop.
        It briefly did, and because the FPS overlay ("perf") is a GAME-domain
        layer, a windowed playtest blacked out the desk and re-placed the game:
        the owner saw "the project launcher games also go fullscreen"."""
        return self.mode == "console"

    def viewport(self):
        win = self._wins.get("desktop")
        if win is None:
            return FullscreenStackWM.viewport(self)
        return self._player_view(win)


    def present_frame(self, cf, view):
        # Desktop mode: the player WINDOW shows the frame itself
        # (_draw_player_window), so a frame shown full-viewport here would
        # land on the desktop. Console mode shows it where composite_game
        # would have composited the canvas, over the same bezel.
        if self.mode == "desktop":
            return False
        ws = self.ws
        sc = ws.sys_canvas
        if sc is not self._pf_for:
            self._pf_for = sc
            self._pf_fn = getattr(sc, "present_frame", None)
        pf = self._pf_fn
        if pf is None:
            return False
        ox, oy, scale = FullscreenStackWM.viewport(self)
        src = self._view_src()
        self._play_bezel(sc, ox, oy, scale, src)
        return pf(cf, view, ws.canvas, ox, oy, scale, src)

    def _window_frame(self, gc, ox, oy, scale, defer, src):
        """The player window's composite of a compiled cart's frame: shown
        from the cart's memory, or -- on a composite the cart drew no new
        frame for -- from the copy of the last one shown. True when the root
        canvas took it; otherwise the frame is settled into the game canvas,
        which the window composites as ever (Workstation._composite_game's
        rule)."""
        cf = self.ws.cart_frame
        if cf is None:
            return False
        view = cf.take()
        if view is not None:
            sc = self._root_canvas
            if sc is not self._pf_for:
                self._pf_for = sc
                self._pf_fn = getattr(sc, "present_frame", None)
            pf = self._pf_fn
            if pf is not None and pf(cf, view, gc, ox, oy, scale, src, defer):
                return True
            cf.settle(gc)
        cf.nrects = 0
        return False

    def composite_game(self):
        # Desktop mode: a no-op -- the window layer blits the game canvas into
        # the player WINDOW itself, and stamping it full-viewport would paint
        # over the desktop. Console mode: the fullscreen composite, routed
        # through the polymorphic _blit_game so the P4's native RGB565 path and
        # the web's b64-spr path both work (the parent's index loops need an
        # indexed system buffer this tier's canvases may not have).
        if self.mode == "desktop":
            return
        ws = self.ws
        gc = ws.canvas
        sc = ws.sys_canvas
        if gc is not self._fb_for:     # the parent's cached flush_batch probe
            self._fb_for = gc
            self._fb_fn = getattr(gc, "flush_batch", None)
        if self._fb_fn is not None:
            self._fb_fn()
        if sc is gc:
            return
        if getattr(gc, "buf", None) is None \
                and not hasattr(sc, "blit_game"):
            # Command-only game canvas (#175): the cart's frame is ALREADY in
            # the stream; the letterbox cls() below would wipe it, so bail.
            # Ask the question _blit_game asks -- a system canvas with a native
            # blit_game can composite, buffer or no buffer. A `.buf`-only probe
            # silently never composited the fullscreen game on the device
            # raster canvas, whose framebuffer is `_buf` (P4, 2026-08-01).
            return
        ox, oy, scale = FullscreenStackWM.viewport(self)
        src = self._view_src()
        self._play_bezel(sc, ox, oy, scale, src)
        self._blit_game(sc, gc, ox, oy, scale, src=src)

    def play_viewport(self):
        """Console mode's fullscreen game rect, (ox, oy, scale): what a run
        the kernel drives in front composes (native/moy_play's front). None in
        the desk world and for a cart-declared view, which the console
        composes."""
        if self._order or self._view_src() is not None:
            return None
        return FullscreenStackWM.viewport(self)

    def _play_bezel(self, sc, ox, oy, scale, src):
        """Console mode's letterbox bezel, at most once a frame.

        It is STATIC: fill it only until every framebuffer holds it (the
        stale-by-N rule) -- it was a full-screen fill EVERY play frame
        (chrome=8ms of a 15ms celeste frame). Retention must end BOTH on a
        geometry change AND on a gap in the drawn-frame counter: anything
        larger than one frame means another stack painted the screen and the
        bezel pixels are no longer ours -- without the gap rule, N stale
        Library frames rotate/flicker behind the game (P4, 2026-08-01,
        measured as 0/32/24 bytes differing between the three
        framebuffers)."""
        drawn = self.ws._frames_drawn
        if drawn == getattr(self, "_bezel_frame", -2):
            return              # a declined frame's composite, same frame
        bkey = (ox, oy, scale, sc.w, sc.h, src)
        if bkey != getattr(self, "_bezel_key", None) \
                or drawn != getattr(self, "_bezel_frame", -2) + 1:
            self._bezel_key = bkey
            self._bezel_paints = 0
        self._bezel_frame = drawn
        if self._bezel_paints < self._retained_n():
            self._bezel_paints += 1
            sc.cls(_VIEWPORT_BEZEL)    # letterbox fill

    # -- what a frame keys its retained pixels on ------------------------------

    def _shape_sig(self):
        """The frame's window SHAPE: which windows exist, z-order, geometry,
        minimised state, focus. The ONE signature both retained-pixel voiders
        key on (window stamps AND the desk-restore streak) -- a change means
        pixels somewhere stopped being covered by what covered them last frame."""
        return (tuple(self._order), self._focus,
                tuple((w.x, w.y, w.w, w.h, w.minimized, w.kind)
                      for w in (self._wins[k] for k in self._order)))

    def _content_flinging(self):
        """True while a window's CONTENT is coasting on a kinetic fling (#113) --
        the finger is up but the view is still moving, so the desk is as static
        as it is mid-drag. Without this the release dropped straight back onto
        the live desk render and the fling frames measured ~180ms against the
        drag's ~100ms (on glass, 2026-07-26)."""
        ws = self.ws
        for grid in (ws.projects_grid, ws.launcher):
            if grid is not None and getattr(grid, "flinging", False):
                return True
        return False

    # -- drawing ---------------------------------------------------------------

    def _lowest_dirty_window(self, dt):
        """Index of the LOWEST window that must repaint this frame; everything
        below it is already correct in the target framebuffer and is skipped.

        Why (measured on glass 2026-07-26): every open window was re-stamped and
        re-chromed EVERY frame even when nothing about it changed -- an unfocused
        window isn't even re-rendered, just copied. Each extra open window cost
        ~44ms a frame (1 window 56ms, 2 windows 99ms, 3 windows 144ms), which is
        what made a desktop with a few things open feel far worse than the
        fullscreen home.

        The z-order rule that makes skipping SOUND: windows are painted bottom to
        top, so a repainting window overwrites the overlap of everything below
        it. Skipping is therefore only safe for a CONTIGUOUS run at the BOTTOM --
        find the lowest window that must paint and draw from there up.

        A window may be skipped only once its pixels sit in BOTH ping-pong
        buffers (`_stamp_streak >= _retained_n()`, the same rule _BackdropLayer
        uses), it is
        not the focused window (whose content re-renders live), and nothing
        global invalidated the frame -- the desk backdrop repainting under it, a
        live animation, or a visible cursor whose old stamp would ghost."""
        n = len(self._order)
        # Any change to the window SHAPE of the frame (which windows, their
        # z-order, geometry, minimised state, or which one has focus) voids every
        # retained stamp. One signature comparison beats hunting each mutation
        # site (open/close/move/resize/maximise/focus) and can't miss one.
        sig = self._shape_sig()
        if sig != self._win_sig:
            self._win_sig = sig
            self._player_under = self._covers_player()
            for k in self._order:
                self._wins[k]._stamp_streak = 0
            self._sig_stable = False
            return 0
        self._sig_stable = True
        if not self._frame_is_quiet(dt):
            return 0                      # something under/over everything moved
        horizon = self._retained_n()
        for i in range(n):
            win = self._wins[self._order[i]]
            if win.minimized:
                continue                  # nothing painted -> nothing to skip past
            if (self._order[i] == self._focus or win.kind == "desktop"
                    or getattr(win, "_stamp_streak", 0) < horizon):
                return i
        return n                          # every window is settled: draw none

    def _covers_player(self):
        """Whether a shown window above the game window reaches it, its
        shadow included (read once a shape change, by draw_stack's quiet
        path)."""
        order = self._order
        if "desktop" not in order:
            return False
        win = self._wins["desktop"]
        if win.minimized:
            return False
        for k in order[order.index("desktop") + 1:]:
            up = self._wins[k]
            if not up.minimized and _overlap(self._pad_extent(up, up.x, up.y, up.w, up.h),
                                             (win.x, win.y, win.w, win.h)):
                return True
        return False

    def _frame_is_quiet(self, dt):
        """True when nothing GLOBAL invalidated the retained ping-pong pixels
        this frame: the desk backdrop did not repaint under the windows, no
        window is being dragged or resized, nothing is animating but a verbs
        page moving on its own (`ws.ui_moving`, which changes its window's
        content alone), and no cursor stamp needs erasing. Shared by the
        window-skip and the chrome freeze."""
        if (self._desk_painted or self._drag is not None
                or self._resize is not None or self.ws._animating(dt, False)
                or self._scrim_up()):
            return False
        p = self.ws.pointer
        if p is not None and getattr(p, "visible", False):
            return False
        return True

    def _draw_windows(self, dt):
        self._sync_windows()
        self._undock_orphans()
        self._resize_feet()
        n = len(self._order)
        # A full-screen window covers the glass: nothing under it is drawn.
        cover = self.full_cover()
        first = max(self._lowest_dirty_window(dt), cover)
        # Chrome freeze (#155): a window's strip/border/shadow is DISJOINT from
        # its content stamp, so on a quiet frame -- a content scroll, a fling,
        # typing -- those pixels are already correct in this ping-pong buffer and
        # redrawing them is pure waste. Measured on P4 glass during a picker
        # scroll: 8.2ms of a 70ms frame, every frame, for an unchanged title bar.
        # _lowest_dirty_window sets _sig_stable (window shape/order/focus).
        self._chrome_quiet = self._sig_stable and self._frame_is_quiet(dt)
        touched = []
        self._under_hit = False
        top = self._wins[self._order[-1]] if n else None
        for i in range(n):
            key = self._order[i]
            win = self._wins[key]
            focused = (key == self._focus)
            if win.minimized:
                continue                  # hidden: no z-slot, not replayed
            if i < first:
                continue                  # the raster partial path settled this window
            win._stamp_streak = getattr(win, "_stamp_streak", 0) + 1
            self._win_touched = False
            self._chrome_drew = False
            self._part[4] = 0
            if win is not top and not self._under_hit \
                    and _overlap(self._win_extent(win), top.content_rect()):
                self._under_hit = True    # the top window's retained pixels are hit
            u = self._under_gesture(win, focused, top)
            if u is not None:
                if u:
                    touched.append(self._draw_under_gesture(win, i, u))
                continue
            if win.kind == "desktop":
                # The Player TICKS whenever its window is open, independent of
                # input focus AND of what sits above it on the back-stack -- a
                # game keeps running while the editor beside it is typed in or
                # while Settings floats over it (wifi setup mid-game, #38). The
                # one exception: the crash-editor flow keeps the frozen frame
                # (cart_error -> tick just repaints the error panel, harmless).
                # Its composite is the canvas's word (mark_game), not this
                # loop's: only a moved/re-chromed player window is touched.
                self._draw_player_window(win, True, focused, dt)
            else:
                self._draw_app_window(win, focused, dt)
            self._cast_onto(i)
            if self._win_touched and not focused:
                self._outside_f = self.ws._frames_drawn
            if self._win_touched:
                pt = self._part
                if pt[4] and not self._chrome_drew and pt[2] > pt[0] and pt[3] > pt[1]:
                    touched.append((pt[0], pt[1], pt[2] - pt[0], pt[3] - pt[1]))
                else:
                    touched.append(self._win_extent(win))
        # The chips live on the OS bar, whose strip the compositor carries
        # when the chip draw says it moved; the resize outline lies inside the
        # gesture union. Neither is a rect of its own.
        self._hand_damage(touched)
        if cover < 0:                              # the bar is covered with the desk
            self._draw_taskbar_chips(quiet=self._chrome_quiet or self._chips_untouched(dt))
        self._alone_note()
        if self._resize is not None:               # rubber-band resize preview
            g = self._resize
            kind, cw, chh, rx, ry = g[0], g[5], g[6], g[10], g[11]
            win = self._wins.get(kind)
            # App windows resize LIVE-BODY (drawn in _draw_app_window, #58 "real
            # OS" feel); the outline preview remains for the game window (its
            # content is the scaled composite -- cropping it mid-gesture reads as
            # glitch, and the scale recomputes on release anyway) and for
            # canvases without the rect stamp (the web RecordingLayer).
            if win is not None and (win.kind == "desktop"
                                    or not self._live_resize_ok()):
                self._root_canvas.rectb(rx, ry, cw, chh,
                                        self.ws.theme_colors["accent"])

    def _under_gesture(self, win, focused, top):
        """While the top window is dragged or resized over the desk's cache,
        the rects of window `win` under it that this frame's restore laid the
        desk over (`_blit_backdrop_cache`): none where it laid none, so `win`'s
        pixels in this buffer are as the gesture found them. None where `win`
        draws whole -- the game's window, a window that renders again (a look
        changed, its focus mark), a full-screen one, a restore of the whole
        glass."""
        r = self._restored
        if not r or self._restored_f != self.ws._frames_drawn or win is top:
            return None
        g = self._gesture()
        if g is None or self._wins.get(g[0]) is not top:
            return None
        if (focused or win.kind == "desktop" or win.saved is not None or win.buf is None
                or win._look_gen != self.ws.look.look_gen
                or win._marked):
            return None
        ex = self._win_extent(win)
        out = []
        for r in self._laid:
            x0, y0 = max(ex[0], r[0]), max(ex[1], r[1])
            x1 = min(ex[0] + ex[2], r[0] + r[2])
            y1 = min(ex[1] + ex[3], r[1] + r[3])
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1 - x0, y1 - y0))
        return out

    def _desk_clock(self):
        """The desk bar's clock over the cached desk: repainted unless the
        RETAINED_FRAMES painted frames before this one, one after another,
        each left this text in its buffer and this frame's restore laid the
        cache over no bar."""
        ws = self.ws
        bar = ws.bar_layer
        t = bar._clock_text()
        f = ws._frames_drawn
        h = self._clock_held
        r = self._restored if self._restored_f == f else False
        over = r is None
        if r:
            for b in self._root_ctx.layout.bars:
                if _overlap(r, b[1]):
                    over = True
        if h[0] == t and h[1] == f - 1 and not over:
            h[2] += 1
        else:
            h[0] = t
            h[2] = 1
        h[1] = f
        if h[2] <= self._retained_n():
            self._outside_f = f
            bar.redraw_clock("desk")

    def _clock_drawn(self):
        """The desk drawn whole this frame, its bar's clock with it: the
        first of the RETAINED_FRAMES frames that hold its text."""
        h = self._clock_held
        h[0] = self.ws.bar_layer._clock_text()
        h[1] = self.ws._frames_drawn
        h[2] = 1

    def _chips_untouched(self, dt):
        """Whether this frame left the chips' bar as the last one did: a
        gesture's restore laid the desk away from it, and no cursor or
        animation draws over it."""
        r = self._restored
        if not r or self._restored_f != self.ws._frames_drawn:
            return False
        if getattr(self.ws.pointer, "visible", False) or self.ws._animating(dt, False):
            return False
        got = self._chips_cell()
        if got is None:
            return True
        b = got[0][1]
        return not _overlap(r, b)

    def _draw_under_gesture(self, win, i, rects):
        """Window `win` (z-slot `i`) under a gesture, in `rects` alone: its
        stamp and what the windows above it cast on it in each, its chrome in
        their bounding box, which the gesture's window covers between them.
        Answers that box. The casts blend, so they land in the rects alone."""
        rc = self._root_canvas
        cx, cy, _cw, _ch = win.content_rect()
        for r in rects:
            rc.blit_strip_rect(win.buf, cx, cy, r[0], r[1], r[2], r[3])
        u = self._union_bbox(rects)
        rc.clip(u[0], u[1], u[2], u[3])
        try:
            self._win_chrome(win, False)
        finally:
            rc.clip()
        for r in rects:
            self._cast_onto(i, r)
        return u

    def _direct_render(self, win, dt):
        """Draw the window's content into the framebuffer IN PLACE, through a
        viewport at its content rect (#155). Returns False if this backend or
        this window can't take the path, and the caller falls back to the
        render-into-buffer-then-stamp route.

        The layout context still comes from the window (every responsive surface
        must keep deriving its geometry from the WINDOW size); only the canvas is
        swapped for the root, masked to the window's rect. That is why the
        viewport has to match win.buf exactly -- the layouts were built for that
        size, and a mismatch would lay the content out for the wrong surface."""
        root = self._root_canvas
        if not hasattr(root, "set_viewport"):
            return False                      # web RecordingLayer: keep the stamp
        cx, cy, cw, ch = win.content_rect()
        if cw <= 0 or ch <= 0 or cw != win.buf.w or ch != win.buf.h:
            return False
        # A window may hang off the screen edge; a clamped viewport would then be
        # a different surface from the one its layouts were built for. Stamp
        # instead (the buffer path crops for free).
        if cx < 0 or cy < 0 or cx + cw > root.w or cy + ch > root.h:
            return False
        ws = self.ws
        content = self._content_for(win.kind)
        held = getattr(content, "HELD", False)
        # An app on the verbs: whether the buffer this frame paints holds
        # its page as the last frame left it, so its pages may move that
        # frame's pixels (the shift frame's voucher, `_vouches`).
        pages = 0
        if not held and self._speaks(win):
            pages = 2 if self._vouches(win) else 1
        known = 0
        chain = False
        u = self._u
        if held:
            # What the buffer this frame paints holds of the page: the shown
            # ring's word for it, else the window's buffer stamped into it
            # (the page, but for the buffer's own lag).
            u[0] = u[1] = u[2] = u[3] = 0
            f = ws._frames_drawn
            if self._shown_ok(win) and self._shown_chain(win, f, u):
                known = 1
                chain = True
            elif not win._buf_stale or win._lag[4]:
                root.blit_strip(win.buf, cx, cy)
                u[0] = u[1] = u[2] = u[3] = 0
                if win._buf_stale:
                    lag = win._lag
                    u[0] = lag[0]
                    u[1] = lag[1]
                    u[2] = lag[2]
                    u[3] = lag[3]
                known = 1
        vouched = getattr(content, "vouched", None) if held else None
        if vouched is not None:
            # 3: the ring's word too, that this buffer holds the page as the
            # frames RETAINED_FRAMES back left it, but for their changes
            vouched((3 if chain else 2) if self._vouches(win) else 1)
        self._install(win.ctx, win)
        ws._sys_canvas = root                 # ...but paint on the framebuffer
        ws._surface_write(cx, cy)
        root.set_viewport(cx, cy, cw, ch)
        _ui_draw.window_frame(pages)
        self._direct_root = True
        try:
            if held:
                content.retained(known, u[0], u[1], u[2], u[3])
            content.draw(dt)
        finally:
            self._direct_root = False
            if self._scale_owed:
                # The content changed the font scale: the root takes it now
                # its page is drawn (`Appearance._font_scale_canvas`).
                self._scale_owed = False
                root.set_font_scale(ws.look.effective_font_scale())
            _ui_draw.window_frame(0)
            root.clear_viewport()
            self._install(self._root_ctx)
        # the row is the root's already where the root held the page as its
        # last draw left it, but for a lag clear of the row
        self._row_over(win, root, held, bool(known) and self._row_clear(win, u))
        stale = win._buf_stale
        win._buf_stale = True                 # buf skipped: it now lags the truth
        if not held:
            if pages:
                self._own_page(win)
            return True
        self._own_of(win, content, held)
        own = self._own
        lag = win._lag
        if not own[4]:
            lag[4] = 0
        elif not stale:
            lag[0] = own[0]                   # buf held the page until now
            lag[1] = own[1]
            lag[2] = own[2]
            lag[3] = own[3]
            lag[4] = 1
        elif lag[4]:
            _grow(lag, own[0], own[1], own[2], own[3])
        return True

    # -- the shown ring (docs/surface_model_v1.md §14.3) ------------------------
    #
    # A window's content may speak a small protocol with the WM, three names
    # on its Layer: `HELD = True` says it does; `retained(known, x0, y0, x1,
    # y1)`, called before each draw the WM makes of it in its window, says
    # whether (`known`) the target it is about to draw into holds its page as
    # of its last draw, and the part of the target that lags it, [x0, y0, x1,
    # y1) in the content's coordinates; `changed()`, read after that draw,
    # answers the part of the page the draw changed, a list [x0, y0, x1, y1)
    # or None for unknown. The WM keeps what it learns per physical buffer of
    # the root (`_shown`) and per window buffer (`_lag`), so a content that
    # speaks it repaints only what moved and the window's stamp copies only
    # what its buffer gained. A content that does not draws whole, as every
    # content did before.

    def _shown_ok(self, win):
        """Whether, this frame, `win`'s content rect on the root is written by
        that window alone: it is on top and focused, nothing under it drew
        into it, and nothing above it draws -- no overlay, popover, scrim or
        cursor, the chrome quiet (no drag, resize or desk repaint)."""
        if not self._chrome_quiet or self._under_hit:
            return False
        if (self._cache_sig & ~2) != 0 or self._pop_live() or self.popover_owed():
            return False
        order = self._order
        return bool(order) and order[-1] == self._focus and self._wins.get(order[-1]) is win

    def _shown_chain(self, win, f, c):
        """Whether the root buffer frame `f` paints holds `win`'s page but
        for what the frames since that buffer was painted changed, which
        grows `c`: the RETAINED_FRAMES painted frames before `f` all left
        their buffer holding the page, and the changes of all but the
        oldest are known."""
        n = getattr(self._root_canvas, "RETAINED_FRAMES", 0)
        if n < 1 or n > _SHOWN:
            return False
        ring = self._shown
        for i in range(1, n + 1):
            e = ring[(f - i) % (_SHOWN + 1)]
            if e[0] != f - i or e[1] is not win:
                return False
            if i < n:
                if not e[2]:
                    return False
                _grow(c, e[3], e[4], e[5], e[6])
        return True

    def _vouches(self, win):
        """Whether the buffer the last frame drew into holds `win`'s page,
        where it lies now, with nothing over it, as this frame will hold it
        (the shown ring's word for frame n-1, and this frame's): a page of
        its content may then move that frame's pixels (`shift_last`) rather
        than draw them (docs/history/scroll_and_text_2026-10.md §3.1, one voucher
        per region)."""
        if not self._shown_ok(win):
            return False
        f = self.ws._frames_drawn
        e = self._shown[(f - 1) % (_SHOWN + 1)]
        if e[0] != f - 1 or e[1] is not win or e[11] != self.ws.look.look_gen:
            return False
        cx, cy, cw, ch = win.content_rect()
        return e[7] == cx and e[8] == cy and e[9] == cw and e[10] == ch

    def _own_page(self, win):
        """This frame's change of `win`'s page into `_own`: the page's rect,
        for a content that says no narrower (an app on the verbs)."""
        own = self._own
        cx, cy, cw, ch = win.content_rect()
        own[0] = own[1] = 0
        own[2] = cw
        own[3] = ch
        own[4] = 1

    def _own_of(self, win, content, held):
        """This frame's change of `win`'s page into `_own`: what the content
        says its draw changed, unknown where it says nothing, or where the
        full-screen row `_row_over` painted over it changed."""
        own = self._own
        r = content.changed() if held else None
        if r is None:
            own[4] = 0
        else:
            own[0] = r[0]
            own[1] = r[1]
            own[2] = r[2]
            own[3] = r[3]
            own[4] = 1
        if win._row_moved:
            win._row_moved = False
            own[4] = 0

    def _shown_note(self, win, f, ok, held):
        """Record frame `f` in the shown ring: `win` (when `ok`) and this
        frame's change. A content that speaks the protocol and whose next
        frame's buffer the ring cannot yet vouch for asks for that frame
        now, while nothing waits on it, until the ring can; a frame asked
        for that changed nothing known stops asking."""
        e = self._shown[f % (_SHOWN + 1)]
        own = self._own
        e[0] = f
        e[1] = win if ok else None
        e[2] = own[4]
        e[3] = own[0]
        e[4] = own[1]
        e[5] = own[2]
        e[6] = own[3]
        cx, cy, cw, ch = win.content_rect()
        e[7] = cx
        e[8] = cy
        e[9] = cw
        e[10] = ch
        e[11] = self.ws.look.look_gen
        e[12] = 0                     # the windows' pass closes it (`_alone_note`)
        n = getattr(self._root_canvas, "RETAINED_FRAMES", 0)
        if ok and held and 1 <= n <= _SHOWN:
            sc = self._sc
            sc[0] = sc[1] = sc[2] = sc[3] = 0
            if not self._shown_chain(win, f + 1, sc) \
                    and (own[4] or self._settle_f != f):
                self._settle_f = f + 1
                self.ws.request_frame()

    def _repeat_note(self):
        """A repeated frame's buffer holds what the last frame's held, so it
        counts as that frame painted once more: the streaks of buffers that
        hold the desk's cache, each window's stamp and the chips advance as
        an unchanged whole frame advances them, and its shown ring entry is
        the last frame's, the page unchanged."""
        n = self._retained_n()
        if self._backdrop_valid and self._desk_streak < n:
            self._desk_streak += 1
        for win in self._wins.values():
            if not win.minimized:
                win._stamp_streak = getattr(win, "_stamp_streak", 0) + 1
        if self._chip_streak < n:
            self._chip_streak += 1
        f = self.ws._frames_drawn
        h = self._clock_held              # ...and the desk clock's text
        if h[1] == f - 1:
            h[1] = f
            h[2] += 1
        ring = self._shown
        p = ring[(f - 1) % (_SHOWN + 1)]
        e = ring[f % (_SHOWN + 1)]
        e[0] = f
        same = p[0] == f - 1
        e[1] = p[1] if same else None
        e[2] = 1
        e[3] = e[4] = e[5] = e[6] = 0
        for i in range(7, 12):
            e[i] = p[i]
        e[12] = 1                     # the glass is the last frame's: nothing changed

    def _shown_part(self, win, x0, y0, x1, y1):
        """Name [x0, y0, x1, y1) of `win`'s content as the root rect it
        changed this frame (the damage a describing compositor takes)."""
        cx, cy, cw, ch = win.content_rect()
        pt = self._part
        pt[0] = cx + max(0, x0)
        pt[1] = cy + max(0, y0)
        pt[2] = cx + min(cw, x1)
        pt[3] = cy + min(ch, y1)
        pt[4] = 1

    def _alone_note(self):
        """Close this frame's shown ring entry: whether nothing outside the
        focused window's content rect changed on the glass (no other window,
        clock, tile or chip drew), so what the frame changed is the page's
        part alone (`_debt_rows`)."""
        f = self.ws._frames_drawn
        e = self._shown[f % (_SHOWN + 1)]
        if e[0] == f:
            e[12] = 1 if e[1] is not None and self._outside_f != f else 0

    def _debt_rows(self):
        """The rows the buffer this frame paints lacks of the last frame's
        picture, into `_rows` [y0, y1, whole]: the union of what the frames
        since that buffer was painted changed, where each changed nothing
        outside the focused window's page (its ring entry says what, else
        the page's rect); the whole glass where one cannot say."""
        r = self._rows
        r[0] = r[1] = 0
        r[2] = 1
        n = self._pop_n()
        f = self.ws._frames_drawn
        if n > _SHOWN:
            return r
        ring = self._shown
        y0 = y1 = 0
        for k in range(f - n + 1, f):
            e = ring[k % (_SHOWN + 1)]
            if e[0] != k or not e[12]:
                return r
            if e[2]:
                if e[5] <= e[3] or e[6] <= e[4]:
                    continue                  # it changed nothing
                a = e[8] + max(0, e[4])
                b = e[8] + min(e[10], e[6])
            else:
                a = e[8]
                b = e[8] + e[10]
            if b <= a:
                continue
            if y1 <= y0:
                y0, y1 = a, b
            else:
                y0 = a if a < y0 else y0
                y1 = b if b > y1 else y1
        r[0] = y0
        r[1] = y1
        r[2] = 0
        return r

    def _speaks(self, win):
        """Whether `win`'s content is an app on the public UI API's verbs,
        whose pages say what each frame changes (`UI`, `SPEAKS`), and not
        one that speaks the shown protocol itself (`HELD`, which keeps its
        own frames): it renders straight into the glass on every frame it
        draws (`_direct_render`), the shown ring vouching where its pages
        may move the last frame's pixels, and its window buffer is refilled
        from the glass where a reader needs it (`_fresh`)."""
        c = self._content_for(win.kind)
        return (c is not None and not getattr(c, "HELD", False)
                and (getattr(c, "UI", False) or getattr(c, "SPEAKS", False)))

    def _refill(self, win):
        """Bring `win.buf` up to the page the last painted frame showed, out
        of that frame's buffer (the canvas's `copy_last_into`): only where
        the shown ring says that frame left the window's content rect, where
        it lies now and under the look in force, holding its page with
        nothing over it. Whether it did."""
        root = self._root_canvas
        cli = getattr(root, "copy_last_into", None)
        buf = win.buf
        if cli is None or buf is None:
            return False
        last = self.ws._frames_drawn - 1
        e = self._shown[last % (_SHOWN + 1)]
        gen = self.ws.look.look_gen
        if e[0] != last or e[1] is not win or e[11] != gen:
            return False
        cx, cy, cw, ch = win.content_rect()
        if (e[7] != cx or e[8] != cy or e[9] != cw or e[10] != ch
                or cw != buf.w or ch != buf.h):
            return False
        if not cli(buf, cx, cy, cw, ch):
            return False
        win._buf_stale = False
        win._lag[4] = 0
        win._look_gen = gen
        self._refills += 1
        return True

    def _fresh(self, win):
        """`win.buf` holds its page before a reader stamps it: refilled from
        the last frame's buffer where the root keeps one apart from the one
        this frame draws (a single buffer is being drawn over), else drawn
        (`_prewarm`)."""
        if win.buf is None or not win._buf_stale:
            return
        if getattr(self._root_canvas, "RETAINED_FRAMES", 1) > 1 and self._refill(win):
            return
        self._prewarm(win)

    def refill_step(self):
        """A frame with nothing to paint: the focused window's buffer,
        which direct render left behind the glass, refilled from the last
        frame's buffer (the stamp run backwards, off the paint path). Whether
        it ran."""
        win = self._wins.get(self._focus) if self._focus is not None else None
        if (win is None or win.minimized or win.kind == "desktop" or win.buf is None
                or not win._buf_stale or self.mode != "desktop"):
            return False
        return self._refill(win)

    def _held_direct(self, win, content):
        """Whether `win`'s content, one that speaks the shown protocol
        (`HELD`), renders straight into the root this frame: its page
        changed, the shown ring vouches for the buffer this frame paints,
        and the content repaints what that buffer lags (its `takes_lag`,
        where it has one, says whether it will this frame)."""
        if self._content_static(win, False) or not self._shown_ok(win):
            return False
        c = self._sc
        c[0] = c[1] = c[2] = c[3] = 0
        if not self._shown_chain(win, self.ws._frames_drawn, c):
            return False
        t = getattr(content, "takes_lag", None)
        return t is None or bool(t(c))

    def _still_shown(self, win):
        """Whether this frame need not draw `win`'s page at all: nothing
        changed it (§14.1's legs, but for its buffer's: the glass is the
        target) and the buffer this frame paints holds it, with nothing
        over it and nothing lagging (the shown ring)."""
        if not self._content_static(win, False) or not self._shown_ok(win):
            return False
        f = self.ws._frames_drawn
        e = self._shown[(f - 1) % (_SHOWN + 1)]
        if e[0] != f - 1 or e[11] != self.ws.look.look_gen:
            return False
        c = self._sc
        c[0] = c[1] = c[2] = c[3] = 0
        return self._shown_chain(win, f, c) and (c[2] <= c[0] or c[3] <= c[1])

    def _content_static(self, win, buf=True):
        """True when this PAINTED frame provably did not change WIN's content,
        so the focused window's draw may be skipped and the stamp reuse the
        retained win.buf (the surface-granularity damage model,
        docs/surface_model_v1.md §14.1 -- this is its first slice).

        "Provably" is three facts together:

        - Nothing marked the UI dirty. ws._dirty is still readable during the
          draw -- frame() clears it AFTER -- so a False here means no input or
          state handler reported a visible change this frame. MODE_MARK, the
          home's X bringing the desk back, is the WM's own and no change.
        - No pointer DOWN or CLICK was live this frame or last, unless the
          press is the WM's (`_grab`: a strip, a resize zone, a full-screen row's
          head), which no content is handed while it is held. The drag-driven
          handlers (paint strokes, map pans, block drags, kinetic scrolls)
          mutate content WITHOUT marking dirty -- they predate the #44 gate and
          rely on the pointer-state change arming the repaint -- so any button
          activity forces a live render. The click check reads BOTH frames'
          states because handlers consume p.click in place (a tap that opened a
          prompt zeroes it before the draw sees it); the previous frame's edge
          is still visible in ws._last_ptr. A position/visibility-only change
          -- the moving cursor, its auto-hide -- is inert: no content surface
          draws hover feedback (audited 2026-07-27; the cursor is an overlay,
          and anything that appears without input must already be an
          _animating source or it could never paint under the redraw gate).
        - The content is not one of the self-animating window surfaces: the
          _animating sources that draw INSIDE an app window (the music
          preview's playhead + PLAY/STOP, the bluetooth panel's async
          scan/pair states, the update screen's progress) repaint on
          animation-armed frames precisely because nothing marks dirty, so
          they are excluded by name. Sources OUTSIDE app windows (running
          cart, live wallpaper, splash, confetti/toast/egg overlays) draw on
          other layers and need no exclusion. A verbs page coasting or gliding
          (`ws.ui_moving`, the engine's `api_moving`) is one of them, read
          as the declaration it is.

        win._buf_stale guards the buffer itself: a fresh/rebuilt buffer or
        _direct_render (which paints past the buffer straight into the
        framebuffer) leaves buf behind the truth, so it is refilled from the
        glass first where the shown ring allows (`_refill`), else the next
        paint renders live to refill it before the freeze may resume, and
        win._look_gen against `look_gen` re-renders a buffer drawn under an
        older look. `buf` False asks about the page alone: whether nothing
        changed it, whatever its buffer holds."""
        ws = self.ws
        if buf:
            gen = ws.look.look_gen
            if win._look_gen != gen:
                win._look_gen = gen
                return False
            if win.buf is None or win._buf_stale:
                return False
        d = ws._dirty
        if d and d is not MODE_MARK:
            return False
        if win._marked != self._marks_for(win):
            return False                  # it holds a focus mark it no longer shows, or lacks one
        cur = ws._ptr_state()
        last = ws._last_ptr
        if not self._grab:
            if cur is not None and (cur[3] or cur[4]):
                return False
            if last is not None and (last[3] or last[4]):
                return False
        if (cur is None) != (last is None):
            return False                  # pointer appeared/vanished: be safe
        if ws.ui_moving:
            return False                  # a verbs page coasts or glides on its own
        k = win.kind
        if k == "update":
            return False
        if k == "settings" and ws.settings_layer.bluetooth_animating():
            return False
        if k == "menu" and ws.menu_view == "music" \
                and ws.music_ui.music_preview is not None:
            return False
        return True

    # -- a window's content: an app's buffer, a game's frame -------------------

    def _draw_app_window(self, win, focused, dt):
        # Freeze the content render while THIS window is being dragged/resized
        # (#58 drag perf): its buffer can't change under a drag (no input reaches
        # the content, and a resize only rubber-bands until release), so re-running
        # the editor-tab layout every drag frame is pure waste -- blit the retained
        # buffer at the new position instead. A drag of ANOTHER window still lets
        # this one render live (it's not the one moving).
        # Gesture keys are the window REGISTRY key -- resolve through _wins
        # identity (see _gesture_extent's note).
        if self._resize is not None \
                and self._wins.get(self._resize[0]) is win \
                and self._live_resize_ok():
            # Live-body resize: the frame follows the edge, content crops.
            self._win_touched = True
            self._fresh(win)
            g = self._resize
            self._draw_resizing_window(win, focused, g[10], g[11], g[5], g[6])
            return
        moving = ((self._drag is not None
                   and self._wins.get(self._drag[0]) is win)
                  or (self._resize is not None
                      and self._wins.get(self._resize[0]) is win))
        if moving:
            self._win_touched = True
            self._fresh(win)              # the move stamps its page
        elif not focused and (win._look_gen != self.ws.look.look_gen or win._marked):
            # A background window's retained buffer under an older look, or
            # drawn while it had the focus mark.
            win._look_gen = self.ws.look.look_gen
            self._win_touched = True
            self._prewarm(win)
        elif not focused:
            self._fresh(win)
        if focused and not moving:
            # DIRECT RENDER (#155): draw the content STRAIGHT into the
            # framebuffer through a viewport and skip the 1:1 stamp -- that
            # copy is ~900KB of bus traffic per frame on the P4, against a
            # measured 91MB/s ceiling -- while it is scrolled or flung, and on
            # every frame of a content that says what its frames change
            # (`_speaks`: a key's, a glide's, a coast's, a press's) or that
            # keeps its own frames (`HELD`) and changed its page. Its window
            # buffer is left behind the glass, and every reader of it brings it
            # up first (`_fresh`; the idle frames' `refill_step`).
            speaks = self._speaks(win)
            content = self._content_for(win.kind)
            held = getattr(content, "HELD", False)
            if (self._chrome_quiet
                    and (speaks or self._content_gesture or self._content_flinging()
                         or held and self._held_direct(win, content))
                    and self._order and self._wins.get(self._order[-1]) is win):
                if speaks and self._still_shown(win):
                    # Nothing changed the page and this buffer holds it.
                    own = self._own
                    own[0] = own[1] = own[2] = own[3] = 0
                    own[4] = 1
                    self._shown_note(win, self.ws._frames_drawn, True, False)
                    self._win_chrome(win, focused, quiet=self._chrome_quiet)
                    return
                if self._direct_render(win, dt):
                    self._win_touched = True
                    if speaks or held:
                        ok = self._shown_ok(win)
                        own = self._own
                        if ok and own[4]:
                            self._shown_part(win, own[0], own[1], own[2], own[3])
                        self._shown_note(win, self.ws._frames_drawn, ok, held)
                    self._win_chrome(win, focused, quiet=self._chrome_quiet)
                    return
            # CONTENT FREEZE (docs/surface_model_v1.md §14.1, first slice): on a
            # painted frame that provably did not change this window's content
            # (see _content_static), skip the re-render and let the retained-
            # buffer stamp below present it -- the map tab's draw is ~70ms on P4
            # glass, the stamp ~14ms, and the stamp runs either way.
            own = self._own
            if (win._buf_stale and getattr(self._root_canvas, "RETAINED_FRAMES", 1) > 1
                    and self._content_static(win, False)):
                self._refill(win)         # the freeze's buffer, from the glass
            if not self._content_static(win):
                # Live: render the focused app into its buffer at the window's
                # layout, telling a content that speaks the shown protocol
                # (§14.3) what of its page the buffer holds.
                self._win_touched = True
                self._install(win.ctx, win)
                if speaks and not held:
                    _ui_draw.window_frame(1)
                kept = held and not win._buf_stale
                vouched = getattr(content, "vouched", None) if held else None
                if vouched is not None:
                    vouched(2 if kept else 1)
                try:
                    if held:
                        lag = win._lag
                        if kept:
                            content.retained(1, 0, 0, 0, 0)
                        else:
                            content.retained(lag[4], lag[0], lag[1], lag[2], lag[3])
                    content.draw(dt)
                finally:
                    _ui_draw.window_frame(0)
                    self._install(self._root_ctx)
                self._row_over(win, win.buf, held, kept)
                win._buf_stale = False
                if held:
                    self._own_of(win, content, held)
                elif speaks:
                    self._own_page(win)
            else:
                own[0] = own[1] = own[2] = own[3] = 0
                own[4] = 1                    # frozen: the page did not change
            # THE PARTIAL STAMP (§14.3): where the shown ring vouches for the
            # buffer this frame paints, the stamp copies only what the page
            # changed since that buffer last held it -- nothing at all on a
            # frame that changed nothing.
            f = self.ws._frames_drawn
            ok = (held or speaks) and self._shown_ok(win)
            if ok and own[4]:
                sc = self._sc
                sc[0] = own[0]
                sc[1] = own[1]
                sc[2] = own[2]
                sc[3] = own[3]
                if self._shown_chain(win, f, sc):
                    self._shown_part(win, own[0], own[1], own[2], own[3])
                    cx, cy, cw, ch = win.content_rect()
                    x0 = sc[0] if sc[0] > 0 else 0
                    y0 = sc[1] if sc[1] > 0 else 0
                    x1 = sc[2] if sc[2] < cw else cw
                    y1 = sc[3] if sc[3] < ch else ch
                    if x1 > x0 and y1 > y0:
                        _perf = getattr(self.ws, "perf_capture", False)
                        _t0 = _ticks_ms() if _perf else 0
                        self._root_canvas.blit_strip_rect(win.buf, cx, cy, cx + x0,
                                                          cy + y0, x1 - x0, y1 - y0)
                        if _perf:
                            self.ws._pf_wm_stamp = _ticks_ms() - _t0
                    self._shown_note(win, f, ok, held)
                    self._win_chrome(win, focused, quiet=self._chrome_quiet)
                    return
            if held or speaks:
                self._shown_note(win, f, ok, held)
        # Stamp-defer (#58 drag; extended to CONTENT gestures 2026-07-26): on a
        # device with an async 1:1 blitter (P4SystemCanvas.blit_strip_async ->
        # PPA DMA) draw the chrome FIRST -- strip/borders/shadow/title are all
        # DISJOINT from the content rect -- and register the content stamp so the
        # compositor kicks it as the frame's LAST framebuffer write. The deferred
        # present then overlaps the ~25ms DMA with the next loop's input poll,
        # the same machinery as the quiet-game-frame composite.
        #
        # Why content scrolls qualify now: the stamp is a 1:1 copy, so it is a
        # wall-time WASH against the CPU (PSRAM-bound both ways, measured 91MB/s
        # either engine) -- ALL of its value is the overlap, and the overlap is
        # only sound when nothing draws over the region afterwards. The #155
        # chrome freeze made that true on a quiet gesture frame: the strip,
        # border and taskbar chips no longer repaint at all, so the deferred DMA
        # really is the last write. Same accepted trade as the drag path -- the
        # resize grip sits inside the content rect and hides for the duration.
        #
        # Host/web canvases have no hook, so their path is byte-identical.
        moving_this = (self._drag is not None
                       and self._wins.get(self._drag[0]) is win)
        scrolling_this = (focused and self._chrome_quiet
                          and (self._content_gesture
                               or self._content_flinging()))
        if ((moving_this or scrolling_this)
                and self._order and self._wins.get(self._order[-1]) is win):
            rc = self._root_canvas
            if hasattr(rc, "blit_strip_async"):
                self._win_chrome(win, focused, quiet=self._chrome_quiet)
                cx, cy, _cw, _ch = win.content_rect()
                _perf = getattr(self.ws, "perf_capture", False)
                _t0 = _ticks_ms() if _perf else 0
                if rc.blit_strip_async(win.buf, cx, cy):
                    if _perf:                 # the stamp's CPU share: its submit
                        self.ws._pf_wm_stamp = _ticks_ms() - _t0
                    return
                # non-fit / refusal: fall through to the sync stamp (the chrome
                # is already down; the second draw below is harmless overdraw).
        # Retained (or just-rendered) buffer -> desktop, then chrome.
        _perf = getattr(self.ws, "perf_capture", False)
        _t0 = _ticks_ms() if _perf else 0
        cx, cy, _cw, _ch = win.content_rect()
        self._root_canvas.blit_strip(win.buf, cx, cy)
        if _perf:
            self.ws._pf_wm_stamp = _ticks_ms() - _t0
        self._win_chrome(win, focused, quiet=self._chrome_quiet)

    def _fps_chip_on(self):
        """The parent's overlay-sig bit 2, re-read where the chip is now drawn:
        show_fps AND the player is the process on top (a game under Settings
        keeps its chip hidden, as before)."""
        return (self.ws.show_fps and self.stack.top() == "desktop"
                and not self.ws.app_window_canvas)

    def _draw_player_window(self, win, running, focused, dt, full=True,
                            defer=None):
        ws = self.ws
        gc = ws.canvas
        # Geometry FIRST (it does not depend on the cart's draw): on a recording
        # target with a command-only game canvas we place the cart's draw span with
        # a WM view bracket (#175) instead of compositing its pixels afterwards --
        # so the bezel and the bracket must both precede the draw, or the bezel
        # would wipe the frame the cart just recorded.
        cx, cy, cw, ch = win.content_rect()
        ox, oy, scale = self._player_view(win)
        _view = getattr(self._root_canvas, "view", None)
        # A command-only game canvas (no `buf`) has NO pixels to composite, so the
        # bracket is not optional -- without it the cart's commands would land
        # unplaced at the desktop origin, unclipped. (That is exactly the bug the
        # first cut of this shipped.) `scale` is the same integer magnification the
        # pixel path would have used, so the game FILLS its window.
        use_view = _view is not None and getattr(gc, "buf", None) is None
        if full and not ws.app_window_canvas:
            self._root_canvas.rect(cx, cy, cw, ch, _SHADOW)   # letterbox bezel
        if running:
            # The Player ticks while it's the STACK top (the running process),
            # focused or not -- editing beside a live playtest keeps it alive.
            if use_view:
                # (ox, oy, scale) is the centered game rect the pixel composite
                # used; vCW/vCH bound cls() and every draw to the cart surface, so
                # a cart cannot paint over the desk around it.
                _view(ox, oy, scale, gc.w, gc.h)
            self._content_for("desktop").draw(dt)  # Player.tick -> the game canvas
            if self._fps_chip_on():
                ws._perf_layer.draw(dt)   # game domain: inside the bracket (above)
            if use_view:
                _view()
        if gc is not self._fb_for:     # the parent's cached flush_batch probe
            self._fb_for = gc
            self._fb_fn = getattr(gc, "flush_batch", None)
        if self._fb_fn is not None:
            self._fb_fn()
        if use_view:
            if full and win.docked is None:
                self._win_chrome(win, focused)
            return
        # On a quiet game frame (full=False) the composite is this frame's LAST
        # framebuffer write, so a device backend may run it async and defer the
        # present (the #58 composite-overlap budget lever). A full paint draws
        # chrome AFTER it, so it must stay synchronous -- defer=not full, unless
        # the caller says otherwise. A compiled cart's frame is shown from
        # where it is when the root canvas can (_window_frame), the FPS chip
        # over it a rect its painter declared.
        src = self._view_src()
        if defer is None:
            defer = not full
        if (ws.app_window_canvas and running and ws.cart_frame is None
                and self._player_rows(win, gc, ox, oy, scale)):
            if full and win.docked is None:
                self._win_chrome(win, focused, quiet=self._chrome_quiet)
            return
        if not self._window_frame(gc, ox, oy, scale, defer, src):
            self._blit_game(self._root_canvas, gc, ox, oy, scale,
                            defer=defer, src=src)
        if full and win.docked is None:
            self._win_chrome(win, focused)

    def _player_rows(self, win, gc, ox, oy, scale):
        """A window-bound app's frame through the shown ring: where its run
        says what its draw changed of its canvas (`Player.change`, the
        rect its pass's shift or rows frame spans, inside a region that
        holds the window's whole view) and the ring vouches for the buffer
        this frame paints, only that and what the buffer lags is copied onto
        the glass; else what it shows whole (`app_window_content`), one copy
        at 1:1 (`blit_game` crops the canvas first). Records the frame's change either way; whether
        it composited."""
        ws = self.ws
        p = ws.player
        ch = p.change
        f = ws._frames_drawn
        own = self._own
        ok = self._shown_ok(win)
        cx, cy, _cw, _ch = win.content_rect()
        cw, chh = self.app_window_content()     # what of the canvas the window shows
        root = self._root_canvas
        if (scale != 1 or ox != cx or oy != cy or cw > gc.w or chh > gc.h
                or not hasattr(root, "blit_strip_rect")):
            own[4] = 0
            self._shown_note(win, f, ok, False)
            return False
        if (not ch[8] or p.change_f != _ui_draw.painted() or ch[4] > 0 or ch[5] > 0
                or ch[6] < cw or ch[7] < chh):
            own[4] = 0
            self._shown_note(win, f, ok, False)
            root.blit_strip_rect(gc, cx, cy, cx, cy, cw, chh)
            self._win_touched = True
            return True
        own[0] = ch[0]
        own[1] = ch[1]
        own[2] = ch[2]
        own[3] = ch[3]
        own[4] = 1
        sc = self._sc
        sc[0] = ch[0]
        sc[1] = ch[1]
        sc[2] = ch[2]
        sc[3] = ch[3]
        if not ok or not self._shown_chain(win, f, sc):
            self._shown_note(win, f, ok, False)
            root.blit_strip_rect(gc, cx, cy, cx, cy, cw, chh)
            self._win_touched = True
            return True
        x0 = sc[0] if sc[0] > 0 else 0
        y0 = sc[1] if sc[1] > 0 else 0
        x1 = sc[2] if sc[2] < cw else cw
        y1 = sc[3] if sc[3] < chh else chh
        if x1 > x0 and y1 > y0:
            self._root_canvas.blit_strip_rect(gc, cx, cy, cx + x0, cy + y0, x1 - x0, y1 - y0)
            self._win_touched = True
        self._shown_part(win, own[0], own[1], own[2], own[3])
        self._shown_note(win, f, ok, False)
        return True

    def _blit_game(self, sc, gc, ox, oy, scale, defer=False, src=None):
        """Integer-scale the 320x240 game canvas into the desktop at (ox, oy) --
        the windowed sibling of the parent's centered composite_game. A device
        system canvas (the P4, #58) exposes a native blit_game (RGB565 scaled
        blit in one moy_gfx call) -- the Python index loops below can't run there
        (no index buffer) and would be far too slow anyway. On a RECORDING
        desktop (the web console) there is no framebuffer to copy into: ship the
        game frame as ONE scaled self-contained spr instead (the same move as
        FullscreenStackWM._composite_via_spr)."""
        if hasattr(sc, "blit_game"):
            sc.blit_game(gc, ox, oy, scale, defer, src)
            return
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None:
            return
        if sbuf is None:
            if src is not None:            # ship only the declared view region
                sx, sy, vw, vh = src
                rows = bytearray(vw * vh)
                for gy in range(vh):
                    b = (sy + gy) * gc.w + sx
                    rows[gy * vw:(gy + 1) * vw] = gbuf[b:b + vw]
                img = _Blit(vw, vh, bytes(rows), -1)
            else:
                img = _Blit(gc.w, gc.h, bytes(gbuf), -1)
            img._paint = True              # -> the compact b64 wire form (~2.4x lighter)
            sc.spr(img, ox, oy, scale)
            return
        stride = gc.w
        sx, sy, gw, gh = src if src is not None else (0, 0, gc.w, gc.h)
        sw, sh = sc.w, sc.h
        for gy in range(gh):
            grow = (sy + gy) * stride + sx
            for s in range(scale):
                dy = oy + gy * scale + s
                if dy < 0 or dy >= sh:
                    continue
                base = dy * sw + ox
                if scale == 1:
                    end = min(gw, sw - ox)
                    if end > 0:
                        sbuf[base:base + end] = gbuf[grow:grow + end]
                else:
                    out = base
                    for gx in range(gw):
                        if out + scale <= (dy + 1) * sw:
                            sbuf[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                        out += scale
