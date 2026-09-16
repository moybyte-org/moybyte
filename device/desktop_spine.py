"""The console desktop's boot spine: ONE body for every console board.

Four boards boot the same console, and the ORDER they boot it in is the
payload -- the boot splash before the store, the store before the Workstation,
the service wiring in `wire_workstation_core`'s one order, the windowed WM
after the boot loads, the OTA verdict before anything can overwrite it, the
frame loop last. `build_desktop` is that order written once; `Desktop.run` is
the frame loop constructed once. What a board supplies is its HARDWARE, as
arguments and hooks:

  name / link_id       the serial prefix on every line, the ESP-NOW board id
  comp / sys_canvas    the board's compositor and its SYSTEM canvas, already
                       constructed -- which class, at what size, is the glass's
  set_backlight        the panel light (GPIO and polarity are the board's)
  inp                  the board's InputState; its keyboards write into it
  inputs()             builds the board's pointer source and returns it (the
                       touch driver), called AFTER the splash's first frame so
                       a slow part -- the GSL3680's firmware upload -- happens
                       behind a lit screen. A board with more input hardware
                       builds the rest of it here too
  keyboard             the console's keyboard, or None: a BLE HID keyboard on
                       the touch-only boards, the C3 matrix on the T-Deck. It
                       is started after the WM if it can be
  ble_keyboard         an OPTIONAL SECOND keyboard beside a physical one
  seed_carts           the packed seed roster
  game_wh              the GAME canvas size, or None when the system canvas
                       IS the game canvas (the 320x240 tier)
  store_root / ota_dir the internal-flash arrangement. A board whose store can
                       live on a card supplies `load_carts(boot, store) ->
                       (carts, carts_root, update_dir)` instead
  with_sd              the store gate the web console's writes go through,
                       where the store shares a bus with the panel
  before_slim          board glue between the store hookup and the cart diet
  after_services       board glue once every shared service is wired
  make_audio           the audio backend factory, where the board has one
  wm                   the presentation tier to install over the fullscreen
                       stack, where the glass has room for windows
  c6_updater           the companion radio's updater class, where there is one
  extras / serial      the dev channel's board-only commands, and whether the
                       channel is built at all
  overlap / perf_emit  the PERF sampler's compositor counters and its sink
  log(tag, msg)        the boot's line sink; None prints with the board's name

Every hook the loop runs each frame -- poll_inputs / present / tail /
account / frame_error -- has a default here for the touch-only tier, and a
board with more hardware passes its own closures to `run()`, reading what it
needs off the returned `Desktop`.
"""

from console import Pointer, Workstation, wire_workstation_core
from device_boot import (DeviceBoot, FrameLoop, FramePump, IdleBlank,
                         OtaHealth, PerfSampler, apply_touch, poll_link,
                         poll_webhost)
from device_api import make_api
from device_canvas import DeviceCanvas, _LayerComp
from device_wifi import autoconnect_wifi, make_wifi


class Desktop:
    """What `build_desktop` assembled. The board's frame hooks read it, and
    `run()` is the shared frame loop over them."""

    def __init__(self, name):
        self.name = name
        self.loop = None          # the FrameLoop, set by run() before its first frame

    # -- the touch-only tier's frame hooks --------------------------------

    def poll_inputs(self, now):
        """The keyboard's async reports (before begin_frame, so InputState gets
        clean edges), the merge, then the touch into the pointer. Returns
        (click, active) for the loop's idle blank."""
        keyboard = self.keyboard
        if keyboard is not None:
            try:
                keyboard.poll()
            except Exception as exc:  # noqa: BLE001 -- a keyboard must fail touch-only
                print("%s keyboard poll failed:" % self.name, exc)
        inp = self.inp
        inp.begin_frame()
        touched, click = apply_touch(self.touch, self.pointer)
        return click, (touched or bool(inp._held) or bool(inp.last_key))

    def present(self):
        """Re-point both canvases at the compositor's new BACK buffer."""
        game = self.game
        if game is not self.sys_canvas:
            game.sync_back()       # off-screen: contract no-op
        self.sys_canvas.sync_back()

    def tail(self, now):
        """The per-frame services, at the frame TAIL: the web console, then
        the radio. Returns the webhost's elapsed ms."""
        ws = self.ws
        web = poll_webhost(ws)
        poll_link(ws)
        return web

    def frame_error(self, exc):
        print("%s frame error:" % self.name, exc)
        try:
            import sys
            sys.print_exception(exc)
        except Exception:  # noqa: BLE001
            pass
        import gc
        gc.collect()

    def run(self, poll_inputs=None, present=None, tail=None, account=None,
            frame_error=None):
        """The shared frame loop over this desktop, until Ctrl-C or `quit`."""
        loop = FrameLoop(self.ws, self.pump, self.pointer,
                         poll_inputs or self.poll_inputs,
                         idle=self.idle, serial=self.serial,
                         present=present or self.present,
                         tail=tail or self.tail,
                         account=account or self.perf.account,
                         frame_error=frame_error or self.frame_error,
                         set_backlight=self.set_backlight, lit=self.boot.lit)
        self.loop = loop
        if loop.run() == "quit":
            print("%s desktop: serial quit -> REPL" % self.name)
            return "quit"
        return None


def bt_command(keyboard, comp=None):
    """The `bt status|scan|forget|trace [0|1]` dev-channel extra over a BLE HID
    keyboard. `comp` adds the DSI underrun count where the compositor has one."""
    def _bt_cmd(ws, parts, line):
        action = parts[1] if len(parts) > 1 else "status"
        if action == "scan":
            print("REMOTE bt scan ->", keyboard.scan())
        elif action == "forget":
            keyboard.forget()
            print("REMOTE bt forgot keyboard + local bonds")
        elif action == "status":
            st = keyboard.status()
            underruns = getattr(comp, "underruns", None)
            print("REMOTE bt status state=%s name=%s passkey=%s "
                  "protocol=%s interval_ms=%s notify=%s fast=%s%s error=%s"
                  % (st[0], st[1], st[2], keyboard.protocol,
                     keyboard._conn_interval_ms, keyboard._notify_count,
                     keyboard.fast_status(),
                     "" if underruns is None
                     else " dsi_underruns=%s" % underruns(),
                     keyboard.error))
        elif action == "trace":
            on = not (len(parts) > 2 and parts[2] == "0")
            print("REMOTE bt trace ->", keyboard.trace(on))
        else:
            print("REMOTE bt ? %s" % line)
    return _bt_cmd


def build_desktop(name, link_id, comp, sys_canvas, set_backlight, inp, inputs,
                  keyboard, seed_carts, power_save_ms,
                  game_wh=None, font_scale=1, panel_diagonal_in=None,
                  store_root=None, ota_dir=None, load_carts=None, with_sd=None,
                  before_slim=None, after_services=None, make_audio=None,
                  ble_keyboard=None, wm=None, c6_updater=None, extras=None,
                  serial=True, overlap=None, perf_emit=print, log=None,
                  fps_cap=60):
    """Boot the shared console on a board (the module docstring is the
    contract). Returns the Desktop; the caller's `run()` is the loop."""
    board_log = log
    if log is None:
        def log(tag, msg):
            print("%s %s: %s" % (name, tag, msg))
    d = Desktop(name)
    gfx = comp.gfx()
    import moy_carts

    # DeviceBoot owns the boot splash + its progress bar, the cart seed/scan,
    # the Lua runtime probe and the "first frame in Nms" report. The panel
    # stays dark until a frame has composed (#45), so the splash is what
    # makes a slow boot legible on the glass and on the wire.
    boot = DeviceBoot(sys_canvas, comp, set_backlight, name)
    idle = IdleBlank(set_backlight, power_save_ms)
    boot.note("starting")
    if game_wh is None:
        game = sys_canvas
    else:
        # The fixed GAME canvas (#39): off-screen RGB565 over the same native
        # kernel; the WM composites it onto the system canvas.
        game = DeviceCanvas(_LayerComp(game_wh[0], game_wh[1], gfx))
    touch = inputs()
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer          # touch-driven carts read it via the api touch()

    boot.note("loading cartridges")
    if load_carts is None:
        carts, carts_root = boot.load_carts(moy_carts, seed_carts,
                                            root=store_root, media="flash")
        update_dir = ota_dir
    else:
        carts, carts_root, update_dir = load_carts(boot, moy_carts)

    boot.note("building the desktop")
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=font_scale,
                     panel_diagonal_in=panel_diagonal_in)
    # The chrome strip a quiet frame rotates beside the game rect on a
    # rotated compositor: the TALLEST bar the layout can draw.
    if getattr(comp, "rotated", False):
        comp.strip_h = max(ws.layout.status_h, ws.bar_layer._bar_h("desktop"))

    def _mk_game_canvas(w, h):
        # Per-run cart canvas (SPEC.md 1/3.1): a cart declaring a smaller
        # raster plays on its own off-screen canvas, which blit_game upscales.
        # No native kernel -> None, so the Player refuses the cart cleanly
        # instead of crawling per-pixel.
        if gfx is None:
            return None
        return DeviceCanvas(_LayerComp(int(w), int(h), gfx))

    ws.make_game_canvas = _mk_game_canvas
    # The Lua probe's line goes to the boot's own sink unless the board has a
    # ring to route it through.
    lua_log = None if board_log is None else (lambda m: log("carts", m))
    lua_runtime = boot.lua_runtime(ws, log=lua_log)
    # The shared service wiring: api/audio/lua + store/root/can_manage + WiFi
    # + the #66 slim_carts diet + pointer/keyboard + the boot loads, in the
    # ONE canonical order the host uses too.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          make_wifi(moy_carts, carts_root),
                          make_audio=make_audio, lua_runtime=lua_runtime,
                          before_slim=before_slim,
                          pointer=pointer, inp=inp, keyboard=keyboard)
    if ble_keyboard is not None:
        # A second keyboard beside the physical one (#26). Both write into the
        # same InputState, so the console never asks which one a key came
        # from; Settings finds it because _bt_service() checks ws.ble_keyboard
        # before ws.keyboard.
        ws.ble_keyboard = ble_keyboard

    # THE RADIO LINK (#7/#65): the console's one ESP-NOW owner. Built here,
    # INERT until a cart with the "multiplayer" permission runs (ws.link_arm
    # starts the radio; pm=PM_NONE costs power and a console on its shelf has
    # nobody to talk to).
    try:
        from moy_espnow import make_link
        ws.link = make_link(board=link_id,
                            name=ws.system.get("name", link_id))
        ws.net = ws.link.net
    except Exception as exc:  # noqa: BLE001 -- no radio must never cost a console
        log("boot", "link unavailable: %s" % exc)
        ws.link = None
    # OTA (#53): the image stages where the board's store said (update_dir),
    # and every write goes through the console's store gate -- the SD bracket
    # where the card shares the panel's bus, a plain call-through elsewhere.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(ws._with_sd, update_dir=update_dir)
    except Exception as exc:  # noqa: BLE001
        log("boot", "OTA updater unavailable: %s" % exc)
    if ws.updater is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:  # noqa: BLE001
            log("boot", "OTA wifi wiring failed: %s" % exc)
    if c6_updater is not None and ws.updater is not None:
        # The companion radio's own updater (#7/#58): Settings -> UPGRADE C6
        # RADIO. Failure is a missing Settings row, never a boot failure.
        try:
            ws.c6_updater = c6_updater(ws.updater)
        except Exception as exc:  # noqa: BLE001
            log("boot", "C6 updater unavailable: %s" % exc)
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        log("boot", "reboot hook unavailable: %s" % exc)
    # WEB CONSOLE (moycore plan 3.4 pull half): the wasm console baked into
    # the image, served from the board. Constructed, NOT started -- __init__
    # binds no socket, so injecting it only makes the Settings row appear.
    try:
        from moy_webhost import make_webhost
        ws.webhost = make_webhost(ws, carts_root,
                                  autoconnect=autoconnect_wifi,
                                  with_sd=with_sd)
    except Exception as exc:  # noqa: BLE001
        log("boot", "web console unavailable: %s" % exc)
    if after_services is not None:
        after_services(ws)
    if wm is not None:
        # The windowed tier (#73): installed AFTER the boot loads (the same
        # order as host build_workstation) so the persisted font scale is
        # applied before the root layout context is captured.
        ws.wm = wm(ws)
        ws.open_desk()
    if keyboard is not None and getattr(keyboard, "start", None) is not None:
        # A BLE keyboard starts its radio only now, after the Workstation's
        # boot allocations; a keyboard that answers from __init__ has no
        # start(). Failure is touch-only, never a boot failure.
        keyboard.start()

    ws._psave_ms = power_save_ms   # `state` reports the LIVE timeout
    serial_ch = None
    if serial:
        try:
            from dev_channel import DevChannel
            # env: what the `py` probe reaches beyond ws/wm/pointer. `touch`
            # is the board's live driver, so a finger on the glass and
            # `py touch.flip_x = True` calibrate without a REPL; `pump`
            # joins right after it is created.
            serial_ch = DevChannel(ws, pointer, set_backlight=set_backlight,
                                   idle=idle, extra=extras,
                                   env={"comp": comp, "game": game,
                                        "boot": boot, "touch": touch})
            log("boot", "serial dev channel %s"
                % ("armed" if serial_ch.armed else "unavailable"))
        except Exception as exc:  # noqa: BLE001 -- remote input is optional sugar
            log("boot", "serial channel unavailable: %s" % exc)
            serial_ch = None

    import gc
    gc.collect()
    # The OTA verdict before anything can overwrite the evidence (#53). The
    # rollback CONFIRM is not made here: reaching this line proves the desktop
    # was CONSTRUCTED, not that a pixel reached the glass (#56). FramePump.tail
    # fires it from the loop once frames are really going out.
    ota = OtaHealth(ws, log=lambda m: log("OTA", m))
    ota.boot_check()
    print("%s desktop running (Ctrl-C for REPL)" % name)
    boot.start_frames(ws)
    pump = FramePump(boot, ota, fps_cap)
    if serial_ch is not None:
        serial_ch.env["pump"] = pump
    # The METERS follow Settings -> PERF DIAG (#68 kid mode); the sampler
    # re-syncs it live. The PERF line itself is unconditional.
    ws.perf_capture = bool(getattr(ws, "diag_live", False))
    perf = PerfSampler(ws, overlap=overlap, emit=perf_emit)

    d.comp = comp
    d.sys_canvas = sys_canvas
    d.game = game
    d.gfx = gfx
    d.inp = inp
    d.touch = touch
    d.keyboard = keyboard
    d.pointer = pointer
    d.set_backlight = set_backlight
    d.boot = boot
    d.idle = idle
    d.ws = ws
    d.carts_root = carts_root
    d.serial = serial_ch
    d.pump = pump
    d.perf = perf
    return d
