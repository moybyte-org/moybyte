"""Host glue that runs the SHARED console (runtime/console.py -- the exact same UI
the T-Deck runs) on the PC: a 320x240 host Canvas, the petme128 font, the
kid_carts .kcart store, a host make_api, and a mouse/keyboard driver.

This is what makes the simulator a faithful emulator: the launcher/desktop/cards/
code/paint pixels come from the same `console.Workstation` as the device -- only
the canvas backend, the cart store's filesystem, and the input source differ.
"""

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
sys.modules.setdefault("blocks", _blocks)   # kid_carts.save_blocks does `import blocks`

from . import console  # noqa: E402  (after the editors/audio aliases above)
from . import kid_carts  # noqa: E402  (shared .kcart store; host-clean)
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
    needed. Mirrors the existing sim fakes (kidcode_sim fake audio,
    kidcode/audio.py AudioService.calls). The optional real-playback backend
    (SdlAudio, see docs/audio_design_v04.md) is a thin follow-on that pulls
    engine.render() from an SDL stream instead of just recording.

    `tick(dt)` renders a block each frame so render() is exercised on the same
    schedule the device's per-frame I2S feeder would use."""

    def __init__(self, engine):
        self.engine = engine
        self.calls = []           # [("sfx", n, chan), ("beep", f, d), ...]
        self.rendered = 0         # total PCM frames pulled via tick()

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
            self.engine.render(n)
            self.rendered += n


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
# Credentials persist through the SAME store the device uses (kid_carts
# load_wifi/remember_wifi/forget_wifi over wifi.json), so a connect() the kid
# makes in the sim survives a reload -- the host story matches the device story.


class FakeWifi:
    """Host WiFi backend: a faithful stand-in for the device network.WLAN service.

    `store`/`root` are the kid_carts credential store + its carts dir; when given,
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
                print("KidCode wifi remember failed:", exc)
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
                print("KidCode wifi forget failed:", exc)
        if self._ssid == ssid:
            self.disconnect()
        return True

    def known(self):
        """The remembered SSIDs (for the manager's 'saved' markers + autoconnect)."""
        if self._store is not None and self._root is not None:
            try:
                return [n["ssid"] for n in self._store.load_wifi(self._root)]
            except Exception as exc:  # noqa: BLE001
                print("KidCode wifi known failed:", exc)
        return []


def make_wifi(store=None, root=None):
    """Injected backend factory: the host FakeWifi over the kid_carts store.
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


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None,
             pmem=None, wifi=None):
    """The cartridge global namespace on the host -- same names/signature as the
    device make_api (TIC-80 draw API + sheet-or-Image spr + audio + tilemap), bound
    to a host Canvas and audio backend.

    `wifi` is the capability-gated network backend (#38): the Workstation passes it
    ONLY for a cart whose manifest permissions include "network", and we inject the
    `wifi` name into the namespace iff it is non-None -- so a normal kid cart gets
    no network access at all (the base key-set is identical either way)."""

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
            img = sheet.tile_span_image(int(n), int(w), int(h), colorkey)
        else:
            img = sheet.tile_image(int(n), colorkey)
        if img is not None:
            canvas.spr(img, x, y, scale, flip)

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
        # carts: (x, y, tapped) this frame, or None when there is no pointer.
        # `tapped` is the press edge so a cart scores at most one hit per tap. The
        # coords are GAME-canvas space (input.game_pointer, set by handle_pointer
        # from the viewport transform), so a cart in a larger system canvas reads the
        # 320x240 viewport, not the panel (#39). Falls back to the raw pointer.
        gp = getattr(input, "game_pointer", None)
        if gp is not None:
            return (gp[0], gp[1], bool(gp[2]))
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click))

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
        # restores game mode. Same name + behavior on the device (kid_runtime). The
        # Workstation applies it: on the host it gates char routing to the cart's
        # key(); on the device it flips the T-Deck keyboard ASCII<->raw. Resets to
        # game mode automatically when the cart exits to the desktop/home.
        input.text_mode = bool(on)

    def pmem_fn(index, value=None):
        # TIC-80 pmem(i[, v]): read pmem(i) -> int, write pmem(i, v) -> persists.
        if pmem is None:
            return 0
        return pmem.cell(index, value)

    ns = {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
        "spr_batch": spr_batch,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch, "mouse": mouse,
        "clip": canvas.clip, "camera": canvas.camera,
        "pal": canvas.pal, "palt": canvas.palt,
        "btn": input.held, "btnp": input.pressed,
        "key": key, "keyp": keyp, "time": time, "pmem": pmem_fn,
        "textmode": textmode,
        "cfg": cfg, "col": palette.color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": lambda rows, mapping, transparent=".": Image.from_ascii(rows, mapping, transparent),
    }
    if wifi is not None:                 # capability-gated network API (#38)
        ns["wifi"] = wifi
    return ns


class _NullComp:
    """The device flushes the panel via a compositor; the host reads the canvas
    directly, so this just satisfies Workstation.frame()'s flush() call."""
    def flush(self):
        pass


def _seed_system_carts(carts_dir):
    """Copy the read-only system .kcart folders into the user store on first run,
    so the launcher shows them (and the child duplicates/edits copies)."""
    os.makedirs(carts_dir, exist_ok=True)
    if not os.path.isdir(SYSTEM_CARTS):
        return
    for name in sorted(os.listdir(SYSTEM_CARTS)):
        if name.endswith(".kcart"):
            dst = os.path.join(carts_dir, name)
            if not os.path.exists(dst):
                shutil.copytree(os.path.join(SYSTEM_CARTS, name), dst)


def build_workstation(carts_dir=None, sys_size=None, font_scale=1):
    """Build the shared console.Workstation wired to host backends.

    The two-domain seam (#39): `sys_size` is the SYSTEM canvas size (w, h) -- the
    panel/window the desktop renders on, responsive. The GAME canvas is always the
    fixed 320x240 the carts + cart API draw on. When `sys_size` is None or 320x240
    (the T-Deck default) the system canvas IS the game canvas (one object), so the
    desktop is pixel-identical to today. `font_scale` (1/2/3) is the initial
    system-UI font size (the persisted system.json value overrides it on load)."""
    carts_dir = carts_dir or os.path.expanduser("~/.kidcode/carts")
    _seed_system_carts(carts_dir)
    carts = kid_carts.scan(carts_dir)
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
    ws.make_api = make_api
    ws.make_audio = make_audio
    ws.carts_store = kid_carts
    ws.carts_root = carts_dir
    # WiFi (#38): one fake system service shared across carts, persisting through
    # the same kid_carts wifi.json store the device uses. Injected into a cart's
    # namespace ONLY when its manifest grants "network" (see Workstation._start).
    ws.wifi = make_wifi(kid_carts, carts_dir)
    ws.can_manage = True
    # The pointer ranges over the SYSTEM canvas (the panel surface the cursor moves
    # on), so size it to that. The api touch() reads it in system coords.
    ws.pointer = console.Pointer(ws.sys_canvas.w, ws.sys_canvas.h)
    inp.pointer = ws.pointer       # touch-driven carts read it via the api touch()
    # Desktop shell (#28): load the system settings (system.json) and apply the
    # saved wallpaper so the home screen boots with the chosen backdrop.
    ws.load_system()
    # Unified top bar (Stage 1): build the 16x16 IconSheet the bar draws its chrome
    # icons from -- from system_icons.kgfx if present, else the baked default theme.
    ws.load_icon_sheet()
    # Achievements (#21): load the unlocked badges (achievements.json) so earned
    # milestones persist across reboots and the toast/view reflect them.
    ws.load_achievements()
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
        self._typed = 0
        self._click = False
        self._down = False      # touch/button currently held (for drag-scroll)
        self._pan = (0, 0)      # held-arrow trackball velocity (dx, dy in [-1,1])

    # -- input the sim feeds in ---------------------------------------------
    def press(self, name):
        self._pending.append(name)

    def hold(self, name, down):
        self.input.set_held(name, down)

    def type_char(self, code):
        self._typed = code

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
        self.touch(x, y)                      # a tap, for tests/scripts

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
        self.input.last_key = self._typed
        self.pointer.down = self._down
        self.pointer.click = self._click
        self.ws.handle_input()
        self.ws.handle_pointer()
        self.ws.frame(dt)
        for name in self._pending:
            self.input.set_held(name, False)
        self._pending = []
        self._typed = 0
        self._click = False
        self.input.last_key = 0

    def rgb888(self):
        # The SYSTEM canvas is what the panel/window shows (the composited viewport +
        # responsive desktop chrome). When it's the same object as the game canvas
        # (320x240 degradation) this is exactly today's output (#39).
        return self.ws.sys_canvas.to_rgb888()

    def current_canvas(self):
        return self.ws.sys_canvas
