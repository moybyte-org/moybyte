"""The on-screen perf HUD's *rendering* layer (#43/#44), extracted from
Workstation (runtime/console.py).

Two layers, the same split as the console's other sub-UIs:
  * The perf *query* API -- perf_sample / perf_breakdown / perf_breakdown_raw /
    perf_backdrop / perf_pointer / perf_batch -- is the PerfMeters mixin's
    (runtime/console_perf.py). Those are the device backend's measurement
    contract (device_diag calls `ws.perf_sample()` / `ws.perf_breakdown()`
    every few seconds for its PERF/DRAWBRK diag lines). They read the
    frame-timing fields the frame loop writes.
  * PerfHud (here) -- the tiny bottom-right FPS chip + optional frame-time
    breakdown drawn over it, plus the tap target that toggles the HUD. Pure
    read-only consumer of the owning Workstation's timing fields; it draws, it
    never measures.

Dependency profile (the facade lens, shell_architecture_v1.md §2) -- PerfHud is
the console's *cleanest* seam: it touches NO privileged verbs at all, only
read-only frame-timing state through its `self.ws` back-reference:
  * shared (non-privileged):   ws.canvas, ws.theme_colors, ws.perf_hud,
                               ws._fps, ws._flush_ms, ws._draw_ms, ws._upd_ms
`NAMES` is injected at construction (same reason as BlockEditorUI: importing it
back from console.py would be a circular import, since console.py builds the one
PerfHud a Workstation holds). It draws through native/moy_ui's `moy_ui_perf`
(the one body every tier draws), host == device.
"""


try:
    from array import array as _array
    import ui_draw as _ui_draw
except ImportError:  # pragma: no cover - host fallback
    from array import array as _array
    from runtime import ui_draw as _ui_draw

_RIGHT, _BOTTOM, _FPS, _FRAME, _DETAIL, _FS, _DRAW = (
    _ui_draw.PERF_RIGHT, _ui_draw.PERF_BOTTOM, _ui_draw.PERF_FPS, _ui_draw.PERF_FRAME,
    _ui_draw.PERF_DETAIL, _ui_draw.PERF_FS, _ui_draw.PERF_DRAW)
_PARTS = _ui_draw.PERF_PARTS

# The HUD's words, NUL-separated (moy_ui_perf's order), made once: a frame
# that paints the HUD formats no string.
_WORDS = "Frame\0Draw\0Flush\0Logic\0Idle\0fps\0ms"


class PerfHud:
    def __init__(self, ws, names):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring).
        self._NAMES = names
        self._args = _array("h", [0] * 16)        # ui_draw.perf's slots
        self._box = _array("i", [0, 0, 0, 0])

    def draw(self):
        """The FPS chip at the canvas's bottom right (GAPS 54) -- a flat pill
        in the surface's polarity, its number in the code face's digits -- and
        with the breakdown on (`ws.perf_hud`) its card over it: the frame
        against 33 ms, Draw, Flush and Logic each a hue of its own and their
        rows in ms. Drawn on the game canvas every game frame, in flat fills
        and allocating nothing; its rect is patched under it first."""
        ws = self.ws
        cv = ws.canvas
        a = self._args
        fps = int(ws._fps + 0.5)
        fps = fps if fps > 0 else 0
        logic = int(ws._upd_ms * 10 + 0.5)
        flush = int(ws._flush_ms * 10 + 0.5)
        draw = int(ws._draw_ms * 10 + 0.5) - logic
        draw = draw if draw > 0 else 0
        frame = (10000 + fps // 2) // fps if fps else draw + flush + logic
        idle = frame - draw - flush - logic
        a[_RIGHT] = cv.w - 4
        a[_BOTTOM] = cv.h - 4
        a[_FPS] = fps
        a[_FRAME] = frame
        a[_DETAIL] = 1 if ws.perf_hud else 0
        a[_FS] = 1
        a[_DRAW] = 0
        a[_PARTS] = draw
        a[_PARTS + 1] = flush
        a[_PARTS + 2] = logic
        a[_PARTS + 3] = idle if idle > 0 else 0
        th = ws.theme_colors
        b = self._box
        _ui_draw.perf(cv, th, a, _WORDS, b)
        ws.patch_cart_frame(b[0], b[1], b[2], b[3])
        a[_DRAW] = 1
        was = _ui_draw.cart_text(False)
        try:
            _ui_draw.perf(cv, th, a, _WORDS, b)
        finally:
            _ui_draw.cart_text(was)

    def _fps_tap_rect(self):
        """The bottom-right corner the FPS readout lives in, used as the tap target
        that toggles the perf HUD (#43/#44). Generous (a fixed corner box, not just
        the chip) so a finger on the device touchscreen lands it; it sits over the
        FPS chip in GAME-canvas coords (the desktop hit-tests in game space). Kept
        off the cart's own top-bar tools, so a kid never trips it by accident --
        they'd have to deliberately poke the FPS number."""
        cv = self.ws.canvas
        w, h = 60, 24
        return (cv.w - w, cv.h - h, w, h)
