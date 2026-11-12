"""Host glue that runs the SHARED console (runtime/shell/console.py -- the exact same UI
the T-Deck runs) on the PC: a 320x240 host Canvas, the petme128 font, the
moy_carts .moy store, a host make_api, and a mouse/keyboard driver.

This is what makes the simulator a faithful emulator: the launcher/desktop/cards/
code/paint pixels come from the same `console.Workstation` as the device -- only
the canvas backend, the cart store's filesystem, and the input source differ.
"""

import json
import os

from . import players as _players
from . import console
from . import moy_carts  # noqa: E402  (shared .moy store; host-clean)
from . import moy_store_base as _store_base  # noqa: E402
from . import moy_catalogue  # noqa: E402  (the store's carts-by-handle interface)
# The RASTER is the boards' (`device_canvas.DeviceCanvas`, RGB565), reached
# through the two factories in host_canvas.py. There is no host-only canvas
# class any more -- runtime/canvas.py, the second raster, is deleted.
from . import host_canvas  # noqa: E402
from .moy_image import Image  # noqa: E402,F401  (re-exported: host_app.Image)
# The pure-Python cart-API backend (make_api + ConsoleDriver + the fake services),
# extracted to host_api.py so non-CPython targets can freeze it (#151 web runner);
# re-exported here so host_app.make_api / .ConsoleDriver / ... are unchanged for
# the sim, the web console, and the tests.
from .host_api import (ConsoleDriver, FakeWifi,  # noqa: E402,F401
                       _Layer, _NullComp, _decode_moyimg, make_api, make_wifi)
from .audio_session import PcmPump  # noqa: E402
from .input import InputState  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
WIDTH, HEIGHT = 320, 240        # the fixed GAME canvas (the console spec)
HOST_INPUTS = (("local", "keys"),)   # the inputs it declares (Workstation.use_inputs)


class SdlPump(PcmPump):
    """The host's speaker (#16): the kernel's mix pulled once a frame and queued
    on a pygame.mixer channel so blocks play back to back. Without an audio
    device (headless, CI) it pulls and discards, so the sequencers still
    advance exactly as they do with one."""

    def __init__(self, rate=22050):
        PcmPump.__init__(self, rate)
        self._ok = False
        self._pygame = None
        self._chan = None
        self._keep = None        # the in-flight Sound, kept alive
        try:
            import pygame
            pygame.mixer.quit()  # reset any default (44.1k stereo) init to our format
            pygame.mixer.init(frequency=self.rate, size=-16, channels=1, buffer=512)
            self._pygame = pygame
            self._chan = pygame.mixer.Channel(0)
            self._ok = True
        except Exception:        # no audio device (headless/CI)
            self._ok = False

    def tick(self, dt):
        pcm = self.block(self.frames(dt))
        if not self._ok or not pcm:
            return
        try:
            snd = self._pygame.mixer.Sound(buffer=pcm)
            if self._chan.get_busy():
                self._chan.queue(snd)
            else:
                self._chan.play(snd)
            self._keep = snd
        except Exception:
            self._ok = False              # the device dropped out


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
    sockets, mirroring the device `network.WLAN` service. scan/connect
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


class _HostResponse:
    def __init__(self, resp, status, length):
        self._r = resp
        self.status = status
        self.length = length

    def readinto(self, buf):
        return self._r.readinto(buf) if self._r is not None else 0

    def close(self):
        r, self._r = self._r, None
        if r is not None:
            r.close()


class HostCartNet:
    """The network Get Carts fetches through on the host (#124):
    `runtime/carts/cart_index.py`'s transport over urllib, which follows redirects
    and checks certificates.
    The board's twin is device/wire_links.CartNet; both answer `dial()`,
    `ready()` and `open(url)` -> status / length / readinto / close (and this
    one `online()`, the shape a transport with no `ready()` keeps).
    The PC is already on a network, so there is nothing to dial."""

    def __init__(self, timeout=30):
        self.timeout = timeout

    def online(self):
        return True

    def dial(self):
        pass

    def ready(self):
        return True

    def open(self, url):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "moybyte-carts"})
        try:
            r = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            return _HostResponse(None, exc.code, None)
        length = r.headers.get("Content-Length")
        return _HostResponse(r, r.status, int(length) if length else None)


def make_host_cart_net():
    """Live-sim factory for Get Carts' network (simulate_desktop wires it;
    tests wire a transport over their own local server, or none)."""
    return HostCartNet()


def _manifest_title(cart_dir):
    with open(os.path.join(cart_dir, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)["title"]


def _seed_system_carts(carts_dir):
    """Copy the read-only system .moy folders into the user store so the launcher shows
    them (and the child duplicates/edits copies), by the rules a board seeds its
    built-ins by (#47, moy_seed.seed_folders over the store as a cache): a built-in
    whose shipped manifest "version" is newer than the seeded copy is RE-SEEDED, its
    tuning and saves (config.json, pmem.json) kept."""
    os.makedirs(carts_dir, exist_ok=True)
    names = ([n for n in sorted(os.listdir(SYSTEM_CARTS)) if n.endswith(".moy")]
             if os.path.isdir(SYSTEM_CARTS) else [])
    moy_carts.sweep_store(carts_dir, [{"title": _manifest_title(
        os.path.join(SYSTEM_CARTS, n))} for n in names])
    _store_base.seeded(carts_dir, names)
    if names:
        moy_carts.seed_folders(SYSTEM_CARTS, carts_dir, record=False)


def _bind_media_keys(table):
    """The media service answers the MEDIA records on `table`: none where the
    host has no C compiler for it."""
    try:
        from . import moy_media
        moy_media.keys_on(table)
    except (ImportError, OSError):  # pragma: no cover - no host build of the C
        pass


def build_workstation(carts_dir=None, sys_size=None, font_scale=1,
                      windowed=False, panel_diagonal_in=None, first_variant=None,
                      kernel_input=False):
    """Build the shared console.Workstation wired to host backends.
    `first_variant` ("dark"/"light") is the theme variant of a first run, as
    `console.wire_workstation_core` takes it.

    The two-domain seam (#39): `sys_size` is the SYSTEM canvas size (w, h) -- the
    panel/window the desktop renders on, responsive. The GAME canvas is always the
    fixed 320x240 the carts + cart API draw on. When `sys_size` is None or 320x240
    (the T-Deck default) the system canvas IS the game canvas (one object), so the
    desktop is pixel-identical to today. `font_scale` (1/2/3) is the initial
    system-UI font size (the persisted system/settings value overrides it on load).

    `windowed=True` installs the Picotron-style windowed WM (wm_windowed.py --
    the big-screen / P4 presentation, #73/#58): the launcher is the desktop and
    every pushed app is a floating window. Needs a distinct big `sys_size`;
    silently ignored on the shared-canvas 320x240 build.

    `panel_diagonal_in` is the board fact a BOARD declares in its board.toml
    (#203): the glass's diagonal in inches, from which the chrome tap-target
    floor is derived. None -- every simulated tier by default -- keeps chrome on
    the font scale.

    `kernel_input=True` makes the console's input the kernel's table
    (moy_input_kernel), as a board's console binds it (device/wire_input.py),
    so the device APIs' records (the radio's, the microphone's), which their
    drivers post there, wake a Python program's `on(fn)`. One console a
    process may hold it (the simulator's); by default a console's table is
    its own, so two consoles in one process never share input."""
    carts_dir = carts_dir or os.path.expanduser("~/.moybyte/carts")
    _seed_system_carts(carts_dir)
    carts = moy_catalogue.catalogue(carts_dir)
    sw, sh = sys_size if sys_size else (WIDTH, HEIGHT)
    # The system canvas must be at least the game size -- the game is composited into
    # it as a viewport, so a smaller panel makes no sense (and would letterbox into
    # nothing). Clamp up so a stray small --size never produces a broken surface.
    sw = max(WIDTH, int(sw))
    sh = max(HEIGHT, int(sh))
    # This console's lifetimes by tag (device_canvas `_owners`): a host
    # process runs several consoles (a test drives two side by side), and a
    # run end in one must never return the buffers another is drawing into.
    owners = host_canvas.ConsoleOwners()
    if (sw, sh) == (WIDTH, HEIGHT) and int(font_scale) <= 1:
        sys_canvas = None                        # share one canvas -> identical to today
        # The SHARED-canvas tier (the T-Deck's arrangement): one surface is both
        # the game raster AND the glass, so it must carry the system-surface
        # contract -- `to_rgb888` for pygame/GIF readout, and `blit_cover`/
        # `set_font_scale` for the moment a cart declares a smaller raster and
        # console.bind_run_canvas PROMOTES this canvas to system canvas. At
        # font_scale 1 a HostSystemCanvas draws byte-identically to the plain
        # game canvas, so this costs nothing but the extra verbs.
        canvas = host_canvas.make_system_canvas(WIDTH, HEIGHT)
    else:
        canvas = host_canvas.make_canvas(WIDTH, HEIGHT)   # the fixed 320x240 GAME canvas
        sys_canvas = host_canvas.make_system_canvas(sw, sh, font_scale=font_scale,
                                                    desk=windowed)
    for cv in (canvas, sys_canvas):
        if cv is not None:
            cv._owners = owners
    if kernel_input:
        from . import moy_input as _mi
        inp = InputState(_mi._lib().moy_input_kernel())
    else:
        inp = InputState()
    ws = console.Workstation(_NullComp(), canvas, inp, carts,
                             sys_canvas=sys_canvas, font_scale=font_scale,
                             panel_diagonal_in=panel_diagonal_in)
    # The host's inputs: its keyboard, on the table's own source. Its mouse is
    # a mouse, not a finger, so a click leaves the keys showing.
    ws.use_inputs(HOST_INPUTS)
    # Its media keys are MEDIA records on this table, which the media service
    # answers (a board's console and the service share the kernel's).
    _bind_media_keys(inp)
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster plays on its own Canvas; the WM composites it up like a view.
    ws.make_game_canvas = lambda w, h, owner=None: host_canvas.make_canvas(
        w, h, owner, owners)
    # ONE Lua runtime on the host, and it is the boards': device/moycore_glue.py
    # over runtime/host/moycore.py, the C a board runs (moycore_lua.c) and the
    # kernel's Player (moy_play.c) by ctypes. A "lua" cart with no binding opens
    # the Player's runtime-missing panel, exactly as a device build without the
    # module does. A "wasm" cart runs on runtime/wasm_binding: libmoy's import
    # table over WAMR built for Linux at the boards' pin; no compiler (or no
    # WAMR) is an absent key.
    host_canvas.install()
    import moycore_glue                 # device/, on the path host_canvas puts it
    try:
        from runtime import wasm_host
    except ImportError:  # pragma: no cover
        import wasm_host

    runtimes = {}
    lua = moycore_glue.make_moycore_runtime(ws)
    if lua is not None:
        runtimes["lua"] = lua
    if wasm_host.available():
        runtimes["wasm"] = wasm_host.WasmHostRuntime(ws)
    from runtime import elf_binding
    if elf_binding.available():
        from runtime.elf_run import ElfRuntime
        runtimes["elf"] = ElfRuntime(ws, elf_binding)
    # The shared service wiring (console.wire_workstation_core -- one canonical
    # order for host + both boards). WiFi (#38) is the fake host service over the
    # same moy_carts wifi.json store the device uses; the pointer ranges over the
    # SYSTEM canvas (the surface the cursor moves on), so it's sized to that.
    console.wire_workstation_core(
        ws, moy_carts, carts_dir, make_api, make_wifi(moy_carts, carts_dir),
        audio_out=PcmPump(), runtimes=runtimes, can_manage=True,
        pointer=console.Pointer(ws.sys_canvas.w, ws.sys_canvas.h), inp=inp,
        first_variant=first_variant)
    # Multiplayer (#65): a host-side fake net transport (the sim's fake radio, for
    # net.*), so a "multiplayer"-permission cart runs in the sim. Unlinked here (a
    # solo desktop has no second console) -> send() drops; a test link()s two. The
    # PlayerRouter (extra controller slots) is attached to inp by wire_workstation_core.
    ws.net = _players.LoopbackNet()
    # The Picotron-style windowed WM (#73): swap the presentation tier in before
    # the first frame. Only meaningful with a distinct (big) system canvas.
    if windowed and ws._sys_canvas is not None:
        from runtime.wm_windowed import WindowedWM
        # A windowed composite must NOT letterbox: `blit_game` is asked to put a
        # cart inside a WINDOW here, and the fullscreen meaning of the same verb
        # (fill everything outside the game rect black) would paint over the
        # desk, its icons, the bar and every other window. See
        # DeviceCanvas.letterbox_composite (the flag is on the shared class, so
        # the wasm head's twin of this line -- web_boot.boot -- clears the same
        # one) -- the tier is chosen here, so this is where the canvas is told
        # which meaning it serves.
        ws._sys_canvas.letterbox_composite = False
        ws.wm = WindowedWM(ws)
        ws.open_desk()                 # boot onto the DESK (desktop mode)
    return ws
