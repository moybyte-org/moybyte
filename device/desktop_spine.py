"""The console desktop's boot spine: ONE body for every console board.

Four boards boot the same console, and the ORDER they boot it in is the
payload -- the boot splash before the store, the store before the Workstation,
the service wiring in `wire_workstation_core`'s one order, the windowed WM
after the boot loads, the OTA verdict before anything can overwrite it, the
kernel's frame loop last. `build_desktop` is that order written once;
`Desktop.run` hands the console to the kernel's loop
(native/moy_kernel/moy_loop.c) and returns. What a board supplies is its HARDWARE, as
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
  wm                   the presentation tier to install over the fullscreen
                       stack, where the glass has room for windows
  c6_updater           the companion radio's updater class, where there is one
  extras / serial      the dev channel's board-only commands, and whether the
                       channel is built at all
  log(tag, msg)        the boot's line sink; None prints with the board's name

The frame's stages are the kernel's C; what a board passes to `run()` is
which input hardware its stage reads (the T-Deck's trackball, its input task
kicked at the frame's tail) and a per-frame Python hook of its own.
"""

from console import Workstation, wire_workstation_core
from device_boot import DeviceBoot, report_update
from chrome import _cursor_delta
from idle_ladder import IdleLadder
from device_api import make_api
from device_canvas import DeviceCanvas, _LayerComp, _owner_h
import moy_input
import wire_input
import wire_links
from mem_census import mark as _census


class Desktop:
    """What `build_desktop` assembled, and `run()`, which hands it to the
    kernel's frame loop (native/moy_kernel/moy_loop.c)."""

    def __init__(self, name):
        self.name = name

    def run(self, ball=None, kick_at_tail=False, after_frame=None):
        """Hand the console to the kernel's loop and return this Desktop: the loop's stages
        are bound (the input stage over this console's pointer and drivers,
        the glass over its compositor), and the three console upcalls, the
        console's dev words and the services' Python half are registered as
        root pointers. The VM service runs the loop once main.py returns, as
        this task's outermost frame (docs/kernel_survival_2026-10.md 7.1).

        What each upcall carries beyond the console's own entry is the Python
        half of a stage the kernel cannot do for a VM object: the canvases
        re-pointed at the compositor's new back buffer (handle_input, after
        the kernel's present_pending), the trackball's pulses spent on a caret
        or the cursor (handle_input, the T-Deck), the boot's first-frame
        report and the console's PERF half (frame). `after_frame(drew)` is a
        board's per-frame Python inside the frame upcall (the T-Deck's offline
        diag), never an upcall of its own."""
        import moy_glass
        import moy_loop
        ws = self.ws
        boot = self.boot
        game = self.game
        sys_canvas = self.sys_canvas
        pointer = self.pointer
        nav = moy_input.nav if ball is not None else None
        ble = self.keyboard if self.keyboard is not None and hasattr(
            self.keyboard, "apply_mouse") else getattr(ws, "ble_keyboard", None)
        moy_input.loop_bind(pointer, touch=self.touch, ble=ble, ball=ball,
                            kick_at_tail=kick_at_tail)
        moy_glass.loop_bind(self.comp)
        cursor = _cursor_delta

        def handle_input():
            if game is not sys_canvas:
                game.sync_back()        # off-screen: contract no-op
            sys_canvas.sync_back()
            if nav is not None:
                n = nav()
                if n:
                    dx = n // 4096 - 2048
                    dy = n % 4096 - 2048
                    if not ws.nav(dx, dy):
                        pointer.move(cursor(dx), cursor(dy))
            ws.handle_input()

        drawn = [0]
        rung = [0]
        idle_state = moy_loop.idle_state

        def frame(dt):
            st = idle_state()
            if st != rung[0]:
                rung[0] = st
                ws.saver_state(st == moy_loop.SAVER)
            ws.frame(dt)
            n = ws._frames_drawn
            if not boot.done:
                boot.first_frame(ws)
            if ws.perf_capture and moy_loop.perf_due():
                ws.perf_push(moy_loop)
            if after_frame is not None:
                after_frame(n != drawn[0])
            drawn[0] = n
            return n

        serial = self.serial
        words = None
        if serial is not None:
            def words(line):
                return serial.word(ws, line)

        if serial is not None:
            # `pump.last` in the `py` scope: the clock at the top of the frame a
            # word runs in, which tools/p4_perf.py times the drawn-frame
            # counter against (the kernel's loop keeps it now).
            serial.env["pump"] = _FrameClock(moy_loop)
        service = make_service(ws, moy_loop)
        moy_loop.register(handle_input, ws.handle_pointer, frame, words, service)
        moy_loop.fps(self.fps_cap)
        moy_loop.lit(boot.lit)
        moy_loop.capture(ws.perf_capture)
        # The idle ladder's rungs: the system store's, the board's blank rung
        # the default (Settings rows over the kernel's ladder, idle_ladder.py).
        ws.idle_ladder = IdleLadder(ws, moy_loop, (self.power_save_ms or 0) // 1000)
        upd = getattr(ws, "updater", None)
        moy_loop.health(upd is not None and not getattr(upd, "confirmed", True))
        return self


class _FrameClock:
    """`pump.last` for the tools: the kernel loop's frame-top clock."""

    def __init__(self, loop):
        self._loop = loop

    @property
    def last(self):
        return self._loop.frame_at()


def make_service(ws, loop):
    """The services' Python half, one upcall a frame while any is live (the
    kernel's MOY_SVC_* bits): the webhost's routes while it joins, serves or
    says goodbye, the link's netplay drain while a match runs, and the OTA
    confirm's pending marker, once. Answers the bits it still wants."""
    def service(bits):
        keep = 0
        if bits & SVC_WEB:
            wh = getattr(ws, "webhost", None)
            if wh is not None:
                try:
                    wh.poll()
                except Exception as exc:  # noqa: BLE001 -- never break a frame
                    print("WEB ERR %s: %s" % (type(exc).__name__, exc))
        if bits & SVC_LINK:
            lk = getattr(ws, "link", None)
            if lk is not None and lk.active:
                lk.poll(ws)
                keep |= SVC_LINK
        if bits & SVC_HEALTHY:
            upd = getattr(ws, "updater", None)
            if upd is not None:
                try:
                    upd.confirmed_by_kernel()
                except Exception as exc:  # noqa: BLE001
                    print("Moybyte OTA: confirm failed: %s" % (exc,))
        return keep
    return service


SVC_WEB, SVC_LINK, SVC_UPDATE, SVC_HEALTHY = 1, 2, 4, 8


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
                     keyboard.conn_interval_ms, keyboard.notify_count,
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
                  before_slim=None, after_services=None,
                  ble_keyboard=None, wm=None, c6_updater=None, extras=None,
                  serial=True, log=None, fps_cap=60):
    """Boot the shared console on a board (the module docstring is the
    contract). Returns the Desktop; the caller's `run()` is the loop."""
    board_log = log
    if log is None:
        def log(tag, msg):
            print("%s %s: %s" % (name, tag, msg))
    _census("spine")
    d = Desktop(name)
    gfx = comp.gfx()
    import moy_carts
    import moy_catalogue

    # DeviceBoot owns the boot splash + its progress bar, the cart seed/scan,
    # the Lua runtime probe and the "first frame in Nms" report. The panel
    # stays dark until a frame has composed (#45), so the splash is what
    # makes a slow boot legible on the glass and on the wire.
    boot = DeviceBoot(sys_canvas, comp, set_backlight, name)
    boot.note("starting")
    if game_wh is None:
        game = sys_canvas
    else:
        # The fixed GAME canvas (#39): off-screen RGB565 over the same native
        # kernel; the WM composites it onto the system canvas.
        game = DeviceCanvas(_LayerComp(game_wh[0], game_wh[1], gfx))
    _census("splash")
    touch, pointer = wire_input.wire_pointer(inp, inputs, sys_canvas.w,
                                             sys_canvas.h)
    _census("inputs")

    boot.note("loading cartridges")
    if load_carts is None:
        carts, carts_root = boot.load_carts(moy_catalogue, seed_carts,
                                            root=store_root, media="flash")
        update_dir = ota_dir
    else:
        carts, carts_root, update_dir = load_carts(boot, moy_catalogue)

    _census("store")
    boot.note("building the desktop")
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=font_scale,
                     panel_diagonal_in=panel_diagonal_in)
    _census("workstation")
    # The chrome strip a quiet frame rotates beside the game rect on a
    # rotated compositor: the TALLEST bar the layout can draw.
    if getattr(comp, "rotated", False):
        comp.strip_h = max(ws.layout.status_h, ws.bar_layer._bar_h("desktop"))

    def _mk_game_canvas(w, h, owner=None):
        # Per-run cart canvas (SPEC.md 1/3.1): a cart declaring a smaller
        # raster plays on its own off-screen canvas, which blit_game upscales;
        # its pixels are on loan to `owner`, the run, and go back with it.
        # No native kernel -> None, so the Player refuses the cart cleanly
        # instead of crawling per-pixel.
        if gfx is None:
            return None
        own = 0 if owner is None else _owner_h(owner)
        return DeviceCanvas(_LayerComp(int(w), int(h), gfx, own))

    ws.make_game_canvas = _mk_game_canvas
    # The runtime probe's lines go to the boot's own sink unless the board has
    # a ring to route them through.
    rt_log = None if board_log is None else (lambda m: log("carts", m))
    runtimes = boot.runtimes(ws, log=rt_log)
    _census("runtimes")
    # The shared service wiring: api/audio/runtimes + store/root/can_manage + WiFi
    # + the #66 slim_carts diet + pointer/keyboard + the boot loads, in the
    # ONE canonical order the host uses too.
    wire_workstation_core(ws, moy_carts, carts_root, make_api,
                          wire_links.make_wifi(moy_carts, carts_root),
                          runtimes=runtimes,
                          before_slim=before_slim,
                          pointer=pointer, inp=inp, keyboard=keyboard)
    _census("wired")

    # The links: the radio, the updaters, Get Carts' network, the reboot
    # hook and the web console, in that order (device/wire_links.py).
    wire_links.wire_links(ws, link_id, update_dir, carts_root, with_sd,
                          c6_updater, log, _census)
    if after_services is not None:
        after_services(ws)
    _census("services")
    if wm is not None:
        # The windowed tier (#73): installed AFTER the boot loads (the same
        # order as host build_workstation) so the persisted font scale is
        # applied before the root layout context is captured.
        ws.wm = wm(ws)
        ws.open_desk()
        _census("wm")
    wire_input.start_keyboards(ws, keyboard, ble_keyboard, _census)
    # A soft reset under a serving web console leaves the kernel's webhost up
    # (native/moy_net/moy_webhost.c): this VM takes its radio lease and parks
    # the glass on it, as the switch that started it did.
    wh = getattr(ws, "webhost", None)
    try:
        if wh is not None and wh.adopt(ws):
            ws.park_web_console()
            log("boot", "web console still serving: adopted")
    except Exception as exc:  # noqa: BLE001 -- the console boots regardless
        log("boot", "web console adopt failed: %s" % exc)

    serial_ch = None
    if serial:
        try:
            from dev_channel import DevChannel
            # env: what the `py` probe reaches beyond ws/wm/pointer. `touch`
            # is the board's live driver, so a finger on the glass and
            # `py touch.flip_x = True` calibrate without a REPL.
            serial_ch = DevChannel(ws, pointer, extra=extras,
                                   env={"comp": comp, "game": game,
                                        "boot": boot, "touch": touch})
            log("boot", "serial dev channel %s"
                % ("armed" if serial_ch.armed else "unavailable"))
        except Exception as exc:  # noqa: BLE001 -- remote input is optional sugar
            log("boot", "serial channel unavailable: %s" % exc)
            serial_ch = None
    _census("dev channel")

    import gc
    gc.collect()
    # The OTA verdict before anything can overwrite the evidence (#53). The
    # rollback CONFIRM is not made here: reaching this line proves the desktop
    # was CONSTRUCTED, not that a pixel reached the glass (#56). The kernel's
    # loop fires it once frames are really going out.
    report_update(ws, lambda m: log("OTA", m))
    print("%s desktop running (Ctrl-C for REPL)" % name)
    _census("ota check")
    boot.start_frames(ws)
    _census("frames")
    # The METERS and the PERF line both follow Settings -> PERF DIAG (#68 kid
    # mode).
    ws.perf_capture = bool(getattr(ws, "diag_live", False))

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
    d.ws = ws
    d.carts_root = carts_root
    d.serial = serial_ch
    d.power_save_ms = power_save_ms
    d.fps_cap = fps_cap
    _census("spine done")
    return d
