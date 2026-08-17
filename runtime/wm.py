"""The window manager -- the S3/host fullscreen back-stack WM (Stage 6 of
docs/history/shell_ux_technical_plan_v1.md).

`FullscreenStackWM` is the ONLY tier-specific layer of the console (spec
shell_architecture_v1.md Section 3's tier table): on the S3 + the host simulator the
top of the process back-stack owns the whole 320x240, so this WM's whole job is

  * the game<->system COMPOSITE (`composite_game` + `viewport`/`game_xy`, #39): blit
    the fixed 320x240 GAME canvas into the (possibly larger) SYSTEM canvas as a
    fixed-aspect, integer-scaled, centered viewport, and map a system-canvas point
    back into game coords so a running cart / the editors hit-test correctly;
  * (Stage 6b) the process BACK-STACK (launcher root -> spawned app -> Player/tool),
    the state of record `Workstation.screen` becomes a read-only projection OF
    (`top_kind`) -- exactly as `menu_view` projects `EditorApp.tab` since Stage 3;
  * (Stage 6c) the MEMOIZED visible/draw layer stack -- rebuilt only on a real
    change (a back-stack push/pop OR an overlay-gate change), so a static top-of-stack
    allocates NO new per-frame list (retiring the ~9-lists/frame router churn the
    layers refactor introduced -- the #66 perf-recovery lever this stage owns).

It is a pure host/S3 mechanism -- it holds a `ws` back-reference (the shared canvases
+ layer instances the console owns) and NOTHING device-specific, so the SAME file the
host imports is frozen onto the device (staged into modules/ by build.sh, same pattern
as project.py/player.py/editor_app.py). It stays a LEAF: the tiny blittable + tick
helpers it needs are imported from the widgets leaf (bare name on the device / once
host_app has aliased it, `runtime.X` for a direct test load), so it never imports back
into console (no circular import).
"""

try:
    from widgets import _Blit, _ticks_ms, _ticks_diff
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import _Blit, _ticks_ms, _ticks_diff


_VIEWPORT_BEZEL = 0         # black -- the letterbox fill around a scaled game viewport


class FullscreenStackWM:
    """The fullscreen back-stack window manager (S3 + host). Holds a `ws` back-ref to
    the console it composites for; owns the game<->system viewport composite (#39) and
    -- from Stage 6b/6c -- the process back-stack `screen` projects onto + the memoized
    layer stack. One instance per Workstation, built in `Workstation.__init__`."""

    def __init__(self, ws):
        self.ws = ws
        # The process back-stack (Stage 6b): launcher root -> spawned app -> Player/tool.
        # A list of "kind" strings (the same vocabulary the flat `screen` attribute used:
        # "launcher" | "menu" | "settings" | "update" | "desktop"); the TOP is what
        # `Workstation.screen` now projects onto (`top_kind`). It is a DATA STRUCTURE the
        # string-keyed router READS (via ws._content_layer), NOT a second dispatcher --
        # source-of-truth != dispatch, so "one router at all times" holds (plan Section 6).
        # The launcher is the permanent root (index 0, never popped).
        self._stack = ["launcher"]
        # Content-change generation (Stage 6c): bumped whenever the top-of-stack kind
        # actually changes, so the memoized layer stack knows to rebuild. (menu_view tab
        # switches bump it via EditorApp.tab; overlay-gate changes are caught separately
        # by the per-call signature -- see _ensure_stack.)
        self.content_gen = 0
        # The memoized visible/draw/overlay layer stacks (Stage 6c). Rebuilt ONLY on a
        # real change -- a back-stack push/pop (content_gen), a menu_view tab switch
        # (content layer identity), or an overlay-gate/splash flip (the int signature).
        # On a static top-of-stack every frame's frame()/handle_input()/handle_pointer()
        # reuses the SAME list objects, so the per-frame router walk allocates NOTHING
        # (this retires the ~9-lists/frame churn the layers refactor introduced -- #66).
        self._cache_content = None    # the content Layer the caches were built for
        self._cache_gen = -1          # content_gen the caches were built at
        self._cache_sig = -1          # _overlay_sig() the caches were built at
        self._cache_visible = None    # [content] + overlays (draw/route order, bottom->top)
        self._cache_visible_rev = None  # visible reversed (top->bottom, input routing)
        self._cache_draw = None       # [splash-or-content] + overlays (draw order)
        self._cache_overlay = None    # the transient overlays + cursor
        # #75: composite_game's per-frame flush_batch probe, cached by canvas identity
        # (the canvas only changes on a web-view Tee swap, never mid-play).
        self._fb_for = None
        self._fb_fn = None

    # -- the process back-stack (Stage 6b) -----------------------------------

    def top_kind(self):
        """The kind of the top-of-stack process -- what `Workstation.screen` projects
        onto (a read-only projection of the back-stack top, exactly as `menu_view`
        projects `EditorApp.tab`)."""
        return self._stack[-1]

    def top_is(self, kind):
        """True iff the top-of-stack process is `kind`. The stack query the production
        readers use in place of `screen == kind` (Stage 6d) -- same answer, phrased as a
        question to the stack (the source of truth) rather than the projection string."""
        return self._stack[-1] == kind

    def top_is_player(self):
        """True iff a cart Player is on top (screen "desktop") -- the running-cart test
        the bar-visibility rule, the perf sampler, and the FPS overlay gate all key on."""
        return self._stack[-1] == "desktop"

    # -- the two worlds (#105): play (fullscreen) vs make (the windowed desk) --

    has_desk = False                   # capability probe: only WindowedWM has a desk

    def desk_open(self):
        """True while the MAKE world (the windowed tier's desk) is open --
        `ws.windowed_chrome` projects this. Always False on this fullscreen
        tier: there is one world here and it is the fullscreen stack."""
        return False

    def keys_to_cart(self):
        """Console hook (#44 redraw gate): this frame's keys are consumed by a
        healthy RUNNING cart, not by any system surface -- so a key press/hold
        must NOT mark the shell dirty (the cart's viewport animates on its own
        and the chrome around it is unchanged by cart-bound keys). On this
        fullscreen tier a running cart on top owns ALL input; the crash panel
        (cart_error) hands keys back to the system chrome."""
        ws = self.ws
        return (self._stack[-1] == "desktop" and ws.cart_error is None
                and (ws._update is not None or ws._draw is not None))

    def goto(self, kind):
        """Navigate the back-stack so `kind` is on top -- the mechanism behind the
        `ws.screen = kind` projection setter (and the explicit push/pop verbs).
        A `kind` already open BELOW the top is a RETURN (pop back to it, truncating
        everything above); a new `kind` is a PUSH. This reproduces the old flat-string
        `screen` transitions exactly at the top (golden-identical) while giving the
        stack an honest launcher-root -> ... -> top shape."""
        st = self._stack
        if st and st[-1] == kind:
            return                      # already on top -- no navigation, no gen bump
        idx = None
        for i in range(len(st) - 1, -1, -1):
            if st[i] == kind:
                idx = i
                break
        if idx is not None:
            del st[idx + 1:]            # RETURN: pop back to the already-open screen
        else:
            st.append(kind)             # PUSH: a newly-spawned screen
        self._on_nav()

    def _on_nav(self):
        """A real top-of-stack change happened: bump the content generation so the
        memoized layer stack (Stage 6c) rebuilds on the next access."""
        self.content_gen += 1

    def note_content_change(self):
        """Signal that the ACTIVE content layer changed WITHOUT a stack push/pop -- the
        one case being a menu_view/tab switch within the Editor (screen stays "menu" but
        _content_layer resolves to a different tab). EditorApp.tab's setter calls this so
        the memoized stack rebuilds; folding it here keeps all content-change signals on
        the WM (the memo's single owner)."""
        self.content_gen += 1

    # -- the memoized visible/draw/overlay layer stack (Stage 6c) ------------
    #
    # The single source of z-order + visibility (mirrors the pre-refactor tail of
    # frame()). Bottom -> top: the active content layer, the visible transient overlays,
    # then the always-on cursor. Drawing walks it bottom -> top (with the one game->system
    # composite at the domain boundary); input routing walks it top -> bottom so the
    # overlay that owns the event claims it before the content underneath.
    #
    # The whole point of Stage 6c: this used to be rebuilt from scratch on EVERY access
    # (_visible_stack/_draw_stack each allocated a fresh [content] + overlays list, walked
    # THRICE per frame -- ~9 fresh lists/frame even during play). Now it is memoized and
    # rebuilt ONLY on a real change, so a static top-of-stack allocates zero new lists.

    def _overlay_sig(self):
        """A cheap, allocation-free signature (int bitmask) of the splash state + which
        transient overlays are gated ON this frame. Together with the content-layer
        identity + content_gen it is the memo key: while it is unchanged the cached
        stacks are reused verbatim. Only small-int ops + boolean reads (no list/tuple
        alloc), so computing it every access is free on the hot path."""
        ws = self.ws
        au = ws.ach_ui
        sig = 0
        if ws._splash_until is not None:
            sig |= 1
        if ws.show_fps and self._stack[-1] == "desktop":
            sig |= 2
        if au._confetti_until and _ticks_diff(au._confetti_until, _ticks_ms()) > 0:
            sig |= 4
        if ws.show_achievements:
            sig |= 8
        if au._egg_active():
            sig |= 16
        if ws.ach.toast_active():
            sig |= 32
        if ws.sysmenu.open:
            sig |= 64
        if ws._about:
            sig |= 128
        if ws.notice_active():
            sig |= 256
        return sig

    def _ensure_stack(self):
        """Reuse the cached stacks when nothing changed, else rebuild once. The cache is
        valid iff content_gen is unchanged (no push/pop/tab-switch) and the overlay/
        splash signature matches. On a static frame both match and this returns without
        building a single list -- so the stack accessors below hand back the SAME objects
        every frame (zero allocation).

        #75: content_gen alone stands in for the content-layer identity on the hot path
        (no per-access ws._content_layer() call). The contract that makes this sound:
        EVERY writer that can change what _content_layer() resolves to bumps the gen --
        a back-stack push/pop/return goes through goto() -> _on_nav(), and an Editor tab
        switch (screen stays "menu", the resolved tab layer changes) goes through
        EditorApp.tab's setter -> note_content_change(). The memo guardrail test
        (tests/test_wm_stack_memo.py) pins both invalidation paths."""
        sig = self._overlay_sig()
        if (self._cache_visible is not None
                and self.content_gen == self._cache_gen
                and sig == self._cache_sig):
            return
        self._rebuild(self.ws._content_layer(), sig)

    def _rebuild(self, content, sig):
        """Build the overlay list once, then the visible/reversed/draw lists off it, and
        cache them with the key that produced them. This is the ONLY place the per-change
        stack lists are allocated -- the Stage-6c guardrail test asserts it is NOT reached
        on repeat static frames (the memo returns the cached objects instead)."""
        ws = self.ws
        overlays = []
        # Perf HUD first: it's GAME-domain (drawn on the 320x240 canvas right after the
        # running cart, before the composite), so it must precede any system overlay.
        if sig & 2:
            overlays.append(ws._perf_layer)
        if sig & 4:
            overlays.append(ws._confetti_layer)
        if sig & 8:
            overlays.append(ws._ach_layer)
        if sig & 16:
            overlays.append(ws._egg_layer)
        if sig & 32:
            overlays.append(ws._toast_layer)
        if sig & 64:
            overlays.append(ws._sysmenu_layer)
        if sig & 128:
            overlays.append(ws._about_layer)
        if sig & 256:
            overlays.append(ws._notice_layer)
        overlays.append(ws._cursor_layer)          # cursor last -> above everything
        # The boot logo is a draw-time takeover of the content slot (input still routes to
        # the content underneath -- so _cache_visible keeps `content`, only the draw slot
        # swaps in the splash).
        draw_content = ws._splash_layer if (sig & 1) else content
        self._cache_overlay = overlays
        self._cache_visible = [content] + overlays
        self._cache_draw = [draw_content] + overlays
        rev = list(self._cache_visible)
        rev.reverse()                              # top -> bottom for input routing
        self._cache_visible_rev = rev
        self._cache_content = content
        self._cache_gen = self.content_gen
        self._cache_sig = sig

    def overlay_stack(self):
        """The transient overlays + cursor, in draw order (bottom -> top). Memoized."""
        self._ensure_stack()
        return self._cache_overlay

    def visible_stack(self):
        """The full z-ordered stack, bottom -> top: content, overlays, cursor. Memoized."""
        self._ensure_stack()
        return self._cache_visible

    def visible_stack_rev(self):
        """The visible stack top -> bottom (input routing order) -- cached pre-reversed, so
        the hot handle_input/handle_pointer path allocates neither a list nor a reversed()
        iterator."""
        self._ensure_stack()
        return self._cache_visible_rev

    def draw_stack(self):
        """The draw-order stack: same as visible_stack() except the boot logo, when armed,
        takes the content slot. Memoized."""
        self._ensure_stack()
        return self._cache_draw

    # -- two-domain composite + viewport coords (#39) ------------------------

    def viewport(self):
        """The composited game viewport as (ox, oy, scale) -- the top-left of the
        320x240 game canvas inside the system canvas, and its integer scale. (0, 0,
        1) when the two canvases are the same object (degradation)."""
        gc = self.ws.canvas
        sc = self.ws.sys_canvas
        if sc is gc:
            return (0, 0, 1)
        view = self._view_src()
        gw, gh = (view[2], view[3]) if view else (gc.w, gc.h)
        scale = min(sc.w // gw, sc.h // gh)
        if scale < 1:
            scale = 1
        ox = (sc.w - gw * scale) // 2
        oy = (sc.h - gh * scale) // 2
        return (ox, oy, scale)

    def game_xy(self, px, py):
        """Map a SYSTEM-canvas point (where the pointer lives) into GAME-canvas
        coords, so a running cart / the editors (drawn in the 320x240 viewport) hit-
        test correctly. Identity in the degradation case. A cart-declared VIEW
        (`view(w, h)`) shifts the mapping by its source origin, so touch coords
        stay in full game-canvas space -- the cart's own frame of reference."""
        ox, oy, scale = self.viewport()
        view = self._view_src()
        sx, sy = (view[0], view[1]) if view else (0, 0)
        return (sx + (px - ox) // scale, sy + (py - oy) // scale)

    def _view_src(self):
        """The cart view rect the COMPOSITE honors, or None. One source of
        truth for every viewport/blit/tap-mapping site on both tiers (the
        windowed player window scales the view exactly like fullscreen)."""
        return getattr(self.ws, "game_view", None)

    def game_is_fullscreen(self):
        """True when the game viewport IS the screen -- i.e. composite_game
        letterboxes it over the whole system canvas. Always so on this WM; the
        windowed tier overrides (its desk world puts the game in a WINDOW).

        Read by the router when the game canvas is COMMAND-ONLY (#175): there
        the letterbox + placement must be emitted BEFORE the cart draws, and
        only the fullscreen presentation wants them."""
        return True

    def set_play_intent(self, intent):
        """No-op here (#178): a cart owns the whole screen on this tier, so
        there is no window whose default size could vary with WHY it started.
        The windowed WM overrides -- see its _win_size."""

    def letterbox_inplace(self):
        """Fill the bezel when the system canvas IS the game canvas.

        composite_game owns the bezel on every tier where the two canvases
        differ -- but it fills it AFTER the cart draws, which it can only get
        away with because it is copying into a DIFFERENT buffer. When they are
        the same object (the 320x240 device glass, where the composite is a
        no-op) nothing ever writes the pixels a cart-declared view leaves
        uncovered, and a cart is under no obligation to: celeste clears with a
        clipped `rectfill`, not `cls`. On a ping-pong double-buffered root the
        two buffers then hold DIFFERENT stale content and the border flashes at
        the frame rate -- reported on the T-Deck, invisible on the P4 for
        exactly this reason. So paint it here: before the cart draws, into the
        buffer it is about to draw into.

        Only the four bands OUTSIDE the view, not a whole-surface cls: this runs
        on the tier with the least fill rate to spare, and for a 256x240 view
        that is 15,360 px instead of 76,800. Camera and clip are identity here --
        the Player resets both after every cart frame."""
        view = self._view_src()
        if view is None:
            return                      # cart owns the whole canvas: nothing outside
        sc = self.ws.sys_canvas
        if sc is not self.ws.canvas:
            return                      # the composite will fill it, as it always has
        sx, sy, vw, vh = view
        if sy > 0:
            sc.rect(0, 0, sc.w, sy, _VIEWPORT_BEZEL)
        if sy + vh < sc.h:
            sc.rect(0, sy + vh, sc.w, sc.h - (sy + vh), _VIEWPORT_BEZEL)
        if sx > 0:
            sc.rect(0, sy, sx, vh, _VIEWPORT_BEZEL)
        if sx + vw < sc.w:
            sc.rect(sx + vw, sy, sc.w - (sx + vw), vh, _VIEWPORT_BEZEL)

    def composite_game(self):
        """Blit the fixed 320x240 GAME canvas into the SYSTEM canvas as a
        fixed-aspect, integer-scaled, centered viewport, filling the letterbox with
        a solid bezel color. A no-op when the two canvases are the same object (the
        degradation case: 320x240 system canvas == game canvas, pixel-identical to
        today). Index-only (host == device): reads game indices, writes them scaled
        into the system buffer, so no palette resolve is needed."""
        gc = self.ws.canvas
        sc = self.ws.sys_canvas
        # #63: complete any sprites still queued in the game canvas's auto-batch before
        # its buffer is read (usually already flushed by _reset_canvas_state; belt-and-
        # suspenders so a missed reset can never drop a cart's last sprite run).
        # #75: the flush_batch probe is cached by canvas identity -- this runs every
        # play frame, and the canvas only changes on a web-view Tee swap.
        if gc is not self._fb_for:
            self._fb_for = gc
            self._fb_fn = getattr(gc, "flush_batch", None)
        if self._fb_fn is not None:
            self._fb_fn()
        if sc is gc:
            return
        ox, oy, scale = self.viewport()
        # Native composite probe (the wm_windowed._blit_game convention): a
        # system canvas with a blit_game verb scales + letterboxes in C -- the
        # T-Deck path for a cart-declared small canvas (SPEC.md 1/3.1), where
        # the boot DeviceCanvas was promoted to system canvas and neither side
        # has an index `buf` for the Python loops below.
        bg = getattr(sc, "blit_game", None)
        if bg is not None:
            bg(gc, ox, oy, scale, src=self._view_src())
            return
        sc.cls(_VIEWPORT_BEZEL)                     # letterbox fill
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None or sbuf is None:
            # A recording system canvas (the web CommandCanvas) has no framebuffer to
            # copy into -- blit the whole game frame as one scaled sprite so the draw
            # stream carries the viewport. The game canvas must expose its pixels.
            self._composite_via_spr(gc, sc, gbuf, ox, oy, scale)
            return
        gw = gc.w
        sw = sc.w
        sh = sc.h
        # A cart-declared view (`view(w, h)`) composites only its source rect --
        # the stride stays the full canvas width, the row slices start at sx.
        view = getattr(self.ws, "game_view", None)
        sx, sy, srw, srh = view if view is not None else (0, 0, gw, gc.h)
        vw = srw * scale
        # The viewport always fits a system canvas >= the game (the supported case),
        # so take the fast row-replication path. A degenerate smaller-than-game system
        # canvas (negative offset / overflow) falls to a clipped per-pixel path that
        # can never resize the bytearray.
        fits = ox >= 0 and oy >= 0 and ox + vw <= sw and oy + srh * scale <= sh
        if fits:
            for gy in range(srh):
                grow = (sy + gy) * gw + sx
                for s in range(scale):
                    base = (oy + gy * scale + s) * sw + ox
                    if scale == 1:
                        sbuf[base:base + srw] = gbuf[grow:grow + srw]
                    else:
                        out = base
                        for gx in range(srw):
                            sbuf[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                            out += scale
            return
        for gy in range(srh):                       # clipped fallback (defensive)
            grow = (sy + gy) * gw + sx
            for s in range(scale):
                dy = oy + gy * scale + s
                if dy < 0 or dy >= sh:
                    continue
                dx0 = ox if ox > 0 else 0
                dx1 = min(sw, ox + vw)
                if dx1 <= dx0:
                    continue
                base = dy * sw
                for dx in range(dx0, dx1):
                    sbuf[base + dx] = gbuf[grow + (dx - ox) // scale]

    def _composite_via_spr(self, gc, sc, gbuf, ox, oy, scale):
        """Composite by blitting the game frame as ONE scaled sprite -- the path for a
        recording system canvas (the web CommandCanvas) that has no framebuffer to
        copy into. Records a single command per frame carrying the game pixels;
        tagged as a paint image so the recorder ships it BASE64 (["img", ..., b64,
        scale], ~2.4x lighter than a JSON int-list spr -- the webview's heaviest op)."""
        if gbuf is None:
            return
        view = getattr(self.ws, "game_view", None)
        if view is not None:
            # A cart-declared view ships only its region (smaller payload, and
            # the browser scales the VIEW like the framebuffer tiers do).
            sx, sy, vw, vh = view
            stride = gc.w
            rows = bytearray(vw * vh)
            for gy in range(vh):
                b = (sy + gy) * stride + sx
                rows[gy * vw:(gy + 1) * vw] = gbuf[b:b + vw]
            img = _Blit(vw, vh, bytes(rows), -1)
        else:
            img = _Blit(gc.w, gc.h, bytes(gbuf), -1)  # opaque (no transparency)
        img._paint = True                           # -> the compact b64 wire form
        sc.spr(img, ox, oy, scale)
