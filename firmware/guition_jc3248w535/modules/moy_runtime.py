"""Moybyte Guition JC3248W535 device backend (#202): the shared console on the
board the port kit was built for.

The first FULLSCREEN-tier board where the system canvas is not the game
canvas: the system surface is the native 320x480 portrait glass (the #39
responsive layouts run at native res, exactly as they do in a P4 window) and
the game stays a fixed 320x240 DeviceCanvas that `wm.FullscreenStackWM`'s
composite_game centres 1:1-width -- the seam the P4 runs windowed, run
fullscreen for the first time, with no new code on this side of it: the base
`SystemCanvas` already carries blit_game/blit_cover, the WM already computes
the viewport, and this file only constructs the pieces.

Input is the P4's shape (touch-only, no poller thread, no keyboard modes),
storage is the P4's (internal-flash VFS -- SD is an open stage-4 decision),
the panel is this board's own (`guition_panel.GuitionCompositor` over
`moy_axs`). Everything else is the shared spine.
"""

from console import Pointer, Workstation, wire_workstation_core
from device_boot import (DeviceBoot, FrameLoop, FramePump, IdleBlank,
                         OtaHealth, apply_touch, poll_webhost)
from carts_data import CARTS   # build-time generated from system_carts/
from device_util import _ticks_ms, _ticks_diff
from device_api import make_api
from device_canvas import DeviceCanvas, SystemCanvas, _LayerComp
from device_wifi import autoconnect_wifi, make_wifi

GAME_W, GAME_H = 320, 240
FONT_SCALE = 1                 # 320px-wide glass: geometry, not magnification
# Internal-flash store root -- the P4's arrangement and the P4's hard-learned
# name rule: NOT "/moybyte/..." (a root-level VFS dir named like a frozen
# module SHADOWS it; '' precedes '.frozen' on sys.path).
CARTS_ROOT = "/moy/carts"
OTA_UPDATE_DIR = "/moy/update"

# Idle screen blank -- the shared IdleBlank, the shared 5 minutes.
POWER_SAVE_MS = 300000         # 0 disables


def run_desktop(fps_cap=60):
    """Boot the shared console: launcher + carts under FullscreenStackWM,
    AXS15231 touch as the pointer, carts on internal flash. The REPL stays
    alive under the desktop (#201's console arrangement), so Ctrl-C interrupts
    and the dev channel takes complete lines."""
    from guition_panel import GuitionCompositor, set_backlight
    from axs_touch import Touch
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
    # native kernel; composite_game centres it 1:1 on the 320x480 glass.
    game = DeviceCanvas(_LayerComp(GAME_W, GAME_H, gfx))
    inp = InputState()
    touch = Touch(sys_canvas.w, sys_canvas.h)
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer

    boot.note("loading cartridges")
    carts, carts_root = boot.load_carts(moy_carts, CARTS, root=CARTS_ROOT,
                                        media="flash")
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
                          pointer=pointer, inp=inp)
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
        from moy_webhost import make_webhost, P4_WEB_DIR
        ws.webhost = make_webhost(ws, carts_root, P4_WEB_DIR,
                                  autoconnect=autoconnect_wifi)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte Guition: web console unavailable:", exc)

    # The serial dev channel: ONE class, every board (runtime/dev_channel.py).
    # No board extras yet -- this board has no windowed tier and no BLE.
    ws._psave_ms = POWER_SAVE_MS
    try:
        from dev_channel import DevChannel
        serial = DevChannel(ws, pointer, set_backlight=set_backlight, idle=idle,
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
    # Perf sampler -- the P4's PERF line, verbatim shape (tools/p4_perf.py
    # parses it); the meters follow Settings -> PERF DIAG live.
    ws.perf_capture = bool(getattr(ws, "diag_live", False))
    _pf = {"at": _ticks_ms() + 2000, "n": 0, "busy": 0, "drawn": 0}

    def _poll_inputs(now):
        """This board's one input source: the AXS15231 pointer."""
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

    def _account(now, elapsed, sleep_ms):
        _pf["n"] += 1
        _pf["busy"] += elapsed
        if _ticks_diff(_ticks_ms(), _pf["at"]) >= 0:
            _drawn = getattr(ws, "_frames_drawn", 0)
            # Guarded like the P4's: a diag never kills the loop it measures.
            try:
                _live = bool(getattr(ws, "diag_live", False))
                if ws.perf_capture != _live:
                    ws.perf_capture = _live
                print("PERF fps=%d/%d busy=%dms draw=%.0f flush=%.0f logic=%.0f "
                      "render=%.0f chrome=%.0f cart=%s"
                      % ((_drawn - _pf["drawn"]) // 2, _pf["n"] // 2,
                         _pf["busy"] // (_pf["n"] or 1),
                         getattr(ws, "_draw_ms", 0), getattr(ws, "_flush_ms", 0),
                         getattr(ws, "_upd_ms", 0), getattr(ws, "_cart_ms", 0),
                         getattr(ws, "_chrome_ms", 0),
                         (getattr(ws, "cart", None) or {}).get("title", "-")))
            except Exception as _pf_exc:  # noqa: BLE001
                print("PERF sample failed: %s: %s"
                      % (type(_pf_exc).__name__, _pf_exc))
            _pf["at"] = _ticks_ms() + 2000
            _pf["n"] = 0
            _pf["busy"] = 0
            _pf["drawn"] = _drawn

    # The shared frame loop (#202 Phase B): the invariant order lives ONCE in
    # device_boot.FrameLoop; every hook above is this board's hardware.
    loop = FrameLoop(ws, pump, pointer, _poll_inputs, idle=idle, serial=serial,
                     present=_present, tail=_tail, account=_account,
                     frame_error=_frame_error,
                     set_backlight=set_backlight, lit=boot.lit)
    if loop.run() == "quit":
        print("Moybyte Guition desktop: serial quit -> REPL")
