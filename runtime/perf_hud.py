"""The on-screen perf HUD's *rendering* layer (#43/#44), extracted from
Workstation (runtime/console.py).

Two layers, the same split as the console's other sub-UIs:
  * The perf *query* API -- Workstation.perf_sample / perf_breakdown /
    perf_breakdown_raw / perf_chrome / perf_batch -- STAYS on Workstation.
    Those are the device backend's measurement contract (moy_runtime.run_desktop
    calls `ws.perf_sample()` / `ws.perf_breakdown()` every few seconds for its
    PERF/DRAWBRK diag lines, and tests pin `def perf_sample(self):` to the
    console module). They read the frame-timing fields the frame loop writes.
  * PerfHud (here) -- the tiny bottom-right FPS chip + optional frame-time
    breakdown drawn over it, plus the tap target that toggles the HUD. Pure
    read-only consumer of the owning Workstation's timing fields; it draws, it
    never measures.

Dependency profile (the facade lens, shell_architecture_v1.md §2) -- PerfHud is
the console's *cleanest* seam: it touches NO privileged verbs at all, only
read-only frame-timing state through its `self.ws` back-reference:
  * shared (non-privileged):   ws.canvas, ws.screen, ws.cart, ws.cart_error,
                               ws._fps, ws._flush_ms, ws._draw_ms
`NAMES` is injected at construction (same reason as BlockEditorUI: importing it
back from console.py would be a circular import, since console.py builds the one
PerfHud a Workstation holds). Method bodies are kept byte-for-byte identical to
the pre-extraction Workstation versions -- each aliases `NAMES = self._NAMES`
and reads `cv = self.ws.canvas`, so the drawing is unchanged (host == device).
"""


class PerfHud:
    def __init__(self, ws, names):
        self.ws = ws
        # Injected instead of imported back from console.py (see module docstring).
        self._NAMES = names

    def _draw_fps(self):
        # Tiny FPS readout in the bottom-right, over a dark chip so it stays legible
        # on any cart. The desktop overlay buttons all sit along the top, so this
        # corner is free. Drawn with the indexed API only (host == device).
        NAMES = self._NAMES
        cv = self.ws.canvas
        s = "%d" % int(self.ws._fps + 0.5)
        tw = len(s) * 8
        x = cv.w - tw - 3
        y = cv.h - 10
        cv.rect(x - 2, y - 1, tw + 4, 10, NAMES["black"])
        cv.print(s, x, y, NAMES["yellow"], 1)

    def _fps_tap_rect(self):
        """The bottom-right corner the FPS readout lives in, used as the tap target
        that toggles the perf HUD (#43/#44). Generous (a fixed corner box, not just
        the few-pixel digit chip) so a finger on the device touchscreen lands it; it
        sits over the FPS chip in GAME-canvas coords (the desktop hit-tests in game
        space). Kept off the cart's own top-bar tools, so a kid never trips it by
        accident -- they'd have to deliberately poke the FPS number."""
        cv = self.ws.canvas
        w, h = 40, 14
        return (cv.w - w, cv.h - h, w, h)

    def _draw_perf_hud(self):
        """Frame-time breakdown (#43/#44 perf), drawn just above the FPS chip when
        perf_hud is on: "f<flush> d<draw> t<total>" in ms (total = flush + draw).
        flush is the panel DMA flush (comp.flush(); ~0 on the host's _NullComp, real
        only on device); draw is everything else (cart _update/_draw + console draw).
        Indexed API only (host == device); compact so it doesn't overlap the cart's
        HUD where avoidable."""
        NAMES = self._NAMES
        cv = self.ws.canvas
        f = int(self.ws._flush_ms + 0.5)
        d = int(self.ws._draw_ms + 0.5)
        s = "f%d d%d t%d" % (f, d, f + d)
        tw = len(s) * 8
        x = cv.w - tw - 3
        if x < 1:
            x = 1
        y = cv.h - 20            # one 8px row above the FPS chip (which sits at h-10)
        cv.rect(x - 2, y - 1, tw + 4, 10, NAMES["black"])
        cv.print(s, x, y, NAMES["white"], 1)
