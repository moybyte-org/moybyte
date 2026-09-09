"""The P4 desktop spine: ONE `run_desktop` for both ESP32-P4 boards.

The Waveshare 7B (#58) and the Guition 10.1" (#220) run the same console on
the same silicon over the same shared tier -- `native/p4/` (moy_dsi, moy_ppa,
moy_ble_hid, moy_c6), `device/dsi_panel.py`, `device/p4_canvas.py` -- and
their two `moy_runtime.run_desktop` bodies had drifted into 189 and 187 lines
of code that differed in FIFTY, of which all but five were the board's own
name inside a print string. Measured 2026-09-09; this file is those two bodies
made one, which is the same call `native/p4/` and the two device modules
already made one level down (docs/board_ports_2026-08.md's "TAKE THESE").

The five real differences are ARGUMENTS now, and that is the contract:

  name          the serial prefix every line of this boot carries
  link_id       the ESP-NOW board id (and the default console name)
  compositor    the board's compositor class, called with no arguments --
                `P4Compositor` on a landscape panel, `RotatedCompositor` on
                portrait glass. A compositor that says `rotated` is handed the
                chrome strip height a quiet frame rotates beside the game.
  set_backlight the panel light, GPIO and polarity being the board's own
  touch_cls     the touch driver class, called (w, h) -- a GT911 or a GSL3680
  font_scale / panel_diagonal_in / seed_carts / store_root / ota_dir /
  power_save_ms / game_wh
                the board's constants, which stay declared in ITS module
                beside the comments that justify them

Everything else here -- the boot spine, the two-domain canvas seam, the
service wiring, the radio link, OTA, the C6 updater, the web console, the
windowed WM, the dev channel and its three P4 extras, the perf sampler and the
frame loop -- is the same on both boards because it is the same console. A fix
made here lands on both, which is the whole point: before this, every one of
them had to be made twice, and the drift above is what happens when it is not.

The board keeps what its own glass decides: its display and input modules, its
constants, its calibrate/smoke aids, and its README.
"""

from console import Pointer, Workstation, wire_workstation_core
from device_boot import (DeviceBoot, FrameLoop, FramePump, IdleBlank,
                         OtaHealth, PerfSampler, apply_touch, poll_webhost)
from device_api import make_api
from device_canvas import DeviceCanvas, _LayerComp
from device_wifi import autoconnect_wifi, make_wifi
from p4_canvas import P4SystemCanvas


def run_desktop(name, link_id, compositor, set_backlight, touch_cls,
                font_scale, panel_diagonal_in, seed_carts, store_root, ota_dir,
                power_save_ms, game_wh=(320, 240), fps_cap=60):
    """Boot the shared console on a P4 board: launcher-as-desktop under
    WindowedWM, this board's glass, its touch as the pointer, a BLE HID
    keyboard over the companion C6, and carts on internal flash. Ctrl-C over
    the board's REPL interrupts the loop."""
    game_w, game_h = game_wh

    from ble_keyboard import BleHidKeyboard
    from moybyte.input import InputState
    from wm_windowed import WindowedWM
    import moy_carts

    comp = compositor()
    gfx = comp.gfx()
    print("%s display up (%dx%d, gfx=%s)"
          % (name, comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = P4SystemCanvas(comp, font_scale=font_scale)
    # Hardware compositing (#58): the P4 PPA offloads the game->window scale
    # blit + the drag backdrop-cache copy from the CPU (DMA, ~2.6x). CPU kernel
    # if it fails to register.
    print("%s PPA:" % name,
          "enabled" if P4SystemCanvas.enable_ppa() else "CPU-only")

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
    boot = DeviceBoot(sys_canvas, comp, set_backlight, name)
    boot.note("starting")
    # The fixed 320x240 GAME canvas (#39): off-screen RGB565 sharing the same
    # native kernel; the windowed WM composites it into the player window.
    # (#77: -O3 on moy_gfx and an internal-SRAM game canvas were A/B'd here --
    # individually AND combined -- and all measured render-slice no-ops: the
    # slice is MicroPython per-draw-call dispatch, not C compute or framebuffer
    # bandwidth. So the canvas stays in PSRAM (internal SRAM is wanted for
    # WiFi/audio DMA). See docs/perf_native_gap_v1.md.)
    game = DeviceCanvas(_LayerComp(game_w, game_h, gfx))
    inp = InputState()
    # The GSL3680 uploads its firmware here (~1.5s of I2C at 400kHz), after
    # the panel is up.
    touch = touch_cls(sys_canvas.w, sys_canvas.h)
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer          # touch-driven carts read it via the api touch()

    boot.note("loading cartridges")
    carts, carts_root = boot.load_carts(moy_carts, seed_carts,
                                        root=store_root, media="flash")
    # P4 keyboard (#26): the C6_WIFI MicroPython variant already exposes NimBLE
    # central/GATT-client bindings over ESP-Hosted SDIO. Keep construction lazy
    # until /moy exists (the bond store lives beside the carts), and start the
    # radio after the Workstation has finished its boot allocations below.
    keyboard = BleHidKeyboard(inp, store_path="/moy/ble_keyboard.json",
                              auto_start=False)
    boot.note("building the desktop")
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=font_scale,
                     panel_diagonal_in=panel_diagonal_in)
    # The chrome strip a quiet frame rotates besides the game rect (the top
    # bar is stamped by an ungated blit every play frame, so the gates cannot
    # see it change): the TALLEST bar the layout can draw, once the console
    # knows it -- the app bar at the chrome floor (36 rows here, #203), which
    # also covers the 18-row desk/game bar.
    if getattr(comp, "rotated", False):
        comp.strip_h = max(ws.layout.status_h, ws.bar_layer._bar_h("desktop"))
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
        ws.link = make_link(board=link_id,
                            name=ws.system.get("name", link_id))
        ws.net = ws.link.net
    except Exception as exc:  # noqa: BLE001 -- no radio must never cost a console
        print("%s link unavailable:" % name, exc)
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
                                        update_dir=ota_dir)
        ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
    except Exception as exc:  # noqa: BLE001
        print("%s: OTA updater unavailable:" % name, exc)
    # The C6 radio's own updater (#7/#58): Settings -> UPGRADE C6 RADIO.
    # Rides ws.updater for the manifest + download, moy_c6.ota_* for the
    # flash; the backend module's header carries the whole design. Failure
    # is a missing Settings row, never a boot failure.
    try:
        if ws.updater is not None:
            from moy_c6_update import C6Updater
            ws.c6_updater = C6Updater(ws.updater)
    except Exception as exc:  # noqa: BLE001
        print("%s: C6 updater unavailable:" % name, exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("%s: reboot hook unavailable:" % name, exc)
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
        print("%s: web console unavailable:" % name, exc)
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
    # `crisp 0|1` is NOT here: it is a settings-toggle word (the table lives
    # in settings_layer), served by the
    # shared dev channel wherever its capability gate says yes. This board's
    # identically-behaved extra was shadowed dead the day that landed, and a
    # dead handler that looks like the live one is how the next reader edits
    # the wrong body.
    idle = IdleBlank(set_backlight, power_save_ms)
    ws._psave_ms = power_save_ms   # `state` reports the LIVE timeout

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
        # `touch` is this board's: its flips are live attributes, so a finger
        # on the glass and `py touch.flip_x = True` calibrate without a REPL.
        serial = DevChannel(ws, pointer, set_backlight=set_backlight, idle=idle,
                            extra={"bt": _bt_cmd, "union": _union_cmd,
                                   "cache": _cache_cmd},
                            env={"comp": comp, "game": game, "boot": boot,
                                 "touch": touch})
    except Exception as exc:  # noqa: BLE001 -- remote input is optional sugar
        print("%s serial channel unavailable:" % name, exc)
        serial = None

    import gc
    gc.collect()
    # Say what became of the last update before anything else can overwrite the
    # evidence (#53). The rollback CONFIRM does not happen here -- reaching this
    # line only proves the desktop was built, and an image that never paints has
    # already shipped once (#56). It is fired from the frame loop below
    # (FramePump.tail), after the console has actually drawn.
    _ota = OtaHealth(ws, log=lambda m: print("%s OTA: %s" % (name, m)))
    _ota.boot_check()
    print("%s desktop running (Ctrl-C for REPL)" % name)
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
        outside the NimBLE IRQ) and this board's pointer. The dev channel and the
        idle blank run in the SHARED loop, in the one order that lets the
        waking touch be swallowed."""
        try:
            keyboard.poll()
        except Exception as exc:  # noqa: BLE001 -- keyboard must fail touch-only
            print("%s BLE keyboard poll failed:" % name, exc)
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
        print("%s frame error:" % name, exc)
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
