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
from . import editors as _editors
sys.modules.setdefault("editors", _editors)
sys.modules.setdefault("audio", _audio)

from . import console  # noqa: E402  (after the editors/audio aliases above)
from . import kid_carts  # noqa: E402  (shared .kcart store; host-clean)
from . import palette  # noqa: E402
from .canvas import Canvas, Image  # noqa: E402
from .input import InputState  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_CARTS = os.path.join(ROOT, "system_carts")
WIDTH, HEIGHT = 320, 240
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


def make_api(canvas, input, config, sheet=None, audio=None, tilemap=None):
    """The cartridge global namespace on the host -- same names/signature as the
    device make_api (TIC-80 draw API + sheet-or-Image spr + audio + tilemap), bound
    to a host Canvas and audio backend."""

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

    def spr(n, x, y, colorkey=-1, scale=1):
        if isinstance(n, Image):
            return canvas.spr(n, x, y, colorkey if colorkey != -1 else scale)
        if sheet is None:
            return
        img = sheet.tile_image(int(n), colorkey)
        if img is not None:
            canvas.spr(img, x, y, scale)

    def map_(mx=0, my=0, w=None, h=None, sx=0, sy=0, colorkey=-1, scale=1):
        # TIC-80 map(mx, my, w, h, sx, sy, colorkey, scale): blit a w x h region of
        # the cart's tilemap (top-left cell mx,my) to screen (sx,sy). Tiles are the
        # 8x8 sheet sprites; `scale` enlarges each (so scale=2 => 16px world tiles).
        if tilemap is None or sheet is None:
            return
        canvas.map(tilemap, sheet, mx, my, w, h, sx, sy, colorkey, scale)

    def mget(x, y):
        return tilemap.mget(x, y) if tilemap is not None else -1

    def mset(x, y, tile):
        if tilemap is not None:
            tilemap.mset(x, y, tile)

    def touch():
        # Pointer (mouse stands in for touch on the host) exposed to touch-driven
        # carts: (x, y, tapped) this frame, or None when there is no pointer.
        # `tapped` is the press edge so a cart scores at most one hit per tap.
        p = getattr(input, "pointer", None)
        if p is None:
            return None
        return (p.x, p.y, bool(p.click))

    return {
        "W": canvas.w, "H": canvas.h,
        "cls": canvas.cls, "pix": canvas.pix,
        "line": canvas.line, "rect": canvas.rect, "rectb": canvas.rectb,
        "circ": canvas.circ, "circb": canvas.circb, "spr": spr,
        "map": map_, "mget": mget, "mset": mset,
        "print": canvas.print, "touch": touch,
        "btn": input.held, "btnp": input.pressed,
        "cfg": cfg, "col": palette.color,
        "sfx": _sfx, "beep": _beep, "music": _music,
        "music_stop": _music_stop, "sound_stop": _sound_stop, "volume": _volume,
        "rnd": lambda n=1.0: random.random() * n,
        "flr": lambda x: int(x // 1),
        "Image": Image,
        "image": lambda rows, mapping, transparent=".": Image.from_ascii(rows, mapping, transparent),
    }


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


def build_workstation(carts_dir=None):
    """Build the shared console.Workstation wired to host backends."""
    carts_dir = carts_dir or os.path.expanduser("~/.kidcode/carts")
    _seed_system_carts(carts_dir)
    carts = kid_carts.scan(carts_dir)
    canvas = Canvas(WIDTH, HEIGHT)
    inp = InputState()
    ws = console.Workstation(_NullComp(), canvas, inp, carts)
    ws.make_api = make_api
    ws.make_audio = make_audio
    ws.carts_store = kid_carts
    ws.carts_root = carts_dir
    ws.can_manage = True
    ws.pointer = console.Pointer(WIDTH, HEIGHT)
    inp.pointer = ws.pointer       # touch-driven carts read it via the api touch()
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
        return self.ws.canvas.to_rgb888()

    def current_canvas(self):
        return self.ws.canvas
