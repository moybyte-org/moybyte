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
    from widgets import Pmem, _SilentAudio
except ImportError:  # pragma: no cover - host fallback when not yet aliased
    from runtime.widgets import Pmem, _SilentAudio


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
                             tilemap, Pmem(), None, cart.get("images") or {})
            exec(compile(cart["src"], "<wallpaper>", "exec"), ns)
            if ns.get("_init"):
                ns["_init"]()
            self._wp_ns = ns
            self._wp_cart = cart
            self._wp_update = ns.get("_update")
            self._wp_draw = ns.get("_draw")
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
        if self._wp_draw is not None:
            try:
                if self._wp_live and self._wp_update is not None and dt > 0:
                    self._wp_update(dt)
                sh = ws.layout.status_h
                safe = sh if ws.screen in ("launcher", "settings") else 0
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
                return
            except Exception as exc:  # noqa: BLE001 -- drop a broken wallpaper to the fill
                print("Moybyte wallpaper draw error:", _err_text(exc))
                ws._reset_canvas_state()
                self._wp_ns = self._wp_update = self._wp_draw = None
        # Solid fill fallback (also the "fill:<color>" built-ins).
        wp = ws.wallpaper_id or "fill:dark_blue"
        name = wp[5:] if isinstance(wp, str) and wp.startswith("fill:") else "dark_blue"
        ws.canvas.cls(NAMES.get(name, NAMES["dark_blue"]))
