"""Guition P4 bring-up smokes, each SELF-TERMINATING (paints, prints a number,
returns to the REPL). From the REPL:

    import guition_p4_smoke as s; s.panel(); s.touch()
"""

import time


def panel(hold_s=4):
    """Stage 1: the DSI hardware bars (no framebuffer involved), then a
    framebuffer paint -- colour quadrants + a checker + a white border -- and
    the cost of a full-screen native fill. Separates "panel path broken" from
    "framebuffer path broken" from "console broken"."""
    import moy_dsi
    from guition_p4_display import P4Compositor, set_backlight

    comp = P4Compositor()
    w, h = comp.size()
    print("Moybyte Guition P4 panel smoke: %s %dx%d, %d framebuffers, underruns=%s"
          % (moy_dsi.PANEL, w, h, moy_dsi.nfbs(), comp.underruns()))
    set_backlight(True)
    moy_dsi.set_pattern(1)
    time.sleep(hold_s)
    moy_dsi.set_pattern(2)
    time.sleep(hold_s)
    moy_dsi.set_pattern(0)
    gfx = comp.gfx()
    if gfx is None:
        print("no moy_gfx -- framebuffer paint skipped")
        return
    fb = comp.framebuffer()
    t0 = time.ticks_us()
    gfx.fill(fb, w * h, 0x0000)
    fill_us = time.ticks_diff(time.ticks_us(), t0)
    # RGB565 quadrants: red TL, green TR, blue BL, yellow BR (byte order and
    # mirror instantly readable on glass).
    gfx.fill_rect(fb, w, 0, 0, w // 2, h // 2, 0xF800)
    gfx.fill_rect(fb, w, w // 2, 0, w // 2, h // 2, 0x07E0)
    gfx.fill_rect(fb, w, 0, h // 2, w // 2, h // 2, 0x001F)
    gfx.fill_rect(fb, w, w // 2, h // 2, w // 2, h // 2, 0xFFE0)
    for y in range(0, 160, 20):
        for x in range(0, 160, 20):
            col = 0xFFFF if ((x + y) // 20) & 1 else 0x0000
            gfx.fill_rect(fb, w, w // 2 - 80 + x, h // 2 - 80 + y, 20, 20, col)
    gfx.fill_rect(fb, w, 0, 0, w, 8, 0xFFFF)
    gfx.fill_rect(fb, w, 0, h - 8, w, 8, 0xFFFF)
    gfx.fill_rect(fb, w, 0, 0, 8, h, 0xFFFF)
    gfx.fill_rect(fb, w, w - 8, 0, 8, h, 0xFFFF)
    # A small black square in the TOP-LEFT corner of the image: which physical
    # corner it lands in IS the orientation answer.
    gfx.fill_rect(fb, w, 16, 16, 64, 64, 0x0000)
    t0 = time.ticks_us()
    comp.flush()
    show_us = time.ticks_diff(time.ticks_us(), t0)
    time.sleep(hold_s)
    print("Moybyte Guition P4 panel smoke done: fill=%dus show=%dus underruns=%s -> REPL"
          % (fill_us, show_us, comp.underruns()))


def touch(seconds=15):
    """Stage 2: bring the GSL3680 up (firmware upload) and stream every sample
    for `seconds` -- raw + mapped + the knob state -- so a tap on each corner
    calibrates the flips."""
    from guition_p4_input import Touch
    import guition_p4_input as knobs

    t0 = time.ticks_ms()
    tp = Touch(progress=lambda k, n: print("  fw %d/%d" % (k, n)))
    print("Moybyte Guition P4 touch smoke: available=%s (init %dms) swap=%s flip_x=%s flip_y=%s"
          % (tp.available, time.ticks_diff(time.ticks_ms(), t0),
             knobs.SWAP_XY, knobs.FLIP_X, knobs.FLIP_Y))
    if not tp.available:
        return
    last = None
    end = time.ticks_add(time.ticks_ms(), seconds * 1000)
    n = 0
    while time.ticks_diff(end, time.ticks_ms()) > 0:
        p = tp.poll()
        if p is not None and (p[2] or p[:2] != last):
            last = p[:2]
            n += 1
            print("TAP%s mapped=(%d,%d) raw=%s fingers=%d"
                  % ("*" if p[2] else " ", p[0], p[1], tp.raw, tp.fingers))
        time.sleep_ms(20)
    print("Moybyte Guition P4 touch smoke done: %d samples -> REPL" % n)
