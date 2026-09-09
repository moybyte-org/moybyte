"""Moybyte P4 device backend (#58): the shared console on the Waveshare 7B.

This is the P4 sibling of the T-Deck's moy_runtime, an order of magnitude
smaller because the board removed the walls the T-Deck backend exists to fight:
no flush ceiling (DPI scan-out), no SD<->display bus war (separate buses), no
keyboard mode-flipping (BLE HID has real make/break reports), no input-poller
thread (nothing stalls the loop).

The two-domain seam (#39) runs for real here for the first time on hardware:

  * `P4SystemCanvas` -- the SYSTEM canvas (device/p4_canvas.py since
    2026-09-06, one body for both P4 boards): a DeviceCanvas (RGB565 + native
    moy_gfx) drawing DIRECTLY into the 1024x600 DSI scan-out framebuffer, plus
    what a system surface must add over a game canvas: a settings-chosen
    font_scale, font-scale-carrying layers, and the two native composite hooks
    the shared presentation code probes for -- blit_game and blit_cover -- on
    the P4 PPA where it wins.
  * the GAME canvas stays a plain 320x240 DeviceCanvas over an off-screen
    buffer; carts + make_api (device_api, staged from the T-Deck tree) are
    byte-identical to the T-Deck's.
  * `run_desktop` constructs the shared Workstation with BOTH canvases and
    installs `WindowedWM` -- the launcher is the desktop, every app a floating
    window (#73's presentation tier, finally on its intended hardware).

Carts live on the INTERNAL flash VFS (31.5MB -- SD is optional on this board;
the SDIO slot + LDO4 power fix are a follow-up for removable-cart workflows).
"""

import time

# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_util import _ticks_ms, _ticks_diff
from p4_canvas import P4SystemCanvas

GAME_W, GAME_H = 320, 240
FONT_SCALE = 1                     # 1x everywhere (owner call, 2026-07-12): the 7"
                                   # 1024x600 fits CONTENT, not magnification --
                                   # geometry is resolution-driven; persisted
                                   # system.json still overrides (Settings FONT SIZE)
PANEL_DIAGONAL_IN = 7.0            # the glass, in inches -- board.toml [panel] is
                                   # the authority and tests/test_board_toml.py
                                   # pins the two together. It buys the #203 chrome
                                   # tap-target floor: at ~170 PPI a 16px bar icon
                                   # is 2.4mm, so interactive geometry lays out at
                                   # scale 2 while every glyph stays at FONT_SCALE.
# Internal-flash store root. NOT "/moybyte/..." -- a root-level dir named like an
# importable module SHADOWS the frozen module of that name ('' precedes '.frozen'
# on sys.path), and the first boot's seeded /moybyte dir broke the next boot's
# `from moybyte.input import ...` (hardware-learned 2026-07-08).
CARTS_ROOT = "/moy/carts"
# Where an OTA image stages (#53). This board has no SD -- the T-Deck's
# /sd/update has no meaning here -- so it lands on the internal VFS, which
# has ~23MB free against a ~3MB image. NOT under /moy/carts: the store
# scans that directory.
OTA_UPDATE_DIR = "/moy/update"


# Loading the carts is DeviceBoot.load_carts now (#161 Phase 4): the seed +
# scan + built-in fallback is the same on both boards, and what differs here is
# arguments -- the internal-flash root above, no storage SESSION at all (this
# console has no SD card and the store races nobody), and the word "flash" in
# the serial lines. On a full-erase boot that call is 17.5 of the 25 seconds
# before anything composes, and every second of it is seeding -- which is what
# the splash's progress bar is for.


def run_touch_calibrate():
    """Touch calibration aid (#58): corner + center targets on the glass, every
    GT911 sample printed to serial as raw + mapped coords + the live knob state.

    Run it from the REPL (Ctrl-C out of the desktop first):

        import moy_runtime; moy_runtime.run_touch_calibrate()

    Tap each numbered box; read which box the MAPPED coords land in. The knobs
    are live module globals -- Ctrl-C, `import p4_input; p4_input.FLIP_X = True`
    (etc.), re-run, and once mapped == tapped everywhere, bake the winners into
    p4_input.py. Ctrl-C exits (the REPL stays alive on this board)."""
    from p4_display import P4Compositor, set_backlight
    from p4_input import Touch
    import p4_input

    comp = P4Compositor()
    canvas = P4SystemCanvas(comp, font_scale=2)
    touch = Touch(canvas.w, canvas.h)
    w, h = canvas.w, canvas.h
    targets = ((60, 60, "1 TOP-LEFT"), (w - 61, 60, "2 TOP-RIGHT"),
               (60, h - 61, "3 BOT-LEFT"), (w - 61, h - 61, "4 BOT-RIGHT"),
               (w // 2, h // 2, "5 CENTER"))

    def _draw(msg, mx=-1, my=-1):
        canvas.cls(0)
        for (cx, cy, label) in targets:
            canvas.rectb(cx - 20, cy - 20, 40, 40, 10)          # yellow box
            canvas.print(label, max(4, min(w - 180, cx - 40)), cy + 26, 7)
        canvas.print("TOUCH CALIBRATE - tap the boxes, watch serial", w // 2 - 340, h // 2 - 60, 7)
        canvas.print(msg, w // 2 - 340, h // 2 + 40, 6)
        if mx >= 0:
            canvas.rect(mx - 4, my - 4, 9, 9, 8)                # red: mapped landing
        comp.flush()

    _draw("swap=%s flip_x=%s flip_y=%s" % (p4_input.SWAP_XY, p4_input.FLIP_X, p4_input.FLIP_Y))
    set_backlight(True)
    print("Moybyte P4 touch calibrate: swap=%s flip_x=%s flip_y=%s (Ctrl-C to exit)"
          % (p4_input.SWAP_XY, p4_input.FLIP_X, p4_input.FLIP_Y))
    last_print = 0
    while True:
        tp = touch.poll()
        now = _ticks_ms()
        if tp is not None and (tp[2] or _ticks_diff(now, last_print) > 250):
            last_print = now
            raw = touch.raw or (-1, -1)
            print("TAP%s mapped=(%d,%d) raw=(%d,%d) swap=%s flip_x=%s flip_y=%s"
                  % ("*" if tp[2] else " ", tp[0], tp[1], raw[0], raw[1],
                     p4_input.SWAP_XY, p4_input.FLIP_X, p4_input.FLIP_Y))
            if tp[2]:
                _draw("last mapped=(%d,%d) raw=(%d,%d)"
                      % (tp[0], tp[1], raw[0], raw[1]), tp[0], tp[1])
        time.sleep_ms(20)


def run_ppa_smoke(scale=2, iters=60):
    """A/B the PPA vs the CPU composite on this board's glass -- the body is
    device/p4_canvas.run_ppa_smoke (one copy for both P4 boards); this hands
    it the compositor and the backlight. Ctrl-C the desktop first, then:

        import moy_runtime; moy_runtime.run_ppa_smoke()
    """
    from p4_display import P4Compositor, set_backlight
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
# for hours. The backlight is also the one power lever the board README calls
# out as unmeasured -- its 2.85W draw has never been split between the SoC and
# the panel, and "one reading with the backlight blanked settles it".
POWER_SAVE_MS = 300000          # 5 minutes


def run_desktop(fps_cap=60):
    """Boot the shared console on the Waveshare 7B: launcher-as-desktop under
    WindowedWM, the 1024x600 DSI glass, GT911 touch as the pointer, a BLE HID
    keyboard over the companion C6, and carts on internal flash. Ctrl-C over
    the CH343 REPL interrupts the loop.

    The BODY is `device/p4_desktop.py`, shared with the Guition P4
    (2026-09-09): two P4 boards running the same console over the same silicon
    tier had two copies of it, differing in fifty lines of which all but five
    were the board's own name in a print string. What is left here is what
    this glass decides -- its compositor, its touch, its constants."""
    from p4_display import P4Compositor, set_backlight
    from p4_input import Touch
    from p4_desktop import run_desktop as _run_desktop

    return _run_desktop(
        name="Moybyte P4", link_id="p4",
        compositor=P4Compositor, set_backlight=set_backlight, touch_cls=Touch,
        font_scale=FONT_SCALE, panel_diagonal_in=PANEL_DIAGONAL_IN, seed_carts=CARTS,
        store_root=CARTS_ROOT, ota_dir=OTA_UPDATE_DIR,
        power_save_ms=POWER_SAVE_MS, game_wh=(GAME_W, GAME_H), fps_cap=fps_cap)
