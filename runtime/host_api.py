"""The HOST side of the cart-API backend: the driver + fake services, plus the
re-export of THE make_api.

make_api itself moved to `runtime/cart_api.py` on 2026-08-17 -- it existed here
and in device/device_api.py as ~80% line-identical twins, and the twin killed
that day had already drifted (the device's layer verbs lost `tline`, the host's
multi-tile spr had lost the #63 cache). What REMAINS here is what is genuinely
the host's: FakeAudio/FakeWifi (the recordable service fakes), ConsoleDriver
(the sim/web per-frame driver), _NullComp. host_app.py imports this back and
re-exports every name, so `host_app.make_api` / `.ConsoleDriver` / ... are
unchanged for the sim, the web console, and the tests. Everything stays
portable Python: no pygame, no sockets, no threading (#151: the web runner
freezes this module under MicroPython-WASM).
"""

try:                                    # staged/frozen flat namespace (web runner)
    from cart_api import (CART_BUTTONS, _Layer,  # noqa: F401 -- re-exports
                          _decode_moyimg, make_api)
except ImportError:                     # host: the runtime package
    from runtime.cart_api import (CART_BUTTONS, _Layer,  # noqa: F401
                                  _decode_moyimg, make_api)

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

    def _stored_password(self, ssid):
        """The password wifi.json holds for `ssid`, else None.

        DeviceWifi's helper of the same name, so both backends draw the same
        distinction between "this network is open" and "the store could not
        tell us".
        """
        if self._store is None or self._root is None:
            return None
        try:
            for n in self._store.load_wifi(self._root):
                if n["ssid"] == ssid:
                    return n.get("password", "") or ""
        except Exception:  # noqa: BLE001 -- a store hiccup reads as "unknown"
            pass
        return None

    def connect(self, ssid, password=""):
        """'Associate' with `ssid` (fake: always succeeds), remember the creds, and
        report connected. Returns True. The connection persists across carts (it's
        system state) and the creds persist to disk for autoconnect. An EMPTY
        password resolves to the stored one first (the DeviceWifi contract): the
        panel's known-network reconnect passes "", and remembering that "" used
        to overwrite the saved password in wifi.json.

        The remember condition is DeviceWifi's verbatim (stored when the radio
        associated, or when it is a new non-blank password) so the rule has one
        reading, not two -- the fake radio always associates, so it never bites
        here."""
        ok = True                       # the fake radio always associates
        self._ssid = str(ssid)
        stored = self._stored_password(self._ssid)
        if not password and stored:
            password = stored
        self._connected = ok
        if (ok or (password and password != stored)) \
                and self._store is not None and self._root is not None:
            try:
                self._store.remember_wifi(ssid, password, self._root)
            except Exception as exc:  # noqa: BLE001 -- a save failure must not crash the cart
                print("Moybyte wifi remember failed:", exc)
        return ok

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
