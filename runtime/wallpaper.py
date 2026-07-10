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
        self._wp_live = True

    def clear(self):
        """Drop the compiled wallpaper (back to a solid fill). Called by
        ws.select_wallpaper before it (re)compiles the new choice."""
        self._wp_ns = self._wp_update = self._wp_draw = None
        self._wp_cart = None
        self._wp_restore_bg = None
        # #63 leak fix: return the dead wallpaper's pooled layer buffers for reuse.
        rl = getattr(self.ws.canvas, "reclaim_layers", None)
        if rl is not None:
            rl("wallpaper")

    def compile(self, cart):
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
                self._wp_draw()
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
            img = _Blit(gw, gh, bytes(gbuf), -1)
            img._paint = True              # -> the compact b64 wire form (~2.4x lighter)
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
