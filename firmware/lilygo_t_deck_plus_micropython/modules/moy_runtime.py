# Moybyte v0.4 workstation -- DEVICE side.
#
# Boots the fantasy workstation on the T-Deck: a cartridge launcher + the carts,
# navigated with the keyboard/trackball, each cart drawn through the native
# moy_compositor. The drawing API (cls/pix/rect/rectb/circ/circb/spr/print/btn/...,
# TIC-80 style: rect/circ are filled, rectb/circb are outlines) matches the host
# `runtime/` reference, so cartridges are portable; only the
# canvas backend differs (framebuf over the compositor buffer + palette->RGB565).
#
# v1 embeds the cart sources; loading real .moy files from SD is the follow-on.

import time
from array import array

# Editor cores (CodeEditor / SpriteSheet / PaintEditor) are backend-agnostic and
# shared verbatim with the host (canonical: runtime/editors.py; build.sh stages a
# copy into modules/ so it freezes here as the top-level module `editors`).
from editors import CodeEditor, PaintEditor, SpriteSheet
from console import NAMES, Pointer, Workstation, _cursor_delta, color
from carts_data import CARTS  # build-time generated from system_carts/ (tools/gen_device_carts.py)
# Leaf tick + diag helpers (extracted to device_util.py so every device cluster can
# import them without a moy_runtime cycle -- see device_util.py's module docstring).
from device_util import (
    _ticks_ms, _ticks_diff, _ticks_us, _diag_note, _diag_log,
)
# The device WiFi service (#38, extracted to device_wifi.py). run_desktop calls
# make_wifi()/autoconnect_wifi(); DeviceWifi is the injected `wifi` backend.
from device_wifi import DeviceWifi, make_wifi, autoconnect_wifi
# The pointer input drivers (extracted to device_input.py): the trackball + GT911
# touch. run_desktop constructs TrackBall()/Touch(...) and feeds them to Pointer.
from device_input import TrackBall, Touch
# Serial diagnostics (extracted to device_diag.py): the between-frames logging
# functions run_desktop calls when perf capture is on. HITCH_MS + _CALIB_DONE are
# the loop's hitch threshold + one-shot calib flag (mutated in place).
from device_diag import (
    _diag_flush, _diag_perf_sample, _diag_hitch, _diag_drawbrk, _diag_draw2,
    _diag_chromebrk, _diag_pump, _diag_i2cstat, _diag_calib, _diag_gc,
    HITCH_MS, _CALIB_DONE,
)
# The device WEB VIEW controller (#41/#22, extracted to device_webview.py).
# run_desktop constructs WebView(...) and services it between frames.
from device_webview import WebView
# The device AUDIO backend (#16, extracted to device_audio.py). run_desktop wires
# ws.make_audio = make_audio; DeviceAudio is the injected I2S backend.
from device_audio import DeviceAudio, make_audio
# The device DRAWING backend (extracted to device_canvas.py): the indexed canvas +
# scroll layers + sprite images. run_desktop constructs DeviceCanvas(comp);
# make_api uses Image / _decode_moyimg.
from device_canvas import DeviceCanvas, Image, _decode_moyimg, _Layer

# --- the device DRAWING backend now lives in device_canvas.py (DeviceCanvas +
# Image + _decode_moyimg + _LayerComp/_Layer + the PAL565/native-detection consts),
# imported at the top of this module. run_desktop constructs DeviceCanvas(comp);
# make_api uses Image / _decode_moyimg.


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None, images=None, owner="cart"):
    import random

    _img_cache = {}        # name -> decoded paint Image (see image() below), so a
                           # repeated image(name) returns the SAME Image (#63) and its
                           # RGB565 bake cache survives across frames.
    tile_cache = {}        # (tile id, colorkey) -> Image, so a redrawn sheet sprite
                           # reuses one Image (and its RGB565 blit cache) every frame
                           # instead of rebuilding it. Invalidated when the sheet's
                           # gen counter changes (a paint edit), so a live sprite edit
                           # shows fresh art instead of stale cached pixels.
    _cache_gen = [None]

    def cfg(key, default=None):
        return config.get(key, default)

    # Audio (#16): same names/signature as the host make_api. Bound to the injected
    # device audio backend (DeviceAudio / a silent fallback); no-op if absent so a
    # cart's sfx()/beep()/music() never crash when audio isn't wired.
    def _sfx(n, chan=None):
        if audio is not None:
            audio.sfx(n, chan)

    def _beep(freq, dur=0.15):
        if audio is not None:
            audio.beep(freq, dur)

    def _music(track, loop=True):
        if audio is not None:
            audio.music(track, loop)

    def _music_stop():
        if audio is not None:
            audio.music_stop()

    def _sound_stop(chan=None):
        if audio is not None:
            audio.sound_stop(chan)

    def _volume(level):
        if audio is not None:
            audio.volume(level)

    def spr(n, x, y, colorkey=-1, scale=1, flip=0, w=1, h=1):
        # TIC-80 spr(id, x, y[, colorkey, scale, flip, w, h]) from the cart's sheet.
        # w/h are the tile span: spr(n, x, y, w=2, h=2) draws the 16x16 multi-tile
        # sprite whose top-left is tile n (#30). flip (0=none, 1=h, 2=v, 3=both, #11)
        # mirrors the sprite. w=h=1, flip=0 is the plain 8x8 sprite. Also accepts an
        # Image directly (ASCII-art sprites); then a 4th positional is treated as
        # scale, e.g. spr(pet, x, y, scale=4).
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale, flip)
        if sheet is None:
            return
        if w > 1 or h > 1:
            # Multi-tile sprite: resolve the span Image and draw it immediately (the
            # canvas flushes any pending 1x1 auto-batch first). Cached per (n,ck,w,h).
            g = getattr(sheet, "gen", 0)
            if g != _cache_gen[0]:
                tile_cache.clear()
                _cache_gen[0] = g
            ck = (int(n), colorkey, int(w), int(h))
            img = tile_cache.get(ck)
            if img is None:
                img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
                if img is None:
                    return
                tile_cache[ck] = img
            canvas.spr(img, x, y, scale, flip)
            return
        # Plain 1x1 sheet tile: auto-batch (#63). The canvas queues it and coalesces a
        # contiguous run into one native blit_batch, flushing on any state break.
        canvas.spr_tile(sheet, int(n), x, y, colorkey, scale, flip)

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(): blit a region of the cart's tilemap over the sheet (#32).
        # Same signature/semantics as the host make_api -- one native blit_map call.
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    def spr_batch(items, colorkey=-1, scale=1):
        # spr_batch(items[, colorkey, scale]): draw MANY sheet tiles in ONE native
        # call (#43) -- the sprite analogue of map(). `items` is a sequence of
        # (tile, x, y) or (tile, x, y, flip) tuples (flip 0=none/1=h/2=v/3=both,
        # like spr()); `colorkey` + `scale` apply uniformly to the whole batch. Coords
        # are world space (the camera offsets each; the clip rect is honoured) and the
        # tiles come from the cart's sheet -- the SAME RGB565 atlas map() uses, so the
        # cost is one C walk over the items instead of N per-sprite MP->C blits. This
        # is the lever for explosion-heavy frames (the per-sprite draw-call count is
        # the device's FPS bottleneck). SHEET TILES ONLY, 1x1 tiles: Image sprites and
        # multi-tile (w/h>1) sprites still use spr(). No-op when the cart has no sheet.
        if sheet is None:
            return
        # No tile-cache refresh needed (unlike spr()): the atlas is keyed on sheet.gen,
        # so _sheet_atlas rebakes itself after a live paint edit.
        canvas.spr_batch(sheet, items, colorkey, scale)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    def touch():
        # GT911 pointer exposed to touch-driven carts: (x, y, tapped, held) this
        # frame, or None when there is no pointer. `tapped` is the press edge so a
        # cart scores at most one hit per tap; `held` stays True while the finger
        # is on the glass (run_desktop drives pointer.down from the GT911 poll), so
        # a cart can track a DRAG (drawing, sliders). Same contract as the host.
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click), bool(getattr(p, "down", False)))

    def mouse():
        # TIC-80-shaped 7-tuple (x, y, left, middle, right, scrollx, scrolly)
        # aliasing touch(): tap -> left button. The touchscreen has no
        # middle/right/scroll, so those are constant 0/False.
        p = getattr(input, "pointer", None)
        if p is None:
            return (0, 0, False, False, False, 0, 0)
        return (p.x, p.y, bool(p.click), False, False, 0, 0)

    def time():
        # Milliseconds since the cart started (set by Workstation._start).
        start = getattr(input, "cart_start_ms", 0)
        return _ticks_diff(_ticks_ms(), start)

    def key(code=None):
        # key([code]) -> is that ASCII key held this frame (key(ord("a"))). The
        # T-Deck keyboard reports one byte per frame, so key() tracks that single
        # last key, not a full held-set: only one key reads as down at a time. With
        # no arg, returns the last key code (0 when nothing is down).
        cur = getattr(input, "cart_key", 0)
        if code is None:
            return cur
        return cur == int(code)

    def keyp(code=None):
        # keyp([code]) -> pressed THIS frame (the 0->key edge). Same single-key
        # limitation as key(); no auto-repeat hold/period args.
        edge = getattr(input, "cart_keyp", 0)
        if code is None:
            return edge
        return edge == int(code)

    def textmode(on=True):
        # textmode([on]) -> opt a RUNNING cart into TEXT-keyboard input (#38/#42).
        # By default a running cart is in GAME mode: the T-Deck keyboard is in raw
        # matrix mode so a held WASD/arrow keeps driving btn() (true hold-to-move),
        # but it yields no clean typeable ASCII. Call textmode(True) to switch to
        # text mode -- the Workstation flips the keyboard to clean 1-byte ASCII so
        # key()/keyp() return typeable bytes (a password, a name); textmode(False)
        # restores game mode. Same name + behavior on the host (host_app). Resets to
        # game mode automatically when the cart exits. (On older keyboard firmware
        # that ignores raw mode the keyboard is always ASCII; textmode is then a
        # no-op flip but key()/keyp() still work via the hold-latch path.)
        input.text_mode = bool(on)

    def _quit():
        # quit() -> END this cart and return to whoever launched it (the launcher, or
        # the Editor). A cart calls it from a key or an on-screen affordance it draws.
        # This is how a TEXT-mode cart exits: once it calls textmode(True), the console's
        # hold-BACKSPACE game-exit can't reach it (BACKSPACE is a typed 0x08 the cart
        # reads as delete, and the T-Deck keyboard has no autorepeat, so the ~700ms hold
        # never accumulates) -- so a textmode(True) cart MUST provide its own exit via
        # quit(). ADDITIVE to the frozen kid API, works for ANY cart type, same name +
        # behavior on the host (host_app). Sets a flag the Player honors AFTER this
        # frame's _update runs (player.tick), popping to the run caller via
        # ws._exit_to_caller(). `quit` shadows the site builtin inside the cart's exec
        # namespace, resolving to this closure.
        input.cart_quit = True

    def pmem_fn(index, value=None):
        # TIC-80 pmem(i[, v]): read pmem(i) -> int, write pmem(i, v) -> persists.
        if pmem is None:
            return 0
        return pmem.cell(index, value)

    def make_layer(w, h):
        # make_layer(w, h) -> a scroll background (#54): a wider off-screen canvas the
        # cart pre-renders a level into ONCE (with the SAME verbs -- cls/map/spr/rect/
        # circ/print/...), then window-copies to the screen each frame via draw_layer.
        # Replaces a per-frame full-background re-render (map() over a scrolling level,
        # ~12-14ms) with a flat memory copy (~7ms) -- the lever for ~60fps scrollers.
        lc = canvas.new_layer(w, h, owner=owner)   # #63: lent to this program (leak fix)
        lns = make_api(lc, input, config, sheet, audio, tilemap, pmem, wifi, images,
                       owner=owner)
        return _Layer(lc, lns)

    def draw_layer(layer, cam_x=0, cam_y=0):
        # draw_layer(layer, cam_x, cam_y): blit the visible W x H window of `layer` at
        # the camera offset into the framebuffer (this frame's background; draw actors
        # on top afterwards). The camera is clamped to [0, layer - screen] so the full
        # window always lands -- no torn edge at the world boundary.
        lc = layer._canvas
        cx = int(cam_x)
        cy = int(cam_y)
        maxx = lc.w - canvas.w
        maxy = lc.h - canvas.h
        if cx < 0:
            cx = 0
        elif maxx > 0 and cx > maxx:
            cx = maxx
        if cy < 0:
            cy = 0
        elif maxy > 0 and cy > maxy:
            cy = maxy
        canvas.blit_window_from(lc, cx, cy)

    def image(a, mapping=None, transparent="."):
        # Two forms, dispatched on the first arg (str vs ASCII rows) -- host==device:
        #   image("bg")          -> the cart's paint-image asset images/bg.moyimg as a
        #     big Image (a 64-colour MOY64 index bitmap), placed with spr(img, x, y).
        #     The #63 Fold 3 background path; DeviceCanvas.spr bakes it index->565 ONCE
        #     via blit_indices. None when the cart has no such image; the SAME Image is
        #     returned across calls (memoised) so its 565 bake survives frames.
        #   image(rows, mapping) -> build a small Image from ASCII art (kid convenience).
        if isinstance(a, str):
            im = _img_cache.get(a)
            if im is None:
                blob = images.get(a) if images else None
                if blob is None:
                    return None
                dec = _decode_moyimg(blob)
                if dec is None:
                    return None
                w, h, idx = dec
                im = Image(w, h, idx, -1)      # opaque (no transparent index)
                im._paint = True               # marks the paint-image bake/ship fast paths
                im._name = a                   # web view (#63 Fold 4): spr() ships ["imgref",
                                               # x, y, name]; the pixels ride /assets, not the frame
                _img_cache[a] = im
            return im
        return Image.from_ascii(a, mapping, transparent)

    # #63: hand the kid the NATIVE spr fast path when available. The C gate parses
    # (n, x, y[, colorkey[, scale[, flip]]]) and appends to the canvas batch array
    # with no Python call frame -- the fix for the warm-heap frame-spill pathology
    # that made a 120-sprite kid loop cost ~150ms/frame (see make_spr_gate). The
    # Python closure above stays as its fallback (Image sprites, w/h spans, kwargs)
    # and as the whole path off-gfx (host parity, web TeeCanvas), so pixels and
    # semantics are identical either way. Kid code never changes: it's still spr().
    _spr_entry = spr
    _mkgate = getattr(canvas, "make_spr_gate", None)
    if _mkgate is not None:
        _gate = _mkgate(sheet, spr)
        if _gate is not None and callable(_gate):
            _spr_entry = _gate

    # Declared background (#63 fast-by-default -- the "software PPU layer 0", mirrors
    # host_app.make_api): the cart names its backdrop ONCE (a color or a painted
    # Image); the engine restores it at the START of every frame via the Player's
    # ns["_moy_restore_bg"] hook. An Image bakes into a hidden full-screen layer once;
    # the per-frame restore is one draw_layer window copy -- the full-screen shape the
    # async GDMA restore predicts, so on-device the backdrop costs ~0 visible ms.
    _bg = [None]

    def background(x=None):
        if x is None:
            _bg[0] = None
        elif isinstance(x, Image):
            lay = make_layer(canvas.w, canvas.h)
            lay.spr(x, 0, 0)               # bake once (paint images take blit_indices)
            _bg[0] = ("l", lay)
        else:
            _bg[0] = ("c", color(x))

    def _restore_bg():
        b = _bg[0]
        if b is not None:
            if b[0] == "c":
                canvas.cls(b[1])
            else:
                draw_layer(b[1], 0, 0)

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": _spr_entry,
        "spr_batch": spr_batch,
        "background": background, "_moy_restore_bg": _restore_bg,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": input.held, "btnp": input.pressed,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode, "quit": _quit,
        "cfg": cfg, "col": color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": image,
    }
    # Capability-gated network API (#38): the shared Workstation passes a non-None
    # wifi backend ONLY for a cart with the "network" permission, so a normal kid
    # cart's namespace never carries `wifi` (the base key-set is identical here and
    # on the host).
    if wifi is not None:
        ns["wifi"] = wifi
    return ns


# --- Audio backend (#16) now lives in device_audio.py (DeviceAudio + make_audio +
# the MOY_AUDIO_CORE1 / I2S_* / AUDIO_* consts), imported at the top of this module.
# run_desktop wires ws.make_audio = make_audio.


# --- WiFi service (#38) now lives in device_wifi.py (DeviceWifi + make_wifi +
# autoconnect_wifi), imported at the top of this module. run_desktop still calls
# make_wifi()/autoconnect_wifi() to build + boot-connect the injected wifi service.

# --- pointer input drivers now live in device_input.py (TrackBall + the GT911
# Touch + the TOUCH_* calibration constants), imported at the top of this module.
# run_desktop constructs them and (in poller mode) pokes touch._source.

# --- #69 the input-poller thread knob ----------------------------------------
#
# THE keyboard/touch stall fix: moybyte.input.InputPoller (see its docstring for
# the full story) owns every I2C0 transaction on a dedicated Python thread; the
# frame loop only consumes staged state, so a C3 clock-stretch stall (40-60ms,
# I2CSTAT-sized) blocks the poller thread instead of a frame. Needs the build's
# I2C GIL-release patch (esp32_i2c_gil_release.patch) to actually isolate the
# stall -- without it the stall holds the GIL and freezes the loop from any
# thread (the poller is then harmless, just useless). Set False -- or lose
# _thread, or let the thread die -- and run_desktop stays on / falls back to
# the synchronous keyboard.poll()/touch path, exactly the pre-poller behavior
# (revert with NO rebuild, same pattern as MOY_AUDIO_CORE1).
MOY_INPUT_POLLER = True


# _ticks_ms/_ticks_diff/_ticks_us + _diag_note/_diag_log now live in the leaf
# device_util.py (imported at the top of this module), so extracted device
# clusters can share them without a moy_runtime import cycle.

# --- serial diagnostics now live in device_diag.py (_diag_flush / _diag_perf_sample
# / _diag_hitch / _diag_drawbrk / _diag_draw2 / _diag_chromebrk / _diag_pump /
# _diag_i2cstat / _diag_calib / _diag_gc), imported at the top of this module.
# run_desktop calls them between frames when perf capture is on.


def _load_carts(session=None):
    """Load cartridges from SD (seeding the built-ins on first boot). Returns
    (carts, carts_root); carts_root is None (management disabled) on fallback to
    the embedded carts if the SD card is missing/unreadable.

    `session` is the SD lifecycle wrapper to mount under. Default is the
    pre-display machine.SDCard path (used by the boot prefetch); pass
    moybyte_sd.with_sd_live for the post-display native path."""
    try:
        import moybyte_sd
        import moy_carts

        if session is None:
            session = moybyte_sd.with_sd

        def _seed_and_scan():
            moy_carts.ensure_dirs()
            moy_carts.seed_builtins(CARTS)
            return moy_carts.scan()

        # Mount only for the seed+scan, then unmount: the render loop must own
        # the shared SPI bus with no SDCard device attached, or flushes hang.
        carts = session(_seed_and_scan)
        if carts:
            print("Moybyte loaded %d carts from SD" % len(carts))
            return carts, moy_carts.CARTS_DIR
    except Exception as exc:  # noqa: BLE001
        print("Moybyte SD carts unavailable:", exc)
    print("Moybyte using built-in carts")
    return [dict(c) for c in CARTS], None


# --- the device WEB VIEW controller now lives in device_webview.py (WebView +
# _PointerSink + _WebProvider), imported at the top of this module. run_desktop
# constructs WebView(...) and services it between frames (begin_frame/commit_frame/
# poll); Settings -> WEB VIEW swaps its TeeCanvas in.


# Kid-side bench source (#63 run_perf_bench): sakura's exact _update/_draw shape,
# compiled AT RUNTIME with exec() like a real SD cart -- so the kid side runs RAM
# bytecode against the frozen engine, the same split as production. 120 petals of
# float physics + the naive per-petal spr() loop.
_BENCH_KID_CODE = """
import math
SIN = [math.sin(i / 256.0 * 6.2831853) for i in range(256)]
petals = []
t = 0.0

def _sin(turn):
    return SIN[int(turn * 256.0) & 255]

def _init():
    global petals
    petals = []
    for i in range(120):
        shade = i % 3
        petals.append([(i * 37) % 320 * 1.0, (i * 53) % 240 * 1.0,
                       30.0 * (1.0 - 0.18 * shade), 0.3 + i * 0.01,
                       4.0 + (i % 9), shade])

def _update(dt):
    global t
    t += dt
    breeze = 18.0
    cx = -999.0
    cy = -999.0
    R = 52.0
    for p in petals:
        p[3] += dt * (0.32 + 0.06 * p[5])
        sway = _sin(p[3]) * p[4]
        p[0] += (breeze * (1.0 - 0.15 * p[5]) + sway) * dt
        p[1] += p[2] * dt
        dx = p[0] - cx
        dy = p[1] - cy
        if -R < dx < R and -R < dy < R:
            far = dx if dx >= 0 else -dx
            ady = dy if dy >= 0 else -dy
            if ady > far:
                far = ady
            k = (R - far) / R * 130.0
            inv = 1.0 / (far + 4.0)
            p[0] += dx * inv * k * dt
            p[1] += dy * inv * k * dt
        if p[1] > H + 4.0:
            p[1] = 0.0
        elif p[0] < -8.0:
            p[0] += W + 16.0
        elif p[0] > W + 8.0:
            p[0] -= W + 16.0

def _draw():
    draw_layer(lay, 0, 0)
    for p in petals:
        spr(p[5], int(p[0]), int(p[1]), 0)
"""


def run_perf_bench(handler):
    """Self-terminating perf bench (#63): boots the REAL device pipeline (compositor,
    DeviceCanvas, frozen engine, runtime-exec'd kid code, real flush DMA) and measures
    the sakura-shaped frame under every combination that matters:
      - Python spr path vs the native spr_gate
      - cold heap vs warm/fragmented heap (the frame-spill pathology trigger)
      - flush on vs off (PSRAM DMA cache-contention probe)
    plus the CALIB cost-model line on the frozen interpreter. Prints BENCH lines and
    RETURNS (no takeover loop): the board drops back to the REPL, so a headless bench
    board (XIAO, no buttons) stays reflashable. Never enabled in user images -- boots
    only via the _moy_bench build stamp (MOYBYTE_BENCH=1)."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:
            print("Moybyte bench: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState
        from editors import SpriteSheet
    except Exception as exc:
        print("Moybyte bench unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("Moybyte bench: no compositor")
        return
    import gc

    canvas = DeviceCanvas(comp)
    sheet = SpriteSheet()
    px = sheet.pix
    for i in range(len(px)):
        px[i] = (i * 7) % 15 + 1       # non-transparent noise tiles
    inp = InputState()

    class _Diag:
        def log(self, tag, msg):
            print("BENCH %s %s" % (tag, msg))

    diag = _Diag()

    def build_ns(use_gate):
        # Fresh kid namespace, exec'd at runtime like a real cart. use_gate=False
        # shadows make_spr_gate so make_api keeps the Python spr closure.
        if not use_gate:
            canvas.make_spr_gate = lambda s, f: None       # instance shadow
        elif "make_spr_gate" in canvas.__dict__:
            del canvas.make_spr_gate                       # restore the C gate
        ns = make_api(canvas, inp, {}, sheet=sheet)
        exec(_BENCH_KID_CODE, ns)                           # noqa: S102 -- bench-only
        ns["lay"] = ns["make_layer"](canvas.w, canvas.h)
        ns["lay"].cls(3)
        ns["_init"]()
        return ns

    def run_cfg(label, use_gate, frames=60, flush=True):
        ns = build_ns(use_gate)
        upd = ns["_update"]
        drw = ns["_draw"]
        gc.collect()
        tu = 0
        td = 0
        tf = 0
        for i in range(frames):
            canvas.sync_back()
            canvas.batch_reset()
            t0 = _ticks_us()
            upd(0.033)
            t1 = _ticks_us()
            drw()
            canvas.flush_batch()
            t2 = _ticks_us()
            if flush:
                comp.flush()
            t3 = _ticks_us()
            tu += _ticks_diff(t1, t0)
            td += _ticks_diff(t2, t1)
            tf += _ticks_diff(t3, t2)
        print("BENCH %s: update=%.2fms draw=%.2fms flush=%.2fms (batch=%d/%d)"
              % (label, tu / frames / 1000.0, td / frames / 1000.0,
                 tf / frames / 1000.0, canvas._batch_flushes, canvas._batch_sprites))

    print("BENCH start (frozen engine, runtime-exec kid code)")
    _diag_calib(diag)                       # cost model, cold-ish heap
    run_cfg("cold pyspr ", False)
    run_cfg("cold gate  ", True)
    # warm/fragmented heap: the production trigger (live buffers + churn holes)
    ballast = [bytearray(150 * 1024)]
    for i in range(6000):
        ballast.append([i, i, i, i, i, i, i, i])
    frag = [(i, i, i, i) for i in range(20000)]
    keep = []
    for i in range(0, 20000, 2):
        keep.append(frag[i])
    frag = keep
    gc.collect()
    print("BENCH warm heap live=%dk" % (gc.mem_alloc() >> 10))
    _CALIB_DONE[0] = False
    _diag_calib(diag)                       # cost model again, warm heap
    run_cfg("warm pyspr ", False)
    run_cfg("warm gate  ", True)
    run_cfg("warm gate noflush", True, flush=False)
    print("BENCH done -- returning to REPL")


def run_desktop(handler, prefetched=None, fps_cap=60):
    """Boot the workstation on the device: launcher + carts + keyboard.

    `prefetched` is the (carts, carts_root) tuple read from SD BEFORE display
    init (see moybyte_shell._prefetch_carts). SD shares the panel's SPI bus, so
    mounting after the panel runs hard-hangs the device -- never call _load_carts
    here once the display is live."""
    if handler is not None:
        try:
            handler.deinit()  # stop the LVGL TaskHandler; the compositor owns the bus
        except Exception as exc:
            print("Moybyte desktop: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState, TDeckKeyboard, InputPoller
    except Exception as exc:
        print("Moybyte desktop unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=24)
    if comp is None:
        print("Moybyte desktop: no compositor")
        return
    # The compositor flushes the dedicated _frame buffer in strip_h-row bands: each a
    # distinct, stable slice (the async esp_lcd DMA can't race a reused buffer -> no
    # offset/duplication) and small enough that the per-band DMA bounce fits the S3's
    # fragmented internal heap (a single 320x240 tx_color NO_MEMs). strip_h=24 = band.

    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    ball = TrackBall()
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    pointer = Pointer(canvas.w, canvas.h)
    inp.pointer = pointer         # touch-driven carts read it via the api touch()
    # #69 input-poller thread: move EVERY I2C0 transaction (kbd + GT911 + mode
    # switches) off the frame loop so a C3 clock-stretch stall can't freeze a
    # frame (needs the build's GIL-release patch to bite; see the class comment).
    # Any failure leaves the synchronous path untouched.
    poller = None
    if MOY_INPUT_POLLER:
        try:
            _p = InputPoller(keyboard, touch)
            if _p.start():
                poller = _p
                keyboard._poller_owned = True
                touch._source = poller.consume_touch
                _diag_note("input", "poller thread running (#69, %dms cadence)"
                           % poller.period)
        except Exception as exc:  # noqa: BLE001 -- input must never fail closed
            _diag_note("input", "poller setup failed: %s" % (exc,))
            poller = None
    import moybyte_sd
    # Carts are read from SD before display init; only fall back to a post-display
    # mount (now safe via the native moy_sd path) if the shell didn't prefetch.
    carts, carts_root = (prefetched if prefetched is not None
                         else _load_carts(moybyte_sd.with_sd_live))
    import moy_carts
    ws = Workstation(comp, canvas, inp, carts)
    ws.make_api = make_api        # device cart namespace (DeviceCanvas + Image + color)
    ws.make_audio = make_audio    # device I2S audio backend (#16, NEEDS HW VERIFICATION)
    ws.carts_store = moy_carts    # SD .moy store (scan/load/save/create/dup/delete)
    ws.carts_root = carts_root
    # Writes are enabled on-device via moy_sd: it attaches the SD card to the SPI
    # host esp_lcd already initialized (instead of machine.SDCard re-initializing
    # it, which hangs the live bus). with_sd_live mounts the card once and keeps
    # it resident -- tearing it down per op silent-hangs the next panel flush.
    # can_manage falls back off if the SD root is unknown (booted on embedded carts).
    ws.can_manage = carts_root is not None
    # SD vs panel-DMA mutual exclusion (#40 double-buffer): SD shares the panel's SPI
    # host, so an SD op can NOT overlap an in-flight panel DMA. Wrap with_sd_live so it
    # drains any pending panel DMA (comp.sync()) BEFORE touching the SD card -- the
    # desktop loop is single-threaded so SD ops run between frames, but with double-
    # buffer a frame's flush DMA may still be in flight when the op starts. sync() is a
    # no-op in single-buffer mode (the flush already blocked), so this is safe either
    # way and the wrapper is transparent to the shared console code.
    def _with_sd_synced(fn):
        comp.sync()
        return moybyte_sd.with_sd_live(fn)
    ws._with_sd = _with_sd_synced
    # OTA firmware update (#53): the shared console's Settings -> UPDATE FW row flashes a
    # new app image from /sd/update into the inactive OTA slot (esp32.Partition) and
    # reboots. SD shares the panel SPI host, so the updater reads through the SAME
    # _with_sd_synced wrapper as cart saves (drain panel DMA -> native single-bus mount).
    # Available only on an --ota build (running slot is ota_0/ota_1); on a legacy single-
    # factory image available() is False and the row never shows.
    try:
        import moy_ota
        ws.updater = moy_ota.OtaUpdater(_with_sd_synced)
    except Exception as exc:
        print("Moybyte: OTA updater unavailable:", exc)
    ws.pointer = pointer
    ws.keyboard = keyboard        # lets the code editor switch to text (ASCII) mode
    # WiFi (#38): one SYSTEM service (network.WLAN STA) shared across carts, so the
    # connection persists when the WiFi-manager cart exits and #22/#8 can use it.
    # Injected into a cart's namespace ONLY when its manifest grants "network".
    # Autoconnect from the saved creds at boot. NEEDS ON-DEVICE VERIFICATION.
    ws.wifi = make_wifi(moy_carts, carts_root)
    # OTA online update (#53, Phase 3): hand the updater the wifi service so Settings ->
    # UPDATE ONLINE can fetch a manifest + stream a new image to SD. go_online reuses the
    # saved-credential autoconnect (autoconnect_wifi) so the kid needn't re-enter wifi to
    # update -- it only connects to a network they already joined via the WiFi cart.
    if getattr(ws, "updater", None) is not None:
        try:
            ws.updater.set_wifi(ws.wifi, go_online=lambda: autoconnect_wifi(ws.wifi))
        except Exception as exc:
            print("Moybyte: OTA wifi wiring failed:", exc)
    # System menu (#52): the ≡ dropdown's "Reboot" row. On device a real reboot is
    # machine.reset(); the shared console calls this injected hook (None on host -> a
    # safe go_home stub). Additive -- it never touches the render/flush path.
    try:
        import machine
        ws.reboot_hook = machine.reset
    except Exception as exc:
        print("Moybyte: reboot hook unavailable:", exc)
    # Web view (#41/#22): serve the running console to a browser on the same WiFi via a
    # draw-command stream (NOT raw pixels -- WiFi is ~72KB/s, 153KB/frame is unplayable).
    # It starts OFF: ws.canvas stays the RAW DeviceCanvas so there is ZERO per-draw cost
    # in the normal (no-browser) path. Only when Settings -> WEB VIEW turns it ON does the
    # WebView swap a recording TeeCanvas in as ws.canvas (and even then it records only
    # while a browser is actively polling /frame). web is None on a build without
    # moy_webserver -> the Settings row is hidden.
    web = WebView(ws, canvas, inp, pointer, ws.wifi)
    if web.available():
        canvas = web.install()        # boot no-op; keeps sync_back() on the real canvas
        ws.web_hook = web
    # WiFi is deliberately NOT brought up at boot: the WLAN stack reserves internal RAM
    # the LCD DMA flush needs, so autoconnecting here starved the panel flush (OSError
    # 257 / ESP_ERR_NO_MEM) and froze the desktop. DeviceWifi is lazy now -- the radio
    # only spins up when the WiFi-manager cart scans/connects. WiFi<->display coexistence
    # on this RAM budget is an open #38 item. (autoconnect_wifi left defined, not called.)
    # Desktop shell (#28): load system.json + apply the saved wallpaper. On device
    # the wallpaper backdrop runs the chosen wallpaper cart's _draw (and _update if
    # cheap) each home frame; _wp_live can be set False to keep it _draw-only.
    ws.load_system()
    # Unified top bar (Stage 1): build the 16x16 IconSheet the bar draws its chrome
    # icons from -- from system_icons.moygfx on SD if present, else the baked default
    # theme. Same store + with_sd_live path as system.json.
    ws.load_icon_sheet()
    # Achievements (#21): load the unlocked badges (achievements.json) so earned
    # milestones survive a reboot. Same store + with_sd_live path as system.json.
    ws.load_achievements()
    # Offline diagnostics (moybyte_diag): RAM ring now, flushed to SD every ~5s and
    # on a crash, dumped to serial at next boot. perf_capture makes ws.frame() record
    # the flush/draw split each frame WITHOUT drawing the on-screen HUD, so the perf
    # sampler below can read steady numbers. Guarded import: no diag -> plain loop.
    try:
        import moybyte_diag as diag
    except Exception:
        diag = None
    if diag is not None:
        try:
            ws.perf_capture = True   # measure flush/draw for the diag perf samples
        except Exception:
            pass
    _diag_log("boot", "desktop running kb=%d ball=%d touch=%d"
              % (1 if keyboard.available else 0, 1 if ball.available else 0,
                 1 if touch.available else 0), diag)

    # OTA rollback confirm (#53): reaching here means this image booted, mounted the
    # panel + SD + keyboard, and loaded the desktop -- a strong "healthy" signal. Mark
    # the running app valid so the bootloader cancels the pending rollback it would
    # otherwise trigger on the next reset (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE). No-op
    # if the image was already confirmed or this is a non-OTA build.
    if getattr(ws, "updater", None) is not None:
        try:
            if ws.updater.mark_valid():
                _diag_log("ota", "marked app valid (slot %s)" % ws.updater.slot(), diag)
        except Exception as exc:
            _diag_log("ota", "mark_valid failed: %s" % exc, diag)

    import gc
    gc.collect()                                # defrag after the heavy boot so the LCD
                                                # DMA flush has the internal RAM it needs
    try:                                        # one-shot heap snapshot (diagnostic):
        import esp32                            # each region = (total, free, max_contiguous, min_free);
        # the small regions are internal SRAM, the huge one is PSRAM. The LCD DMA
        # bounce needs a contiguous INTERNAL block, so watch the small regions' max.
        _diag_log("mem", "gc_free=%d heap=%s"
                  % (gc.mem_free(), esp32.idf_heap_info(esp32.HEAP_DATA)), diag)
    except Exception as _e:                     # noqa: BLE001 -- diagnostic only
        _diag_log("mem", "gc_free=%d (esp32 n/a: %s)" % (gc.mem_free(), _e), diag)
    frame_ms = 1000 // fps_cap
    last = _ticks_ms()
    _backlight_on = False         # #45: panel stays dark until the first frame ships
    # Diag timers: flush the RAM ring to SD every ~5s (between frames, never during a
    # panel flush -- with_sd_live mounts on the native single-bus path), and sample
    # the perf HUD numbers into a PERF line every ~3s while a cart runs.
    _diag_flush_at = _ticks_ms() + 5000
    _diag_perf_at = _ticks_ms() + 3000
    _diag_prev_cart_err = None    # last ws.cart_error we logged, so we log each crash once
    _diag_cart_prev = False       # #68: cart-running edge -> flush the ring on cart EXIT
    ws.arm_splash()               # boot logo: show the moybyte mascot before the launcher
    while True:
        now = _ticks_ms()
        dt = max(0.0, min(0.1, _ticks_diff(now, last) / 1000.0))
        last = now
        # #69: with the poller thread live, the frame loop only APPLIES staged
        # input (no I2C -> no stall can land here). If the thread ever dies,
        # detach and fall back to the synchronous poll -- input never goes dark.
        if poller is not None and not poller.alive:
            _diag_note("input", "poller thread died -> synchronous fallback")
            keyboard._poller_owned = False
            touch._source = None
            poller = None
        try:
            if poller is not None:
                poller.consume()
            else:
                keyboard.poll()
        except Exception:
            pass
        # HITCH v2 (#66): the first hardware pass showed ~188ms hitches every
        # ~1.3s with every MEASURED phase normal -- the pause lives in the input
        # polls (I2C keyboard/touch), the one loop stage that wasn't timed. Time
        # kbd / (ball+touch) / ws.frame separately so a HITCH line names it.
        _t_kbd = _ticks_diff(_ticks_ms(), now)
        # Web view (#41): start this frame's recording (no-op unless a browser is live)
        # and inject any queued browser button/pan input BEFORE begin_frame, so a
        # browser press registers a clean one-frame edge like the keyboard's.
        web.begin_frame()
        web.feed_input(now)
        _t0 = _ticks_ms()
        inp.begin_frame()                       # keyboard edges (still a fallback)
        counts, click = ball.poll()             # trackball
        nx = counts[3] - counts[2]              # right - left (raw pulses)
        ny = counts[1] - counts[0]              # down - up
        if ws.screen == "menu" and ws.menu_view == "code":
            ws.nav(nx, ny)                      # in the code editor the trackball moves the caret
        else:
            dx = _cursor_delta(nx)
            dy = _cursor_delta(ny)
            if dx or dy:
                pointer.move(dx, dy)            # elsewhere it moves the cursor
        tp = touch.poll()                       # touch -> absolute position + tap
        pointer.down = tp is not None           # held finger drives drag-scroll
        if tp is not None:
            pointer.place(tp[0], tp[1])
            if tp[2]:                           # press edge = tap = click
                click = True
        # Web view (#41): merge a browser finger/tap AFTER the physical touch read (so
        # it isn't clobbered); a real finger on the device wins over the browser.
        if web.feed_pointer(tp is not None):
            click = True
        pointer.click = click
        pointer.tick(now)                       # auto-hide the idle trackball cursor
        _t_inp = _ticks_diff(_ticks_ms(), _t0)  # trackball + touch + pointer (HITCH v2)
        # DMA double-buffer (#40, DEFAULT ON): point the canvas at the compositor's
        # current BACK buffer before drawing. The previous flush() swapped it, so this
        # frame's cls/rect/spr/map must target the new back, never the buffer that's
        # mid-DMA. No-op (buffer unchanged) in single-buffer mode or on a skipped frame.
        _frames_before = getattr(ws, "_frames_drawn", 0)
        _t0 = _ticks_ms()
        canvas.sync_back()                      # buffer repoint + GDMA layer kick
        _t_sb = _ticks_diff(_ticks_ms(), _t0)   # (was an unmeasured stage; HITCH v3)
        _t0 = _ticks_ms()
        try:
            ws.handle_input()                   # keyboard W/A/S/D etc.
            ws.handle_pointer()                 # cursor hover + click
            ws.frame(dt)                        # draw + composite + flush
        except Exception as exc:                # never let one bad frame brick the device:
            # Capture the crash in diag AND print it live: a crash we can't see live
            # (the takeover loop has starved USB) is the whole reason diag exists, so
            # flush it to SD immediately so next boot's dump has it.
            _diag_log("frame error", exc, diag)
            print("Moybyte frame error:", exc)  # print the traceback's reason to serial
            _diag_flush(diag, ws)
            gc.collect()                        # a NO_MEM flush may recover after a collect
        _t_ws = _ticks_diff(_ticks_ms(), _t0)   # handle_input/pointer + ws.frame (HITCH v2)
        # Web view (#41): publish this frame's recorded draw commands to the browser --
        # but ONLY if the frame actually drew (the redraw-on-change gate #44 may skip a
        # static screen, which would record nothing; keep serving the last full frame).
        if getattr(ws, "_frames_drawn", 0) != _frames_before:
            web.commit_frame()
        # DMA double-buffer (#40): finish the displayed frame when the UI goes IDLE.
        # flush() holds back the final band (the busy-wait completion point) for the
        # NEXT flush's drain so render overlaps the DMA -- but the redraw-on-change gate
        # (#44) may skip flush() for many idle frames, which would leave that final band
        # un-issued and the panel showing an incomplete frame. So when THIS frame did
        # not draw (no flush happened), drain any pending band: the panel is then fully
        # painted and stays idle (pending -> None, so this fires once, not every idle
        # frame). No-op in single-buffer mode (sync() is a no-op there).
        if getattr(ws, "_frames_drawn", 0) == _frames_before:
            try:
                comp.sync()
            except Exception:
                pass
        # A cart that raises inside its own _update/_draw is caught INSIDE ws.frame()
        # (so it never reaches the except above) -- it sets ws.cart_error. Mirror any
        # NEW cart_error into diag + flush it, so an in-cart crash is captured offline
        # for the next-boot dump (the takeover loop has starved live serial by now).
        if diag is not None:
            _ce = getattr(ws, "cart_error", None)
            if _ce is not None and _ce != _diag_prev_cart_err:
                _diag_prev_cart_err = _ce
                _diag_log("cart error", _ce, diag)
                _diag_flush(diag, ws)
            elif _ce is None:
                _diag_prev_cart_err = None
        # Boot "CRT" flash fix (#45): the backlight booted OFF (tdeck_board/tdeck_display)
        # so the ST7789 power-on GRAM noise is never lit. Turn it on the instant the
        # first real frame has been composed+flushed -- ws._frames_drawn ticks past 0
        # only inside frame() after comp.flush() -- so the user's first sight is the
        # desktop, not garbage. One-shot; guarded so a no-op redraw frame won't re-light.
        if not _backlight_on and getattr(ws, "_frames_drawn", 0) > 0:
            try:
                import tdeck_display
                tdeck_display.set_backlight(True)
            except Exception as _bl:            # display-less host / bring-up: ignore
                print("Moybyte backlight on failed:", _bl)
            _backlight_on = True
        # Diag perf sample (~3s): a structured "PERF cart=<name> fps=<n> flush=<ms>
        # draw=<ms>" line while a cart runs -- the payload that makes "play -> reboot
        # -> paste the serial" yield per-cart frame timings offline. No SD touch here
        # (just the RAM ring); the 5s flush below is what writes it out.
        _tnow = _ticks_ms()
        _t_diag = 0
        # #68 "kid mode" gate: Settings -> PERF DIAG (ws.diag_live, persisted,
        # default OFF). OFF skips the two diag costs a player can FEEL -- the 30s
        # forced GC sample (~130-230ms) and the periodic diag->SD write (~115ms) --
        # and hushes the live echo. The RAM ring still collects every line
        # (us-cheap) and still reaches SD on crash / cart exit below, so
        # "play -> crash -> read diag.log" works in kid mode too.
        _live = bool(getattr(ws, "diag_live", False))
        if diag is not None and _ticks_diff(_tnow, _diag_perf_at) >= 0:
            _diag_perf_at = _tnow + 3000
            try:
                diag.ECHO_LIVE = _live   # echo follows the toggle (boot lines echoed
            except Exception:            # before the first 3s tick either way)
                pass
            _diag_perf_sample(diag, ws)
            _diag_drawbrk(diag, ws)
            _diag_draw2(diag, ws)       # #63: split render into layer-copy vs sprite-batch us
            _diag_chromebrk(diag, ws)   # #66 lever 5: bar/composite/cursor chrome sub-split
            _diag_pump(diag, comp)      # #66 lever 4: bounce-feed pacing (SPI idle gaps)
            _diag_i2cstat(diag, keyboard, touch)  # #69: kbd/touch I2C session latency
            _diag_calib(diag)           # #63: one-shot interpreter cost model (spill probe)
            if _live:
                _diag_gc(diag)          # #63/#68: the FORCED collect sample -- diag-only,
                                        # never during kid play (it costs a felt frame)
            _t_diag = _ticks_diff(_ticks_ms(), _tnow)
        # Diag SD flush: overwrite /sd/moybyte/diag.log with the current ring.
        # Runs between frames on the native single-bus path (with_sd_live), never
        # during a panel flush. Guarded -> a flush failure degrades to a no-op.
        # CADENCE (#66, hardware-measured): the write costs 80-120ms -- at the old
        # flat 5s that was a visible stutter DURING PLAY (HITCH sdflush=82-118
        # confirmed it). #68: the timer flush now runs ONLY with PERF DIAG on
        # (20s in-cart / 5s otherwise); in kid mode the ring is persisted at cart
        # EXIT instead (one write, off the play path) + the crash paths.
        _cart_now = ws.cart is not None
        _t_sd = 0
        if diag is not None and _diag_cart_prev and not _cart_now:
            _t_sd = _diag_flush(diag, ws)  # #68: cart exited -> persist the session's ring
        _diag_cart_prev = _cart_now
        if diag is not None and _live and _ticks_diff(_tnow, _diag_flush_at) >= 0:
            _diag_flush_at = _tnow + (20000 if ws.cart is not None else 5000)
            _t_sd = _diag_flush(diag, ws)
        # Web view (#41): service the server BETWEEN frames, fully non-blocking -- accept
        # new connections + drain the persistent WebSocket's queued input and push the
        # latest committed frame down it (WiFi STA is a separate peripheral from the display
        # SPI, so this never touches the SD/panel bus -- it only competes for CPU here).
        # No-op when the server is off; a slow client is dropped, never waited on.
        _t0 = _ticks_ms()
        web.poll()
        _t_web = _ticks_diff(_ticks_ms(), _t0)
        elapsed = _ticks_diff(_ticks_ms(), now)
        # Hitch logger (#66): any frame past HITCH_MS gets a HITCH line naming the
        # measured stages -- kbd (I2C keyboard poll), inp (trackball+touch+pointer),
        # ws (input handlers + frame: logic/render/chrome/flush), the 3s diag
        # sample, the diag->SD write, web.poll -- the tool for catching the
        # "micro-stutter every couple of seconds" class of bug. A spike with all
        # the named parts small = the pause was between stages (e.g. an implicit
        # GC collect inside an alloc), which is itself the answer.
        if diag is not None and elapsed >= HITCH_MS:
            _diag_hitch(diag, ws, comp, elapsed, _t_kbd, _t_inp, _t_sb, _t_ws,
                        _t_diag, _t_sd, _t_web)
        if elapsed < frame_ms:
            time.sleep_ms(frame_ms - elapsed)


def run_touch_calibrate(handler):
    """Touch bring-up aid (moybyte_shell.RUN_TOUCH_CALIBRATE). Draws corner
    targets and prints each GT911 sample (raw + current mapping) over serial.

    It flushes the panel only ONCE up front and then just polls + prints, so USB
    serial keeps draining -- the normal desktop loop's continuous flush starves
    USB and you'd see nothing. Touch each yellow corner, read the raw coords over
    serial, then set TOUCH_SWAP / TOUCH_FLIP_X / TOUCH_FLIP_Y / TOUCH_RAW_* in device_input.py
    so the mapped value lands on that corner, and rebuild."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte touch-cal: takeover failed:", exc)
    try:
        from tdeck_display import get_display_bus
        from moy_compositor import make_compositor
        from moybyte.input import InputState, TDeckKeyboard
    except Exception as exc:  # noqa: BLE001
        print("Moybyte touch-cal unavailable:", exc)
        return
    comp = make_compositor(get_display_bus(), 320, 240, strip_h=40)
    if comp is None:
        print("Moybyte touch-cal: no compositor")
        return
    canvas = DeviceCanvas(comp)
    inp = InputState()
    keyboard = TDeckKeyboard(inp)
    touch = Touch(canvas.w, canvas.h, i2c=getattr(keyboard, "_i2c", None))
    canvas.cls(NAMES["black"])
    for (cx, cy) in ((8, 8), (canvas.w - 9, 8), (8, canvas.h - 9),
                     (canvas.w - 9, canvas.h - 9), (canvas.w // 2, canvas.h // 2)):
        canvas.rectb(cx - 6, cy - 6, 12, 12, NAMES["yellow"])
    canvas.print("TOUCH CORNERS", 100, canvas.h // 2 - 24, NAMES["white"], 2)
    canvas.print("watch serial", 108, canvas.h // 2 + 8, NAMES["light_grey"], 1)
    comp.flush()
    print("Moybyte touch-cal start avail=%d addr=%s"
          % (1 if touch.available else 0, hex(touch.addr) if touch.addr else "?"))
    while True:
        r = touch.debug_read()
        if r and r[1]:  # (status, 8 raw bytes) on a real touch
            status, d = r
            print("Moybyte touch-cal status=0x%02x bytes=%s"
                  % (status, " ".join("%02x" % b for b in d)))
        time.sleep_ms(50)


def run_keyboard_probe(handler):
    """Keyboard bring-up aid (moybyte_shell.RUN_KEYBOARD_PROBE): read the T-Deck
    keyboard over I2C0 and print the byte each key returns -- the code-editor's
    1-byte ASCII read path. No panel takeover/flush, so USB serial stays alive
    (the desktop loop's continuous flush would starve it).

    Tap each key left->right, top->bottom; each new key prints one `KEY ...` line.
    We deliberately do NOT send the raw-matrix command (0x03) so this shows the
    keyboard's plain ASCII protocol -- exactly what the editor should consume."""
    if handler is not None:
        try:
            handler.deinit()
        except Exception as exc:  # noqa: BLE001
            print("Moybyte kb-probe: takeover failed:", exc)
    try:
        from machine import I2C, Pin
    except Exception as exc:  # noqa: BLE001
        print("Moybyte kb-probe unavailable:", exc)
        return
    addr = 0x55
    try:
        i2c = I2C(0, scl=Pin(8), sda=Pin(18), freq=400000)
    except Exception as exc:  # noqa: BLE001
        print("Moybyte kb-probe i2c failed:", exc)
        return
    found = []
    try:
        found = i2c.scan()
    except Exception:  # noqa: BLE001
        pass
    print("Moybyte keyboard probe start; i2c scan=%s addr=0x%02x"
          % ([hex(a) for a in found], addr))
    print("Moybyte kb-probe: tap keys L->R, T->B. lines = KEY <n> 0x<hex> <dec> '<char>'")
    prev = 0
    n = 0
    beat = 0
    while True:
        try:
            d = i2c.readfrom(addr, 1)
            k = d[0] if d else 0
        except Exception as exc:  # noqa: BLE001
            print("Moybyte kb-probe read err:", exc)
            time.sleep_ms(300)
            continue
        if k and k != prev:
            n += 1
            ch = chr(k) if 0x20 <= k <= 0x7E else "."
            print("KEY %d 0x%02x %d '%s'" % (n, k, k, ch))
        prev = k
        beat += 1
        if beat % 250 == 0:        # ~5s heartbeat so you know it's alive
            print("Moybyte kb-probe alive (keys so far: %d)" % n)
        time.sleep_ms(20)
