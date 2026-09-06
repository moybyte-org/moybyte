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

from console import Pointer, Workstation, wire_workstation_core
# The boot spine + frame pump, shared with the T-Deck (#161 Phase 4/5,
# canonical: runtime/device_boot.py; board.toml stages it like every other
# shared module). The steps that used to be written twice -- boot splash, cart
# seed+scan, the Lua runtime probe, the OTA verdict + rollback confirm, the
# frame cadence -- live there now, as do the idle screen blank (IdleBlank) and
# the serial dev channel (runtime/dev_channel.py, one vocabulary for every
# board -- this file adds only the P4-only extras: bt/union/cache). What stays
# here is hardware: the DPI scan-out, the PPA composite, BLE HID.
from device_boot import (DeviceBoot, FrameLoop, FramePump, IdleBlank,
                         OtaHealth, PerfSampler, apply_touch, poll_webhost)
# The seed roster, generated from system_carts/ at build time and PACKED
# (2026-08-30): one raw-deflate blob per cart, inflated ONE AT A TIME by
# `moy_carts.seed_any`, which reads the roster's form rather than being told.
# Named CARTS because that is what it is to everything downstream -- the
# compression is a storage detail of this one import.
from carts_data import CARTS_Z as CARTS
from device_util import _ticks_ms, _ticks_diff
from device_api import make_api
from device_canvas import DeviceCanvas, _LayerComp
from p4_canvas import P4SystemCanvas
from device_wifi import autoconnect_wifi, make_wifi

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
    """Boot the shared console on the P4: launcher-as-desktop under WindowedWM,
    GT911 touch as the pointer, a BLE HID keyboard over the companion C6, and
    carts on internal flash. Ctrl-C over the CH343 REPL interrupts the loop (no
    USB starvation on this board)."""
    from p4_display import P4Compositor, set_backlight
    from p4_input import Touch
    from ble_keyboard import BleHidKeyboard
    from moybyte.input import InputState
    from wm_windowed import WindowedWM
    import moy_carts

    comp = P4Compositor()
    gfx = comp.gfx()
    print("Moybyte P4 display up (%dx%d, gfx=%s)"
          % (comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = P4SystemCanvas(comp, font_scale=FONT_SCALE)
    # Hardware compositing (#58): the P4 PPA offloads the game->window scale
    # blit + the drag backdrop-cache copy from the CPU (DMA, ~2.6x). CPU kernel
    # if it fails to register.
    print("Moybyte P4 PPA:", "enabled" if P4SystemCanvas.enable_ppa() else "CPU-only")

    # -- the shared boot spine (#45/#58/#161) ------------------------------
    # DeviceBoot owns the boot splash + its progress bar, the cart seed/scan,
    # the Lua runtime probe and the "first frame in Nms" report -- one
    # implementation, both boards. What differs here is its arguments: the
    # serial prefix and this board's panel-light function.
    #
    # The panel stays dark until a frame has composed (#45), which is right:
    # an uninitialised framebuffer is worse than black. But it makes a slow
    # boot indistinguishable from a dead board. Owner-reported after a full
    # erase: "screen is black", serial silent after its last boot line, on a
    # board that was in fact working and did light up eventually.
    # The splash makes the wait legible on the glass and on the wire; the
    # timing line at the end of this function names where a slow boot went.
    boot = DeviceBoot(sys_canvas, comp, set_backlight, "Moybyte P4")
    boot.note("starting")
    # The fixed 320x240 GAME canvas (#39): off-screen RGB565 sharing the same
    # native kernel; the windowed WM composites it into the player window.
    # (#77: -O3 on moy_gfx and an internal-SRAM game canvas were A/B'd here --
    # individually AND combined -- and all measured render-slice no-ops: the
    # slice is MicroPython per-draw-call dispatch, not C compute or framebuffer
    # bandwidth. So the canvas stays in PSRAM (internal SRAM is wanted for
    # WiFi/audio DMA). See docs/perf_native_gap_v1.md.)
    game = DeviceCanvas(_LayerComp(GAME_W, GAME_H, gfx))
    inp = InputState()
    touch = Touch(sys_canvas.w, sys_canvas.h)
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer          # touch-driven carts read it via the api touch()

    boot.note("loading cartridges")
    carts, carts_root = boot.load_carts(moy_carts, CARTS, root=CARTS_ROOT,
                                        media="flash")
    # P4 keyboard (#26): the C6_WIFI MicroPython variant already exposes NimBLE
    # central/GATT-client bindings over ESP-Hosted SDIO. Keep construction lazy
    # until /moy exists (the bond store lives beside the carts), and start the
    # radio after the Workstation has finished its boot allocations below.
    keyboard = BleHidKeyboard(inp, store_path="/moy/ble_keyboard.json",
                              auto_start=False)
    boot.note("building the desktop")
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=FONT_SCALE,
                     panel_diagonal_in=PANEL_DIAGONAL_IN)
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster plays on its own off-screen canvas -- the exact constructor the
    # boot `game` canvas uses -- and P4SystemCanvas.blit_game (PPA) upscales it
    # like any game composite (a 128x120 view fills the 600px height at 5x).
    ws.make_game_canvas = lambda w, h: DeviceCanvas(
        _LayerComp(int(w), int(h), gfx))
    # The #67 Lua cart runtime (DeviceBoot.lua_runtime -- one probe, both
    # boards; see its docstring for why there is no chooser and what a build
    # without the module does instead).
    lua_runtime = boot.lua_runtime(ws)
    # The shared service wiring (console.wire_workstation_core -- one canonical
    # order for host + both boards; this used to be a hand-kept "same order as
    # host build_workstation" copy). P4 notes: can_manage's carts_root default is
    # the internal VFS -- no bus gymnastics, _with_sd stays the direct-call
    # default; the C6-hosted WLAN is transparent to network.WLAN
    # (bring-up-confirmed); no I2S audio backend yet.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          make_wifi(moy_carts, carts_root),
                          lua_runtime=lua_runtime,
                          pointer=pointer, inp=inp, keyboard=keyboard)
    # THE RADIO LINK (#7/#65 Phase 2 -- Phase E of docs/espnow_p4_2026-08.md):
    # the console's one ESP-NOW owner, the same module and the same wiring as
    # the S3 boards. Built here, INERT until a cart with the "multiplayer"
    # permission runs (ws.link_arm() starts the radio; pm=PM_NONE costs power
    # and a console on its shelf has nobody to talk to). On this board the
    # espnow module underneath is the moy_c6 shim to the C6 -- a stock C6
    # (no shim slave) makes start() fail into an inactive link, never a crash.
    try:
        from moy_espnow import make_link
        ws.link = make_link(board="p4", name=ws.system.get("name", "p4"))
        ws.net = ws.link.net
    except Exception as exc:  # noqa: BLE001 -- no radio must never cost a console
        print("Moybyte P4 link unavailable:", exc)
        ws.link = None

    # OTA firmware update (#53 on this board). The partition table has been
    # OTA-shaped since bring-up (ota_0/ota_1, 4MB each) and update_ui has been
    # frozen in all along; this is the piece that was missing.
    #
    # Two things differ from the T-Deck. There is no SD card in this console, so
    # the image stages on the internal VFS (~23MB free, against a ~3MB image) and
    # with_sd is a plain call-through -- no bus to drain, no card to mount. And
    # the board identity matters: an OTA payload is an app-partition image, so
    # the manifest is per board and this one must never be handed an S3 build.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(lambda fn: fn(),
                                        update_dir=OTA_UPDATE_DIR)
        ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: OTA updater unavailable:", exc)
    # The C6 radio's own updater (#7/#58): Settings -> UPGRADE C6 RADIO.
    # Rides ws.updater for the manifest + download, moy_c6.ota_* for the
    # flash; the backend module's header carries the whole design. Failure
    # is a missing Settings row, never a boot failure.
    try:
        if ws.updater is not None:
            from moy_c6_update import C6Updater
            ws.c6_updater = C6Updater(ws.updater)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: C6 updater unavailable:", exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: reboot hook unavailable:", exc)
    # WEB CONSOLE (moycore plan 3.4 pull half): serve the wasm console from this
    # board. Constructed, NOT started -- __init__ binds no socket, so injecting
    # it only makes the Settings row appear. `ensure_online` returns the STA IP,
    # which is what the row displays: 0.0.0.0 is the one address nobody can type
    # into a browser.
    try:
        from moy_webhost import make_webhost

        # The link wait that used to be a closure here is moy_webhost.ensure_online
        # now -- it was the same 25 lines the T-Deck needed, and writing it per
        # board is how that board went without the feature entirely.
        ws.webhost = make_webhost(ws, carts_root,
                                  autoconnect=autoconnect_wifi)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: web console unavailable:", exc)
    # The P4 presentation tier (#73/#58, two worlds #105): the DESK is home
    # (make world, windows); the PLAY icon drops to the fullscreen Library.
    # Installed AFTER load_system (same order as host build_workstation) so the
    # persisted font scale is applied before the root layout context is captured.
    ws.wm = WindowedWM(ws)
    ws.open_desk()
    keyboard.start()               # failure is touch-only, never a boot failure

    # The serial dev channel (#58/#156): ONE implementation for every board --
    # `dev_channel.DevChannel`, staged from runtime/ (state / tap / run / open /
    # swipe / drag / diag / skip / gov / power / py / web / quit; the CH343 REPL
    # stays alive under the desktop, so complete lines piped into the port
    # drive the UI while the glass is watched). Ctrl-C still interrupts as
    # before. This board's EXTRAS -- commands only its hardware or tier has:
    #   bt status|scan|forget|trace [0|1]  BLE keyboard diagnostics
    #   union 0|1   A/B the dirty-union gesture restore (pairs with `drag`)
    #   cache 0|1   A/B the drag backdrop cache
    # `crisp 0|1` is NOT here: it is a SETTINGS_TOGGLES word, served by the
    # shared dev channel wherever its capability gate says yes. This board's
    # identically-behaved extra was shadowed dead the day that landed, and a
    # dead handler that looks like the live one is how the next reader edits
    # the wrong body.
    idle = IdleBlank(set_backlight, POWER_SAVE_MS)
    ws._psave_ms = POWER_SAVE_MS   # `state` reports the LIVE timeout

    def _bt_cmd(ws, parts, line):
        action = parts[1] if len(parts) > 1 else "status"
        if action == "scan":
            print("REMOTE bt scan ->", keyboard.scan())
        elif action == "forget":
            keyboard.forget()
            print("REMOTE bt forgot keyboard + local bonds")
        elif action == "status":
            print("REMOTE bt status state=%s name=%s passkey=%s "
                  "protocol=%s interval_ms=%s notify=%s fast=%s "
                  "dsi_underruns=%s error=%s"
                  % (keyboard.status()[0], keyboard.status()[1],
                     keyboard.status()[2], keyboard.protocol,
                     keyboard._conn_interval_ms, keyboard._notify_count,
                     keyboard.fast_status(), comp.underruns(),
                     keyboard.error))
        elif action == "trace":
            on = not (len(parts) > 2 and parts[2] == "0")
            print("REMOTE bt trace ->", keyboard.trace(on))
        else:
            print("REMOTE bt ? %s" % line)

    def _union_cmd(ws, parts, line):
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.wm._union_disabled = not on
        print("REMOTE union %s" % ("on" if on else "off"))

    def _cache_cmd(ws, parts, line):
        on = not (len(parts) == 2 and parts[1] == "0")
        ws.wm._backdrop_disabled = not on
        print("REMOTE cache %s" % ("on" if on else "off"))

    try:
        from dev_channel import DevChannel
        # env: what the `py` probe hook can reach beyond ws/wm/pointer --
        # `pump.debt` and `boot.lit`/`boot.done` are the shared spine's only
        # on-glass witnesses (pump joins the env right after it is created).
        serial = DevChannel(ws, pointer, set_backlight=set_backlight, idle=idle,
                            extra={"bt": _bt_cmd, "union": _union_cmd,
                                   "cache": _cache_cmd},
                            env={"comp": comp, "game": game, "boot": boot})
    except Exception as exc:  # noqa: BLE001 -- remote input is optional sugar
        print("Moybyte P4 serial channel unavailable:", exc)
        serial = None

    import gc
    gc.collect()
    # Say what became of the last update before anything else can overwrite the
    # evidence (#53). The rollback CONFIRM does not happen here -- reaching this
    # line only proves the desktop was built, and an image that never paints has
    # already shipped once (#56). It is fired from the frame loop below
    # (FramePump.tail), after the console has actually drawn.
    _ota = OtaHealth(ws, log=lambda m: print("Moybyte P4 OTA: %s" % m))
    _ota.boot_check()
    print("Moybyte P4 desktop running (Ctrl-C for REPL)")
    # The last thing before the loop, and the stage the silent wait was in:
    # everything above had already printed when the screen was reported black.
    # start_frames also arms the boot logo, but ONLY if the splash never came up
    # (arming it otherwise replays the splash and delays the desktop).
    boot.start_frames(ws)
    # The shared frame pump (#161 Phase 5): the dt clock, the once-only
    # first-frame/OTA housekeeping, and the cadence + pacing debt. Everything
    # BETWEEN its head and its tail is this board's own hardware.
    pump = FramePump(boot, _ota, fps_cap)
    if serial is not None:
        serial.env["pump"] = pump   # created just above; see the env note
    # Perf sampler (#58 fps-ledger groundwork): serial is free on this board, so
    # a PERF line every ~2s -- drawn-fps, average busy loop ms, the console's
    # draw/flush/logic/render/chrome EMAs and this board's own WM/PPA columns.
    # Costs two tick reads per frame; the LINE is unconditional (its fps= field
    # reads _frames_drawn, so it is valid with the meters off).
    #
    # ONE BODY, ONE FORMAT, THREE BOARDS since #206 item 2: device_boot's
    # PerfSampler measures and runtime/perf_line.py formats, so nothing about
    # the shape is decided here. The only per-board argument is the compositor's
    # cumulative overlap counters, because this is the only board with a PPA;
    # the windowed-WM columns need no argument at all (wm_windowed stamps them
    # on the Workstation, and a board that does not stage it prints `-`).
    #
    # The METERS follow Settings -> PERF DIAG (#68 kid mode: perf_capture arms
    # per-layer walk timing, per-op canvas timers and the EMA tail -- ~1-1.5ms
    # of every frame there). This read an unconditional True until 2026-08-15,
    # so the toggle gated nothing at boot and the shipping fps could not be
    # measured without first issuing `diag 0` -- which tools/p4_perf.py already
    # did, its docstring already claiming "DIAG IS OFF BY DEFAULT". The sampler
    # re-syncs it live, so flipping the toggle needs no reboot.
    ws.perf_capture = bool(getattr(ws, "diag_live", False))
    _perf = PerfSampler(ws, overlap=comp.overlap_stats)

    def _poll_inputs(now):
        """This board's input sources: the BLE keyboard's async notifications
        (applied before begin_frame so InputState gets clean press/release
        edges; poll() also advances scan/reconnect and flushes a new bond once,
        outside the NimBLE IRQ) and the GT911 pointer. The dev channel and the
        idle blank run in the SHARED loop, in the one order that lets the
        waking touch be swallowed."""
        try:
            keyboard.poll()
        except Exception as exc:  # noqa: BLE001 -- keyboard must fail touch-only
            print("Moybyte P4 BLE keyboard poll failed:", exc)
        inp.begin_frame()
        touched, click = apply_touch(touch, pointer)
        return click, (touched or bool(inp._held) or bool(inp.last_key))

    def _present():
        # Present the PREVIOUS quiet game frame now (its async composite has
        # been DMAing through the input poll above): wait the DMA, switch
        # scan-out to it, free the other buffer. No-op unless the last frame
        # deferred (#58 composite-overlap). Must precede sync_back, which
        # re-points at the freed buffer.
        comp.present_pending()
        game.sync_back()           # off-screen: contract no-op
        sys_canvas.sync_back()     # double-buffer: re-point at the new BACK fb

    def _frame_error(exc):
        print("Moybyte P4 frame error:", exc)
        gc.collect()

    def _tail(now):
        poll_webhost(ws)               # see the helper for why the frame TAIL
        # The radio, once per frame -- same slice as the S3 boards (the module
        # header carries the numbers). No-op while the link is inert, which is
        # every frame nobody is playing together.
        _lk = ws.link
        if _lk is not None and _lk.active:
            _lk.poll(ws)

    # The shared frame loop (#202 Phase B): the invariant order lives ONCE, in
    # device_boot.FrameLoop -- including the #77/#161 pacing debt via
    # pump.pace and the first-frame backlight gate (dark until the first
    # composed frame, #45, unless the splash already lit it). Every hook above
    # is this board's own hardware.
    loop = FrameLoop(ws, pump, pointer, _poll_inputs, idle=idle, serial=serial,
                     present=_present, tail=_tail, account=_perf.account,
                     frame_error=_frame_error,
                     set_backlight=set_backlight, lit=boot.lit)
    loop.run()
