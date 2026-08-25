"""Moybyte web runner boot (#151): the SHARED console under MicroPython-WASM.

The wasm RASTERIZES (moycore stage 4). This used to be the thinnest backend --
the system canvas was a `web_view.CommandCanvas` that recorded draw commands for
a JS replayer in the page, so the console never touched a pixel ("the browser is
the GPU"). That thesis is retired: the same `moy_gfx` + libmoy kernel the boards
run is compiled into this build, the canvas is literally `device_canvas.
DeviceCanvas` over `web_canvas.WebCompositor`, and the page's only job is to
blit the finished RGB565 framebuffer. See web_canvas.py for why the boards'
class runs unmodified in a browser.

What that deleted, beyond the recorder itself: the whole /assets pixel payload.
The page needed the palette, the font, the cart's sheet/tilemap/images and every
shelf cover in order to replay commands -- shipped incrementally, memoised, and
re-requested through an `imgWant` latch, all of which was the runner's most
delicate machinery. None of those bytes cross any more; `assets_json` is now
metadata (size, title, audio rate, input hint) and nothing else.

The cart API is the shared pure-Python `host_api.make_api` -- the same backend
the PC sim runs -- and input replays through the shared `web_input.apply_events`
decode, so host == device == browser stays one code path.

Scope: the FULL console, both presentation tiers -- `can_manage=True`, so the
Make tile and the editors are reachable in the browser. pmem/journal writes land
in the browser's MEMFS, so they are ephemeral: a reload resets the machine.

JS contract (see worker.js):
    boot(carts_root, cart=None, ...) -> build the Workstation
    assets_json()                 -> the page's metadata payload (JSON string)
    step_frame_json(dt, ahead)    -> tick one frame; "" when the redraw was
                                     skipped (#44 dirty gate), else a small
                                     JSON string; the PIXELS travel separately
    fb_addr() / fb_len()          -> the framebuffer's wasm-heap address and
                                     byte length, read by the worker
    apply_events_json(text)       -> feed an {"events":[...]} JSON text batch
    open_cart(name)               -> select+run a cart by folder name or title
"""

import json

import console
import host_api
import moy_carts
import web_canvas
import web_input
from input import InputState

_S = {}          # the runner singletons: ws / canvas / driver / sink
_AUDIO_RATE = [0]


# The page keeps ~this many seconds of PCM scheduled ahead of the audio clock.
# Deep enough to ride out a late frame (wasm GC pause, phone jank -- the 20ms
# scheduling margin alone was the owner-reported crackle), shallow enough that
# a game sfx mixed into the stream isn't noticeably late.
_AUDIO_TARGET = 0.12
_AUDIO_MAX_STEP = 0.20      # bound one frame's top-up render (seconds)


class _RunnerAudio(host_api.FakeAudio):
    """FakeAudio with the calls list capped, plus two web-runner twists
    (#170 round 3 -- the owner's "crackle and slowdown" report):

    * TOP-UP rendering (the crackle fix): the page reports how many seconds
      of PCM it still has scheduled (step_frame_json's audio_ahead); tick()
      renders whatever refills that to _AUDIO_TARGET instead of exactly
      rate*dt. One late frame now eats cushion, not the stream -- the browser
      twin of the device feed's ring top-up (device_audio.py). Without the
      report (ahead < 0: a transport that never sends it) the per-dt render
      stays, unchanged.
    * The synth is the NATIVE moy_audio module when the wasm was built with the
      usermod -- i.e. libmoy, moy-spec's own SPEC.md 8 implementation, compiled
      in (#97). It owns the bank, both sequencers and the mixer; this class only
      forwards the 8.2 verbs and pulls finished blocks. That is also the
      slowdown fix: the Python per-sample loop cost whole milliseconds per frame
      under wasm. Without the usermod it falls back to the shared Python engine,
      which is a twin of the same libmoy source, so browser == device == host
      stays one audible behaviour either way.

    step_frame_json drains take_pcm() into the frame payload and the page's
    playPCM plays the FINISHED samples (no JS synth), as before."""

    def __init__(self, engine):
        host_api.FakeAudio.__init__(self, engine)
        self.ahead = -1.0           # page-reported queue depth; <0 = no report
        try:
            import moy_audio
            self._ka = moy_audio
        except ImportError:
            self._ka = None
        self._bank_rev = None
        self._buf = bytearray(int(engine.rate * _AUDIO_MAX_STEP) * 2 + 64)
        if self._ka is not None:
            self._ka.set_rate(engine.rate)
            self._push_bank()
            self._ka.volume(engine.master)

    # -- the bank: one crossing per cart, re-pushed when the editor moves it --

    def _push_bank(self):
        import json
        bank = self.engine.bank
        self._ka.bank_load(json.dumps(bank.to_dict()))
        self._bank_rev = bank.rev

    def _sync_bank(self):
        if self._ka is not None and self.engine.bank.rev != self._bank_rev:
            self._push_bank()

    # -- SPEC.md 8.2, forwarded (FakeAudio still records every call) ----------

    def sfx(self, n, chan=None):
        self.calls.append(("sfx", int(n), chan))
        if self._ka is not None:
            self._sync_bank()
            self._ka.sfx(int(n), -1 if chan is None else int(chan))
        else:
            self.engine.play_sfx(n, chan)

    def beep(self, freq, dur=0.15):
        self.calls.append(("beep", freq, dur))
        if self._ka is not None:
            self._ka.beep(float(freq), float(dur))
        else:
            self.engine.play_beep(freq, dur)

    def music(self, track, loop=True):
        self.calls.append(("music", int(track), bool(loop)))
        if self._ka is not None:
            self._sync_bank()
            self._ka.music(int(track), 1 if loop else 0)
        else:
            self.engine.play_music(track, loop)

    def music_stop(self):
        self.calls.append(("music_stop",))
        if self._ka is not None:
            self._ka.music_stop()
        else:
            self.engine.stop_music()

    def sound_stop(self, chan=None):
        self.calls.append(("sound_stop", chan))
        if self._ka is not None:
            self._ka.sound_stop(-1 if chan is None else int(chan))
        else:
            self.engine.stop(chan)

    def volume(self, level):
        self.calls.append(("volume", level))
        self.engine.set_volume(level)      # keep the model in step
        if self._ka is not None:
            self._ka.volume(self.engine.master)

    def is_active(self):
        if self._ka is not None:
            return bool(self._ka.active())
        return self.engine.is_active()

    def tick(self, dt):
        if len(self.calls) > 64:
            del self.calls[:]
        eng = self.engine
        if self.ahead >= 0.0:
            want = _AUDIO_TARGET - self.ahead
            if want > _AUDIO_MAX_STEP:
                want = _AUDIO_MAX_STEP
            n = int(eng.rate * want) if want > 0 else 0
        else:
            n = int(eng.rate * dt) if dt > 0 else 0
        cap = len(self._buf) // 2
        if n > cap:
            n = cap
        if n <= 0 or not self.is_active():
            return
        if self._ka is not None:
            buf = memoryview(self._buf)[:n * 2]
            self._ka.render(buf, n)
            self.last_pcm = bytes(buf)
        else:
            self.last_pcm = eng.render(n)
        self.rendered += n


def _make_audio(engine):
    return _RunnerAudio(engine)


class _PointerSink:
    """Adapts the ConsoleDriver's DEFERRED touch state to the Pointer-shaped
    interface web_input.apply_events expects, so the browser drives the same
    event-decode path every other transport does."""

    def __init__(self, driver):
        self._d = driver

    def place(self, x, y):
        self._d.pointer.place(int(x), int(y))

    @property
    def down(self):
        return self._d._down

    @down.setter
    def down(self, v):
        self._d._down = bool(v)

    @property
    def click(self):
        return self._d._click

    @click.setter
    def click(self, v):
        if v:
            self._d._click = True


def boot(carts_root="/moy/carts", cart=None, width=320, height=240,
         windowed=False, font_scale=0, hud=True):
    """Build the shared Workstation over the RGB565 canvas + the VFS store.
    The page/harness wrote the cart bundle into `carts_root` before calling.

    `hud=False` suppresses the perf HUD -- see the note at the ws.show_fps
    assignment below for why a bundle would want that.

    Two presentation TIERS out of one wasm binary (the page picks per tab), and
    both are now the SAME arrangement their hardware twin uses:
      windowed=False -- the HANDHELD tier, i.e. the T-Deck's: ONE canvas, the
        system canvas IS the game canvas at 320x240, so composite_game is a
        no-op and the cart owns every pixel.
      windowed=True  -- the DESKTOP tier (#73/#105), i.e. the P4's: a big system
        canvas for the desk and its app windows, plus a separate 320x240 game
        canvas that `blit_game` composites into a window (integer-scaled through
        the same C kernel). Boots onto the desk with project management on.
    """
    # GC POLICY (surface model Phase B, the gate-0 finding): the port hardcodes
    # gc_alloc_threshold to 16KB, so EVERY painted frame schedules a FULL
    # collect at the next JS->Python boundary -- measured on the desktop tier:
    # ~67ms against its live set, i.e. the entire 70ms-vs-2.8ms window-drag gap
    # (quiet frames stay under 16KB and skip it, which is why idle was always
    # free). Raise the trigger so collects are RARE, and let the worker land
    # them on IDLE frames via idle_collect() below (SPLIT_HEAP_AUTO grows the
    # heap instead of collecting, so somebody must still collect eventually).
    #
    # 4MB (revised 2026-07-31 after measuring the collect itself). Earlier cuts
    # of this knob guessed in both directions and one regressed play: 24MB is
    # ABOVE the 16MB heap, so the threshold could never fire -- the split heap
    # just grew (16.3MB live after one drag, ~150MB reserved) and every later
    # collect scanned more. The measurement that settles it: an idle_collect
    # costs 5.7ms on the desk, 11ms after a 120-frame drag -- cheap. So collects
    # do not need to be rare, only OFF the gesture path: 4MB (~90 drag frames of
    # headroom at the post-attribution ~45KB/f) keeps memory bounded while the
    # worker's idle collect still lands them on quiet frames.
    import gc
    gc.threshold(4 * 1024 * 1024)
    carts = moy_carts.scan(carts_root)
    if windowed:
        # The system canvas is never smaller than the 320x240 game canvas it
        # composites in as a viewport (host_app.build_workstation clamps the
        # same way, for the same reason).
        width = max(320, int(width))
        height = max(240, int(height))
    inp = InputState()
    if not font_scale:
        # 1x on BOTH tiers (owner call): at 1024x600 the 2x system font ate the
        # desktop's density for no legibility win on a real monitor. The page can
        # still pass an explicit scale.
        font_scale = 1
    if windowed:
        # The P4's arrangement exactly: a big system canvas, a real 320x240 game
        # canvas beside it, and blit_game compositing one into the other through
        # the C kernel. The old recording tier could not do this -- it had no
        # pixels to composite, so a cart's commands shipped with a ["view", ...]
        # placement bracket (#175) for the page to honour, and a genuine
        # rasterizer here was unaffordable at ~85ms/frame interpreted.
        sysc = web_canvas.make_canvas(width, height, font_scale=font_scale)
        game = web_canvas.WebSystemCanvas(web_canvas.WebCompositor(320, 240))
        ws = console.Workstation(host_api._NullComp(), game, inp, carts,
                                 sys_canvas=sysc, font_scale=font_scale)
    else:
        sysc = game = web_canvas.make_canvas(320, 240, font_scale=font_scale)
        ws = console.Workstation(host_api._NullComp(), game, inp, carts)
    # Per-run cart canvas factory (SPEC.md 1/3.1): a cart declaring a smaller
    # raster (celeste's view(128, 120)) plays on its own off-screen canvas and
    # blit_game upscales it, same as both boards.
    ws.make_game_canvas = lambda w, h: web_canvas.WebSystemCanvas(
        web_canvas.WebCompositor(int(w), int(h)))
    # Lua carts: moycore, the SAME native module and glue both boards run --
    # third architecture, one engine. A build without the usermod still boots
    # (a lua cart opens the Player's runtime-missing panel).
    lua_runtime = None
    try:
        from moycore_glue import make_moycore_runtime
        lua_runtime = make_moycore_runtime(ws)
    except ImportError:
        pass
    # The board-agnostic service wiring, in the one canonical order (host + both
    # boards + this runner). Runner-only: can_manage=False (no Make tile / no
    # project management); the FakeWifi keeps any wifi UI harmless.
    console.wire_workstation_core(
        ws, moy_carts, carts_root, host_api.make_api,
        host_api.make_wifi(moy_carts, carts_root),
        make_audio=_make_audio, lua_runtime=lua_runtime, can_manage=True,
        pointer=console.Pointer(sysc.w, sysc.h), inp=inp)
    # AUTHORING IS ON, BOTH TIERS (owner call): the browser build is the whole
    # console, not the player-only runner #151 originally scoped -- the Make tile
    # and the editors are the point. Nothing persists across a reload (the VFS is
    # in-memory), which the page says out loud.
    # can_manage is wired AFTER the launcher was built, so rebuild the shelf for it
    # to appear.
    ws.launcher.items = ws._launcher_items(ws._all_carts)
    # The Moybyte shell's achievements are gamification for the kid console,
    # not part of a cart player (and doubly not of the brand-neutral spec
    # bundle). The REAL trigger is the Achievements core's note() (e.g.
    # ach.note("open", ...) -> toast "First Steps" -- console.py's own
    # _draw_toast renders it, so stubbing achievements_ui alone was not
    # enough, as the first owner session proved). Kill the core's verbs.
    ws.ach.note = lambda *a, **k: None
    ws.ach.toast = None
    ws._achievement_unlocked = lambda *a, **k: None
    # The Pointer boots visible (a device shows the trackball cursor until its
    # idle timeout) -- in the browser that composited an arrow over the first
    # game frames (owner report: "a cursor flashes at open"). Start hidden; a
    # real trackball-style pan on a system surface re-shows it, a mouse/touch
    # place() keeps it hidden as always.
    ws.pointer.visible = False
    if windowed and ws._sys_canvas is not None:
        # A windowed composite must NOT letterbox. `blit_game` means two things
        # -- fill everything outside the game rect black (this canvas IS the
        # glass, the handheld tier below) versus put a cart inside a WINDOW --
        # and the shared `DeviceCanvas.blit_game` defaults to the first. Left
        # set, the desk, its icon column, the OS bar and every other window go
        # black behind the first game window opened. This build shipped exactly
        # that until 2026-08-15; see DeviceCanvas.letterbox_composite. The tier
        # is chosen HERE, so this is where the canvas is told which meaning it
        # serves -- the same line, in the same place, as
        # host_app.build_workstation.
        ws._sys_canvas.letterbox_composite = False
        # Installed LAST so it anchors its root context -- and its per-window
        # content buffers, via root.new_layer -- to the system canvas. Two
        # worlds (#105): boot onto the desk.
        from wm_windowed import WindowedWM
        ws.wm = WindowedWM(ws)
        ws.open_desk()
    # HUD (perf_hud.PerfHud): the bottom-right FPS chip draws onto the GAME
    # canvas, so on the handheld tier -- where the system canvas IS the game
    # canvas -- it lands inside the cart's own 320x240 raster and ships in the
    # cart's own command stream. That is host chrome sitting in a frame the cart
    # owns, and it is wrong anywhere the frame IS the product: a spec
    # conformance capture (moy SPEC.md 11 makes this player the golden-frame
    # tiebreaker, and a golden frame must not contain an FPS counter) or a
    # published web export of somebody's game.
    #
    # Found by moy-spec's conformance harness, which measured this player against
    # the suite and got exactly 200 differing pixels on every scene -- a 20x10
    # box at (299, 229), which is the chip.
    #
    # Default stays True, so the device, the dev page and the desktop tier are
    # unchanged. A caller that wants clean frames passes hud=False.
    ws.show_fps = bool(hud)
    if not hud:
        ws.perf_hud = False
    driver = host_api.ConsoleDriver(ws)
    _S["ws"] = ws
    # The 3.4 sync push (moy_sync): watch this VFS store for committed changes
    # and hand them to the worker as wire batches; the worker POSTs them to
    # the relative /sync of whoever served the page. Constructed AFTER the
    # pull wrote the store, so the baseline is "the board's own state, nothing
    # pending". A page served by a host with no /sync (moybyte.com, an
    # export) gets one failed POST and the worker calls sync_off().
    try:
        import moy_sync
        _S["sync"] = moy_sync.StoreWatcher(carts_root)
        _S["sync_files"] = _files_watcher(moy_sync, carts_root)
    except Exception as exc:  # noqa: BLE001 -- sync must never block a boot
        print("sync watcher unavailable:", exc)
        _S["sync"] = None
        _S["sync_files"] = None
    # The canvas the PAGE presents: the system canvas on the desktop tier, the
    # one shared canvas on the handheld tier. Its buffer is what fb_addr()
    # publishes, so this is the single place the two tiers differ downstream.
    _S["canvas"] = sysc
    _S["driver"] = driver
    _S["sink"] = _PointerSink(driver)
    _S["root"] = carts_root
    _S["exit"] = ws._exit_to_caller     # the real exit (reload_cart uses it)
    if cart:
        open_cart(cart)
    return True


def _files_watcher(moy_sync, carts_root):
    """The #108 files watcher, or None when this page's server has no files half.

    CAPABILITY, not configuration, and the mechanism is deliberately the pull's
    own result: worker.js creates the files root in the VFS only when its
    GET /files.json answered, so an older board -- one that serves carts and
    /sync but predates this -- leaves no directory here and no files batch is
    ever aimed at a receiver that would misapply it. (The wire's v2 bump would
    refuse such a batch anyway; this is what keeps it from being sent, and it
    is why one 404 cannot disable the CARTS push along with it.)
    """
    import os
    root = moy_sync.files_root(carts_root)
    if root is None:
        return None
    try:
        os.stat(root)
    except OSError:
        return None
    return moy_sync.StoreWatcher(root, root_id=moy_sync.FILES_ROOT_ID)


def kiosk(name):
    """Single-cart bundle mode (the spec export): the game IS the page. The
    exit gesture RESTARTS the cart instead of dropping into the console shell
    -- a PICO-8 web export has no shell, and neither should this."""
    ws = _S["ws"]
    real_exit = _S["exit"]

    def _restart():
        real_exit()
        open_cart(name)
    ws._exit_to_caller = _restart


def reload_cart(name=None):
    """Dev hot-reload (the moy CLI's watch loop): the page rewrote changed cart
    files in the VFS; pop any running cart (flushes pmem via release_world),
    re-scan the store, and restart `name` (default: the cart that was running).
    Returns True when a cart restarted."""
    ws = _S["ws"]
    cart = getattr(ws, "cart", None)
    if name is None and cart is not None:
        name = (cart.get("path") or "").rsplit("/", 1)[-1]
    if cart is not None:
        _S["exit"]()          # the REAL exit (kiosk wraps ws._exit_to_caller)
    ws._all_carts = moy_carts.scan(_S["root"])
    ws.launcher.items = ws._launcher_items(ws._all_carts)
    ws.slim_carts()
    ws._dirty = True
    for key in _SYNC_ROOTS:
        w = _S.get(key)
        if w is not None:
            # The reload just re-pulled the served store over the VFS -- adopt
            # it as the new baseline (deliberate LWW: replaying local unpushed
            # edits over a state the human just asked for would undo the
            # reload).
            w.rebase()
    return open_cart(name) if name else True


def open_cart(name):
    """Select + run a cart by folder name ('star_catcher.moy', extension
    optional) or manifest title, skipping the launcher -- the single-cart-embed
    path (?cart=...). Unknown name -> False (the shelf stays up)."""
    ws = _S["ws"]
    want = str(name).lower()
    if want.endswith(".moy"):
        want = want[:-4]
    for i, c in enumerate(ws.launcher.items):
        path = c.get("path") or ""       # synthetic tiles (Make) carry no path
        folder = path.rsplit("/", 1)[-1].lower()
        if folder.endswith(".moy"):
            folder = folder[:-4]
        if want in (folder, c.get("title", "").lower()):
            ws.launcher.sel = i
            ws.open()
            return True
    return False


def _cart_title():
    cart = getattr(_S["ws"], "cart", None)
    return cart.get("title") if cart is not None else None


def _audio_rate():
    if not _AUDIO_RATE[0]:
        from audio import AudioEngine
        _AUDIO_RATE[0] = AudioEngine().rate
    return _AUDIO_RATE[0]


def assets_json():
    """The page's METADATA -- no pixels.

    This used to be the runner's heaviest and most delicate call: the page
    replayed draw commands, so it needed the palette, the petme128 blob, the
    open cart's sheet and tilemap, every paint image and every shelf cover,
    base64'd into one payload. Serialising it cost 360-560ms (json.dumps over
    ~644KB), which meant a memo keyed on a cover generation, an incremental
    "ship only what this client lacks" diff, a re-request latch in the page for
    images a draw referenced but the client did not hold, and a documented trap
    where returning the previous payload on a memo miss shipped a stale input
    hint. All of it existed to move pixels the wasm can now draw itself, and all
    of it is deleted with the replayer.

    What the page still cannot derive: how big the surface is, what the cart is
    called, the audio rate its worklet resamples to, which on-screen controls to
    show, and how this build orders the bytes of a 565 pixel (below).
    """
    ws = _S["ws"]
    ws._dirty = True                 # the reloaded page wants a full frame
    cv = _S["canvas"]
    return json.dumps({
        "v": 3,
        "w": cv.w, "h": cv.h,
        "cart": _cart_title(),
        "audio_rate": _audio_rate(),
        "input": web_input.effective_input_kinds(ws),
        # BYTE ORDER of the RGB565 framebuffer, reported rather than assumed.
        # device_canvas picks its palette table from the PANEL it is talking to:
        # canonical little-endian for the P4's DSI scan-out, byte-swapped for the
        # T-Deck's SPI panel (which folds the swap into the LUT so the driver can
        # skip a ~17ms/frame CPU pass). A browser has no panel, and this build
        # takes whichever branch the probe lands on -- so the page builds its
        # 565->RGBA table from this flag instead of guessing, and stays correct
        # if the probe ever changes.
        "swap": 0 if _wire_is_canonical() else 1,
    })


def _wire_is_canonical():
    """True when the framebuffer holds little-endian RGB565."""
    try:
        import device_canvas
        return device_canvas.PAL565_WIRE is device_canvas.PAL565
    except Exception:  # noqa: BLE001 -- unknown -> assume canonical
        return True


def fb_addr():
    """The presented framebuffer's address in the wasm heap.

    The worker reads the finished frame straight out of `Module.HEAPU8` at this
    offset, so a painted frame costs one memcpy on the JS side and nothing at
    all on the Python side. Re-read every frame rather than cached: a resize
    builds a new canvas, and MicroPython's own heap can move under us in ways
    this module has no business tracking.
    """
    import uctypes
    return uctypes.addressof(_S["canvas"]._buf)


def fb_len():
    """The framebuffer's length in BYTES (w * h * 2)."""
    return len(_S["canvas"]._buf)


def step_frame_json(dt, audio_ahead=-1.0):
    """Advance the console one frame.

    Returns "" when the console skipped the redraw (#44 dirty gate: nothing
    moved) and there is no audio to ship -- the page then simply keeps the
    pixels it already has. Otherwise a small JSON string carrying what does not
    live in the framebuffer: whether it painted, the cart title, the input hint,
    and this frame's finished PCM. `audio_ahead` is the page's scheduled-ahead
    audio depth in seconds (-1 = not reported): _RunnerAudio tops the cushion
    back up to target each frame (the crackle fix, #170).

    The PIXELS are not in here. The worker reads them from fb_addr()/fb_len()
    when `paint` is set.
    """
    ws = _S["ws"]
    au = getattr(ws, "audio", None)
    if au is not None and hasattr(au, "ahead"):
        au.ahead = float(audio_ahead)
    painted_before = ws._frames_drawn
    _S["driver"].frame(dt)
    painted = ws._frames_drawn != painted_before
    # Drain the engine's FINISHED PCM (rendered by _RunnerAudio.tick during the
    # frame) -- the page plays it through its AudioWorklet ring.
    pcm = au.take_pcm() if (au is not None and hasattr(au, "take_pcm")) else b""
    audio_b64 = ""
    if pcm and any(pcm):
        import binascii
        audio_b64 = binascii.b2a_base64(pcm).decode().strip()
    if not painted and not audio_b64:
        return ""
    return json.dumps({
        "paint": 1 if painted else 0,
        "cart": _cart_title(),
        "audio": audio_b64,
        "input": web_input.effective_input_kinds(ws),
    })


def _apply(events):
    d = _S["driver"]
    rest = []
    for e in events:
        if e.get("type") == "reset":
            # Esc during play: restart the cart (kiosk wraps the exit into a
            # relaunch; the multi-cart runner pops to the shelf).
            if getattr(_S["ws"], "cart", None) is not None:
                _S["ws"]._exit_to_caller()
        else:
            rest.append(e)
    web_input.apply_events(
        rest, d.input, _S["sink"],
        on_press=d.press, on_pan=d.pan, on_key=d.type_char,
        on_esc=d.escape, on_hold=d.hold, on_key_hold=d.key_hold)


def sync_config(pin=None):
    """The page's ?pin= (if any), forwarded by the worker after init -- it
    rides inside every batch body, where the board's WebHost checks it."""
    _S["sync_pin"] = pin or None
    return ""


# The watched roots, drained in this order. CARTS FIRST is a deliberate
# priority and not an accident of the tuple: a cart edit is what the kid is
# looking at on the board's glass, and a drawing landing a second later costs
# nobody anything.
_SYNC_ROOTS = ("sync", "sync_files")


def sync_poll_json():
    """One sweep + the next wire batch as JSON, or "" (nothing changed, or a
    batch is already awaiting its answer). The worker calls this about once a
    second -- the sweep is a stat walk of an in-memory VFS, so its cost is
    noise; the READ of changed files happens only when something committed.

    ONE BATCH IN FLIGHT is a rule about the TRANSPORT, so it is enforced here
    across both roots rather than left to each watcher's own `take`. The worker
    posts one body at a time and acks before polling again -- but a poll that
    fell through to the files root while a carts batch was still unanswered
    would hand out a second body on that promise, and `sync_ack` would then
    settle the wrong one."""
    for key in _SYNC_ROOTS:
        w = _S.get(key)
        if w is not None and w.busy():
            return ""
    for key in _SYNC_ROOTS:
        w = _S.get(key)
        if w is None:
            continue
        w.sweep()
        body = w.take_json(_S.get("sync_pin"))
        if body:
            _S["sync_took"] = key
            return body
    return ""


def sync_ack(ok):
    """Settle the in-flight batch: the worker's POST got an answer (ok=True
    clears it; anything else requeues every path it carried). Routed to the
    watcher that TOOK it -- acking the wrong root would strand its batch in
    flight forever and requeue nothing."""
    w = _S.get(_S.get("sync_took") or "sync")
    if w is not None:
        w.ack(bool(ok))
    return ""


def sync_off():
    """The far end has no /sync at all (a static host, an old read-only board):
    stop sweeping for good. One failed probe, then silence -- the standalone
    browser console must not retry-log forever.

    BOTH roots, because this is the "there is no push half here" answer. A board
    that has /sync but no files layer is a different case entirely, and it is
    settled at boot: no files.json means no files watcher, so nothing ever
    probes for a files endpoint that would 404."""
    _S["sync"] = None
    _S["sync_files"] = None
    return ""


def idle_collect():
    """Run the deferred split-heap collect NOW -- called by the worker on a
    quiet streak (and as a bounded periodic guard), so the full mark+sweep
    lands on an idle frame instead of mid-gesture. Under GC_SPLIT_HEAP_AUTO
    gc.collect() only sets the pending flag; the real collect executes when
    THIS call returns to JS (the top level) -- exactly the boundary we chose."""
    import gc
    gc.collect()
    return ""


def apply_events_json(text):
    """Feed a browser {"events":[...]} JSON text batch through the ONE shared
    decode path (the same wire format the WS transports speak)."""
    web_input.apply_ws_text(text, _apply)
