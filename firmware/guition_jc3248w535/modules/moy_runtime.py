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
the panel is this board's own (`moy_glass.BandedCompositor` over
`moy_axs`), the store is a TF card when one is in the slot and internal flash
when not. Everything else -- the boot order, the service set, the frame loop
-- is the shared spine (`device/desktop_spine.py`).
"""

from mem_census import mark as _census
from card_store import carts_loader
from desktop_spine import build_desktop, bt_command
# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_canvas import SystemCanvas

_census("imports")

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

# A TF card, when present, IS the cart store (the T-Deck model -- removable,
# kid-swappable carts); no card, an unreadable one or a store that cannot be
# seeded there all degrade to the internal-flash root above, exactly the store
# this board shipped with. The mount is `device/card_store.py`'s, once at boot;
# what is this board's is how the card is CONSTRUCTED. The slot is on its OWN
# SPI3 pins (community map, verified on this glass), nothing shared with the
# panel's QSPI on SPI2, so none of the T-Deck's bus-sharing machinery applies:
# `tf_card` opens the bus once and mounts the store's card volume over it. The OTA directory (a
# copied image, the pending marker) and the BLE bond store stay on the INTERNAL
# VFS (device identity, not cart data). A WiFi update stages nothing: it streams
# into the inactive slot, because this VFS cannot hold the image. The store on a
# card is moy_carts.CARTS_DIR, so the system documents beside the carts
# (system.json, wifi.json, shared.moygfx) land in /sd/moybyte, not at the
# card's root.
# slot=2 IS SPI3_HOST -- machine_sdcard.c's spi table lists SPI3 FIRST, so
# SPI slot numbers map in the OPPOSITE order of the host numbers (slot 2 ->
# SPI3, slot 3 -> SPI2). slot=3 therefore grabs the PANEL's bus and every
# construction dies with ESP_ERR_INVALID_STATE before touching the card --
# measured on this glass 2026-08-20, one evening of postmortem plumbing.
SD_PINS = dict(slot=2, sck=12, mosi=11, miso=13, cs=10)
SD_SPI_HOST = 2                 # SPI3_HOST: slot 2 (slot and host numbers run opposite)
SD_FREQ_KHZ = 20000
SD_CARTS_ROOT = "/sd/moybyte/carts"


def tf_card():
    """The TF card on SPI3, as the store's own volume (native/moy_store's
    card over moy_sd): the bus is initialised once, by `moy_sd.open`, and
    never torn down, so the store's FATFS outlives anything the VM frees."""
    import moy_sd
    import moy_store
    p = SD_PINS
    sectors = moy_sd.open(SD_SPI_HOST, p["sck"], p["mosi"], p["miso"], p["cs"],
                          SD_FREQ_KHZ)
    return moy_store.card(sectors)


# Idle screen blank -- the shared IdleBlank, the shared 5 minutes.
POWER_SAVE_MS = 300000         # 0 disables


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts under FullscreenStackWM,
    AXS15231 touch as the pointer, a BLE HID keyboard on the S3's own radio,
    carts on the card or internal flash. The REPL stays alive under the
    desktop (#201's console arrangement), so Ctrl-C interrupts and the dev
    channel takes complete lines."""
    import moy_axs
    import moy_glass
    from guition_input import make_input, make_touch

    # The kernel's banded compositor over moy_axs's transport: the band copy
    # rotates the landscape frame onto this panel, and the game window ships
    # the game rect alone (moy_axs's notes).
    comp = moy_glass.BandedCompositor(moy_axs, nfbs=2)
    set_backlight = comp.set_backlight
    gfx = comp.gfx()
    print("Moybyte Guition display up (%dx%d, gfx=%s)"
          % (comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = SystemCanvas(comp, font_scale=FONT_SCALE)
    _census("panel")
    inp, keyboard = make_input()

    d = build_desktop("Moybyte Guition", "guition_s3", comp, sys_canvas,
                      set_backlight, inp,
                      inputs=lambda: make_touch(sys_canvas.w, sys_canvas.h),
                      keyboard=keyboard, seed_carts=CARTS,
                      power_save_ms=POWER_SAVE_MS, game_wh=(GAME_W, GAME_H),
                      font_scale=FONT_SCALE,
                      panel_diagonal_in=PANEL_DIAGONAL_IN,
                      load_carts=carts_loader(tf_card, CARTS, SD_CARTS_ROOT,
                                              CARTS_ROOT, OTA_UPDATE_DIR),
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
