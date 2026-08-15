"""Moybyte T-Deck (mainline) boot shell: mode flags + main().

STAGE 1 -- panel only. RUN_PANEL_SMOKE is the default because the shared console
is not staged into this build yet; there is nothing else to run. The smoke is
SELF-TERMINATING (it returns to the REPL rather than taking the loop over),
which matters more on this board than on the P4: the T-Deck's USB is native
(no CH343), and under the fork build a takeover loop left USB-CDC RX dead --
Ctrl-C never arrived. Whether mainline's CDC stack has the same hole is one of
the open questions this build answers, so stage 1 does not bet on it.

What "the panel works" means, in the order the smoke proves it:
  1. moy_lcd.init() returns          -> SPI2 came up, esp_lcd took the ST7789,
                                        the vendor init sequence was accepted
  2. the backlight lights            -> GPIO42 and the board power rail (GPIO10)
  3. colour bars appear, right way up-> MADCTL/rotation, byte order, stride
  4. the checker is square           -> no row shear (stride) and no band seam
                                        at y=48/96/144/192 (the flush banding)
  5. FLUSH us= prints                -> the completion fence ran; the number is
                                        the real per-frame panel cost
"""

RUN_PANEL_SMOKE = True
RUN_DESKTOP = False        # stage 6: the shared console, once it is staged


def main():
    print("Moybyte T-Deck (mainline) shell starting")
    if RUN_PANEL_SMOKE:
        try:
            panel_smoke()
        except Exception as exc:           # noqa: BLE001 -- say what broke, keep the REPL
            print("Moybyte panel smoke FAILED:", exc)
        return
    if RUN_DESKTOP:
        try:
            from moy_runtime import run_desktop
            run_desktop()
        except KeyboardInterrupt:
            print("Moybyte desktop interrupted -> REPL")
        return
    print("Moybyte T-Deck: no boot mode selected (REPL)")


def panel_smoke(frames=6):
    """Bring the panel up and paint a test pattern. Returns to the REPL."""
    import time
    import moy_lcd
    from tdeck_panel import TDeckCompositor

    print("Moybyte panel: init")
    comp = TDeckCompositor(nfbs=2)
    w, h = comp.size()
    print("Moybyte panel: %dx%d nfbs=%d madctl=0x%02x gfx=%s"
          % (w, h, moy_lcd.nfbs(), moy_lcd.madctl(), comp.has_gfx()))

    # Paint the pattern into EVERY framebuffer before lighting the backlight, so
    # the ping-pong can never present a buffer of power-on noise.
    for i in range(moy_lcd.nfbs()):
        moy_lcd.bars(i)
    for _ in range(moy_lcd.nfbs()):
        comp.flush()
    comp.set_backlight(True)
    print("Moybyte panel: backlight ON -- expect 8 colour bars over a checker")

    # Re-flush a few times so `us=` is a steady-state number rather than the
    # first-transfer outlier, and so a tear/flicker would be visible.
    for _ in range(frames):
        comp.flush()
        time.sleep_ms(120)
    n, us = comp.stats()
    print("Moybyte panel: flushes=%d last=%dus (%.1f fps ceiling)"
          % (n, us, 1000000.0 / us if us else 0.0))
    print("Moybyte panel smoke done -> REPL "
          "(moy_lcd.set_madctl(0x28|0x68|0xA8|0xE8) if the image is turned)")
