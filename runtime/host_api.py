"""The PURE-PYTHON cart-API backend (make_api + driver + fake services) -- the
host twin of the device's device_api.py, extracted from host_app.py so a
non-CPython target can freeze it (#151: the web runner runs this under
MicroPython-WASM with a CommandCanvas -- browser-as-GPU, no rasterizer).

Everything here is portable Python: no pygame, no sockets, no shutil, no
threading. host_app.py imports this back and re-exports every name, so
`host_app.make_api` / `host_app.ConsoleDriver` / ... are unchanged for the
sim, the web console, and the tests. Imports follow the bare-or-package
pattern (bare top-level on a staged/frozen target, runtime.* on the host).
"""

import json
import random

try:
    import palette                     # staged/generated module on a frozen target
except ImportError:                    # host / package import
    from runtime import palette
try:
    from moy_image import Image        # ONE Image, shared with device_canvas
except ImportError:                    # host / package import
    from runtime.moy_image import Image

PAN_SPEED = 6            # px/frame the arrow-keys-as-trackball nudge the cursor

# The logical buttons a CART may read (moy SPEC.md 7.3): four directions, a/b,
# and the optional `run`. The InputState button sets are deliberately wider --
# the host carries "home" and the device adds x/y/select/start/save/... -- but
# those belong to the console, not to carts. "home" especially: §7.3 gives exit
# to the HOST ("no cart is required to provide one... the cart never sees it"),
# so a cart that polled btn("home") could watch the player reaching for the exit.
# Anything outside this set reads as not-pressed on every tier, which is also
# what a conforming console with different hardware would report.
CART_BUTTONS = ("left", "right", "up", "down", "a", "b", "run")

_console = None


def _console_mod():
    """The shared console module, resolved lazily (bare on a frozen target,
    runtime.* on the host) -- by the time a cart calls time() the console is
    always imported, so this never triggers the heavy import itself."""
    global _console
    if _console is None:
        try:
            import console as _c
        except ImportError:
            from runtime import console as _c
        _console = _c
    return _console


def _widgets_mod():
    """The shared widgets module, lazily resolved like _console_mod (only the
    scene rotation path needs it)."""
    try:
        import widgets as _w
    except ImportError:
        from runtime import widgets as _w
    return _w


class FakeAudio:
    """Host audio backend (#16) that records every call AND drives the shared
    AudioEngine, so behavior is fully assertable headlessly -- no sound hardware
    needed. Mirrors the existing sim fakes (moybyte_sim fake audio,
    moybyte/audio.py AudioService.calls). The optional real-playback backend
    (SdlAudio, see docs/audio_design_v04.md) is a thin follow-on that pulls
    engine.render() from an SDL stream instead of just recording.

    `tick(dt)` renders a block each frame so render() is exercised on the same
    schedule the device's per-frame I2S feeder would use."""

    def __init__(self, engine):
        self.engine = engine
        self.calls = []           # [("sfx", n, chan), ("beep", f, d), ...]
        self.rendered = 0         # total PCM frames pulled via tick()
        self.last_pcm = b""       # most recent tick()'s PCM block (drained by the web console)

    def sfx(self, n, chan=None):
        self.calls.append(("sfx", int(n), chan))
        self.engine.play_sfx(n, chan)

    def beep(self, freq, dur=0.15):
        self.calls.append(("beep", freq, dur))
        self.engine.play_beep(freq, dur)

    def music(self, track, loop=True):
        self.calls.append(("music", int(track), bool(loop)))
        self.engine.play_music(track, loop)

    def music_stop(self):
        self.calls.append(("music_stop",))
        self.engine.stop_music()

    def sound_stop(self, chan=None):
        self.calls.append(("sound_stop", chan))
        self.engine.stop(chan)

    def volume(self, level):
        self.calls.append(("volume", level))
        self.engine.set_volume(level)

    def is_active(self):
        """True while anything is audible. The backend-level hook the Music
        editor asks (#97) -- on the host that is just the engine, but the device
        and web backends answer from libmoy, which owns the sequencers there."""
        return self.engine.is_active()

    def tick(self, dt):
        n = int(self.engine.rate * max(0.0, dt))
        if n > 0:
            # Keep the rendered block (was discarded) so the web console can stream the
            # FINISHED PCM to the browser -- no second synth in JS (audio.py stays the
            # single source of truth). The device/headless paths just ignore last_pcm.
            self.last_pcm = self.engine.render(n)
            self.rendered += n

    def take_pcm(self):
        """Hand off the last tick()'s PCM (signed-16 LE mono bytes) and clear it. The
        web console drains this each frame to stream finished audio; empty between
        renders or when nothing is playing."""
        pcm = self.last_pcm
        self.last_pcm = b""
        return pcm


def make_audio(engine):
    """Injected backend factory: wrap an AudioEngine in the host FakeAudio backend.
    build_workstation hands this to the Workstation; the device injects its own."""
    return FakeAudio(engine)


# --- WiFi (#38): host fake backend ------------------------------------------
# The device wraps network.WLAN; on the PC there is no radio, so this fake gives
# the WiFi-manager cart something to drive in the simulator. It mirrors the
# device backend's interface exactly -- scan/connect/status/forget/known -- with
# canned scan results, a fake connect (records creds + reports connected), and a
# fake IP, so the manager cart is fully assertable headlessly (like FakeAudio).
#
# Credentials persist through the SAME store the device uses (moy_carts
# load_wifi/remember_wifi/forget_wifi over wifi.json), so a connect() the kid
# makes in the sim survives a reload -- the host story matches the device story.


class FakeWifi:
    """Host WiFi backend: a faithful stand-in for the device network.WLAN service.

    `store`/`root` are the moy_carts credential store + its carts dir; when given,
    connect()/forget() persist to wifi.json and known() reads it back (so the sim
    exercises the real persistence path). With no store it stays in-memory only."""

    # Canned access points the sim "sees" (ssid, signal%, locked?). A real radio
    # returns far more; this is enough for the manager cart's list UI.
    FAKE_APS = (
        ("Home WiFi", 88, True),
        ("Coffee Shop", 60, False),
        ("Neighbor 5G", 42, True),
        ("Library Guest", 30, False),
    )
    FAKE_IP = "192.168.1.42"

    def __init__(self, store=None, root=None):
        self._store = store
        self._root = root
        self._connected = False
        self._ssid = None

    # -- the injected `wifi` API surface (host == device) ----------------
    def scan(self):
        """List nearby networks as (ssid, signal, locked) tuples."""
        return [tuple(ap) for ap in self.FAKE_APS]

    def connect(self, ssid, password=""):
        """'Associate' with `ssid` (fake: always succeeds), remember the creds, and
        report connected. Returns True. The connection persists across carts (it's
        system state) and the creds persist to disk for autoconnect. An EMPTY
        password resolves to the stored one first (the DeviceWifi contract): the
        panel's known-network reconnect passes "", and remembering that "" used
        to overwrite the saved password in wifi.json."""
        self._connected = True
        self._ssid = str(ssid)
        if self._store is not None and self._root is not None:
            try:
                if not password:
                    for n in self._store.load_wifi(self._root):
                        if n["ssid"] == self._ssid:
                            password = n.get("password", "") or ""
                            break
                self._store.remember_wifi(ssid, password, self._root)
            except Exception as exc:  # noqa: BLE001 -- a save failure must not crash the cart
                print("Moybyte wifi remember failed:", exc)
        return True

    def disconnect(self):
        self._connected = False
        self._ssid = None

    def status(self):
        """(connected, ssid, ip): the live link state other features read."""
        if self._connected:
            return (True, self._ssid, self.FAKE_IP)
        return (False, None, None)

    def forget(self, ssid):
        """Drop a saved network; disconnect if it's the active one."""
        ssid = str(ssid)
        if self._store is not None and self._root is not None:
            try:
                self._store.forget_wifi(ssid, self._root)
            except Exception as exc:  # noqa: BLE001
                print("Moybyte wifi forget failed:", exc)
        if self._ssid == ssid:
            self.disconnect()
        return True

    def known(self):
        """The remembered SSIDs (for the manager's 'saved' markers + autoconnect)."""
        if self._store is not None and self._root is not None:
            try:
                return [n["ssid"] for n in self._store.load_wifi(self._root)]
            except Exception as exc:  # noqa: BLE001
                print("Moybyte wifi known failed:", exc)
        return []


def make_wifi(store=None, root=None):
    """Injected backend factory: the host FakeWifi over the moy_carts store.
    build_workstation hands this to the Workstation; the device injects DeviceWifi."""
    return FakeWifi(store, root)


def _decode_moyimg(text):
    """Decode a .moyimg paint-image asset (#63 Fold 3) into (w, h, index_bytes), or
    None on any error (a bad image just doesn't draw). The blob is a JSON header
    {w, h, data} where `data` is base64 of the zlib-compressed MOY64 index bitmap
    (1 byte/pixel) -- the same base64+zlib envelope sprites author with. The shared
    moy_carts.decode_moyimg handles it on every target; the CPython zlib path stays
    as the host fallback (guarded, so a zlib-less frozen target just returns None)."""
    try:
        try:
            import moy_carts
        except ImportError:
            from runtime import moy_carts
        shared = moy_carts.decode_moyimg(text)
        if shared is not None:
            return shared
        import binascii
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        raw = binascii.a2b_base64(meta["data"])
        try:
            import zlib
            idx = zlib.decompress(raw)
        except ImportError:
            # MicroPython target (#151 web runner): `deflate` is its zlib --
            # the same mirror device_canvas._decode_moyimg uses. Without this
            # the legacy zlib envelope (sakura's bg) silently decoded to None,
            # so the Lua/paint background never drew in the browser.
            import deflate
            import io
            idx = deflate.DeflateIO(io.BytesIO(raw), deflate.ZLIB).read()
        return (w, h, idx)
    except Exception:  # noqa: BLE001 -- bad/absent image -> caller gets None
        return None


class _Layer:
    """A scroll background (#54): a wider off-screen canvas the cart pre-renders a
    level into ONCE, then window-copies to the screen per frame via draw_layer. Exposes
    the draw verbs (sheet/tilemap-aware, pixel-identical to the main api) bound to its
    OWN canvas, plus W/H. Built by the api's make_layer(w, h). Mirrors moy_runtime."""

    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              "tri", "trib", "sspr", "tline",
              "spr", "map", "mget", "mset", "print",
              "camera", "clip", "pal", "palt")

    def __init__(self, canvas, ns):
        self._canvas = canvas
        self.W = canvas.w
        self.H = canvas.h
        for k in _Layer._VERBS:
            setattr(self, k, ns[k])


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None, images=None, scenes=None, tables=None,
             texts=None, net=None, owner="cart"):
    # `owner` tags device-side layer loans for the leak-fix reclaim (#63); the host
    # Canvas allocates layers on the gc heap, so it is accepted and unused here.
    """The cartridge global namespace on the host -- same names/signature as the
    device make_api (TIC-80 draw API + sheet-or-Image spr + audio + tilemap), bound
    to a host Canvas and audio backend.

    `wifi` is the capability-gated network backend (#38): the Workstation passes it
    ONLY for a cart whose manifest permissions include "network", and we inject the
    `wifi` name into the namespace iff it is non-None -- so a normal kid cart gets
    no network access at all (the base key-set is identical either way).

    `images` (#63 Fold 3) is the cart's paint-image assets ({name: .moyimg text});
    the cart fetches one as a big Image via image(name) and places it with
    spr(img, x, y). Decoded lazily + memoised so repeated image(name) calls return
    the SAME Image (its per-image bake cache stays valid)."""

    _img_cache = {}                    # name -> decoded paint Image (see image() below)

    def cfg(key, default=None):
        return config.get(key, default)

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
        # TIC-80 spr(id, x, y[, colorkey, scale, flip, w, h]): w/h are the tile span,
        # so spr(n, x, y, w=2, h=2) draws the 16x16 multi-tile sprite whose top-left
        # is tile n (#30). flip (0=none, 1=h, 2=v, 3=both) mirrors the sprite pixels
        # (#11). w=h=1, flip=0 is the plain 8x8 sprite (unchanged for old carts).
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale, flip)
        if sheet is None:
            return
        if w > 1 or h > 1:
            # Multi-tile sprite: resolve the span Image and draw it immediately (the
            # canvas flushes any pending 1x1 auto-batch first).
            img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
            if img is not None:
                canvas.spr(img, x, y, scale, flip)
            return
        # Plain 1x1 sheet tile: auto-batch (#63). The canvas queues it and coalesces a
        # contiguous run into one spr_batch/blit_batch, flushing on any state break.
        canvas.spr_tile(sheet, int(n), x, y, colorkey, scale, flip)

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(mx, my, w, h, sx, sy, colorkey, scale): blit a w x h region of
        # the cart's tilemap (top-left cell mx,my) to screen (sx,sy). Tiles are the
        # 8x8 sheet sprites; `scale` enlarges each (so scale=2 => 16px world tiles).
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    # spr_batch / spans / rect_batch were cart verbs here until 2026-08-14. They are
    # DELETED, not moved: the Bench twins measured the draw paths of the two languages
    # indistinguishable at 300 calls/frame on the S3 (plan 6.10), so the batch verbs
    # bought <=1ms at realistic call counts while costing every kid two vocabularies
    # -- Lua could not call them at all (a trampoline cannot marshal a list or a span
    # buffer), which is why every Lua twin already drew the same frame with a plain
    # loop. A `for ...: spr(...)` run still leaves as ONE native blit_batch: the
    # canvas's auto-batch gate (#63, Canvas.spr_tile -> Canvas.spr_batch) coalesces
    # it. That method survives BECAUSE it is the gate's flush, not a verb.

    def sspr(sx, sy, sw, sh, dx, dy, dw=None, dh=None, colorkey=-1, flip=0):
        # sspr(sx, sy, sw, sh, dx, dy[, dw, dh, colorkey, flip]): stretch a sw x sh
        # PIXEL region of the sheet into a dw x dh destination rect (#167). Unlike
        # spr()'s integer `scale` this is an arbitrary stretch -- the textured
        # wall-slice verb for software 3D, and non-integer sprite scaling. Source
        # coords are sheet PIXELS, not tile ids.
        if sheet is None:
            return
        canvas.sspr(sheet, sx, sy, sw, sh, dx, dy, dw, dh, colorkey, flip)

    def tline(x0, y0, x1, y1, u, v, du, dv, colorkey=-1):
        # tline(x0, y0, x1, y1, u, v, du, dv[, colorkey]): SPEC.md 6.1's textured
        # line -- exactly line()'s pixels, sampling the MAP as a virtual texture
        # in 16.16 FIXED POINT (u/v/du/dv are ints: float * 65536 is the cart's
        # job). One call per scanline is a Mode 7 floor; one per column textures
        # a raycaster. Wraps modulo the map's pixel size; empty cells draw
        # nothing.
        if sheet is None or tilemap is None:
            return
        canvas.tline(tilemap, sheet, x0, y0, x1, y1, u, v, du, dv, colorkey)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    def touch():
        # Pointer (mouse stands in for touch on the host) exposed to touch-driven
        # carts: (x, y, tapped, held) this frame, or None when there is no pointer.
        # `tapped` is the press edge so a cart scores at most one hit per tap;
        # `held` stays True while the finger/button is down, so a cart can track a
        # DRAG (drawing, sliders) -- the position keeps following the finger. The
        # coords are GAME-canvas space (input.game_pointer, set by handle_pointer
        # from the viewport transform), so a cart in a larger system canvas reads the
        # 320x240 viewport, not the panel (#39). Falls back to the raw pointer.
        gp = getattr(input, "game_pointer", None)
        if gp is not None:
            held = bool(gp[3]) if len(gp) > 3 else False
            return (gp[0], gp[1], bool(gp[2]), held)
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click), bool(getattr(p, "down", False)))

    def mouse():
        # TIC-80-shaped 7-tuple (x, y, left, middle, right, scrollx, scrolly)
        # aliasing touch(): tap -> left button. The host pointer (and the device
        # touchscreen) has no middle/right/scroll, so those are constant 0/False.
        # Game-canvas coords (the viewport), like touch() (#39).
        gp = getattr(input, "game_pointer", None)
        if gp is not None:
            return (gp[0], gp[1], bool(gp[2]), False, False, 0, 0)
        p = getattr(input, "pointer", None)
        if p is None:
            return (0, 0, False, False, False, 0, 0)
        return (p.x, p.y, bool(p.click), False, False, 0, 0)

    def time():
        # Milliseconds since the cart started (set by Workstation._start). Uses the
        # shared tick helpers so it's MicroPython-safe on the device.
        start = getattr(input, "cart_start_ms", 0)
        c = _console_mod()
        return c._ticks_diff(c._ticks_ms(), start)

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
        # By default a running cart is in GAME mode: a held WASD/arrow keeps driving
        # btn() (true hold-to-move) but the keyboard yields no clean typeable ASCII.
        # Call textmode(True) to switch to text mode so key()/keyp() return clean
        # 1-byte ASCII for typing (a password, a name, a chat line); textmode(False)
        # restores game mode. Same name + behavior on the device (moy_runtime). The
        # Workstation applies it: on the host it gates char routing to the cart's
        # key(); on the device it flips the T-Deck keyboard ASCII<->raw. Resets to
        # game mode automatically when the cart exits to the desktop/home.
        input.text_mode = bool(on)

    def _quit():
        # quit() -> END this cart and return to whoever launched it (the launcher, or
        # the Editor). A cart calls it from a key or an on-screen affordance it draws.
        # This is how a TEXT-mode cart exits: once it calls textmode(True), the console's
        # hold-BACKSPACE game-exit can't reach it (BACKSPACE is a typed 0x08 the cart
        # reads as delete, and the T-Deck keyboard has no autorepeat, so the ~700ms hold
        # never accumulates) -- so a textmode(True) cart MUST provide its own exit via
        # quit(). ADDITIVE to the frozen kid API, works for ANY cart type, same name +
        # behavior on the device (moy_runtime). Sets a flag the Player honors AFTER this
        # frame's _update runs (player.tick), popping to the run caller via
        # ws._exit_to_caller(). `quit` shadows the site builtin inside the cart's exec
        # namespace, resolving to this closure.
        input.cart_quit = True

    def view(w=0, h=0):
        # view(w, h) -> declare the cart's LOGICAL viewport: the console
        # composites this centered w x h region of the 320x240 canvas to the
        # screen instead of the whole canvas, at the biggest integer scale that
        # fits -- celeste's 128x128 p8 screen fills the P4 glass at 4x instead
        # of riding the container's 2x. view() / view(0, 0) restores the full
        # canvas. ADDITIVE like textmode/quit; rides InputState (cart_quit
        # pattern), cleared by Player.start each run. Same name + behavior on
        # the device (device_api).
        input.game_view = (int(w), int(h)) if w and h else None

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
        lc = canvas.new_layer(w, h)
        lns = make_api(lc, input, config, sheet, audio, tilemap, pmem, wifi, images)
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
        # Two forms, dispatched on the first arg (str vs ASCII rows):
        #   image("bg")            -> the cart's paint-image asset images/bg.moyimg as a
        #     big Image (a 64-colour MOY64 index bitmap), placed with spr(img, x, y) --
        #     the #63 Fold 3 background path. None when the cart has no such image. The
        #     SAME Image is returned across calls (memoised) so its bake cache survives.
        #   image(rows, mapping)   -> build a small Image from ASCII art (the original
        #     kid-convenience form, e.g. image(["..##..", ...], {"#": 8})).
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

    def table(name):
        # Desk Lab interop (#78): a Sheets sheet placed in the cart's folder
        # (tables/<name>.moysheet) read as ROWS -- a list of lists of computed
        # values (numbers as numbers, text as strings, blank cells ""). Missing
        # name -> [] (image()'s degrade-don't-throw contract). The rows were decoded
        # once at cart-load (moy_carts.decode_table), so this is a plain lookup.
        rows = tables.get(name) if tables else None
        return rows if rows is not None else []

    def text(name):
        # Desk Lab interop (#78): a Writer doc placed in the cart's folder
        # (docs/<name>.moytext) read as LINES -- a list of strings. Missing name -> [].
        lines = texts.get(name) if texts else None
        return lines if lines is not None else []

    # Declared background (#63 fast-by-default -- the "software PPU layer 0"): the
    # cart names its backdrop ONCE -- a color, or a painted Image -- and the engine
    # restores it at the START of every frame, so a naive cart never writes a
    # per-frame cls()/backdrop blit (and can't overdraw it). An Image bakes into a
    # hidden full-screen layer once; the per-frame restore is then one flat window
    # copy (draw_layer), which the device hides behind the cart's logic on the async
    # GDMA path. background() with no args clears the declaration. The restore hook
    # rides the namespace (ns["_moy_restore_bg"]) so each running program (cart,
    # wallpaper) owns its OWN declaration; the Player / wallpaper runner calls it
    # before the frame's first draw. Built from the public verbs (make_layer /
    # draw_layer / cls), so the web recorder ships it with the existing protocol.
    _bg = [None]

    def background(x=None):
        if x is None:
            _bg[0] = None
        elif isinstance(x, Image):
            lay = make_layer(canvas.w, canvas.h)
            lay.spr(x, 0, 0)               # bake once (paint images take the fast path)
            _bg[0] = ("l", lay)
        else:
            _bg[0] = ("c", palette.color(x))

    def _restore_bg():
        b = _bg[0]
        if b is not None:
            if b[0] == "c":
                canvas.cls(b[1])
            else:
                draw_layer(b[1], 0, 0)

    # Multiplayer input (#65): btn/btnp take an optional player slot. Player 0 is
    # the local console -- it calls input.held/pressed DIRECTLY, byte-for-byte as
    # before, so every existing single-player cart is unchanged. Higher slots read
    # the PlayerRouter attached to the InputState (console.wire_workstation_core);
    # with no extra controller registered they are always "not held" and players()
    # is 1. `input` may be a bare stub (make_api probes / unit tests) with no
    # router, so fall back to the local path. Host == device.
    _prouter = getattr(input, "players", None)
    _held = input.held        # bound once: btn is the hottest verb a cart calls,
    _pressed = input.pressed  # and the attr lookup per call is dispatch tax (#66)

    def btn(name, player=0):
        if name not in CART_BUTTONS:
            return False
        if player:
            return _prouter.held(name, player) if _prouter is not None else False
        return _held(name)

    def btnp(name, player=0):
        if name not in CART_BUTTONS:
            return False
        if player:
            return _prouter.pressed(name, player) if _prouter is not None else False
        return _pressed(name)

    def players():
        # The connected player count (>=1) so a cart can offer a 2P/co-op mode.
        return _prouter.count() if _prouter is not None else 1

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
        "tri": canvas.tri, "trib": canvas.trib,
        "sspr": sspr, "tline": tline,
        "background": background, "_moy_restore_bg": _restore_bg,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": btn, "btnp": btnp, "players": players,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode, "quit": _quit, "view": view,
        "cfg": cfg, "col": palette.color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": image,
        "table": table, "text": text,
    }
    if wifi is not None:                 # capability-gated network API (#38)
        ns["wifi"] = wifi
    # Capability-gated multiplayer message API (#65): net.send(data)/on_net(fn),
    # injected ONLY for a cart whose manifest permissions include "multiplayer"
    # (the Player passes a non-None backend then, mirroring the wifi gate). A normal
    # kid cart's namespace never carries `net`/`on_net`. on_net registers the handler
    # the Player pumps each frame (net.on_message), so it mirrors the old radio
    # contract. Host == device.
    if net is not None:
        ns["net"] = net

        def on_net(fn):
            net.on_message(fn)
            return fn

        ns["on_net"] = on_net
    # Scene accessors (#85): scene()/scene(name)/load_scene(name) over the cart's
    # placed-actor scenes. Pure DATA (no drawing), so the logic lives once in the
    # shared widgets.Scenes -- make_api just binds its methods (same on the device).
    # The Player always passes a Scenes object (an empty one for a scene-less cart),
    # so every cart's base key-set carries these; a make_layer/probe caller omits it.
    if scenes is not None:
        ns["scene"] = scenes.scene
        ns["load_scene"] = scenes.load_scene
        # Actor-aware helpers (#109 / #85 Section 8): the live mutable actor world +
        # its verbs, the cart-API mirror of the actor blocks. Same object/logic on the
        # device (widgets.SceneWorld); only draw_scene lives per-backend because it
        # draws (spr). The world resets per run via scenes.reset() (Player.start).
        _world = scenes.world()
        ns["actors"] = _world.actors
        ns["touching"] = _world.touching
        ns["move_actor"] = _world.move
        ns["move_actor_to"] = _world.move_to
        ns["remove_actor"] = _world.remove

        _rot_cache = {}

        def _rot_sprite(_tile, _deg):
            # A cached rotated copy of a sheet sprite (1-degree buckets, #85/#93 all-
            # around rotation). rotate_indices fills the exposed corners with -1, which
            # Canvas.spr always skips, so the rotation keeps clean transparent edges.
            if sheet is None:
                return None
            _key = (_tile, int(_deg) % 360)
            _im = _rot_cache.get(_key)
            if _im is None:
                _base = sheet.tile_image(_tile, -1)
                if _base is None:
                    return None
                _rp, _rw, _rh = _widgets_mod().rotate_indices(
                    _base.pix, _base.w, _base.h, _deg, _base.transparent)
                _t = _base.transparent if _base.transparent is not None else -1
                _im = Image(_rw, _rh, _rp, _t)
                _rot_cache[_key] = _im
            return _im

        def draw_scene():
            # #85/#93 Looks + rotation: honour each actor's per-sprite appearance -- hide,
            # size, direction (Scratch rotation styles: all-around=rotate / left-right=
            # flip / none), and a `say` bubble. An actor with no `dir` draws as placed
            # (the common case, unchanged), so non-directional carts are byte-identical.
            for _a in _world.actors():
                _f = _a.flags
                if _f.get("hidden"):
                    continue
                _sc = _f.get("size", 100) // 100
                if _sc < 1:
                    _sc = 1
                _dir = _f.get("dir")
                if _dir is None:
                    spr(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                else:
                    _style = _f.get("rot", "all")
                    if _style == "none":
                        spr(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                    elif _style == "leftright":
                        spr(_a.tile, _a.x, _a.y, -1, _sc,
                            1 if (_dir % 360) > 180 else 0)
                    else:                             # "all" around: rotate to the heading
                        _im = _rot_sprite(_a.tile, _dir - 90)
                        if _im is None:
                            spr(_a.tile, _a.x, _a.y, -1, _sc, _a.flip)
                        else:
                            _cx = _a.x + 4 * _sc      # centre the (larger) rotated sprite
                            _cy = _a.y + 4 * _sc      # on the 8x8 actor's centre
                            canvas.spr(_im, _cx - (_im.w * _sc) // 2,
                                       _cy - (_im.h * _sc) // 2, _sc)
                _sy = _f.get("say")
                if _sy:
                    _t = str(_sy)[:10]
                    _bw = len(_t) * 8 + 4
                    _by = _a.y - 11 if _a.y >= 11 else _a.y + 9
                    canvas.rect(_a.x, _by, _bw, 10, 7)      # white bubble
                    canvas.rectb(_a.x, _by, _bw, 10, 0)     # black outline
                    canvas.print(_t, _a.x + 2, _by + 1, 0)  # black text

        ns["draw_scene"] = draw_scene
    return ns


class _NullComp:
    """The device flushes the panel via a compositor; the host reads the canvas
    directly, so this just satisfies Workstation.frame()'s flush() call."""
    def flush(self):
        pass


class ConsoleDriver:
    """Drives the shared console with the device's per-frame model (begin_frame ->
    handle_input -> handle_pointer -> frame), exposing the simulator's
    press/hold/type_char/click/frame/rgb888 interface so the pygame + headless
    loops stay simple."""

    def __init__(self, ws):
        self.ws = ws
        self.input = ws.input
        self.pointer = ws.pointer
        self._pending = []      # one-frame button presses
        self._held_ext = set()  # buttons held via hold() -- press cleanup must not
                                # release them (a browser Backspace key-repeat maps to
                                # HOME presses that used to clobber bshold's sustained
                                # hold, resetting the hold-to-exit toast every repeat)
        self._typed = []        # queued typed chars; frame() feeds ONE per frame
        self._key_held = 0      # physically held printable key (khold latch): in
                                # game mode it streams as last_key every frame,
                                # the web twin of the device raw-matrix hold
        self._key_prev = 0      # last frame's fed byte (repeats need a 0 gap)
        self._click = False
        self._down = False      # touch/button currently held (for drag-scroll)
        self._tap = False       # click(): auto-release at the end of the frame
        self._pan = (0, 0)      # held-arrow trackball velocity (dx, dy in [-1,1])

    # -- input the sim feeds in ---------------------------------------------
    def press(self, name):
        self._pending.append(name)

    def hold(self, name, down):
        if down:
            self._held_ext.add(name)
        else:
            self._held_ext.discard(name)
        self.input.set_held(name, down)

    def type_char(self, code):
        # QUEUE, not last-wins (#42 Thread 2): the console consumes ONE last_key per
        # frame, but a browser WS batch can carry many typed chars at once (a phone
        # soft keyboard swipe-typing/autocorrect-committing a whole word) -- a bare
        # `self._typed = code` kept only the final char ("hello" typed only "o").
        # frame() drains one char per frame, preserving order.
        self._typed.append(code)

    def key_hold(self, code, down):
        # The khold latch (web_view.apply_events routes {type:"khold"} here).
        # Release matches case-insensitively: shift down/up mid-hold changes
        # the browser's e.key case between the edges.
        c = int(code)
        if down:
            self._key_held = c
        elif self._key_held and (self._key_held == c or abs(self._key_held - c) == 32):
            self._key_held = 0

    def pan(self, dx, dy):
        # Arrow keys = the trackball: a relative, *visible*-cursor nudge each frame.
        self._pan = (dx, dy)

    def touch(self, x, y):
        # Mouse = the touchscreen: place the pointer absolutely (cursor hidden, like
        # a finger) and register a tap.
        self.pointer.place(int(x), int(y))
        self._click = True
        self._down = True

    def touch_drag(self, x, y):
        self.pointer.place(int(x), int(y))   # drag with the button down (no tap)
        self._down = True

    def hover(self, x, y):
        """Pointer position with NO button down -- the mouse as a cursor.

        The exact twin of the browser's `hover` event (web_input.apply_events):
        `place` and nothing else. Deliberately not `touch_drag`, which asserts
        `down` and would fake a drag out of an idle mouse -- drag-scrolling a
        grid, or moving a window, just by crossing it.

        The shell has real hover feedback (the desk icon highlight, the cards
        grid's msel) and web_input's note claimed the pygame sim got it for free
        "because that loop reads the mouse every frame". It does not and never
        did -- it only placed the pointer while a button was down, so the sim's
        shell looked as dead under the cursor as the browser's did before 2026-
        07-31. Same fix, same shape, one tier late.
        """
        self.pointer.place(int(x), int(y))

    def touch_up(self):
        self._down = False

    def click(self, x, y):
        """A full TAP for tests/scripts: the press edge this frame PLUS a release
        pass at the end of the same frame() call. Grid cards activate on release
        (Launcher.pointer_frame's drag/tap disambiguation), so a click must not
        leave the finger down the way touch() deliberately does -- touch()/
        touch_drag()/touch_up() remain the held-gesture verbs."""
        self.touch(x, y)
        self._tap = True

    @property
    def menu_view(self):
        return self.ws.menu_view

    def in_code_editor(self):
        return self.ws.screen == "menu" and self.ws.menu_view == "code"

    def in_text_mode(self):
        # A RUNNING cart that opted into text input via textmode(True) (#38/#42).
        # The pygame loop routes typed unicode to the cart's key() when this is true
        # (as it does for the code editor), so a cart text field can be typed into.
        return (self.ws.screen == "desktop"
                and bool(getattr(self.ws.input, "text_mode", False)))

    def escape(self):
        """Leave an open menu/editor panel back to the desktop."""
        if self.ws.screen == "menu":
            self.ws._leave_menu()

    # -- per-frame tick ------------------------------------------------------
    def frame(self, dt):
        dx, dy = self._pan
        # ARROWS ON A MOUSE TIER (owner call 2026-07-31): the browser/sim page no
        # longer steers a virtual cursor with the arrow keys -- the MOUSE is the
        # cursor there -- so arrows arrive as held left/right/up/down (the same
        # buttons w/a/s/d send) and drive menu navigation. The one surface that
        # still wants them as a DIRECTION is the code editor's caret, which used
        # to ride the pan path: translate the held nav buttons back into a nav
        # step here, and swallow them so the shell does not ALSO act on them.
        # (`_pan` stays wired for real trackball backends; the T-Deck's own
        # driver is untouched by this file.)
        if not (dx or dy) and self._held_ext and self.in_code_editor():
            ndx = (1 if "right" in self._held_ext else 0) \
                - (1 if "left" in self._held_ext else 0)
            ndy = (1 if "down" in self._held_ext else 0) \
                - (1 if "up" in self._held_ext else 0)
            if ndx or ndy:
                self.ws.nav(ndx, ndy)
                for _n in ("left", "right", "up", "down"):
                    self._held_ext.discard(_n)
                    self.input.set_held(_n, False)
        if dx or dy:
            if self.in_code_editor():
                self.ws.nav(dx, dy)          # arrows move the caret in the editor
            else:
                self.pointer.move(dx * PAN_SPEED, dy * PAN_SPEED)   # trackball nudge
        for name in self._pending:
            self.input.set_held(name, True)
        self.input.begin_frame()
        # One queued byte per frame -- and in TEXT MODE, never the same byte in
        # two ADJACENT frames: the editors' KeyEdge dedups identical consecutive
        # bytes (it models the T-Deck's discrete press edges + the P4 BLE
        # keyboard's held level state), so a queued repeat ("ll" in "hello",
        # backspace-backspace, a soft keyboard's delete autorepeat) must ship a
        # 0 GAP frame between the two -- or every second keystroke is silently
        # dropped (found via the phone's Backspace in the code editor). GAME
        # mode keeps the raw contiguous stream: there a repeated byte IS the
        # held-key latch the key()/keyp() cart API reads (v0.4 semantics).
        nxt = 0
        if self._typed:
            if (self._typed[0] != self._key_prev
                    or not getattr(self.input, "text_mode", False)):
                nxt = self._typed.pop(0)
        if (nxt == 0 and self._key_held
                and not getattr(self.input, "text_mode", False)):
            nxt = self._key_held     # game mode: a held key streams every frame
        self.input.last_key = nxt
        self._key_prev = nxt
        self.pointer.down = self._down
        self.pointer.click = self._click
        self.ws.handle_input()
        self.ws.handle_pointer()
        self.ws.frame(dt)
        for name in self._pending:
            if name not in self._held_ext:     # never release an explicit hold()
                self.input.set_held(name, False)
        self._pending = []
        self._click = False
        self.input.last_key = 0
        if self._tap:
            # click()'s release pass: lift the synthetic finger and route one
            # more pointer pass so release-activated surfaces (the launcher/
            # picker card grids) complete the tap within this frame() call.
            # The #44 gate's pointer snapshot never samples this intermediate
            # state (the next click restores it exactly), so mark the repaint
            # explicitly -- whatever the release changed must reach the pixels.
            self._tap = False
            self._down = False
            self.pointer.down = False
            self.pointer.click = False
            self.ws.handle_pointer()
            self.ws.mark_dirty()
            if self.ws._deferred:
                # #184: taps SCHEDULE their heavy transition now (run at the
                # next frame's tail, behind the LOADING paint). This release
                # pass ran after ws.frame(), so honor click()'s "the tap
                # completes within this frame() call" contract by draining
                # here -- click() is the tests/scripts verb; real mouse input
                # rides touch()/touch_up() and the normal frame cadence.
                self.ws._run_deferred()

    def rgb888(self):
        # The SYSTEM canvas is what the panel/window shows (the composited viewport +
        # responsive desktop chrome). When it's the same object as the game canvas
        # (320x240 degradation) this is exactly today's output (#39).
        return self.ws.sys_canvas.to_rgb888()

    def current_canvas(self):
        return self.ws.sys_canvas
