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
storage is the P4's (internal-flash VFS -- SD is an open stage-4 decision),
the panel is this board's own (`guition_panel.GuitionCompositor` over
`moy_axs`). Everything else is the shared spine.
"""

from console import Pointer, Workstation, wire_workstation_core
from device_boot import (DeviceBoot, FrameLoop, FramePump, IdleBlank,
                         OtaHealth, PerfSampler, apply_touch, poll_webhost)
# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_api import make_api
from device_canvas import DeviceCanvas, SystemCanvas, _LayerComp
from device_wifi import autoconnect_wifi, make_wifi

GAME_W, GAME_H = 320, 240
FONT_SCALE = 1                 # 2x was BUILT AND REVERTED on owner verdict
                               # (2026-08-19, same day): text at 1x reads fine on
                               # this glass and 2x "looks bad" -- the real problem
                               # is TAP TARGETS, which want a PPI floor on chrome
                               # geometry (bar icons/menu rows) independent of the
                               # font scale. That design is recorded in #202 and
                               # deferred until after the UI refactor; do not
                               # re-flip this constant to solve tap size.
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

# Idle screen blank -- the shared IdleBlank, the shared 5 minutes.
POWER_SAVE_MS = 300000         # 0 disables


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts under FullscreenStackWM,
    AXS15231 touch as the pointer, carts on internal flash. The REPL stays
    alive under the desktop (#201's console arrangement), so Ctrl-C interrupts
    and the dev channel takes complete lines."""
    from guition_panel import GuitionCompositor, set_backlight
    from axs_touch import Touch
    from ble_keyboard import BleHidKeyboard
    from moybyte.input import InputState
    import moy_carts

    comp = GuitionCompositor(nfbs=2)
    gfx = comp.gfx()
    print("Moybyte Guition display up (%dx%d, gfx=%s)"
          % (comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = SystemCanvas(comp, font_scale=FONT_SCALE)

    # -- the shared boot spine ---------------------------------------------
    boot = DeviceBoot(sys_canvas, comp, set_backlight, "Moybyte Guition")
    idle = IdleBlank(set_backlight, POWER_SAVE_MS)
    boot.note("starting")

    # The fixed 320x240 GAME canvas (#39): off-screen RGB565 over the same
    # native kernel; composite_game centres it 1:1 on the 480x320 glass.
    game = DeviceCanvas(_LayerComp(GAME_W, GAME_H, gfx))
    inp = InputState()
    touch = Touch(sys_canvas.w, sys_canvas.h)
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer

    boot.note("loading cartridges")
    sd_ok = _mount_sd()
    carts, carts_root = boot.load_carts(
        moy_carts, CARTS,
        root=SD_CARTS_ROOT if sd_ok else CARTS_ROOT,
        media="SD" if sd_ok else "flash")
    # BLE HID keyboard (shared driver, #202): the S3's on-chip radio.
    # Constructed AFTER the cart load so /moy exists (the bond store lives
    # beside the carts -- the P4's lesson); started after the Workstation's
    # boot allocations below. Also this board's game-exit path: a paired
    # keyboard's hold-BACKSPACE works through the shared console unmodified.
    keyboard = BleHidKeyboard(inp, store_path="/moy/ble_keyboard.json",
                              auto_start=False)
    boot.note("building the desktop")
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=FONT_SCALE)
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart-declared raster gets
    # its own off-screen canvas; blit_game upscales the view like any other.
    ws.make_game_canvas = lambda w, h: DeviceCanvas(
        _LayerComp(int(w), int(h), gfx))
    lua_runtime = boot.lua_runtime(ws)
    # The shared service wiring. No keyboard on this board (the code editor's
    # text-mode toggle stays a no-op; ws.keyboard stays None, every use is
    # guarded); no audio backend yet (stage 5); _with_sd stays the direct-call
    # default -- the store is internal flash and races nobody.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          make_wifi(moy_carts, carts_root),
                          lua_runtime=lua_runtime,
                          pointer=pointer, inp=inp, keyboard=keyboard)

    # THE RADIO LINK (#7/#65 Phase 2): the console's one ESP-NOW owner. Built
    # here, INERT until a cart with the "multiplayer" permission runs -- the
    # radio is only started by ws.link_arm(), because pm=PM_NONE costs battery
    # and a console sitting on its shelf has nobody to talk to. Two kids each
    # open the same game and the consoles find each other; there is no pairing
    # screen and no code to type, because being in the same room IS the
    # agreement (the doctrine the OTA design already set for the SD card).
    try:
        from moy_espnow import make_link
        ws.link = make_link(board="guition_s3",
                            name=ws.system.get("name", "guition_s3"))
        ws.net = ws.link.net
    except Exception as exc:  # noqa: BLE001 -- no radio must never cost a console
        print("Moybyte Guition link unavailable:", exc)
        ws.link = None
    # OTA (#53), the P4 arrangement: no SD, image stages on the internal VFS,
    # with_sd is a plain call-through.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(lambda fn: fn(),
                                        update_dir=OTA_UPDATE_DIR)
        ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
    except Exception as exc:  # noqa: BLE001
        print("Moybyte Guition: OTA updater unavailable:", exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("Moybyte Guition: reboot hook unavailable:", exc)
    # WEB CONSOLE: the wasm console baked into this image (native/.staged/
    # moy_web), served from the board. Constructed, not started.
    try:
        from moy_webhost import make_webhost, INTERNAL_WEB_DIR
        ws.webhost = make_webhost(ws, carts_root, INTERNAL_WEB_DIR,
                                  autoconnect=autoconnect_wifi)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte Guition: web console unavailable:", exc)

    keyboard.start()               # failure is touch-only, never a boot failure

    # The serial dev channel: ONE class, every board (runtime/dev_channel.py).
    # Board extra: `bt status|scan|forget|trace` -- the P4's BLE keyboard
    # diagnostics, minus its DSI-underrun field.
    ws._psave_ms = POWER_SAVE_MS

    def _bt_cmd(ws, parts, line):
        action = parts[1] if len(parts) > 1 else "status"
        if action == "scan":
            print("REMOTE bt scan ->", keyboard.scan())
        elif action == "forget":
            keyboard.forget()
            print("REMOTE bt forgot keyboard + local bonds")
        elif action == "status":
            print("REMOTE bt status state=%s name=%s passkey=%s "
                  "protocol=%s interval_ms=%s notify=%s fast=%s error=%s"
                  % (keyboard.status()[0], keyboard.status()[1],
                     keyboard.status()[2], keyboard.protocol,
                     keyboard._conn_interval_ms, keyboard._notify_count,
                     keyboard.fast_status(), keyboard.error))
        elif action == "trace":
            on = not (len(parts) > 2 and parts[2] == "0")
            print("REMOTE bt trace ->", keyboard.trace(on))
        else:
            print("REMOTE bt ? %s" % line)

    try:
        from dev_channel import DevChannel
        serial = DevChannel(ws, pointer, set_backlight=set_backlight, idle=idle,
                            extra={"bt": _bt_cmd},
                            env={"comp": comp, "game": game, "boot": boot})
    except Exception as exc:  # noqa: BLE001 -- remote input is optional sugar
        print("Moybyte Guition serial channel unavailable:", exc)
        serial = None

    import gc
    gc.collect()
    # The OTA verdict before anything overwrites the evidence; the rollback
    # CONFIRM fires from the frame loop (FramePump.tail), never from here (#56).
    _ota = OtaHealth(ws, log=lambda m: print("Moybyte Guition OTA: %s" % m))
    _ota.boot_check()
    print("Moybyte Guition desktop running (Ctrl-C for REPL)")
    boot.start_frames(ws)
    pump = FramePump(boot, _ota, fps_cap)
    if serial is not None:
        serial.env["pump"] = pump
    # Perf sampler -- device_boot.PerfSampler over runtime/perf_line.py, the ONE
    # body and ONE format every board emits (#206 item 2; this was a hand copy
    # of the P4's, and said so, until 2026-08-28). The meters follow Settings ->
    # PERF DIAG live. Nothing per-board is passed: this is a fullscreen tier
    # with no windowed WM and a panel with no PPA, so those columns print `-`
    # rather than a zero indistinguishable from a broken lever.
    ws.perf_capture = bool(getattr(ws, "diag_live", False))
    _perf = PerfSampler(ws)

    def _poll_inputs(now):
        """This board's input sources: the BLE keyboard's async notifications
        (applied before begin_frame so InputState gets clean press/release
        edges; poll() also advances scan/reconnect -- the P4's arrangement)
        and the AXS15231 pointer."""
        try:
            keyboard.poll()
        except Exception as exc:  # noqa: BLE001 -- keyboard must fail touch-only
            print("Moybyte Guition BLE keyboard poll failed:", exc)
        inp.begin_frame()
        touched, click = apply_touch(touch, pointer)
        return click, (touched or bool(inp._held) or bool(inp.last_key))

    def _present():
        game.sync_back()           # off-screen: contract no-op
        sys_canvas.sync_back()     # ping-pong: re-point at the new BACK fb

    def _frame_error(exc):
        # With the traceback: this board is in bring-up, and "frame error: X"
        # with no line number cost the first on-glass session a reflash.
        print("Moybyte Guition frame error:", exc)
        try:
            import sys
            sys.print_exception(exc)
        except Exception:  # noqa: BLE001
            pass
        gc.collect()

    def _tail(now):
        # The idle-band drain, the T-Deck's #40/#66 lesson which this board's
        # overlapped flush shares exactly: a quiet frame returns before
        # comp.flush(), leaving the previous frame's tail bands to the 2ms
        # pump timer -- whose constructor is allowed to fail. When THIS frame
        # did not draw, drain; no-op when it did.
        if not loop.drew:
            try:
                comp.sync()
            except Exception:  # noqa: BLE001 -- an idle tidy-up must never throw
                pass
        poll_webhost(ws)

        # The radio, once per frame. At 30Hz an input frame carries ~2 messages
        # and the ring holds hundreds, so a per-frame slice is comfortable --
        # and draining on the frame loop is what keeps ESP-NOW off a thread
        # fighting the panel flush for the VM core. No-op while the link is
        # inert, which is every frame nobody is playing together.
        _lk = ws.link
        if _lk is not None and _lk.active:
            _lk.poll(ws)

    # The shared frame loop (#202 Phase B): the invariant order lives ONCE in
    # device_boot.FrameLoop; every hook above is this board's hardware.
    loop = FrameLoop(ws, pump, pointer, _poll_inputs, idle=idle, serial=serial,
                     present=_present, tail=_tail, account=_perf.account,
                     frame_error=_frame_error,
                     set_backlight=set_backlight, lit=boot.lit)
    if loop.run() == "quit":
        print("Moybyte Guition desktop: serial quit -> REPL")
