# MoyByte Zero -- the HEADLESS device backend (Seeed XIAO ESP32-S3).
#
# The Player/T-Deck backend (moy_runtime.DeviceCanvas) rasterizes into a compositor and
# flushes an RGB565 panel. The Zero has NO panel: its "canvas" only RECORDS the cart's
# draw-commands, which the web view streams to a browser that does the rasterizing
# ("the browser is the GPU", #41/#22). So this backend is pure Python -- no moy_gfx,
# no moy_compositor, no framebuffer, no flush.
#
# It reuses everything shareable rather than forking it:
#   * console.Workstation        -- the launcher/editors/cart runner (backend-agnostic)
#   * moy_runtime.make_api/Image  -- the cart API namespace + sprite holder (canvas-agnostic:
#                                    make_api just calls canvas.spr/map/... and builds Images)
#   * web_view.DrawRecorder/TeeCanvas/ServedState/assets_payload/apply_events -- the shared
#                                    draw-command protocol (identical to the T-Deck web view)
#   * moy_webserver.WebServer     -- the MicroPython socket + WebSocket transport
#
# The one new thing is the RECORDING canvas: a TeeCanvas whose "real" canvas is a no-op
# NullCanvas and whose recorder is always in record_only mode -- so draws never rasterize,
# they only accumulate as atlas-form commands (spr-by-index + serve-time defspr), exactly
# the bandwidth-appropriate device model.

import time

import moy_runtime as mr          # make_api, Image, PAL565, AUDIO_RATE, make_wifi, autoconnect_wifi
import web_view as wv
import moy_webserver
import moy_carts
import zero_net
from console import Pointer, Workstation
from moybyte.input import InputState
from carts_data import CARTS

W = 320
H = 240
AUDIO_RATE = getattr(mr, "AUDIO_RATE", 8000)

# Persistent store on the FLASH filesystem (the Zero has no SD). moy_carts is fully
# root-parameterized, so pointing `root` at a flash dir gives the same writable store the
# T-Deck gets on SD -- crucially, the WiFi cart's saved credentials (wifi.json) + system
# settings live here and survive a reboot, which is what lets the Zero come up in STA mode.
ZERO_ROOT = "/moybyte"

# mDNS hostname so a STA-mode Zero is reachable at http://moybyte.local without knowing its
# router-assigned IP (the headless Zero can't show it on a screen).
HOSTNAME = "moybyte"


# ---------------------------------------------------------------------------
# Null backend: the "real" canvas the recording TeeCanvas wraps. record_only is always on
# (a browser or not, the Zero never rasterizes), so the Tee never forwards a pixel op here
# -- but it DOES forward state ops (camera/clip/pal/palt/reset_state), pixel READS, and
# new_layer(), so those must exist and no-op. new_layer() returns a NullLayer the shared
# RecordingLayer wraps (it records the layer's own stream for a ship-once deflayer).
# ---------------------------------------------------------------------------


class _NullLayer:
    """A do-nothing off-screen layer. RecordingLayer wraps one and records the layer's own
    draw stream; every verb here is a no-op (nothing rasterizes). NO catch-all __getattr__: a
    catch-all that returned a callable would defeat `getattr(layer, 'font_scale', 1)` (the
    _blit_glyph read) -- it would get a bound method, not the default -- so VALUE attrs must be
    real. font_scale=1 mirrors a plain (game-scale) canvas."""

    font_scale = 1

    def __init__(self, w, h):
        self.w = w
        self.h = h

    def _noop(self, *a, **k):
        pass

    # every draw/state verb a layer can receive (RecordingLayer records the primitives + spr +
    # print itself and forwards the rest -- spr_tile/spr_batch/map/blit -- here):
    cls = pix = line = rect = rectb = circ = circb = spr = print = _noop
    camera = clip = pal = palt = reset_state = _noop
    spr_tile = spr_batch = map = flush_batch = _noop
    blit_window_from = blit_strip = _noop

    def new_layer(self, w, h):
        return _NullLayer(w, h)


class _NullCanvas:
    """A do-nothing drawing surface -- the "real" canvas the recording TeeCanvas wraps. While a
    browser is live the Tee is record_only (nothing reaches here); with NO browser the Tee
    FORWARDS pixel ops here, so every draw verb must exist as a no-op. Reads (pix, to_rgb888)
    return empty -- the console's device path never reads back pixels (sys_canvas is canvas, so
    _composite_game returns early without touching buf)."""

    font_scale = 1                  # a plain (game-scale) canvas; the console reads this

    def __init__(self, w, h):
        self.w = w
        self.h = h

    def _noop(self, *a, **k):
        pass

    # primitives + sprites + text (all no-op: the Zero never rasterizes)
    cls = line = rect = rectb = circ = circb = _noop
    spr = spr_tile = spr_batch = map = print = _noop
    blit_window_from = blit_strip = _noop

    def pix(self, x, y, c=None):
        return 0                    # read (c is None) -> 0; write -> ignored

    def reset_state(self):
        pass

    def camera(self, x=0, y=0):
        return (0, 0)

    def clip(self, x=None, y=None, w=None, h=None):
        pass

    def pal(self, c0=None, c1=None):
        pass

    def palt(self, c=None, on=None):
        pass

    def new_layer(self, w, h):
        return _NullLayer(w, h)

    def to_rgb888(self):
        return b""


class _NullComp:
    """Stand-in for the RGB565 compositor. The Workstation calls comp.flush()/sync(); on the
    Zero there is nothing to flush (the browser renders), so both are no-ops -- the mirror of
    the host's _NullComp."""

    def flush(self):
        pass

    def sync(self):
        pass


def make_record_canvas(w=W, h=H):
    """The Zero's ws.canvas: a TeeCanvas(NullCanvas, DrawRecorder) locked to record_only. Atlas
    form (self_contained False) -- make_api reuses one Image per (id, colorkey), so a bitmap
    ships ONCE as a serve-time defspr and each frame carries only a tiny spr-by-index (the same
    efficient stream the T-Deck web view sends). Returns (tee_canvas, recorder)."""
    rec = wv.DrawRecorder(w, h)
    rec.self_contained = False
    tee = wv.TeeCanvas(_NullCanvas(w, h), rec)
    return tee, rec


# ---------------------------------------------------------------------------
# ZeroWeb: the console<->web glue. It is BOTH the moy_webserver provider (assets/frame/apply)
# AND the browser pointer-sink (apply_events writes place()/down/click here, merged into the
# live Pointer once per frame in feed()). Mirrors moy_runtime.WebView, minus the on/off Tee
# swap + wallpaper rebind the Zero doesn't need (its canvas is ALWAYS the recorder).
# ---------------------------------------------------------------------------


class ZeroWeb:
    def __init__(self, ws, inp, pointer, recorder, canvas):
        self._ws = ws
        self._inp = inp
        self._pointer = pointer
        self._rec = recorder
        self._canvas = canvas
        # browser input, applied each frame in feed() (queued during server.poll()):
        self._held = set()          # held buttons (joystick / WASD)
        self._press = []            # one-shot presses
        self._keys = []             # typed key bytes (one per frame)
        self._bx = 0
        self._by = 0
        self._bdown = False
        self._bclick = False

    # -- moy_webserver provider protocol -------------------------------------
    def assets(self):
        ws = self._ws
        cart = getattr(ws, "cart", None)
        title = cart.get("title") if cart else None
        decoded = {}
        raw = getattr(ws, "images", None)
        if raw:
            for name in raw:
                dec = mr._decode_moyimg(raw[name])
                if dec is not None:
                    decoded[name] = dec
        return wv.assets_payload(self._canvas.w, self._canvas.h, mr.PAL565,
                                 getattr(ws, "sheet", None), getattr(ws, "tilemap", None),
                                 title, AUDIO_RATE, decoded or None)

    def frame(self):
        cart = getattr(self._ws, "cart", None)
        title = cart.get("title") if cart else None
        return (self._rec.frame(), title)

    def apply(self, events):
        # The Zero has no physical pointer to clobber, so browser pointer events write a
        # deferred sink (self) merged in feed(); buttons/keys/pan go through the hooks.
        wv.apply_events(events, self._inp, self,
                        on_press=self._on_press, on_pan=self._on_pan,
                        on_key=self._on_key, on_esc=self._on_esc, on_hold=self._on_hold)

    # -- pointer-sink shape (apply_events calls place()/.down/.click) ---------
    def place(self, x, y):
        self._bx = int(x)
        self._by = int(y)

    @property
    def down(self):
        return self._bdown

    @down.setter
    def down(self, v):
        self._bdown = bool(v)

    @property
    def click(self):
        return self._bclick

    @click.setter
    def click(self, v):
        if v:
            self._bclick = True

    # -- button / key / esc hooks --------------------------------------------
    def _on_press(self, name):
        self._press.append(name)

    def _on_hold(self, name, down):
        if down:
            self._held.add(name)
        else:
            self._held.discard(name)

    def _on_key(self, code):
        self._keys.append(code)

    def _on_pan(self, dx, dy):
        self._pointer.move(dx, dy)

    def _on_esc(self):
        try:
            if self._ws.screen == "menu":
                self._ws._leave_menu()
        except Exception:
            pass

    # -- per-frame input apply (before inp.begin_frame) ----------------------
    def feed(self, now):
        inp = self._inp
        # Re-assert the browser-held + one-shot buttons (the Zero has no keyboard.poll to
        # clear them). release_all first so a released button drops. Guard unknown names
        # (run/home are console nav, not cart BUTTONS -> set_button would raise).
        inp.release_all()
        for n in self._held:
            try:
                inp.set_button(n, True)
            except Exception:
                pass
        for n in self._press:
            try:
                inp.set_button(n, True)
            except Exception:
                pass
        self._press = []
        inp.last_key = self._keys.pop(0) if self._keys else 0
        # Merge the browser pointer into the live cursor; consume the one-shot click.
        p = self._pointer
        p.place(self._bx, self._by)
        p.down = self._bdown
        if self._bclick:
            p.click = True
            self._bclick = False
        p.tick(now)


# ---------------------------------------------------------------------------
# Boot the headless console.
# ---------------------------------------------------------------------------


def _ticks_ms():
    return time.ticks_ms() if hasattr(time, "ticks_ms") else int(time.time() * 1000)


def _ticks_diff(a, b):
    return time.ticks_diff(a, b) if hasattr(time, "ticks_diff") else (a - b)


def build_workstation():
    """Assemble the headless Workstation + web glue. Returns (ws, web, recorder). Carts are the
    embedded CARTS (read-only for now); the flash store (ZERO_ROOT) holds WiFi creds + system
    settings so the WiFi cart's saved network survives a reboot."""
    canvas, rec = make_record_canvas()
    inp = InputState()
    pointer = Pointer(W, H)
    inp.pointer = pointer
    ws = Workstation(_NullComp(), canvas, inp, [dict(c) for c in CARTS])
    ws.make_api = mr.make_api          # cart namespace (Image sprites, canvas-agnostic)
    ws.pointer = pointer
    # Flash-backed persistence: ensure the store dir exists, then wire it. moy_carts ops run
    # directly (no SD bus on the Zero). Carts stay embedded (can_manage off -> cart editing is
    # M2), but WiFi creds + system settings persist to ZERO_ROOT.
    root = None
    try:
        moy_carts.ensure_dirs(ZERO_ROOT)
        root = ZERO_ROOT
    except Exception as exc:
        print("[zero] flash store unavailable:", exc)
    ws.carts_store = moy_carts
    ws.carts_root = root
    ws.can_manage = False
    ws._with_sd = lambda fn: fn()
    # WiFi service (#38): the SAME DeviceWifi the T-Deck uses, over the flash store. The WiFi
    # cart (a system cart with the "network" permission) scans/connects/saves through this; creds
    # persist to <root>/wifi.json so autoconnect_wifi() brings up STA on the next boot.
    ws.wifi = mr.make_wifi(moy_carts, root)
    try:
        import machine
        ws.reboot_hook = machine.reset      # ≡ menu -> Reboot, so a newly-saved network takes effect
    except Exception:
        pass
    for setup in ("load_system", "load_icon_sheet", "load_achievements"):
        fn = getattr(ws, setup, None)
        if fn is not None:
            try:
                fn()
            except Exception as exc:
                print("[zero]", setup, "skipped:", exc)
    web = ZeroWeb(ws, inp, pointer, rec, canvas)
    return ws, web, rec


def _bring_up_network(wifi):
    """Prefer JOINING a saved WiFi (STA -- the T-Deck's mode, far better streaming than an ESP32
    SoftAP); fall back to HOSTING our own AP so the WiFi cart can provision one. Returns
    (ip, label). The AP<->STA gap is why an AP-mode Zero stutters where the T-Deck doesn't."""
    try:
        import network
        network.hostname(HOSTNAME)          # mDNS: reach a STA Zero at http://moybyte.local
    except Exception:
        pass
    if wifi is not None:
        try:
            if mr.autoconnect_wifi(wifi):    # tries saved creds, most-recent first
                connected, ssid, ip = wifi.status()
                if connected and ip:
                    return ip, "joined %s -- http://%s  (or http://%s.local)" % (
                        ssid or "WiFi", ip, HOSTNAME)
        except Exception as exc:
            print("[zero] STA autoconnect failed:", exc)
    # No saved network (or it didn't come up) -> host our own so the WiFi cart can set one.
    ip = zero_net.start_ap()
    return ip, "hosting %s (key %s) -- http://%s" % (zero_net.AP_SSID, zero_net.AP_KEY, ip)


def _banner(netlabel):
    print("=" * 46)
    print("  MoyByte Zero")
    print("  " + netlabel)
    print("=" * 46)


def run_zero(port=80, fps_cap=30):
    """The headless desktop loop: bring up the network (STA from saved creds, else host an AP),
    then draw the console into the recording canvas and serve the stream to a browser.
    Single-threaded; the web server is serviced BETWEEN frames, fully non-blocking."""
    ws, web, rec = build_workstation()
    ip, netlabel = _bring_up_network(ws.wifi)
    _banner(netlabel)
    server = moy_webserver.WebServer(rec, web, port)
    if not server.start(ip):
        print("[zero] web server failed to start")
        return
    print("[zero] console streaming (port %d)" % port)

    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    ws.arm_splash()                    # boot logo before the launcher
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        server.begin_frame()           # arm the recorder gate for this frame (no-op if no browser)
        web.feed(now)                  # apply queued browser input
        inp = ws.input
        inp.begin_frame()              # compute button edges from the re-asserted held set
        drew_before = getattr(ws, "_frames_drawn", 0)
        try:
            ws.handle_input()
            ws.handle_pointer()
            ws.frame(dt)               # draw into the recording canvas (never flushes)
        except Exception as exc:
            print("[zero] frame error:", exc)
        if getattr(ws, "_frames_drawn", 0) != drew_before:
            server.commit_frame()      # publish this frame's draw commands
        server.poll()                  # accept conns + drain WS input + push the latest frame
        elapsed = _ticks_diff(_ticks_ms(), now)
        if elapsed < frame_ms:
            time.sleep_ms(frame_ms - elapsed)
