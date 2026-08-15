"""The desktop wallpaper backdrop (#28), extracted from Workstation
(runtime/console.py) as a component -- docs/shell_layers_refactor_v1.md (Move 1a).

Wallpaper isn't a screen like the surface Layers -- it's the SHARED backdrop the
launcher home AND the Settings screen both draw behind their chrome (the Picotron
"wallpaper is a cart" model). So it's a component the Workstation owns (ws.wallpaper),
and both draw paths call ws.wallpaper.draw(dt).

Boundary (single source of truth): this component owns only the RENDERING + the
COMPILED cache -- a chosen wallpaper-type cart compiled into its OWN namespace and run
(_draw, optionally _update) as the backdrop, or a solid MOY64 fill fallback. The CHOICE
+ the picker/query API stay on Workstation as the single source: ws.wallpaper_id, plus
ws.select_wallpaper / cycle_wallpaper / _persist_wallpaper / wallpaper_options /
wallpaper_carts / _wp_id_for / _wp_cart_by_id (all device/test-pinned). select_wallpaper
drives this component via clear() + compile(cart); draw() reads ws.wallpaper_id for the
fill fallback. It reaches the cart-run machinery (build sheet/tilemap, make_api) through
its self.ws back-ref; the audio/pmem building blocks for the wallpaper's own namespace
are imported (leaf modules; same bare-or-runtime fallback the other extracted modules
use). `NAMES` is injected; `_err_text` is duplicated (tiny/pure).
"""
try:
    from audio import AudioBank, AudioEngine
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.audio import AudioBank, AudioEngine
try:
    from widgets import Pmem, _SilentAudio, _Blit
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import Pmem, _SilentAudio, _Blit
try:
    from moy_image import cover_sig, load_wallpaper_preview, save_wallpaper_preview
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.moy_image import (cover_sig, load_wallpaper_preview,
                                   save_wallpaper_preview)


def _err_text(exc):
    """A short, kid-readable one-liner for an exception (type: message). Robust
    on MicroPython, whose exceptions sometimes stringify oddly."""
    try:
        name = type(exc).__name__
    except Exception:  # noqa: BLE001
        name = "Error"
    try:
        msg = str(exc)
    except Exception:  # noqa: BLE001
        msg = ""
    return (name + ": " + msg) if msg else name


class Wallpaper:
    """The wallpaper backdrop component (#28). Owns the RENDERING (draw) + the compiled
    wallpaper-cart cache; ws.wallpaper_id + the picker API stay on Workstation."""

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
        self._wp_images = {}   # captured at compile (the cart re-slims after)
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
        # #113 web wire diet: the backdrop composite's ship-once identity. A
        # STATIC wallpaper repeats the same 320x240 frame every draw, so once
        # two consecutive frames match it gets a serial name ("wall:N") and
        # rides /assets ONCE (wire_assets below) -- every later draw is a tiny
        # ["imgref", ...] instead of ~100KB of inline b64 (the measured
        # window-drag payload eater). A LIVE wallpaper's frame changes every
        # draw, so it keeps the inline form (status quo) and never churns the
        # client's asset cache.
        self._wire_pix = None          # last composite frame's raw indices
        self._wire_wh = (0, 0)
        self._wire_name = None         # set only while the frame is stable
        self._wire_serial = 0

    def clear(self):
        """Drop the compiled wallpaper (back to a solid fill). Called by
        ws.select_wallpaper before it (re)compiles the new choice."""
        self._wp_ns = self._wp_update = self._wp_draw = None
        self._wp_cart = None
        self._wp_images = {}
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
                             owner="wallpaper")   # #63: layer loans reclaimed on clear()
            exec(compile(cart["src"], "<wallpaper>", "exec"), ns)
            if ns.get("_init"):
                ns["_init"]()
            self._wp_ns = ns
            self._wp_cart = cart
            # Hold the images HERE, not via _wp_cart later: the #66 live-set diet
            # re-slims a wallpaper cart after this compile, so cart["images"] is
            # gone by the time a transport asks (sakura's backdrop shipped as an
            # imgref for a picture /assets never carried -- owner, 2026-07-31).
            # The namespace already references the same blobs, so this adds a
            # dict of references, not a copy of the pixels.
            self._wp_images = dict(cart.get("images") or {})
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
        if art is not None and art.owns_wallpaper(ws.wallpaper_id):
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
        wp = ws.wallpaper_id or "fill:dark_blue"
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

    def cart_images(self):
        """The WALLPAPER cart's own raw images ({name: blob}) -- what its
        `image("bg")` draws reference. The transports merge these into /assets
        beside the open cart's `ws.images`, which they are NOT part of: the
        wallpaper runs in its own namespace, so a recording tier that ships the
        wallpaper's DRAW COMMANDS (rather than a composited framebuffer) would
        otherwise emit an imgref for a picture the page never received --
        sakura's backdrop went missing exactly that way (owner, 2026-07-31)."""
        return dict(getattr(self, "_wp_images", None) or {})

    def wire_assets(self):
        """{name: (w, h, index_bytes)} for the backdrop composite's ship-once
        wire image, or {} while the frame is churning / never composited --
        merged into /assets beside the shelf covers (Workstation.cover_assets)."""
        if self._wire_name is None or self._wire_pix is None:
            return {}
        w, h = self._wire_wh
        return {self._wire_name: (w, h, self._wire_pix)}

    def draw_preview(self, cv, rect, dt):
        """The Appearance monitor's screen: the chosen wallpaper FIT inside
        `rect` -- the FULL frame, vs draw()'s full-bleed cover-crop. Same
        guarded degradation ladder as draw(): My Art direct -> cart frame ->
        solid fill.

        A cart wallpaper shows its COMPUTED preview: rendered once per cart
        source through the preview runner (the cart compiled a second time over
        an offscreen pure-Python Canvas -- never the game canvas) and cached
        like a thumbnail (RAM memo + the thumbs/wp*.mct sidecar). A STILL, so
        the appearance screen closes the redraw gate like any static UI.

        HOST AND WEB ONLY, and this docstring used to claim otherwise -- "one
        identical behavior on every tier (host, web, both boards)" -- which was
        never true for a single build. runtime/palette.py builds indices 16-63
        with CPython's `colorsys` at import time and MicroPython has none, so on
        a board `import palette` raised, `import canvas` raised with it, and
        _ensure_preview returned False every time; the panel below has always
        been the bare black fill. Neither board stages those two files any more
        (2026-08-15, owner call: tapping a wallpaper applies it, so a second
        raster in frozen flash bought a preview nobody ever saw), which makes
        the degradation deliberate instead of accidental.

        That gate is now the OFFSCREEN CANVAS FACTORY rather than the raster:
        `runtime/canvas.py` is gone and the preview renders on the same
        `DeviceCanvas` the boards run, so nothing about the pixels stops a board
        doing this -- only `_ensure_preview`'s import, which finds neither
        host_canvas nor web_canvas there. Re-enabling it is a product decision
        (a 320x240 RGB565 scratch surface plus a whole cart run in interpreted
        Python), not a missing file."""
        NAMES = self._NAMES
        ws = self.ws
        x, y, w, h = rect
        wp = ws.wallpaper_id or ""
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
        selection; clear() invalidates). False when there's no cart, no offscreen
        canvas factory (a BOARD build -- see draw_preview), or the compile fails.

        The factory is imported rather than injected because its ABSENCE is the
        tier gate: `host_canvas` exists only in the host package, `web_canvas`
        only in the wasm head's staged tree, and a board has neither -- which is
        exactly the set of tiers this preview has ever run on. (It used to gate
        on `import canvas`, the deleted second raster; the surface below is a
        565 `DeviceCanvas` on all three, reduced back to indices in
        _render_static.)"""
        cart = self._wp_cart
        if cart is None:
            return False
        if self._pv_draw is not None and self._pv_for is cart:
            return True
        try:
            from runtime.host_canvas import make_canvas
        except ImportError:
            try:
                from web_canvas import make_canvas
            except ImportError:
                return False            # a board: no offscreen canvas factory
        ws = self.ws
        try:
            pv = self._pv_canvas
            if pv is None:
                pv = make_canvas(ws.canvas.w, ws.canvas.h)
                self._pv_canvas = pv
            else:
                rl = getattr(pv, "reclaim_layers", None)
                if rl is not None:      # #63: return the last preview's layer loans
                    rl("wallpaper_pv")
            # #66 live-set diet: the slimmed cart rehydrates for this compile
            # (src/sheet bake into the preview ns), then re-slims -- the same
            # dance select_wallpaper does for the backdrop compile.
            ws._rehydrate_cart(cart)
            try:
                sheet = ws._build_sheet(cart)
                tilemap = ws._build_tilemap(cart)
                ns = ws.make_api(pv, ws.input, dict(cart.get("cfg", {})),
                                 sheet, _SilentAudio(AudioEngine(AudioBank.default())),
                                 tilemap, Pmem(), None, cart.get("images") or {},
                                 owner="wallpaper_pv")
                exec(compile(cart["src"], "<wallpaper-preview>", "exec"), ns)
            finally:
                if cart is not getattr(ws, "_fat_cart", None):
                    ws._reslim_cart(cart)
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
                ws._rehydrate_cart(cart)
                src = cart.get("src")
            finally:
                if cart is not getattr(ws, "_fat_cart", None):
                    ws._reslim_cart(cart)
        return cover_sig(src) if src else None

    def _static_preview(self, w, h):
        """The COMPUTED (thumbnail-model) preview of the current wallpaper
        cart, sized to fit (w, h): RAM memo -> wp sidecar -> one offscreen
        render (persisted). Returns a _Blit or None. General on every device:
        the render needs only the staged pure-Python Canvas, and the result is
        stamped against the cart's source so an edit recomputes it."""
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
            pix = self._preview_indices(pv)
            if pix is None:
                return None
            if (vw, vh) == (pv.w, pv.h):
                return bytes(pix)
            return self._sample(pix, pv.w, pv.h, vw, vh)
        except Exception as exc:  # noqa: BLE001 -- a broken cart has no preview
            print("Moybyte wallpaper preview error:", _err_text(exc))
            self._pv_ns = self._pv_update = self._pv_draw = None
            self._pv_for = None
            return None

    @staticmethod
    def _preview_indices(pv):
        """The preview frame as ONE PALETTE INDEX PER BYTE.

        This is the boundary the format conversion belongs at, and the reason it
        is a boundary at all: everything downstream of here is index-native and
        stays that way -- `_sample`, the `_Blit` it feeds, `spr()`, and the
        `thumbs/wp<w>x<h>.mct` sidecar, whose validator checks `len == w * h`
        (moy_image.load_wallpaper_preview). Converting THOSE to 565 would change
        a persisted on-disk format for nothing.

        The reduction is EXACT -- MOY64's 64 entries resolve to 64 distinct 565
        words -- but it runs `strict=False`: a wallpaper is arbitrary cart code,
        and a single unmappable word (a cart that blits raw 565 in) should cost
        that pixel, not the whole preview."""
        buf = getattr(pv, "_buf", None)
        if buf is None:                          # an indexed canvas, if one ever returns
            return bytes(pv.buf)
        try:
            from device_canvas import to_indices
        except ImportError:                      # pragma: no cover
            return None
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
        a player WINDOW in windowed mode.) On a RECORDING system canvas (the web
        console) there is no framebuffer to copy into: ship the frame as ONE scaled
        self-contained b64 img instead (the replayers clip the crop)."""
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
            pix = bytes(gbuf)
            if pix == self._wire_pix:
                # Stable frame (a static wallpaper): mint/keep the serial name
                # so the pixels ride /assets once (wire_assets) and this draw
                # records as one small imgref (#113).
                if self._wire_name is None:
                    self._wire_serial += 1
                    self._wire_name = "wall:%d" % self._wire_serial
                    self._wire_wh = (gw, gh)
            else:
                # Changed frame (first draw / a live wallpaper): inline it and
                # remember the pixels -- naming a churning frame would refetch
                # the whole asset set every frame.
                self._wire_pix = pix
                self._wire_name = None
            img = _Blit(gw, gh, pix, -1)
            img._paint = True              # -> the compact b64 wire form (~2.4x lighter)
            if self._wire_name is not None:
                img._name = self._wire_name
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
