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
    gpio_enable(pins_json)        -> the host answered POST /gpio: wire the
                                     pin verbs (absent otherwise -- see #9)
    gpio_poll_json() / gpio_ack_json(ok, text) / gpio_off()
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


def _make_api(*a, **kw):
    """The shared cart API. No tier-specific gate any more, deliberately.

    This used to inject the pin backend itself, reading `_S["gpio"]` at cart
    start so the probe's timing and the cart's timing stayed independent. That
    worked and was wrong: it handed `pin_write` to EVERY cart the moment the
    serving host had pins, which is the capability half of the question with the
    consent half missing -- while cart_api's own comment said pins were gated
    "the same way as wifi and net", which have both halves. The backend is
    `ws.gpio` now and `player.start` gates it on the "pins" permission with its
    two siblings, so there is one gate rather than a second one over here.
    """
    return host_api.make_api(*a, **kw)


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
    # boards + this runner).
    #
    # NO WIFI SERVICE, and that is the honest answer rather than a missing one.
    # This wired host_api's FakeWifi until 2026-08-30 -- the SIMULATOR's stand-in,
    # whose whole job is to invent four access points ("Home WiFi", "Coffee
    # Shop", "Neighbor 5G", "Library Guest") and a 192.168.1.42 so the desktop's
    # WIFI panel can be developed with no radio. On a page SERVED BY A BOARD that
    # is not a stand-in, it is a lie: the Zero is headless, so this panel is the
    # only WIFI screen that board has, and it listed networks that do not exist
    # and accepted a join that went nowhere. Reported from a real session.
    #
    # `None` is the shape every other absent capability uses here, and the shell
    # already knows it -- settings_layer says NO WIFI SERVICE and stops. A page
    # cannot reach a radio, and the day it can it will be through a bridge to the
    # board's own service (as gpio_link and update_link are), not a fake.
    console.wire_workstation_core(
        ws, moy_carts, carts_root, _make_api,
        None,
        make_audio=_make_audio, lua_runtime=lua_runtime, can_manage=True,
        pointer=console.Pointer(sysc.w, sysc.h), inp=inp)
    # AUTHORING IS ON, BOTH TIERS (owner call): the browser build is the whole
    # console, not the player-only runner #151 originally scoped -- the Make tile
    # and the editors are the point. This used to add "and nothing persists
    # across a reload", which #193 made false: a static-host page keeps carts in
    # OPFS and a board-served one writes them home, so the only in-memory case
    # left is a browser with no usable OPFS -- and the page still says THAT out
    # loud.
    # can_manage is wired AFTER the launcher was built, so rebuild the shelf for it
    # to appear.
    ws.launcher.items = ws._launcher_items(ws.carts.all)
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
    # The pin backend, if the worker's probe found one. Stashed by gpio_enable
    # (which runs BEFORE this, since the probe is answered while the console is
    # still being built) and hung on the Workstation HERE, because `ws.gpio` is
    # the seam the Player gates on -- beside ws.wifi and ws.net, in one place.
    ws.gpio = _S.get("gpio")
    # The 3.4 sync push (moy_sync): watch this VFS store for committed changes
    # and hand them to the worker as wire batches; the worker POSTs them to
    # the relative /sync of whoever served the page. Constructed AFTER the
    # pull wrote the store, so the baseline is "the board's own state, nothing
    # pending". A page served by a host with no /sync (moybyte.com, an
    # export) gets one failed POST and the worker calls sync_off().
    # ONE watcher per registered sync root (moy_sync.SYNC_ROOTS) -- built by
    # ITERATING the registry, so a new store is watched the day it is registered
    # with no new line here. THE JOURNAL FOLLOWS THE STORE OF RECORD (2026-08-25):
    # `root.watch_skip(site)` sweeps journal/ into the browser's own OPFS in SITE
    # mode for the root that is of record there (carts), and keeps the wire's own
    # rule everywhere else -- so a board-mode sweep is byte-identical to what it
    # always was. A root whose store is not present in the VFS (a sibling root
    # the served host has no layer for) simply gets no watcher.
    _S["watchers"] = _build_watchers(carts_root, _S.get("store_mode") == "site")
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


def _build_watchers(carts_root, site):
    """`{root_id: StoreWatcher}` for every registered sync root whose store is
    PRESENT in the VFS. A missing store means no watcher -- and that is a
    CAPABILITY signal, not a bug: worker.js seeds a sibling root's directory only
    when its source exists (a board-served `files.json`, or the browser's own
    OPFS in site mode), so a host that has no files layer leaves no directory,
    gets no files watcher, and never aims a files batch at a receiver that could
    only refuse it. Never blocks a boot -- any failure leaves an empty dict."""
    import os
    out = {}
    try:
        import moy_sync
        for root in moy_sync.SYNC_ROOTS:
            path = root.path(carts_root)
            if path is None:
                continue                 # no such layer on this host at all
            try:
                os.stat(path)            # present in the VFS?
            except OSError:
                continue                 # not seeded -> not watched
            out[root.id] = moy_sync.StoreWatcher(
                path, root_id=root.id, skip=root.watch_skip(site))
    except Exception as exc:  # noqa: BLE001 -- sync must never block a boot
        print("sync watcher unavailable:", exc)
        return {}
    return out


def _watchers():
    """The live watchers, in REGISTRY (drain) order -- CARTS FIRST, a deliberate
    priority: a cart edit is what the kid is watching on the glass, and a drawing
    landing a second later costs nobody anything. Order comes from the registry,
    never the dict (MicroPython dicts are not insertion-ordered)."""
    import moy_sync
    ws = _S.get("watchers") or {}
    return [ws[r.id] for r in moy_sync.SYNC_ROOTS if r.id in ws]


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


def _rescan():
    """Re-derive the shelf from the store on disk, through the ONE body every
    other roster change already runs -- `CartManager.apply`.

    It used to hand-roll that: set `carts.all`, rebuild `launcher.items`, slim.
    This copy was written 2026-08-25, two days BEFORE the #209 carve made
    `apply` the single body for create/duplicate/delete and the board's sync
    re-scan -- and living outside `runtime/`, it was the one caller that sweep
    did not reach. It then drifted three ways, each of which reached a user:

      * it never rebuilt the PICKER, so a cart imported in the browser appeared
        on the launcher and was MISSING from Projects (#194, reported on the
        hosted console). `edit_cart`'s own docstring says an imported cart
        arrives "exactly like an authored one", and the grid disagreed.
      * it never invalidated the cover caches, and `slim()` bakes each icon and
        then deletes the art it was baked FROM -- the exact ordering `apply`
        calls out, where clearing nothing lets a stale entry become permanent.
      * it used `_launcher_items` where `apply` uses `_launcher_view_items`,
        dropping an active search filter (#105).

    `apply` also keeps the old shelf when a scan comes back empty rather than
    blanking it, which is its deliberate failure direction and now this one's."""
    ws = _S["ws"]
    ws.carts.apply(moy_carts.scan(_S["root"]))
    ws._dirty = True


def rescan_store():
    """Files landed in the VFS from OUTSIDE the console -- the page's .moy
    import (#193). Re-scan the shelf, and deliberately do NOT rebase the sync
    watcher: an import is a CHANGE, so it must stay pending and reach the store
    (the browser's OPFS in mode 1) on the next sweep like any other commit.
    reload_cart's rebase is the opposite case -- there the files arrived FROM
    the far end, and replaying them back at it would undo the reload."""
    _rescan()
    return True


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
    _rescan()
    for w in _watchers():
        # The reload just re-pulled the served store over the VFS -- adopt it as
        # the new baseline (deliberate LWW: replaying local unpushed edits over a
        # state the human just asked for would undo the reload).
        w.rebase()
    return open_cart(name) if name else True


def _select_cart(name):
    """Point the launcher at the cart `name` names (folder name with or without
    `.moy`, or manifest title). True when it landed on one."""
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
            return True
    return False


def open_cart(name):
    """Select + run a cart by folder name ('star_catcher.moy', extension
    optional) or manifest title, skipping the launcher -- the single-cart-embed
    path (?cart=...). Unknown name -> False (the shelf stays up)."""
    if not _select_cart(name):
        return False
    _S["ws"].open()
    return True


_EDIT_TABS = {"paint": "open_paint", "map": "open_map", "music": "open_music",
              "scene": "open_scene", "blocks": "open_blocks"}


def edit_cart(name, tab=None):
    """Open a cart in the EDITOR, landing on its tabs with its own assets --
    the "open in editor" half of the p8 drop (#194).

    The tap that RUNS a cart and the one that EDITS it are different verbs in
    the shell (shell_ux_v1.md: a launcher tap always runs), and this is the
    edit one -- the same entry the Editor's project picker uses, so an imported
    cart arrives in the Sprites / Map / Music tabs exactly like an authored one.
    `tab` lands on one of them ("paint"/"map"/"music"/...) instead of the
    default Config landing.

    Returns a small JSON state so the caller can SEE it landed rather than
    assume: an editor that silently did not open looks identical to one that
    did until somebody screenshots it."""
    ws = _S["ws"]
    # END any run FIRST, and before the launcher selection is moved. On the
    # windowed desk a cart plays in its own window, so an editor opened over a
    # still-running import leaves the game sitting on top of the code it is
    # meant to show -- and the run's world would outlive the workspace the
    # Editor rebuilds. PLAY in the Editor is how it starts again.
    if getattr(ws, "cart", None) is not None:
        _S["exit"]()              # the REAL exit (kiosk wraps ws._exit_to_caller)
    ok = _select_cart(name)
    if ok:
        ws.open_in_editor()
        opener = _EDIT_TABS.get(tab)
        if opener is not None:
            getattr(ws.editor_app, opener)()
    return json.dumps({
        "ok": ok,
        "screen": getattr(ws, "screen", None),
        "tab": getattr(ws, "menu_view", None),
        "title": _cart_title(),
    })


def import_p8_json(path, name, out_dir):
    """Convert a dropped PICO-8 cart at `path` into a `.moy` folder (#194).

    Lazily imported so the console boots without the converter in RAM: a session
    that never drops a cart never pays for it."""
    import web_p8
    return web_p8.import_p8_json(path, name, out_dir)


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


def store_mode(mode=None):
    """Which world this page is in, told to us by the worker BEFORE boot().

    The worker decides it (moy_store.probeMode) before anything is written,
    because the answer is what the VFS gets seeded FROM. boot() then needs it
    for one decision only: whether the carts watcher sweeps the journal, which
    it does exactly when THIS browser is the store of record ("site"). So this
    must be called before boot() -- after it, the watcher already exists and a
    late mode would be a setting with no effect, which is the shape of bug this
    whole file is careful about.

    Anything other than "site" ("board", "none", or never called at all) leaves
    the wire's own predicate in place. That default matters more than the
    setting: a mode that fails to arrive must not start shipping journals at a
    board.
    """
    _S["store_mode"] = mode or None
    return ""


def sync_config(pin=None):
    """The page's ?pin= (if any), forwarded by the worker after init -- it
    rides inside every batch body, where the board's WebHost checks it."""
    _S["sync_pin"] = pin or None
    return ""


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
    live = _watchers()
    for w in live:
        if w.busy():
            return ""
    for w in live:
        w.sweep()
        body = w.take_json(_S.get("sync_pin"))
        if body:
            _S["sync_took"] = w.root_id
            return body
    return ""


def sync_ack(ok):
    """Settle the in-flight batch: the worker's POST got an answer (ok=True
    clears it; anything else requeues every path it carried). Routed to the
    watcher that TOOK it -- acking the wrong root would strand its batch in
    flight forever and requeue nothing."""
    import moy_sync
    watchers = _S.get("watchers") or {}
    w = watchers.get(_S.get("sync_took") or moy_sync.CARTS_ROOT_ID)
    if w is not None:
        w.ack(bool(ok))
    return ""


def sync_off():
    """The far end has no /sync at all (a static host, an old read-only board):
    stop sweeping for good. One failed probe, then silence -- the standalone
    browser console must not retry-log forever.

    BOTH roots, because this is the "there is no push half here" answer. A board
    that has /sync but no files layer is a different case entirely, and it is
    settled at boot: no files store in the VFS means no files watcher, so
    nothing ever probes for a files endpoint that would 404."""
    _S["watchers"] = {}
    return ""


def gpio_enable(pins_json):
    """The host answered the /gpio probe: wire the pin verbs (#9).

    Called by the worker in the boot script, so this lands BEFORE any cart can
    start and the availability question is settled exactly once. A host that
    did not answer never calls it, and `pin_write`/`pin_read` then have no name
    at all -- the repo's standing rule for a capability that is not there.

    `pins_json` is the allowlist the board sent. Kept rather than assumed,
    because the refusal has to be able to say WHICH pins this board has, and
    the browser cannot know that: the answer is a fact about the hardware on
    the other end of the wire.
    """
    try:
        pins = json.loads(pins_json)
    except Exception:                # noqa: BLE001 -- a garbled probe answer
        pins = None
    if not pins:
        return ""
    try:
        from gpio_link import GpioLink
        _S["gpio"] = GpioLink(pins)
    except Exception as exc:         # noqa: BLE001 -- never block a boot
        print("gpio unavailable:", exc)
    return ""


def services_json():
    """Which capability seams this console ACTUALLY has, as JSON.

    Written because the observable lied. The worker posted `{t:"update"}` on the
    strength of the PROBE having answered and its own comment called that "did
    the bridge bind?" -- so a page could report an updater while the console
    behind it had none, and a browser test asserting on that message proved the
    probe and nothing else. A seam is live or it is not, and only the console
    can say which.

    Cheap and read-only: four getattrs, called once after boot and by a harness
    that wants to know. It is the browser tier's answer to the question
    tests/test_board_service_parity.py asks of every other tier by reading
    source -- here the wiring is decided at RUNTIME, by what the far end
    answered, so no amount of source-reading can settle it.
    """
    ws = _S.get("ws")
    if ws is None:
        return "{}"
    return json.dumps({
        "updater": getattr(ws, "updater", None) is not None,
        "wifi": getattr(ws, "wifi", None) is not None,
        "gpio": getattr(ws, "gpio", None) is not None,
        "net": getattr(ws, "net", None) is not None,
        "can_manage": bool(getattr(ws, "can_manage", False)),
    })


def update_enable(status_json):
    """The host answered the /update probe: give the console an UPDATER (#41).

    Same shape and same moment as `gpio_enable`, and for the same reason -- the
    availability question is a fact about the far end, settled once, before any
    screen can ask. A host that did not answer never calls this, `ws.updater`
    stays None, and Settings simply has no update row: the standing rule for a
    capability that is not there, rather than a row that fails when tapped.

    What this buys is the whole point of doing it here instead of in the page:
    `runtime/update_ui.py` is 659 lines that are ALREADY frozen into this
    bundle, and were dead only because nothing was injected. The console gets
    the same update screen every board shows, reached from the same Settings
    row, with the same two-act confirm -- rather than a second update UI drawn
    in page chrome.
    """
    try:
        doc = json.loads(status_json)
    except Exception:                # noqa: BLE001 -- a garbled probe answer
        return ""
    if not isinstance(doc, dict) or "running" not in doc:
        return ""
    try:
        from update_link import RemoteUpdater
        _S["update"] = RemoteUpdater(doc)
        _S["ws"].updater = _S["update"]
    except Exception as exc:         # noqa: BLE001 -- never block a boot
        print("update unavailable:", exc)
    return ""


def update_poll_json():
    """The next queued request, or "" -- the worker POSTs it to /update. When
    there is nothing to send it answers "" and `update_wants_poll` decides
    whether to GET instead."""
    u = _S.get("update")
    return u.take_json(_S.get("sync_pin")) if u is not None else ""


def update_wants_poll():
    """True when the screen is waiting on something and no request is in
    flight, so the worker should GET the status. Polling only while a screen
    cares is what keeps an idle console off the wire entirely."""
    u = _S.get("update")
    return bool(u is not None and u.wants_poll())


def update_ack_json(ok, text=""):
    """Settle the in-flight request or poll: `text` is the board's status
    document, which is where every number the screen prints comes from."""
    u = _S.get("update")
    if u is not None:
        u.ack(bool(ok), text or "")
    return ""


def update_off():
    """The far end stopped answering. The updater STAYS (a screen may still be
    open) and goes inert reporting an error -- see RemoteUpdater.stop."""
    u = _S.get("update")
    if u is not None:
        u.stop()
    return ""


def gpio_poll_json():
    """The next batch of queued pin ops, or "" -- the worker POSTs it to the
    relative /gpio of whoever served the page, exactly like the sync push."""
    g = _S.get("gpio")
    return g.take_json(_S.get("sync_pin")) if g is not None else ""


def gpio_ack_json(ok, text=""):
    """Settle the in-flight batch: `text` is the board's answer, whose `reads`
    are what `pin_read` returns until the next one arrives."""
    g = _S.get("gpio")
    if g is not None:
        g.ack(bool(ok), text or "")
    return ""


def gpio_off():
    """The far end stopped answering /gpio. The verbs STAY (a running cart is
    holding them) and go inert -- see GpioLink.stop."""
    g = _S.get("gpio")
    if g is not None:
        g.stop()
    _S["gpio"] = None
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
