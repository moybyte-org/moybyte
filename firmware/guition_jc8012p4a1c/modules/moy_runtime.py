"""Moybyte Guition P4 device backend: the shared console on the Guition
JC8012P4A1C (ESP32-P4 + C6, 10.1" 800x1280 MIPI-DSI, GSL3680 touch).

The Waveshare 7B's backend (#58) on the second ESP32-P4 board, and the port
that moved the P4 silicon tier out of that board's tree (2026-09-06): the DSI
compositor (`device/dsi_panel.py`), the SYSTEM canvas with its PPA composite
hooks (`device/p4_canvas.py`) and the four C modules (`native/p4/`) are TAKEN,
so what this file owns is exactly what this glass decides --

  * the SYSTEM canvas is 1280x800 LANDSCAPE on portrait-native glass (owner
    call 2026-09-06). The P4's DSI scans the framebuffer continuously, so the
    rotation is the compositor's: `device/dsi_panel.RotatedCompositor` paints
    a persistent landscape buffer and rotates it onto the panel with the PPA
    -- the whole frame when chrome painted, one rect on a quiet game frame.
    Its header carries the design; the README the measured costs.
  * the touch driver is the GSL3680 (`guition_p4_input.py` over the shared
    `device/gsl3680.py`, firmware upload at boot) instead of a GT911.
  * the backlight is GPIO23 active-high (`guition_p4_display.py`).

Everything else -- the 320x240 GAME canvas, the windowed WM, the BLE keyboard
over the C6, OTA on the internal VFS, the dev channel, the frame loop -- is the
Waveshare's, by import.

Carts live on the INTERNAL flash VFS (~7.9MB of the 16MB chip); the TF slot is
wired like the Waveshare's (SDMMC slot 0 on LDO4) and equally unused.
"""

# The seed roster, generated from system_carts/ at build time and PACKED: one
# raw-deflate blob per cart, inflated ONE AT A TIME by `moy_carts.seed_any`.
from carts_data import CARTS_Z as CARTS

GAME_W, GAME_H = 320, 240
FONT_SCALE = 1                     # 1x, the Waveshare's call carried over: this
                                   # 10.1" 800x1280 is ~150 PPI against the 7"
                                   # 1024x600's ~170 -- the same legibility
                                   # class; Settings FONT SIZE persists overrides
PANEL_DIAGONAL_IN = 10.1       # the glass, in inches -- board.toml [panel] is the
                               # authority and tests/test_board_toml.py pins the
                               # two together. It buys the #203 chrome tap-target
                               # floor: at ~150 PPI a 16px bar icon is 2.7mm, so
                               # interactive geometry lays out at scale 2 while
                               # every glyph stays at FONT_SCALE (owner, from the
                               # desk, 2026-09-06: "too tiny", not "too small to read").
# Internal-flash store root. NOT "/moybyte/..." -- a root-level dir named like an
# importable module SHADOWS the frozen module of that name (the Waveshare's
# hardware-learned rule, 2026-07-08; same MicroPython, same rule).
CARTS_ROOT = "/moy/carts"
# Where an OTA image stages (#53): the internal VFS, NOT under /moy/carts
# (the store scans that directory). ~4MB free after the seed against a ~3.6MB
# image on this 16MB chip -- the README carries the headroom.
OTA_UPDATE_DIR = "/moy/update"


def run_touch_calibrate():
    """Touch calibration aid: corner + center targets on the glass, every
    GSL3680 sample printed to serial as raw + mapped coords + the live knob
    state. Run it from the REPL (Ctrl-C out of the desktop first):

        import moy_runtime; moy_runtime.run_touch_calibrate()

    Tap each numbered box; read which box the MAPPED coords land in. The knobs
    are guition_p4_input's module globals, read when Touch() is constructed --
    Ctrl-C, `import guition_p4_input as k; k.FLIP_X = True` (etc.), re-run, and
    once mapped == tapped everywhere, bake the winners into that file. The
    body is `device/p4_desktop.run_touch_calibrate`, shared with the
    Waveshare."""
    from guition_p4_display import P4Compositor, set_backlight
    from guition_p4_input import Touch
    from p4_desktop import run_touch_calibrate as _calibrate

    _calibrate("Moybyte Guition P4", P4Compositor, set_backlight, Touch,
               lambda touch: "swap=%s flip_x=%s flip_y=%s"
               % (touch.swap_xy, touch.flip_x, touch.flip_y))


def run_ppa_smoke(scale=2, iters=60):
    """A/B the PPA vs the CPU composite on this board's glass -- the body is
    device/p4_canvas.run_ppa_smoke (one copy for both P4 boards); this hands
    it the compositor and the backlight. Ctrl-C the desktop first, then:

        import moy_runtime; moy_runtime.run_ppa_smoke()
    """
    from guition_p4_display import P4Compositor, set_backlight
    from p4_canvas import run_ppa_smoke as _smoke
    _smoke(P4Compositor(), set_backlight, scale=scale, iters=iters,
           game_w=GAME_W, game_h=GAME_H)


# Idle screen blank (#58): milliseconds of NO INPUT before the panel goes dark,
# so the board can sit plugged in for days without a lit screen. 0 disables it.
# Overridable before boot (`import moy_runtime; moy_runtime.POWER_SAVE_MS = ...`)
# and at runtime over the serial `power` command.
#
# This is a DARK SCREEN, not a suspend, and deliberately so: the loop keeps
# running, a cart mid-run keeps ticking, and the serial dev channel stays live,
# because the on-glass harness (#156) has to reach a board that has been idle
# for hours.
POWER_SAVE_MS = 300000          # 5 minutes


def run_desktop(fps_cap=60):
    """Boot the shared console on the Guition P4: launcher-as-desktop under
    WindowedWM, 1280x800 landscape rotated onto the portrait glass, GSL3680
    touch as the pointer, a BLE HID keyboard over the companion C6, and carts
    on internal flash. Ctrl-C over the USB-Serial/JTAG REPL interrupts the
    loop.

    The BODY is `device/p4_desktop.py`, shared with the Waveshare (2026-09-09):
    two P4 boards running the same console over the same silicon tier had two
    copies of it, differing in fifty lines of which all but five were this
    board's name in a print string. What is left here is what this glass
    decides -- its compositor, its touch, its constants."""
    from guition_p4_display import P4Compositor, set_backlight
    from guition_p4_input import Touch
    from p4_desktop import run_desktop as _run_desktop

    return _run_desktop(
        name="Moybyte Guition P4", link_id="guition_p4",
        compositor=P4Compositor, set_backlight=set_backlight, touch_cls=Touch,
        font_scale=FONT_SCALE, panel_diagonal_in=PANEL_DIAGONAL_IN, seed_carts=CARTS,
        store_root=CARTS_ROOT, ota_dir=OTA_UPDATE_DIR,
        power_save_ms=POWER_SAVE_MS, game_wh=(GAME_W, GAME_H), fps_cap=fps_cap)
