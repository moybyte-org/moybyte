"""Host glue that runs the SHARED console (runtime/console.py -- the exact same UI
the T-Deck runs) on the PC: a 320x240 host Canvas, the petme128 font, the
moy_carts .moy store, a host make_api, and a mouse/keyboard driver.

This is what makes the simulator a faithful emulator: the launcher/desktop/cards/
code/paint pixels come from the same `console.Workstation` as the device -- only
the canvas backend, the cart store's filesystem, and the input source differ.
"""

import json
import os
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
# The pure-Python cart-API backend (make_api + ConsoleDriver + the fake services),
# extracted to host_api.py so non-CPython targets can freeze it (#151 web runner);
# re-exported here so host_app.make_api / .ConsoleDriver / ... are unchanged for
# the sim, the web console, and the tests.
from .host_api import (PAN_SPEED, ConsoleDriver, FakeAudio, FakeWifi,  # noqa: E402,F401
                       _Layer, _NullComp, _decode_moyimg, make_api, make_audio,
                       make_wifi)
from .input import InputState  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
WIDTH, HEIGHT = 320, 240        # the fixed GAME canvas (the console spec)


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
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster plays on its own Canvas; the WM composites it up like a view.
    ws.make_game_canvas = lambda w, h: Canvas(w, h)
    # #67 dual-runtime seam: the lupa-backed Lua cart runtime, injected only when
    # lupa is importable (an optional dev dependency) -- without it a "lua" cart
    # opens the Player's runtime-missing panel, same as today's device builds.
    # Rung 4: a cart that stays inside the SPEC verb table runs on the boards'
    # own Lua (runtime/lua_binding -- libmoy's binding over the same vendored
    # 5.4, LUA_32BITS and all), so the host stops being a different program
    # from the device for it. A cart using moybyte's superset keeps lupa, which
    # supplies the whole namespace; that split is the same one moycore_glue
    # makes on device.
    lua_runtime = None
    _lupa_make = None
    try:
        import lupa  # noqa: F401 -- availability probe only
        try:
            from lua_host import make_lua_runtime as _lupa_make
        except ImportError:  # pragma: no cover - package-relative fallback
            from runtime.lua_host import make_lua_runtime as _lupa_make
    except ImportError:
        pass
    try:
        from runtime.lua_host import MoycoreHostRun, moycore_supports
    except ImportError:  # pragma: no cover
        from lua_host import MoycoreHostRun, moycore_supports

    def _make_lua(ns, src, _ws=ws):
        if moycore_supports(src):
            try:
                return MoycoreHostRun(_ws, ns, src)
            except RuntimeError as exc:
                # SAY SO. The fallback used to be silent, and that is how
                # moycore came to run none of the seed carts while every test
                # stayed green: make_layer's Layer would not marshal, the load
                # raised, lupa quietly took the cart, and the only observable
                # difference was a cart running on the runtime we were trying
                # to retire. The device has printed this since it shipped.
                print("Moybyte: moycore declined ->", exc)
                if _lupa_make is None:
                    raise
        if _lupa_make is None:
            raise RuntimeError("needs the Lua runtime (not in this build)")
        return _lupa_make(ns, src)

    if _lupa_make is not None or moycore_supports(""):
        lua_runtime = _make_lua
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
