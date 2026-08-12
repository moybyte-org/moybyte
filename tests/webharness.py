"""A minimal console-over-CommandCanvas harness for tests.

Distilled from tools/web_console.py's WebConsole when that transport died in
the 2026-08 streaming sunset (docs/moycore_plan_2026-08.md 3.2). Several suites
used WebConsole not as a web server but as the convenient way to drive the
SHARED console over a recording canvas -- per-WM-surface slicing, the windowed
desktop over RecordingLayers, delta semantics. That construction is exactly
what the wasm head (firmware/web_runner/web_boot.py) still ships, so the tests
pin surviving behavior; this harness is the transport-free core: swap the
system canvas for a CommandCanvas, step frames through the ConsoleDriver, and
serve the recorded streams through the shared ServedState.
"""

import os
import shutil

from runtime import host_app
from runtime import web_view


class _DriverPointerSink:
    """Adapt web_view.apply_events' pointer verbs onto the ConsoleDriver."""

    def __init__(self, driver):
        self._d = driver

    def place(self, x, y):
        self._d.place(x, y)

    def press(self, x, y):
        self._d.press_at(x, y)

    def release(self):
        self._d.release()

    def tap(self, x, y):
        self._d.tap(x, y)


class WebHarness:
    """The console with its SYSTEM canvas swapped for a recording CommandCanvas
    (surfaces_on, like the wasm head), stepped one frame at a time. Drop-in for
    the old WebConsole's test-visible surface: step_frame() returns
    (served_cmds_or_None, cart_title, audio_b64) and ._last_surfaces holds the
    frame's per-surface streams (None when the redraw was skipped)."""

    def __init__(self, save_dir, fps=30, cart=None, sys_size=None,
                 font_scale=1, windowed=False):
        self.dt = 1.0 / max(1, fps)
        self.ws = host_app.build_workstation(save_dir, sys_size=sys_size,
                                             font_scale=font_scale)
        sw, sh = (self.ws.sys_canvas.w, self.ws.sys_canvas.h)
        self.canvas = web_view.CommandCanvas(
            sw, sh, font_scale=self.ws._effective_font_scale())
        self.canvas._rec.surfaces_on = True
        self._last_surfaces = None
        self._served = web_view.ServedState(self.canvas._rec)
        if self.ws._sys_canvas is None:
            self.canvas.set_font_scale(1)
            self.ws.canvas = self.canvas
        else:
            self.ws._sys_canvas = self.canvas
            self.ws._relayout()
        if getattr(self.ws, "wallpaper_id", None):
            self.ws.select_wallpaper(self.ws.wallpaper_id, persist=False)
        if windowed and self.ws._sys_canvas is not None:
            from runtime.wm_windowed import WindowedWM
            self.ws.wm = WindowedWM(self.ws)
            self.ws.open_desk()
        if cart:
            self._open_named_cart(cart, save_dir)
        self.driver = host_app.ConsoleDriver(self.ws)
        self._pointer_sink = _DriverPointerSink(self.driver)

    def _open_named_cart(self, cart_path, carts_dir):
        name = os.path.basename(os.path.normpath(cart_path))
        dst = os.path.join(carts_dir, name)
        if os.path.abspath(cart_path) != os.path.abspath(dst) \
                and not os.path.exists(dst):
            shutil.copytree(cart_path, dst)
        self.ws.launcher.items = host_app.moy_carts.scan(self.ws.carts_root)
        for i, c in enumerate(self.ws.launcher.items):
            if os.path.abspath(c["path"]) == os.path.abspath(dst):
                self.ws.launcher.sel = i
                break
        self.ws.open()

    def _cart_title(self):
        cart = getattr(self.ws, "cart", None)
        return None if cart is None else cart.get("title")

    def assets(self):
        """The shared /assets payload the page-shape consumers read (w/h,
        palette, sheet/tilemap, input hint) -- built by the ONE shared builder,
        exactly as the wasm head does. Also arms ws._dirty (a fresh page needs
        one full keyframe), matching the old transport's behavior."""
        from runtime import palette
        self._served.reset()
        self.ws._dirty = True
        decoded = {}
        raw = dict(getattr(self.ws, "images", None) or {})
        wc = getattr(self.ws.wallpaper, "cart_images", None)
        if wc is not None:
            for n, b in (wc() or {}).items():
                raw.setdefault(n, b)
        for name, blob in raw.items():
            dec = host_app._decode_moyimg(blob)
            if dec is not None:
                decoded[name] = dec
        pb = getattr(self.ws, "prebuild_covers", None)
        if pb is not None:
            pb()
        decoded.update(self.ws.cover_assets())
        return web_view.assets_payload(
            self.canvas.w, self.canvas.h,
            getattr(self.canvas, "palette", None) or palette.MOY64,
            getattr(self.ws, "sheet", None), getattr(self.ws, "tilemap", None),
            self._cart_title(), images=decoded or None,
            input_kinds=web_view.effective_input_kinds(self.ws))

    def apply_events(self, events):
        d = self.driver
        web_view.apply_events(
            events, d.input, self._pointer_sink,
            on_press=d.press, on_pan=d.pan, on_key=d.type_char,
            on_esc=d.escape, on_hold=d.hold, on_key_hold=d.key_hold)

    def step_frame(self):
        self.canvas.take_commands()      # drop anything stale (defensive)
        self.driver.frame(self.dt)
        flat = self.canvas.take_commands()
        cart = self._cart_title()
        if not flat:                     # redraw skipped (#44): nothing recorded
            self._last_surfaces = None
            return None, cart, ""
        surfaces = self.canvas.take_surfaces()
        if surfaces is not None:
            cmds, self._last_surfaces = self._served.served_surfaces(flat, surfaces)
        else:
            cmds = self._served.served_frame(flat)
            self._last_surfaces = None
        return cmds, cart, ""
