"""Moybyte P4 device backend (#58): the shared console on the Waveshare 7B.

This is the P4 sibling of the T-Deck's moy_runtime, an order of magnitude
smaller because the board removed the walls the T-Deck backend exists to fight:
no flush ceiling (DPI scan-out), no SD<->display bus war (separate buses), no
keyboard mode-flipping (no keyboard yet -- USB-HID is the #58 follow-up), no
input-poller thread (nothing stalls the loop).

The two-domain seam (#39) runs for real here for the first time on hardware:

  * `P4SystemCanvas` -- the SYSTEM canvas: a DeviceCanvas (RGB565 + native
    moy_gfx) drawing DIRECTLY into the 1024x600 DSI scan-out framebuffer, plus
    what a system surface must add over a game canvas: a settings-chosen
    font_scale (native text kernel's scale arg), font-scale-carrying layers
    (window buffers, the bar cache), and the two native composite hooks the
    shared presentation code probes for -- blit_game (the windowed WM's
    game->window viewport, wm_windowed._blit_game) and blit_cover (the
    wallpaper's cover-crop desktop backdrop, wallpaper._backdrop_blit) -- each
    ONE moy_gfx.blit565_scale call over the game canvas's RGB565 buffer.
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

from console import Pointer, Workstation
from carts_data import CARTS   # build-time generated from system_carts/
from device_util import _ticks_ms, _ticks_diff
from device_api import make_api
from device_canvas import DeviceCanvas, _LayerComp, _FONT8, _FONT8_FIRST
from device_wifi import make_wifi

GAME_W, GAME_H = 320, 240
FONT_SCALE = 2                     # 7" 1024x600 default; persisted system.json overrides
# Internal-flash store root. NOT "/moybyte/..." -- a root-level dir named like an
# importable module SHADOWS the frozen module of that name ('' precedes '.frozen'
# on sys.path), and the first boot's seeded /moybyte dir broke the next boot's
# `from moybyte.input import ...` (hardware-learned 2026-07-08).
CARTS_ROOT = "/moy/carts"


class P4SystemCanvas(DeviceCanvas):
    """The P4 SYSTEM canvas: DeviceCanvas over the DSI framebuffer + the system-
    surface contract (#39/#73) -- font_scale text, font-scale layers, and the
    native game/wallpaper composite hooks the shared WM/wallpaper probe for."""

    def __init__(self, comp, font_scale=1):
        DeviceCanvas.__init__(self, comp)
        self.font_scale = max(1, int(font_scale))

    def set_font_scale(self, scale):
        self.font_scale = max(1, int(scale))

    def print(self, s, x, y, c, scale=1):
        # SystemCanvas.print (#39): petme128 at font_scale via the native text
        # kernel's scale arg. The legacy per-call `scale` arg stays ignored,
        # exactly like the host. framebuf fallback can't scale -- but a P4 build
        # always carries moy_gfx, so that path only ever renders 1x.
        fs = self.font_scale
        if fs <= 1 or self._gfx_text is None:
            DeviceCanvas.print(self, s, x, y, c)
            return
        self.flush_batch()
        self._gfx_text(self._buf, self.w, self.h, str(s), int(x), int(y),
                       self._col(c), _FONT8, _FONT8_FIRST, fs,
                       self._cam_x, self._cam_y,
                       self._clip_x0, self._clip_y0,
                       self._clip_x1, self._clip_y1)

    def new_layer(self, w, h, owner=None):
        # Font-scale-carrying layers (mirrors host SystemCanvas.new_layer): the
        # windowed WM's window buffers and the bar cache print through these, so
        # they must scale like the surface they composite onto. Same body as
        # DeviceCanvas.new_layer with the subclass constructed instead.
        try:
            import gc
            gc.collect()
        except Exception:  # noqa: BLE001
            pass
        lay = P4SystemCanvas(_LayerComp(int(w), int(h), self._gfx),
                             font_scale=self.font_scale)
        lay._nocache = True
        comp = lay._comp
        if owner is not None and comp.pooled:
            lent = self._lent_layers
            if lent is None:
                lent = self._lent_layers = {}
            lent.setdefault(owner, []).append((comp._buf, comp._nbytes))
        return lay

    # -- the native composite hooks (probed via getattr by the shared code) ----

    def blit_game(self, gc, ox, oy, scale):
        """wm_windowed._blit_game's device path (#58/#73): integer-scale the
        320x240 game canvas into this surface at (ox, oy) -- one C call."""
        fb = getattr(gc, "flush_batch", None)
        if fb is not None:
            fb()
        self.flush_batch()
        g = self._gfx
        if g is None:
            return
        g.blit565_scale(self._buf, self.w, self.h, int(ox), int(oy),
                        gc._buf, gc.w, gc.h, int(scale))

    def blit_cover(self, gc):
        """wallpaper._backdrop_blit's device path (#58): the smallest integer
        upscale of the 320x240 wallpaper frame that COVERS the whole desktop,
        centered + cropped (dx/dy <= 0; the C kernel clips). Same math as the
        host raster path."""
        gw, gh = gc.w, gc.h
        sw, sh = self.w, self.h
        scale = max(1, (sw + gw - 1) // gw, (sh + gh - 1) // gh)
        ox = (sw - gw * scale) // 2
        oy = (sh - gh * scale) // 2
        self.blit_game(gc, ox, oy, scale)


def _load_carts():
    """Load carts from the internal-flash store (seeding built-ins on first
    boot); fall back to the embedded CARTS on any store failure."""
    try:
        import moy_carts
        moy_carts.ensure_dirs(CARTS_ROOT)
        moy_carts.seed_builtins(CARTS, CARTS_ROOT)
        carts = moy_carts.scan(CARTS_ROOT)
        if carts:
            print("Moybyte P4 loaded %d carts from flash" % len(carts))
            return carts, CARTS_ROOT
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4 flash carts unavailable:", exc)
    print("Moybyte P4 using built-in carts")
    return [dict(c) for c in CARTS], None


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


def run_desktop(fps_cap=60):
    """Boot the shared console on the P4: launcher-as-desktop under WindowedWM,
    GT911 touch as the pointer, carts on internal flash. Ctrl-C over the CH343
    REPL interrupts the loop (no USB starvation on this board)."""
    from p4_display import P4Compositor, set_backlight
    from p4_input import Touch
    from moybyte.input import InputState
    from wm_windowed import WindowedWM
    import moy_carts

    comp = P4Compositor()
    gfx = comp.gfx()
    print("Moybyte P4 display up (%dx%d, gfx=%s)"
          % (comp.size()[0], comp.size()[1], "native" if gfx else "NONE"))
    sys_canvas = P4SystemCanvas(comp, font_scale=FONT_SCALE)
    # The fixed 320x240 GAME canvas (#39): off-screen RGB565 sharing the same
    # native kernel; the windowed WM composites it into the player window.
    game = DeviceCanvas(_LayerComp(GAME_W, GAME_H, gfx))
    inp = InputState()
    touch = Touch(sys_canvas.w, sys_canvas.h)
    pointer = Pointer(sys_canvas.w, sys_canvas.h)
    inp.pointer = pointer          # touch-driven carts read it via the api touch()

    carts, carts_root = _load_carts()
    ws = Workstation(comp, game, inp, carts,
                     sys_canvas=sys_canvas, font_scale=FONT_SCALE)
    ws.make_api = make_api
    ws.carts_store = moy_carts
    ws.carts_root = carts_root
    ws.can_manage = carts_root is not None   # internal VFS: no bus gymnastics,
    #                                          _with_sd stays the direct-call default
    ws.wifi = make_wifi(moy_carts, carts_root)   # C6-hosted WLAN is transparent
    #                                              to network.WLAN (bring-up-confirmed)
    ws.slim_carts()
    ws.pointer = pointer
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:  # noqa: BLE001
        print("Moybyte P4: reboot hook unavailable:", exc)
    ws.load_system()
    ws.load_icon_sheet()
    ws.load_achievements()
    # The P4 presentation tier (#73/#58): launcher = desktop, apps = windows.
    # Installed AFTER load_system (same order as host build_workstation) so the
    # persisted font scale is applied before the root layout context is captured.
    ws.wm = WindowedWM(ws)

    # Remote input over serial (#58 dev affordance): the CH343 REPL stays alive
    # under the desktop (no USB starvation on this board), so complete LINES piped
    # into the port drive the UI while the glass is watched:
    #   tap <x> <y>   synthetic tap at system-canvas coords
    #   tap sysmenu   tap a named bar button (any ws.layout.<name>_btn rect:
    #                 sysmenu / wifi / batt / context_x)
    #   quit          leave the desktop for the REPL
    # Ctrl-C still interrupts as before (handled below the stdin read).
    try:
        import sys
        import select
        _sin = select.poll()
        _sin.register(sys.stdin, select.POLLIN)
    except Exception:  # noqa: BLE001 -- remote input is optional sugar
        _sin = None

    import gc
    gc.collect()
    print("Moybyte P4 desktop running (Ctrl-C for REPL)")
    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    _backlight_on = False          # dark until the first composed frame (#45)
    # Perf sampler (#58 fps-ledger groundwork): serial is free on this board, so
    # print a PERF line every ~2s -- drawn-fps, average busy loop ms, and the
    # console's own draw/flush/logic/render/chrome EMAs (filled because
    # perf_capture is on). Costs two tick reads per frame.
    ws.perf_capture = True
    _pf_at = _ticks_ms() + 2000
    _pf_n = 0
    _pf_busy = 0
    _pf_drawn = 0
    ws.arm_splash()
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        inp.begin_frame()
        click = False
        tp = touch.poll()
        pointer.down = tp is not None
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:
                click = True
        if _sin is not None and _sin.poll(0):
            line = ""
            try:
                line = sys.stdin.readline().strip()
            except Exception:  # noqa: BLE001
                pass
            parts = line.split() if line else []
            if parts and parts[0] == "quit":
                print("REMOTE quit -> REPL")
                return
            if parts and parts[0] == "tap":
                r = None
                if len(parts) == 3:
                    try:
                        r = (int(parts[1]), int(parts[2]))
                    except ValueError:
                        r = None
                elif len(parts) == 2:
                    rect = getattr(ws.layout, parts[1] + "_btn", None)
                    if rect:
                        r = (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)
                if r is not None:
                    pointer.place(r[0], r[1])
                    pointer.down = True   # released next frame (touch reads None)
                    click = True
                    print("REMOTE tap %d %d" % r)
                else:
                    print("REMOTE ? %s" % line)
        pointer.click = click
        pointer.tick(now)
        game.sync_back()           # off-screen: contract no-op
        sys_canvas.sync_back()     # double-buffer: re-point at the new BACK fb
        try:
            ws.handle_input()
            ws.handle_pointer()
            ws.frame(dt)           # draw + composite + flush (cache msync)
        except KeyboardInterrupt:
            raise                  # Ctrl-C -> moybyte_shell -> REPL
        except Exception as exc:   # noqa: BLE001 -- one bad frame must not brick the boot
            print("Moybyte P4 frame error:", exc)
            gc.collect()
        if not _backlight_on and getattr(ws, "_frames_drawn", 0) > 0:
            set_backlight(True)
            _backlight_on = True
        elapsed = _ticks_diff(_ticks_ms(), now)
        _pf_n += 1
        _pf_busy += elapsed
        if _ticks_diff(_ticks_ms(), _pf_at) >= 0:
            _drawn = getattr(ws, "_frames_drawn", 0)
            print("PERF fps=%d/%d busy=%dms draw=%.0f flush=%.0f logic=%.0f "
                  "render=%.0f chrome=%.0f cart=%s"
                  % ((_drawn - _pf_drawn) // 2, _pf_n // 2,
                     _pf_busy // (_pf_n or 1),
                     ws._draw_ms, ws._flush_ms, ws._upd_ms, ws._cart_ms,
                     ws._chrome_ms,
                     (ws.cart or {}).get("title", "-")))
            _pf_at = _ticks_ms() + 2000
            _pf_n = 0
            _pf_busy = 0
            _pf_drawn = _drawn
        try:
            _fms = 1000 // ws.frame_cap_fps()
        except Exception:  # noqa: BLE001 -- pacing must never kill the loop
            _fms = frame_ms
        if _fms < frame_ms:
            _fms = frame_ms
        if elapsed < _fms:
            time.sleep_ms(_fms - elapsed)
