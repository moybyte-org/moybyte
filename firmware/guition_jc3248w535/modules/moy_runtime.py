"""Moybyte Guition JC3248W535 device backend (#202): the shared console on the
board the port kit was built for.

The first FULLSCREEN-tier board where the system canvas is not the game
canvas: the system surface is the LANDSCAPE 480x320 glass (owner call
2026-08-18 -- the panel is portrait-native and its MADCTL MV is dead, so
moy_axs rotates in the band copy; the #39 responsive layouts run at native
res exactly as they do in a P4 window) and the game stays a fixed 320x240
DeviceCanvas that `wm.FullscreenStackWM`'s composite_game centres at 1:1 --
the seam the P4 runs windowed, run fullscreen for the first time, with no new
code on this side of it: the base `SystemCanvas` already carries
blit_game/blit_cover, the WM already computes the viewport, and this file
only constructs the pieces.

Input is the P4's shape (touch-only, no poller thread, no keyboard modes),
the panel is this board's own (`guition_panel.GuitionCompositor` over
`moy_axs`), the store is a TF card when one is in the slot and internal flash
when not. Everything else -- the boot order, the service set, the frame loop
-- is the shared spine (`device/desktop_spine.py`).
"""

from desktop_spine import build_desktop, bt_command
# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_canvas import SystemCanvas

GAME_W, GAME_H = 320, 240
FONT_SCALE = 1                 # 2x was BUILT AND REVERTED on owner verdict
                               # (2026-08-19, same day): text at 1x reads fine on
                               # this glass and 2x "looks bad" -- the real problem
                               # is TAP TARGETS, and PANEL_DIAGONAL_IN below is
                               # the lever that answers them. Do not re-flip this
                               # constant to solve tap size.
PANEL_DIAGONAL_IN = 3.5        # the glass, in inches -- board.toml [panel] is the
                               # authority and tests/test_board_toml.py pins the
                               # two together. It buys the #203 chrome tap-target
                               # floor: at ~165 PPI a 16px bar icon is 2.5mm, so
                               # interactive geometry lays out at scale 2 while
                               # every glyph stays at FONT_SCALE.
# Internal-flash store root -- the P4's arrangement and the P4's hard-learned
# name rule: NOT "/moybyte/..." (a root-level VFS dir named like a frozen
# module SHADOWS it; '' precedes '.frozen' on sys.path).
CARTS_ROOT = "/moy/carts"
OTA_UPDATE_DIR = "/moy/update"

# Stage 4 (owner call 2026-08-20): a TF card, when present, IS the cart store
# (the T-Deck model -- removable, kid-swappable carts); no card degrades to the
# internal-flash root above, exactly the store this board shipped with. The
# slot is on its OWN SPI3 pins (community map, verified on this glass), nothing
# shared with the panel's SPI2, so this is plain machine.SDCard + os.mount --
# none of the T-Deck's moy_sd bus-sharing machinery applies. Deliberate: OTA
# keeps staging on the INTERNAL VFS (a pulled card must never kill an update
# mid-stream; the 16MB flash has the room), and the BLE bond store stays
# internal too (device identity, not cart data). Wifi credentials live beside
# the carts and so follow the card -- the T-Deck accepts the same trade.
# slot=2 IS SPI3_HOST -- machine_sdcard.c's spi table lists SPI3 FIRST, so
# SPI slot numbers map in the OPPOSITE order of the host numbers (slot 2 ->
# SPI3, slot 3 -> SPI2). slot=3 therefore grabs the PANEL's bus and every
# construction dies with ESP_ERR_INVALID_STATE before touching the card --
# measured on this glass 2026-08-20, one evening of postmortem plumbing.
SD_PINS = dict(slot=2, sck=12, mosi=11, miso=13, cs=10)
SD_MOUNT = "/sd"
SD_CARTS_ROOT = "/sd/carts"
# _mount_sd's postmortem: the boot happens before a serial host attaches (the
# #201 console DROPS unheard output), so the mount verdict is also recorded
# here for the dev channel -- `py __import__("moy_runtime").SD_STATUS`.
SD_STATUS = "not attempted"


def _mount_sd():
    """Mount the TF card; True if the store should live there. Any failure --
    no card, wrong pins, dead card, foreign filesystem -- degrades to internal
    flash with the reason on serial, so SD can only ever ADD storage, never
    cost the boot."""
    global SD_STATUS
    try:
        import machine
        import os
        sd = machine.SDCard(**SD_PINS)
    except Exception as exc:  # noqa: BLE001
        SD_STATUS = "construct failed: %r" % exc
        print("Moybyte Guition SD: no card interface (%r)" % exc)
        return False
    try:
        os.mount(sd, SD_MOUNT)
        SD_STATUS = "mounted"
        print("Moybyte Guition SD: mounted at %s" % SD_MOUNT)
        return True
    except Exception as exc:  # noqa: BLE001
        SD_STATUS = "mount failed: %r" % exc
        # deinit() frees the SPI bus (it calls spi_bus_free) -- without it a
        # failed mount leaks the claimed host and every later probe, live ones
        # over the dev channel included, reads ESP_ERR_INVALID_STATE.
        try:
            sd.deinit()
        except Exception:  # noqa: BLE001
            pass
        print("Moybyte Guition SD: card unreadable (%r) -- carts on internal "
              "flash (a modern card often ships exFAT; format it FAT32)" % exc)
        return False


def _load_carts(boot, store):
    """This board's store: the card when it mounts, internal flash when not;
    the OTA image stages on internal flash either way."""
    sd_ok = _mount_sd()
    carts, root = boot.load_carts(store, CARTS,
                                  root=SD_CARTS_ROOT if sd_ok else CARTS_ROOT,
                                  media="SD" if sd_ok else "flash")
    return carts, root, OTA_UPDATE_DIR


# Idle screen blank -- the shared IdleBlank, the shared 5 minutes.
POWER_SAVE_MS = 300000         # 0 disables


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts under FullscreenStackWM,
    AXS15231 touch as the pointer, a BLE HID keyboard on the S3's own radio,
    carts on the card or internal flash. The REPL stays alive under the
    desktop (#201's console arrangement), so Ctrl-C interrupts and the dev
    channel takes complete lines."""
    from guition_panel import GuitionCompositor, set_backlight
    from axs_touch import Touch
    from ble_keyboard import BleHidKeyboard
    from moybyte.input import InputState

    comp = GuitionCompositor(nfbs=2)
    gfx = comp.gfx()
    print("Moybyte Guition display up (%dx%d, gfx=%s)"
          % (comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = SystemCanvas(comp, font_scale=FONT_SCALE)
    inp = InputState()
    # The S3's on-chip radio; started by the spine after the Workstation's boot
    # allocations. Also this board's game-exit path: a paired keyboard's
    # hold-BACKSPACE works through the shared console unmodified.
    keyboard = BleHidKeyboard(inp, store_path="/moy/ble_keyboard.json",
                              auto_start=False)

    d = build_desktop("Moybyte Guition", "guition_s3", comp, sys_canvas,
                      set_backlight, inp,
                      inputs=lambda: Touch(sys_canvas.w, sys_canvas.h),
                      keyboard=keyboard, seed_carts=CARTS,
                      power_save_ms=POWER_SAVE_MS, game_wh=(GAME_W, GAME_H),
                      font_scale=FONT_SCALE,
                      panel_diagonal_in=PANEL_DIAGONAL_IN,
                      load_carts=_load_carts,
                      extras={"bt": bt_command(keyboard)}, fps_cap=fps_cap)

    def _tail(now):
        # The idle-band drain, the T-Deck's #40/#66 lesson which this board's
        # overlapped flush shares exactly: a quiet frame returns before
        # comp.flush(), leaving the previous frame's tail bands to the 2ms
        # pump timer -- whose constructor is allowed to fail. When THIS frame
        # did not draw, drain; no-op when it did.
        if not d.loop.drew:
            try:
                comp.sync()
            except Exception:  # noqa: BLE001 -- an idle tidy-up must never throw
                pass
        d.tail(now)

    return d.run(tail=_tail)
