"""The window manager -- the S3/host fullscreen back-stack WM (Stage 6 of
docs/shell_ux_technical_plan_v1.md).

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
    from widgets import _Blit
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import _Blit


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

    # -- the process back-stack (Stage 6b) -----------------------------------

    def top_kind(self):
        """The kind of the top-of-stack process -- what `Workstation.screen` projects
        onto (a read-only projection of the back-stack top, exactly as `menu_view`
        projects `EditorApp.tab`)."""
        return self._stack[-1]

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

    # -- two-domain composite + viewport coords (#39) ------------------------

    def viewport(self):
        """The composited game viewport as (ox, oy, scale) -- the top-left of the
        320x240 game canvas inside the system canvas, and its integer scale. (0, 0,
        1) when the two canvases are the same object (degradation)."""
        gc = self.ws.canvas
        sc = self.ws.sys_canvas
        if sc is gc:
            return (0, 0, 1)
        scale = min(sc.w // gc.w, sc.h // gc.h)
        if scale < 1:
            scale = 1
        ox = (sc.w - gc.w * scale) // 2
        oy = (sc.h - gc.h * scale) // 2
        return (ox, oy, scale)

    def game_xy(self, px, py):
        """Map a SYSTEM-canvas point (where the pointer lives) into GAME-canvas
        coords, so a running cart / the editors (drawn in the 320x240 viewport) hit-
        test correctly. Identity in the degradation case."""
        ox, oy, scale = self.viewport()
        return ((px - ox) // scale, (py - oy) // scale)

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
        _fb = getattr(gc, "flush_batch", None)
        if _fb is not None:
            _fb()
        if sc is gc:
            return
        ox, oy, scale = self.viewport()
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
        vw = gw * scale
        # The viewport always fits a system canvas >= the game (the supported case),
        # so take the fast row-replication path. A degenerate smaller-than-game system
        # canvas (negative offset / overflow) falls to a clipped per-pixel path that
        # can never resize the bytearray.
        fits = ox >= 0 and oy >= 0 and ox + vw <= sw and oy + gc.h * scale <= sh
        if fits:
            for gy in range(gc.h):
                grow = gy * gw
                for s in range(scale):
                    base = (oy + gy * scale + s) * sw + ox
                    if scale == 1:
                        sbuf[base:base + gw] = gbuf[grow:grow + gw]
                    else:
                        out = base
                        for gx in range(gw):
                            sbuf[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                            out += scale
            return
        for gy in range(gc.h):                      # clipped fallback (defensive)
            grow = gy * gw
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
        copy into. Records a single spr command per frame carrying the game pixels."""
        if gbuf is None:
            return
        img = _Blit(gc.w, gc.h, list(gbuf), -1)     # opaque (no transparent index)
        sc.spr(img, ox, oy, scale)
