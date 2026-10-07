"""The P4 tier's desktop: what both ESP32-P4 boards add over the shared spine.

The Waveshare 7B (#58) and the Guition 10.1" (#220) run the same console on
the same silicon over the same shared tier -- `native/p4/` (moy_dsi, moy_ppa,
moy_ble_hid, moy_c6), `device/dsi_panel.py`, `device/p4_canvas.py` -- and the
boot itself is every console board's (`device/desktop_spine.py`). What is the
P4 tier's, and lives here once for both boards:

  * the SYSTEM canvas is a `P4SystemCanvas` drawing straight into the DSI
    scan-out buffer, with the PPA composite hooks enabled at boot;
  * the presentation tier is the WINDOWED WM (#73): the launcher is the desk,
    every app a floating window, the PLAY icon the way to the fullscreen
    Library;
  * a BLE HID keyboard over the companion C6 is the console's keyboard, and
    the C6 has an updater of its own (Settings -> UPGRADE C6 RADIO);
  * the cart store is the TF card when one mounts and the board's internal
    flash when not (`device/card_store.py`), the slot being SDMMC slot 0 on
    LDO channel 4 on both boards;
  * the deferred present (#58 composite-overlap) runs before the canvases
    re-point, and the PPA's overlap counters ride the PERF line;
  * three dev-channel extras: `bt` (the keyboard), `union` and `cache` (the
    windowed WM's two A/B levers).

A board passes what its own glass decides -- its name, its compositor, its
touch driver, its constants -- and takes the rest.
"""

from card_store import carts_loader
from desktop_spine import build_desktop, bt_command
from device_util import _ticks_ms, _ticks_diff
from p4_canvas import P4SystemCanvas
import wire_input
import wire_links

BLE_STORE = "/moy/ble_keyboard.json"   # the bond store is device identity: internal flash

# The TF slot, wired the same on both boards: SDMMC slot 0 on GPIO39-44. (Slot 1
# is the C6's transport; constructing it panics the board.) A card's store is
# `moy_carts.CARTS_DIR`, the T-Deck's, so the system documents beside the carts
# (system.json, wifi.json, shared.moygfx) land in /sd/moybyte and not at the
# card's root.
SD_SLOT = 0
SD_CLK = 43
SD_CMD = 44
SD_DATA = (39, 40, 41, 42)
SD_FREQ_KHZ = 20000
SD_CARTS_ROOT = "/sd/moybyte/carts"
# PMU_EXT_LDO_P1_0P2A: the register that owns LDO channel 4, which powers the
# slot. Stock MicroPython never enables it, and without it the card times out
# whether one is there or not.
LDO4_REG = 0x501151D8


def p4_card():
    """The TF card as the store's own volume (native/moy_store's card over
    moy_sd's SDMMC slot): LDO4 switched on (software-owned, tied to the 3.3V
    rail, then powered), then the slot brought up once and never torn down,
    so the store's FATFS outlives anything the VM frees. Raises when there is
    no card."""
    import time
    import moy_sd
    import moy_store
    from machine import mem32
    mem32[LDO4_REG] |= (1 << 7) | (1 << 14)
    mem32[LDO4_REG] |= (1 << 8)
    time.sleep_ms(10)               # the card's supply settling before CMD0
    sectors = moy_sd.mmc(SD_SLOT, SD_CLK, SD_CMD, SD_DATA, SD_FREQ_KHZ)
    return moy_store.card(sectors)


def run_desktop(name, link_id, compositor, set_backlight, touch_cls,
                font_scale, panel_diagonal_in, seed_carts, store_root, ota_dir,
                power_save_ms, game_wh=(320, 240), fps_cap=60):
    """Boot the shared console on a P4 board: launcher-as-desktop under
    WindowedWM, this board's glass, its touch as the pointer, a BLE HID
    keyboard over the companion C6, and carts on the TF card when one mounts
    (`store_root`, on the board's internal flash, when not). Ctrl-C over the
    board's REPL interrupts the loop."""
    from wm_windowed import WindowedWM

    comp = compositor()
    gfx = comp.gfx()
    print("%s display up (%dx%d, gfx=%s)"
          % (name, comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = P4SystemCanvas(comp, font_scale=font_scale)
    # Hardware compositing (#58): the PPA offloads the game->window scale blit
    # + the drag backdrop-cache copy from the CPU. CPU kernel if it fails to
    # register.
    print("%s PPA:" % name,
          "enabled" if P4SystemCanvas.enable_ppa() else "CPU-only")
    inp, keyboard = wire_input.ble_keyboard_input(BLE_STORE)

    def _union_cmd(ws, parts, line):
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.wm._union_disabled = not on
        print("REMOTE union %s" % ("on" if on else "off"))

    def _cache_cmd(ws, parts, line):
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.wm._backdrop_disabled = not on
        print("REMOTE cache %s" % ("on" if on else "off"))

    d = build_desktop(name, link_id, comp, sys_canvas, set_backlight, inp,
                      inputs=lambda: touch_cls(sys_canvas.w, sys_canvas.h),
                      keyboard=keyboard, seed_carts=seed_carts,
                      power_save_ms=power_save_ms, game_wh=game_wh,
                      font_scale=font_scale,
                      panel_diagonal_in=panel_diagonal_in,
                      load_carts=carts_loader(p4_card, seed_carts, SD_CARTS_ROOT,
                                              store_root, ota_dir),
                      wm=WindowedWM,
                      c6_updater=wire_links.c6_updater_class(),
                      extras={"bt": bt_command(keyboard, comp),
                              "union": _union_cmd, "cache": _cache_cmd},
                      overlap=comp.overlap_stats, fps_cap=fps_cap)
    game = d.game
    sys_canvas = d.sys_canvas

    def _present():
        # Present the PREVIOUS quiet game frame now (its async composite has
        # been DMAing through the input poll): wait the DMA, switch scan-out
        # to it, free the other buffer. No-op unless the last frame deferred.
        # Must precede sync_back, which re-points at the freed buffer.
        comp.present_pending()
        game.sync_back()           # off-screen: contract no-op
        sys_canvas.sync_back()     # double-buffer: re-point at the new BACK fb

    return d.run(present=_present)


def run_touch_calibrate(name, compositor, set_backlight, touch_cls, knobs):
    """Touch calibration aid: corner + center targets on the glass, every
    sample printed to serial as raw + mapped coords + the live knob state.
    `knobs(touch)` returns that state as text -- the board says where its
    knobs live (module globals read per poll, or attributes on the driver).

    Run it from the REPL (Ctrl-C out of the desktop first):

        import moy_runtime; moy_runtime.run_touch_calibrate()

    Tap each numbered box; read which box the MAPPED coords land in; flip the
    knobs and re-run until mapped == tapped everywhere, then bake the winners
    into the board's input module. Ctrl-C exits."""
    import time

    comp = compositor()
    canvas = P4SystemCanvas(comp, font_scale=2)
    touch = touch_cls(canvas.w, canvas.h)
    w, h = canvas.w, canvas.h
    targets = ((60, 60, "1 TOP-LEFT"), (w - 61, 60, "2 TOP-RIGHT"),
               (60, h - 61, "3 BOT-LEFT"), (w - 61, h - 61, "4 BOT-RIGHT"),
               (w // 2, h // 2, "5 CENTER"))

    def _draw(msg, mx=-1, my=-1):
        canvas.cls(0)
        for (cx, cy, label) in targets:
            canvas.rectb(cx - 20, cy - 20, 40, 40, 10)          # yellow box
            canvas.print(label, max(4, min(w - 180, cx - 40)), cy + 26, 7)
        canvas.print("TOUCH CALIBRATE - tap the boxes, watch serial",
                     20, h // 2 - 60, 7)
        canvas.print(msg, 20, h // 2 + 40, 6)
        if mx >= 0:
            canvas.rect(mx - 4, my - 4, 9, 9, 8)                # red: mapped landing
        comp.flush()

    _draw(knobs(touch))
    set_backlight(True)
    print("%s touch calibrate: available=%s %s (Ctrl-C to exit)"
          % (name, touch.available, knobs(touch)))
    last_print = 0
    while True:
        tp = touch.poll()
        now = _ticks_ms()
        if tp is not None and (tp[2] or _ticks_diff(now, last_print) > 250):
            last_print = now
            raw = touch.raw or (-1, -1)
            print("TAP%s mapped=(%d,%d) raw=(%d,%d) %s"
                  % ("*" if tp[2] else " ", tp[0], tp[1], raw[0], raw[1],
                     knobs(touch)))
            if tp[2]:
                _draw("last mapped=(%d,%d) raw=(%d,%d)"
                      % (tp[0], tp[1], raw[0], raw[1]), tp[0], tp[1])
        time.sleep_ms(20)
