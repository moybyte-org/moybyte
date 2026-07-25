"""Host glue that runs the SHARED console (runtime/console.py -- the exact same UI
the T-Deck runs) on the PC: a 320x240 host Canvas, the petme128 font, the
moy_carts .moy store, a host make_api, and a mouse/keyboard driver.

This is what makes the simulator a faithful emulator: the launcher/desktop/cards/
code/paint pixels come from the same `console.Workstation` as the device -- only
the canvas backend, the cart store's filesystem, and the input source differ.
"""

import json
import os
import random
import shutil
import sys

# console.py uses `from editors import ...` and `from audio import ...` (its frozen
# device names). Register the canonical runtime/editors.py and runtime/audio.py under
# those bare names so console.py imports them on the host too.
from . import audio as _audio
from . import blocks as _blocks
from . import editors as _editors
sys.modules.setdefault("editors", _editors)
sys.modules.setdefault("audio", _audio)
sys.modules.setdefault("blocks", _blocks)   # moy_carts.save_blocks does `import blocks`
# block_editor_ui.py / map_editor_ui.py / music_editor_ui.py are the block/map/
# music editors' UI (issues #29 Part 2 / #32 / #50, extracted from console.py);
# each does `from editors import ...` (needs the alias above, music_editor_ui.py
# also does `from audio import ...`, needs the alias above too) and console.py
# does `from block_editor_ui import BlockEditorUI, ...` / `from map_editor_ui
# import MapEditorUI, ...` / `from music_editor_ui import MusicEditorUI, ...`
# (their own frozen device names), so they need the same bare-name aliasing --
# imported only after the editors/audio/blocks aliases above are in place.
from . import block_editor_ui as _block_editor_ui
from . import map_editor_ui as _map_editor_ui
from . import music_editor_ui as _music_editor_ui
sys.modules.setdefault("block_editor_ui", _block_editor_ui)
sys.modules.setdefault("map_editor_ui", _map_editor_ui)
sys.modules.setdefault("music_editor_ui", _music_editor_ui)
# layers.py is the Layer protocol + the self-contained surface adapters; bar_layer.py
# is the top-bar/dock surface + the single source of the bar/dock geometry constants
# (extracted from console.py). console.py does `from layers import ...` / `from
# bar_layer import ...` (its frozen device names). Both are dependency-free leaves, so
# they only need the same bare-name alias.
from . import ui as _ui
sys.modules.setdefault("ui", _ui)   # the shared widget toolkit: editor_app + the
                                    # apps do `import ui` (their frozen device name)
from . import calc_app as _calc_app
sys.modules.setdefault("calc_app", _calc_app)   # console does `from calc_app import ...`
from . import bar_layer as _bar_layer
from . import cards_layer as _cards_layer
from . import paint_layer as _paint_layer
from . import settings_layer as _settings_layer
from . import code_layer as _code_layer
from . import widgets as _widgets
from . import wallpaper as _wallpaper
from . import launcher_layer as _launcher_layer
from . import project as _project
from . import player as _player
from . import editor_app as _editor_app
from . import wm as _wm
from . import layers as _layers
sys.modules.setdefault("layers", _layers)
sys.modules.setdefault("bar_layer", _bar_layer)
sys.modules.setdefault("cards_layer", _cards_layer)
sys.modules.setdefault("paint_layer", _paint_layer)
sys.modules.setdefault("settings_layer", _settings_layer)
sys.modules.setdefault("code_layer", _code_layer)
sys.modules.setdefault("widgets", _widgets)
sys.modules.setdefault("wallpaper", _wallpaper)
sys.modules.setdefault("launcher_layer", _launcher_layer)
sys.modules.setdefault("project", _project)   # console.py does `from project import Project`
sys.modules.setdefault("player", _player)     # console.py does `from player import Player`
sys.modules.setdefault("editor_app", _editor_app)  # console.py does `from editor_app import EditorApp`
sys.modules.setdefault("wm", _wm)             # console.py does `from wm import FullscreenStackWM`
from . import players as _players             # #65 multiplayer: PlayerRouter + net seam
sys.modules.setdefault("players", _players)   # console.py does `from players import PlayerRouter`

from . import console  # noqa: E402  (after the editors/audio aliases above)
from . import moy_carts  # noqa: E402  (shared .moy store; host-clean)
from . import palette  # noqa: E402
from .canvas import Canvas, Image, SystemCanvas  # noqa: E402
from .input import InputState  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
WIDTH, HEIGHT = 320, 240        # the fixed GAME canvas (the console spec)
PAN_SPEED = 6            # px/frame the arrow-keys-as-trackball nudge the cursor


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


class SdlAudio(FakeAudio):
    """Real desktop playback backend (#16): like FakeAudio (records calls + drives
    the engine) but ALSO streams the rendered PCM to the speakers via pygame.mixer,
    so audio can be evaluated on the PC before the device's I2S path. Each frame it
    renders one block (signed-16-bit mono LE, the engine's native format) and queues
    it on a channel so blocks play back-to-back. Falls back to silent (plain
    FakeAudio behavior) if no audio device is available, so headless runs never
    crash."""

    def __init__(self, engine):
        FakeAudio.__init__(self, engine)
        self._ok = False
        self._pygame = None
        self._chan = None
        self._keep = None        # hold a ref to the in-flight Sound so it isn't GC'd
        try:
            import pygame
            pygame.mixer.quit()  # reset any default (44.1k stereo) init to our format
            pygame.mixer.init(frequency=engine.rate, size=-16, channels=1, buffer=512)
            self._pygame = pygame
            self._chan = pygame.mixer.Channel(0)
            self._ok = True
        except Exception:        # no audio device (headless/CI) -> silent fallback
            self._ok = False

    def tick(self, dt):
        n = int(self.engine.rate * (dt if dt > 0.0 else 0.0))
        if n <= 0:
            return
        pcm = self.engine.render(n)   # advance the mixer; bytes of LE int16 mono
        self.rendered += n
        if not self._ok or not pcm:
            return
        try:
            snd = self._pygame.mixer.Sound(buffer=pcm)
            if self._chan.get_busy():
                self._chan.queue(snd)     # play right after the current block
            else:
                self._chan.play(snd)
            self._keep = snd
        except Exception:
            self._ok = False              # stop trying if the device drops out


def make_sdl_audio(engine):
    """Factory for the real desktop-playback backend (simulate_desktop wires this
    for live windowed runs; tests/headless keep make_audio's FakeAudio)."""
    return SdlAudio(engine)


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
        system state) and the creds persist to disk for autoconnect."""
        self._connected = True
        self._ssid = str(ssid)
        if self._store is not None and self._root is not None:
            try:
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


def _real_local_ip():
    """The desktop's real outbound LAN IP (no packet sent), or None. Lets the live
    sim report the actual connection so network features (web editor #22, AI #8)
    bind to / report a real IP on the host instead of a placeholder."""
    import socket
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # no traffic; just resolves the route
        return s.getsockname()[0]
    except Exception:  # noqa: BLE001 -- offline / sandboxed -> fall back
        return None
    finally:
        if s is not None:
            s.close()


class HostWifi(FakeWifi):
    """Live-sim WiFi backend that reports the *real* desktop connection -- your PC
    is already online, so `status()` returns the actual LAN IP and `scan()` lists
    the real network alongside the canned demo APs. This makes the desktop sim a
    genuine testbed for network features (#22 web editor, #8 AI) over real Python
    sockets, with the device `network.WLAN` as the unverified port. scan/connect
    stay light (we don't manage the OS's WiFi); the value is real status/IP. Falls
    back to FakeWifi when offline."""

    def status(self):
        ip = _real_local_ip()
        if ip:
            return (True, self._ssid or "desktop", ip)
        return FakeWifi.status(self)

    def scan(self):
        nets = list(FakeWifi.scan(self))
        if _real_local_ip():
            nets.insert(0, (self._ssid or "desktop", 99, False))
        return nets


def make_host_wifi(store=None, root=None):
    """Live-sim factory: real-connection-aware WiFi (simulate_desktop wires this for
    interactive runs; tests/headless keep the deterministic FakeWifi)."""
    return HostWifi(store, root)


def _decode_moyimg(text):
    """Decode a .moyimg paint-image asset (#63 Fold 3) into (w, h, index_bytes), or
    None on any error (a bad image just doesn't draw). The blob is a JSON header
    {w, h, data} where `data` is base64 of the zlib-compressed MOY64 index bitmap
    (1 byte/pixel) -- the same base64+zlib envelope sprites author with, so the host
    inflates it with CPython's zlib (the device mirror in moy_runtime uses `deflate`)."""
    try:
        shared = moy_carts.decode_moyimg(text)
        if shared is not None:
            return shared
        import binascii
        import zlib
        meta = json.loads(text)
        w = int(meta["w"])
        h = int(meta["h"])
        idx = zlib.decompress(binascii.a2b_base64(meta["data"]))
        return (w, h, idx)
    except Exception:  # noqa: BLE001 -- bad/absent image -> caller gets None
        return None


class _Layer:
    """A scroll background (#54): a wider off-screen canvas the cart pre-renders a
    level into ONCE, then window-copies to the screen per frame via draw_layer. Exposes
    the draw verbs (sheet/tilemap-aware, pixel-identical to the main api) bound to its
    OWN canvas, plus W/H. Built by the api's make_layer(w, h). Mirrors moy_runtime."""

    _VERBS = ("cls", "pix", "line", "rect", "rectb", "circ", "circb",
              "spr", "spr_batch", "map", "mget", "mset", "print",
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

    def spr_batch(items, colorkey=-1, scale=1):
        # spr_batch(items[, colorkey, scale]): draw MANY sheet tiles in one call (#43)
        # -- the sprite analogue of map(). `items` is a sequence of (tile, x, y) or
        # (tile, x, y, flip) tuples (flip 0=none/1=h/2=v/3=both, like spr()); colorkey +
        # scale apply uniformly to the whole batch. Coords are world space (camera +
        # clip apply), tiles come from the cart's sheet. On the device this is ONE
        # native blit_batch call for N sprites (the draw-call count is its FPS
        # bottleneck); here it's the readable per-item reference. SHEET TILES ONLY,
        # 1x1 tiles -- Image sprites and multi-tile (w/h>1) sprites still use spr().
        if sheet is None:
            return
        canvas.spr_batch(sheet, items, colorkey, scale)

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
        return console._ticks_diff(console._ticks_ms(), start)

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

    def btn(name, player=0):
        if player:
            return _prouter.held(name, player) if _prouter is not None else False
        return input.held(name)

    def btnp(name, player=0):
        if player:
            return _prouter.pressed(name, player) if _prouter is not None else False
        return input.pressed(name)

    def players():
        # The connected player count (>=1) so a cart can offer a 2P/co-op mode.
        return _prouter.count() if _prouter is not None else 1

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
        "spr_batch": spr_batch,
        "background": background, "_moy_restore_bg": _restore_bg,
        "make_layer": make_layer, "draw_layer": draw_layer,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": btn, "btnp": btnp, "players": players,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode, "quit": _quit,
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
                _rp, _rw, _rh = _widgets.rotate_indices(
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


_SEED_PRESERVE = ("config.json", "pmem.json")   # the kid's tuning + saves, kept across a re-seed


def _manifest_version(cart_dir):
    """The integer "version" in a cart folder's manifest.json (0 if missing/unreadable),
    so a newer shipped version supersedes a stale seeded copy -- mirrors the device's
    _cart_version (#47)."""
    try:
        with open(os.path.join(cart_dir, "manifest.json"), encoding="utf-8") as f:
            return int(json.load(f).get("version", 0))
    except (OSError, ValueError, TypeError):
        return 0


def _seed_system_carts(carts_dir):
    """Copy the read-only system .moy folders into the user store so the launcher shows
    them (and the child duplicates/edits copies). Version-aware (#47): a built-in whose
    shipped manifest "version" is newer than the seeded copy is RE-SEEDED -- code + art
    refreshed, while the kid's tuning + saves (config.json, pmem.json) are preserved --
    matching the device's seed_builtins, so a bumped cart actually propagates on the host
    (it used to seed once and ignore version bumps)."""
    os.makedirs(carts_dir, exist_ok=True)
    if not os.path.isdir(SYSTEM_CARTS):
        return
    for name in sorted(os.listdir(SYSTEM_CARTS)):
        if not name.endswith(".moy"):
            continue
        src = os.path.join(SYSTEM_CARTS, name)
        dst = os.path.join(carts_dir, name)
        if not os.path.exists(dst):
            shutil.copytree(src, dst)
        elif _manifest_version(src) > _manifest_version(dst):
            kept = {}
            for f in _SEED_PRESERVE:                 # snapshot the kid's data
                p = os.path.join(dst, f)
                if os.path.isfile(p):
                    with open(p, "rb") as fh:
                        kept[f] = fh.read()
            shutil.rmtree(dst)
            shutil.copytree(src, dst)                # refresh code + art
            for f, data in kept.items():             # restore the kid's data
                with open(os.path.join(dst, f), "wb") as fh:
                    fh.write(data)


def build_workstation(carts_dir=None, sys_size=None, font_scale=1, windowed=False):
    """Build the shared console.Workstation wired to host backends.

    The two-domain seam (#39): `sys_size` is the SYSTEM canvas size (w, h) -- the
    panel/window the desktop renders on, responsive. The GAME canvas is always the
    fixed 320x240 the carts + cart API draw on. When `sys_size` is None or 320x240
    (the T-Deck default) the system canvas IS the game canvas (one object), so the
    desktop is pixel-identical to today. `font_scale` (1/2/3) is the initial
    system-UI font size (the persisted system.json value overrides it on load).

    `windowed=True` installs the Picotron-style windowed WM (wm_windowed.py --
    the big-screen / P4 presentation, #73/#58): the launcher is the desktop and
    every pushed app is a floating window. Needs a distinct big `sys_size`;
    silently ignored on the shared-canvas 320x240 build."""
    carts_dir = carts_dir or os.path.expanduser("~/.moybyte/carts")
    _seed_system_carts(carts_dir)
    carts = moy_carts.scan(carts_dir)
    canvas = Canvas(WIDTH, HEIGHT)               # the fixed 320x240 GAME canvas
    sw, sh = sys_size if sys_size else (WIDTH, HEIGHT)
    # The system canvas must be at least the game size -- the game is composited into
    # it as a viewport, so a smaller panel makes no sense (and would letterbox into
    # nothing). Clamp up so a stray small --size never produces a broken surface.
    sw = max(WIDTH, int(sw))
    sh = max(HEIGHT, int(sh))
    if (sw, sh) == (WIDTH, HEIGHT) and int(font_scale) <= 1:
        sys_canvas = None                        # share one canvas -> identical to today
    else:
        sys_canvas = SystemCanvas(sw, sh, font_scale=font_scale)
    inp = InputState()
    ws = console.Workstation(_NullComp(), canvas, inp, carts,
                             sys_canvas=sys_canvas, font_scale=font_scale)
    # #67 dual-runtime seam: the lupa-backed Lua cart runtime, injected only when
    # lupa is importable (an optional dev dependency) -- without it a "lua" cart
    # opens the Player's runtime-missing panel, same as today's device builds.
    lua_runtime = None
    try:
        import lupa  # noqa: F401 -- availability probe only
        try:
            from lua_host import make_lua_runtime
        except ImportError:  # pragma: no cover - package-relative fallback
            from runtime.lua_host import make_lua_runtime
        lua_runtime = make_lua_runtime
    except ImportError:
        pass
    # The shared service wiring (console.wire_workstation_core -- one canonical
    # order for host + both boards). WiFi (#38) is the fake host service over the
    # same moy_carts wifi.json store the device uses; the pointer ranges over the
    # SYSTEM canvas (the surface the cursor moves on), so it's sized to that.
    console.wire_workstation_core(
        ws, moy_carts, carts_dir, make_api, make_wifi(moy_carts, carts_dir),
        make_audio=make_audio, lua_runtime=lua_runtime, can_manage=True,
        pointer=console.Pointer(ws.sys_canvas.w, ws.sys_canvas.h), inp=inp)
    # Multiplayer (#65): a host-side fake net transport (the sim's fake radio, for
    # net.*), so a "multiplayer"-permission cart runs in the sim. Unlinked here (a
    # solo desktop has no second console) -> send() drops; a test link()s two. The
    # PlayerRouter (extra controller slots) is attached to inp by wire_workstation_core.
    ws.net = _players.LoopbackNet()
    # The Picotron-style windowed WM (#73): swap the presentation tier in before
    # the first frame. Only meaningful with a distinct (big) system canvas.
    if windowed and ws._sys_canvas is not None:
        from runtime.wm_windowed import WindowedWM
        ws.wm = WindowedWM(ws)
        ws.open_desk()                 # two worlds (#105): boot onto the DESK
    return ws


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
        self._typed = []        # queued typed chars; frame() feeds ONE per frame
        self._key_prev = 0      # last frame's fed byte (repeats need a 0 gap)
        self._click = False
        self._down = False      # touch/button currently held (for drag-scroll)
        self._tap = False       # click(): auto-release at the end of the frame
        self._pan = (0, 0)      # held-arrow trackball velocity (dx, dy in [-1,1])

    # -- input the sim feeds in ---------------------------------------------
    def press(self, name):
        self._pending.append(name)

    def hold(self, name, down):
        self.input.set_held(name, down)

    def type_char(self, code):
        # QUEUE, not last-wins (#42 Thread 2): the console consumes ONE last_key per
        # frame, but a browser WS batch can carry many typed chars at once (a phone
        # soft keyboard swipe-typing/autocorrect-committing a whole word) -- a bare
        # `self._typed = code` kept only the final char ("hello" typed only "o").
        # frame() drains one char per frame, preserving order.
        self._typed.append(code)

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
        self.input.last_key = nxt
        self._key_prev = nxt
        self.pointer.down = self._down
        self.pointer.click = self._click
        self.ws.handle_input()
        self.ws.handle_pointer()
        self.ws.frame(dt)
        for name in self._pending:
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

    def rgb888(self):
        # The SYSTEM canvas is what the panel/window shows (the composited viewport +
        # responsive desktop chrome). When it's the same object as the game canvas
        # (320x240 degradation) this is exactly today's output (#39).
        return self.ws.sys_canvas.to_rgb888()

    def current_canvas(self):
        return self.ws.sys_canvas
