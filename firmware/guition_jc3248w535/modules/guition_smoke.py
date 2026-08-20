"""Guition JC3248W535 bring-up smokes -- self-terminating, one question each.

Stage 1 `panel()`: does an AXS15231B come up over raw QSPI with our init table
and our banded bounce flush. Stage 2 `touch()`: does the AXS15231's I2C half
report, and do the mapping knobs put a tap where the finger is. Both return to
the REPL; both are driven from moybyte_shell.MODE or by hand:

    import guition_smoke; guition_smoke.touch()
"""

import time

from guition_panel import GuitionCompositor
import moy_axs


def panel(frames=6):
    """Bring the panel up and paint a test pattern in C. Returns to the REPL.

    Deliberately draws through `moy_axs.bars()` -- no moy_gfx, no palette, no
    Python raster in the picture, so this answers ONE question. In order:
      1. moy_axs.init() returns     -> the QSPI bus came up and the panel
                                       accepted the ESPHome-provenance init
      2. the backlight lights       -> GPIO1
      3. bars appear, right way up  -> portrait orientation, byte order, stride
      4. the checker is square      -> no row shear, no band seam at the
                                       y=48/96/... bounce-band boundaries
      5. FLUSH us= prints           -> the completion ISR ran; the number is
                                       the real per-frame panel cost (~15.4ms
                                       expected at 40MHz x 4 lines)
    """
    print("Moybyte panel: init")
    comp = GuitionCompositor(nfbs=2)
    w, h = comp.size()
    print("Moybyte panel: %dx%d nfbs=%d gfx=%s"
          % (w, h, moy_axs.nfbs(), comp.has_gfx()))

    for i in range(moy_axs.nfbs()):
        moy_axs.bars(i)
    for _ in range(moy_axs.nfbs()):
        comp.flush()
    # flush() returns with the frame still going out (the overlap); fence
    # before lighting so power-on GRAM noise is never lit.
    comp.sync()
    comp.set_backlight(True)
    print("Moybyte panel: backlight ON -- expect 8 colour bars over a checker")

    for _ in range(frames):
        comp.flush()
        time.sleep_ms(120)
    comp.sync()
    n, us = comp.stats()
    print("Moybyte panel: flushes=%d last=%dus (%.1f fps ceiling)"
          % (n, us, 1000000.0 / us if us else 0.0))
    print("Moybyte panel: pump %s" % (comp.bounce_stats(),))
    print("Moybyte panel smoke done -> REPL "
          "(moy_axs.cmd(0x21) if colors look inverted; "
          "moy_axs.set_madctl(0x40|0x80|0xC0) if mirrored)")


_TOUCH_MARGIN = 30
_TOUCH_BOX = 24


def touch(secs=60):
    """Corner + centre targets on the glass; every sample printed raw + mapped
    + the live knob state. Tap the targets; when mapped == tapped everywhere,
    bake the knob values into device/axs_touch.py with the date. Self-
    terminating after `secs` (and Ctrl-C works -- the REPL is alive here)."""
    import axs_touch
    from axs_touch import Touch

    comp = GuitionCompositor(nfbs=2)
    gfx = comp.gfx()
    w, h = comp.size()
    touch = Touch(w, h)
    print("Moybyte touch: available=%s addr=0x%02x knobs swap=%s fx=%s fy=%s"
          % (touch.available, touch.addr, axs_touch.SWAP_XY,
             axs_touch.FLIP_X, axs_touch.FLIP_Y))

    m, b = _TOUCH_MARGIN, _TOUCH_BOX
    targets = ((m, m), (w - m, m), (m, h - m), (w - m, h - m), (w // 2, h // 2))

    def _paint(mark=None):
        for i in range(len(comp._fbs)):
            fb = comp._fbs[i]
            if gfx is not None:
                gfx.fill(fb, w * h, 0)
        if gfx is not None:
            fb = comp.framebuffer()
            for (cx, cy) in targets:
                # PAL565_SW yellow-ish: raw wire 565 -- use white (0xFFFF).
                gfx.fill_rect(fb, w, cx - b // 2, cy - b // 2, b, b, 0xFFFF)
            if mark is not None:
                # 0x00F8 = red in WIRE order (the fb stores byte-swapped 565;
                # 0xFFFF/0x0000 above are swap-invariant and need no care).
                gfx.fill_rect(fb, w, max(0, mark[0] - 5), max(0, mark[1] - 5),
                              11, 11, 0x00F8)
        comp.flush()

    _paint()
    comp.sync()
    comp.set_backlight(True)
    print("Moybyte touch: tap the 5 boxes, watch serial (%ds)" % secs)
    end = time.ticks_add(time.ticks_ms(), secs * 1000)
    last = 0
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        tp = touch.poll()
        now = time.ticks_ms()
        if tp is not None and (tp[2] or time.ticks_diff(now, last) > 250):
            last = now
            raw = touch.raw or (-1, -1)
            print("TAP%s mapped=(%d,%d) raw=(%d,%d) swap=%s fx=%s fy=%s"
                  % ("*" if tp[2] else " ", tp[0], tp[1], raw[0], raw[1],
                     axs_touch.SWAP_XY, axs_touch.FLIP_X, axs_touch.FLIP_Y))
            if tp[2]:
                _paint(mark=(tp[0], tp[1]))
        time.sleep_ms(20)
    print("Moybyte touch smoke done -> REPL")
