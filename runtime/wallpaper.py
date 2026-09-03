"""The desktop wallpaper backdrop (#28), extracted from Workstation
(runtime/console.py) as a component -- docs/history/shell_layers_refactor_v1.md (Move 1a).

Wallpaper isn't a screen like the surface Layers -- it's the SHARED backdrop the
launcher home AND the Settings screen both draw behind their chrome (the Picotron
"wallpaper is a cart" model). So it's a component the Workstation owns (ws.wallpaper),
and both draw paths call ws.wallpaper.draw(dt).

Boundary (single source of truth): this component owns only the RENDERING + the
COMPILED cache -- a chosen wallpaper-type cart compiled into its OWN namespace and run
(_draw, optionally _update) as the backdrop, or a solid MOY64 fill fallback. The CHOICE
+ the picker/query API are the LOOK's (appearance.py, #209 landing D):
ws.look.wallpaper_id, plus select_wallpaper / cycle_wallpaper / wallpaper_options /
wallpaper_carts / wp_id_for / wp_cart_by_id. select_wallpaper drives this component
via clear() + compile(cart); draw() reads ws.look.wallpaper_id for the fill
fallback. It reaches the cart-run machinery (build sheet/tilemap, make_api) through
its self.ws back-ref; the audio/pmem building blocks for the wallpaper's own namespace
are imported (leaf modules; same bare-or-runtime fallback the other extracted modules
use). `NAMES` is injected; `_err_text` is duplicated (tiny/pure).
"""
try:
    from audio import AudioBank, AudioEngine
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio import AudioBank, AudioEngine
try:
    from widgets import Pmem, _SilentAudio, _Blit, _err_text
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import Pmem, _SilentAudio, _Blit, _err_text
try:
    from moy_image import cover_sig, load_wallpaper_preview, save_wallpaper_preview
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_image import (cover_sig, load_wallpaper_preview,
                                   save_wallpaper_preview)


class Wallpaper:
    """The wallpaper backdrop component (#28). Owns the RENDERING (draw) + the compiled
    wallpaper-cart cache; ws.look.wallpaper_id + the picker API are the look's."""

    def __init__(self, ws, names):
        self.ws = ws
        self._NAMES = names
        # Compiled wallpaper cart: its namespace + _update/_draw + the cart dict, or all
        # None for a solid-fill backdrop. `_wp_live` runs the wallpaper's _update too
        # (set False to save cost: a _draw-only static backdrop).
        self._wp_ns = None
        self._wp_update = None
        self._wp_draw = None
        self._wp_cart = None
        self._wp_live = True
        # The Appearance monitor's PREVIEW runner: the same cart compiled a
        # second time over an OFFSCREEN host canvas, so the little screen can
        # show the full frame without touching the game canvas (and without a
        # readable framebuffer -- the web tier records the blit as one spr).
        self._pv_canvas = None
        self._pv_ns = None
        self._pv_update = None
        self._pv_draw = None
        self._pv_restore = None
        self._pv_for = None
        # Static (compute-once) previews: {(path, vw, vh): (src_sig, _Blit)}.
        # Keyed by source stamp, so it survives selection changes and drops
        # stale entries on its own; bounded below.
        self._static_cache = {}

    def clear(self):
        """Drop the compiled wallpaper (back to a solid fill). Called by
        ws.look.select_wallpaper before it (re)compiles the new choice."""
        self._wp_ns = self._wp_update = self._wp_draw = None
        self._wp_cart = None
        self._wp_restore_bg = None
        self._pv_ns = self._pv_update = self._pv_draw = None
        self._pv_restore = None
        self._pv_for = None
        # #63 leak fix: return the dead wallpaper's pooled layer buffers for reuse.
        rl = getattr(self.ws.canvas, "reclaim_layers", None)
        if rl is not None:
            rl("wallpaper")

    def _stock_bracket(self):
        """The backdrop never follows a cart's private raster: while a per-run
        cart canvas (SPEC.md 1/3.1) is bound, every live ws.canvas read in this
        component must see the STOCK canvas the wallpaper world lives on. This
        returns (restore_needed, previous) after pointing ws.canvas at stock --
        pair with _stock_unbracket in a finally."""
        ws = self.ws
        stock = getattr(ws, "_run_canvas_stock", None)
        if stock is None or ws.canvas is stock:
            return (False, None)
        prev = ws.canvas
        ws.canvas = stock
        return (True, prev)

    def _stock_unbracket(self, bracket):
        restore, prev = bracket
        if restore:
            self.ws.canvas = prev

    def compile(self, cart):
        b = self._stock_bracket()
        try:
            self._compile(cart)
        finally:
            self._stock_unbracket(b)

    def _compile(self, cart):
        """Compile a wallpaper cart into its own namespace + grab its _update/_draw,
        running its _init. Guarded: any failure leaves the backdrop on the solid
        fill (a broken wallpaper must never take down the desktop)."""
        ws = self.ws
        try:
            sheet = ws._build_sheet(cart)
            tilemap = ws._build_tilemap(cart)
            ns = ws.make_api(ws.canvas, ws.input, dict(cart.get("cfg", {})),
                             sheet, _SilentAudio(AudioEngine(AudioBank.default())),
                             tilemap, Pmem(), None, cart.get("images") or {},
                             flags=ws._build_flags(cart),   # SPEC.md 3.5 tile flags
                             owner="wallpaper")   # #63: layer loans reclaimed on clear()
            exec(compile(cart["src"], "<wallpaper>", "exec"), ns)
            if ns.get("_init"):
                ns["_init"]()
            self._wp_ns = ns
            self._wp_cart = cart
            self._wp_update = ns.get("_update")
            self._wp_draw = ns.get("_draw")
            self._wp_restore_bg = ns.get("_moy_restore_bg")   # #63 declared background
        except Exception as exc:  # noqa: BLE001
            print("Moybyte wallpaper error:", _err_text(exc))
            self._wp_ns = self._wp_update = self._wp_draw = None

    def is_animating(self, dt):
        """True when a LIVE wallpaper (its own _update advancing it) is loaded, so the
        home/settings backdrop must keep redrawing without input (the #44 gate reads
        this after checking screen in launcher/settings)."""
        return (self._wp_live and self._wp_update is not None
                and self._wp_draw is not None and dt > 0)

    def draw(self, dt):
        b = self._stock_bracket()       # a bound cart canvas never hosts the backdrop
        try:
            self._paint(dt)
        finally:
            self._stock_unbracket(b)

    def _paint(self, dt):
        """Paint the backdrop: run the wallpaper cart's _update/_draw, or a solid
        fill. Always fully clears the canvas so the foreground draws over a clean
        backdrop. Guarded so a misbehaving wallpaper degrades to a fill.

        Status-strip safe area (#46): on the launcher/settings the strip sits along the
        top, so a wallpaper that draws art/text near y=0 (the shipped ones print their
        title at y=10) gets sliced by the strip band. Before running the wallpaper we
        push its drawing DOWN by the strip height (camera) and clip the art to the rows
        below the strip, so the wallpaper composites into a known safe area beneath the
        strip and is never cut into. cls() ignores camera/clip (like TIC-80), so the
        backdrop FILL still covers the whole surface -- only the foreground art shifts,
        leaving a clean strip band of the wallpaper's own background colour."""
        NAMES = self._NAMES
        ws = self.ws
        # Paint's desktop document is a SYSTEM-domain wallpaper. In particular the
        # 512x300 preset maps exactly 2x onto the P4's 1024x600 panel instead of being
        # forced through the fixed 320x240 cart canvas and cover-cropped. My Art still
        # has a wallpaper cartridge identity for discovery/settings; only its pixels
        # take this direct, resolution-aware path.
        art = getattr(ws, "artwork", None)
        if art is not None and art.owns_wallpaper(ws.look.wallpaper_id):
            try:
                if art.draw_wallpaper(ws.sys_canvas):
                    ws._reset_canvas_state()
                    return
            except Exception as exc:  # noqa: BLE001 -- fall back to the wallpaper cart
                print("Moybyte artwork wallpaper error:", _err_text(exc))
        if self._wp_draw is not None:
            try:
                rb = getattr(self, "_wp_restore_bg", None)
                if rb is not None:
                    rb()            # #63: restore the wallpaper's declared backdrop
                if self._wp_live and self._wp_update is not None and dt > 0:
                    self._wp_update(dt)
                sh = ws.layout.status_h
                safe = sh if ws.screen in ("launcher", "settings") else 0
                # Two-domain tiers (#39/#58): the wallpaper cart draws on the 320x240
                # GAME canvas but status_h is SYSTEM-layout units (36px at font 2) --
                # shifting 240 game rows by that cut the scene's bottom clean off
                # (glass-found on the P4). The cover-crop composite + the bar drawn
                # OVER the backdrop make the safe area moot there anyway.
                if ws.sys_canvas is not ws.canvas:
                    safe = 0
                if safe:
                    # camera(0, -sh): a draw at world y lands at screen y + sh (below
                    # the strip); clip keeps the art inside the safe rows.
                    ws.canvas.camera(0, -safe)
                    ws.canvas.clip(0, safe, ws.canvas.w, ws.canvas.h - safe)
                # COMMAND-ONLY GAME CANVAS (#175, web/wasm): there are no pixels to
                # composite afterwards -- _backdrop_blit needs gc.buf and returns
                # early -- so the cart's commands ARE the backdrop and must be
                # PLACED here, cover-style, exactly as _backdrop_blit would have
                # scaled its framebuffer. Without this the wallpaper cart drew
                # unbracketed at the origin and the desktop showed it in the
                # top-left 320x240 (owner report 2026-07-31).
                _wv = self._view_bracket(ws)
                _gp = self._enter_pointer(ws, _wv)
                try:
                    self._wp_draw()
                finally:
                    if _gp is not False:
                        ws.input.game_pointer = _gp
                if _wv:
                    ws.sys_canvas.view()
                # Clear any camera/clip/pal/palt (#11) the wallpaper cart set (and the
                # safe-area camera/clip above), so the home/settings foreground (icons,
                # status strip) draws clean at full extent.
                ws._reset_canvas_state()
                # Two-domain seam (#39): a wallpaper CART draws on the fixed 320x240
                # game canvas (it runs under the kid cart API). On a DISTINCT (big)
                # system canvas the desktop backdrop lives on the SYSTEM canvas, so
                # composite the cart's frame up into it (centered integer scale over
                # a black bezel) -- without this the big desktop showed whatever was
                # left on the system canvas (the windowed WM exposed it as stale
                # pixels). One object on the 320x240 tiers -> this never runs there.
                sc = ws.sys_canvas
                if sc is not ws.canvas:
                    self._backdrop_blit(sc, ws.canvas)
                return
            except Exception as exc:  # noqa: BLE001 -- drop a broken wallpaper to the fill
                print("Moybyte wallpaper draw error:", _err_text(exc))
                ws._reset_canvas_state()
                self._wp_ns = self._wp_update = self._wp_draw = None
        # Solid fill fallback (also the "fill:<color>" built-ins). Fill the SYSTEM
        # canvas -- the surface the desktop actually shows (#39; the same object as
        # the game canvas on the 320x240 tiers, so byte-identical there).
        wp = ws.look.wallpaper_id or "fill:dark_blue"
        name = wp[5:] if isinstance(wp, str) and wp.startswith("fill:") else "dark_blue"
        ws.sys_canvas.cls(NAMES.get(name, NAMES["dark_blue"]))

    def _enter_pointer(self, ws, bracketed):
        """Re-map the pointer into the WALLPAPER's own space for the duration of
        its draw, returning the value to restore (or False when untouched).

        `input.game_pointer` is built for the GAME viewport (on the windowed
        tier, the player WINDOW's rect), but the wallpaper is placed by the
        cover-crop instead -- so an interactive backdrop like sakura's
        run-from-the-cursor petals read the wrong coordinates: only the middle
        320x240 of the desktop reacted, and it reacted as if it were the whole
        screen (owner, 2026-07-31). Invert the SAME transform _view_bracket
        applies: cart = (screen - origin) / scale."""
        if not bracketed:
            return False
        gp = getattr(ws.input, "game_pointer", None)
        p = getattr(ws, "pointer", None)
        if p is None:
            return False
        sc, gc = ws.sys_canvas, ws.canvas
        gw, gh = gc.w, gc.h
        scale = max(1, (sc.w + gw - 1) // gw, (sc.h + gh - 1) // gh)
        ox = (sc.w - gw * scale) // 2
        oy = (sc.h - gh * scale) // 2
        gx = (int(getattr(p, "x", 0)) - ox) // scale
        gy = (int(getattr(p, "y", 0)) - oy) // scale
        # Out-of-frame reads as "no pointer" rather than a clamped edge value,
        # so petals never stampede toward a corner the cursor is not in.
        if not (0 <= gx < gw and 0 <= gy < gh):
            ws.input.game_pointer = None
            return gp
        ws.input.game_pointer = (gx, gy, False, bool(getattr(p, "down", False)))
        return gp

    def draw_preview(self, cv, rect, dt):
        """The Appearance monitor's screen: the chosen wallpaper FIT inside
        `rect` -- the FULL frame, vs draw()'s full-bleed cover-crop. Same
        guarded degradation ladder as draw(): My Art direct -> cart frame ->
        solid fill.

        A cart wallpaper shows its COMPUTED preview: rendered once per cart
        source through the preview runner (the cart compiled a second time onto
        an OFFSCREEN canvas -- never the game canvas) and cached like a
        thumbnail (RAM memo + the thumbs/wp*.mct sidecar). A STILL, so the
        appearance screen closes the redraw gate like any static UI.

        EVERY TIER -- and until 2026-08-15 that sentence was false on the two
        that matter most, in a way nothing reported. The runner built its
        offscreen surface from `runtime/canvas.py`, the pure-Python indexed
        raster, whose import reached `runtime/palette.py` -> `colorsys`, which
        MicroPython does not have. So on a board `_ensure_preview` took its
        `return False` path on every call and this panel drew its black fill and
        nothing else, from the day it shipped; every caller is guarded, so the
        failure read as a feature that quietly did not exist. Reproduced on P4
        glass (tests/test_staging_closure.py records the session).

        Deleting that raster did not move the gate to a new place, and this
        docstring said for a while that re-enabling the preview was a product
        decision about a second raster in frozen flash. It is not: there is no
        second raster left to decline. The runner draws on
        `device_canvas.DeviceCanvas`, which every tier already runs, and it asks
        the BACKEND for one through `ws.make_game_canvas` instead of importing a
        per-tier module -- see _ensure_preview. So the tier branch is gone
        rather than extended, and the degradation that remains (no factory -> a
        black fill) is the honest one: a Workstation whose backend cannot build
        an offscreen canvas at all.

        What a board pays, once per cart and preview size: a 320x240 RGB565
        scratch surface, the wallpaper's own _init/_update/_draw in interpreted
        Python, and the reduction in _preview_indices. The .mct sidecar is what
        holds it to once -- every later open reads the file and never builds a
        runner at all."""
        # Bracket to the STOCK canvas, like draw() and compile(): a per-run cart
        # canvas (SPEC.md 3.1) can be bound over ws.canvas while the Appearance
        # window is on screen (the windowed tier draws apps beside a live game),
        # and the preview sizes both its render surface and its sidecar key off
        # ws.canvas -- so without this a preview computed during a 128x120 run
        # would be keyed and shaped to that cart's raster, not the wallpaper's.
        _b = self._stock_bracket()
        try:
            self._draw_preview(cv, rect, dt)
        finally:
            self._stock_unbracket(_b)

    def _draw_preview(self, cv, rect, dt):
        NAMES = self._NAMES
        ws = self.ws
        x, y, w, h = rect
        wp = ws.look.wallpaper_id or ""
        if isinstance(wp, str) and wp.startswith("fill:"):
            cv.rect(x, y, w, h, NAMES.get(wp[5:], NAMES["dark_blue"]))
            return
        art = getattr(ws, "artwork", None)
        if art is not None and art.owns_wallpaper(wp):
            try:
                size = art.wall_size()
                if size:
                    aw, ah = size
                    if aw * h >= ah * w:            # aspect-fit inside rect
                        fw, fh = w, max(1, ah * w // aw)
                    else:
                        fw, fh = max(1, aw * h // ah), h
                    img = art.thumbnail(fw, fh)
                    if img is not None:
                        cv.rect(x, y, w, h, NAMES["black"])
                        cv.spr(img, x + (w - fw) // 2, y + (h - fh) // 2)
                        return
            except Exception as exc:  # noqa: BLE001 -- fall through to the cart/fill
                print("Moybyte artwork preview error:", _err_text(exc))
        cv.rect(x, y, w, h, NAMES["black"])
        # A stable _Blit identity per cart+size lets a device canvas keep its
        # sprite cache warm across frames.
        img = self._static_preview(w, h)
        if img is not None:
            s = max(1, min(w // img.w, h // img.h))
            cv.spr(img, x + (w - img.w * s) // 2, y + (h - img.h * s) // 2, s)

    def _ensure_preview(self):
        """Compile the current wallpaper cart into the preview runner (once per
        selection; clear() invalidates). False when there's no cart, no backend
        canvas factory, or the compile fails.

        THE FACTORY IS ASKED FOR, NOT IMPORTED, and that is the whole fix for
        the board-side hole draw_preview describes. What stood here was an
        import ladder -- `runtime.host_canvas`, then `web_canvas`, then give up
        -- which named the two tiers that happen to expose a canvas factory as a
        MODULE and silently excluded the two that inject one instead. Adding a
        third rung (`import device_canvas`, which does resolve on a board) would
        have worked and would also have made the host's behaviour depend on
        import ORDER, because host_canvas.install() appends the T-Deck modules
        directory to sys.path -- so `device_canvas` is importable there too, and
        a rung tried too early would quietly render the host preview through a
        different compositor than the host draws on.

        `ws.make_game_canvas` has no such ambiguity: it is a backend attach
        point like `make_api`, `(w, h) -> an offscreen DeviceCanvas of that
        size`, injected by all four heads (runtime/host_app.py, web_boot.py and
        both boards' moy_runtime.py) and pinned as a service by
        tests/test_board_service_parity.py. Each head hands back a canvas over
        ITS OWN compositor -- HostCompositor, WebCompositor, an moy_alloc-backed
        _LayerComp in board PSRAM -- so there is nothing left to choose between
        and no per-tier name in this file at all. It is the same factory
        `Workstation.bind_run_canvas` uses to give a cart with a small raster
        (SPEC.md 3.1) a canvas to play on, which is the same request: an
        offscreen surface the size of a cart frame.

        It can still answer None -- the T-Deck's returns None on a build with no
        native kernel -- and a Workstation nobody injected one on (a bare test
        fixture) has None outright. Both degrade to the black fill, which is the
        behaviour the old ladder produced for a whole class of tier by
        accident."""
        cart = self._wp_cart
        if cart is None:
            return False
        if self._pv_draw is not None and self._pv_for is cart:
            return True
        ws = self.ws
        make_canvas = getattr(ws, "make_game_canvas", None)
        if make_canvas is None:
            return False            # a backend with no offscreen canvas factory
        try:
            pv = self._pv_canvas
            if pv is None:
                pv = make_canvas(ws.canvas.w, ws.canvas.h)
                if pv is None:      # a build with no native kernel to draw with
                    return False
                self._pv_canvas = pv
            else:
                rl = getattr(pv, "reclaim_layers", None)
                if rl is not None:      # #63: return the last preview's layer loans
                    rl("wallpaper_pv")
            # #66 live-set diet: the slimmed cart rehydrates for this compile
            # (src/sheet bake into the preview ns), then re-slims -- the same
            # dance look.select_wallpaper does for the backdrop compile.
            ws.carts.rehydrate(cart)
            try:
                sheet = ws._build_sheet(cart)
                tilemap = ws._build_tilemap(cart)
                ns = ws.make_api(pv, ws.input, dict(cart.get("cfg", {})),
                                 sheet, _SilentAudio(AudioEngine(AudioBank.default())),
                                 tilemap, Pmem(), None, cart.get("images") or {},
                                 flags=ws._build_flags(cart),
                                 owner="wallpaper_pv")
                exec(compile(cart["src"], "<wallpaper-preview>", "exec"), ns)
            finally:
                if cart is not getattr(ws, "_fat_cart", None):
                    ws.carts.reslim(cart)
            if ns.get("_init"):
                ns["_init"]()
            self._pv_ns = ns
            self._pv_update = ns.get("_update")
            self._pv_draw = ns.get("_draw")
            self._pv_restore = ns.get("_moy_restore_bg")
            self._pv_for = cart
            return self._pv_draw is not None
        except Exception as exc:  # noqa: BLE001 -- a broken cart keeps a dark screen
            print("Moybyte wallpaper preview error:", _err_text(exc))
            self._pv_ns = self._pv_update = self._pv_draw = None
            self._pv_for = None
            return False

    def _src_sig(self, cart):
        """cover_sig of the cart's SOURCE -- the staleness stamp for computed
        previews. Rehydrates a slimmed cart (#66) just long enough to read it."""
        src = cart.get("src")
        if src is None:
            ws = self.ws
            try:
                ws.carts.rehydrate(cart)
                src = cart.get("src")
            finally:
                if cart is not getattr(ws, "_fat_cart", None):
                    ws.carts.reslim(cart)
        return cover_sig(src) if src else None

    def _static_preview(self, w, h):
        """The COMPUTED (thumbnail-model) preview of the current wallpaper
        cart, sized to fit (w, h): RAM memo -> wp sidecar -> one offscreen
        render (persisted). Returns a _Blit or None. The render is the only
        expensive rung and the only one that needs a canvas factory, so a tier
        that cannot render still SHOWS a preview any tier once wrote; the
        result is stamped against the cart's source, so an edit recomputes it."""
        cart = self._wp_cart
        if cart is None:
            return None
        gw, gh = self.ws.canvas.w, self.ws.canvas.h
        if gw * h >= gh * w:                    # aspect-fit target, capped at
            vw, vh = w, max(1, gh * w // gw)    # native (draw upscales cleanly)
        else:
            vw, vh = max(1, gw * h // gh), h
        if vw >= gw:
            vw, vh = gw, gh
        path = cart.get("path")
        key = (path, vw, vh)
        try:
            sig = self._src_sig(cart)
        except Exception:  # noqa: BLE001 -- an unreadable cart has no preview
            return None
        ent = self._static_cache.get(key)
        if ent is not None and ent[0] == sig:
            return ent[1]
        pix = None
        if path and sig is not None:
            try:
                pix = load_wallpaper_preview(path, vw, vh, sig)
            except Exception:  # noqa: BLE001 -- a bad sidecar just recomputes
                pix = None
        if pix is None:
            pix = self._render_static(vw, vh)
            if (pix is not None and path and sig is not None
                    and getattr(self.ws, "can_manage", False)):
                try:
                    self.ws._with_sd(lambda: save_wallpaper_preview(
                        path, vw, vh, sig, pix))
                except Exception:  # noqa: BLE001 -- regenerable cache
                    pass
        if pix is None:
            return None
        img = _Blit(vw, vh, bytes(pix), -1)
        img._paint = True                       # compact b64 wire form on the web
        while len(self._static_cache) > 8:      # bound the memo
            self._static_cache.pop(next(iter(self._static_cache)))
        self._static_cache[key] = (sig, img)
        return img

    def _render_static(self, vw, vh):
        """Render ONE representative frame of the current wallpaper cart on
        the offscreen runner canvas (live scenes warmed a few seconds so the
        still looks alive) and resample it to (vw, vh). The one-time cost the
        sidecar amortizes away; None when the runner is unavailable."""
        if not self._ensure_preview():
            return None
        try:
            if self._pv_restore is not None:
                self._pv_restore()
            if self._pv_update is not None:
                for _ in range(120):            # ~4s at 30Hz
                    self._pv_update(1 / 30)
            self._pv_draw()
            pv = self._pv_canvas
            rs = getattr(pv, "reset_state", None)
            if rs is not None:
                rs()
            fb = getattr(pv, "flush_batch", None)
            if fb is not None:
                fb()
            return self._preview_indices(pv, vw, vh)
        except Exception as exc:  # noqa: BLE001 -- a broken cart has no preview
            print("Moybyte wallpaper preview error:", _err_text(exc))
            self._pv_ns = self._pv_update = self._pv_draw = None
            self._pv_for = None
            return None

    @classmethod
    def _preview_indices(cls, pv, vw, vh):
        """The preview frame, resampled to exactly (vw, vh), as ONE PALETTE
        INDEX PER BYTE. None on a build that cannot reduce.

        WHY THE FORMAT CHANGES HERE, and nowhere downstream. Every tier's canvas
        is RGB565 since runtime/canvas.py was deleted, so something has to
        convert -- and everything past this point is index-native and should
        stay that way: the `_Blit` this feeds, the `spr()` that draws it, and the
        `thumbs/wp<w>x<h>.mct` sidecar, whose validator checks `len == w * h`
        (moy_image.load_wallpaper_preview). Converting at the RENDER boundary
        costs one pass over a small buffer and leaves all of that untouched.
        Converting downstream instead would change a persisted on-disk format
        and need a migration for every sidecar already written to a kid's card,
        which is a lot of moving parts to buy nothing.

        RESAMPLE FIRST, REDUCE SECOND, and the order is load-bearing rather than
        incidental. Nearest-neighbour picks source pixels verbatim, so the two
        orders produce byte-identical output (pinned by
        tests/test_wallpaper_preview.py) -- but reducing first walks all 76,800
        pixels of the canvas, where reducing the sample walks only the ~17,000
        that survive it. Measured on the host for a 152x114 preview of a 320x240
        canvas: 10.7ms reduce-then-sample against 3.8ms sample-then-reduce, 2.8x.
        Both loops are interpreted Python, so a board pays the same ratio on a
        far slower interpreter -- which is the difference between a pause and a
        freeze, on the tier this only just started running on.

        The reduction is EXACT -- MOY64's 64 entries resolve to 64 distinct 565
        words -- but it runs `strict=False`: a wallpaper is arbitrary cart code,
        and a single unmappable word (a cart that blits raw 565 in) should cost
        that pixel, not the whole preview."""
        gw, gh = pv.w, pv.h
        buf = getattr(pv, "_buf", None)
        if buf is None:
            # An INDEXED canvas, if one ever comes back. A contract rather than
            # a live lane: nothing in the tree publishes `.buf` today.
            pix = bytes(pv.buf)
            return (pix if (vw, vh) == (gw, gh)
                    else bytes(cls._sample(pix, gw, gh, vw, vh)))
        try:
            from device_canvas import to_indices
        except ImportError:                      # pragma: no cover
            return None
        stride = int(getattr(pv, "_stride", gw) or gw)
        ox = int(getattr(pv, "_ox", 0) or 0)
        oy = int(getattr(pv, "_oy", 0) or 0)
        if (vw, vh) != (gw, gh) or stride != gw or ox or oy:
            buf = cls._sample565(buf, gw, gh, vw, vh, stride, ox, oy)
        return to_indices(buf, getattr(pv, "_wire", None), False)

    @staticmethod
    def _sample(pix, gw, gh, vw, vh):
        """Nearest-neighbor resample of gw x gh indices to exactly vw x vh."""
        xm = [dx * gw // vw for dx in range(vw)]
        out = bytearray(vw * vh)
        o = 0
        for dy in range(vh):
            base = (dy * gh // vh) * gw
            for i in range(vw):
                out[o + i] = pix[base + xm[i]]
            o += vw
        return out

    @staticmethod
    def _sample565(buf, gw, gh, vw, vh, stride=None, ox=0, oy=0):
        """`_sample` in the 565 domain: gw x gh RGB565 pixels to exactly vw x vh.

        The twin exists because of the order argued in _preview_indices -- this
        is what runs BEFORE the reduction, on the wide frame, so the reduction
        only ever sees the pixels that survive.

        `stride`/`ox`/`oy` describe a canvas drawing into a sub-rect of a wider
        buffer (#155). Every backend's make_game_canvas returns a full-surface
        canvas today, so they default to the simple case and cost one multiply
        when they are not needed.

        Two byte reads per pixel rather than a slice assignment: a slice would
        allocate a fresh object per pixel, and this loop runs on MicroPython."""
        stride = gw if stride is None else stride
        xm = [(dx * gw // vw + ox) * 2 for dx in range(vw)]
        out = bytearray(vw * vh * 2)
        o = 0
        for dy in range(vh):
            base = (oy + dy * gh // vh) * stride * 2
            for i in range(vw):
                s = base + xm[i]
                out[o] = buf[s]
                out[o + 1] = buf[s + 1]
                o += 2
        return out

    def _view_bracket(self, ws):
        """Open a WM view bracket for a wallpaper cart drawing on a COMMAND-ONLY
        game canvas (#175): the cart's draw span is placed cover-style on the
        system canvas -- the smallest integer upscale that covers the desktop,
        centered (so the crop is symmetric), which is the SAME geometry
        _backdrop_blit computes for a real framebuffer. Returns True when a
        bracket was opened (the caller must close it).

        No-op on every other tier: a raster game canvas keeps the composite
        path, and the 320x240 tiers share one canvas object. A raster publishes
        its framebuffer either as `buf` (palette indices) or as `_buf` (RGB565,
        which every tier's canvas is since runtime/canvas.py was deleted) -- ask
        for BOTH, or a 565 canvas reads as command-only and gets bracketed."""
        sc = ws.sys_canvas
        gc = ws.canvas
        if sc is gc or getattr(gc, "buf", None) is not None \
                or getattr(gc, "_buf", None) is not None:
            return False
        view = getattr(sc, "view", None)
        if view is None:
            return False
        gw, gh = gc.w, gc.h
        if gw <= 0 or gh <= 0:
            return False
        scale = max(1, (sc.w + gw - 1) // gw, (sc.h + gh - 1) // gh)
        view((sc.w - gw * scale) // 2, (sc.h - gh * scale) // 2, scale, gw, gh)
        return True

    def _backdrop_blit(self, sc, gc):
        """Composite the 320x240 wallpaper frame into the big system canvas as the
        desktop backdrop, COVER-style (the Picotron model): the smallest integer
        upscale that covers the whole desktop, centered and cropped -- a real
        full-bleed backdrop, never a letterboxed rectangle floating in black.
        (Always full-desktop -- never routed through the WM, whose viewport may be
        a player WINDOW in windowed mode.) A system canvas with no readable
        framebuffer has nothing to copy into: draw the frame as ONE scaled spr
        instead, and let the canvas clip the crop."""
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        bc = getattr(sc, "blit_cover", None)
        if bc is not None:
            # A device system canvas (the P4, #58): native cover-crop blit in one
            # moy_gfx call -- an RGB565 canvas has no index buffer for the loops
            # below, and a per-frame Python expansion of ~600k px is unusable.
            bc(gc)
            return
        gbuf = getattr(gc, "buf", None)
        sbuf = getattr(sc, "buf", None)
        if gbuf is None:
            return
        gw, gh = gc.w, gc.h
        sw, sh = sc.w, sc.h
        scale = max(1, (sw + gw - 1) // gw, (sh + gh - 1) // gh)   # cover, not fit
        ox = (sw - gw * scale) // 2                                # <= 0 (crop)
        oy = (sh - gh * scale) // 2
        if sbuf is None:
            img = _Blit(gw, gh, bytes(gbuf), -1)
            img._paint = True              # -> the native blit_indices bake
            sc.spr(img, ox, oy, scale)
            return
        # Raster: expand each source row ONCE, then slice the visible crop into every
        # destination row it covers -- row-level copies, no per-pixel inner loop.
        crop_x = -ox if ox < 0 else 0
        dst_x = ox if ox > 0 else 0
        span = min(sw - dst_x, gw * scale - crop_x)
        for gy in range(gh):
            grow = gy * gw
            if scale == 1:
                er = gbuf[grow:grow + gw]
            else:
                er = bytearray(gw * scale)
                out = 0
                for gx in range(gw):
                    er[out:out + scale] = bytes((gbuf[grow + gx],)) * scale
                    out += scale
            seg = er[crop_x:crop_x + span]
            for s in range(scale):
                dy = oy + gy * scale + s
                if dy < 0 or dy >= sh:
                    continue
                base = dy * sw + dst_x
                sbuf[base:base + span] = seg
